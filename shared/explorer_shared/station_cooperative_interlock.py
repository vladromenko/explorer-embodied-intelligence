"""ENGINEERING_GLUE: durable metadata wrapper for the COMMON native receiver.

No native command, camera owner or driver. File operations occur only during
construction/explicit metadata requests, outside Station/publication locks.
check_publication reads cached state only and must precede legacy early-return.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from .contracts import canonical,digest
from .cooperative_receiver_interlock05 import ReceiverHoldInterlock,PROFILE_SCHEMA,observed_gripper_direction
from .so101_station import LOW,HIGH

ENVELOPE='explorer_station_cooperative_interlock_wal_v1'
INITIALIZED='explorer_station_cooperative_interlock_initialized_v1'
LIMIT=1024*1024
ID=re.compile('[0-9a-f]{64}')
CAPTURE_ID=re.compile('[0-9a-f]{32}')


def _raw(path,limit=LIMIT):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        meta=os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_size>limit:raise ValueError('Bounded regular interlock metadata required')
        raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('Interlock metadata bound exceeded')
    return raw


def _read(path,limit=LIMIT):return json.loads(_raw(path,limit))


def _original(path,raw):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


def _write(path,value):
    raw=canonical(value)
    if len(raw)>LIMIT:raise ValueError('Interlock metadata state exceeds bound')
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if temporary.exists():temporary.unlink()


class StationCooperativeInterlock:
    def __init__(self,provider,state_path):
        self.provider=provider;self.station=provider.station;self.clock=provider.clock
        self.path=Path(state_path);self.metadata_lock=threading.RLock();self.fault=None;self.fault_id=None;self.fault_revision=0
        self.initialized_path=self.path.with_name(self.path.name+'.initialized.json')
        self.profile_root=self.path.parent/'cooperative-direction-profiles';self.profiles={}
        self.capture_root=self.path.parent/'cooperative-direction-captures';self.captures={};self.capture_errors={}
        self.helper=self._new_helper();self.last_transaction=None
        try:
            if self.path.parent.resolve()!=Path(self.station.root).resolve() or self.path.is_symlink():
                raise ValueError('Fixed original Station state directory required')
            if self.initialized_path.exists() or self.initialized_path.is_symlink():
                self._initialized()
                if not self.path.exists():raise ValueError('Previously initialized interlock WAL is missing; armed history is unknown')
            if self.path.exists():
                envelope=_read(self.path)
                if not isinstance(envelope,dict) or envelope.get('schema')!=ENVELOPE or envelope.get('envelope_sha256')!=digest({key:value for key,value in envelope.items() if key!='envelope_sha256'}):
                    raise ValueError('Interlock WAL integrity/schema differs')
                if envelope.get('pending') is not None or envelope.get('fault') is not None:
                    raise ValueError('Pending/failed interlock WAL must not restore an unarmed default')
                self.helper=self._new_helper(envelope['state']);self.last_transaction=envelope.get('last_transaction')
            self._load_profiles()
            self._load_captures()
        except (ValueError,OSError,KeyError,TypeError,AttributeError) as error:self._fault('INTERLOCK_PERSISTENCE_UNCERTAIN:'+str(error))
    def _new_helper(self,state=None):
        return ReceiverHoldInterlock(clock_domain=self.station.clock_domain,boot_id=self.station.boot,clock=self.clock,state=state)
    def _fault(self,reason):
        if self.fault is None:self.fault_id=uuid.uuid4().hex;self.fault_revision+=1
        self.fault=str(reason)[:1000]
    def _initialized(self,create=False,repair=False):
        marker={'schema':INITIALIZED,'state_file':self.path.name,'initialization_is_not_object_safety_or_authority':True}
        if self.initialized_path.exists() or self.initialized_path.is_symlink():
            try:
                if _read(self.initialized_path,4096)!=marker:raise ValueError('Original interlock initialization marker differs')
            except (ValueError,OSError):
                if not repair:raise
                # Explicit recovery first sealed a pending/fault WAL. Repairing
                # this known constant can never expose an unrecorded reset.
                _write(self.initialized_path,marker)
        elif create:_original(self.initialized_path,canonical(marker))
        else:raise ValueError('Original interlock initialization marker is missing')
    def _load_profiles(self):
        if not self.profile_root.exists():return
        if self.profile_root.is_symlink():raise ValueError('Managed direction profiles required')
        paths=list(self.profile_root.glob('*.json'))
        if len(paths)>64:raise ValueError('Observed direction profile inventory bound exceeded')
        profiles={}
        for path in paths:
            if not ID.fullmatch(path.stem):raise ValueError('Only source direction profile IDs are allowed')
            profile=_read(path,128*1024)
            if (not isinstance(profile,dict) or profile.get('schema')!=PROFILE_SCHEMA or not ID.fullmatch(str(profile.get('follower_calibration_sha256'))) or
                    not isinstance(profile.get('basis'),str) or profile.get('profile_sha256')!=path.stem or
                    digest({key:value for key,value in profile.items() if key!='profile_sha256'})!=path.stem):
                raise ValueError('Original direction profile digest differs')
            self._verify_profile_sources(profile)
            profiles[path.stem]=profile
        self.profiles=profiles
    def _verify_profile_sources(self,profile):
        identifiers=profile.get('source_capture_ids')
        if identifiers is None:return  # Existing trusted source builder profiles have external original SHAs.
        if not isinstance(identifiers,dict) or set(identifiers)!={'open','empty_closed'}:
            raise ValueError('Exact original observed endpoint source IDs required')
        for label,identity in identifiers.items():
            if not isinstance(identity,str) or not CAPTURE_ID.fullmatch(identity):raise ValueError('Exact original source capture ID required')
            raw=_raw(self.capture_root/(identity+'.json'),128*1024);source=json.loads(raw)
            if (hashlib.sha256(raw).hexdigest()!=(profile.get('original_source_sha256') or {}).get(label) or
                    not isinstance(source,dict) or not isinstance(source.get('frame'),dict) or
                    hashlib.sha256(_raw(self.capture_root/(identity+'.jpg'),2*1024**2)).hexdigest()!=source['frame'].get('jpeg_sha256')):
                raise ValueError('Original observed endpoint record/JPEG no longer matches the registered profile')
    def _cached_permission(self):
        with self.provider.lock:
            record=self.provider.grant
            if record is None or record['revoked'].is_set():return None
            value=self.provider._data(record)
            follower=(getattr(self.station,'profiles',{}).get('follower') or {})
            if (value.get('control_epoch')!=self.station.control_epoch or value.get('native_mode')!=self.station.mode or
                    value.get('follower_calibration_sha256')!=follower.get('calibration_sha256') or
                    follower.get('verified_on_connected_hardware') is not True):return None
            return value
    def _load_captures(self):
        if not self.capture_root.exists():return
        if self.capture_root.is_symlink():raise ValueError('Managed direction captures required')
        paths=list(self.capture_root.glob('*.json'))
        if len(paths)>64:raise ValueError('Direction capture inventory bound exceeded')
        captures={};errors={}
        for path in paths:
            try:
                if not CAPTURE_ID.fullmatch(path.stem):raise ValueError('Exact source capture ID required')
                raw=_raw(path,128*1024);value=json.loads(raw);image=_raw(self.capture_root/(path.stem+'.jpg'),2*1024**2)
                if (not isinstance(value,dict) or value.get('schema')!='explorer_so101_gripper_endpoint_observation_v1' or value.get('capture_id')!=path.stem or
                        not isinstance(value.get('operator_confirmation'),dict) or not isinstance(value.get('frame'),dict) or
                        not isinstance(value.get('measurement_stamp'),dict) or
                        value.get('operator_confirmation',{}).get('label') not in ('empty_open','empty_closed') or
                        not ID.fullmatch(str(value.get('follower_calibration_sha256'))) or
                        type(value.get('measurement_stamp',{}).get('monotonic_ns')) is not int or
                        not isinstance(value.get('measurement_stamp',{}).get('boot_id'),str) or
                        not isinstance(value.get('measurement_stamp',{}).get('clock_domain'),str) or
                        hashlib.sha256(image).hexdigest()!=value.get('frame',{}).get('jpeg_sha256')):
                    raise ValueError('Original direction capture/frame differs')
                captures[path.stem]={'capture_id':path.stem,'label':value['operator_confirmation']['label'],
                    'follower_calibration_sha256':value['follower_calibration_sha256'],'source_sha256':hashlib.sha256(raw).hexdigest(),
                    'frame_id':value['frame']['frame_id'],'jpeg_sha256':value['frame']['jpeg_sha256'],
                    'monotonic_ns':value['measurement_stamp']['monotonic_ns'],
                    'boot_id':value['measurement_stamp']['boot_id'],'clock_domain':value['measurement_stamp']['clock_domain'],
                    'actor_id':value['operator_confirmation'].get('actor_id')}
            except (ValueError,OSError,KeyError,TypeError) as error:errors[path.stem]=str(error)
        self.captures=captures;self.capture_errors=errors
    def _current(self,body,actor):
        current=self._cached_permission()
        if (current is None or current.get('grant_id')!=body.get('grant_id') or
                current.get('control_epoch')!=body.get('expected_control_epoch') or current.get('actor_id')!=actor or
                current['binding'].get('purpose')!='cooperative05'):
            raise ValueError('Exact current USER cooperative grant/epoch/actor required')
        # Cached camera/native checks are outside the critical publication hook.
        return self.provider.check(current['grant_id'],current['binding'],current['control_epoch'])
    @contextmanager
    def _locks(self):
        gate=self.provider.publication_gate
        if gate is None or not hasattr(gate,'publication_lock'):raise ValueError('Actual original publication admission gate required')
        with self.station.lock:
            with gate.publication_lock:
                with self.provider.lock:yield
    def _locked_current(self,current,actor):
        native,calibration=self.provider._native()
        record=self.provider._current_locked(current['grant_id'],current['binding'],native,calibration,current['control_epoch'])
        actual=self.provider._data(record)
        if actual['actor_id']!=actor:raise ValueError('Current operator changed during metadata persistence')
        return actual
    def _envelope(self,*,pending=None,state=None,fault=None,last_transaction=None):
        value={'schema':ENVELOPE,'state':state if state is not None else self.helper.snapshot_state(),
            'pending':pending,'fault':fault,'last_transaction':last_transaction}
        return dict(value,envelope_sha256=digest(value))
    def _transaction(self,operation,body,actor,mutate,*,recover=False):
        # This lock is NEVER taken by check_publication; fsync cannot deadlock a
        # worker holding the original Station lock while awaiting metadata I/O.
        with self.metadata_lock:
            if self.fault is not None and not recover:raise ValueError('Uncertain receiver state requires explicit current USER safe reset')
            current=self._current(body,actor);before=self.helper.snapshot_state()
            transaction={'transaction_id':uuid.uuid4().hex,'operation':operation,'actor_id':actor,
                'grant_id':current['grant_id'],'native_epoch':current['control_epoch'],'before_state_sha256':before['state_sha256']}
            try:
                if recover:
                    _write(self.path,self._envelope(pending=transaction,fault=self.fault))
                    self._initialized(create=True,repair=True)
                else:
                    self._initialized(create=True)
                    _write(self.path,self._envelope(pending=transaction,fault=self.fault))
            except Exception as error:self._fault('INTERLOCK_WAL_WRITE_FAILED:'+str(error));raise
            try:
                with self._locks():
                    actual=self._locked_current(current,actor)
                    result=mutate(actual)
                    after=self.helper.snapshot_state()
                _write(self.path,self._envelope(state=after,last_transaction=transaction))
                self.last_transaction=transaction
                if recover:self.fault=self.fault_id=None
                return result
            except Exception:
                # A rejected, unmodified metadata request can safely seal the
                # same original state; a changed/unknown state stays faulted.
                try:
                    after=self.helper.snapshot_state()
                    if after==before and not recover:
                        _write(self.path,self._envelope(state=before,last_transaction=dict(transaction,rejected_without_mutation=True)))
                    else:self._fault('INTERLOCK_MUTATION_OR_FINAL_PERSISTENCE_UNCERTAIN')
                except Exception:self._fault('INTERLOCK_FINAL_PERSISTENCE_FAILED')
                raise
    def check_publication(self,control_epoch,mode,target,last_command=None):
        """Cached-only; caller already holds original/publication locks."""
        if self.fault is not None:return {'allowed':False,'armed':True,'binding_current':False,
            'reason':'INTERLOCK_PERSISTENCE_UNCERTAIN','interlock_id':self.fault_id,'revision':self.fault_revision}
        if (self.helper.frozen or {}).get('parked_observation_only') is True:
            return {'allowed':False,'armed':self.helper.armed,'binding_current':False,'interlock_id':self.helper.interlock_id,
                'revision':self.helper.revision,'reason':'PARKED_MEASURED_HOLD_REQUIRES_EXPLICIT_NATIVE_PHASE_ACTIVATION'}
        return self.helper.check(permission=self._cached_permission(),control_epoch=control_epoch,mode=mode,target=target,
            last_command=getattr(self.station,'last_command',None) if last_command is None else last_command)
    def status(self):
        result=self.helper.status(self._cached_permission())
        if self.fault is not None:result.update(interlock_id=self.fault_id,revision=self.fault_revision,armed=True,
            binding_current=False,capability_ready=False,phase='PERSISTENCE_UNCERTAIN_REQUIRES_USER_RESET')
        result.update(persistence_fault=self.fault,all_target_writes_blocked=self.fault is not None,
            publication_gate_required=True,original_station_methods_changed_by_wrapper=False,
            direction_profile_choices=[{'profile_id':key,'follower_calibration_sha256':value['follower_calibration_sha256'],
                'basis':value['basis']} for key,value in self.profiles.items()])
        result.update(direction_capture_choices=deepcopy(list(self.captures.values())),direction_capture_errors=deepcopy(self.capture_errors),
            direction_capture_uses_existing_caches_only=True,physical_position_accuracy_known=False)
        result.update(parked_observation_only=(self.helper.frozen or {}).get('parked_observation_only') is True,
            parked_target_writes_allowed=False,parked_is_not_dispatch_authority=True)
        return result
    @staticmethod
    def _native6(value):
        if (not isinstance(value,list) or len(value)!=6 or any(type(item) not in (int,float) or not math.isfinite(item) or
                not LOW[index]<=item<=HIGH[index] for index,item in enumerate(value))):raise ValueError('Actual finite original native6 hold required')
        return deepcopy(value)
    def _hold_origin(self,old,permission):
        frozen=old.get('frozen') or {};sealed=frozen.get('held_successor') or {};origin=sealed.get('origin')
        if origin is not None:
            if (not isinstance(origin,dict) or origin.get('origin_sha256')!=digest({key:value for key,value in origin.items() if key!='origin_sha256'})):
                raise ValueError('Source-owned sealed hold lineage differs')
            raw=origin['raw_receipt']
            current=(self.provider.publication_gate.last_measured_hold if origin['basis']=='original_denial_measured_hold'
                else self.station.last_command)
            if current!=raw:raise ValueError('Original parked hold receipt is no longer current')
            return deepcopy(origin)
        owner=old['owner'];hold=deepcopy(getattr(self.provider.publication_gate,'last_measured_hold',None) or {})
        previous_command=deepcopy(getattr(self.station,'last_command',None))
        if (hold.get('schema')=='explorer_station_observed_denial_measured_hold_v1' and hold.get('grant_id')==owner['grant_id'] and
                hold.get('denied_native_epoch')==owner['control_epoch']):
            if (hold.get('original_position_write_returned') is not True or hold.get('MCU_attainment_verified') is not False or
                    hold.get('old_target_sample_emitted') is not False or hold.get('boot_id')!=self.station.boot or
                    hold.get('clock_domain')!=self.station.clock_domain or type(hold.get('measured_monotonic_ns')) is not int or
                    type(hold.get('write_returned_monotonic_ns')) is not int or
                    not 0<=hold['measured_monotonic_ns']<=hold['write_returned_monotonic_ns']<=self.clock()):
                raise ValueError('Actual original gate-denial measured hold receipt required')
            values=self._native6(hold.get('requested_and_dispatched'));epoch=hold.get('ended_native_epoch');stamp=hold['write_returned_monotonic_ns']
            # A labelled canonical reference view; never invent a Station
            # last_command or action sequence absent from this actual receipt.
            reference={'schema':'explorer_returned_measured_hold_reference_view_v1','operation':'original_denial_measured_hold',
                'control_epoch':epoch,'sequence':None,'requested':values,'dispatched':values,'monotonic_ns':stamp,
                'clock_domain':self.station.clock_domain,'derived_metadata_view_not_station_last_command':True,
                'original_source_receipt_sha256':digest(hold)}
            basis='original_denial_measured_hold'
        else:
            record=self.provider.retired.get(owner['grant_id']);retired=(record or {}).get('data') or {}
            hold=deepcopy(getattr(self.station,'last_command',None) or {});prior=deepcopy(getattr(self.station,'preceding_command',None))
            previous_command=prior
            if (retired.get('revoked_reason')!='EXPLICIT_USER_SCOPED_STOP' or retired.get('binding')!=old['binding'] or
                    any(retired.get(key)!=owner[key] for key in owner) or hold.get('operation')!='hold_current_position' or
                    hold.get('sequence')!=-1 or hold.get('requested')!=hold.get('dispatched') or
                    hold.get('clock_domain')!=self.station.clock_domain or type(hold.get('monotonic_ns')) is not int or
                    not 0<=hold['monotonic_ns']<=self.clock() or getattr(self.station,'last_hold_error',None) is not None or
                    old['armed'] and (not isinstance(prior,dict) or prior.get('control_epoch')!=owner['control_epoch'] or
                        prior.get('clock_domain')!=self.station.clock_domain or type(prior.get('monotonic_ns')) is not int or
                        not 0<=prior['monotonic_ns']<=hold['monotonic_ns'])):
                raise ValueError('Actual original scoped STOP measured-hold receipt and retired owned lineage required; idle is insufficient')
            values=self._native6(hold.get('dispatched'));epoch=hold.get('control_epoch');stamp=hold['monotonic_ns'];reference=hold
            basis='original_scoped_stop_measured_hold'
        origin={'schema':'explorer_station_source_measured_hold_origin_v1','basis':basis,'raw_receipt':hold,
            'reference_view':reference,'previous_source_command':previous_command,'held_native6':values,'held_native_epoch':epoch,'write_returned_native_ns':stamp,
            'boot_id':self.station.boot,'clock_domain':self.station.clock_domain,
            'follower_calibration_sha256':permission['follower_calibration_sha256'],'binding':deepcopy(old['binding']),
            'original_owner':deepcopy(owner),'measurement_time_reported':basis=='original_denial_measured_hold',
            'physical_attainment_or_contact_claimed':False}
        return dict(origin,origin_sha256=digest(origin))
    def _successor(self,permission,body):
        old=self.helper.snapshot_state();self.helper._cas(body.get('interlock_id'),body.get('expected_revision'));owner=old.get('owner') or {}
        if (not owner or old['binding']!=permission['binding'] or permission['native_mode']!='idle' or self.station.mode!='idle' or
                body.get('previous_grant_id')!=owner.get('grant_id') or body.get('previous_control_epoch')!=owner.get('control_epoch') or
                permission['grant_id']==owner.get('grant_id') or any(permission.get(key)!=owner.get(key) for key in
                    ('actor_id','provider_process','boot_id','clock_domain','follower_calibration_sha256')) or
                body.get('observing') is not True or body.get('user_observed_test_confirmed') is not True):
            raise ValueError('Exact previous owner and NEW explicit same-source USER idle grant/binding required')
        origin=self._hold_origin(old,permission);measured=deepcopy(self.station.measurements);now=self.clock();stamp=measured.get('monotonic_ns')
        frame=permission.get('displayed_frame') or {};received=frame.get('received_monotonic_ns');created=permission.get('created_monotonic_ns')
        if (origin.get('held_native_epoch')!=self.station.control_epoch or origin.get('held_native_epoch')!=permission['control_epoch'] or
                origin.get('boot_id')!=self.station.boot or origin.get('clock_domain')!=self.station.clock_domain or
                origin.get('follower_calibration_sha256')!=permission['follower_calibration_sha256'] or origin.get('binding')!=old['binding'] or
                measured.get('clock_domain')!=self.station.clock_domain or type(stamp) is not int or
                not origin['write_returned_native_ns']<=stamp<=now or not 0<=now-stamp<=500_000_000 or
                self._native6(measured.get('follower'))!=origin['held_native6'] or type(received) is not int or type(created) is not int or
                not origin['write_returned_native_ns']<=received<=created<=now or frame.get('boot_id')!=self.station.boot or
                frame.get('clock_domain')!=self.station.clock_domain):
            raise ValueError('Current unchanged fresh measured hold/idle epoch and USER image after actual hold return required')
        previous=old.get('frozen') or {};lineage=deepcopy(previous.get('held_successor') or {
            'origin':origin,'initial_reference':deepcopy(previous),'successions':[]})
        if not isinstance(lineage.get('successions'),list) or len(lineage['successions'])>=32:raise ValueError('Retained successor lineage bound reached')
        identity=uuid.uuid4().hex
        event={'schema':'explorer_station_measured_hold_successor_v1','receipt_id':identity,'event_id':identity,'kind':'EXPLICIT_USER_MEASURED_HOLD_SUCCESSOR',
            'interlock_id':old['interlock_id'],'revision':old['revision']+1,'previous_owner':owner,'new_owner':self.helper._owner(permission),
            'origin_sha256':origin['origin_sha256'],'previous_reference_sha256':previous.get('source_receipt_sha256'),
            'current_measured_snapshot':measured,'displayed_frame':deepcopy(frame),'confirmed_native_ns':now,
            'clock_domain':self.station.clock_domain,'boot_id':self.station.boot,'parked_observation_only':True,
            'retained_armed':old['armed'],'native_commands_sent':0,'execution_authority':False}
        lineage['successions'].append(deepcopy(event));reference=deepcopy(origin['reference_view'])
        old.update(owner=self.helper._owner(permission),revision=old['revision']+1,pending=None,
            frozen={'native6':deepcopy(origin['held_native6']),'original_last_command':reference,'source_receipt_sha256':digest(reference),
                'parked_observation_only':True,'held_successor':lineage},events=(old['events']+[deepcopy(event)])[-64:])
        old['state_sha256']=digest({key:value for key,value in old.items() if key!='state_sha256'})
        self.helper=self._new_helper(old)
        return {'schema':'explorer_station_cooperative_successor_receipt_v1','successor':event,
            'source_hold_origin':deepcopy(origin),'interlock':self.helper.status(permission),
            'parked_observation_only':True,'native_commands_sent':0,'physical_execution_authorized':False}
    def _calibration_resolution(self,permission):
        profile=(self.station.profiles.get('follower') or {});path=profile.get('calibration_path')
        if not isinstance(path,str):raise ValueError('Original verified follower calibration file required for native normalization resolution')
        raw=_raw(Path(path),128*1024)
        if hashlib.sha256(raw).hexdigest()!=permission['follower_calibration_sha256']:
            raise ValueError('Original connected follower calibration SHA changed')
        calibration=json.loads(raw)
        if not isinstance(calibration,dict) or not isinstance(calibration.get('gripper'),dict):
            raise ValueError('Original typed gripper motor calibration required')
        gripper=calibration['gripper'];minimum,maximum=gripper.get('range_min'),gripper.get('range_max')
        if (type(minimum) is not int or type(maximum) is not int or not 0<=minimum<maximum<=4095):
            raise ValueError('Known existing STS3215 position-mode encoder range required; no register reads/writes')
        return 100./(maximum-minimum),hashlib.sha256(raw).hexdigest()
    def observe_direction(self,body,*,authenticated_actor):
        allowed={'grant_id','expected_control_epoch','label','frame_id','displayed_jpeg_sha256','observing',
            'empty_gripper_confirmed','user_observed_test_confirmed'}
        if (set(body)-allowed or body.get('label') not in ('open','empty_open','empty_closed') or
                not isinstance(body.get('frame_id'),str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}',body['frame_id']) or
                not ID.fullmatch(str(body.get('displayed_jpeg_sha256'))) or
                not all(body.get(key) is True for key in ('observing','empty_gripper_confirmed','user_observed_test_confirmed'))):
            raise ValueError('Separate explicit USER empty-open/empty-closed source frame confirmation required; no numeric endpoints')
        with self.metadata_lock:
            current=self._current(body,authenticated_actor)
            uncertainty,calibration_sha=self._calibration_resolution(current)
            frame,metadata=self.provider._frame(body.get('frame_id'),body.get('displayed_jpeg_sha256'))
            with self._locks():
                actual=self._locked_current(current,authenticated_actor);measured=deepcopy(self.station.measurements)
                positions=measured.get('follower');stamp=measured.get('monotonic_ns');now=self.clock()
                if (not isinstance(positions,list) or len(positions)!=6 or any(type(value) not in (int,float) or
                        not math.isfinite(value) or not LOW[i]<=value<=HIGH[i] for i,value in enumerate(positions)) or
                        measured.get('clock_domain')!=self.station.clock_domain or type(stamp) is not int or
                        not frame.monotonic_ns<=stamp<=now or not 0<=now-stamp<=500_000_000 or
                        actual['follower_calibration_sha256']!=calibration_sha):
                    raise ValueError('Actual fresh same-boot cached follower measurement after this frame required')
                label='empty_open' if body['label']=='open' else body['label'];identity=uuid.uuid4().hex
                record={'schema':'explorer_so101_gripper_endpoint_observation_v1','capture_id':identity,'role':'follower',
                    'state_source':'measured_state','measured_native6':positions,'verified_on_connected_hardware':True,
                    'follower_calibration_sha256':calibration_sha,'measurement_uncertainty_native':uncertainty,
                    'uncertainty_basis':'one_encoder_tick_original_range_normalization_only','physical_accuracy_unknown':True,
                    'measurement_stamp':{'monotonic_ns':stamp,'clock_domain':self.station.clock_domain,'boot_id':self.station.boot},
                    'frame':{'frame_id':metadata['frame_id'],'jpeg_sha256':metadata['jpeg_sha256'],
                        'monotonic_ns':frame.monotonic_ns,'clock_domain':self.station.clock_domain,'boot_id':self.station.boot},
                    'operator_confirmation':{'label':label,'observing':True,'empty_gripper_confirmed':True,
                        'user_observed_test_confirmed':True,'actor_id':authenticated_actor},
                    'original_native_epoch':actual['control_epoch'],'source_binding':deepcopy(actual['binding']),
                    'capture_cutoff_monotonic_ns':now,'hardware_commands_sent':0}
                jpeg=frame.jpeg
            if hashlib.sha256(jpeg).hexdigest()!=record['frame']['jpeg_sha256']:raise ValueError('Original cached source JPEG bytes changed')
            if len(jpeg)>2*1024**2:raise ValueError('Retained source JPEG bound exceeded')
            self.capture_root.mkdir(mode=0o700,exist_ok=True)
            if self.capture_root.is_symlink():raise ValueError('Managed source capture directory required')
            files=list(self.capture_root.iterdir())
            if len(list(self.capture_root.glob('*.jpg')))>=64 or sum(path.stat().st_size for path in files if path.is_file())+len(jpeg)>64*1024**2:
                raise ValueError('Retained original direction-capture budget reached; no automatic deletion')
            _original(self.capture_root/(identity+'.jpg'),jpeg)
            _original(self.capture_root/(identity+'.json'),canonical(record))
            self._load_captures();profile_id,blocker=self._profile_from_captures(calibration_sha,authenticated_actor)
            return {'capture_id':identity,'label':label,'source_sha256':self.captures[identity]['source_sha256'],
                'frame_id':record['frame']['frame_id'],'jpeg_sha256':record['frame']['jpeg_sha256'],
                'direction_profile_id':profile_id,'direction_profile_blocker':blocker,
                'physical_position_accuracy_known':False,'hardware_commands_sent':0}
    def _profile_from_captures(self,calibration_sha,actor):
        latest={}
        # Native monotonic stamps are comparable ONLY inside the same boot/domain.
        eligible=[capture for capture in self.captures.values() if capture['follower_calibration_sha256']==calibration_sha and
            capture['boot_id']==self.station.boot and capture['clock_domain']==self.station.clock_domain and capture['actor_id']==actor]
        for capture in sorted(eligible,key=lambda row:(row['monotonic_ns'],row['capture_id'])):latest[capture['label']]=capture
        if set(latest)!={'empty_open','empty_closed'}:return None,'EXPLICIT_EMPTY_OPEN_AND_EMPTY_CLOSED_CURRENT_BOOT_CAPTURES_REQUIRED'
        opened=_raw(self.capture_root/(latest['empty_open']['capture_id']+'.json'),128*1024)
        closed=_raw(self.capture_root/(latest['empty_closed']['capture_id']+'.json'),128*1024)
        if hashlib.sha256(opened).hexdigest()!=latest['empty_open']['source_sha256'] or hashlib.sha256(closed).hexdigest()!=latest['empty_closed']['source_sha256']:
            raise ValueError('Original measured endpoint bytes changed')
        try:profile=observed_gripper_direction(opened,closed)
        except ValueError as error:return None,'OBSERVED_ENDPOINT_PAIR_NOT_QUALIFIED:'+str(error)
        profile.update(physical_position_accuracy_known=False,uncertainty_basis='encoder_quantization_only_not_physical_accuracy',
            source_capture_ids={key:latest[label]['capture_id'] for key,label in (('open','empty_open'),('empty_closed','empty_closed'))})
        profile['profile_sha256']=digest({key:value for key,value in profile.items() if key!='profile_sha256'})
        identity=profile['profile_sha256']
        self.profile_root.mkdir(mode=0o700,exist_ok=True)
        if self.profile_root.is_symlink():raise ValueError('Managed original direction profiles required')
        path=self.profile_root/(identity+'.json')
        if path.exists():
            if _read(path,128*1024)!=profile:raise ValueError('Immutable observed direction profile changed')
        else:
            if len(list(self.profile_root.glob('*.json')))>=64:raise ValueError('Retained direction profile budget reached')
            _original(path,canonical(profile))
        self.profiles={**self.profiles,identity:profile}
        return identity,None
    def dispatch(self,operation,body,*,authenticated_actor=None):
        operation={'finish-challenge':'finish/challenge','reset-safe':'reset_safe'}.get(operation,operation)
        if isinstance(body,dict):
            body=deepcopy(body);actor_field=body.pop('actor_id',None)
            if authenticated_actor is None:authenticated_actor=actor_field
        if not isinstance(body,dict) or not isinstance(authenticated_actor,str) or not authenticated_actor:
            raise ValueError('Authenticated typed metadata operation required')
        if operation=='direction/observe':return self.observe_direction(body,authenticated_actor=authenticated_actor)
        common={'grant_id','expected_control_epoch','expected_revision','interlock_id'}
        extras={'bind':{'direction_profile_id'},'arm':set(),'finish/challenge':{'giver_release_receipt'},
            'finish':{'challenge_id','confirmation'},'reset_safe':{'object_safe_no_dual_hold'},
            'successor':{'previous_grant_id','previous_control_epoch','observing','user_observed_test_confirmed'}}
        if operation not in extras or set(body)-common-extras[operation]:raise ValueError('ID-only known receiver metadata operation required')
        # Profile integrity faults and the transaction must share this lock:
        # otherwise a concurrent ordinary final seal could clear a newer fault.
        # The publication check never takes metadata_lock.
        with self.metadata_lock:
            profile=None
            if operation=='bind' and body.get('direction_profile_id') is not None:
                identity=body['direction_profile_id']
                if not isinstance(identity,str) or not ID.fullmatch(identity) or identity not in self.profiles:
                    raise ValueError('Select an actual server-registered source direction profile')
                try:
                    profile=_read(self.profile_root/(identity+'.json'),128*1024)
                    if profile!=self.profiles[identity]:raise ValueError('Original direction profile changed')
                    self._verify_profile_sources(profile)
                except (ValueError,OSError,KeyError,TypeError) as error:
                    self._fault('DIRECTION_PROFILE_SOURCE_UNCERTAIN:'+str(error));raise
            def mutate(permission):
                if operation=='successor':return self._successor(permission,body)
                if operation in ('bind','arm') and (self.helper.frozen or {}).get('parked_observation_only') is True:
                    raise ValueError('Parked hold requires a separate explicit native phase activation or safe reset')
                if operation=='bind':return self.helper.bind(binding=permission['binding'],permission=permission,
                    direction_profile=profile,expected_revision=body.get('expected_revision'))
                arguments={'interlock_id':body.get('interlock_id'),'expected_revision':body.get('expected_revision'),'permission':permission}
                if operation=='arm':return self.helper.arm(**arguments,last_command=deepcopy(self.station.last_command))
                if operation=='finish/challenge':return self.helper.finish_challenge(**arguments,giver_release_receipt=body.get('giver_release_receipt'))
                if operation=='finish':return self.helper.finish(**arguments,challenge_id=body.get('challenge_id'),confirmation=body.get('confirmation'))
                if self.fault is not None:
                    if (body.get('interlock_id')!=self.fault_id or body.get('expected_revision')!=self.fault_revision or
                            body.get('object_safe_no_dual_hold') is not True or self.station.mode!='idle' or permission['native_mode']!='idle'):
                        raise ValueError('Exact uncertain-state ID/revision and explicit USER safe object under idle required')
                    self.helper=self._new_helper()
                    return self.helper.status(permission)
                return self.helper.reset_safe(**arguments,object_safe_no_dual_hold=body.get('object_safe_no_dual_hold'),
                    actor_id=authenticated_actor,native_mode=self.station.mode)
            return self._transaction(operation,body,authenticated_actor,mutate,recover=operation=='reset_safe' and self.fault is not None)
