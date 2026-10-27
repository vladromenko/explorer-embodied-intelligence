"""ENGINEERING_GLUE / HANDOVER_VERIFIED_FSM: full cooperative workbench backend.

Versioned whole-task specs, native subskill references, shared-zone evidence,
dual-camera recording, source admission and observed phase execution. Existing
native controllers/learners are injected; no driver/torque/settings code exists.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

from .cooperative_contract05 import (SCHEMA,PHASES,MODES,ROLE_CONTRACTS,canonical,digest,skill_spec,
                                     source_contexts,model_input_plan,text)
from .cooperative_recording05 import write_original,write_projection,sha_file,verify_recording
from .handover import HandoverFSM,HoldEvidence
from .handover_runtime import CorrelatedEvidence,ArmObservation,DispatchReceipt
from .kinematics import rigid_transform

EVENTS=('giver_holding','offer_ready','receiver_ready','receiver_at_object','receiver_closed',
        'receiver_hold_confirmed','release_requested','giver_released','transfer_confirmed','receiver_finished')
STATEMENTS={'giver_holding':('giver_holds_object','giver'),
            'receiver_hold_confirmed':('receiver_holds_object','receiver'),
            'transfer_confirmed':('object_transferred_to_receiver','receiver'),
            'receiver_finished':('receiver_finished_task','receiver')}
POLICY_OPERATION={'giver_holding':('giver','acquire'),'offer_ready':('giver','offer'),
                  'receiver_ready':('receiver','approach'),'receiver_closed':('receiver','close'),
                  'release_requested':('giver','release'),'receiver_finished':('receiver','finish')}
LEGAL_STATES={'giver_holding':('PREPARE',),'offer_ready':('ROSMASTER_HOLDING',),'receiver_ready':('OFFER',),
    'receiver_at_object':('SO101_APPROACH',),'receiver_closed':('RECEIVER_GRASP_CHECK',),
    'receiver_hold_confirmed':('RECEIVER_GRASP_CHECK','RELEASE_AUTHORIZED'),
    'release_requested':('RECEIVER_GRASP_CHECK','RELEASE_AUTHORIZED'),'giver_released':('RELEASE',),
    'transfer_confirmed':('CONFIRM_TRANSFER',),'receiver_finished':('SO101_FINISH',)}


def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise ValueError('Server-issued cooperative ID required')
    return value


class CooperativeWorkflow:
    def __init__(self,root,registry,recorder=None,evidence_inbox=None,*,clock_domain,
                 clock=time.monotonic_ns,authority=lambda session:{},managed_root=None,native_owned_stop=None):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.registry,self.recorder,self.inbox=registry,recorder,evidence_inbox
        self.domain,self.clock,self.authority=clock_domain,clock,authority
        self.native_owned_stop=native_owned_stop
        self.prepare_parked_release=None;self.wait_parked_release_control=None
        self.managed=Path(managed_root or root).resolve()
        self.lock=threading.RLock();self.record_io=threading.Lock()
        self.native_phase_condition=threading.Condition(self.lock);self.native_phase_changes={};self.native_phase_checkpoints={}
        self.process=uuid.uuid4().hex;self.threads={};self.closed=False
        for name in ('skills','sessions','zones','reviews','aggregates','shadows'):(self.root/name).mkdir(exist_ok=True)
        # Metadata recovery only: never replay a command or call stop/enable in
        # a constructor. Native user leases independently remain original.
        for path in (self.root/'sessions').glob('*/session.json'):
            previous=json.loads(path.read_bytes())
            if previous.get('status') not in ('COMPLETED','STOPPED','INTERRUPTED'):
                backup=path.with_name('session.interrupted-'+self.process+'.json');write_original(backup,path.read_bytes())
                previous.update(status='INTERRUPTED',epoch=uuid.uuid4().hex,pending_operation=None,
                    recovery_reason='COORDINATOR_RESTART_REQUIRES_NEW_OBSERVED_SESSION_NO_MOTION_REPLAY')
                write_projection(path,previous)
    def catalogue(self):
        return {'schema':'explorer_cooperative_catalogue_v1','templates':[{
            'id':'give_receive_finish','title':'Передать предмет ROSMASTER → SO101 и завершить действие',
            'initial_base_mode':'stationary','mode_options':list(MODES),
            'giver_examples':['взять лёгкий жёсткий предмет и подать в общую зону'],
            'receiver_examples':['принять предмет и положить в коробку'],
            'native_roles':deepcopy(ROLE_CONTRACTS),'subskill_sources':{'giver':['rosmaster_keyboard_learning','rosmaster_leader_learning','cross_embodiment_transfer'],
                'receiver':['so101_source_learning']}}],
            'phases':list(PHASES),'model_input_plan':model_input_plan(),'physical_execution_authorized':False}
    def _skill_path(self,skill_id,version):
        identity=str(uuid.UUID(str(skill_id)))
        if type(version) is not int or version<1:raise ValueError('Pinned cooperative version required')
        return self.root/'skills'/identity/('v'+str(version).zfill(4)+'.json')
    def propose_skill(self,body):
        spec=skill_spec(body);path=self._skill_path(spec['skill_id'],spec['version'])
        with self.lock:
            if path.exists():
                old=json.loads(path.read_bytes())
                if old['spec']!=spec:raise ValueError('Immutable cooperative version changed; create a new version')
                return old
            write_original(path,canonical({'spec':spec,'confirmed':False,'created_coordinator_ns':self.clock()}))
        return self.skill(spec['skill_id'],spec['version'])
    def skill(self,skill_id,version):
        row=json.loads(self._skill_path(skill_id,version).read_bytes())
        if row['spec']['spec_sha256']!=digest({key:value for key,value in row['spec'].items() if key!='spec_sha256'}):
            raise ValueError('Immutable cooperative spec changed')
        return row
    def confirm_skill(self,skill_id,version,spec_sha256):
        with self.lock:
            row=self.skill(skill_id,version)
            if row['spec']['spec_sha256']!=spec_sha256:raise ValueError('Review exact current cooperative spec')
            row['confirmed']=True;row['confirmed_coordinator_ns']=self.clock()
            write_projection(self._skill_path(skill_id,version),row)
            return row
    def create_session(self,skill_id,version,body):
        selected=self.skill(skill_id,version);spec=selected['spec']
        if not selected['confirmed']:raise ValueError('Confirm cooperative task and pinned native roles first')
        for field in ('scene_id','object_instance_id'):text(body.get(field),field,160)
        if body.get('split') not in ('train','development','sealed_test'):raise ValueError('Explicit source split required')
        giver=body.get('giver_input_mode');receiver=body.get('receiver_input_mode')
        if giver not in ('keyboard','browser_gamepad','so101_leader','policy') or receiver not in ('leader','policy'):
            raise ValueError('Explicit actual native input adapters required')
        expected_giver=spec['mode'] in ('giver_policy_receiver_manual','both_policy')
        expected_receiver=spec['mode'] in ('giver_manual_receiver_policy','both_policy')
        if (giver=='policy')!=expected_giver or (receiver=='policy')!=expected_receiver:
            raise ValueError('Actual input adapters must match the selected cooperative development mode')
        domain=body.get('execution_domain','observation_only')
        if domain not in ('observation_only','shadow','user_observed_physical_test'):
            raise ValueError('Explicit cooperative observation/shadow/observed-test domain required')
        row={'schema':'explorer_cooperative_session_v1','session_id':uuid.uuid4().hex,'epoch':uuid.uuid4().hex,
            'process_session':self.process,'clock_domain':self.domain,'created_coordinator_ns':self.clock(),
            'skill_id':spec['skill_id'],'skill_version':version,'spec_sha256':spec['spec_sha256'],
            'scene_id':body['scene_id'],'object_instance_id':body['object_instance_id'],'protocol_object_id':uuid.uuid4().hex,
            'split':body['split'],'giver_input_mode':giver,'receiver_input_mode':receiver,'mode':spec['mode'],
            'execution_domain':domain,
            'status':'READY_FOR_OBSERVED_STEPS','protocol_state':'PREPARE','protocol_completed':False,
            'protocol_trace':[],'dispatch_receipts':[],'observation_receipts':[],'pending_operation':None,
            'zone_registration_id':None,'recording_id':None,'recording_source_refs':{},'errors':[]}
        row['native_variants']=deepcopy(body.get('native_variants',{}))
        row['source_execution_domain']=body.get('source_execution_domain')
        if row['source_execution_domain'] not in (None,'REAL','SIMULATION'):
            raise ValueError('Explicit original source REAL/SIMULATION domain or unknown required')
        row['stationary_chassis_confirmed']=body.get('stationary_chassis_confirmed') is True
        row['proposed_native_source_contexts']=source_contexts(spec,row)
        with self.lock:
            for path in (self.root/'sessions').glob('*/session.json'):
                other=json.loads(path.read_bytes())
                if (other['split']!=row['split'] and any(other[field]==row[field] for field in ('scene_id','object_instance_id'))):
                    raise ValueError('Declared object instance/scene cannot cross dataset splits')
            self._save(row)
        return deepcopy(row)
    def _path(self,session_id):return self.root/'sessions'/_id(session_id)/'session.json'
    def _session(self,session_id):return json.loads(self._path(session_id).read_bytes())
    def _save(self,row):
        path=self._path(row['session_id']);path.parent.mkdir(exist_ok=True)
        projection=deepcopy(row)
        if isinstance(projection.get('pending_operation'),dict):
            projection['pending_operation'].pop('native_control_checkpoint',None)
        write_projection(path,projection)
    def snapshot(self,session_id=None):
        if session_id is not None:
            with self.lock:row=self._project_native_checkpoint(self._session(session_id))
            return row
        with self.lock:
            return {'schema':'explorer_cooperative_workbench_v1',
                'skills':[json.loads(path.read_bytes()) for path in sorted((self.root/'skills').glob('*/*.json'))],
                'sessions':[self._project_native_checkpoint(json.loads(path.read_bytes())) for path in sorted((self.root/'sessions').glob('*/session.json'))],
                'model_input_plan':model_input_plan(),'physical_execution_authorized':False}
    def _spec_for(self,row):return self.skill(row['skill_id'],row['skill_version'])['spec']
    def readiness(self,session_id):
        row=self.snapshot(session_id);spec=self._spec_for(row)
        roles={role:self.registry.inspect(role,reference) for role,reference in spec['subskills'].items()}
        blockers=[role+':'+reason for role,state in roles.items() for reason in state['blockers']]
        if row['giver_input_mode']=='so101_leader' and row['receiver_input_mode']=='leader':
            blockers.append('ONE_LEADER_CANNOT_INDEPENDENTLY_TEACH_BOTH_ROBOTS_USE_GIVER_KEYBOARD_OR_POLICY')
        if row['zone_registration_id'] is None:blockers.append('CURRENT_SHARED_ZONE_NOT_REGISTERED')
        policy_roles=[role for role in ('giver','receiver') if self._policy_role(row,role)]
        execution=[]
        if policy_roles and row['zone_registration_id']:
            zone=json.loads((self.root/'zones'/row['zone_registration_id']/'registration.json').read_bytes())
            if not zone.get('metric_registration_available') or not zone.get('registration_source_bound'):
                execution.append('SOURCE_BOUND_METRIC_SHARED_ZONE_REQUIRED_FOR_POLICY_STEPS')
        for role in policy_roles:
            provider=self.registry.provider(role,spec['subskills'][role]);state=roles[role]
            if not spec['subskills'][role].get('model_id'):execution.append(role+':PINNED_NATIVE_MODEL_REQUIRED')
            if not state.get('shadow_supported'):execution.append(role+':NATIVE_SHADOW_PROVIDER_NOT_BOUND')
            if provider is None or not callable(provider.submit_observed):execution.append(role+':OBSERVED_TRANSPORT_NOT_BOUND')
            if provider is None or not callable(provider.observe_state):execution.append(role+':CURRENT_NATIVE_STATE_INSPECTION_NOT_BOUND')
            if not state.get('native_dispatch_receipts'):execution.append(role+':NATIVE_HOST_PUBLICATION_PROOF_NOT_BOUND')
            if role=='giver' and not state.get('authoritative_release_deadline'):
                execution.append('giver:RELEASE_HOLD_GUARD_NOT_AT_NATIVE_PUBLICATION_BOUNDARY')
            if role=='giver' and (provider is None or not callable(provider.arm_release_guard) or not callable(provider.prepare_release)):
                execution.append('giver:NATIVE_DUAL_HOLD_AND_CHALLENGE_BINDING_REQUIRED')
        return {'schema':'explorer_cooperative_readiness_v1','session_id':session_id,'native_roles':roles,
                'metadata_and_recording_blockers':blockers,'observed_execution_blockers':blockers+execution,
                'raw_two_camera_recording_available':self.recorder is not None,
                'native_role_recording_supported':all(state.get('recording_supported') for state in roles.values()),
                'observed_policy_steps_supported':bool(policy_roles) and not(blockers or execution),
                'operator_phase_workflow_available':self.inbox is not None,'automatic_motion_started':False,
                'force_sensor_required_if_available_otherwise_explicit_operator_hold':True,
                'model_input_plan':model_input_plan(),'physical_task_mastery_inferred':False}
    @staticmethod
    def _policy_role(row,role):
        return row['mode']=='both_policy' or row['mode']==('giver_policy_receiver_manual' if role=='giver' else 'giver_manual_receiver_policy')
    def register_zone(self,session_id,body,source_payloads=None):
        row=self.snapshot(session_id);files=source_payloads or {};declared=body.get('source_files_sha256',{})
        if body.get('scene_id')!=row['scene_id'] or body.get('method') not in ('operator_checked_shared_zone','measured_frame_registration'):
            raise ValueError('Current explicit shared-scene registration required')
        if (body.get('both_robot_workspaces_checked') is not True or not body.get('actor_id') or
                body.get('observing') is not True or not declared):
            raise ValueError('Observed shared workspace and original calibration/camera evidence required')
        for name,expected in declared.items():
            if not isinstance(name,str) or name.startswith('/') or '..' in Path(name).parts or name not in files or hashlib.sha256(files[name]).hexdigest()!=expected:
                raise ValueError('Original shared-zone source payload hash/path mismatch')
        transforms=body.get('base_from_zone') or {}
        if body['method']=='measured_frame_registration':
            if set(transforms)!={'giver','receiver'}:raise ValueError('Both separately registered robot-base transforms required')
            provenance=body.get('transform_provenance') or {}
            for role,matrix in transforms.items():
                rigid_transform(matrix);proof=provenance.get(role) or {};name=proof.get('source_file')
                try:
                    source=json.loads(files[name]);pointer=proof.get('json_pointer')
                    if not isinstance(pointer,str) or (pointer and not pointer.startswith('/')):raise ValueError('JSON pointer required')
                    for part in pointer[1:].split('/') if pointer else []:
                        key=part.replace('~1','/').replace('~0','~');source=source[int(key)] if isinstance(source,list) else source[key]
                    expected={'base_from_zone':matrix,'scene_id':row['scene_id'],'role':role,'translation_unit':'m'}
                    if name not in declared or any(source.get(key)!=value for key,value in expected.items()):raise ValueError('Unbound metric registration')
                    if source.get('physical_registration_measured') is not True:raise ValueError('Physical registration missing')
                except (ValueError,TypeError,KeyError,IndexError,UnicodeError):
                    raise ValueError('Each metric transform must match original measured registration JSON/role/scene/units') from None
        elif transforms:
            raise ValueError('Operator scene check must not invent metric transforms')
        value={**deepcopy(body),'schema':'explorer_cooperative_zone_v1','zone_id':uuid.uuid4().hex,
               'registered_coordinator_ns':self.clock(),'clock_domain':self.domain,
               'metric_registration_available':body['method']=='measured_frame_registration',
               'registration_source_bound':body['method']=='measured_frame_registration',
               'physical_collision_or_contact_validated':False}
        folder=self.root/'zones'/value['zone_id'];folder.mkdir()
        for name,raw in files.items():
            if name in declared:write_original(folder/name,raw)
        write_original(folder/'registration.json',canonical(value))
        with self.lock:
            current=self._session(session_id)
            if current['epoch']!=row['epoch'] or current['pending_operation']:raise ValueError('Session changed during zone evidence validation')
            current['zone_registration_id']=value['zone_id'];self._save(current)
        return value
    def record_start(self,session_id):
        if not self.record_io.acquire(blocking=False):raise ValueError('Another recording metadata operation is pending')
        try:return self._record_start(session_id)
        finally:self.record_io.release()
    def _finish_sources(self,row,refs,outcome):
        results={}
        for role,ref in self._spec_for(row)['subskills'].items():
            provider=self.registry.provider(role,ref);native=refs.get(role)
            if provider is not None and callable(provider.record_finish) and native:
                try:results[role]=provider.record_finish(deepcopy(native),deepcopy((outcome.get('native_roles') or {}).get(role,{'status':'unknown'})))
                except Exception as error:results[role]={'error':type(error).__name__+': '+str(error)}
        return results
    def _record_start(self,session_id):
        if self.recorder is None:raise ValueError('Both authenticated existing camera caches must be bound')
        with self.lock:
            row=self._session(session_id)
            if self.closed or row['status'] in ('STOPPED','INTERRUPTED','COMPLETED'):
                raise ValueError('A current cooperative session is required')
            if any(json.loads(path.read_bytes()).get('recording_id') for path in (self.root/'sessions').glob('*/session.json')):
                raise ValueError('Finish active cooperative recording first')
            expected=row['epoch'];spec=self._spec_for(row)
        refs={};errors={}
        for role,ref in spec['subskills'].items():
            if self.closed or self.snapshot(session_id)['epoch']!=expected:
                errors[role]='SESSION_REVOKED_NO_NEW_NATIVE_RECORD_START';break
            provider=self.registry.provider(role,ref)
            if provider is None or not callable(provider.record_start):errors[role]='NATIVE_RECORD_PRODUCER_NOT_BOUND'
            else:
                try:refs[role]=provider.record_start(deepcopy(row['proposed_native_source_contexts'][role]))
                except Exception as error:errors[role]=type(error).__name__+': '+str(error)
        context={**row,'cooperative_skill_spec':spec,'native_record_start_errors':errors,
                 'declared_scene_and_object_are_not_automatic_vision_success':True}
        if self.closed or self.snapshot(session_id)['epoch']!=expected:
            self._finish_sources(row,refs,{'status':'interrupted'})
            raise ValueError('Session was stopped during native recording metadata start')
        try:recording=self.recorder.start(context,refs)
        except Exception:
            self._finish_sources(row,refs,{'status':'unknown'})
            raise
        with self.lock:
            current=self._session(session_id)
            superseded=self.closed or current['epoch']!=expected
            if not superseded:
                current['recording_id']=recording['recording_id'];current['recording_source_refs']=refs
                current['record_start_errors']=errors;self._save(current)
        if superseded:
            finished=self._finish_sources(row,refs,{'status':'interrupted'})
            self.recorder.finish({'status':'interrupted','reason':'SESSION_CHANGED_DURING_METADATA_RECORD_START'},finished)
            raise ValueError('Session was stopped during record start; no new control session created')
        return {**recording,'native_sources':refs,'native_source_errors':errors,'control_sessions_started':False}
    def record_stage(self,session_id,phase_id):
        if not self.record_io.acquire(blocking=False):raise ValueError('Another recording metadata operation is pending')
        try:return self._record_stage(session_id,phase_id)
        finally:self.record_io.release()
    def _record_stage(self,session_id,phase_id):
        if phase_id not in PHASES:raise ValueError('Only explicit cooperative phase annotations')
        row=self.snapshot(session_id);spec=self._spec_for(row);receipts={}
        if not row['recording_id']:raise ValueError('Start cooperative recording first')
        self._fresh(row,row['epoch'])
        for role,ref in spec['subskills'].items():
            self._fresh(row,row['epoch'])
            provider=self.registry.provider(role,ref);native=row['recording_source_refs'].get(role)
            phase=ref.get('phase_map',{}).get(phase_id)
            if provider is None or not callable(provider.record_stage) or not native or phase is None:
                receipts[role]={'available':False,'reason':'PINNED_NATIVE_SOURCE_PHASE_MAPPING_OR_RECORDER_MISSING'}
            else:
                try:receipts[role]=provider.record_stage(deepcopy(native),phase,{'cooperative_session_id':session_id,'phase_id':phase_id})
                except Exception as error:receipts[role]={'available':False,'error':type(error).__name__+': '+str(error)}
        return self.recorder.stage(phase_id,receipts)
    def record_finish(self,session_id,outcome):
        if not self.record_io.acquire(blocking=False):raise ValueError('Another recording metadata operation is pending')
        try:return self._record_finish(session_id,outcome)
        finally:self.record_io.release()
    def _record_finish(self,session_id,outcome):
        row=self.snapshot(session_id);spec=self._spec_for(row);results={}
        if not row['recording_id']:raise ValueError('No active cooperative recording')
        if not isinstance(outcome,dict):raise ValueError('Typed actual/unknown outcome evidence required')
        results=self._finish_sources(row,row['recording_source_refs'],outcome)
        result=self.recorder.finish(outcome,results)
        with self.lock:
            current=self._session(session_id);current['last_recording_id']=row['recording_id'];current['recording_id']=None
            current['native_record_finish_results']=results;self._save(current)
        return result
    def _fsm(self,row):
        fsm=HandoverFSM(row['protocol_object_id'],mode='physical',clock_domain=self.domain,run_id=row['session_id'])
        for event in row['protocol_trace']:
            evidence=HoldEvidence(**event['evidence']) if event.get('evidence') else None
            fsm.apply(event['event'],event_id=event['event_id'],now_ns=event['coordinator_monotonic_ns'],evidence=evidence,**event['facts'])
        return fsm
    def request_step(self,session_id,event,*,expected_epoch):
        if event not in EVENTS:raise ValueError('Supported evidence-gated protocol event required')
        if self.inbox is None:raise ValueError('Authenticated operator evidence inbox is not bound')
        with self.lock:
            row=self._session(session_id)
            if self.closed or row['epoch']!=expected_epoch or row['pending_operation'] or row['status'] in ('STOPPED','INTERRUPTED','COMPLETED'):
                raise ValueError('Fresh cooperative session/epoch with no pending step required')
            if self._native_phase_inflight(session_id,expected_epoch):
                raise ValueError('Complete explicit native phase change before starting a protocol step')
            if self._fsm(row).state not in LEGAL_STATES[event]:
                raise ValueError('Protocol state does not admit this step; no command submitted')
            operation=uuid.uuid4().hex;row['pending_operation']={'operation_id':operation,'event':event}
            self._save(row)
            thread=threading.Thread(target=self._step,args=(session_id,event,expected_epoch,operation),daemon=True,name='cooperative-observed-step')
            self.threads[operation]=thread;thread.start()
            return {'session_id':session_id,'operation_id':operation,'event':event,'state':'WAITING_FOR_CURRENT_EVIDENCE_OR_PROVIDER',
                    'motor_command_claimed':False}
    def _native_phase_inflight(self,session_id,epoch):
        return any(item['session_id']==session_id and item['epoch']==epoch for item in self.native_phase_changes.values())
    def _project_native_checkpoint(self,row):
        pending=row.get('pending_operation') or {};key=(row['session_id'],row['epoch'],pending.get('operation_id'))
        if pending and key in self.native_phase_checkpoints:
            pending['native_control_checkpoint']=deepcopy(self.native_phase_checkpoints[key])
        return row
    def native_phase_change_window(self,row):
        """A metadata admission window, never evidence or actuator authority."""
        with self.lock:
            current=self._session(row['session_id']);pending=current.get('pending_operation') or {}
            checkpoint=self.native_phase_checkpoints.get((current['session_id'],current['epoch'],pending.get('operation_id'))) or {}
            return bool(not self.closed and current['epoch']==row['epoch'] and current['process_session']==self.process and
                current['status'] not in ('STOPPED','INTERRUPTED','COMPLETED') and
                (not pending or checkpoint.get('accepting') is True))
    def begin_native_phase_change(self,session_id,expected_epoch,role,operation):
        """Reserve explicit source scope changes before any source RPC.

        A pending protocol admits them only while its evidence-only checkpoint
        is open. The reservation spans source reply custody/lineage; STOP does
        not wait for it, and a constructor never restores it as permission.
        """
        allowed={'native_phase_park','native_phase_adopt','native_phase_begin','native_phase_resume',
            'native_phase_activate','receiver_setup_adopt_hold','native_attach'}
        if role not in ('giver','receiver') or operation not in allowed:
            raise ValueError('Fixed explicit USER native phase scope change required')
        with self.native_phase_condition:
            current=self._session(session_id)
            if current['epoch']!=expected_epoch or not self.native_phase_change_window(current):
                raise ValueError('Native phase change requires current idle/evidence-only checkpoint')
            key=(session_id,expected_epoch,role)
            if key in self.native_phase_changes:raise ValueError('This native role already has a phase change in flight')
            pending=current.get('pending_operation') or {}
            checkpoint=self.native_phase_checkpoints.get((session_id,expected_epoch,pending.get('operation_id'))) or {}
            token={'token_id':uuid.uuid4().hex,'session_id':session_id,'epoch':expected_epoch,'role':role,'operation':operation,
                'process_session':self.process,'protocol_state':current['protocol_state'],'pending_operation_id':pending.get('operation_id'),
                'checkpoint_id':checkpoint.get('checkpoint_id'),'started_nx_ns':self.clock()}
            self.native_phase_changes[key]=deepcopy(token)
            return deepcopy(token)
    def end_native_phase_change(self,token):
        if not isinstance(token,dict):raise ValueError('Original native phase reservation required')
        key=(token.get('session_id'),token.get('epoch'),token.get('role'))
        with self.native_phase_condition:
            if self.native_phase_changes.get(key)!=token:return False
            self.native_phase_changes.pop(key);self.native_phase_condition.notify_all();return True
    def begin_parked_release_control(self,session_id,expected_epoch,release_bundle_id):
        """Reserve only the USER control matching this actual source challenge.

        This is not a general phase-change window. Other scope changes remain
        blocked while an epoch-bound source challenge is pending. STOP never
        waits for the control reply or its immutable custody.
        """
        _id(release_bundle_id)
        with self.native_phase_condition:
            row=self._session(session_id);self._fresh(row,expected_epoch)
            pending=row.get('pending_operation') or {};descriptor=pending.get('native_release_challenge') or {}
            checkpoint=self.native_phase_checkpoints.get((session_id,expected_epoch,pending.get('operation_id'))) or {}
            if (row['execution_domain']!='user_observed_physical_test' or pending.get('event')!='release_requested' or
                    descriptor.get('release_bundle_id')!=release_bundle_id or descriptor.get('operation_id')!=pending.get('operation_id') or
                    descriptor.get('epoch')!=expected_epoch or not checkpoint.get('checkpoint_id') or
                    checkpoint.get('accepting') is not False or checkpoint.get('binding_change_blocker')!='SOURCE_CHALLENGE_IS_EPOCH_BOUND' or
                    type(descriptor.get('deadline_nx_ns')) is not int or self.clock()>=descriptor['deadline_nx_ns'] or
                    self._native_phase_inflight(session_id,expected_epoch)):
                raise ValueError('Exact live parked release question and original protocol checkpoint required')
            token={'token_id':uuid.uuid4().hex,'session_id':session_id,'epoch':expected_epoch,'role':'giver',
                'operation':'native_parked_release_resume','process_session':self.process,'protocol_state':row['protocol_state'],
                'pending_operation_id':pending['operation_id'],'checkpoint_id':checkpoint['checkpoint_id'],
                'release_bundle_id':release_bundle_id,'started_nx_ns':self.clock()}
            self.native_phase_changes[(session_id,expected_epoch,'giver')]=deepcopy(token)
            return deepcopy(token)
    @contextmanager
    def _native_evidence_checkpoint(self,row,operation,fsm):
        """Quiesce source-binding mutations before any post-evidence action.

        No native RPC or grant/lease renewal occurs here. Closing the window
        waits only for already reserved metadata/control replies, with the
        original phase deadline and a bounded host wait. Freshness and dispatch
        authority are rechecked afterwards by the existing protocol paths.
        """
        with self.native_phase_condition:
            current=self._fresh(row,row['epoch']);pending=current.get('pending_operation') or {}
            if pending.get('operation_id')!=operation:raise ValueError('Original pending evidence operation changed')
            challenge_bound=bool(pending.get('native_release_challenge') or pending.get('native_transfer_challenge'))
            checkpoint={'checkpoint_id':uuid.uuid4().hex,'accepting':not challenge_bound,'kind':'EVIDENCE_ONLY_NO_NATIVE_SUBMISSION',
                'binding_change_blocker':'SOURCE_CHALLENGE_IS_EPOCH_BOUND' if challenge_bound else None,
                'opened_nx_ns':self.clock(),'clock_domain':self.domain}
            self.native_phase_checkpoints[(row['session_id'],row['epoch'],operation)]=checkpoint
        try:yield
        except BaseException:
            # Preserve the actual evidence error and proceed to the existing
            # STOP path immediately; never wait on source I/O after rejection.
            try:self._close_native_checkpoint(row,operation,checkpoint,fsm,wait=False)
            except Exception:pass  # Epoch may already have been revoked by STOP.
            raise
        else:self._close_native_checkpoint(row,operation,checkpoint,fsm,wait=True)
    def _close_native_checkpoint(self,row,operation,checkpoint,fsm,*,wait):
        with self.native_phase_condition:
            current=self._fresh(row,row['epoch']);pending=current.get('pending_operation') or {}
            key=(row['session_id'],row['epoch'],operation);actual=self.native_phase_checkpoints.get(key) or {}
            if pending.get('operation_id')!=operation or actual.get('checkpoint_id')!=checkpoint['checkpoint_id']:
                raise ValueError('Evidence checkpoint changed or was cancelled')
            actual['accepting']=False
            try:
                if wait:
                    deadline=time.monotonic()+3.
                    while self._native_phase_inflight(row['session_id'],row['epoch']) and time.monotonic()<deadline:
                        self._fresh(row,row['epoch']);self._phase_current(fsm)
                        self.native_phase_condition.wait(timeout=min(.05,max(0.,deadline-time.monotonic())))
                    self._fresh(row,row['epoch']);self._phase_current(fsm)
                    if self._native_phase_inflight(row['session_id'],row['epoch']):
                        raise ValueError('Explicit native phase change did not finish before evidence revalidation')
            finally:self.native_phase_checkpoints.pop(key,None)
    def _fresh(self,row,epoch):
        with self.lock:current=self._session(row['session_id'])
        if self.closed or current['epoch']!=epoch or current['process_session']!=self.process or current['status'] in ('STOPPED','INTERRUPTED'):
            raise ValueError('Cooperative step was stopped/superseded')
        return current
    def _stationary(self,row,role,after=None):
        result=self.inbox.stationary_observation(row['session_id'],row['protocol_object_id'],role,after,30.)
        if (not isinstance(result,ArmObservation) or result.role!=role or not result.stationary or
                result.clock_domain!=self.domain or not 0<=self.clock()-result.received_ns<=2_000_000_000 or
                result.after_command_id!=after or not result.source_receipt):raise ValueError('Fresh independent/operator stationary evidence required')
        return result
    def _phase_current(self,fsm):
        if fsm.phase_started_ns is not None and self.clock()-fsm.phase_started_ns>fsm.phase_timeout_ns:
            raise ValueError('Original protocol phase deadline expired; no automatic release or movement')
    def _receiver_hold(self,row,after):
        result=self.inbox('receiver_holds_object',row['session_id'],row['protocol_object_id'],'receiver',after,30.)
        if (not isinstance(result,CorrelatedEvidence) or result.run_id!=row['session_id'] or
                result.object_id!=row['protocol_object_id'] or result.role!='receiver' or
                result.after_command_id!=after or not result.source_receipt):
            raise ValueError('Current receiver/run/object/native-command hold correlation required')
        return result
    @staticmethod
    def _last_command(row,role):
        return next((item['command_id'] for item in reversed(row['dispatch_receipts']) if item['role']==role),None)
    def _parked_release_evidence(self,row,operation,descriptor,completion,after):
        """Validate consumed Root facts; a CONTROL ACK alone is never holding."""
        if not isinstance(completion,dict):raise ValueError('Validated explicit parked release completion required')
        challenge=descriptor.get('challenge') or {};grant=completion.get('release_grant') or {}
        expected={'release_bundle_id':descriptor.get('release_bundle_id'),'run_id':row['session_id'],
            'object_id':row['protocol_object_id'],'epoch':row['epoch'],'operation_id':operation}
        actor=(row.get('observed_test_binding') or {}).get('actor_id');received=descriptor.get('received_coordinator_ns')
        issued=challenge.get('issued_native_ns');expires=challenge.get('expires_native_ns')
        now=self.clock()
        if (completion.get('schema')!='explorer_cooperative_parked_release_control_completion_v1' or
                any(descriptor.get(key)!=value or completion.get(key)!=value for key,value in expected.items()) or
                completion.get('source_control_validated') is not True or not completion.get('source_control_receipt') or
                not actor or descriptor.get('actor_id')!=actor or challenge.get('actor_id')!=actor or
                challenge.get('schema')!='explorer_native_parked_receiver_hold_challenge_v1' or
                challenge.get('run_id')!=row['session_id'] or challenge.get('object_id')!=row['protocol_object_id'] or
                challenge.get('coordinator_epoch')!=row['epoch'] or
                not isinstance(challenge.get('native_clock_domain'),str) or not challenge['native_clock_domain'] or
                challenge['native_clock_domain']==self.domain or not isinstance(challenge.get('native_boot_id'),str) or not challenge['native_boot_id'] or
                type(issued) is not int or type(expires) is not int or not 0<=issued<expires<=issued+2_000_000_000 or
                challenge.get('after_receiver_command_id')!=after or descriptor.get('after_receiver_command_id')!=after or
                descriptor.get('source_challenge_sha256')!=digest(challenge) or
                descriptor.get('received_clock_domain')!=self.domain or type(received) is not int or
                type(descriptor.get('deadline_nx_ns')) is not int or not 0<=received<=now<descriptor['deadline_nx_ns']<=received+(expires-issued) or
                grant.get('challenge')!=challenge or grant.get('expires_native_ns')!=challenge.get('expires_native_ns')):
            raise ValueError('Exact source challenge, immutable CONTROL custody and original expiry required')
        proofs=completion.get('operator_confirmations');statements={'receiver_holds_object','giver_stationary','receiver_stationary'}
        if not isinstance(proofs,dict) or set(proofs)!=statements:raise ValueError('Three actual consumed post-challenge facts required')
        identities=set()
        for statement,proof in proofs.items():
            role='giver' if statement=='giver_stationary' else 'receiver'
            command=self._last_command(row,'giver') if role=='giver' else after
            if (not isinstance(proof,dict) or proof.get('statement')!=statement or proof.get('role')!=role or
                    proof.get('state')!='consumed' or proof.get('run_id')!=row['session_id'] or
                    proof.get('object_id')!=row['protocol_object_id'] or proof.get('authenticated_actor')!=actor or
                    proof.get('affirmed') is not True or proof.get('observing') is not True or
                    proof.get('clock_domain')!=self.domain or proof.get('after_command_id')!=command or
                    proof.get('release_bundle_id')!=descriptor['release_bundle_id'] or
                    proof.get('source_challenge_sha256')!=descriptor['source_challenge_sha256'] or
                    any(type(proof.get(key)) is not int for key in ('created_ns','received_ns','consumed_ns')) or
                    not received<=proof['created_ns']<=proof['received_ns']<=proof['consumed_ns']<=now or
                    not 0<=now-proof['received_ns']<=2_000_000_000):
                raise ValueError('Fresh same-actor/run/command post-source consumed observation required')
            identity=_id(proof.get('challenge_id'))
            if identity in identities:raise ValueError('Release facts require separate question identities')
            identities.add(identity)
        if (grant.get('operator_confirmation')!=proofs['receiver_holds_object'] or
                grant.get('stationary_confirmations')!={key:proofs[key] for key in ('giver_stationary','receiver_stationary')}):
            raise ValueError('Actual installed native grant must retain these exact three consumed facts')
        hold=proofs['receiver_holds_object']
        held=CorrelatedEvidence(row['session_id'],row['protocol_object_id'],'receiver',after,
            HoldEvidence(hold['challenge_id'],row['protocol_object_id'],'operator','operator_confirmation',
                'receiver_holds_object',False,hold['received_ns'],self.domain,hold['received_ns'],self.domain,True),
            {'kind':'authenticated_operator_confirmation','confirmation':deepcopy(hold)})
        def stationary(role):
            proof=proofs[role+'_stationary']
            return ArmObservation(role,True,proof['received_ns'],self.domain,
                {'kind':'explicit_authenticated_operator_stationary_confirmation','confirmation':deepcopy(proof),
                 'measured_joint_feedback_claimed':False},proof['after_command_id'])
        return held,stationary('giver'),stationary('receiver')
    def _step(self,session_id,event,epoch,operation):
        try:
            row=self._session(session_id);fsm=self._fsm(row);facts={};evidence=None;observations=[]
            self._fresh(row,epoch);spec=self._spec_for(row)
            self._phase_current(fsm)
            if fsm.state not in LEGAL_STATES[event]:raise ValueError('Protocol changed before this step')
            command=POLICY_OPERATION.get(event);after=None
            already_applied=False;release_challenge=None;parked_release_completion=None
            if event=='release_requested':
                receiver_after=self._last_command(row,'receiver')
                giver_provider=self.registry.provider('giver',spec['subskills']['giver'])
                if row['execution_domain']=='user_observed_physical_test' and callable(self.prepare_parked_release):
                    if not callable(self.wait_parked_release_control):raise ValueError('Explicit parked CONTROL completion binder required')
                    release_challenge=deepcopy(self.prepare_parked_release(deepcopy(row),receiver_after))
                parked_release=release_challenge is not None
                if not parked_release and row['execution_domain']=='user_observed_physical_test' and giver_provider is not None and callable(giver_provider.prepare_release):
                    release_challenge=giver_provider.prepare_release(deepcopy(row),receiver_after)
                if release_challenge is not None:
                    if receiver_after is None:receiver_after=release_challenge.get('after_receiver_command_id')
                    self._fresh(row,epoch)
                    with self.lock:
                        current=self._session(session_id)
                        if current['epoch']!=epoch:raise ValueError('Release challenge superseded by STOP')
                        current['pending_operation']['native_release_challenge']=deepcopy(release_challenge);self._save(current)
                # All three prompts are current and concurrent; none is inferred
                # from servo closure or time passing. Check freshness again only
                # AFTER all responses, before any physical transport call.
                with self._native_evidence_checkpoint(row,operation,fsm):
                    if parked_release:
                        parked_release_completion=self.wait_parked_release_control(deepcopy(row),deepcopy(release_challenge))
                        self._fresh(row,epoch);self._phase_current(fsm)
                        held,giver_still,receiver_still=self._parked_release_evidence(row,operation,release_challenge,parked_release_completion,receiver_after)
                    else:
                        with ThreadPoolExecutor(max_workers=3) as pool:
                            hold=pool.submit(self._receiver_hold,row,receiver_after)
                            giver=pool.submit(self._stationary,row,'giver',self._last_command(row,'giver'))
                            receiver=pool.submit(self._stationary,row,'receiver',self._last_command(row,'receiver'))
                            held,giver_still,receiver_still=hold.result(),giver.result(),receiver.result()
                self._fresh(row,epoch)
                self._phase_current(fsm)
                if any(not 0<=self.clock()-item.received_ns<=fsm.hold_ttl_ns for item in (giver_still,receiver_still)):
                    raise ValueError('Both stationary observations must remain fresh at release')
                facts={'run_id':session_id,'receiver_role':'receiver'}
                fsm.apply('receiver_hold_confirmed',event_id=operation+':fresh-hold',now_ns=self.clock(),evidence=held.evidence,**facts)
                fsm.apply(event,event_id=operation,now_ns=self.clock(),both_stationary=True)
                observations.extend((asdict(held),asdict(giver_still),asdict(receiver_still)))
                if parked_release_completion is not None:
                    observations.append({'explicit_parked_release_control':deepcopy(parked_release_completion),
                        'actual_arm_opening_command_claimed':False})
                already_applied=True
                if row['execution_domain']=='user_observed_physical_test' and not self._policy_role(row,'giver') and parked_release_completion is None:
                    authority=self.authority(deepcopy(row))
                    if (authority.get('explicit_user_observed_test_current') is not True or giver_provider is None or
                            not callable(giver_provider.authorize_manual_release)):
                        raise ValueError('Both current observed native owners and manual giver release boundary required')
                    self._fresh(row,epoch);self._phase_current(fsm)
                    if not fsm._valid_hold(fsm.hold,self.clock(),'receiver_holds_object'):
                        raise ValueError('Fresh receiver hold is required for manual native release authorization')
                    grant=giver_provider.authorize_manual_release(deepcopy(row),{
                        'receiver_hold_evidence':asdict(fsm.hold),'received_clock_domain':self.domain,
                        'native_release_challenge':deepcopy(release_challenge),'operator_confirmation_receipt':deepcopy(held.source_receipt)})
                    self._fresh(row,epoch);self._phase_current(fsm)
                    observations.append({'manual_giver_native_release_authorization':grant,'native_opening_command_sent':False})
            if command and self._policy_role(row,command[0]):
                if row['execution_domain']!='user_observed_physical_test':
                    raise ValueError('Policy transport is disabled outside a separate user-observed physical test; use shadow')
                role,op=command;readiness=self.readiness(session_id)
                if readiness['observed_execution_blockers']:raise ValueError(';'.join(readiness['observed_execution_blockers']))
                authority=self.authority(deepcopy(row))
                if authority.get('explicit_user_observed_test_current') is not True:
                    raise ValueError('Fresh separate observed execution authorization required')
                provider=self.registry.provider(role,spec['subskills'][role])
                native_state=provider.observe_state()
                self._fresh(row,epoch);self._phase_current(fsm)
                if (not isinstance(native_state,dict) or native_state.get('current_native_owner_ready') is not True or
                        not native_state.get('source_receipt')):
                    raise ValueError('Current native executor ownership/source evidence required')
                authorization=None
                if op=='release':
                    if fsm.state!='RELEASE' or not fsm._valid_hold(fsm.hold,self.clock(),'receiver_holds_object'):
                        raise ValueError('Fresh receiver hold is required at native release submission')
                    authorization={'receiver_hold_evidence':asdict(fsm.hold),'valid_until_ns':fsm.hold.received_ns+fsm.hold_ttl_ns,
                                   'received_clock_domain':self.domain,'must_validate_at_native_publication':True,
                                   'operator_confirmation_receipt':deepcopy(held.source_receipt),
                                   'native_release_challenge':deepcopy(release_challenge)}
                    if parked_release_completion is not None:
                        authorization['preinstalled_parked_release_grant']=deepcopy(parked_release_completion['release_grant'])
                command_id=uuid.uuid4().hex;submitted_ns=self.clock()
                command_body={'command_id':command_id,'run_id':session_id,
                    'object_id':row['protocol_object_id'],'role':role,'operation':op,'reference':deepcopy(spec['subskills'][role]),
                    'zone_registration_id':row['zone_registration_id'],'coordinator_epoch':epoch,
                    'coordinator_process_session':self.process,'observed_authority_receipt':deepcopy(authority)}
                receipt=provider.submit_observed(command_body,authorization)
                with self.lock:
                    current=self._session(session_id)
                    # Retain a late actual native response as original evidence;
                    # cancellation forbids advancing it into a successful step.
                    returned=asdict(receipt) if isinstance(receipt,DispatchReceipt) else deepcopy(receipt) if isinstance(receipt,dict) else {'invalid_return_type':type(receipt).__name__}
                    current.setdefault('dispatch_attempts',[]).append({'request':command_body,'returned_receipt':returned,
                        'response_received_coordinator_ns':self.clock(),'late_after_epoch_revocation':current['epoch']!=epoch,
                        'attainment_or_contact_inferred':False})
                    self._save(current)
                self._fresh(row,epoch)
                if (not isinstance(receipt,DispatchReceipt) or receipt.command_id!=command_id or receipt.run_id!=session_id or
                        receipt.object_id!=row['protocol_object_id'] or receipt.role!=role or receipt.dispatched is not True or
                        receipt.clock_domain!=self.domain or not submitted_ns<=receipt.received_ns<=self.clock() or not receipt.source):
                    raise ValueError('Actual correlated native host-publication receipt required; API ACK is insufficient')
                after=receipt.command_id
                with self.lock:
                    current=self._session(session_id)
                    if current['epoch']!=epoch:raise ValueError('Late native response after STOP is retained only as unknown outcome')
                    current['dispatch_receipts'].append(asdict(receipt));self._save(current)
            if event in STATEMENTS:
                statement,role=STATEMENTS[event]
                transfer_challenge=None;receiver_provider=None
                if event=='transfer_confirmed' and row['execution_domain']=='user_observed_physical_test':
                    if self.authority(deepcopy(row)).get('explicit_user_observed_test_current') is not True:
                        raise ValueError('Both current USER native owners required before independent transfer proof')
                    receiver_provider=self.registry.provider('receiver',spec['subskills']['receiver'])
                    if receiver_provider is None or not callable(receiver_provider.prepare_receiver_finish) or not callable(receiver_provider.authorize_receiver_finish):
                        raise ValueError('Current receiver native transfer-proof hooks required')
                    transfer_challenge=receiver_provider.prepare_receiver_finish(deepcopy(row))
                    after=(transfer_challenge.get('challenge') or {}).get('giver_release_receipt_id')
                    if not isinstance(after,str) or not after:raise ValueError('Actual giver opening receipt identity required before independent confirmation')
                    self._fresh(row,epoch)
                    with self.lock:
                        current=self._session(session_id)
                        if current['epoch']!=epoch:raise ValueError('Transfer challenge superseded by STOP')
                        current['pending_operation']['native_transfer_challenge']=deepcopy(transfer_challenge);self._save(current)
                elif after is None:after=self._last_command(row,role)
                with self._native_evidence_checkpoint(row,operation,fsm):
                    result=self.inbox(statement,session_id,row['protocol_object_id'],role,after,30.)
                self._fresh(row,epoch)
                if (not isinstance(result,CorrelatedEvidence) or result.run_id!=session_id or result.object_id!=row['protocol_object_id'] or
                        result.role!=role or result.after_command_id!=after or not result.source_receipt):
                    raise ValueError('Current run/object/command-correlated evidence required')
                evidence=result.evidence;observations.append(asdict(result))
                if transfer_challenge is not None:
                    self._fresh(row,epoch)
                    unfreeze=receiver_provider.authorize_receiver_finish(deepcopy(row),transfer_challenge,deepcopy(result.source_receipt))
                    self._fresh(row,epoch)
                    observations.append({'native_receiver_independent_transfer_finish':unfreeze,'actual_giver_opening_receipt_id':after})
                if event=='receiver_hold_confirmed':facts={'run_id':session_id,'receiver_role':'receiver'}
            elif event=='offer_ready':
                if not row['zone_registration_id']:raise ValueError('Register current shared zone before offering')
                with self._native_evidence_checkpoint(row,operation,fsm):
                    observations.append(asdict(self._stationary(row,'giver',after)))
                facts={'zone_registered':True,'giver_stationary':True}
            elif event=='receiver_ready':facts={'receiver_ready':True}
            elif event=='receiver_at_object':
                # Evidence requests are separate and fresh for each robot;
                # closed command/busy=false/time passage cannot substitute.
                with self._native_evidence_checkpoint(row,operation,fsm):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        giver=pool.submit(self._stationary,row,'giver',self._last_command(row,'giver'))
                        receiver=pool.submit(self._stationary,row,'receiver',self._last_command(row,'receiver'))
                        stationary=[giver.result(),receiver.result()]
                if any(not 0<=self.clock()-item.received_ns<=fsm.hold_ttl_ns for item in stationary):
                    raise ValueError('Both stationary observations must remain fresh at grasp check')
                observations.extend(asdict(item) for item in stationary)
                facts={'both_stationary':True}
                provider=self.registry.provider('giver',spec['subskills']['giver'])
                if row['execution_domain']=='user_observed_physical_test' and provider is not None and callable(provider.arm_release_guard):
                    self._fresh(row,epoch)
                    observations.append({'native_cooperative_dual_hold_guards':provider.arm_release_guard(deepcopy(row))})
                elif row['execution_domain']=='user_observed_physical_test':
                    raise ValueError('Native giver dual-hold protection must be bound before an observed grasp test')
            self._fresh(row,epoch)
            self._phase_current(fsm)
            if not already_applied:fsm.apply(event,event_id=operation,now_ns=self.clock(),evidence=evidence,**facts)
            with self.lock:
                current=self._session(session_id)
                if current['epoch']!=epoch:raise ValueError('Late step cannot reactivate stopped protocol')
                current.update(protocol_trace=fsm.trace,protocol_state=fsm.state,protocol_completed=fsm.completed,pending_operation=None,
                    status='COMPLETED' if fsm.completed else 'READY_FOR_OBSERVED_STEPS')
                current['observation_receipts'].extend(observations);self._save(current)
        except Exception as error:
            with self.lock:
                current=self._session(session_id);current['errors'].append({'operation_id':operation,'error':type(error).__name__+': '+str(error)})
                stop_current=current['epoch']==epoch
                self._save(current)
            if stop_current:self.cancel_session(session_id,'PROTOCOL_STEP_FAILED:'+type(error).__name__)
    def cancel_session(self,session_id,reason='explicit_operator_stop'):
        with self.lock:
            row=self._session(session_id);previous=deepcopy(row);row.update(epoch=uuid.uuid4().hex,status='STOPPED',pending_operation=None,
                protocol_state='SAFE_STOP',protocol_completed=False,stop_reason=reason)
            self._save(row)
            for key in tuple(self.native_phase_checkpoints):
                if key[0]==session_id:self.native_phase_checkpoints.pop(key,None)
            self.native_phase_condition.notify_all()
        spec=self._spec_for(row);stops={}
        def stop_role(role,ref):
            provider=self.registry.provider(role,ref)
            if provider is None or not callable(provider.stop):return {'delivery_unknown':True,'reason':'EXISTING_STOP_NOT_BOUND'}
            try:return provider.stop(session_id)
            except Exception as error:return {'delivery_unknown':True,'error':type(error).__name__+': '+str(error)}
        def stop_owned():
            try:return self.native_owned_stop(previous)
            except Exception as error:return {'delivery_unknown':True,'error':type(error).__name__+': '+str(error),
                'dual_hold_interlocks_unarmed':False}
        with ThreadPoolExecutor(max_workers=3) as pool:
            calls={role:pool.submit(stop_role,role,ref) for role,ref in spec['subskills'].items()}
            owned=pool.submit(stop_owned) if callable(self.native_owned_stop) else None
            evidence_cancellation_error=None
            try:
                if self.inbox is not None:self.inbox.cancel_run(session_id)
            except Exception as error:
                evidence_cancellation_error=type(error).__name__+': '+str(error)
            stops={role:call.result() for role,call in calls.items()}
            native_owned=owned.result() if owned is not None else None
        with self.lock:
            current=self._session(session_id);current['stop_receipts']=stops
            if native_owned is not None:
                current['native_owned_session_cleanup']=deepcopy(native_owned)
            if evidence_cancellation_error is not None:
                current['evidence_cancellation_error']=evidence_cancellation_error
            self._save(current)
        def finish_recording():
            # Cancellation revokes the epoch before any file/network work. A
            # delayed native record/start is cleaned by its own epoch fence.
            with self.record_io:
                if self.snapshot(session_id).get('recording_id'):
                    try:self._record_finish(session_id,{'status':'interrupted','reason':reason})
                    except Exception as error:
                        with self.lock:
                            current=self._session(session_id);current['errors'].append({'record_finish_error':str(error)});self._save(current)
        threading.Thread(target=finish_recording,daemon=True,name='cooperative-cancel-recording').start()
        result={'session':self.snapshot(session_id),'stop_receipts':stops,'physical_stop_confirmed':False}
        if native_owned is not None:result['native_owned_session_cleanup']=native_owned
        return result
    def training_requests(self,skill_id,version,settings=None):
        selected=self.skill(skill_id,version)
        if not selected['confirmed']:raise ValueError('Confirm exact cooperative and native specs first')
        return self.registry.training_requests(selected['spec'],settings)
    def shadow(self,session_id,source_requests=None):
        """Run only existing native pure predictors, preserving separate heads.

