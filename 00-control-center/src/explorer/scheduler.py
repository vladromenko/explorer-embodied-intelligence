"""ENGINEERING_GLUE: independent durable resource scheduler; no robot I/O."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import psutil
from pathlib import Path

HEAVY_JOBS = ('sim_pilot', 'train_act', 'world_model', 'dagger', 'skill_cycle', 'manipulation', 'manipulation_student', 'manipulation_benchmark', 'policy_transfer', 'fault_repair')
PHYSICAL_JOBS = ('station_rollout', 'handover_physical')


def recover_jobs(store):
    """Resume software computation; old physical authorizations never survive restart."""
    for job in store.jobs(10000):
        if job['kind'] in PHYSICAL_JOBS and job['status'] not in ('completed','failed','cancelled'):
            store.update_job(job['id'],status='cancelled',error='Supervisor restarted; a fresh explicit observed physical attempt is required')
        elif job['status'] in ('running','pause_requested','resume_requested'):
            store.update_job(job['id'],status='queued',error='Supervisor restarted; resuming from persisted artifacts')


def host_resources(root):
    """Read actual host capacity and existing thermal trips; never change power mode."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(root)
    thermal = []
    for zone in sorted(Path('/sys/class/thermal').glob('thermal_zone*')):
        try:
            temperature = int((zone/'temp').read_text())/1000
            trips = [int(path.read_text())/1000 for path in zone.glob('trip_point_*_temp')
                if path.with_name(path.name.replace('_temp','_type')).read_text().strip() in ('hot','critical')]
            thermal.append({'name':(zone/'type').read_text().strip(),'temperature_c':temperature,
                'existing_hot_critical_trip_c':min(trips) if trips else None})
        except (OSError,ValueError):
            pass
    return {'available_memory_bytes':memory.available,'free_disk_bytes':disk.free,
        'cpu_count':len(os.sched_getaffinity(0)),'load_1m':os.getloadavg()[0], 'thermal':thermal,
        'power_state':'UNKNOWN_no_external_power_measurement', 'controller_settings_writes':0}


def admission(job, resources):
    """Host-only reserve for UI/recording/inference. Values are budgets, not benchmarks."""
    heavy = job['kind'] in HEAVY_JOBS
    gib = 1024**3
    # Keep the pre-existing 2.5 GiB runtime reserve and account for job working space.
    expected_memory = (1.5 if heavy else .25)*gib
    expected_disk = (2 if heavy else .25)*gib
    reasons = []
    if resources['available_memory_bytes'] < 2.5*gib+expected_memory:
        reasons.append('available_memory_below_runtime_reserve_plus_job_budget')
    if resources['free_disk_bytes'] < gib+expected_disk:
        reasons.append('free_disk_below_runtime_reserve_plus_job_budget')
    if heavy and resources['load_1m'] >= max(1,resources['cpu_count']-1):
        reasons.append('host_cpu_reserve_busy')
    for zone in resources['thermal']:
        trip = zone['existing_hot_critical_trip_c']
        if trip is not None and zone['temperature_c'] >= trip-5:
            reasons.append('near_existing_thermal_trip:'+zone['name'])
    return {'admitted':not reasons,'reasons':reasons,'resources':resources,
        'budget_not_measured_peak':{'memory_bytes':int(expected_memory),'disk_bytes':int(expected_disk)},
        'runtime_reserve_bytes':int(2.5*gib)}


def executable_for(job):
    if job['kind'] in ('train_act','sim_pilot') and job.get('config',{}).get('device')=='cuda':
        runtime=Path.home()/'.local/share/explorer/gpu-venv/bin/python'
        evidence=Path.home()/'.local/share/explorer/state/qualification/act-cu132-fp32-20261026/qualification.json'
        if not runtime.is_file() or not evidence.is_file() or not json.loads(evidence.read_text()).get('passed'):
            raise ValueError('The selected GPU runtime has not passed its actual ACT qualification')
        return str(runtime)
    return sys.executable


def execution_environment(config,store,job):
    current=Path(config['root']).resolve()
    marker=current/'RELEASE_COMMIT'
    if not marker.is_file():
        raise ValueError('Durable jobs require a commit-addressed Explorer deployment')
    pinned=store.pin_job_release(job['id'],marker.read_text().strip())
    release=current.parent/pinned
    if release.resolve().parent!=current.parent or not (release/'RELEASE_COMMIT').is_file() or (release/'RELEASE_COMMIT').read_text().strip()!=pinned:
        raise ValueError('Pinned execution release is unavailable; job source will not be silently replaced')
    return dict(os.environ,PYTHONPATH=str(release/'00-control-center/src')+':'+str(release/'shared'))


def supervise(config, store):
    lock = (store.root / 'scheduler.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    processes = {}
    recover_jobs(store)
    while True:
        jobs = list(reversed(store.jobs(10000)))
        for job in jobs:
            job_id = job['id']
            process = processes.get(job_id)
            if process and process.poll() is not None:
                processes.pop(job_id)
                current = store.job(job_id)
                if current['status'] not in ('completed', 'failed', 'cancelled'):
                    store.update_job(job_id, status='failed', error='Worker exited without a completed result')
            elif process and job['status'] == 'cancel_requested':
                os.killpg(process.pid, signal.SIGCONT)
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                store.update_job(job_id, status='cancelled')
            elif process and job['status'] == 'pause_requested':
                os.killpg(process.pid, signal.SIGSTOP)
                store.update_job(job_id, status='paused')
            elif process and job['status'] == 'resume_requested':
                os.killpg(process.pid, signal.SIGCONT)
                store.update_job(job_id, status='running')
            elif process is None and job['status'] == 'cancel_requested':
                store.update_job(job_id, status='cancelled')
            elif process is None and job['status']=='pause_requested':
                store.update_job(job_id,status='paused')
            elif process is None and job['status'] in ('queued', 'resume_requested'):
                training = job['kind'] in HEAVY_JOBS
                inference=job['kind'] in ('station_shadow','station_rollout')
                data_job=job['kind'] in ('station_import','mobile_import','camera_recording','program_trace')
                busy = any(store.job(key)['kind'] in HEAVY_JOBS for key in processes)
                inference_busy=any(store.job(key)['kind'] in ('station_shadow','station_rollout') for key in processes)
                data_busy=any(store.job(key)['kind'] in ('station_import','mobile_import','camera_recording','program_trace') for key in processes)
                capacity = admission(job,host_resources(store.root))
                if not capacity['admitted']:
                    previous=job.get('result') or {}
                    if previous.get('resource_wait_reasons') != capacity['reasons']:
                        store.update_job(job_id,result={'phase':'waiting_for_host_resources','resource_wait_reasons':capacity['reasons'],
                            'admission':capacity,'prior_progress':previous.get('prior_progress',previous)})
                if (not training or not busy) and (not inference or not inference_busy) and (not data_job or not data_busy) and capacity['admitted']:
                    folder = store.root / 'jobs' / job_id
                    folder.mkdir(parents=True, exist_ok=True)
                    try:
                        executable=executable_for(job)
                        environment=execution_environment(config,store,job)
                        with (folder / 'worker.log').open('ab') as log:
                            process = subprocess.Popen([executable, '-m', 'explorer.job_runner', job_id],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,env=environment)
                    except (ValueError,OSError) as error:
                        store.update_job(job_id,status='failed',error=str(error))
                    else:
                        processes[job_id] = process
                        store.update_job(job_id, status='running')
        time.sleep(1)
