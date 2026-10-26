"""ENGINEERING_GLUE: independent durable resource scheduler; no robot I/O."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import psutil
import uuid
from pathlib import Path
from .queue_policy import HEAVY_JOBS,PHYSICAL_JOBS,INFERENCE_JOBS,DATA_JOBS,TERMINAL,queue_plan
from explorer_shared.learning.checkpoint_control import (active_optimizer,request_pause,verified_pause_receipt,
    clear_pause_request,worker_identity,read_json,PAUSED_EXIT_CODE)
from .worker_bootstrap import PAUSED_BEFORE_WORK_EXIT



class AdoptedWorker:
    """An exact surviving worker is reconciled, never duplicated after restart."""
    def __init__(self,identity):self.identity=identity;self.pid=identity['pid']
    def poll(self):
        try:return None if worker_identity(self.pid)==self.identity and psutil.Process(self.pid).status()!=psutil.STATUS_ZOMBIE else -999
        except psutil.Error:return -999
    def wait(self,timeout=None):
        try:return psutil.Process(self.pid).wait(timeout=timeout)
        except psutil.TimeoutExpired:raise subprocess.TimeoutExpired(str(self.pid),timeout)


def recover_jobs(store):
    """Resume software computation; old physical authorizations never survive restart."""
    adopted={}
    for job in store.queue_jobs():
        runtime=store.schedule_status(job['id'])['runtime'];identity=runtime.get('worker');alive=False
        if isinstance(identity,dict) and worker_identity(identity.get('pid'))==identity:
            try:
                arguments=psutil.Process(identity['pid']).cmdline()
                alive=job['id'] in arguments and any(value in arguments for value in ('explorer.job_runner','explorer.worker_bootstrap'))
            except psutil.Error:pass
        if job['kind'] in PHYSICAL_JOBS and job['status'] not in ('completed','failed','cancelled'):
            store.update_job(job['id'],status='cancelled',error='Supervisor restarted; a fresh explicit observed physical attempt is required')
            if alive:
                os.killpg(identity['pid'],signal.SIGCONT);os.killpg(identity['pid'],signal.SIGTERM)
        elif alive:adopted[job['id']]=AdoptedWorker(identity)
        elif job['status'] in ('running','pause_requested','resume_requested'):
            if job['status']=='pause_requested' and runtime.get('pause_request'):
                try:receipt=verified_pause_receipt(store.root/'jobs'/job['id'],runtime['pause_request'])
                except (OSError,ValueError,KeyError):receipt=None
                if receipt:
                    store.update_job(job['id'],status='queued' if runtime.get('auto_resume') and not runtime.get('user_pause_requested') else 'paused')
                    store.update_schedule_runtime(job['id'],phase='recovered_checkpoint_pause',memory_retained=False,worker=None,
                        checkpoint_pause_receipt=receipt,pause_request=None,exact_optimizer_resume=True)
                else:store.update_job(job['id'],status='queued',error='Supervisor restarted; resuming from persisted artifacts')
            else:store.update_job(job['id'],status='queued',error='Supervisor restarted; resuming from persisted artifacts')
        elif job['status']=='paused' and runtime.get('memory_retained'):
            store.update_schedule_runtime(job['id'],phase='paused_process_lost_after_supervisor_restart',memory_retained=False,worker=None,exact_optimizer_resume=False)
    return adopted


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
    return {'available_memory_bytes':memory.available,'total_memory_bytes':memory.total,'free_disk_bytes':disk.free,'total_disk_bytes':disk.total,
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


def executable_for(job,release=None,store=None):
    if job['kind']=='so101_skill_train' and job.get('config',{}).get('backend','act')=='diffusion':
        from .policy_runtime import qualified_diffusion_runtime
        selection=qualified_diffusion_runtime(release)
        pinned=job['config'].get('_policy_runtime_binding_sha256')
        if pinned is not None and pinned!=selection['binding_sha256']:raise ValueError('Pinned policy runtime differs from the current measured source binding')
        if store is not None:store.pin_job_policy_binding(job['id'],selection['binding_sha256'])
        return selection['executable']
    if job['kind'] in ('train_act','sim_pilot','manipulation_student') and job.get('config',{}).get('device')=='cuda':
        from .qualification_bindings import qualified_gpu_binding
        if release is None:
            raise ValueError('GPU dispatch requires the resolved pinned source release')
        runtime=Path.home()/'.local/share/explorer/gpu-venv/bin/python'
        selection=qualified_gpu_binding(job,release,runtime,
            Path.home()/'.local/share/explorer/state/qualification/immutable-source-bindings',
            required_binding_sha256=job.get('config',{}).get('_gpu_binding_sha256'))
        if store is not None:
            store.pin_job_gpu_binding(job['id'],selection['execution_commit'],selection['binding_sha256'])
        return selection['executable']
    return sys.executable


def execution_environment(config,store,job):
    from .qualification_bindings import resolve_job_release
    release=resolve_job_release(config,store,job)
    environment=dict(os.environ,PYTHONPATH=str(release/'00-control-center/src')+':'+str(release/'shared'))
    from .qualification_bindings import pipeline_graph_version
    if job.get('config',{}).get('device')=='cuda' and job['kind'] in ('train_act','sim_pilot','manipulation_student') and pipeline_graph_version(release,job['kind'])>=4:
        from explorer_shared.learning.runtime_profiles import act_worker_environment
        environment.update(act_worker_environment(job['config']))
    return environment


class Supervisor:
    """Testable one-tick scheduler; production uses the same durable jobs table."""
    def __init__(self,config,store,*,clock=time.monotonic,wall=time.time,resource_provider=None,popen=subprocess.Popen,quantum_s=60,processes=None):
        self.config,self.store,self.clock,self.wall=config,store,clock,wall
        self.resource_provider=resource_provider or (lambda:host_resources(store.root));self.popen=popen
        self.quantum_s=quantum_s;self.processes=dict(processes or {});self.last_charge=clock()
        self.started={identity:self._runtime(identity).get('last_start_monotonic',clock()) for identity in self.processes}

    def _runtime(self,identity):return self.store.schedule_status(identity)['runtime']

    def _pause(self,job,process,*,automatic=False,reason='user_pause'):
        folder=self.store.root/'jobs'/job['id'];runtime=self._runtime(job['id'])
        active=active_optimizer(folder,process.pid)
        pending=runtime.get('pause_request')
        if pending and pending.get('worker')==worker_identity(process.pid):
            if runtime.get('auto_resume') and not runtime.get('user_pause_requested'):
                automatic=True;reason=runtime.get('pause_reason',reason)
            raw=read_json(folder/'optimizer-active.json')
            if active and active['signature']==pending.get('signature'):return
            if isinstance(raw,dict) and raw.get('worker')==pending['worker'] and raw.get('signature')==pending.get('signature') and raw.get('phase')=='paused_checkpoint_saved':return
            if active is None:
                clear_pause_request(folder)
                self.store.update_schedule_runtime(job['id'],pause_request=None,pause_mode=None)
                if runtime.get('auto_resume') and not runtime.get('user_pause_requested'):
                    self.store.update_job(job['id'],status='running')
                    self.store.update_schedule_runtime(job['id'],phase='running',preemption_blocked_reason='optimizer_finished_before_pause_poll',auto_resume=False)
                    return
        if runtime.get('phase')=='starting' and not runtime.get('bootstrap_admitted'):
            self.store.update_schedule_runtime(job['id'],phase='starting',pause_reason=reason)
        elif active:
            request=request_pause(folder,active,uuid.uuid4().hex)
            self.store.update_schedule_runtime(job['id'],phase='waiting_for_actual_optimizer_checkpoint',pause_request=request,
                pause_mode='checkpoint_process_exit',auto_resume=automatic and not runtime.get('user_pause_requested',False),pause_reason=reason,
                memory_retained=True,exact_optimizer_resume=True)
            if job['status']!='pause_requested':self.store.update_job(job['id'],status='pause_requested')
        elif not automatic:
            if job['kind'] in PHYSICAL_JOBS:
                self.store.update_job(job['id'],status='cancel_requested',error='Physical attempts cannot pause; a fresh observed test is required')
            else:
                os.killpg(process.pid,signal.SIGSTOP)
                self.store.update_job(job['id'],status='paused')
                self.store.update_schedule_runtime(job['id'],phase='paused_process_memory_retained',pause_mode='SIGSTOP_no_exact_resume',
                    pause_request=None,memory_retained=True,exact_optimizer_resume=False,auto_resume=False,pause_reason=reason)
        else:self.store.update_schedule_runtime(job['id'],preemption_blocked_reason='no_active_source_bound_checkpoint_phase')

    def _finished(self,identity,process,code):
        self.processes.pop(identity);self.started.pop(identity,None);job=self.store.job(identity);runtime=self._runtime(identity)
        if job['status'] in ('pause_requested','resume_requested') and code==PAUSED_BEFORE_WORK_EXIT:
            receipt=read_json(self.store.root/'jobs'/identity/'worker-not-started.json')
            if not receipt or receipt.get('worker')!=runtime.get('worker') or receipt.get('launch_nonce')!=runtime.get('launch_nonce'):raise ValueError('Worker-before-start pause is not bound to its durable launch lease')
            resume=job['status']=='resume_requested'
            self.store.update_job(identity,status='queued' if resume else 'paused');self.store.update_schedule_runtime(identity,phase='resume_after_prework_pause' if resume else 'paused_before_work',memory_retained=False,worker=None,exact_optimizer_resume=None)
        elif job['status'] in ('pause_requested','resume_requested') and runtime.get('pause_request') and code in (PAUSED_EXIT_CODE,-999):
            receipt=verified_pause_receipt(self.store.root/'jobs'/identity,runtime['pause_request'])
            automatic=job['status']=='resume_requested' or runtime.get('auto_resume') and not runtime.get('user_pause_requested',False)
            self.store.update_job(identity,status='queued' if automatic else 'paused',error=None)
            self.store.update_schedule_runtime(identity,phase='preempted_checkpoint_waiting' if automatic else 'paused_checkpoint_process_exited',
                memory_retained=False,worker=None,checkpoint_pause_receipt=receipt,pause_request=None,exact_optimizer_resume=True,
                completed_updates=receipt['completed_steps'],process_exit_code=code)
        elif job['status']=='cancel_requested':
            self.store.update_job(identity,status='cancelled');self.store.update_schedule_runtime(identity,phase='cancelled_process_exited',memory_retained=False,worker=None)
        elif job['status'] not in TERMINAL:
            self.store.update_job(identity,status='failed',error='Worker exited without completed result or verified checkpoint pause; exit='+str(code))
            self.store.update_schedule_runtime(identity,phase='worker_failed',memory_retained=False,worker=None)
        else:self.store.update_schedule_runtime(identity,phase='worker_exited',memory_retained=False,worker=None,process_exit_code=code)

    def _start(self,job,metadata,expected_revision=None):
        identity=job['id'];folder=self.store.root/'jobs'/identity;folder.mkdir(parents=True,exist_ok=True)
        try:
            from .qualification_bindings import resolve_job_release
            release=resolve_job_release(self.config,self.store,job);executable=executable_for(self.store.job(identity),release,self.store)
            environment=execution_environment(self.config,self.store,job);cores=metadata['resources']['cpu_cores']
            environment.update(OMP_NUM_THREADS=str(cores),MKL_NUM_THREADS=str(cores),OPENBLAS_NUM_THREADS=str(cores))
            clear_pause_request(folder)
            allowed=sorted(os.sched_getaffinity(0));pool=allowed[min(2,max(0,len(allowed)-1)):]
            used={cpu for key in self.processes for cpu in self._runtime(key).get('worker_cpus',[])}
            cpus=[cpu for cpu in pool if cpu not in used][:cores]
            if len(cpus)<cores:raise ValueError('Reserved control/camera CPU pool has insufficient free worker cores')
            if not self.store.claim_job_start(identity,expected_schedule_revision=expected_revision):return
            nonce=uuid.uuid4().hex;bootstrap=(release/'00-control-center/src/explorer/worker_bootstrap.py').is_file()
            if bootstrap:environment['EXPLORER_WORKER_LEASE_NONCE']=nonce
            command=[executable,'-m','explorer.worker_bootstrap',identity,nonce] if bootstrap else [executable,'-m','explorer.job_runner',identity]
            with (folder/'worker.log').open('ab') as log:
                process=self.popen(command,stdout=log,stderr=subprocess.STDOUT,
                    start_new_session=True,env=environment,preexec_fn=lambda:os.sched_setaffinity(0,cpus))
            self.processes[identity]=process;self.started[identity]=self.clock()
            self.store.update_schedule_runtime(identity,phase='starting' if bootstrap else 'running',worker=worker_identity(process.pid),worker_cpus=cpus,
                launch_nonce=nonce,bootstrap_admitted=not bootstrap,waited_seconds=0,
                pause_request=None,memory_retained=True,started_wall_utc=self.wall(),last_start_monotonic=self.clock(),exact_optimizer_resume=None)
        except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError) as error:
            self.store.update_job(identity,status='failed',error=str(error));self.store.update_schedule_runtime(identity,phase='dispatch_blocked',wait_reasons=[str(error)])

    def tick(self):
        elapsed=max(0,self.clock()-self.last_charge);self.last_charge=self.clock()
        for identity,process in list(self.processes.items()):
            job=self.store.job(identity);code=process.poll()
            if code is not None:
                try:self._finished(identity,process,code)
                except (ValueError,OSError,KeyError) as error:
                    self.processes.pop(identity,None);self.started.pop(identity,None)
                    self.store.update_job(identity,status='failed',error='Pause/exit evidence rejected: '+str(error))
                    self.store.update_schedule_runtime(identity,phase='pause_or_exit_proof_rejected',memory_retained=False,worker=None)
            elif job['status']=='cancel_requested':
                os.killpg(process.pid,signal.SIGCONT);os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL)
            elif job['status']=='pause_requested':self._pause(job,process)
            elif job['status']=='resume_requested':
                runtime=self._runtime(identity)
                if runtime.get('pause_mode')=='checkpoint_process_exit' and runtime.get('pause_request'):
                    # The worker may already be committing its checkpoint. Finish
                    # that handshake, exit and immediately queue a new process.
                    self.store.update_schedule_runtime(identity,phase='waiting_checkpoint_then_resume',auto_resume=True,user_pause_requested=False)
                    self.store.update_job(identity,status='pause_requested')
                else:
                    os.killpg(process.pid,signal.SIGCONT);self.store.update_job(identity,status='running')
                    self.store.update_schedule_runtime(identity,phase='running',memory_retained=True,user_pause_requested=False)
            if code is None and job['status']!='paused':
                metadata=self.store.schedule_status(identity)['metadata'];budget=metadata['resources']
                self.store.charge_scheduling_usage(metadata['experiment_slug'],elapsed*(budget['cpu_cores']+2*budget['gpu_slots']))
        jobs=self.store.queue_jobs();known={job['id'] for job in jobs}
        jobs += [self.store.job(identity) for identity in self.processes if identity not in known]
        schedules={};revisions={};active=[]
        for job in jobs:
            if job['status'] not in TERMINAL or job['id'] in self.processes:
                record=self.store.schedule_job(job['id']);schedules[job['id']]=record['metadata'];revisions[job['id']]=record['revision']
                if job['status'] in ('queued','resume_requested') and job['id'] not in self.processes:
                    waited=record['runtime'].get('waited_seconds',0)+elapsed
                    self.store.update_schedule_runtime(job['id'],waited_seconds=waited)
                    schedules[job['id']]=dict(record['metadata'],_waiting_age_s=waited)
                if job['id'] not in self.processes:
                    if job['status']=='cancel_requested':self.store.update_job(job['id'],status='cancelled')
                    elif job['status']=='pause_requested':
                        self.store.update_job(job['id'],status='paused');self.store.update_schedule_runtime(job['id'],phase='paused_before_start',memory_retained=False)
                else:
                    try:resident=psutil.Process(self.processes[job['id']].pid).memory_info().rss
                    except (psutil.Error,OSError):resident=0
                    active.append(dict(job_id=job['id'],resources=record['metadata']['resources'],resident_memory_bytes=resident,process_stopped=job['status']=='paused'))
        resources=self.resource_provider();plan=queue_plan(jobs,schedules,resources,active,service_seconds=self.store.scheduling_usage(),now=self.wall())
        for identity,state in plan['waiting'].items():self.store.update_schedule_runtime(identity,phase='waiting',wait_reasons=state['reasons'],queue_score=state['effective_score'])
        for identity in plan['start']:self._start(self.store.job(identity),schedules[identity],revisions[identity])
        # Fair quantum and urgent inference can request only an ACTUAL active
        # checkpoint. Unsupported phases keep running; RAM is never claimed free.
        waiting=[self.store.job(identity) for identity in plan['ranked'] if identity in plan['waiting'] and not any(reason.startswith(('dependency_','scheduled_time')) for reason in plan['waiting'][identity]['reasons'])]
        pressure=resources['available_memory_bytes']<plan['UMA_runtime_reserve_bytes']
        checkpoint_pending=any(self.store.job(identity)['status']=='pause_requested' and self._runtime(identity).get('pause_mode')=='checkpoint_process_exit' for identity in self.processes)
        if (waiting or pressure) and not checkpoint_pending:
            urgent=any(job['kind'] in INFERENCE_JOBS for job in waiting)
            for identity,process in list(self.processes.items()):
                job=self.store.job(identity);metadata=self.store.schedule_status(identity)['metadata']
                if (job['status']=='running' and metadata['preemptible'] and job['kind'] in HEAVY_JOBS
                        and (pressure or urgent or self.clock()-self.started.get(identity,self.clock())>=self.quantum_s)):
                    other_role=any(schedules[item['id']]['experiment_slug']!=metadata['experiment_slug'] for item in waiting)
                    if pressure or urgent or other_role:
                        self._pause(job,process,automatic=True,reason='UMA_runtime_reserve_pressure' if pressure else 'inference_priority' if urgent else 'fairshare_quantum')
                        if self._runtime(identity).get('pause_request'):break
        return plan


def queue_status(store,resources=None):
    """Read-only queue API view; never starts a process or creates metadata."""
    jobs=store.queue_jobs();schedules={};rows=[];active=[]
    for job in jobs:
        record=store.schedule_status(job['id']);schedules[job['id']]=dict(record['metadata'],_waiting_age_s=record['runtime'].get('waited_seconds',0))
        runtime=record['runtime'];worker=runtime.get('worker')
        if isinstance(worker,dict) and worker_identity(worker.get('pid'))==worker:
            try:resident=psutil.Process(worker['pid']).memory_info().rss
            except psutil.Error:resident=0
            active.append(dict(job_id=job['id'],resources=record['metadata']['resources'],resident_memory_bytes=resident,process_stopped=job['status']=='paused'))
        rows.append(dict(id=job['id'],kind=job['kind'],project_id=job['project_id'],experiment_slug=job.get('experiment_slug'),status=job['status'],progress=job['progress'],scheduling=record,minimum_resources=record['minimum_resources']))
    selected_resources=resources or host_resources(store.root)
    return dict(schema='explorer_shared_durable_queue_v1',owner='shared_training_platform',authoritative_table='jobs',experiments=['01','02','03','04','05','06'],
        jobs=rows,resources=selected_resources,plan_preview=queue_plan(jobs,schedules,selected_resources,active,service_seconds=store.scheduling_usage()),
        service_budget_seconds=store.scheduling_usage(),read_only_preview=True,physical_execution_authorized=False,controller_settings_writes=0)


def supervise(config,store):
    lock=(store.root/'scheduler.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    adopted=recover_jobs(store);supervisor=Supervisor(config,store,processes=adopted)
    while True:
        supervisor.tick();time.sleep(1)
