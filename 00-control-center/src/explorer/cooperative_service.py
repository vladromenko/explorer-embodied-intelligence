"""ENGINEERING_GLUE: real control-center assembly for cooperative Experiment05.

No app import/global state, model import, network request or control selection in
bootstrap. All physical execution defaults disabled. Existing public source
workflows own admission/training; clients address only configured private nodes.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
import re

import httpx

from explorer_shared.contracts import digest
from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS,PHASES,native_role_reference
from explorer_shared.cooperative_native05 import NativeMetadataRecorder,public_capture_preparers
from explorer_shared.cooperative_providers05 import existing_workflow_providers,contract_projection,phase_ids
from explorer_shared.cooperative_recording05 import CachedNodeObservation,CooperativeRecorder,write_original,write_projection,canonical,sha_file
from explorer_shared.cooperative_transport05 import NativeGiverPolicyTransport
from explorer_shared.cooperative_workflow05 import CooperativeWorkflow,LEGAL_STATES
from explorer_shared.handover_adapters import OperatorEvidenceInbox
from . import skill_workflow as mobile
from . import lab_skill_workflow as lab
from .mobile_dataset_evidence import inspect_mobile_import
from .cooperative_policy_adapters import QueuedNativeShadows
from .cooperative_warm_service import CooperativeWarmService
from .cooperative_observed_service import CooperativeObservedService


def _boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


class CooperativeService:
    def __init__(self,config,store,*,root=None,client=None,model_adapters=None,clock=time.monotonic_ns):
        self.config,self.store,self.clock=deepcopy(config),store,clock
        self.root=Path(root or store.root/'cooperative05');self.root.mkdir(parents=True,exist_ok=True)
        self.domain='nx:'+_boot();self.lock=threading.RLock();self.closed=False;self.model_errors={}
        self.client=client or httpx.Client(timeout=3,trust_env=False,follow_redirects=False)
        self.own_client=client is None;self.models=model_adapters or {};self.transports={};self.native_bindings={}
        self.nodes={node['id']:deepcopy(node) for node in config.get('nodes',[]) if node.get('id') in ('nano','pi')}
        self.preparers=public_capture_preparers(store,rosmaster_create_session=mobile.create_session,
            rosmaster_prepare_capture=mobile.prepare_capture_request,lab_create_session=lab.create_session,lab_capture_context=lab.capture_context)
        self.producers={role:NativeMetadataRecorder(role,self._request(role),lambda context,_role=role:self._prepare_context(_role,context),
            capture_started=self._capture_started if role=='giver' else None) for role in ('giver','receiver')}
        bindings={slug:self._provider_bindings('giver',slug) for slug in ('rosmaster_keyboard_learning','rosmaster_leader_learning')}
        bindings['so101_source_learning']=self._provider_bindings('receiver','so101_source_learning')
        self.registry=existing_workflow_providers(store,rosmaster_workflow=mobile.workflow,rosmaster_aggregate=mobile.build_aggregate,
            rosmaster_training=mobile.training_request,lab_workflow=lab.state_for_skill,lab_readiness=lab.model_readiness,
            lab_aggregate=lab.build_aggregate,bindings=bindings)
        self.inbox=OperatorEvidenceInbox(self.root/'evidence',clock=clock,clock_domain=self.domain)
        sources={}
        for role,node_id in (('giver','nano'),('receiver','pi')):
            node=self.nodes.get(node_id)
            if node:sources[role]=CachedNodeObservation(self._origin(node),node['token'],role,clock=clock)
        self.sources=sources
        self.recorder=CooperativeRecorder(self.root/'recordings',sources,clock_domain=self.domain,clock=clock) if len(sources)==2 else None
        self.workflow=CooperativeWorkflow(self.root/'workflow',self.registry,self.recorder,self.inbox,
            clock_domain=self.domain,clock=clock,authority=self._authority,managed_root=store.root)
        self.queued_shadows=QueuedNativeShadows(store,self.root/'queued-shadows',current_session=self.workflow.snapshot)
        self.warm_compute=CooperativeWarmService(store,clock=clock)
        self.observed_setup=CooperativeObservedService(self.workflow,{role:self._request(role) for role in ('giver','receiver')},
            self._observed_binary,inspect_native=self._inspect_skill,has_prior_evidence=self._prior_test_evidence,clock=clock)
    def _prior_test_evidence(self,row):
        with self.store.connect() as db:
            jobs=list(db.execute("SELECT id,kind,config FROM jobs WHERE json_extract(config,'$.session_id')=? OR json_extract(config,'$.cooperative_shadow_binding.session_id')=?",
                (row['session_id'],row['session_id'])))
        for job in jobs:
            if job['kind'] in ('rosmaster_skill_shadow','so101_skill_shadow'):return True
            if job['kind']=='cooperative_policy_warm' and any((self.store.root/'jobs'/job['id']/'inferences').glob('*')):return True
        for path in (self.workflow.root/'shadows').glob('*.json'):
            if json.loads(path.read_bytes()).get('session_id')==row['session_id']:return True
        return False
    def _observed_binary(self,role,path):
        prefix='/native-activation/frame/' if role=='giver' else '/station/observed-test/challenges/'
        valid=(re.fullmatch(re.escape(prefix)+'[0-9a-f]{32}'+('' if role=='giver' else '/frame'),path) if role in ('giver','receiver') else None)
        if role=='receiver':valid=valid or re.fullmatch('/station/observations/[0-9a-f]{32}/frame',path)
        if self.closed or not valid:raise ValueError('Fixed current source challenge image only')
        node=self.nodes.get('nano' if role=='giver' else 'pi')
        if node is None:raise ValueError('Configured source camera node required')
        with self.client.stream('GET',self._origin(node)+path,headers={'Authorization':'Bearer '+node['token']}) as response:
            if response.status_code!=200:raise ValueError('Pinned source challenge image unavailable: HTTP'+str(response.status_code))
            chunks=[];size=0
            for chunk in response.iter_bytes():
                size+=len(chunk)
                if size>8*1024**2:raise ValueError('Pinned source image exceeds budget')
                chunks.append(chunk)
            return b''.join(chunks),dict(response.headers)
    @staticmethod
    def _origin(node):
        host=node.get('host');port=node.get('port')
        if not isinstance(host,str) or any(char in host for char in '/@?#') or type(port) is not int or not 1<=port<=65535:
            raise ValueError('Only explicitly configured private node origins are allowed')
        return 'http://'+host+':'+str(port)
    def _request(self,role):
        def request(method,path,body):
            cleanup=(method=='GET' and path in ('/native-capture/status','/station/status')) or (
                method=='POST' and path in ('/native-capture/record/finish','/station/record/finish',
                    '/station/observed-test/revoke','/legacy/explorer/retarget/activation/revoke'))
            if self.closed and not cleanup:raise ValueError('Cooperative service is closed')
            node=self.nodes.get('nano' if role=='giver' else 'pi')
            if node is None:raise ValueError('Configured native '+role+' node unavailable')
            if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or '#' in path:
                raise ValueError('Fixed server-owned node path required')
            response=self.client.request(method,self._origin(node)+path,headers={'Authorization':'Bearer '+node['token']},
                json=body if body is not None else None)
            if response.status_code!=200:raise ValueError('Native '+role+' API rejected '+path.split('?')[0]+':HTTP'+str(response.status_code))
            if len(response.content)>16*1024**2:raise ValueError('Native metadata reply exceeds bound')
            value=response.json()
            if not isinstance(value,dict):raise ValueError('Typed native metadata object required')
            return value
        return request
    def _capture_started(self,prepared,identity):
        return mobile.capture_started(self.store,prepared['source_capture']['capture_id'],identity)
    def _prepare_context(self,role,context):
        prepared=self.preparers[role](context)
        native=prepared['body']['skill_context'] if role=='giver' else prepared['body']['context']
        native['cooperative_source_execution_domain']='REAL'
        native['cooperative_domain_basis']='configured_physical_source_declared_by_server_not_independent_task_success'
        return prepared
    def _record_stage(self,role,reference,phase,coordinator):
        result=self.producers[role].record_stage(reference,phase,coordinator)
        if role=='giver':
            result={'native':result,'source_workflow':mobile.capture_stage(self.store,reference['source_capture']['capture_id'],phase)}
        return result
    def _record_finish(self,role,reference,outcome):
        if role=='giver':
            capture=reference['source_capture'];status=outcome.get('status','unknown')
            if status not in ('success','failure','unknown'):status='unknown'
            review=deepcopy(outcome.get('review') or {})
            if outcome.get('method')!='operator_observation':review={};status='unknown'
            native=mobile.native_outcome_evidence(self.store,capture['capture_id'],reference['recording_id'],review,status)
            result=self.producers[role].record_finish(reference,native)
            binding=mobile.capture_finished(self.store,capture['capture_id'],reference['recording_id'],{**review,'outcome':status})
            return {'native':result,'source_workflow':binding}
        return self.producers[role].record_finish(reference,outcome)
    def _provider_bindings(self,role,slug):
        return {'inspect_skill':lambda ref:self._inspect_skill(role,ref),
            'record_start':self.producers[role].record_start,
            'record_stage':lambda ref,phase,context:self._record_stage(role,ref,phase,context),
            'record_finish':lambda ref,outcome:self._record_finish(role,ref,outcome),
            'inspect_episode':lambda reference:self._inspect_episode(role,reference),
            'predict_shadow':(lambda ref,request:self._shadow(role,ref)) if callable(self.models.get(role)) else None,
            'observe_state':lambda:self._observed_state(role),
            'submit_observed':lambda command,authorization:self._submit(role,command,authorization),
            'stop':lambda run:self._stop_native(role,run),
            'arm_release_guard':self._arm_guard if role=='giver' else None,
            'prepare_release':self._prepare_release if role=='giver' else None,
            # Capability describes this concrete binder; readiness still checks
            # current owner/model/observation and defaults authority to false.
            'native_dispatch_receipts':role=='giver','authoritative_release_deadline':role=='giver'}
    def _models_for(self,role,skill_id,version,spec_sha):
        rows=[]
        for job in self.store.jobs(10000):
            config=job.get('config') or {};result=job.get('result') or {}
            lineage=config.get('skill_workflow') or config
            if (job['status']=='completed' and job['kind']==('rosmaster_skill_train' if role=='giver' else 'so101_skill_train') and
                    lineage.get('skill_id')==skill_id and lineage.get('skill_version')==version and lineage.get('spec_sha256')==spec_sha and
                    all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed'))):
                try:
                    checkpoint=result.get('checkpoint_sha256')
                    if not isinstance(checkpoint,dict) or not checkpoint or result.get('optimizer_steps',0)<1:
                        raise ValueError('Actual saved checkpoint/optimizer inventory required')
                    root=Path(result.get('checkpoint_dir','')).resolve()
                    if not root.is_relative_to(self.store.root.resolve()):raise ValueError('Model checkpoint escaped managed state')
                    for name,expected in checkpoint.items():
                        if Path(name).name!=name or not (root/name).resolve().is_relative_to(root) or sha_file(root/name)!=expected:
                            raise ValueError('Native model checkpoint changed')
                    if role=='giver':
                        path=Path(result['model_manifest']).resolve()
                        if not path.is_relative_to(self.store.root.resolve()) or sha_file(path)!=result['model_manifest_sha256']:
                            raise ValueError('Actual native9 model manifest changed')
                        saved=json.loads(path.read_bytes())
                        if saved.get('weights_sha256')!=checkpoint or saved.get('skill_id')!=skill_id or saved.get('skill_version')!=version or saved.get('skill_spec_sha256')!=spec_sha:
                            raise ValueError('Native9 saved model lineage differs')
                    rows.append({'model_id':job['id'],'checkpoint_sha256':digest(checkpoint),'spec_sha256':spec_sha,
                        'backend':config.get('backend','act'),'source_kind':result.get('source_kind'),
                        'trained_offline':True,'physical_execution_qualified':False})
                    with self.lock:self.model_errors.pop(job['id'],None)
                except (OSError,ValueError,KeyError,TypeError) as error:
                    with self.lock:self.model_errors[job['id']]=type(error).__name__+':'+str(error)
        return rows
    def _inspect_skill(self,role,reference):
        state=(mobile.workflow(self.store,reference['skill_id'],reference['skill_version'],project_id='03' if reference['experiment_slug']=='rosmaster_leader_learning' else '02')
               if role=='giver' else lab.state_for_skill(self.store,reference['skill_id'],reference['skill_version']))
        spec=state['spec'];models=self._models_for(role,spec['skill_id'],spec['version'],state['spec_sha256'])
        chosen=next((model for model in models if model['model_id']==reference.get('model_id')),None)
        return {'skill_id':spec['skill_id'],'skill_version':spec['version'],'spec_sha256':state['spec_sha256'],
            'confirmed':state['confirmed'],'action_contract':contract_projection(spec.get('action_profile') or spec.get('action_contract'),role),
            'phase_ids':phase_ids(spec),'model':chosen,'native_models':models,'train_ready':state['train_ready'],
            'physical_task_qualified':False,'source_workflow':state}
    def native_choices(self):
        choices={'giver':[],'receiver':[]}
        for role,selected in (('giver',mobile.list_skills(self.store)),('receiver',lab.list_skills(self.store))):
            for row in selected:
                if row['confirmed']:
                    spec=row['spec'];slug='rosmaster_keyboard_learning' if role=='giver' else 'so101_source_learning'
                    reference={'experiment_slug':slug,'skill_id':spec['skill_id'],'skill_version':spec['version'],
                        'spec_sha256':row['spec_sha256'],'action_contract':deepcopy(ROLE_CONTRACTS[role]),'phase_map':{},'execution_scope':'whole_skill'}
                    variants=deepcopy((spec.get('coverage_plan') or spec.get('demo_plan') or {}).get('variants',[]))
                    models=self._models_for(role,spec['skill_id'],spec['version'],row['spec_sha256'])
                    choice={'reference':reference,'name':spec.get('title',spec['skill_id']),'phase_ids':phase_ids(spec),
                        'native_variants':variants,'models':models,'confirmed':True}
                    choice['choice_id']=digest(reference);choices[role].append(choice)
        return choices
    def _choice(self,role,identity):
        row=next((row for row in self.native_choices()[role] if row['choice_id']==identity),None)
        if row is None:raise ValueError('Select a current confirmed native '+role+' skill version')
        return row
    def _native_original(self,role,job):
        result=job.get('result') or {}
        if role=='giver':
            report=inspect_mobile_import(job,managed_root=self.store.root,include_manifest=True)
            artifacts=(report.get('mobile_embodied') or {}).get('source_artifacts',[])
            source=next((item for item in artifacts if item.get('relative_name')=='sources/skill-context.json'),None)
            context={}
            if source:
                path=Path(source['path']).resolve()
                if not path.is_relative_to(self.store.root.resolve()) or sha_file(path)!=source['sha256']:raise ValueError('Native original context changed')
                context=json.loads(path.read_bytes())
            envelope=(report.get('mobile_embodied') or {}).get('envelope') or {}
            return report,context,envelope
        path=Path(result.get('observation_manifest',result.get('source_manifest_path',''))).resolve()
        if not path.is_relative_to(self.store.root.resolve()) or not path.is_file():raise ValueError('Original SO101 source is not managed')
        expected=result.get('source_manifest_sha256')
        if expected is None or sha_file(path)!=expected:raise ValueError('Original SO101 manifest SHA changed/missing')
        from explorer_sim.so101_dataset_qualification import inspect_station_episode
        report=inspect_station_episode(path);raw=json.loads(path.read_bytes())
        return report,raw.get('source_context') or {},raw
    def _inspect_episode(self,role,reference):
        job=self.store.job(reference.get('import_job_id',''))
        if not job or job['status']!='completed' or job['kind'] not in (('mobile_import','retarget_import') if role=='giver' else ('station_import',)):
            raise ValueError('Actual completed native role import required')
        report,context,raw=self._native_original(role,job)
        skill_id=context.get('skill_id') or (raw.get('skill') or {}).get('id')
        version=context.get('skill_version') or (raw.get('skill') or {}).get('version')
        state=mobile.workflow(self.store,skill_id,version) if role=='giver' else lab.state_for_skill(self.store,skill_id,version)
        original=report.get('original_episode_id',report.get('episode_id'))
        decision=next((item for item in state['assignments'] if item['binding']['original_episode_id']==original),None)
        source_kind=report.get('source_kind') if role=='receiver' else (report.get('mobile_embodied') or {}).get('source_kind')
        domain='SIMULATION' if source_kind in ('simulation','SIMULATION') else 'REAL' if source_kind in ('physical','SO101_RECORDED_DEMONSTRATIONS') else None
        nested=context.get('context') or {}
        declared=context.get('cooperative_source_execution_domain') or nested.get('cooperative_source_execution_domain')
        if domain is None and declared in ('REAL','SIMULATION'):domain=declared
        return {'admitted_for_training':bool(decision and decision['accepted']),
            'eligible_as_successful_bc_expert':bool(decision and decision['accepted']),
            'reasons':deepcopy(decision.get('reasons',[]) if decision else ['NATIVE_SOURCE_BINDING_AND_VIDEO_PHASE_REVIEW_REQUIRED']),
            'skill_id':skill_id,'skill_version':version,'spec_sha256':state['spec_sha256'],'action_contract':deepcopy(ROLE_CONTRACTS[role]),
            'original_episode_id':original,'split':(decision['binding']['split'] if decision else context.get('split')),
            'execution_domain':domain,'cooperative_parent_session_id':context.get('cooperative_parent_session_id') or nested.get('cooperative_parent_session_id'),
            'execution_domain_basis':'retained_original_source_kind_or_explicit_server_context_not_inferred_from_page',
            'source_manifest_sha256':report.get('manifest_sha256',report.get('source_manifest_sha256')),
            'source_artifact_sha256':deepcopy(report.get('embodied_source_files') or {}),'native_admission_binding':deepcopy(decision)}
    def _episode_choices(self,session):
        choices={'giver':[],'receiver':[]}
        for job in self.store.jobs(10000):
            role='giver' if job['kind'] in ('mobile_import','retarget_import') else 'receiver' if job['kind']=='station_import' else None
            if role is not None and job['status']=='completed':
                try:report=self._inspect_episode(role,{'import_job_id':job['id']})
                except (ValueError,OSError,KeyError,TypeError):report=None
                if report is not None and report.get('cooperative_parent_session_id')==session['session_id']:
                    value={'import_job_id':job['id'],'original_episode_id':report['original_episode_id'],
                        'admitted_for_training':report['admitted_for_training'],'reasons':report['reasons'],
                        'review_url':'/experiments/04' if role=='receiver' else '/experiments/'+str(job['project_id']),
                        'timeline_url':'/api/lab/skills/imports/'+job['id']+'/timeline' if role=='receiver' else None}
                    value['choice_id']=digest({'role':role,**value});choices[role].append(value)
        return choices
    def _authority(self,row):
        transport=self.transports.get((row['session_id'],'giver'));authority=self.native_bindings.get(row['session_id'])
        receiver=self.transports.get((row['session_id'],'receiver'))
        current=bool(row.get('observed_test_binding') and row['execution_domain']=='user_observed_physical_test' and
            authority and authority['epoch']==row['epoch'] and transport and receiver and
            transport.observe_state()['current_native_owner_ready'] and receiver.observe_state()['current_native_owner_ready'])
        return {'explicit_user_observed_test_current':current,'source_receipt':deepcopy(authority),
            'reason':None if current else 'BOTH_EXACT_SCOPED_NATIVE_TRANSPORTS_AND_CURRENT_USER_LEASES_REQUIRED'}
    def _observed_state(self,role):
        with self.lock:candidates=[transport for (session,kind),transport in self.transports.items() if kind==role]
        if len(candidates)==1:return candidates[0].observe_state()
        return {'current_native_owner_ready':False,'source_receipt':{'reason':'NO_CURRENT_OBSERVED_COOPERATIVE_BINDING'}}
    def attach_native_giver(self,session_id,body,*,authenticated_actor):
        """Attach only an ALREADY separately observed/resumed native owner.

