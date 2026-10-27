"""ENGINEERING_GLUE: current COMMON Pi permission + original native6 receipts.

No camera/servo owner, model load, automatic user renewal or control setting.
The optional predictor is pure isolated-worker IPC; commands use the common
native atomic permission.command boundary, never a check-then-unguarded action.
"""
from copy import deepcopy
import math
import re
import threading
import time

from .cooperative_contract05 import ROLE_CONTRACTS
from .contracts import digest
from .station_observed_http import StationObservedHttp
from .so101_station import LOW,HIGH
from .handover_runtime import DispatchReceipt


class NativeReceiverTransport:
    def __init__(self,request,cooperative_binding,permission_binding,grant,reference,action_provider=None,*,
                 clock_domain,clock=time.monotonic_ns,sleep=time.sleep,timeout_s=.5,dual_hold_integration_ready=False):
        if not callable(request):raise ValueError('Trusted authenticated original Pi request required')
        self.request,self.binding=request,deepcopy(cooperative_binding)
        self.permission=StationObservedHttp(request,permission_binding);self.grant=deepcopy(grant);self.reference=deepcopy(reference)
        self.predict=action_provider;self.domain,self.clock,self.sleep,self.timeout=clock_domain,clock,sleep,timeout_s
        self.revoked=threading.Event();self.lock=threading.Lock();self.sequence=-1
        if type(dual_hold_integration_ready) is not bool:raise ValueError('Server-owned coordinator integration flag required')
        self.dual_hold_integration_ready=dual_hold_integration_ready
        self.metadata_lock=threading.Lock();self.interlock_lock=threading.Lock();self.interlock_cache={};self.pending_finish=None
        if (grant.get('binding')!=self.permission.binding or not grant.get('grant_id') or not grant.get('control_epoch') or
                not grant.get('provider_process') or not grant.get('boot_id') or not grant.get('clock_domain') or
                not (grant.get('displayed_frame') or {}).get('camera_id') or
                any(permission_binding.get(key)!=cooperative_binding.get(source) for key,source in
                    (('cooperative_session_id','run_id'),('coordinator_epoch','coordinator_epoch'),('object_id','object_id')))):
            raise ValueError('Exact actual native grant/source/cooperative binding required')
    def _current(self):
        if self.revoked.is_set():raise ValueError('Owned receiver transport was revoked')
        wrapper=self.request('GET','/station/observed-test/status',None);grant=wrapper.get('grant') or {}
        if (wrapper.get('execution_permission') is not True or grant.get('execution_permission') is not True or
                any(grant.get(key)!=self.grant.get(key) for key in ('grant_id','binding','control_epoch','provider_process',
                    'actor_id','boot_id','clock_domain','follower_calibration_sha256'))):
            raise ValueError('Exact current USER Pi grant/native epoch/source binding required')
        native=self.request('GET','/station/status',None)
        expected='policy' if self.permission.binding.get('input_mode','policy')=='policy' else 'teleoperation'
        if (native.get('available') is not True or native.get('configuration_writes')!=0 or
                native.get('control_epoch')!=self.grant['control_epoch'] or native.get('mode')!=expected or
                ((native.get('roles') or {}).get('follower') or {}).get('calibration_sha256')!=self.grant['follower_calibration_sha256']):
            raise ValueError('Current original native Pi owner/mode/calibration required')
        if self.revoked.is_set():raise ValueError('Owned receiver transport was revoked while reading source')
        return wrapper,native
    def observe_state(self):
        try:
            wrapper,native=self._current();ready=True;reason=None
            interlock=self._interlock_description(wrapper,native,wrapper.get('cooperative_hold_interlock'))
        except Exception as error:
            wrapper,native={},{};ready=False;reason=type(error).__name__+':'+str(error)
            interlock={'cooperative_dual_hold_publication_interlock_current':False,'source_interlock_current':False,'reason':reason}
        with self.interlock_lock:self.interlock_cache=deepcopy(interlock)
        return {'current_native_owner_ready':ready,'source_receipt':{'permission':wrapper,'native':native,'reason':reason},
            'clock_domain':self.domain,'received_ns':self.clock(),'attained_stationarity':None,
            'cooperative_dual_hold_publication_interlock_current':interlock['cooperative_dual_hold_publication_interlock_current'],
            'receiver_interlock':interlock,
            'native_user_observation_automatically_renewed':False,'native_hold_contact_or_success_inferred':False}
    def _session(self,row):
        if (not isinstance(row,dict) or row.get('session_id')!=self.binding['run_id'] or
                row.get('protocol_object_id')!=self.binding['object_id'] or row.get('epoch')!=self.binding['coordinator_epoch']):
            raise ValueError('Old cooperative source session cannot operate the receiver interlock')
    def _interlock_description(self,wrapper,native,source):
        source=source if isinstance(source,dict) else {};owner=source.get('native_owner') or {}
        expected={key:self.grant[key] for key in ('grant_id','provider_process','control_epoch','actor_id','boot_id','clock_domain','follower_calibration_sha256')}
        profile=source.get('direction_profile') or {};gate=wrapper.get('publication_gate') or {}
        installed=(wrapper.get('native_publication_permission_gate_installed') is True and
            gate.get('schema')=='explorer_station_publication_gate_v1' and gate.get('installed') is True and source.get('publication_gate_required') is True)
        known=(isinstance(profile,dict) and profile.get('follower_calibration_sha256')==self.grant['follower_calibration_sha256'] and
            profile.get('profile_sha256')==digest({key:value for key,value in profile.items() if key!='profile_sha256'}) and
            type(profile.get('closing_sign')) is int and profile['closing_sign'] in (-1,1))
        current=bool(installed and source.get('schema')=='explorer_cooperative_receiver_interlock_v1' and
            re.fullmatch('[0-9a-f]{32}',str(source.get('interlock_id'))) and type(source.get('revision')) is int and source['revision']>=0 and
            source.get('binding')==self.permission.binding and owner==expected and source.get('binding_current') is True and
            source.get('capability_ready') is True and source.get('direction_known') is True and known and
            source.get('all_target_writes_blocked') is False and source.get('persistence_fault') is None and
            native.get('control_epoch')==self.grant['control_epoch'] and not self.revoked.is_set())
        return {'source_interlock_current':current,'source_publication_gate_installed':installed,
            'source_direction_known':known and source.get('direction_known') is True,'source_armed':source.get('armed') is True,
            'source_binding_current':source.get('binding_current') is True,'source_status':deepcopy(source),
            'coordinator_both_guard_and_finish_hooks_connected':self.dual_hold_integration_ready,
            'cooperative_dual_hold_publication_interlock_current':current and self.dual_hold_integration_ready,
            'received_coordinator_ns':self.clock(),'received_clock_domain':self.domain,
            'reason':None if current and self.dual_hold_integration_ready else
                'COORDINATOR_BOTH_GUARD_AND_FINISH_HOOKS_NOT_CONNECTED' if current else 'EXACT_CURRENT_SOURCE_RECEIVER_INTERLOCK_REQUIRED',
            'metadata_is_not_contact_attainment_or_physical_success':True}
    def interlock_state(self,refresh=False):
        """Cached reads never claim current authority or perform an RPC."""
        if type(refresh) is not bool:raise ValueError('Explicit read-only refresh flag required')
        if refresh:
            wrapper,native=self._current();source=self.permission.cooperative('status')
            value=self._interlock_description(wrapper,native,source)
            with self.interlock_lock:self.interlock_cache=deepcopy(value)
            return value
        with self.interlock_lock:value=deepcopy(self.interlock_cache)
        value['source_current_at_last_fetch']=value.get('source_interlock_current') is True
        value.update(cached_snapshot_only=True,cooperative_dual_hold_publication_interlock_current=False,source_interlock_current=False)
        return value
    def _metadata(self,operation,row,extra):
        self._session(row);wrapper,native=self._current();source=self.permission.cooperative('status')
        before=self._interlock_description(wrapper,native,source)
        if not before['source_interlock_current']:raise ValueError('Exact current source-bound receiver interlock capability required')
        body={'grant_id':self.grant['grant_id'],'expected_control_epoch':self.grant['control_epoch'],
            'interlock_id':source['interlock_id'],'expected_revision':source['revision'],**deepcopy(extra)}
        response=self.permission.cooperative(operation,body,authenticated_actor=self.grant['actor_id'])
        wrapper,native=self._current();actual=wrapper.get('cooperative_hold_interlock') or {}
        description=self._interlock_description(wrapper,native,actual)
        if (not description['source_interlock_current'] or actual.get('interlock_id')!=source['interlock_id'] or
                actual.get('revision')!=source['revision']+1):
            raise ValueError('Receiver metadata return superseded or current native source permission lost')
        with self.interlock_lock:self.interlock_cache=deepcopy(description)
        return response,actual,description
    def arm_release_guard(self,row):
        with self.metadata_lock:
            result,actual,description=self._metadata('arm',row,{})
            frozen=actual.get('frozen') or {};original=frozen.get('original_last_command') or {};pose=original.get('dispatched')
            if (not isinstance(result,dict) or result.get('interlock_id')!=actual['interlock_id'] or result.get('revision')!=actual['revision'] or
                    result.get('armed') is not True or actual.get('armed') is not True or result.get('frozen')!=actual.get('frozen') or
                    not isinstance(pose,list) or len(pose)!=6 or any(type(value) not in (int,float) or not math.isfinite(value) or
                        not LOW[i]<=value<=HIGH[i] for i,value in enumerate(pose)) or frozen.get('native6')!=pose or
                    original.get('control_epoch')!=self.grant['control_epoch'] or original.get('clock_domain')!=self.grant['clock_domain'] or
                    type(original.get('sequence')) is not int or type(original.get('monotonic_ns')) is not int or
                    frozen.get('source_receipt_sha256')!=digest(original)):
                raise ValueError('Actual source receiver armed write reference required')
            return {'native_receiver_dual_hold_guard':deepcopy(result),'source_status':description,
                'hardware_command_sent':False,'attainment_or_object_hold_claimed':False}
    def finish_challenge(self,row,giver_release_receipt):
        with self.metadata_lock:
            result,actual,description=self._metadata('finish/challenge',row,{'giver_release_receipt':giver_release_receipt})
            if (not isinstance(result,dict) or result!=actual.get('pending_challenge') or actual.get('armed') is not True or
                    result.get('cooperative_binding')!=self.permission.binding or result.get('clock_domain')!=self.grant['clock_domain'] or
                    result.get('boot_id')!=self.grant['boot_id']):raise ValueError('Actual native receiver transfer challenge required')
            pending={'challenge':deepcopy(result),'received_coordinator_ns':self.clock(),'received_clock_domain':self.domain,
                'cross_clock_conversion_assumed':False,'metadata_does_not_unfreeze':True}
            with self.interlock_lock:self.pending_finish=deepcopy(pending)
            return pending
    def finish_interlock(self,row,challenge_id,confirmation):
        with self.metadata_lock:
            self._session(row)
            with self.interlock_lock:pending=deepcopy(self.pending_finish)
            proof=(confirmation or {}).get('confirmation') if isinstance(confirmation,dict) else None
            challenge=(pending or {}).get('challenge') or {};now=self.clock()
            if (not pending or challenge.get('challenge_id')!=challenge_id or not isinstance(proof,dict) or
                    confirmation.get('kind')!='authenticated_operator_confirmation' or proof.get('clock_domain')!=self.domain or
                    type(proof.get('created_ns')) is not int or type(proof.get('received_ns')) is not int or
                    not pending['received_coordinator_ns']<=proof['created_ns']<=proof['received_ns']<=now or
                    proof.get('state')!='consumed' or proof.get('statement')!='object_transferred_to_receiver' or
                    proof.get('role')!='receiver' or proof.get('run_id')!=self.binding['run_id'] or proof.get('object_id')!=self.binding['object_id'] or
                    proof.get('after_command_id')!=challenge.get('giver_release_receipt_id') or proof.get('affirmed') is not True or
                    proof.get('observing') is not True or proof.get('authenticated_actor')!=self.grant['actor_id']):
                raise ValueError('Consumed independent NX confirmation created after this exact native receiver challenge required')
            wrapped={'operator_confirmation':deepcopy(proof),'coordinator_verified_confirmation_created_after_challenge':True,
                'coordinator_clock_domain':self.domain,'coordinator_challenge_received_ns':pending['received_coordinator_ns'],
                'coordinator_confirmation_checked_ns':now}
            result,actual,description=self._metadata('finish',row,{'challenge_id':challenge_id,'confirmation':wrapped})
            if (not isinstance(result,dict) or result.get('interlock_id')!=actual['interlock_id'] or result.get('revision')!=actual['revision'] or
                    result.get('armed') is not False or actual.get('armed') is not False or actual.get('phase')!='UNFROZEN_TO_FINISH'):
                raise ValueError('Actual independent-proof receiver unfreeze receipt required')
            with self.interlock_lock:self.pending_finish=None
            return {'native_receiver_interlock_finish':deepcopy(result),'source_status':description,
                'independent_confirmation':wrapped,'hardware_command_sent':False,'full_task_success_claimed':False}
    def submit_observed(self,command,authorization=None):
        if (command.get('role')!='receiver' or any(command.get(key)!=self.binding.get(key) for key in
                ('run_id','object_id','coordinator_epoch')) or command.get('reference')!=self.reference):
            raise ValueError('Exact source-bound cooperative receiver command required')
        if not callable(self.predict) or self.permission.binding.get('input_mode','policy')!='policy':
            raise ValueError('Manual receiver has no hidden policy command')
        with self.lock:
            self._current();predicted=self.predict(deepcopy(command));_,native=self._current()
            model={key:self.reference.get(key) for key in ('model_id','checkpoint_sha256','spec_sha256')}
            action=predicted.get('predicted_native6');source=predicted.get('source_observation') or {}
            if (predicted.get('action_contract')!=ROLE_CONTRACTS['receiver'] or predicted.get('model')!=model or
                    predicted.get('dispatch_performed') is not False or predicted.get('physical_execution_authorized') is not False or
                    not predicted.get('source_evidence') or not isinstance(action,list) or len(action)!=6 or any(
                        type(value) not in (int,float) or not math.isfinite(value) or not LOW[i]<=value<=HIGH[i] for i,value in enumerate(action))):
                raise ValueError('Actual pinned finite native6 isolated model prediction required')
            image=((predicted.get('source_evidence') or {}).get('original_artifacts') or {}).get('frame.jpg') or {}
            if (source.get('camera_id')!=self.grant['displayed_frame']['camera_id'] or
                    not re.fullmatch('[0-9a-f]{64}',str(source.get('frame_sha256'))) or image.get('sha256')!=source.get('frame_sha256') or
                    source.get('follower_calibration_sha256')!=self.grant['follower_calibration_sha256'] or
                    source.get('state_basis')!='measured_state' or not source.get('frame_id') or
                    any(source.get(key)!=self.grant['boot_id'] for key in ('frame_boot_id','state_boot_id','source_cutoff_boot_id')) or
                    any(source.get(key)!=self.grant['clock_domain'] for key in ('frame_clock_domain','state_clock_domain','source_cutoff_clock_domain'))):
                raise ValueError('Actual same-boot Pi cached RGB/measured6 source required')
            stamps=[source.get(key) for key in ('frame_monotonic_ns','state_monotonic_ns','source_cutoff_monotonic_ns')]
            if any(type(value) is not int for value in stamps) or not 0<=stamps[0]<=stamps[1]<=stamps[2]:
                raise ValueError('Original causal native Pi source clocks required')
            previous=(native.get('last_command') or {}).get('sequence',-1)
            if type(previous) is not int:raise ValueError('Original native sequence receipt required')
            sequence=max(previous,self.sequence)+1;self.sequence=sequence
            body={'control_epoch':self.grant['control_epoch'],'sequence':sequence,'action':deepcopy(action),
                'action_names':deepcopy(ROLE_CONTRACTS['receiver']['names']),'action_units':deepcopy(ROLE_CONTRACTS['receiver']['units']),
                'source_frame_id':source['frame_id'],'source_frame_monotonic_ns':stamps[0],
                'source_clock_domain':self.grant['clock_domain']}
            accepted=self.permission.command(self.grant['grant_id'],self.permission.binding,'action',body)
            if (accepted.get('native_command_called') is not True or accepted.get('dispatch_claimed_from_HTTP_acceptance') is not False or
                    (accepted.get('grant') or {}).get('control_epoch')!=self.grant['control_epoch']):
                raise ValueError('Original atomic native command result is not correlated')
            deadline=self.clock()+int(self.timeout*1e9)
            while self.clock()<deadline:
                _,state=self._current();actual=state.get('last_command') or {}
                if actual.get('control_epoch')==self.grant['control_epoch'] and actual.get('sequence')==sequence:
                    tick=actual.get('monotonic_ns');observed=actual.get('source_observation') or {}
                    if (actual.get('requested')!=action or actual.get('dispatched')!=action or type(tick) is not int or tick<stamps[2] or
                            actual.get('clock_domain')!=self.grant['clock_domain'] or observed.get('frame_id')!=source['frame_id'] or
                            observed.get('monotonic_ns')!=stamps[0] or observed.get('clock_domain')!=self.grant['clock_domain']):
                        raise ValueError('Actual original Pi dispatch/frame/action/clock differs')
                    return DispatchReceipt(command['command_id'],command['run_id'],command['object_id'],'receiver',True,
                        self.clock(),self.domain,{'basis':'actual_original_pi_native_position_write','native_receipt':deepcopy(actual),
                            'api_accepted':deepcopy(accepted),'source_model_prediction':deepcopy(predicted),
                            'native_control_epoch':self.grant['control_epoch'],'native_sequence':sequence,
                            'MCU_attainment_verified':False,'grasp_contact_or_success_verified':False})
                if actual.get('control_epoch')==self.grant['control_epoch'] and actual.get('sequence',-1)>sequence:
                    raise ValueError('Another native Pi command superseded this prediction')
                self.sleep(.005)
            raise TimeoutError('Pi API returned but no exact original native position-write receipt')
    def stop(self,run_id):
        if run_id!=self.binding['run_id']:raise ValueError('Receiver STOP belongs to another cooperative run')
        self.revoked.set();grant=self.grant
        try:revoked=self.permission.revoke('COOPERATIVE_SCOPED_STOP',expected_grant_id=grant['grant_id'],expected_control_epoch=grant['control_epoch'])
        except Exception as error:revoked={'metadata_revoke_return_unknown':type(error).__name__+':'+str(error)}
        stopped=self.permission.command(grant['grant_id'],self.permission.binding,'stop',{'expected_control_epoch':grant['control_epoch']})
        return {'metadata_revoke':revoked,'scoped_native_stop':stopped,'attainment_or_object_safety_inferred':False}
