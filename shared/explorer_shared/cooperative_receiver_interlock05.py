"""ENGINEERING_GLUE: pure metadata receiver dual-hold publication interlock.

Caller owns Station/publication/provider locks and actual permission validation.
No driver, camera, file, network, worker, motor setting or automatic release.
Original measured-position STOP holds are handled separately by the existing
Station; this helper governs only later target writes, including unowned epochs.
"""
from collections import deque
from copy import deepcopy
import hashlib
import json
import math
import re
import threading
import time
import uuid

from .contracts import digest
from .handover import HandoverFSM
from .so101_station import LOW,HIGH

CONTRACT='so101_follower_calibrated_position6_v1'
STATE_SCHEMA='explorer_cooperative_receiver_interlock_state_v1'
PROFILE_SCHEMA='explorer_receiver_observed_gripper_direction_v1'


def _text(value):return isinstance(value,str) and bool(re.fullmatch('[A-Za-z0-9_.:-]{1,160}',value))
def _sha(value):return isinstance(value,str) and bool(re.fullmatch('[0-9a-f]{64}',value))
def _six(value):
    if (not isinstance(value,list) or len(value)!=6 or any(type(item) not in (int,float) or not math.isfinite(item) or
            not LOW[i]<=item<=HIGH[i] for i,item in enumerate(value))):raise ValueError('Six finite original normalized native coordinates required; no clipping')
    return deepcopy(value)


def observed_gripper_direction(open_original_bytes,closed_original_bytes):
    """Validate source records outside the write hook; retain exact byte SHAs.

The trusted provider must produce these from actual cached measurements/images
and authenticated USER labels, never from browser-supplied numeric endpoints.
"""
    records=[];hashes=[]
    for raw,label in ((open_original_bytes,'empty_open'),(closed_original_bytes,'empty_closed')):
        if not isinstance(raw,bytes) or not 0<len(raw)<=64*1024:raise ValueError('Bounded original endpoint source bytes required')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('Typed original endpoint observation required')
        position=_six(value.get('measured_native6'))
        stamp=value.get('measurement_stamp') or {};frame=value.get('frame') or {};operator=value.get('operator_confirmation') or {}
        times=(frame.get('monotonic_ns'),stamp.get('monotonic_ns'))
        if (value.get('schema')!='explorer_so101_gripper_endpoint_observation_v1' or value.get('role')!='follower' or
                value.get('state_source')!='measured_state' or value.get('verified_on_connected_hardware') is not True or
                not _sha(value.get('follower_calibration_sha256')) or not _sha(frame.get('jpeg_sha256')) or not _text(frame.get('frame_id')) or
                not _text(stamp.get('clock_domain')) or not _text(stamp.get('boot_id')) or
                frame.get('clock_domain')!=stamp['clock_domain'] or frame.get('boot_id')!=stamp['boot_id'] or
                any(type(item) is not int or item<0 for item in times) or times[0]>times[1] or
                operator.get('label')!=label or operator.get('observing') is not True or
                operator.get('user_observed_test_confirmed') is not True or operator.get('empty_gripper_confirmed') is not True or
                not _text(operator.get('actor_id'))):raise ValueError('Actual source-bound measured empty endpoint and explicit USER frame label required')
        uncertainty=value.get('measurement_uncertainty_native')
        if type(uncertainty) not in (int,float) or not math.isfinite(uncertainty) or uncertainty<0:
            raise ValueError('Source-native endpoint measurement uncertainty required')
        records.append((value,position[5],uncertainty));hashes.append(hashlib.sha256(raw).hexdigest())
    opened,closed=records
    if (opened[0]['follower_calibration_sha256']!=closed[0]['follower_calibration_sha256'] or
            opened[0]['frame']['jpeg_sha256']==closed[0]['frame']['jpeg_sha256'] or hashes[0]==hashes[1] or
            abs(closed[1]-opened[1])<=opened[2]+closed[2]):raise ValueError('Distinct unambiguous observed empty endpoints from the same calibration required')
    profile={'schema':PROFILE_SCHEMA,'action_contract_id':CONTRACT,'follower_calibration_sha256':opened[0]['follower_calibration_sha256'],
        'observed_open_native':opened[1],'observed_empty_closed_native':closed[1],'closing_sign':1 if closed[1]>opened[1] else -1,
        'measurement_uncertainty_native':max(opened[2],closed[2]),'endpoint_uncertainty_native':{'open':opened[2],'empty_closed':closed[2]},
        'original_source_sha256':{'open':hashes[0],'empty_closed':hashes[1]},
        'basis':'measured_native_endpoints_with_explicit_USER_empty_frame_labels','metric_aperture_or_force_claimed':False,
        'empty_closed_is_not_object_hold_or_squeeze_target':True}
    return dict(profile,profile_sha256=digest(profile))


