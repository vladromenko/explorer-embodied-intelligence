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
    for job in store.jobs(10000):
        if job['status'] in ('running', 'pause_requested', 'resume_requested'):
            store.update_job(job['id'], status='queued', error='Supervisor restarted; resuming from persisted artifacts')
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
                training = job['kind'] in ('sim_pilot', 'train_act','world_model','dagger')
                inference=job['kind'] in ('station_shadow','station_rollout')
                data_job=job['kind'] in ('station_import','mobile_import','camera_recording')
                busy = any(store.job(key)['kind'] in ('sim_pilot', 'train_act','world_model','dagger') for key in processes)
                inference_busy=any(store.job(key)['kind'] in ('station_shadow','station_rollout') for key in processes)
                data_busy=any(store.job(key)['kind'] in ('station_import','mobile_import','camera_recording') for key in processes)
                memory_ok = psutil.virtual_memory().available > 2.5 * 1024 ** 3
                if (not training or not busy) and (not inference or not inference_busy) and (not data_job or not data_busy) and (not (training or inference or data_job) or memory_ok):
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
