"""ENGINEERING_GLUE: durable metadata wrapper for the COMMON native receiver.

No native command, camera owner or driver. File operations occur only during
construction/explicit metadata requests, outside Station/publication locks.
check_publication reads cached state only and must precede legacy early-return.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from .contracts import canonical,digest
from .cooperative_receiver_interlock05 import ReceiverHoldInterlock,PROFILE_SCHEMA

ENVELOPE='explorer_station_cooperative_interlock_wal_v1'
LIMIT=1024*1024
ID=re.compile('[0-9a-f]{64}')


def _read(path,limit=LIMIT):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        meta=os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_size>limit:raise ValueError('Bounded regular interlock metadata required')
        raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('Interlock metadata bound exceeded')
    return json.loads(raw)


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
        self.profile_root=self.path.parent/'cooperative-direction-profiles';self.profiles={}
        self.helper=self._new_helper();self.last_transaction=None
        try:
            if self.path.parent.resolve()!=Path(self.station.root).resolve() or self.path.is_symlink():
                raise ValueError('Fixed original Station state directory required')
            if self.path.exists():
                envelope=_read(self.path)
                if envelope.get('schema')!=ENVELOPE or envelope.get('envelope_sha256')!=digest({key:value for key,value in envelope.items() if key!='envelope_sha256'}):
                    raise ValueError('Interlock WAL integrity/schema differs')
                if envelope.get('pending') is not None or envelope.get('fault') is not None:
                    raise ValueError('Pending/failed interlock WAL must not restore an unarmed default')
                self.helper=self._new_helper(envelope['state']);self.last_transaction=envelope.get('last_transaction')
            self._load_profiles()
        except (ValueError,OSError,KeyError,TypeError) as error:self._fault('INTERLOCK_PERSISTENCE_UNCERTAIN:'+str(error))
    def _new_helper(self,state=None):
        return ReceiverHoldInterlock(clock_domain=self.station.clock_domain,boot_id=self.station.boot,clock=self.clock,state=state)
    def _fault(self,reason):
        if self.fault is None:self.fault_id=uuid.uuid4().hex;self.fault_revision+=1
        self.fault=str(reason)[:1000]
    def _load_profiles(self):
        if not self.profile_root.exists():return
        if self.profile_root.is_symlink():raise ValueError('Managed direction profiles required')
        paths=list(self.profile_root.glob('*.json'))
        if len(paths)>64:raise ValueError('Observed direction profile inventory bound exceeded')
        profiles={}
        for path in paths:
            if not ID.fullmatch(path.stem):raise ValueError('Only source direction profile IDs are allowed')
            profile=_read(path,128*1024)
            if (profile.get('schema')!=PROFILE_SCHEMA or not ID.fullmatch(str(profile.get('follower_calibration_sha256'))) or
                    not isinstance(profile.get('basis'),str) or profile.get('profile_sha256')!=path.stem or
                    digest({key:value for key,value in profile.items() if key!='profile_sha256'})!=path.stem):
                raise ValueError('Original direction profile digest differs')
            profiles[path.stem]=profile
        self.profiles=profiles
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
            try:_write(self.path,self._envelope(pending=transaction,fault=self.fault))
            except Exception as error:self._fault('INTERLOCK_WAL_WRITE_FAILED:'+str(error));raise
            try:
                with self._locks():
                    actual=self._locked_current(current,actor)
                    result=mutate(actual)
                    after=self.helper.snapshot_state()
                _write(self.path,self._envelope(state=after,last_transaction=transaction))
                self.last_transaction=transaction;self.fault=self.fault_id=None
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
        return result
    def dispatch(self,operation,body,*,authenticated_actor=None):
        operation={'finish-challenge':'finish/challenge','reset-safe':'reset_safe'}.get(operation,operation)
        if isinstance(body,dict):
            body=deepcopy(body);actor_field=body.pop('actor_id',None)
            if authenticated_actor is None:authenticated_actor=actor_field
        if not isinstance(body,dict) or not isinstance(authenticated_actor,str) or not authenticated_actor:
            raise ValueError('Authenticated typed metadata operation required')
        common={'grant_id','expected_control_epoch','expected_revision','interlock_id'}
        extras={'bind':{'direction_profile_id'},'arm':set(),'finish/challenge':{'giver_release_receipt'},
            'finish':{'challenge_id','confirmation'},'reset_safe':{'object_safe_no_dual_hold'}}
        if operation not in extras or set(body)-common-extras[operation]:raise ValueError('ID-only known receiver metadata operation required')
        profile=None
        if operation=='bind' and body.get('direction_profile_id') is not None:
            identity=body['direction_profile_id']
            if not isinstance(identity,str) or not ID.fullmatch(identity) or identity not in self.profiles:
                raise ValueError('Select an actual server-registered source direction profile')
            # Re-read original descriptor only for the explicit request, never
            # from status/write admission; modified originals fail closed.
            try:
                profile=_read(self.profile_root/(identity+'.json'),128*1024)
                if profile!=self.profiles[identity]:raise ValueError('Original direction profile changed')
            except (ValueError,OSError,KeyError,TypeError) as error:
                self._fault('DIRECTION_PROFILE_SOURCE_UNCERTAIN:'+str(error));raise
        def mutate(permission):
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