No select/begin/resume/lease is called. A real Root-owned model adapter must be
bound first; default construction cannot turn on policy execution.
"""
        row=self.workflow.snapshot(session_id);reference=self.workflow.skill(row['skill_id'],row['skill_version'])['spec']['subskills']['giver']
        if (row['epoch']!=body.get('expected_epoch') or row['execution_domain']!='user_observed_physical_test' or
                body.get('observing') is not True or body.get('user_observed_test_confirmed') is not True or
                not callable(self.models.get('giver'))):raise ValueError('Explicit current observed session and actual model adapter required')
        binding={'run_id':session_id,'object_id':row['protocol_object_id'],'coordinator_epoch':row['epoch'],
                 'authorization_id':body.get('authorization_id')}
        expected=self._inspect_skill('giver',reference).get('model')
        if expected is None:raise ValueError('Actual pinned model/checkpoint unavailable')
        def predict(command):return self.models['giver'](deepcopy(command['reference']),dispatch=False,command=deepcopy(command))
        transport=NativeGiverPolicyTransport(self._request('giver'),binding,body.get('control_epoch'),predict,
            clock_domain=self.domain,clock=self.clock,receiver_command=self._receiver_command)
        state=transport.observe_state()
        if not state['current_native_owner_ready']:raise ValueError('Original native owner does not confirm this exact already observed session')
        with self.lock:
            if self.workflow.snapshot(session_id)['epoch']!=row['epoch']:raise ValueError('Observed attachment superseded by STOP')
            self.transports[(session_id,'giver')]=transport
            self.native_bindings[session_id]={'actor':authenticated_actor,'epoch':row['epoch'],'native_source_receipt':state,
                'basis':'existing_native_observed_owner_not_model_physical_qualification'}
        return state
    def _receiver_command(self):
        state=self._request('receiver')('GET','/station/status',None);command=state.get('last_command') or {}
        if (not isinstance(command.get('dispatched'),list) or len(command['dispatched'])!=6 or
                not command.get('clock_domain') or type(command.get('monotonic_ns')) is not int):
            raise ValueError('Actual original receiver servo-loop publication receipt not exposed')
        return {'command_id':'receiver-native-'+digest(command),'actual_native_host_dispatch':True,'source_receipt':command}
    def _shadow(self,role,reference):
        provider=self.models.get(role)
        if not callable(provider):raise ValueError('ACTUAL_'+role.upper()+'_FRESH_NATIVE_OBSERVATION_MODEL_ADAPTER_NOT_BOUND')
        return provider(deepcopy(reference),dispatch=False)
    def _submit(self,role,command,authorization):
        transport=self.transports.get((command['run_id'],role))
        if transport is None:raise ValueError('CURRENT_NATIVE_'+role.upper()+'_OBSERVED_TRANSPORT_NOT_BOUND')
        return transport.submit_observed(command,authorization)
    def _stop_native(self,role,run):
        transport=self.transports.get((run,role))
        if transport is None:return {'native_session_was_not_owned':True,'no_command_sent':True}
        return transport.stop(run)
    def _arm_guard(self,row):
        transport=self.transports.get((row['session_id'],'giver'))
        if transport is None:raise ValueError('Current observed original giver binding required')
        return transport.arm_release_guard(row)
    def _prepare_release(self,row,after):
        transport=self.transports.get((row['session_id'],'giver'))
        if transport is None:raise ValueError('Current observed original giver binding required')
        return transport.prepare_release(row,after)
    def snapshot(self,session_id=None):
        workbench=self.workflow.snapshot();selected=self.workflow.snapshot(session_id) if session_id else None
        for row in workbench['sessions']:
            row['legal_events']=[event for event,states in LEGAL_STATES.items() if row['protocol_state'] in states]
            row['readiness']=self.workflow.readiness(row['session_id'])
            if row['mode']!='both_manual':
                for role in ('giver','receiver'):
                    if self.workflow._policy_role(row,role) and (row['session_id'],role) not in self.transports:
                        row['readiness']['observed_execution_blockers'].append(role+':CURRENT_OBSERVED_NATIVE_TRANSPORT_NOT_ATTACHED')
                row['readiness']['observed_policy_steps_supported']=not row['readiness']['observed_execution_blockers']
        if selected is not None:selected=next(row for row in workbench['sessions'] if row['session_id']==session_id)
        cameras={role:{'camera_id':camera,'preview_url':'/api/cameras/'+camera+'/snapshot','snapshot_url':'/api/cameras/'+camera+'/snapshot','stream_url':'/api/cameras/'+camera+'/stream',
            'source':'existing_native_cache','online_verified_by_this_snapshot':False} for role,camera in (('giver','nano-rgbd'),('receiver','pi-overview'))}
        native_choices=self.native_choices()
        with self.lock:model_errors=deepcopy(self.model_errors)
        warm_compute=self.warm_compute.snapshots(selected) if selected else []
        observed=self.observed_setup.snapshot(selected,self.workflow.skill(selected['skill_id'],selected['skill_version'])['spec']) if selected else None
        return {'schema':'explorer_cooperative_service_v1','workbench':workbench,'catalogue':self.workflow.catalogue(),
            'native_choices':native_choices,'pending_evidence':self.inbox.pending(),'cameras':cameras,
            'selected_session':selected,'native_episode_choices':self._episode_choices(selected) if selected else {'giver':[],'receiver':[]},
            'missing_native_providers':['cross_embodiment_transfer'],'physical_execution_default':False,
            'model_source_errors':model_errors,
            'queued_native_shadows':self.queued_shadows.snapshots(selected) if selected else [],
            'queued_native_shadow_supported':True,
            'warm_compute_sessions':warm_compute,
            'observed_setup':observed,
            'execution_authority':self._authority(selected) if selected else {'explicit_user_observed_test_current':False},
            'persistent_warm_model_actor_ready':any(row['compute_ready'] for row in warm_compute),
            'model_adapters_bound':{role:callable(self.models.get(role)) for role in ('giver','receiver')}}
    def artifact(self,kind,identity,role=None):
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('Server-issued source artifact ID required')
        if kind=='zone' and role in ('giver','receiver'):
            folder=self.root/'workflow/zones'/identity;metadata=json.loads((folder/'registration.json').read_bytes())
            name=role+'-original.jpg';path=folder/name
            if metadata['zone_id']!=identity or sha_file(path)!=metadata['source_files_sha256'][name]:raise ValueError('Original zone JPEG changed')
            media='image/jpeg'
        elif kind=='recording' and role is None:
            folder=self.root/'recordings'/identity;metadata=json.loads((folder/'archive-info.json').read_bytes());path=folder/'recording.tar'
            if metadata['recording_id']!=identity or sha_file(path)!=metadata['archive_sha256']:raise ValueError('Original cooperative archive changed')
            media='application/x-tar'
        else:raise ValueError('Fixed source artifact kind/role required')
        if not path.resolve().is_relative_to(self.root.resolve()) or path.is_symlink():raise ValueError('Managed original artifact required')
        return path,media
    def train(self,skill_id,version,settings=None):
        """Explicit user enqueue through BOTH existing native workflow owners.

