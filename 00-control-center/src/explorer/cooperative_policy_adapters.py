"""ENGINEERING_GLUE: two native CPU shadow jobs in the authoritative queue.

No model/Torch import, actor, device, inference process or actuator request.
Queued fresh-source inference is evidence only, never a warm dispatch lease.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import threading

from explorer_shared.contracts import digest,canonical
from explorer_shared.cooperative_contract05 import native_role_reference,ROLE_CONTRACTS
from explorer_shared.cooperative_recording05 import write_projection,sha_file
from .queue_policy import default_metadata
from .rosmaster_shadow_job import shadow_request as native9_request
from .so101_shadow_job import shadow_request as native6_request


class QueuedNativeShadows:
    def __init__(self,store,root,*,current_session):
        self.store,self.root,self.current=store,Path(root),current_session
        self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()

    def _plan(self,session,spec):
        if (session.get('skill_id')!=spec.get('skill_id') or session.get('skill_version')!=spec.get('version') or
                session.get('spec_sha256')!=spec.get('spec_sha256')):raise ValueError('Exact cooperative source specification required')
        plans={}
        for role in ('giver','receiver'):
            reference=native_role_reference(role,spec['subskills'][role])
            if reference['experiment_slug']=='cross_embodiment_transfer':
                raise ValueError('TRANSFER_NATIVE9_SHADOW_PROVIDER_NOT_BOUND')
            settings=(native9_request if role=='giver' else native6_request)(self.store,reference.get('model_id'))
            if settings['model_checkpoint_sha256']!=reference.get('checkpoint_sha256'):
                raise ValueError('Pinned native '+role+' checkpoint differs')
            training=self.store.job(settings['model_job_id']);config=training.get('config') or {};result=training.get('result') or {}
            lineage=(config.get('skill_workflow') or {}) if role=='giver' else config
            if any(lineage.get(key)!=reference.get(target) for key,target in
                   (('skill_id','skill_id'),('skill_version','skill_version'),('spec_sha256','spec_sha256'))):
                raise ValueError('Pinned native '+role+' original skill/version/source context differs')
            # No policy import: actual worker performs the authoritative cohort,
            # processor, current camera/calibration and saved runtime checks.
            folder=Path(result.get('checkpoint_dir','')).resolve()
            if not folder.is_relative_to(self.store.root.resolve()):raise ValueError('Managed native checkpoint required')
            for name,expected in result['checkpoint_sha256'].items():
                path=(folder/name).resolve()
                if Path(name).name!=name or not path.is_relative_to(folder) or sha_file(path)!=expected:
                    raise ValueError('Actual native checkpoint changed')
            source=result.get('source_manifest_path');source_sha=result.get('source_manifest_sha256')
            source_path=Path(source or '').resolve()
            if (not source_path.is_relative_to(self.store.root.resolve()) or not source_path.is_file() or
                    not isinstance(source_sha,str) or sha_file(source_path)!=source_sha):
                raise ValueError('Actual native original cohort manifest binding required')
            plans[role]={'kind':'rosmaster_skill_shadow' if role=='giver' else 'so101_skill_shadow',
                'project_id':'03' if reference['experiment_slug']=='rosmaster_leader_learning' else '02' if role=='giver' else '04',
                'settings':dict(settings,device='cpu'), 'reference':reference,'source_manifest_sha256':source_sha,
                'checkpoint_files_sha256':deepcopy(result['checkpoint_sha256'])}
            probe=default_metadata({'id':'preflight','kind':plans[role]['kind'],'project_id':plans[role]['project_id'],
                'config':plans[role]['settings']})
            if (probe['resources']['gpu_slots']!=0 or probe['resources']['cpu_cores']!=2 or
                    probe['resources']['memory_bytes']!=2*1024**3 or probe['preemptible'] is not False):
                raise ValueError('Typed shared CPU inference resource classification differs')
        return plans

    def enqueue(self,session,spec,*,request_id=None):
        # Complete preflight BOTH heads before even the first queue insertion.
        plans=self._plan(session,spec)
        if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}',request_id)):
            raise ValueError('Bounded idempotent shadow request identity required')
        binding={'session_id':session['session_id'],'epoch':session['epoch'],'skill_id':spec['skill_id'],
            'skill_version':spec['version'],'spec_sha256':spec['spec_sha256'],'object_id':session['protocol_object_id'],
            'request_id':request_id or 'one-shadow-per-pinned-session-epoch','native_plans':plans}
        key=digest(binding);path=self.root/(key+'.json')
        def fresh():
            current=self.current(session['session_id'])
            if current['epoch']!=session['epoch'] or current.get('status')=='STOPPED':
                raise ValueError('Cooperative shadow request superseded by STOP/current epoch')
        with self.lock:
            fresh();receipt=json.loads(path.read_bytes()) if path.is_file() else {
                'schema':'explorer_cooperative_queued_shadow_v1','request_sha256':key,'binding':binding,
                'jobs':{},'errors':{},'action_spaces_merged':False,'dispatch_performed':False,
                'persistent_warm_actor_ready':False,'reusable_for_native_dispatch':False}
            for role,plan in plans.items():
                fresh()
                if role not in receipt['jobs']:
                    marker={'request_sha256':key,'session_id':session['session_id'],'epoch':session['epoch'],'role':role,
                        'cooperative_spec_sha256':spec['spec_sha256'],'native_reference':plan['reference'],
                        'source_manifest_sha256':plan['source_manifest_sha256']}
                    settings={**plan['settings'],'cooperative_shadow_binding':marker}
                    try:
                        # Recover a committed insertion whose return/receipt
                        # write was lost. Never enqueue missing halves on restart.
                        matches=[row for row in self.store.jobs(10000) if row['kind']==plan['kind'] and
                            row['project_id']==plan['project_id'] and row.get('config')==settings]
                        if len(matches)>1:raise ValueError('Duplicate authoritative shadow reservation requires review')
                        job=matches[0] if matches else self.store.create_job(plan['kind'],plan['project_id'],settings)
                        budget=default_metadata(job)
                        if (budget['resources']['gpu_slots']!=0 or budget['resources']['cpu_cores']!=2 or
                                budget['resources']['memory_bytes']!=2*1024**3 or budget['preemptible'] is not False):
                            raise ValueError('Typed shared CPU inference resource classification differs')
                        receipt['jobs'][role]={'job_id':job['id'],'config_sha256':digest(settings),'queue_budget':budget}
                        receipt['errors'].pop(role,None)
                    except Exception as error:receipt['errors'][role]=type(error).__name__+':'+str(error)
                    write_projection(path,receipt)
            receipt['complete_native_reservation']=set(receipt['jobs'])=={'giver','receiver'}
            receipt['current_session_epoch']=self.current(session['session_id'])['epoch']==session['epoch']
            write_projection(path,receipt)
        return self.inspect(receipt,session)

    def inspect(self,receipt,session):
        output=deepcopy(receipt);heads={};binding=receipt['binding'];current=binding['epoch']==session['epoch'] and session.get('status')!='STOPPED'
        for role,reserved in receipt['jobs'].items():
            job=self.store.job(reserved['job_id']);head={'job_id':reserved['job_id'],'status':job['status'] if job else 'MISSING',
                'action_contract':deepcopy(ROLE_CONTRACTS[role]),'verified_fresh_shadow_at_worker_time':False}
            try:
                if job is None or digest(job.get('config'))!=reserved['config_sha256']:
                    raise ValueError('Authoritative queued native shadow binding differs')
                head['schedule']=self.store.schedule_status(job['id'])
                if job['status']=='completed':head.update(self._completed(role,job,binding['native_plans'][role]))
            except (ValueError,OSError,KeyError,TypeError) as error:
                head['evidence_error']=type(error).__name__+':'+str(error)
            heads[role]=head
        output.update(native_heads=heads,current_session_epoch=current,
            both_native_inferences_verified=set(heads)=={'giver','receiver'} and all(
                head['verified_fresh_shadow_at_worker_time'] for head in heads.values()),
            observations_synchronized_between_robots=False,physical_mastery_qualified=False,
            reusable_for_native_dispatch=False,persistent_warm_actor_ready=False)
        return output

    def _completed(self,role,job,plan):
        result=job.get('result') or {};expected=plan['settings'];folder=self.store.root/'jobs'/job['id']
        if (result.get('model_job_id')!=expected['model_job_id'] or result.get('hardware_commands_dispatched') is not False or
                result.get('physical_execution_authorized') is not False or result.get('physical_control_requests_sent')!=0 or
                result.get('independent_full_task_success') is not False):raise ValueError('Native shadow returned unsupported physical claim')
        checkpoint=result.get('checkpoint_sha256') if role=='giver' else (result.get('lineage') or {}).get('checkpoint_sha256')
        if checkpoint!=expected['model_checkpoint_sha256']:raise ValueError('Actual native worker checkpoint differs')
        artifacts=result.get('artifacts') or {}
        if set(artifacts)!={'observation.json','frame.jpg','prediction.json'}:raise ValueError('Complete original shadow artifacts required')
        raw={}
        for name,metadata in artifacts.items():
            path=(folder/name).resolve()
            if not path.is_relative_to(folder.resolve()) or path.stat().st_size!=metadata.get('bytes') or sha_file(path)!=metadata.get('sha256'):
                raise ValueError('Native worker original artifact changed')
            if path.stat().st_size>16*1024**2:raise ValueError('Bounded native shadow artifact required')
            raw[name]=path.read_bytes()
        prediction=json.loads(raw['prediction.json']);packet=json.loads(raw['observation.json'])
        key='action9' if role=='giver' else 'predicted_native6';action=result.get(key)
        import math
        if (not isinstance(action,list) or len(action)!=ROLE_CONTRACTS[role]['dimension'] or prediction.get(key)!=action or
                any(type(value) not in (int,float) or not math.isfinite(value) for value in action)):
            raise ValueError('Finite native prediction/original result differs')
        if role=='giver':
            if result.get('state_profile')!='ROSMASTER_ACT9_RGB_AVAILABLE_SENSOR106_V1' or result.get('model_loaded_before_fresh_source') is not True:
                raise ValueError('Actual native106D warmed worker profile required')
            from .rosmaster_shadow_job import validate_packet
            _,payloads,_=validate_packet(packet)
            rgb=packet['observation']['channels']['camera_rgb']['value']
            if raw['frame.jpg']!=payloads.get(rgb['source_file']):raise ValueError('Original native9 JPEG binding differs')
        else:
            import hashlib
            if (result.get('transport_freshness_verified_by_worker') is not True or
                    hashlib.sha256(raw['frame.jpg']).hexdigest()!=(packet.get('observation') or {}).get('frame_sha256')):
                raise ValueError('Actual source6 worker transport/JPEG freshness required')
            lineage=result.get('lineage') or {}
            if any(lineage.get(key)!=plan['reference'].get(target) for key,target in
                   (('skill_id','skill_id'),('skill_version','skill_version'),('spec_sha256','spec_sha256'))):
                raise ValueError('Actual source6 worker original context differs')
        return {'verified_fresh_shadow_at_worker_time':True,'actions':[action],
            'checkpoint_sha256':checkpoint,'source_evidence':{'source_observation_id':result.get('source_observation_id'),
                'original_artifacts_sha256':{name:item['sha256'] for name,item in artifacts.items()}},
            'dispatch_performed':False,'physical_mastery_qualified':False}

    def snapshots(self,session):
        rows=[]
        for path in sorted(self.root.glob('*.json')):
            receipt=json.loads(path.read_bytes())
            if receipt.get('binding',{}).get('session_id')==session['session_id']:rows.append(self.inspect(receipt,session))
        return rows
