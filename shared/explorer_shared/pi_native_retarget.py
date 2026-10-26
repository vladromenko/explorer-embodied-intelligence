"""ENGINEERING_GLUE: existing measured Pi cache -> native latest Ruckig intent.

Independent coalesced source loop, no finite plan/start and no second servo bus.
Only explicit operator heartbeat renews the ORIGINAL native manual lease.
Recording is proxied to the SAME Nano producer used by02; old raw archives stay.
"""
from copy import deepcopy
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
        self.recorder=LegacyRawView(Path(state_dir)/'retarget-recordings');self.thread=None

    @classmethod
    def from_config(cls,station,state_dir,config_dir):
        geometry=registered_native_leader_geometry(config_dir);client=None;error=None
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
                'peer_configured':self.client is not None,'lifecycle_inflight':self.lifecycle_inflight,
                'step_inflight':self.step_inflight,'stop_delivery_uncertain':self.stop_uncertain,
                'inflight':self.lifecycle_inflight or self.step_inflight,'pending_stops':self.pending_stops,
                'source':'existing_SO101Station_measured_cache','target':'ROSMASTER_UNIFIED_9D_NATIVE_MUX',
                'target_camera':'nano-rgbd','native_backend':'factory_micro_ros_ruckig_velocity',
                'legacy_finite_queue_used':False,'mirror_requires_recording':False,
                'source_station_mode':station.get('mode'),'source_calibration_sha256':role.get('calibration_sha256'),
                'source_calibrated':bool(role.get('calibration_sha256')),
                'missing_registration_is_cross_map_not_device_recalibration':True,
                'cached_samples_consumed':self.sample_count,'last_result':deepcopy(self.last_result),'error':self.error,
                'loop':'independent_latest_measured_cache_no_FIFO_no_automatic_native_lease',
                'recording':self.recorder.status(),'recording_owner':'nano_native_common_02_03',
                'chassis_requests':0,'controller_configuration_writes':0,'physical_acceptance_confirmed':False}

    def _measurement(self):
        state=self.station.status() if self.station is not None else {}
        measured=state.get('measurements',{});tick=measured.get('monotonic_ns')
        # Reuse station's existing camera freshness source contract, no motor TTL.
        freshness=getattr(getattr(self.station,'camera',None),'stale_s',2.)
        if (state.get('available') is not True or state.get('mode')!='idle' or type(tick) is not int or
                not 0<=self.clock()-tick<=freshness*1e9):
            raise ValueError('Existing measured leader cache is unavailable/stale; lab remains separate')
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
                value.get('measured') is True or value.get('blocked_by')):
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

    def start(self,body):
        if self.geometry.status()['state']=='UNREGISTERED':
            return self.status()
        self._observe(body)
        registration=getattr(self.geometry,'registration',None) or {}
        if registration.get('provenance')=='synthetic' and not self.allow_synthetic:
            raise ValueError('Synthetic registration cannot activate a physical peer')
        with self.lock:
            self._match(body)
            if (self.client is None or self.lifecycle_inflight or self.step_inflight or self.pending_stops or
                    self.mode in ('ACTIVE','STOPPING','CLOSED') or self.stop_uncertain):
                raise ValueError('Native peer/session not idle or STOP delivery unknown')
            expected=self.epoch;self.lifecycle_inflight=True
        target_epoch=None
        self.operation_lock.acquire()
        try:
            measured=self._measurement();native=self.client.status()
            if native.get('execution_permission') is not True:
                raise ValueError('NATIVE_OWNER_REQUIRES_SEPARATE_USER_OBSERVED_TEST')
            if native.get('active') or native.get('stop_latched') is False:
                raise ValueError('Explicit native STOP required before selecting leader mirror')
            self.target_boot=native.get('boot_id')
            period=(native.get('original_stream_config') or {}).get('publish_period_s')
            if not isinstance(period,(int,float)) or not np.isfinite(period) or period<=0:
                raise ValueError('Original native Ruckig period not exposed')
            estimate=self.client.reference();target,basis=self._target_estimate(estimate)
            self.geometry.recenter(measured['positions'],target)
            with self.lock:
                if self.epoch!=expected:
                    raise ValueError('Start superseded before native ownership selection')
            self.client.select_keyboard(observing=True,neutral=True)
            result=self.client.begin(observing=True,neutral=True);target_epoch=result['control_epoch']
            with self.lock:
                self.target_epoch=target_epoch
                if self.epoch!=expected:
                    raise ValueError('Start superseded before native resume')
            self.client.resume(target_epoch,observing=True)
            with self.lock:
                if self.epoch!=expected:
                    raise ValueError('Start superseded after native resume')
                self.target_epoch=target_epoch;self.native_period=period;self.estimate=estimate
                self.source_sequence=0;self.last_source_stamp=measured['monotonic_ns'];self.last_lease_sequence=-1
                self.epoch=uuid.uuid4().hex;self.mode='ACTIVE';self.error=None
                self.last_result={'recentered':True,'target_estimate_basis':basis,'native_dispatched':False}
            self.wake.set();return self.status()
        except Exception as error:
            with self.lock:
                if self.epoch==expected:
                    self.error=type(error).__name__+': '+str(error);self.mode='INTERRUPTED'
                    if target_epoch is not None:
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
            if self.lifecycle_inflight or self.pending_stops or self.target_epoch is None or self.mode=='CLOSED':
                raise ValueError('Native lifecycle operation already pending')
            # Fence immediately, including a source HTTP request already in flight.
            self.epoch=uuid.uuid4().hex;self.mode='CLUTCHING'
            self.lifecycle_inflight=True;expected=self.epoch;target_epoch=self.target_epoch
        self.operation_lock.acquire()
        try:
            result=self.client.recenter(target_epoch);new_target=result['control_epoch']
            with self.lock:
                self.target_epoch=new_target
            measured=self._measurement();estimate=self.client.reference();target,basis=self._target_estimate(estimate)
            self.geometry.recenter(measured['positions'],target)
            with self.lock:
                if self.epoch!=expected:
                    raise ValueError('Recenter superseded by STOP')
            self.client.resume(new_target,observing=True)
            with self.lock:
                if self.epoch!=expected:
                    raise ValueError('Old recenter response discarded')
                self.target_epoch=new_target;self.estimate=estimate;self.last_source_stamp=measured['monotonic_ns']
                self.epoch=uuid.uuid4().hex;self.mode='ACTIVE';self.error=None
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
        with self.lock:
            self._match(body)
            sequence=body.get('sequence')
            if type(sequence) is not int or sequence<=self.last_lease_sequence:
                raise ValueError('Old operator heartbeat rejected')
            self.last_lease_sequence=sequence
            if self.mode!='ACTIVE' or self.target_epoch is None:
                raise ValueError('Fresh explicit native start/recenter required')
            target_epoch=self.target_epoch
        if body.get('held') is not True:
            return self.stop(body)
        self._observe(body)
        actions=body.get('base_actions',{})
        if (not isinstance(actions,dict) or any(key not in BASE_ACTIONS for key in actions) or
                any(not np.isfinite(float(value)) or abs(float(value))>1e-12 for value in actions.values())):
            raise PermissionError('PHYSICAL_CHASSIS_PROHIBITED_BY_OWNER')
        result=self.client.keyboard_base(target_epoch,actions,observing=True)
        with self.lock:
            self.last_result={'operator_native_lease_receipt':result,'native_dispatched':False}
        self.wake.set();return self.status()

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
            if self.client is not None:
                if target_epoch is None:
                    peer=self.client.status();target_epoch=peer.get('control_epoch') if peer.get('active') else None
                if target_epoch is not None:
                    try:
                        self.client.stop(target_epoch)
                    except Exception:
                        peer=self.client.status()
                        if peer.get('active'):
                            raise
            with self.lock:
                self.mode='STOPPED';self.target_epoch=None;self.stop_uncertain=False;self.error=None
            return self.status()
        except Exception as error:
            with self.lock:
                self.mode='STOP_DELIVERY_UNKNOWN';self.stop_uncertain=True;self.error=type(error).__name__
            raise
        finally:
            self.operation_lock.release()
            with self.lock:
                self.pending_stops-=1

    def command(self,operation,body):
        if operation in ('start','recenter','lease','stop'):
            return getattr(self,operation)(body)
        if self.client is None:
            raise ValueError('Native target peer is not configured')
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

    def _work(self):
        while not self.shutdown.is_set():
            self.wake.wait(.01);self.wake.clear()
            with self.lock:
                active=self.mode=='ACTIVE' and not self.lifecycle_inflight and not self.step_inflight
                if active:
                    local_epoch,target_epoch=self.epoch,self.target_epoch;self.step_inflight=True
            if active:
                try:
                    measured=self._measurement()
                    if measured['monotonic_ns']!=self.last_source_stamp:
                        estimate=self.estimate or self.client.reference();target,basis=self._target_estimate(estimate)
                        proposal=self.geometry.propose(measured['positions'],target,original_native_period_s=self.native_period)
                        if proposal.get('accepted') is not True:
                            raise ValueError('Native target IK rejected this latest measured sample')
                        with self.lock:
                            if self.epoch!=local_epoch or self.mode!='ACTIVE':
                                raise ValueError('Old source frame superseded before HTTP intent')
                            self.source_sequence+=1;sequence=self.source_sequence
                        evidence={**measured,'target_estimate_basis':basis,'target_boot_id':self.target_boot,
                                  'registration_sha256':(getattr(self.geometry,'registration',None) or {}).get('sha256'),
                                  'geometric_proposal':deepcopy(proposal),'cross_device_clock_mapping_assumed':False}
                        receipt=self.client.input(target_epoch,sequence,proposal['joint_velocity_deg_s'],evidence)
                        with self.lock:
                            if self.epoch==local_epoch and self.mode=='ACTIVE':
                                self.estimate=receipt.get('command_estimate')
                                self.last_source_stamp=measured['monotonic_ns'];self.sample_count+=1
                                self.last_result={'proposal':proposal,'target_receipt':receipt,'native_dispatched':False}
                except Exception as error:
                    with self.lock:
                        if self.epoch==local_epoch and self.mode=='ACTIVE':
                            self.mode='INTERRUPTED';self.epoch=uuid.uuid4().hex;self.error=type(error).__name__+': '+str(error)
                finally:
                    with self.lock:
                        self.step_inflight=False

    def close(self):
        # Observer shutdown revokes source work; it never issues a new physical
        # POST. The original native user lease/permit expires independently.
        with self.lock:
            self.mode='CLOSED';self.epoch=uuid.uuid4().hex
        self.shutdown.set();self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)
