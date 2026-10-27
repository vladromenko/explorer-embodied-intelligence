"""ENGINEERING_GLUE: real native metadata APIs, no control/enable/action calls.

Root injects its authenticated node request and PUBLIC source workflow validators.
Record/start does not create teleoperation: SO101's original producer explicitly
requires an already observed native control session. Missing capabilities block.
"""
from copy import deepcopy
import threading
import uuid

from .cooperative_contract05 import digest
from .experiments import experiment_id


class NativeMetadataRecorder:
    def __init__(self,role,request,prepare_context,*,capture_started=None,receiver_profile='legacy'):
        if role not in ('giver','receiver') or not callable(request) or not callable(prepare_context):
            raise ValueError('Authenticated native request and public context validator required')
        self.role,self.request,self.prepare,self.started=role,request,prepare_context,capture_started
        if receiver_profile not in ('legacy','common') or role=='giver' and receiver_profile!='legacy':
            raise ValueError('Explicit supported receiver capture profile required')
        self.common_receiver=role=='receiver' and receiver_profile=='common'
        self.lock=threading.Lock();self.active=None
    def _status(self):
        return self.request('GET','/native-capture/status' if self.role=='giver' else '/station/status',None)
    @staticmethod
    def _id(row):
        if not isinstance(row,dict):raise ValueError('Original native metadata receipt required')
        value=row.get('recording_id',row.get('episode_id'))
        if not isinstance(value,str) or not value or len(value)>160:raise ValueError('Original producer recording ID not exposed')
        return value
    def record_start(self,context):
        with self.lock:
            if self.active is not None:raise ValueError('Existing native recording binding is active')
            if self.role=='giver':
                caps=self.request('GET','/native-capture/capabilities',None)
                if (caps.get('metadata_skill_context_supported') is not True or caps.get('native_owner_ready') is not True or
                        caps.get('action_dimension')!=9):raise ValueError('Original native9 producer/tap is not ready')
            else:
                if self.common_receiver:
                    caps=self.request('GET','/so101-capture/capabilities',None)
                    if caps.get('cooperative_permission_recording_supported') is not True:
                        raise ValueError('Guarded cooperative COMMON capture source required; no raw fallback')
                status=self._status()
                if status.get('recording_identity_fence') is not True:
                    raise ValueError('Native metadata recorder lacks identity-fenced finish; upgrade source service first')
                if status.get('recording') or status.get('mode') not in ('teleoperation','policy'):
                    raise ValueError('An already user-observed native SO101 session with no active recording is required')
            prepared=self.prepare(deepcopy(context))
            if not isinstance(prepared,dict) or not isinstance(prepared.get('body'),dict):
                raise ValueError('Public source workflow must return validated native metadata body')
            path='/native-capture/record/start' if self.role=='giver' else '/station/record/start'
            body=deepcopy(prepared['body'])
            if self.common_receiver:
                actor=prepared.get('capture_actor_id');proof=prepared.get('capture_permission')
                if not isinstance(actor,str) or not actor or not isinstance(proof,dict) or set(proof)!={'grant_id','binding','control_epoch'}:
                    raise ValueError('Server-bound current cooperative capture actor/permission required')
                body={'record_start_body':body,'permission':deepcopy(proof),'actor_id':actor}
                path='/so101-capture/record/start'
            result=self.request('POST',path,body);identity=self._id(result)
            reference={'recording_id':identity,'episode_id':identity,'role':self.role,
                'native_start_result':deepcopy(result),'source_context_sha256':digest(prepared['body']),
                'source_capture':deepcopy(prepared.get('source_capture')),
                'metadata_only_no_control_started':True}
            if self.common_receiver:reference.update(capture_profile='common',capture_actor_id=actor)
            self.active=identity
            if self.started is not None:
                try:reference['source_capture_binding']=self.started(deepcopy(prepared),identity)
                except Exception as error:reference['source_capture_binding_error']=type(error).__name__+': '+str(error)
            return reference
    def _current(self,reference):
        identity=self._id(reference)
        if self.active!=identity:raise ValueError('Another native recording binding is current')
        state=self._status()
        current=(state.get('active_recording_id') if self.role=='giver' else (state.get('recording') or {}).get('episode_id'))
        if current!=identity:raise ValueError('Original native recording changed; refuse stale metadata mutation')
        return identity
    def record_stage(self,reference,phase_id,coordinator_context):
        with self.lock:
            identity=self._current(reference)
            body={'recording_id':identity,'phase_id':phase_id}
            if self.role=='receiver':body.update(episode_id=identity,request_id=uuid.uuid4().hex,
                annotation_source='cooperative_coordinator_explicit_phase',phase_scope='coordinator_phase',
                coordinator_context=deepcopy(coordinator_context))
            path='/native-capture/record/stage' if self.role=='giver' else '/station/record/stage'
            if self.common_receiver:path='/so101-capture/record/stage';body['actor_id']=reference.get('capture_actor_id')
            return self.request('POST',path,body)
    def record_finish(self,reference,outcome):
        with self.lock:
            identity=self._current(reference)
            if not isinstance(outcome,dict):raise ValueError('Explicit role outcome or unknown required')
            if self.role=='giver':body={'recording_id':identity,'outcome_evidence':deepcopy(outcome)}
            else:
                status=outcome.get('status','unknown')
                # Whole-coordinator success never supplies this per-native role
                # label. A role success requires an explicit actual observation.
                if status not in ('success','failure','unknown','interrupted'):raise ValueError('Native outcome label invalid')
                if status in ('success','failure') and outcome.get('method')!='operator_observation':
                    raise ValueError('Explicit native-role operator outcome observation required')
                body={'episode_id':identity,'outcome':status if status!='interrupted' else 'unknown'}
            path='/native-capture/record/finish' if self.role=='giver' else '/station/record/finish'
            if self.common_receiver:
                path='/so101-capture/record/finish'
                body={'recording_id':identity,'outcome':body['outcome'],'actor_id':reference.get('capture_actor_id')}
            result=self.request('POST',path,body)
            self.active=None;return result