class ReceiverHoldInterlock:
    def __init__(self,*,clock_domain,boot_id,clock=time.monotonic_ns,state=None):
        if not _text(clock_domain) or not _text(boot_id):raise ValueError('Actual local native clock/boot identity required')
        self.domain,self.boot,self.clock=clock_domain,boot_id,clock;self.lock=threading.RLock()
        self.revision=0;self.interlock_id=None;self.binding=None;self.owner=None;self.direction=None
        self.armed=False;self.phase='UNBOUND';self.frozen=None;self.pending=None;self.used=set();self.events=deque(maxlen=64)
        if state is not None:self._restore(state)
    def _event(self,kind,**values):
        self.events.append({'event_id':uuid.uuid4().hex,'kind':kind,'revision':self.revision,
            'native_monotonic_ns':self.clock(),'clock_domain':self.domain,'boot_id':self.boot,**deepcopy(values),'native_commands_sent':0})
    def _permission(self,permission):
        value=permission if isinstance(permission,dict) else {};binding=value.get('binding') or {}
        if not isinstance(binding,dict):raise ValueError('Typed original cooperative grant binding required')
        if (value.get('execution_permission') is not True or value.get('boot_id')!=self.boot or value.get('clock_domain')!=self.domain or
                not _text(value.get('grant_id')) or not _text(value.get('provider_process')) or not _text(value.get('actor_id')) or
                not _text(value.get('control_epoch')) or not _sha(value.get('follower_calibration_sha256')) or
                type(value.get('observation_deadline_ns')) is not int or self.clock()>=value['observation_deadline_ns'] or
                value.get('native_mode') not in ('idle','policy','teleoperation') or binding.get('purpose')!='cooperative05' or
                binding.get('action_contract_id')!=CONTRACT or binding.get('source_domain')!='PHYSICAL' or
                any(not _text(binding.get(key)) for key in ('cooperative_session_id','coordinator_epoch','object_id'))):
            raise ValueError('Actual provider-validated current cooperative USER native permission required')
        return value
    @staticmethod
    def _owner(permission):
        return {key:deepcopy(permission[key]) for key in ('grant_id','provider_process','control_epoch','actor_id','boot_id',
            'clock_domain','follower_calibration_sha256')}
    def _current(self,permission):
        try:value=self._permission(permission)
        except ValueError:return False
        return self.binding==value['binding'] and self.owner==self._owner(value)
    def _cas(self,interlock_id,expected_revision):
        if interlock_id!=self.interlock_id or type(expected_revision) is not int or expected_revision!=self.revision:
            raise ValueError('Exact current receiver interlock ID/revision required')
    def _profile(self,profile,calibration):
        if profile is None:return None
        if (not isinstance(profile,dict) or profile.get('schema')!=PROFILE_SCHEMA or profile.get('action_contract_id')!=CONTRACT or
                profile.get('follower_calibration_sha256')!=calibration or profile.get('profile_sha256')!=digest({key:value for key,value in profile.items() if key!='profile_sha256'}) or
                type(profile.get('closing_sign')) is not int or profile['closing_sign'] not in (-1,1) or
                profile.get('basis')!='measured_native_endpoints_with_explicit_USER_empty_frame_labels' or
                profile.get('metric_aperture_or_force_claimed') is not False or profile.get('empty_closed_is_not_object_hold_or_squeeze_target') is not True or
                set(profile.get('original_source_sha256') or {})!={'open','empty_closed'} or
                not all(_sha(value) for value in profile['original_source_sha256'].values())):
            raise ValueError('Original observed gripper-direction profile for this follower calibration required')
        opened,closed=profile.get('observed_open_native'),profile.get('observed_empty_closed_native')
        if any(type(value) not in (int,float) or not math.isfinite(value) or not LOW[5]<=value<=HIGH[5] for value in (opened,closed)) or (closed-opened)*profile['closing_sign']<=0:
            raise ValueError('Gripper direction cannot be guessed from normalization or command names')
        uncertainty=profile.get('endpoint_uncertainty_native') or {}
        if (set(uncertainty)!={'open','empty_closed'} or any(type(value) not in (int,float) or not math.isfinite(value) or value<0 for value in uncertainty.values()) or
                abs(closed-opened)<=sum(uncertainty.values()) or len(set(profile['original_source_sha256'].values()))!=2):
            raise ValueError('Unambiguous distinct observed endpoint originals required')
        return deepcopy(profile)
    def bind(self,*,binding,permission,direction_profile=None,expected_revision):
        current=self._permission(permission);direction=self._profile(direction_profile,current['follower_calibration_sha256'])
        with self.lock:
            if type(expected_revision) is not int or expected_revision!=self.revision:raise ValueError('Current interlock revision required')
            if self.armed:raise ValueError('Armed receiver cannot be rebound or implicitly unfrozen')
            if binding!=current['binding']:raise ValueError('Exact provider-owned cooperative binding required')
            self.revision+=1;self.interlock_id=uuid.uuid4().hex;self.binding=deepcopy(binding);self.owner=self._owner(current)
            self.direction=direction;self.phase='BOUND';self.frozen=self.pending=None
            self._event('BOUND',direction_known=direction is not None)
            return self.status(permission=current)
    def _returned(self,last_command):
        if not isinstance(last_command,dict):raise ValueError('Actual original returned native position write required')
        active=(self.frozen or {}).get('active_phase') or {};origin=((self.frozen or {}).get('held_successor') or {}).get('origin') or {}
        if (active.get('schema')=='explorer_station_cooperative_phase_activation_v1' and active.get('new_owner')==self.owner and
                active.get('origin_sha256')==origin.get('origin_sha256') and active.get('native_baseline_exactly_matches_returned_hold') is True and
                last_command==active.get('preceding_actual_station_last_command') and digest(last_command)==active.get('preceding_actual_station_last_command_sha256')):
            # This is the actual earlier hold plus a source-atomic start link,
            # never a fabricated new-epoch command/sequence/publication receipt.
            return _six(origin.get('held_native6'))
        dispatched=_six(last_command.get('dispatched'));stamp=last_command.get('monotonic_ns')
        if (not self.owner or last_command.get('control_epoch')!=self.owner['control_epoch'] or last_command.get('clock_domain')!=self.domain or
                type(stamp) is not int or not 0<=stamp<=self.clock() or type(last_command.get('sequence')) is not int or
                not isinstance(last_command.get('requested'),list) or len(last_command['requested'])!=6):
            raise ValueError('Actual original returned write epoch/clock/sequence required; API ACK is insufficient')
        return dispatched
    def arm(self,*,interlock_id,expected_revision,permission,last_command):
        with self.lock:
            self._cas(interlock_id,expected_revision)
            if not self._current(permission):raise ValueError('Current exact cooperative native permission required before arm')
            if self.armed:raise ValueError('Receiver interlock already armed; no silent reference replacement')
            pose=self._returned(last_command);self.revision+=1;self.armed=True;self.phase='ARMED'
            self.frozen={'native6':pose,'original_last_command':deepcopy(last_command),'source_receipt_sha256':digest(last_command)}
            self.pending=None;self._event('ARMED_FROM_ACTUAL_RETURNED_WRITE',native6=pose)
            return self.status(permission=permission)
    def check(self,*,permission,control_epoch,mode,target,last_command):
        with self.lock:
            current=self._current(permission);reason=None
            if not self.armed:return {'allowed':True,'binding_current':current,'armed':False,'reason':None,'interlock_id':self.interlock_id,'revision':self.revision}
            if not current or control_epoch!=self.owner['control_epoch'] or mode!=permission.get('native_mode'):
                reason='ARMED_RECEIVER_DENIES_UNMATCHED_OR_UNOWNED_NATIVE_EPOCH'
            else:
                try:
                    wanted=_six(target);previous=self._returned(last_command);frozen=self.frozen['native6']
                    if wanted[:5]!=frozen[:5] or previous[:5]!=frozen[:5]:reason='DUAL_HOLD_REQUIRES_EXACT_FROZEN_RECEIVER_ARM5'
                    elif self.direction is None:
                        if wanted!=frozen or previous!=frozen:reason='GRIPPER_DIRECTION_UNREGISTERED_FREEZE_ALL6'
                    elif (wanted[5]-previous[5])*self.direction['closing_sign']<0:reason='RECEIVER_OPENING_FORBIDDEN_BEFORE_INDEPENDENT_TRANSFER'
                except ValueError as error:reason=str(error)
            if reason:self._event('TARGET_DENIED',reason=reason)
            return {'allowed':reason is None,'binding_current':current,'armed':True,'reason':reason,
                'interlock_id':self.interlock_id,'revision':self.revision,'write_or_attainment_claimed':False}
    def _giver_release(self,receipt):
        value=receipt if isinstance(receipt,dict) else {};bound=value.get('binding') or {};grant=value.get('guard_grant') or {}
        challenge=grant.get('challenge') or {};times=(value.get('host_publication_started_native_ns'),value.get('host_publication_returned_native_ns'))
        if (value.get('schema')!='explorer_native_cooperative_publication_v1' or value.get('opening') is not True or
                not _text(value.get('receipt_id')) or not _text(value.get('native_clock_domain')) or not _text(value.get('native_boot_id')) or
                any(type(tick) is not int or tick<0 for tick in times) or times[0]>times[1] or not _text(grant.get('grant_id')) or
                any(bound.get(key)!=self.binding[source] or challenge.get(key)!=self.binding[source] for key,source in
                    (('run_id','cooperative_session_id'),('object_id','object_id'),('coordinator_epoch','coordinator_epoch')))):
            raise ValueError('Actual correlated giver opening host-publication receipt required, not a target or API ACK')
        return deepcopy(value)
    def finish_challenge(self,*,interlock_id,expected_revision,permission,giver_release_receipt):
        with self.lock:
            self._cas(interlock_id,expected_revision)
            if not self.armed or not self._current(permission):raise ValueError('Current armed receiver/native grant required')
            giver=self._giver_release(giver_release_receipt);self.revision+=1;now=self.clock()
            self.pending={'challenge_id':uuid.uuid4().hex,'interlock_id':self.interlock_id,'revision':self.revision,
                'issued_native_ns':now,'expires_native_ns':now+HandoverFSM('receiver-interlock-metadata').hold_ttl_ns,
                'clock_domain':self.domain,'boot_id':self.boot,'giver_release_receipt_id':giver['receipt_id'],
                'giver_release_receipt_sha256':digest(giver),'cooperative_binding':deepcopy(self.binding)}
            self._event('INDEPENDENT_TRANSFER_CHALLENGE',giver_release_receipt_id=giver['receipt_id'])
            return deepcopy(self.pending)
    def finish(self,*,interlock_id,expected_revision,permission,challenge_id,confirmation):
        if not isinstance(confirmation,dict):raise ValueError('Exact authenticated independent confirmation wrapper required')
        with self.lock:
            self._cas(interlock_id,expected_revision);pending=self.pending;proof=(confirmation or {}).get('operator_confirmation') or {}
            received=(confirmation or {}).get('coordinator_challenge_received_ns');domain=(confirmation or {}).get('coordinator_clock_domain')
            times=(received,proof.get('created_ns'),proof.get('received_ns'),(confirmation or {}).get('coordinator_confirmation_checked_ns'))
            if (not self.armed or not self._current(permission) or not pending or pending['challenge_id']!=challenge_id or
                    pending['revision']!=self.revision or not pending['issued_native_ns']<=self.clock()<pending['expires_native_ns'] or
                    confirmation.get('coordinator_verified_confirmation_created_after_challenge') is not True or
                    not _text(domain) or domain==self.domain or proof.get('clock_domain')!=domain or any(type(tick) is not int or tick<0 for tick in times) or
                    not times[0]<=times[1]<=times[2]<=times[3] or proof.get('statement')!='object_transferred_to_receiver' or
                    proof.get('role')!='receiver' or proof.get('run_id')!=self.binding['cooperative_session_id'] or
                    proof.get('object_id')!=self.binding['object_id'] or proof.get('after_command_id')!=pending['giver_release_receipt_id'] or
                    proof.get('state')!='consumed' or proof.get('affirmed') is not True or proof.get('observing') is not True or
                    proof.get('authenticated_actor')!=permission.get('actor_id') or not _text(proof.get('challenge_id'))):
                raise ValueError('Fresh exact independent transfer proof after this native challenge required; clocks never equated')
            if proof['challenge_id'] in self.used:raise ValueError('Independent transfer proof cannot be reused')
            if len(self.used)>=2048:raise ValueError('Transfer evidence retention bound reached')
            self.used.add(proof['challenge_id']);self.revision+=1;self.armed=False;self.phase='UNFROZEN_TO_FINISH'
            self.pending=None;self._event('INDEPENDENT_TRANSFER_CONFIRMED',proof_id=proof['challenge_id'],native_opening_command_sent=False)
            return self.status(permission=permission)
    def reset_safe(self,*,interlock_id,expected_revision,permission,object_safe_no_dual_hold,actor_id,native_mode):
        current=self._permission(permission)
        with self.lock:
            self._cas(interlock_id,expected_revision)
            if (object_safe_no_dual_hold is not True or native_mode!='idle' or current['native_mode']!='idle' or
                    actor_id!=current['actor_id'] or self.owner and current['follower_calibration_sha256']!=self.owner['follower_calibration_sha256']):
                raise ValueError('Fresh current USER safe-object confirmation under original idle for this follower required')
            self.revision+=1;self._event('EXPLICIT_SAFE_OBJECT_RESET',actor_id=actor_id,previous_binding=self.binding)
            self.interlock_id=None;self.binding=self.owner=self.direction=self.frozen=self.pending=None
            self.armed=False;self.phase='UNBOUND'
            return self.status(permission=current)
    def status(self,permission=None):
        with self.lock:
            current=self._current(permission)
            return {'schema':'explorer_cooperative_receiver_interlock_v1','interlock_id':self.interlock_id,'revision':self.revision,
                'phase':self.phase,'binding':deepcopy(self.binding),'native_owner':deepcopy(self.owner),'binding_current':current,
                'direction_known':self.direction is not None,'direction_profile':deepcopy(self.direction),
                'capability_ready':current and self.direction is not None,'armed':self.armed,'frozen':deepcopy(self.frozen),
                'pending_challenge':deepcopy(self.pending),'last_events':deepcopy(list(self.events)),
                'native_commands_sent':0,'timer_unfreezes':False,'metadata_is_not_execution_authority':True,
                'hold_contact_attainment_or_physical_success_claimed':False}
    def snapshot_state(self):
        with self.lock:
            value={'schema':STATE_SCHEMA,'revision':self.revision,'interlock_id':self.interlock_id,'binding':deepcopy(self.binding),
                'owner':deepcopy(self.owner),'direction':deepcopy(self.direction),'armed':self.armed,'phase':self.phase,
                'frozen':deepcopy(self.frozen),'pending':deepcopy(self.pending),'used_confirmations':sorted(self.used),'events':deepcopy(list(self.events))}
            return dict(value,state_sha256=digest(value))
    def _restore(self,state):
        if (not isinstance(state,dict) or state.get('schema')!=STATE_SCHEMA or
                state.get('state_sha256')!=digest({key:value for key,value in state.items() if key!='state_sha256'}) or
                type(state.get('revision')) is not int or state['revision']<0 or type(state.get('armed')) is not bool or
                not isinstance(state.get('used_confirmations'),list) or len(state['used_confirmations'])>2048 or
                any(not _text(value) for value in state['used_confirmations']) or len(set(state['used_confirmations']))!=len(state['used_confirmations']) or
                state.get('phase') not in ('UNBOUND','BOUND','ARMED','UNFROZEN_TO_FINISH') or
                (state.get('phase')=='ARMED')!=state['armed'] or
                not isinstance(state.get('events'),list) or len(state['events'])>64):raise ValueError('Exact retained receiver interlock state required; never silently discard an armed latch')
        if state['armed'] and (not state.get('interlock_id') or not state.get('owner') or not state.get('binding') or not state.get('frozen')):
            raise ValueError('Armed retained interlock requires its original owner/reference')
        if state.get('frozen') is not None:
            frozen=state['frozen'];original=frozen.get('original_last_command') or {}
            if (_six(frozen.get('native6'))!=original.get('dispatched') or frozen.get('source_receipt_sha256')!=digest(original)):
                raise ValueError('Retained frozen pose must preserve actual original returned receipt')
        for name in ('revision','interlock_id','binding','owner','direction','armed','phase','frozen','pending'):setattr(self,name,deepcopy(state[name]))
        self.used=set(state['used_confirmations']);self.events=deque(deepcopy(state['events']),maxlen=64)