The provider validates its actual sensor/history/source/model bundle. A caller
request is retained as a request, never labelled a measured observation.
"""
        row=self.snapshot(session_id);spec=self._spec_for(row);epoch=row['epoch'];outputs={}
        self._fresh(row,epoch)
        for role,ref in spec['subskills'].items():
            state=self.registry.inspect(role,ref);provider=self.registry.provider(role,ref)
            if state['blockers'] or provider is None or not callable(provider.predict_shadow):
                raise ValueError(role+':PINNED_NATIVE_SHADOW_PROVIDER_UNAVAILABLE')
            if not ref.get('model_id'):raise ValueError(role+':PINNED_NATIVE_MODEL_REQUIRED_FOR_SHADOW')
            result=provider.predict_shadow(deepcopy(ref),deepcopy((source_requests or {}).get(role,{})))
            self._fresh(row,epoch)
            if (not isinstance(result,dict) or result.get('dispatch_performed') is not False or
                    result.get('checkpoint_sha256')!=ref['checkpoint_sha256'] or
                    result.get('action_contract')!=ROLE_CONTRACTS[role] or not result.get('source_evidence')):
                raise ValueError('Pure predictor must bind model/native input evidence and explicitly perform no dispatch')
            actions=result.get('actions')
            if (not isinstance(actions,list) or not 1<=len(actions)<=128 or any(
                not isinstance(action,list) or len(action)!=ROLE_CONTRACTS[role]['dimension'] or any(
                    isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)
                    for value in action) for action in actions)):
                raise ValueError('Finite separate native action chunk required')
            outputs[role]=deepcopy(result)
        receipt={'schema':'explorer_cooperative_shadow_v1','shadow_id':uuid.uuid4().hex,'session_id':session_id,
            'epoch':epoch,'skill_id':row['skill_id'],'skill_version':row['skill_version'],'spec_sha256':row['spec_sha256'],
            'native_outputs':outputs,'source_requests_not_claimed_measurements':deepcopy(source_requests or {}),
            'dispatch_performed':False,'physical_mastery_qualified':False,'action_spaces_merged':False}
        receipt['sha256']=digest(receipt)
        with self.lock:
            self._fresh(row,epoch);write_original(self.root/'shadows'/(receipt['shadow_id']+'.json'),canonical(receipt))
        return receipt
    def review_recording(self,session_id,recording_id,body,*,authenticated_actor,manifest_path=None):
        row=self.snapshot(session_id);spec=self._spec_for(row);_id(recording_id)
        if manifest_path is None:
            if self.recorder is None:raise ValueError('Imported cooperative manifest path required')
            manifest_path=self.recorder.root/recording_id/'manifest.json'
        path=Path(manifest_path).resolve()
        if not path.is_relative_to(self.managed):raise ValueError('Original cooperative import must remain in managed state')
        source=verify_recording(path.parent)
        if (source.get('recording_id')!=recording_id or source.get('context',{}).get('session_id')!=session_id or
                source['context'].get('spec_sha256')!=spec['spec_sha256']):
            raise ValueError('Original sidecar belongs to another session/task version')
        if (body.get('outcome') not in ('success','failure','unknown') or not authenticated_actor or
                body.get('whole_episode_seen') is not True or body.get('object_instance_id')!=row['object_instance_id']):
            raise ValueError('Explicit source-bound whole-video/object review required')
        outcomes=body.get('phase_outcomes',{})
        if not isinstance(outcomes,dict) or set(outcomes)!=set(PHASES) or any(value not in ('success','failure','unknown') for value in outcomes.values()):
            raise ValueError('Independent explicit outcomes for every cooperative phase required')
        references=body.get('native_episode_refs',{})
        if not isinstance(references,dict) or set(references)!={'giver','receiver'}:
            raise ValueError('Both original native episode imports must be bound, never raw intent targets')
        reports={};reasons=[]
        for role,reference in references.items():
            report=self.registry.inspect_episode(role,spec['subskills'][role],reference);reports[role]=report
            if report.get('admitted_for_training') is not True or report.get('eligible_as_successful_bc_expert') is not True:
                reasons.append(role+':NATIVE_EPISODE_NOT_QUALIFIED_EXPERT')
            if (report.get('skill_id')!=spec['subskills'][role]['skill_id'] or
                    report.get('skill_version')!=spec['subskills'][role]['skill_version'] or
                    report.get('spec_sha256')!=spec['subskills'][role]['spec_sha256']):
                reasons.append(role+':NATIVE_SKILL_REFERENCE_MISMATCH')
            if report.get('cooperative_parent_session_id')!=session_id:reasons.append(role+':ORIGINAL_PARENT_SESSION_NOT_BOUND')
            if report.get('split')!=row['split']:reasons.append(role+':ORIGINAL_SPLIT_CANNOT_BE_REASSIGNED')
            started=source.get('native_role_sources',{}).get(role) or {}
            ident=started.get('recording_id',started.get('episode_id'))
            if ident is None or report.get('original_episode_id')!=ident:reasons.append(role+':ORIGINAL_STARTED_EPISODE_ID_MISMATCH')
        domains=[reports[role].get('execution_domain') for role in ('giver','receiver')]
        if any(domain not in ('REAL','SIMULATION') for domain in domains) or len(set(domains))!=1:
            reasons.append('ROLE_EXECUTION_DOMAINS_DIFFER_NOT_ONE_REAL_COOPERATIVE_EPISODE')
        source_domain=source.get('context',{}).get('source_execution_domain')
        if source_domain not in ('REAL','SIMULATION') or any(domain!=source_domain for domain in domains):
            reasons.append('ORIGINAL_ROLE_DOMAINS_NOT_EXPLICITLY_BOUND_TO_COOPERATIVE_SOURCE')
        if source.get('block_reasons') or source.get('dropped_packets'):reasons.append('DUAL_CAMERA_SIDECAR_SOURCE_INCOMPLETE')
        valid_samples=[sample for sample in source.get('samples',[]) if not sample.get('late_after_finish')]
        for role in ('giver','receiver'):
            if not any((sample.get('roles',{}).get(role) or {}).get('frame_id') for sample in valid_samples):
                reasons.append(role+':NO_PRE_FINISH_ORIGINAL_CACHED_CAMERA')
        if body['outcome']!='success' or any(value!='success' for value in outcomes.values()):
            reasons.append('WHOLE_TASK_OR_PHASE_OPERATOR_OUTCOME_NOT_SUCCESSFUL')
        if row['zone_registration_id'] is None:reasons.append('NO_CURRENT_SHARED_ZONE_REGISTRATION')
        review={'schema':'explorer_cooperative_review_v1','review_id':uuid.uuid4().hex,'session_id':session_id,
            'skill_id':row['skill_id'],'skill_version':row['skill_version'],'spec_sha256':row['spec_sha256'],
            'recording_id':recording_id,'source_manifest':str(path),'source_manifest_sha256':sha_file(path),
            'split':row['split'],'scene_id':row['scene_id'],'object_instance_id':row['object_instance_id'],
            'outcome':body['outcome'],'phase_outcomes':deepcopy(outcomes),'reviewed_by':authenticated_actor,
            'basis':'authenticated_operator_video_outcomes_and_separately_verified_native_targets_not_model_physical_qualification',
            'native_episode_refs':deepcopy(references),'native_admission_reports':reports,
            'admitted_for_training':not reasons,'reasons':reasons,'clock_domains_equated':False,
            'action_spaces_merged':False,'model_physical_success_inferred':False}
        review['review_sha256']=digest(review)
        with self.lock:
            for other_path in (self.root/'reviews').glob('*.json'):
                other=json.loads(other_path.read_bytes())
                if other['recording_id']==recording_id:
                    raise ValueError('Original recording already reviewed; preserve immutable review rather than duplicate or relabel')
                groups=('scene_id','object_instance_id','session_id')
                if (other.get('admitted_for_training') and row['split']!=other['split'] and
                        any(other.get(field)==review.get(field) for field in groups)):
                    raise ValueError('Original cooperative scene/object/session cannot cross splits')
                for role in ('giver','receiver'):
                    prior=(other.get('native_admission_reports',{}).get(role) or {}).get('original_episode_id')
                    if prior and prior==reports[role].get('original_episode_id'):
                        raise ValueError('One original native episode cannot be counted as multiple cooperative recordings or splits')
            write_original(self.root/'reviews'/(review['review_id']+'.json'),canonical(review))
        return review
    def build_aggregate(self,skill_id,version):
        selected=self.skill(skill_id,version);spec=selected['spec'];reviews=[]
        with self.lock:paths=sorted((self.root/'reviews').glob('*.json'))
        # File IO and source admissions run outside the protocol/STOP lock.
        for path in paths:
            review=json.loads(path.read_bytes())
            if review['skill_id']==spec['skill_id'] and review['skill_version']==version and review['admitted_for_training']:
                expected=review.pop('review_sha256')
                if digest(review)!=expected:raise ValueError('Immutable cooperative review changed')
                review['review_sha256']=expected
                if sha_file(review['source_manifest'])!=review['source_manifest_sha256']:
                    raise ValueError('Original cooperative sidecar changed after review')
                verify_recording(Path(review['source_manifest']).parent)
                for role,reference in review['native_episode_refs'].items():
                    current=self.registry.inspect_episode(role,spec['subskills'][role],reference)
                    if current!=review['native_admission_reports'][role]:
                        raise ValueError('Original native admission/source evidence changed after review')
                reviews.append(review)
        groups={split:[review for review in reviews if review['split']==split] for split in ('train','development','sealed_test')}
        if not groups['train'] or not groups['development']:raise ValueError('Distinct accepted native-paired train and development sessions required')
        manifest={'schema':'explorer_cooperative_accepted_dataset_v1','skill_spec':spec,'spec_sha256':spec['spec_sha256'],
            'native_action_contracts':deepcopy(ROLE_CONTRACTS),'roles_use_existing_native_datasets_and_trainers':True,
            'cooperative_originals':reviews,'train_recordings':[review['recording_id'] for review in groups['train']],
            'development_recordings':[review['recording_id'] for review in groups['development']],
            'sealed_recordings':[review['recording_id'] for review in groups['sealed_test']],
            'sealed_not_for_fitting':True,'action_spaces_merged':False,'synchronous_cross_robot_tensor_invented':False,
            'global_split_groups_verified':True,'model_input_plan':model_input_plan(),'physical_model_mastery_inferred':False}
        checksum=digest(manifest);folder=self.root/'aggregates'/checksum;folder.mkdir(exist_ok=True)
        path=folder/'manifest.json'
        if path.exists():
            if path.read_bytes()!=canonical(manifest):raise ValueError('Immutable cooperative aggregate conflict')
        else:write_original(path,canonical(manifest))
        return {'aggregate_manifest':str(path),'aggregate_manifest_sha256':checksum,'manifest':manifest,
                'native_training_plan':self.registry.training_requests(spec),'jobs_enqueued':False}
    def close(self):
        # Revoke metadata lifecycle only; application shutdown is never a new
        # physical command authorization or automatic native control replay.
        with self.lock:self.closed=True;self.native_phase_condition.notify_all()
        if self.inbox is not None:
            for row in self.snapshot()['sessions']:
                if row.get('pending_operation'):self.inbox.cancel_run(row['session_id'])
        if self.recorder is not None:self.recorder.close()
