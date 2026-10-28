"""ENGINEERING_GLUE: existing measured Pi cache -> native latest Ruckig intent.

Independent coalesced source loop, no finite plan/start and no second servo bus.
Only explicit operator heartbeat renews the ORIGINAL native manual lease.
Recording is proxied to the SAME Nano producer used by02; old raw archives stay.
"""
from copy import deepcopy
from collections import deque
import json
from pathlib import Path
import threading
import time
import uuid

import numpy as np

from .native_stream03 import BASE_ACTIONS
from .native_stream03_client import NativeStreamClient
from .native_leader_geometry import registered_native_leader_geometry
from .retarget_recording import identity,digest
from .native_gripper_alignment import gripper_alignment

HOLD_SCHEMA='explorer_pi_native_explicit_hold_v1'
OPERATOR_PROFILES=('ARM_ONLY','MOBILE_MANIPULATION')
INITIAL_ALIGNMENT_MODES=('CLUTCH_CURRENT','LEADER_POSE')
ALIGNMENT_TOLERANCE_DEG=1.


class SourcePacketDrop(ValueError):
    """A single unusable sample is discarded, not a lifecycle transition."""

class LegacyRawView:
    """Read-only old Pi archives only; new03 target IDs are never duplicated here."""
    def __init__(self,root):
        self.root=Path(root);self.last_status={'active':None,'owner':'nano_native_canonical_producer'}
    def recordings(self):
        return [self.manifest(path.parent.name) for path in sorted(self.root.glob('*/manifest.json'))]
    def manifest(self,ident):
        folder=self.root/identity(ident);result=json.loads((folder/'manifest.json').read_bytes())
        info=folder/'archive-info.json'
        return {**result,**(json.loads(info.read_bytes()) if info.is_file() else {})}
    def archive(self,ident):
        result=self.manifest(ident);path=self.root/identity(ident)/'recording.tar'
        if not path.is_file() or digest(path)!=result.get('archive_sha256'):
            raise ValueError('Original raw Pi archive is missing or changed')
        return path
    def status(self):
        return deepcopy(self.last_status)
    def close(self):
        pass


