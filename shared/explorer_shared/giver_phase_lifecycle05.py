"""ENGINEERING_GLUE: explicit USER cancel/park/adopt/begin/resume for SAME giver.

No new worker/publisher/feedback/settings; STOP is cancel-only. Current state is
command-estimate provenance, never measured hold, stationarity or attainment.
Source HTTP and central one-use USER choices are wired separately by integrator.
"""
from copy import deepcopy
import re
import threading
import uuid

from .contracts import digest
from .cooperative_action_constraint05 import verify_constraint


class GiverPhaseLifecycle:
    def __init__(self,owner):
        self.owner=owner;self.guard=owner.guard;self.mux=owner.mux;self.activation=owner.activation
        self.process=uuid.uuid4().hex;self.transition_thread=None
    def in_transition(self):return self.transition_thread==threading.get_ident()
    def _body(self,body,actor,extra=()):
        common={'run_id','object_id','coordinator_epoch','authorization_id','expected_state_id','expected_revision',
            'observing','neutral','user_observed_test_confirmed','phase_id'}
        if (not isinstance(body,dict) or set(body)-common-set(extra) or not isinstance(actor,str) or not actor or
                not all(body.get(key) is True for key in ('observing','neutral','user_observed_test_confirmed'))):
            raise ValueError('ID-only explicit neutral USER giver phase operation and authenticated actor required')
        if body.get('phase_id') is not None and (not isinstance(body['phase_id'],str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}',body['phase_id'])):
            raise ValueError('Bounded server-derived optional native phase annotation required')
        self.guard._match(body)
        if (body.get('expected_state_id')!=self.guard.journal.state_id or type(body.get('expected_revision')) is not int or
                body['expected_revision']!=self.guard.revision or self.guard.journal.path is None or self.guard.journal.fault is not None or
                self.guard.journal.recovery_required or self.guard.journal.pending):
            raise ValueError('Exact healthy durable giver state/revision required')
        source=(self.guard.binding or {}).get('observed_source_authorization') or {}
        if source.get('actor_id')!=actor:raise ValueError('Same actual source USER actor required')
        return source
    def _current(self,body,actor,*,parked_stage=None):
        old=self._body(body,actor,{'park_request_id','previous_authorization_id','previous_native_generation','previous_control_epoch','control_epoch'})
        self.activation.require(body.get('authorization_id'));receipt=self.activation.receipt(body['authorization_id']) or {}
        if receipt.get('actor_id')!=actor or receipt.get('input_mode')!=old.get('input_mode'):
            raise ValueError('Same current USER source input mode/actor required')
        parked=self.guard.parked or {}
        if (parked.get('process')!=self.process or parked.get('park_request_id')!=body.get('park_request_id') or
                parked_stage is not None and parked.get('stage') not in parked_stage):
            raise ValueError('Exact current source-owned parked phase lineage required')
        return receipt,parked
    def _event(self,kind,**values):
        return {'schema':'explorer_native_giver_phase_event_v1','receipt_id':uuid.uuid4().hex,'kind':kind,
            'native_monotonic_ns':self.mux.tap.clock(),'clock_domain':self.mux.tap.clock_domain,'boot_id':self.mux.tap.boot_id,
            'measured_hold_or_stationarity_claimed':False,**deepcopy(values)}
    def _retain(self,event):
        self.guard.phase_event_count+=1
        event.update(source_event_ordinal=self.guard.phase_event_count,prior_event_chain_sha256=self.guard.phase_event_chain_sha256)
        event['source_event_sha256']=digest(event);self.guard.phase_event_chain_sha256=event['source_event_sha256']
        self.guard.phase_history.append(deepcopy(event));self.guard.phase_history=self.guard.phase_history[-32:]
    def park(self,body,*,authenticated_actor):
        with self.guard.metadata_lock:
            source=self._body(body,authenticated_actor,{'control_epoch','expected_native_generation'})
            if self.guard.parked is not None:raise ValueError('Already parked; do not replay original STOP')
            if body.get('authorization_id')!=self.guard.binding['authorization_id']:raise ValueError('Exact previous native authorization required')
            with self.mux.teleop.lock:
                previous=deepcopy(self.guard.binding)
                result=self.owner.stop(body)  # SAME original cancel-only STOP; no new hold pose.
                if self.mux.active or not self.mux.teleop.stop_latched:raise ValueError('Original scoped STOP return unavailable')
                event=self._event('ORIGINAL_CANCEL_ONLY_PARK',previous_owner=previous,
                    original_stop_return=result,source_command='same_original_STOP_no_hold_pose',native_command_called=True,
                    stopped_generation=self.mux.teleop.generation,stopped_mux_epoch=self.mux.epoch,phase_id=body.get('phase_id'))
                event['park_request_id']=uuid.uuid4().hex
            def seal():
                with self.guard.lock:
                    if (self.mux.active or not self.mux.teleop.stop_latched or self.guard.binding!=previous or
                            self.mux.teleop.generation!=event['stopped_generation'] or self.mux.epoch!=event['stopped_mux_epoch'] or
                            self.activation.current_authorization_id() not in (None,previous['authorization_id'])):
                        raise ValueError('Native owner changed after explicit park STOP')
                    self.guard.parked={'park_request_id':event['park_request_id'],'process':self.process,'stage':'STOPPED_BARRIER',
                        'original_stop':deepcopy(event),'model':deepcopy(self.owner.policy),'constraint':deepcopy(self.owner.action_constraint),
                        'origin':None,'previous_input_mode':source['input_mode']}
                    self.guard.pending=self.guard.grant=None;self.guard._bump_revision();self._retain(event)
                    return event
            receipt=self.guard._durable('park_barrier_after_original_STOP',seal)
            return {'park':receipt,'native_command_called':True,'ready_for_command_estimate_successor':self.guard.drained()['ready'],
                'readiness_is_not_reference_or_attainment_qualification':True,'interlock':self.guard.status()}
    def _origin(self,parked):
        self.guard.require_drained()
        if self.guard.reference_fault is not None:raise ValueError('NOT_READY_ORIGINAL_REFERENCE_FAULT')
        reference=self.owner.reference()  # Read-only ORIGINAL guard; no HOME/readback.
        actual=self.mux.tap.issued_snapshot();header=self.mux.tap.reference or {}
        if (reference.get('boot_id')!=self.mux.tap.boot_id or reference.get('measured') is not False or
                reference.get('servo_deg')!=actual['command9'][:6] or actual['command9'][6:]!=[0.,0.,0.] or
                header.get('native_guard_verified') is not True):
            raise ValueError('NOT_READY_ACTUAL_ISSUED_REFERENCE_AND_BASE_ZERO_REQUIRED')
        return {'schema':'explorer_native_giver_park_command_context_v1','state_source':'command_estimate',
            'actual_issued':actual,'original_reference_sha256':header.get('source_sha256'),
            'original_reference_guard_result':reference,'park_request_id':parked['park_request_id'],
            'measured_feedback':None,'measured_feedback_mask':[False]*6,'measured_hold_or_stationarity_claimed':False}
    def _stable_origin(self,origin):
        self.guard.require_drained();current=self.mux.tap.issued_snapshot()
        if (self.guard.reference_fault is not None or (self.mux.tap.reference or {}).get('source_sha256')!=origin['original_reference_sha256'] or
                any(current[key]!=origin['actual_issued'][key] for key in ('arm_receipt','base_receipt','command9'))):
            raise ValueError('NOT_READY_SOURCE_COMMAND_OR_REFERENCE_CHANGED_DURING_PERSISTENCE')
    def adopt(self,body,*,authenticated_actor):
        with self.guard.metadata_lock:
            receipt,parked=self._current(body,authenticated_actor,parked_stage={'STOPPED_BARRIER','PARKED_NEW_USER'})
            previous=deepcopy(self.guard.binding);mode=parked['previous_input_mode'];auth=body['authorization_id']
            if (auth==previous['authorization_id'] or body.get('previous_authorization_id')!=previous['authorization_id'] or
                    body.get('previous_native_generation')!=previous['native_generation'] or
                    body.get('previous_control_epoch')!=previous.get('native_control_epoch') or self.mux.active or not self.mux.teleop.stop_latched):
                raise ValueError('NEW USER authorization and exact previous owner under original STOP required')
            frame=receipt.get('camera_frame') or {};created=receipt.get('native_monotonic_ns');received=frame.get('received_monotonic_ns')
            cutoff=parked['original_stop']['native_monotonic_ns'];domain=frame.get('clock_domain')
            relation=(domain==self.mux.tap.clock_domain or domain=='native_kernel_monotonic:'+self.mux.tap.boot_id and
                getattr(self.activation,'clock',None) is self.mux.tap.clock)
            if (not relation or frame.get('boot_id')!=self.mux.tap.boot_id or type(created) is not int or type(received) is not int or
                    not cutoff<=received<=created<=self.mux.tap.clock()):
                raise ValueError('Fresh explicit USER image after original park in verified source clock required')
            origin=self._origin(parked);policy=deepcopy(self.owner.policy);registered=deepcopy(self.owner.action_constraint)
            if policy!=parked['model'] or registered!=parked['constraint']:raise ValueError('Pinned original model/constraint changed while parked')
            if registered is not None:verify_constraint(registered['constraint'],binding=previous,model=policy)
            def mutate():
                with self.guard.lock:
                    self._stable_origin(origin)
                    if (self.guard.parked!=parked or self.guard.binding!=previous or self.mux.active or not self.mux.teleop.stop_latched or
                            self.mux.teleop.generation!=parked['original_stop']['stopped_generation'] or
                            self.activation.current_authorization_id()!=auth or self.activation.receipt(auth)!=receipt):
                        raise ValueError('Parked USER/source owner changed during successor persistence')
                    issued=self.mux.tap.issued_snapshot()
                    if (issued['arm_receipt']!=origin['actual_issued']['arm_receipt'] or issued['base_receipt']!=origin['actual_issued']['base_receipt'] or
                            issued['command9']!=origin['actual_issued']['command9']):raise ValueError('Issued source context changed during successor persistence')
                    self.guard.binding.update(authorization_id=auth,native_generation=self.mux.teleop.generation,
                        native_control_epoch=None,observed_source_authorization=deepcopy(receipt))
                    if registered is not None:
                        self.owner.action_constraint=deepcopy(registered);self.owner.action_constraint['authorization_id']=auth
                        self.owner.action_constraint['previous_authorization_lineage']={
                            'previous_registration_sha256':digest(registered),'previous_authorization_id':previous['authorization_id'],
                            'reason':'exact_same_constraint_model_Spec_explicit_USER_successor'}
                    self.guard._bump_revision();self.guard.pending=self.guard.grant=None
                    event=self._event('NEW_USER_COMMAND_ESTIMATE_SUCCESSOR',previous_owner=previous,new_owner=self.guard.binding,
                        origin=origin,phase_id=body.get('phase_id'),verified_clock_relation={'frame_domain':domain,'native_domain':self.mux.tap.clock_domain,
                            'same_source_clock_callable':getattr(self.activation,'clock',None) is self.mux.tap.clock},native_command_called=False)
                    self.guard.parked.update(stage='PARKED_NEW_USER',origin=deepcopy(origin),constraint=deepcopy(self.owner.action_constraint))
                    self._retain(event);return event
            event=self.guard._durable('new_USER_parked_successor',mutate)
            return {'successor':event,'native_command_called':False,'interlock':self.guard.status(),'execution_authority':False}
    def _cleanup(self,epoch,auth,manual_generation=None):
        with self.mux.teleop.lock:
            current=self.activation.current_authorization_id();generation=(self.guard.binding or {}).get('native_generation')
            if (self.mux.active and self.mux.epoch==epoch and self.mux.generation==generation and self.mux.teleop.generation==generation and
                    current in (None,auth)):
                try:self.owner.stop({**self.guard.binding,'control_epoch':epoch})
                except Exception:pass  # Exact owned cleanup unknown; never adopt another epoch.
            elif (epoch is None and not self.mux.active and type(manual_generation) is int and
                    self.mux.teleop.generation==manual_generation and current in (None,auth)):
                try:self.owner.stop({**self.guard.binding,'expected_native_generation':manual_generation})
                except Exception:pass
    def begin(self,body,*,authenticated_actor):
        with self.guard.metadata_lock:
            receipt,parked=self._current(body,authenticated_actor,parked_stage={'PARKED_NEW_USER'})
            if parked['previous_input_mode']!='cooperative_policy':raise ValueError('Manual input uses explicit parked resume without policy begin')
            origin=self._origin(parked);result=None
            def start():
                nonlocal result
                with self.guard.lock:
                    self._stable_origin(origin)
                    if self.mux.teleop.generation!=self.guard.binding['native_generation']:
                        raise ValueError('Original source generation changed before parked begin')
                    if self.activation.current_authorization_id()!=body['authorization_id'] or self.activation.receipt(body['authorization_id'])!=receipt:
                        raise ValueError('Current USER authorization changed during parked begin persistence')
                    self.transition_thread=threading.get_ident()
                    try:
                        selected={**self.guard.binding,'observing':True,'neutral':True,'model':deepcopy(parked['model'])}
                        result=self.activation.transition(lambda:self.owner.begin(selected),body['authorization_id'])
                    finally:self.transition_thread=None
                    self.guard.parked.update(stage='BEGIN_CLUTCHED',control_epoch=result['control_epoch'])
                    self._retain(self._event('EXPLICIT_USER_BEGIN_CLUTCHED',native_result=result,native_command_called=True,phase_id=body.get('phase_id')))
                    return result
            try:
                native=self.guard._durable('explicit_USER_parked_begin',start)
                self.activation.require(body['authorization_id']);self.mux._check(native['control_epoch'])
                return {'native':native,'interlock':self.guard.status(),'native_command_called':True}
            except Exception:
                if isinstance(result,dict):self._cleanup(result['control_epoch'],body['authorization_id'])
                raise
    def resume(self,body,*,authenticated_actor):
        with self.guard.metadata_lock:
            receipt,parked=self._current(body,authenticated_actor,parked_stage={'PARKED_NEW_USER','BEGIN_CLUTCHED'})
            policy=parked['previous_input_mode']=='cooperative_policy';epoch=parked.get('control_epoch') if policy else None;result=None
            if policy and (parked['stage']!='BEGIN_CLUTCHED' or body.get('control_epoch')!=epoch):raise ValueError('Exact CLUTCHED source phase epoch required')
            origin=self._origin(parked)
            def resume():
                nonlocal result
                with self.guard.lock:
                    self._stable_origin(origin)
                    if self.activation.current_authorization_id()!=body['authorization_id'] or self.activation.receipt(body['authorization_id'])!=receipt:
                        raise ValueError('Current USER authorization changed during parked resume persistence')
                    self.transition_thread=threading.get_ident()
                    try:
                        if policy:result=self.mux.resume(epoch,observing=True)
                        else:result=self.owner.manual_resume({**self.guard.binding,'observing':True,'expected_native_generation':self.mux.teleop.generation})
                    finally:self.transition_thread=None
                    self.guard.parked=None;self.guard._bump_revision();self.guard.pending=self.guard.grant=None
                    self._retain(self._event('EXPLICIT_USER_PHASE_RESUMED',native_result=result,native_command_called=True,
                        source_command_origin=origin,phase_id=body.get('phase_id'),native_operator_lease_automatically_renewed=False))
                    return result
            try:
                native=self.guard._durable('explicit_USER_parked_resume',resume);self.guard._current()
                return {'native':native,'interlock':self.guard.status(),'native_command_called':True}
            except Exception:
                if isinstance(result,dict):self._cleanup(epoch,body['authorization_id'],None if policy else result.get('manual_generation'))
                raise
    def status(self):
        return {'schema':'explorer_native_giver_phase_lifecycle_v1','supported':True,'parked':deepcopy(self.guard.parked),
            'same_original_stream_worker':True,'automatic_lease_renewal':False,'new_servo_publisher_or_feedback_reader_created':False,
            'original_native_commands_are_explicit_USER_control':True,
            'command_context_is_not_measured_hold_or_stationarity':True,'physical_qualification':False}
