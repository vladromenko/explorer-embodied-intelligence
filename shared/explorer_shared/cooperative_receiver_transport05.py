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
from .station_observed_http import StationObservedHttp
from .so101_station import LOW,HIGH
from .handover_runtime import DispatchReceipt


class NativeReceiverTransport:
    def __init__(self,request,cooperative_binding,permission_binding,grant,reference,action_provider=None,*,
                 clock_domain,clock=time.monotonic_ns,sleep=time.sleep,timeout_s=.5):
        if not callable(request):raise ValueError('Trusted authenticated original Pi request required')
        self.request,self.binding=request,deepcopy(cooperative_binding)
        self.permission=StationObservedHttp(request,permission_binding);self.grant=deepcopy(grant);self.reference=deepcopy(reference)
        self.predict=action_provider;self.domain,self.clock,self.sleep,self.timeout=clock_domain,clock,sleep,timeout_s
        self.revoked=threading.Event();self.lock=threading.Lock();self.sequence=-1
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
        except Exception as error:wrapper,native={},{};ready=False;reason=type(error).__name__+':'+str(error)
        return {'current_native_owner_ready':ready,'source_receipt':{'permission':wrapper,'native':native,'reason':reason},
            'clock_domain':self.domain,'received_ns':self.clock(),'attained_stationarity':None,
            'native_user_observation_automatically_renewed':False,'native_hold_contact_or_success_inferred':False}
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