class PiNativeRetargetService:
    def __init__(self,geometry,station,state_dir,client=None,*,clock=time.monotonic_ns,allow_synthetic=False):
        self.geometry,self.station,self.client=geometry,station,client
        self.clock,self.allow_synthetic=clock,allow_synthetic
        self.lock=threading.RLock();self.wake=threading.Event();self.shutdown=threading.Event()
        self.operation_lock=threading.Lock();self.pending_stops=0
        self.mode,self.epoch,self.target_epoch='IDLE',uuid.uuid4().hex,None
        self.lifecycle_inflight=False;self.step_inflight=False;self.stop_uncertain=False
        self.error=None;self.last_result=None;self.last_lease_sequence=-1
        self.source_sequence=0;self.last_source_stamp=None;self.sample_count=0
        self.native_period=None;self.target_boot=None;self.estimate=None;self.target_camera=None
        self.chassis_authorized=False;self.chassis_requests=0
        self.operator_profile='ARM_ONLY';self.authorization_id=None;self.target_generation=None
        self.source_identity=None
        self.begin_outcome_unknown=False
        self.gripper_alignment=None;self.packet_drops={};self.last_drop_reason=None
        self.initial_alignment_mode='CLUTCH_CURRENT';self.initial_alignment_phase='NOT_REQUESTED'
        self.initial_alignment_pending=False;self.initial_alignment=None
        self.last_lease_receipt=None;self.last_proposal=None;self.last_stop_receipt=None
        self.operator_hold_deadline_ns=None;self.held_base_actions={};self.last_native_heartbeat_ns=None
        self.round_trips=deque(maxlen=64);self.last_source_age_ns=None;self.discarded_late_replies=0
        self.recorder=LegacyRawView(Path(state_dir)/'retarget-recordings');self.thread=None;self.calibration_wizard=None

    @classmethod
    def from_config(cls,station,state_dir,config_dir):
        calibration=(getattr(station,'configuration',{}).get('roles',{}).get('leader',{}).get('calibration'))
        geometry=registered_native_leader_geometry(config_dir,source_calibration_path=calibration);client=None;error=None
        path=Path(config_dir)/'arm-retarget-peer.json'
        if path.is_file():
            try:
                if path.stat().st_size>16384:
                    raise ValueError('Private peer metadata exceeds source bound')
                settings=json.loads(path.read_bytes())
                client=NativeStreamClient(settings['nano_url'],settings['nano_node_token'])
            except (OSError,ValueError,TypeError,KeyError) as exc:
                error='Private native peer configuration invalid: '+type(exc).__name__
        service=cls(geometry,station,state_dir,client);service.error=error
        context=getattr(geometry,'context',None)
        if context is not None:
            from .native_leader_registration import NativeLeaderRegistrationWizard
            service.calibration_wizard=NativeLeaderRegistrationWizard(context,config_dir)
        return service

    def start_worker(self):
        if self.thread is None:
            self.thread=threading.Thread(target=self._work,name='pi-native-latest-leader',daemon=True)
            self.thread.start()

    def status(self):
        registration=self.geometry.status()
        station=self.station.status() if self.station is not None else {}
        role=station.get('roles',{}).get('leader',{})
        with self.lock:
            return {'schema':'explorer_pi_native_retarget_v1',
                'state':'UNREGISTERED' if registration['state']=='UNREGISTERED' else self.mode,
                'control_epoch':self.epoch,'target_control_epoch':self.target_epoch,'registration':registration,
                'source_hold_gate_schema':HOLD_SCHEMA,'operator_profile':self.operator_profile,'operator_profiles':list(OPERATOR_PROFILES),
                'first_held_requires_neutral_base':True,'mirror_dispatch_requires_explicit_held':True,
                'source_cache_claimed':bool(self.target_epoch is not None or self.lifecycle_inflight or self.step_inflight or
                    self.pending_stops or self.stop_uncertain or self.begin_outcome_unknown),
                'peer_configured':self.client is not None,'lifecycle_inflight':self.lifecycle_inflight,
                'step_inflight':self.step_inflight,'stop_delivery_uncertain':self.stop_uncertain,
                'closed':self.shutdown.is_set(),
                'inflight':self.lifecycle_inflight or self.step_inflight,'pending_stops':self.pending_stops,
                'source':'existing_SO101Station_measured_cache','target':'ROSMASTER_UNIFIED_9D_NATIVE_MUX',
                'target_camera':'nano-rgbd','native_backend':'factory_micro_ros_ruckig_velocity',
                'legacy_finite_queue_used':False,'mirror_requires_recording':False,
                'source_station_mode':station.get('mode'),'source_calibration_sha256':role.get('calibration_sha256'),
                'source_calibrated':bool(role.get('calibration_sha256')),
                'missing_registration_is_cross_map_not_device_recalibration':True,
                'cached_samples_consumed':self.sample_count,'last_result':deepcopy(self.last_result),'error':self.error,
                'last_proposal':deepcopy(self.last_proposal),'last_lease_receipt':deepcopy(self.last_lease_receipt),
                'last_stop_receipt':deepcopy(self.last_stop_receipt),
                'gripper_alignment':deepcopy(self.gripper_alignment),
                'initial_alignment_mode':self.initial_alignment_mode,
                'initial_alignment_modes':list(INITIAL_ALIGNMENT_MODES),
                'initial_alignment_phase':self.initial_alignment_phase,
                'initial_alignment':deepcopy(self.initial_alignment),
                'packet_drops':dict(self.packet_drops),'last_drop_reason':self.last_drop_reason,
                'timing_diagnostics':{'schema':'explorer_pi_retarget_host_timing_v1','sample_count':len(self.round_trips),
                    'input_HTTP_RTT_ns':list(self.round_trips),'last_source_cache_age_ns':self.last_source_age_ns,
                    'clock_basis':'Pi_host_monotonic_only_no_cross_device_subtraction','hardware_latency_verified':False,
                    'discarded_late_replies':self.discarded_late_replies},
                'loop':'independent_latest_measured_cache_no_FIFO_no_automatic_native_lease',
                'recording':self.recorder.status(),'recording_owner':'nano_native_common_02_03',
                'calibration_wizard':self.calibration_wizard.status() if self.calibration_wizard else None,
                'chassis_requests':self.chassis_requests,'observed_chassis_grant_received':self.chassis_authorized,
                'controller_configuration_writes':0,'physical_acceptance_confirmed':False}

    def _measurement(self,*,require_fresh=True):
        state=self.station.status() if self.station is not None else {}
        measured=state.get('measurements',{});tick=measured.get('monotonic_ns')
        # Reuse station's existing camera freshness source contract, no motor TTL.
        freshness=getattr(getattr(self.station,'camera',None),'stale_s',2.)
        if state.get('mode')!='idle' and (state.get('available') is True or state.get('mode') is not None):
            raise ValueError('Measured leader owner changed; lab remains separate')
        if state.get('available') is not True or type(tick) is not int:
            raise SourcePacketDrop('SOURCE_CACHE_UNAVAILABLE')
        if require_fresh and not 0<=self.clock()-tick<=freshness*1e9:
            raise SourcePacketDrop('SOURCE_CACHE_STALE')
        positions=np.asarray(measured.get('leader'),float)
        if positions.shape!=(6,) or not np.isfinite(positions).all():
            raise ValueError('Six genuine normalized leader measurements required')
        role=state.get('roles',{}).get('leader',{});calibration=role.get('calibration_sha256')
        profile=getattr(self.geometry,'registration',None) or {}
        if profile.get('source_calibration_sha256') not in (None,calibration):
            raise ValueError('Leader calibration changed; cross-map must bind existing profile SHA')
        domain=measured.get('clock_domain');boot=getattr(self.station,'boot',None) or (domain.rsplit(':',1)[-1] if isinstance(domain,str) else None)
        if not domain or not boot or not calibration:
            raise ValueError('Original source clock/boot/calibration identity missing')
        return {'positions':positions.tolist(),'monotonic_ns':tick,'clock_domain':domain,'boot_id':boot,
                'sample_id':domain+':'+str(tick),'calibration_sha256':calibration,'state_source':'measured_leader',
                'station_control_epoch':state.get('control_epoch')}

    def _target_estimate(self,value):
        if (not isinstance(value,dict) or value.get('boot_id')!=self.target_boot or
                value.get('measured') is not False or value.get('blocked_by')):
            raise ValueError('Same-boot original factory command reference unavailable')
        field='q_estimated_deg' if value.get('q_estimated_deg') is not None else 'servo_deg'
        q=np.asarray(value.get(field),float)
        if q.shape!=(6,) or not np.isfinite(q).all():
            raise ValueError('Native six-axis command estimate missing; no readback fallback')
        return q.tolist(),field

    def _observe(self,body):
        if body.get('observing') is not True:
            raise ValueError('Current operator observation required; Pi camera alone does not prove Nano visibility')

    def _match(self,body):
        if body.get('control_epoch',body.get('expected_control_epoch'))!=self.epoch:
            raise ValueError('Source control epoch changed; old operator packet rejected')

    def _owned_native(self,native,*,clutched=False):
        if (native.get('execution_permission') is not True or native.get('active') is not True or
                native.get('control_epoch')!=self.target_epoch or native.get('boot_id')!=self.target_boot or
                self.authorization_id is not None and (native.get('activation') or {}).get('authorization_id')!=self.authorization_id or
                self.target_generation is not None and (native.get('native_generation')!=self.target_generation or
                    native.get('owner_generation')!=self.target_generation) or
                clutched and native.get('state')!='CLUTCHED'):
            raise ValueError('Original owned native epoch/authorization/reference context changed; no owner adoption')

    def _same_source(self,measured):
        current=tuple(measured.get(key) for key in ('clock_domain','boot_id','calibration_sha256','station_control_epoch'))
        if self.source_identity is not None and current!=self.source_identity:
            raise ValueError('Measured leader owner/boot/clock/calibration changed; fresh explicit start required')

    def start(self,body):
        if self.shutdown.is_set():
            raise ValueError('Pi native source is permanently closed')
        if self.geometry.status()['state']=='UNREGISTERED':
            return self.status()
        self._observe(body)
        profile=body.get('operator_profile','ARM_ONLY')
        if profile not in OPERATOR_PROFILES:raise ValueError('Explicit supported operator_profile required')
        alignment=body.get('initial_alignment','CLUTCH_CURRENT')
        if alignment not in INITIAL_ALIGNMENT_MODES:raise ValueError('Explicit supported initial_alignment required')
        registration=getattr(self.geometry,'registration',None) or {}
        if registration.get('provenance')=='synthetic' and not self.allow_synthetic:
            raise ValueError('Synthetic registration cannot activate a physical peer')
        with self.lock:
            self._match(body)
            if (self.shutdown.is_set() or self.client is None or self.lifecycle_inflight or self.step_inflight or self.pending_stops or
                    self.mode in ('ACTIVE','CLUTCHED','ACTIVATING','CLUTCHING','STOPPING','CLOSED') or
                    self.target_epoch is not None or self.stop_uncertain):
                raise ValueError('Native peer/session not idle or STOP delivery unknown')
            expected=self.epoch;self.lifecycle_inflight=True
        target_epoch=None
        self.operation_lock.acquire()
        try:
            measured=self._measurement();native=self.client.status()
            if native.get('execution_permission') is not True:
                raise ValueError('NATIVE_OWNER_REQUIRES_SEPARATE_USER_OBSERVED_TEST')
            self.client.authorization_id=(native.get('activation') or {}).get('authorization_id')
            self.authorization_id=self.client.authorization_id
            self.chassis_authorized=native.get('chassis_motion_allowed') is True
            if native.get('active') or native.get('stop_latched') is False:
                raise ValueError('Explicit native STOP required before selecting leader mirror')
            self.target_boot=native.get('boot_id')
            if not isinstance(self.target_boot,str) or not self.target_boot:
                raise ValueError('Original native boot identity unavailable; reference cannot be adopted')
            period=(native.get('original_stream_config') or {}).get('publish_period_s')
            if not isinstance(period,(int,float)) or not np.isfinite(period) or period<=0:
                raise ValueError('Original native Ruckig period not exposed')
            estimate=self.client.reference();target,basis=self._target_estimate(estimate)
            self.geometry.recenter(measured['positions'],target)
            with self.lock:
                if self.epoch!=expected:
                    raise ValueError('Start superseded before native ownership selection')
            self.client.select_keyboard(observing=True,neutral=True)
            with self.lock:
                if self.epoch!=expected:raise ValueError('Start superseded after native neutral owner selection')
                self.begin_outcome_unknown=True
            result=self.client.begin(observing=True,neutral=True,authorization_id=self.authorization_id);target_epoch=result['control_epoch']
            if not isinstance(target_epoch,str) or not target_epoch:
                target_epoch=None
                raise ValueError('Original begin did not return an owned native epoch')
            with self.lock:
                self.target_epoch=target_epoch
                self.begin_outcome_unknown=False
                self.target_generation=result.get('native_generation')
                self.source_identity=tuple(measured.get(key) for key in ('clock_domain','boot_id','calibration_sha256','station_control_epoch'))
                if self.epoch!=expected:
                    raise ValueError('Start superseded before native resume')
            with self.lock:
                if self.epoch!=expected:
                    raise ValueError('Start superseded after native resume')
                self.target_epoch=target_epoch;self.native_period=period;self.estimate=estimate
                self.source_sequence=0;self.last_source_stamp=measured['monotonic_ns'];self.last_lease_sequence=-1
                self.epoch=uuid.uuid4().hex;self.mode='CLUTCHED';self.error=None;self.operator_profile=profile
                self.initial_alignment_mode=alignment;self.initial_alignment_pending=False
                self.initial_alignment_phase='WAITING_FOR_HELD' if alignment=='LEADER_POSE' else 'NOT_REQUESTED'
                self.initial_alignment=None
                self.last_result={'recentered':True,'target_estimate_basis':basis,'native_dispatched':False}
                self.last_proposal=None;self.last_lease_receipt=None;self.round_trips.clear();self.last_source_age_ns=None
                self.gripper_alignment=None;self.packet_drops.clear();self.last_drop_reason=None
            self.wake.set();return self.status()
        except Exception as error:
            with self.lock:
                if self.epoch==expected:
                    self.error=type(error).__name__+': '+str(error);self.mode='INTERRUPTED'
                    if target_epoch is not None or self.begin_outcome_unknown:
                        self.stop_uncertain=True
            raise
        finally:
            with self.lock:
                self.lifecycle_inflight=False
            self.operation_lock.release()

    def recenter(self,body):
        self._observe(body)
        with self.lock:
            self._match(body)
            if self.shutdown.is_set() or self.lifecycle_inflight or self.pending_stops or self.target_epoch is None or self.mode=='CLOSED':
                raise ValueError('Native lifecycle operation already pending')
            # Fence immediately, including a source HTTP request already in flight.
            self.epoch=uuid.uuid4().hex;self.mode='CLUTCHING'
            self.lifecycle_inflight=True;expected=self.epoch;target_epoch=self.target_epoch
        self.operation_lock.acquire()
        try:
            native=self.client.status();self._owned_native(native)
            result=self.client.recenter(target_epoch,authorization_id=self.authorization_id);new_target=result['control_epoch']
            with self.lock:
                self.target_epoch=new_target
            measured=self._measurement();estimate=self.client.reference();target,basis=self._target_estimate(estimate)
            self._same_source(measured)
            self.geometry.recenter(measured['positions'],target)
            with self.lock:
                if self.epoch!=expected:
                    raise ValueError('Recenter superseded by STOP')
            with self.lock:
                if self.epoch!=expected:
                    raise ValueError('Old recenter response discarded')
                self.target_epoch=new_target;self.estimate=estimate;self.last_source_stamp=measured['monotonic_ns']
                self.epoch=uuid.uuid4().hex;self.mode='CLUTCHED';self.error=None
                self.initial_alignment_mode='CLUTCH_CURRENT';self.initial_alignment_pending=False
                self.initial_alignment_phase='RECENTERED';self.initial_alignment=None
                self.last_lease_sequence=-1;self.last_proposal=None;self.last_lease_receipt=None
                self.last_result={'recentered':True,'target_estimate_basis':basis,'native_dispatched':False}
            self.wake.set();return self.status()
        except Exception as error:
            with self.lock:
                if self.epoch==expected:
                    self.mode='INTERRUPTED';self.error=type(error).__name__+': '+str(error)
            raise
        finally:
            with self.lock:
                self.lifecycle_inflight=False
            self.operation_lock.release()

    def lease(self,body):
        if body.get('held') is not True:
            with self.lock:self._match(body)
            return self.stop(body)
        self._observe(body)
        actions=body.get('base_actions',{})
        if (not isinstance(actions,dict) or any(key not in BASE_ACTIONS for key in actions) or
                any(not np.isfinite(float(value)) for value in actions.values())):
            raise ValueError('Finite existing native base actions required')
        moving=any(float(value)!=0. for value in actions.values())
        with self.lock:
            self._match(body)
            if self.shutdown.is_set():
                raise ValueError('Pi native source is permanently closed')
            if self.pending_stops or self.lifecycle_inflight:
                raise ValueError('Source lifecycle/STOP already pending')
            sequence=body.get('sequence')
            if type(sequence) is not int or sequence<=self.last_lease_sequence:
                raise ValueError('Old operator heartbeat rejected')
            if self.mode not in ('ACTIVE','CLUTCHED') or self.target_epoch is None:
                raise ValueError('Fresh explicit native start/recenter required')
            if self.operator_profile=='ARM_ONLY' and moving:
                raise PermissionError('ARM_ONLY_OPERATOR_PROFILE_FORBIDS_BASE_INTENT')
            first=self.mode=='CLUTCHED'
            if first and moving:raise ValueError('NEUTRAL_FIRST_HELD_FRAME_REQUIRED')
            self.last_lease_sequence=sequence;expected=self.epoch;target_epoch=self.target_epoch;authorization=self.authorization_id
            self.operator_hold_deadline_ns=self.clock()+400_000_000;self.held_base_actions=deepcopy(actions)
            if first:self.mode='ACTIVATING';self.lifecycle_inflight=True
        if first:return self._first_held(expected,target_epoch,authorization)
        try:
            # The ORIGINAL native base endpoint validates the current grant.
            # Stable held frames add no permission/status round trip here.
            with self.lock:
                if self.epoch!=expected or self.mode!='ACTIVE':raise ValueError('Held frame superseded before native lease')
            result=self.client.keyboard_base(target_epoch,actions,observing=True,authorization_id=authorization)
            with self.lock:
                if self.epoch==expected and self.mode=='ACTIVE':
                    self.last_native_heartbeat_ns=self.clock()
                    if moving:self.chassis_requests+=1
                    self.last_lease_receipt={'target_receipt':deepcopy(result),'target_control_epoch':target_epoch,'native_dispatched':False}
                else:self.discarded_late_replies+=1
        except Exception as error:
            with self.lock:
                if self.epoch==expected and self.mode=='ACTIVE':
                    self.mode='INTERRUPTED';self.epoch=uuid.uuid4().hex;self.error=type(error).__name__+': '+str(error)
            raise
        self.wake.set();return self.status()

    def _first_held(self,expected,target_epoch,authorization):
        self.operation_lock.acquire()
        resume_started=False
        try:
            with self.lock:
                if self.epoch!=expected or self.mode!='ACTIVATING':raise ValueError('First held superseded before native observation')
            native=self.client.status();self._owned_native(native,clutched=True)
            if native.get('arm_backend')!='existing_factory_ruckig_velocity':
                raise ValueError('GRIPPER_ALIGNMENT_NATIVE_SERIALIZATION_PROFILE_UNSUPPORTED')
            estimate=self.client.reference();target,basis=self._target_estimate(estimate)
            measured=self._measurement();self._same_source(measured)
            self._align_gripper(measured,target,estimate)
            with self.lock:
                if self.epoch!=expected or self.mode!='ACTIVATING':raise ValueError('First held superseded before native resume')
            resume_started=True;self.client.resume(target_epoch,observing=True,authorization_id=authorization)
            with self.lock:
                if self.epoch!=expected or self.mode!='ACTIVATING':raise ValueError('First held superseded after native resume')
            result=self.client.keyboard_base(target_epoch,{},observing=True,authorization_id=authorization)
            # A previous original publication may finish during recenter. Read
            # its CURRENT command estimate after resume/neutral; this acquire-
            # only GET neither renews the original lease nor runs in ACTIVE.
            estimate=self.client.reference();target,basis=self._target_estimate(estimate)
            # All admission network I/O is now complete. Relative mode makes
            # this the new anchor; explicit leader-pose mode first converges
            # through the unchanged native Ruckig stream.
            measured=self._measurement();self._same_source(measured)
            self._align_gripper(measured,target,estimate)
            align_to_leader=self.initial_alignment_mode=='LEADER_POSE'
            if not align_to_leader:self.geometry.recenter(measured['positions'],target)
            with self.lock:
                if self.epoch!=expected or self.mode!='ACTIVATING':raise ValueError('First held superseded after neutral lease')
                self.mode='ACTIVE';self.estimate=estimate;self.last_source_stamp=measured['monotonic_ns'];self.error=None
                self.initial_alignment_pending=align_to_leader
                self.initial_alignment_phase='ALIGNING_TO_LEADER' if align_to_leader else 'FOLLOWING'
                self.operator_hold_deadline_ns=self.clock()+400_000_000;self.held_base_actions={};self.last_native_heartbeat_ns=self.clock()
                self.last_lease_receipt={'target_receipt':deepcopy(result),'target_control_epoch':target_epoch,
                    'first_explicit_held':True,'recentered_at_explicit_held':not align_to_leader,
                    'leader_pose_alignment_requested':align_to_leader,
                    'target_estimate_basis':basis,'native_dispatched':False}
            self.wake.set();return self.status()
        except Exception as error:
            with self.lock:
                if self.epoch==expected:
                    self.mode='INTERRUPTED';self.error=type(error).__name__+': '+str(error)
                    if resume_started:self.stop_uncertain=True
            raise
        finally:
            with self.lock:self.lifecycle_inflight=False
            self.operation_lock.release()

    def stop(self,body=None):
        body=body or {}
        with self.lock:
            if body.get('control_epoch',body.get('expected_control_epoch')) is not None:
                self._match(body)
            self.pending_stops+=1
            self.mode='STOPPING';self.epoch=uuid.uuid4().hex;self.wake.set()
        self.operation_lock.acquire()
        try:
            # Any earlier begin/recenter has finished. Never acknowledge STOP
            # before its late-created native epoch has also been revoked.
            with self.lock:
                target_epoch=self.target_epoch
            cleanup={'owned_target_epoch':target_epoch,'native_stop_attempted':False,'hardware_stop_verified':False,
                'outcome':'NO_OWNED_NATIVE_EPOCH'}
            if self.client is not None and target_epoch is None and self.begin_outcome_unknown:
                peer=self.client.status()
                if peer.get('active') is not False:
                    raise ValueError('Native begin reply unknown; explicit global STOP needed, no GET owner adopted')
                cleanup['outcome']='UNKNOWN_BEGIN_SOURCE_NOW_INACTIVE'
            if self.client is not None and target_epoch is not None:
                cleanup['native_stop_attempted']=True
                try:
                    cleanup['native_receipt']=self.client.stop(target_epoch);cleanup['outcome']='OWNED_STOP_API_RETURNED'
                except Exception:
                    peer=self.client.status()
                    if (not isinstance(peer,dict) or type(peer.get('active')) is not bool or
                            peer.get('active') and not peer.get('control_epoch')):
                        raise ValueError('Owned STOP outcome unavailable; malformed native status')
                    if peer.get('active') and peer.get('control_epoch')==target_epoch:raise
                    cleanup['outcome']='SUPERSEDED_BY_OTHER_OWNER' if peer.get('active') else 'OWNED_SCOPE_NO_LONGER_ACTIVE'
            with self.lock:
                self.mode='CLOSED' if self.shutdown.is_set() else 'STOPPED'
                self.target_epoch=None;self.stop_uncertain=False;self.error=None
                self.chassis_authorized=False
                self.initial_alignment_pending=False;self.initial_alignment_phase='STOPPED'
                self.operator_hold_deadline_ns=None;self.held_base_actions={};self.last_native_heartbeat_ns=None
                self.begin_outcome_unknown=False
                self.last_stop_receipt=cleanup
            return self.status()
        except Exception as error:
            with self.lock:
                self.mode='CLOSED' if self.shutdown.is_set() else 'STOP_DELIVERY_UNKNOWN'
                self.stop_uncertain=True;self.error=type(error).__name__
            raise
        finally:
            self.operation_lock.release()
            with self.lock:
                self.pending_stops-=1

    def command(self,operation,body):
        if operation in ('start','recenter','lease','stop'):
            if operation=='stop':
                # Node HTTP callers must retain THEIR source epoch. Internal
                # global/user STOP may call stop() directly; it still cancels
                # only this service's retained native epoch, never a GET owner.
                with self.lock:self._match(body)
            return getattr(self,operation)(body)
        if self.shutdown.is_set():
            raise ValueError('Pi native source is permanently closed')
        if operation.startswith('calibration/'):
            return self._calibration_command(operation,body)
        if self.client is None:
            raise ValueError('Native target peer is not configured')
        if operation in ('activation/status','activation/challenge','activation/authorize','activation/revoke'):
            method=getattr(self.client,operation.replace('/','_'))
            return method(body) if operation.endswith(('authorize','revoke')) else method()
        if operation=='record/start':
            context=body.get('skill_context')
            if not isinstance(context,dict) or context.get('project_id')!='03':
                raise ValueError('Validated common ROSMASTER03 skill_context required')
            result=self.client.capture_start(context)
        elif operation=='record/stage':
            result=self.client.capture_stage(body['recording_id'],body.get('phase_id',body.get('stage')),
                reason=body.get('reason','explicit_operator_annotation'),recovery_of=body.get('recovery_of'))
        elif operation=='record/finish':
            result=self.client.capture_finish(body['recording_id'],body.get('outcome_evidence'))
        elif operation=='record/status':
            result=self.client.capture_status()
        else:
            raise ValueError('Unknown native source operation')
        self.recorder.last_status=deepcopy(result);return result

    def _calibration_command(self,operation,body):
        if self.calibration_wizard is None:
            raise ValueError('Existing leader calibration/URDF arithmetic not available')
        if operation=='calibration/status':return self.calibration_wizard.status()
        if operation=='calibration/preview':
            measured=self._measurement()
            if self.geometry.status()['state']=='BOOTSTRAP_DERIVED':
                from .native_leader_bootstrap import source_diagnostics
                preview=source_diagnostics(self.geometry,measured['positions'])
            else:preview=self.calibration_wizard.context.preview(measured['positions'])
            return {'measured_leader':measured,'preview':preview}
        with self.lock:
            if (self.shutdown.is_set() or self.mode in ('ACTIVE','CLUTCHED','ACTIVATING','CLUTCHING','STOPPING') or
                    self.target_epoch is not None or
                    self.lifecycle_inflight or self.step_inflight or self.pending_stops):
                raise ValueError('Stop mirror before observing or accepting a new cross-map')
            self.lifecycle_inflight=True;expected=self.epoch
        self.operation_lock.acquire()
        try:
            if operation=='calibration/capture':
                measured=self._measurement();target=None
                if body.get('kind')=='task_axes':
                    if self.client is None:raise ValueError('Original native reference peer not configured')
                    native=self.client.status()
                    if native.get('active') or native.get('stop_latched') is not True:
                        raise ValueError('Original native STOP required for task-axis reference capture')
                    target=self.client.reference()
                frame=getattr(self.station,'camera',None)
                frame=frame.latest() if frame is not None else None
                return self.calibration_wizard.capture(body,measured,target_reference=target,
                    source_frame={**frame.metadata(),'jpeg':frame.jpeg} if frame is not None else None)
            if operation=='calibration/accept':
                result=self.calibration_wizard.accept(body)
                geometry=registered_native_leader_geometry(self.calibration_wizard.config_dir)
                with self.lock:
                    if self.epoch!=expected or self.shutdown.is_set():
                        raise ValueError('Cross-map stored as evidence; source session was stopped/closed')
                    self.geometry=geometry;self.epoch=uuid.uuid4().hex
                return {**result,'retarget':self.status()}
            raise ValueError('Unknown metadata calibration operation')
        finally:
            with self.lock:self.lifecycle_inflight=False
            self.operation_lock.release()

    def _align_gripper(self,measured,target,estimate):
        result=gripper_alignment(self.geometry,measured['positions'],target,
            calibration_sha256=measured['calibration_sha256'],wire_target=estimate.get('servo_deg') or [])
        with self.lock:self.gripper_alignment=result
        if result.get('aligned') is not True:
            if getattr(self.geometry,'gripper_clutch_mode',None)!='relative_delta' or getattr(self.geometry,'gripper_anchor',None) is None:
                raise ValueError(result['reason'])
            mapped_at_clutch,target_at_clutch=self.geometry.gripper_anchor
            result.update(relative_clutch_applied=True,relative_clutch_initial_target_deg=target_at_clutch,
                absolute_mapper_at_clutch_deg=mapped_at_clutch,initial_serialized_endpoint_unchanged=True,
                reason='RELATIVE_CLUTCH_PRESERVES_CURRENT_TARGET_ENDPOINT')
            with self.lock:self.gripper_alignment=deepcopy(result)
        return result

    def _step_once(self):
        """One unchanged coalesced iteration; also usable by finite fake sinks."""
        with self.lock:
            active=self.mode=='ACTIVE' and not self.lifecycle_inflight and not self.step_inflight
            if active:
                local_epoch,target_epoch=self.epoch,self.target_epoch;self.step_inflight=True
        if active:
            try:
                measured=self._measurement(require_fresh=False);self._same_source(measured)
                freshness=getattr(getattr(self.station,'camera',None),'stale_s',2.)
                if not 0<=self.clock()-measured['monotonic_ns']<=freshness*1e9:
                    raise SourcePacketDrop('SOURCE_CACHE_STALE')
                if self.last_source_stamp is not None and measured['monotonic_ns']<=self.last_source_stamp:
                    raise SourcePacketDrop('SOURCE_PACKET_DUPLICATE' if measured['monotonic_ns']==self.last_source_stamp else 'SOURCE_PACKET_OUT_OF_ORDER')
                estimate=self.estimate or self.client.reference();target,basis=self._target_estimate(estimate)
                if self.initial_alignment_pending:
                    proposal=self.geometry.leader_pose_alignment(measured['positions'],target,
                        original_native_period_s=self.native_period)
                    with self.lock:self.initial_alignment=deepcopy(proposal)
                    if proposal['max_error_deg']<=ALIGNMENT_TOLERANCE_DEG:
                        self.geometry.recenter(measured['positions'],target)
                        with self.lock:
                            if self.epoch!=local_epoch or self.mode!='ACTIVE':
                                raise ValueError('Old alignment frame superseded before following')
                            self.initial_alignment_pending=False;self.initial_alignment_phase='FOLLOWING'
                            self.last_source_stamp=measured['monotonic_ns']
                            self.last_result={'initial_alignment_completed':True,
                                'max_error_deg':proposal['max_error_deg'],'native_dispatched':False}
                        return
                else:
                    proposal=self.geometry.propose(measured['positions'],target,original_native_period_s=self.native_period)
                if proposal.get('accepted') is not True:
                    raise SourcePacketDrop('TARGET_IK_UNREACHABLE')
                with self.lock:
                    if self.epoch!=local_epoch or self.mode!='ACTIVE':
                        raise ValueError('Old source frame superseded before HTTP intent')
                    self.source_sequence+=1;sequence=self.source_sequence
                    heartbeat_now=self.clock();heartbeat_due=(self.operator_hold_deadline_ns is not None and
                        heartbeat_now<self.operator_hold_deadline_ns and (self.last_native_heartbeat_ns is None or
                        heartbeat_now-self.last_native_heartbeat_ns>=100_000_000))
                    held_actions=deepcopy(self.held_base_actions);authorization=self.authorization_id
                evidence={**measured,'target_estimate_basis':basis,'target_boot_id':self.target_boot,
                          'registration_sha256':(getattr(self.geometry,'registration',None) or {}).get('sha256'),
                          'geometric_proposal':deepcopy(proposal),'cross_device_clock_mapping_assumed':False}
                started=self.clock()
                if heartbeat_due:
                    heartbeat=self.client.keyboard_base(target_epoch,held_actions,observing=True,authorization_id=authorization)
                    with self.lock:
                        if self.epoch!=local_epoch or self.mode!='ACTIVE':
                            raise ValueError('Old held heartbeat superseded before leader intent')
                        self.last_native_heartbeat_ns=self.clock()
                        self.last_lease_receipt={'target_receipt':deepcopy(heartbeat),'target_control_epoch':target_epoch,
                            'relayed_from_fresh_explicit_operator_hold':True,'native_dispatched':False}
                receipt=self.client.input(target_epoch,sequence,proposal['joint_velocity_deg_s'],evidence,
                    authorization_id=self.authorization_id)
                ended=self.clock()
                with self.lock:
                    if self.epoch==local_epoch and self.mode=='ACTIVE':
                        self.estimate=receipt.get('command_estimate')
                        self.last_source_stamp=measured['monotonic_ns'];self.sample_count+=1
                        self.last_result={'proposal':proposal,'target_receipt':receipt,'native_dispatched':False}
                        self.last_proposal=deepcopy(self.last_result)
                        if self.initial_alignment_pending:
                            self.initial_alignment={**deepcopy(proposal),'target_receipt':deepcopy(receipt)}
                        if ended>=started:self.round_trips.append(ended-started)
                        self.last_source_age_ns=started-measured['monotonic_ns']
                    else:self.discarded_late_replies+=1
            except SourcePacketDrop as error:
                with self.lock:
                    if self.epoch==local_epoch and self.mode=='ACTIVE':
                        reason=str(error);self.packet_drops[reason]=self.packet_drops.get(reason,0)+1;self.last_drop_reason=reason
            except Exception as error:
                with self.lock:
                    if self.epoch==local_epoch and self.mode=='ACTIVE':
                        self.mode='INTERRUPTED';self.epoch=uuid.uuid4().hex;self.error=type(error).__name__+': '+str(error)
            finally:
                with self.lock:self.step_inflight=False

    def _work(self):
        while not self.shutdown.is_set():
            self.wake.wait(.01);self.wake.clear()
            self._step_once()

    def close(self):
        # Observer shutdown revokes source work; it never issues a new physical
        # POST. The original native user lease/permit expires independently.
        with self.lock:
            self.mode='CLOSED';self.epoch=uuid.uuid4().hex
        self.shutdown.set();self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)