Preflight precedes either enqueue. A partial reservation is durable, visible and
idempotent; process restart never queues its missing half automatically.
"""
        from .training_options import so101_training_settings
        settings=settings or {}
        if not isinstance(settings,dict) or set(settings)-{'giver','receiver'}:raise ValueError('Separate native role learner settings required')
        receiver_settings=so101_training_settings(settings.get('receiver',{}))
        if receiver_settings['backend']=='diffusion':
            from .policy_runtime import qualified_diffusion_runtime
            qualified_diffusion_runtime()
        accepted=self.workflow.build_aggregate(skill_id,version);spec=self.workflow.skill(skill_id,version)['spec']
        plan=self.workflow.training_requests(skill_id,version,settings)
        if not plan['ready']:raise ValueError('Native training preflight blocked: '+','.join(plan['blockers']))
        key=digest({'spec':spec['spec_sha256'],'cooperative_aggregate':accepted['aggregate_manifest_sha256'],'settings':settings or {}})
        path=self.root/'training-requests'/(key+'.json');path.parent.mkdir(exist_ok=True)
        with self.lock:
            receipt=json.loads(path.read_bytes()) if path.is_file() else {
                'schema':'explorer_cooperative_native_training_reservation_v1','skill_id':skill_id,'skill_version':version,
                'spec_sha256':spec['spec_sha256'],'cooperative_aggregate_sha256':accepted['aggregate_manifest_sha256'],
                'jobs':{},'errors':{},'action_spaces_merged':False,'coordinator_trained':False}
            for role in ('giver','receiver'):
                if role not in receipt['jobs']:
                    ref=spec['subskills'][role]
                    try:
                        if role=='giver':
                            expected=plan['requests']['giver'];config=expected['config']
                            previous=next((item for item in self.store.jobs(10000) if item['kind']==expected['kind'] and
                                item['project_id']==expected['project_id'] and item['status'] not in ('failed','cancelled') and
                                all((item.get('config') or {}).get(key)==value for key,value in config.items())),None)
                            job=previous or mobile.enqueue_training(self.store,ref['skill_id'],ref['skill_version'],
                                deepcopy((settings or {}).get(role,{})),project_id='03' if ref['experiment_slug']=='rosmaster_leader_learning' else '02')
                        else:job=lab.enqueue_training(self.store,ref['skill_id'],ref['skill_version'],
                            {**deepcopy((settings or {}).get(role,{})),'request_id':'cooperative-'+key+'-receiver'})
                        receipt['jobs'][role]=job;receipt['errors'].pop(role,None)
                    except Exception as error:receipt['errors'][role]=type(error).__name__+':'+str(error)
                    write_projection(path,receipt)
            receipt['complete_native_reservation']=set(receipt['jobs'])=={'giver','receiver'}
            write_projection(path,receipt);return receipt
    def command(self,operation,body,*,authenticated_actor):
        if self.closed or not isinstance(body,dict) or not authenticated_actor:raise ValueError('Current authenticated service command required')
        session=body.get('session_id');identity=body.get('skill_id');version=body.get('version')
        if session is not None and operation!='confirm_evidence':
            current=self.workflow.snapshot(session)
            if body.get('expected_epoch')!=current['epoch']:raise ValueError('Session epoch changed; stale operation rejected')
            if operation in ('record_stage','record_finish') and body.get('recording_id')!=current.get('recording_id'):
                raise ValueError('Original cooperative recording changed; stale metadata operation rejected')
        if operation=='propose_skill':
            refs={role:deepcopy(self._choice(role,body.get(role+'_choice_id'))['reference']) for role in ('giver','receiver')}
            for role,reference in refs.items():
                reference['phase_map']=deepcopy(body.get(role+'_phase_map',{}))
                if (not isinstance(reference['phase_map'],dict) or
                        any(value not in self._choice(role,body.get(role+'_choice_id'))['phase_ids'] for value in reference['phase_map'].values())):
                    raise ValueError('Explicit phase map must reference actual pinned native skill phases')
                model_id=body.get(role+'_model_id')
                if model_id:
                    models=self._choice(role,body.get(role+'_choice_id'))['models'];model=next((item for item in models if item['model_id']==model_id),None)
                    if model is None:raise ValueError('Pinned actual native model not available')
                    reference.update(model_id=model_id,checkpoint_sha256=model['checkpoint_sha256'])
            payload={key:deepcopy(body[key]) for key in ('name','object_description','receiver_goal_description','outcome_criteria','mode') if key in body}
            result=self.workflow.propose_skill({**payload,'subskills':refs})
        elif operation=='confirm_skill':result=self.workflow.confirm_skill(identity,version,body.get('spec_sha256'))
        elif operation=='create_session':
            if self.workflow.skill(identity,version)['spec']['spec_sha256']!=body.get('spec_sha256'):
                raise ValueError('Confirmed cooperative specification changed; refresh the exact version')
            if body.get('execution_domain')=='user_observed_physical_test':
                raise ValueError('Create a fresh metadata intent first, then bind both current explicit USER grants')
            payload=deepcopy(body);payload['source_execution_domain']='REAL'  # actual configured hardware sources, never SIM fixtures
            result=self.workflow.create_session(identity,version,payload);session=result['session_id']
        elif operation=='register_zone':
            if set(self.sources)!={'giver','receiver'}:raise ValueError('Both actual cached camera nodes required')
            payloads={};hashes={}
            for role,source in self.sources.items():
                packet=source.snapshot();name=role+'-original.jpg';payloads[name]=packet['jpeg'];hashes[name]=hashlib.sha256(packet['jpeg']).hexdigest()
                metadata={key:value for key,value in packet.items() if key not in ('jpeg','telemetry')};name=role+'-source.json'
                payloads[name]=canonical(metadata);hashes[name]=hashlib.sha256(payloads[name]).hexdigest()
            row=self.workflow.snapshot(session)
            result=self.workflow.register_zone(session,{'scene_id':row['scene_id'],'method':'operator_checked_shared_zone',
                'both_robot_workspaces_checked':body.get('both_robot_workspaces_checked') is True,'observing':body.get('observing') is True,
                'actor_id':authenticated_actor,'source_files_sha256':hashes},payloads)
        elif operation=='record_start':result=self.workflow.record_start(session)
        elif operation=='record_stage':result=self.workflow.record_stage(session,body.get('phase_id'))
        elif operation=='record_finish':result=self.workflow.record_finish(session,deepcopy(body.get('outcome') or {'status':'unknown'}))
        elif operation=='step':result=self.workflow.request_step(session,body.get('event'),expected_epoch=body.get('expected_epoch'))
        elif operation=='stop':
            previous=self.workflow.snapshot(session)
            result=self.workflow.cancel_session(session)
            result['observed_metadata_revocations']=self.observed_setup.revoke_session(previous)
            result['warm_compute_jobs_cancelled']=self.warm_compute.cancel_session(self.workflow.snapshot(session))
        elif operation=='confirm_evidence':result=self.inbox.confirm(body.get('challenge_id'),
            {key:body[key] for key in ('affirmed','observing','note') if key in body},authenticated_actor=authenticated_actor)
        elif operation=='shadow':
            row=self.workflow.snapshot(session);selected=self.workflow.skill(row['skill_id'],row['skill_version'])
            if not selected['confirmed']:raise ValueError('Confirm exact cooperative native model references first')
            result=self.queued_shadows.enqueue(row,selected['spec'],request_id=body.get('request_id'))
        elif operation=='warm_models':
            if self.root.resolve()!=(self.store.root/'cooperative05').resolve():raise ValueError('Canonical managed cooperative root required for scheduled warm compute')
            row=self.workflow.snapshot(session);selected=self.workflow.skill(row['skill_id'],row['skill_version'])
            if not selected['confirmed']:raise ValueError('Confirm exact native model references before warm compute')
            result=self.warm_compute.enqueue(row,selected['spec'],request_id=body.get('request_id'),
                compute_ttl_s=body.get('compute_ttl_s',120),authenticated_actor=authenticated_actor)
        elif operation in ('observed_refresh','observed_challenge','observed_authorize','bind_observed_test',
                            'observed_display','observed_observe','observed_revoke'):
            row=self.workflow.snapshot(session);spec=self.workflow.skill(row['skill_id'],row['skill_version'])['spec']
            if operation=='observed_refresh':result=self.observed_setup.snapshot(row,spec,refresh=True)
            elif operation=='observed_challenge':result=self.observed_setup.challenge(body.get('source_setup_choice_id'),row,spec,actor=authenticated_actor)
            elif operation=='observed_authorize':result=self.observed_setup.authorize(body.get('challenge_choice_id'),row,spec,body,actor=authenticated_actor)
            elif operation=='observed_display':result=self.observed_setup.display_observation(body.get('permission_choice_id'),row,spec,actor=authenticated_actor)
            elif operation=='observed_observe':result=self.observed_setup.observe(body.get('observation_choice_id'),row,spec,body,actor=authenticated_actor)
            elif operation=='observed_revoke':result=self.observed_setup.revoke(body.get('permission_choice_id'),row,spec,actor=authenticated_actor)
            else:result=self.observed_setup.bind(body.get('observed_choice_id'),row,spec,actor=authenticated_actor)
        elif operation=='attach_native_giver':result=self.attach_native_giver(session,body,authenticated_actor=authenticated_actor)
        elif operation=='review_recording':
            available=self._episode_choices(self.workflow.snapshot(session));references={}
            for role in ('giver','receiver'):
                choice=body.get(role+'_episode_choice_id') or (body.get('native_episode_choice_ids') or {}).get(role)
                chosen=next((item for item in available[role] if item['choice_id']==choice),None)
                if chosen is None:raise ValueError('Select actual source-bound original native '+role+' episode')
                references[role]={'import_job_id':chosen['import_job_id']}
            result=self.workflow.review_recording(session,body.get('recording_id'),{**body,'native_episode_refs':references},authenticated_actor=authenticated_actor)
        elif operation=='aggregate':result=self.workflow.build_aggregate(identity,version)
        elif operation=='training_plan':result=self.workflow.training_requests(identity,version,body.get('settings'))
        elif operation=='train':result=self.train(identity,version,body.get('settings'))
        else:raise ValueError('Unsupported typed cooperative workbench operation')
        return {'result':result,'snapshot':self.snapshot(session)}
    def observed_frame(self,choice_id,*,authenticated_actor):
        if self.closed:raise ValueError('Current cooperative server process required')
        return self.observed_setup.frame(choice_id,actor=authenticated_actor)
    def close(self):
        self.closed=True
        self.warm_compute.close(self.workflow.process)
        with self.workflow.lock:self.workflow.closed=True
        for row in self.workflow.snapshot()['sessions']:
            self.observed_setup.revoke_session(row,'COOPERATIVE_COORDINATOR_CLOSED')
            if row.get('pending_operation'):self.inbox.cancel_run(row['session_id'])
            if row.get('recording_id'):
                try:self.workflow.record_finish(row['session_id'],{'status':'unknown','reason':'COORDINATOR_SHUTDOWN_NOT_TASK_SUCCESS'})
                except (ValueError,OSError,KeyError):pass  # retained active ID stays explicitly interrupted/unknown
        self.workflow.close()
        if self.own_client:self.client.close()
