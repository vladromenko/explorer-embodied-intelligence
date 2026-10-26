"""ENGINEERING_GLUE: central metadata lifecycle for admitted warm CPU workers.

No Torch/model/device import or inference process creation. Only the common
jobs-table queue and private instance-pinned IPC client are used.
"""
from copy import deepcopy
import re
import threading
import time
import uuid

from explorer_shared.contracts import digest
from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS
from explorer_shared.cooperative_policy_actor import NativeActorClient,read_lease,public_lease
from explorer_shared.learning.checkpoint_control import worker_identity
from .cooperative_policy_job import warm_request,binding_from_config,current_source
from .queue_policy import default_metadata


class CooperativeWarmService:
    def __init__(self,store,*,clock=time.monotonic_ns,socket_root=None):
        self.store,self.clock,self.socket_root=store,clock,socket_root
        self.lock=threading.RLock();self.closed=False
    def enqueue(self,session,spec,*,request_id=None,compute_ttl_s=120,authenticated_actor):
        if not authenticated_actor:raise ValueError('Authenticated compute session request required')
        settings=warm_request(self.store,session,spec,compute_ttl_s=compute_ttl_s)
        if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}',request_id)):
            raise ValueError('Bounded idempotent warm request ID required')
        request_id=request_id or 'one-compute-session-per-pinned-epoch'
        config={**settings,'cooperative_warm_request_id':request_id,'requested_by_actor':authenticated_actor}
        probe=default_metadata({'id':'preflight','kind':'cooperative_policy_warm','project_id':'05','config':config})
        if (probe['resources']['cpu_cores']!=4 or probe['resources']['memory_bytes']!=4*1024**3 or
                probe['resources']['gpu_slots']!=0 or probe['preemptible'] is not False):
            raise ValueError('Qualified shared two-head CPU4core/4GiB inference resource classification required')
        with self.lock:
            if self.closed:raise ValueError('Warm compute lifecycle is closed')
            current_source(self.store,settings)
            jobs=[row for row in self.store.jobs(10000) if row['kind']=='cooperative_policy_warm' and
                binding_from_config(row['config'])==settings]
            active=[row for row in jobs if row['status'] not in ('completed','failed','cancelled','cancel_requested')]
            if len(active)>1:raise ValueError('Duplicate active authoritative compute sessions require review')
            previous=active[0] if active else next((row for row in jobs if row['config'].get('cooperative_warm_request_id')==request_id),None)
            job=previous or self.store.create_job('cooperative_policy_warm','05',config)
            return self.snapshot_job(job,session)
    def _valid(self,job_id,settings,lease):
        try:
            job=self.store.job(job_id)
            if not job or job['kind']!='cooperative_policy_warm' or job['status']!='running' or binding_from_config(job['config'])!=settings:return False
            runtime=self.store.schedule_status(job_id)['runtime']
            if (runtime.get('worker')!=lease.get('worker') or runtime.get('launch_nonce')!=lease.get('launch_nonce') or
                    runtime.get('bootstrap_admitted') is not True):return False
            current_source(self.store,settings)
            return True
        except (ValueError,OSError,KeyError,TypeError):return False
    def snapshot_job(self,job,session):
        settings=binding_from_config(job['config']);result={'job_id':job['id'],'status':job['status'],
            'schema':'explorer_cooperative_warm_compute_status_v1','session_id':settings['session_id'],
            'cooperative_epoch':settings['cooperative_epoch'],'compute_ttl_s':settings['compute_ttl_s'],
            'schedule':self.store.schedule_status(job['id']),'compute_ready':False,'physical_execution_authorized':False,
            'native_transports_automatically_attached':False,'hardware_lease_renewed':False,
            'paired_runtime_benchmark_qualified':False,'blockers':[]}
        if (session.get('session_id')!=settings['session_id'] or session.get('epoch')!=settings['cooperative_epoch'] or
                session.get('process_session')!=settings['cooperative_process_session']):result['blockers'].append('COOPERATIVE_SESSION_PROCESS_EPOCH_CHANGED')
        try:
            lease=read_lease(self.store.root/'jobs'/job['id']);result['actor']=public_lease(lease)
            client=NativeActorClient(job['id'],self.store.root/'jobs'/job['id'],settings,
                actor_instance_id=lease.get('actor_instance_id'),valid=lambda value:self._valid(job['id'],settings,value),
                clock=self.clock,socket_root=self.socket_root)
            client._lease()
            # Ready describes actual CPU-loaded models only. It grants no
            # native execution; paired finite ML/RSS qualification is separate.
            models=lease.get('model_receipts') or {}
            if (models.get('model_pair_verified') is not True or models.get('software_model_fixture') is not False or
                    models.get('action_dimensions')!={'giver':9,'receiver':6}):
                result['blockers'].append('ACTUAL_HASH_BOUND_LOADED_MODEL_PAIR_NOT_VERIFIED')
            row,spec=current_source(self.store,settings)
            if warm_request(self.store,row,spec,compute_ttl_s=settings['compute_ttl_s'])!=settings:
                result['blockers'].append('ORIGINAL_NATIVE_MODEL_SOURCE_BINDING_CHANGED')
            result['compute_ready']=not result['blockers']
        except (ValueError,OSError,KeyError,TypeError) as error:
            result['blockers'].append('CURRENT_WARM_WORKER_NOT_READY:'+type(error).__name__+':'+str(error))
        return result
    def snapshots(self,session):
        return [self.snapshot_job(job,session) for job in self.store.jobs(10000) if job['kind']=='cooperative_policy_warm' and
            job.get('config',{}).get('session_id')==session['session_id']]
    def prediction_adapter(self,job_id,session,role):
        """Pure predictor only; caller must separately qualify/attach execution.

Pins one actual worker instance. Neither service restart, requeue nor a new
actor token can silently replace this model callback.
"""
        if role not in ('giver','receiver'):raise ValueError('Actual separate native role required')
        job=self.store.job(job_id)
        if not job or job['kind']!='cooperative_policy_warm':raise ValueError('Select an actual authoritative warm compute job')
        state=self.snapshot_job(job,session)
        if not state['compute_ready']:raise ValueError('Actual current two-head warm compute models not ready')
        settings=binding_from_config(job['config']);reference=settings['native_plans'][role]['reference']
        client=NativeActorClient(job_id,self.store.root/'jobs'/job_id,settings,actor_instance_id=state['actor']['actor_instance_id'],
            valid=lambda value:self._valid(job_id,settings,value),clock=self.clock,socket_root=self.socket_root)
        def predict(selected,*,dispatch=False,command=None):
            if dispatch is not False or selected!=reference:raise ValueError('Pure exact pinned native model reference required')
            result=client.predict(role)
            model={key:reference[key] for key in ('model_id','checkpoint_sha256','spec_sha256')}
            values=result.get('action9' if role=='giver' else 'predicted_native6')
            import math
            if (result.get('model')!=model or result.get('native_role')!=role or result.get('dispatch_performed') is not False or
                    result.get('physical_execution_authorized') is not False or result.get('action_contract')!=ROLE_CONTRACTS[role] or
                    not isinstance(values,list) or len(values)!=ROLE_CONTRACTS[role]['dimension'] or
                    any(type(value) not in (int,float) or not math.isfinite(value) for value in values) or
                    not isinstance(result.get('source_observation'),dict) or not result.get('source_evidence')):
                raise ValueError('Actual warm native model prediction binding differs')
            return result
        return predict
    def cancel_session(self,session):
        cancelled=[]
        for job in self.store.jobs(10000):
            settings=job.get('config') or {}
            if (job['kind']=='cooperative_policy_warm' and settings.get('session_id')==session['session_id'] and
                    job['status'] not in ('completed','failed','cancelled')):
                self.store.request_job_operation(job['id'],'cancel');cancelled.append(job['id'])
        return cancelled
    def close(self,process_session):
        with self.lock:
            self.closed=True
            for job in self.store.jobs(10000):
                if (job['kind']=='cooperative_policy_warm' and job.get('config',{}).get('cooperative_process_session')==process_session and
                        job['status'] not in ('completed','failed','cancelled')):
                    self.store.request_job_operation(job['id'],'cancel')