def public_capture_preparers(store,*,rosmaster_create_session,rosmaster_prepare_capture,
                             lab_create_session,lab_capture_context):
    """Bind real source sessions/variants with public source admission functions.

These calls only write teaching metadata. An explicit existing variant must
match its pinned native split. No control session is implicitly created.
"""
    def common(context):
        if not isinstance(context.get('variant_id'),str) or not context['variant_id']:
            raise ValueError('Select the explicit existing native demonstration variant for each role')
        return {'session_id':context['session_id'],'scene_id':context['scene_id'],'scene':context['scene_id'],
            'split':context['split'],'object_instance_id':context['object_instance_id']}
    def giver(context):
        session=common(context);project=context['project_id']
        if project not in ('02','03'):raise ValueError('Actual ROSMASTER capture input project02/03 required')
        adapter='so101_leader_retarget' if project=='03' else 'keyboard_gamepad'
        rosmaster_create_session(store,context['skill_id'],context['skill_version'],
            {**session,'project_id':project,'input_mode':adapter})
        intent,native=rosmaster_prepare_capture(store,{**context,'version':context['skill_version'],
            'spec_sha256':context['skill_spec_sha256'],'input_mode':adapter,'request_id':uuid.uuid4().hex},project_id=project)
        # This parent link/actual input is metadata, not proof of native dispatch.
        native.update(cooperative_parent=deepcopy(context['cooperative_parent']),
            cooperative_parent_session_id=context['cooperative_parent_session_id'],
            input_mode=context['input_mode'],workflow_demonstration_adapter=adapter)
        return {'body':{'skill_context':native},'source_capture':intent}
    def receiver(context):
        lab_create_session(store,context['skill_id'],context['skill_version'],common(context))
        prepared=lab_capture_context(store,context['skill_id'],context['skill_version'],context)
        body=deepcopy(prepared['record_start_body'])
        if body.get('skill_spec_sha256')!=context['skill_spec_sha256']:raise ValueError('Pinned source04 version changed')
        body['project_id']=experiment_id('so101_source_learning')
        body['context'].update(deepcopy(context['context']))
        return {'body':body,'source_capture':prepared}
    return {'giver':giver,'receiver':receiver}
