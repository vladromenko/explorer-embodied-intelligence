"""ENGINEERING_GLUE: one current-user permission provider for the EXISTING Pi station.

Challenge/auth/check/revoke are cached metadata only. command() calls the SAME
station instance only after a separate explicit user-observed authorization.
No new servo loop, camera, motor setting, lease duration or calibration write.
"""
from collections import OrderedDict
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re
import threading
import time
import uuid

from .contracts import digest

CONTRACT='so101_follower_calibrated_position6_v1'
OPERATIONS=('policy/start','teleop/start','teleop/recenter','action','stop')


def binding(value):
    if not isinstance(value,dict):raise ValueError('Exact server-owned native model/skill binding required')
    allowed={'purpose','skill_id','skill_version','spec_sha256','action_contract_id','model_job_id',
        'checkpoint_content_sha256','source_domain','input_mode','evaluation_id','cooperative_session_id','coordinator_epoch','object_id',
        'source_session_id','demonstration_id'}
    if set(value)-allowed or value.get('purpose') not in ('source04_demo','source04_eval','cooperative05') or value.get('action_contract_id')!=CONTRACT:
        raise ValueError('Known source04/cooperative05 native6 binding required')
    result=deepcopy(value);result['skill_id']=str(uuid.UUID(str(result.get('skill_id'))))
    if type(result.get('skill_version')) is not int or result['skill_version']<1:raise ValueError('Pinned native skill version required')
    if not re.fullmatch('[0-9a-f]{64}',str(result.get('spec_sha256'))):raise ValueError('Pinned original native Spec SHA required')
    mode=result.get('input_mode','policy')
    if result['purpose']=='source04_demo':
        if (mode!='leader' or result.get('source_domain')!='PHYSICAL' or
                any(key in result for key in ('model_job_id','checkpoint_content_sha256','evaluation_id','cooperative_session_id','coordinator_epoch','object_id')) or
                any(not re.fullmatch('[0-9a-f]{32}',str(result.get(key))) for key in ('source_session_id','demonstration_id'))):
            raise ValueError('Source demonstration requires an exact server session/attempt and leader input, without model identity')
    elif any(key in result for key in ('source_session_id','demonstration_id')):
        raise ValueError('Demonstration identity cannot be borrowed by a model/cooperative binding')
    if mode not in ('policy','leader') or result['purpose']=='source04_eval' and mode!='policy':raise ValueError('Explicit native policy/leader input binding required')
    if mode=='policy' and (not re.fullmatch('[0-9a-f]{32}',str(result.get('model_job_id'))) or
            not re.fullmatch('[0-9a-f]{64}',str(result.get('checkpoint_content_sha256')))):
        raise ValueError('Actual pinned native policy job/checkpoint required')
    if result.get('source_domain') not in (None,'PHYSICAL'):raise ValueError('Physical source permission cannot be minted for SIMULATION')
    for key in ('evaluation_id','cooperative_session_id','coordinator_epoch','object_id'):
        if key in result and (not isinstance(result[key],str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}',result[key])):
            raise ValueError('Bounded explicit source/run identity required')
    if result['purpose']=='cooperative05' and not all(result.get(key) for key in ('cooperative_session_id','coordinator_epoch','object_id')):
        raise ValueError('Exact cooperative session/epoch/object required')
    if result['purpose']=='source04_eval' and not re.fullmatch('[0-9a-f]{32}',str(result.get('evaluation_id'))):
        raise ValueError('Exact server-issued source04 attempt identity required; another attempt cannot borrow this grant')
    return result


class StationObservedPermission:
    def __init__(self,station,camera,*,clock=time.monotonic_ns,command_guard=None):
        self.station,self.camera,self.clock=station,camera,clock;self.command_guard=command_guard or (lambda operation:None)
        self.lock=threading.RLock();self.pending=None;self.grant=None;self.retired=OrderedDict();self.revision=0
        self.process=uuid.uuid4().hex
        self.publication_gate=None;self.cooperative_interlock=None
        if getattr(type(station),'observed_publication_hook_version',None)==1:
            from .station_publication_gate import StationPublicationGate
            if getattr(station,'publication_gate',None) is not None:raise ValueError('Original Station already has a publication permission owner')
            self.publication_gate=StationPublicationGate(self);station.publication_gate=self.publication_gate
            from .station_cooperative_interlock import StationCooperativeInterlock
            self.cooperative_interlock=StationCooperativeInterlock(self,Path(station.root)/'cooperative-hold.json')
    def _ttl(self):
        value=self.camera.stale_s
        if type(value) not in (int,float) or not math.isfinite(value) or value<=0:raise ValueError('Existing camera freshness contract required')
        return int(value*1_000_000_000)
    def _frame(self,frame_id=None,expected_sha=None):
        frame=self.camera.latest() if frame_id is None else self.camera.frame_by_id(frame_id)
        if (frame is None or not isinstance(frame.jpeg,bytes) or not 0<len(frame.jpeg)<=8*1024**2 or
                type(frame.monotonic_ns) is not int or not 0<=self.clock()-frame.monotonic_ns<=self._ttl()):
            raise ValueError('Current exact Pi cached camera frame required')
        checksum=hashlib.sha256(frame.jpeg).hexdigest()
        if expected_sha is not None and checksum!=expected_sha:raise ValueError('Displayed Pi JPEG SHA differs from exact cached source')
        return frame,{'camera_id':self.camera.camera_id,'frame_id':frame.frame_id,'jpeg_sha256':checksum,
            'received_monotonic_ns':frame.monotonic_ns,'clock_domain':self.station.clock_domain,'boot_id':self.station.boot,
            'timestamp_basis':getattr(frame,'timestamp_basis','host_receive_estimate'),'exposure_time_verified':False}
    def _native(self):
        state=self.station.status();follower=(state.get('roles') or {}).get('follower') or {}
        if (state.get('available') is not True or not isinstance(state.get('control_epoch'),str) or
                not re.fullmatch('[A-Za-z0-9_.:-]{1,160}',state['control_epoch']) or state.get('mode') not in ('idle','policy','teleoperation') or
                follower.get('verified_on_connected_hardware') is not True or
                not re.fullmatch('[0-9a-f]{64}',str(follower.get('calibration_sha256')))):
            raise ValueError('Original available/calibrated station required; no recalibration or motor writes here')
        return state,follower['calibration_sha256']
    def _demo_leader(self,selected,native,*,fresh=False):
        if selected['purpose']!='source04_demo':return None
        leader=(native.get('roles') or {}).get('leader') or {}
        if leader.get('verified_on_connected_hardware') is not True or not re.fullmatch('[0-9a-f]{64}',str(leader.get('calibration_sha256'))):
            raise ValueError('Existing verified leader calibration/cache required for demonstration')
        if fresh:
            measured=native.get('measurements') or {};stamp=measured.get('monotonic_ns');values=measured.get('leader')
            if (type(stamp) is not int or not 0<=self.clock()-stamp<=self._ttl() or
                    not isinstance(values,list) or len(values)!=6 or
                    any(type(value) not in (int,float) or not math.isfinite(value) or not (-100 if index<5 else 0)<=value<=100 for index,value in enumerate(values))):
                raise ValueError('Fresh genuine existing normalized leader cache required for demonstration')
        return leader['calibration_sha256']
    @staticmethod
    def _data(record):return deepcopy(record['data'])
    def _retire(self,record,reason):
        record['revoked'].set();record['data'].update(execution_permission=False,revoked_reason=reason)
        identity=record['data']['grant_id'];self.retired[identity]=record
        while len(self.retired)>16:self.retired.popitem(last=False)
        if self.grant is record:self.grant=None
    def revoke(self,reason='EXPLICIT_USER_REVOKE',*,expected_grant_id=None,expected_control_epoch=None,expected_binding=None):
        # Per-object Event fences blocked camera/native I/O immediately. A late
        # old revoke cannot set the Event belonging to a newer authorization.
        current=self.grant;pending=self.pending
        if expected_grant_id is not None and (current is None or current['data']['grant_id']!=expected_grant_id):
            return {'revoked':False,'reason':'SUPERSEDED_GRANT','native_command_called':False}
        if current is not None and (expected_control_epoch is not None and current['data']['control_epoch']!=expected_control_epoch or
                expected_binding is not None and current['data']['binding']!=binding(expected_binding)):
            return {'revoked':False,'reason':'SUPERSEDED_NATIVE_EPOCH_OR_MODEL_BINDING','native_command_called':False}
        # This lock covers only an already-admitted position write, never
        # camera/native-command I/O or the original Station lock.
        with self.publication_gate.publication_lock if self.publication_gate is not None else nullcontext():
            if current is not None:current['revoked'].set()
            if expected_grant_id is None and pending is not None:pending['revoked'].set()
        with self.lock:
            self.revision+=1
            if current is not None:self._retire(current,str(reason)[:160])
            if expected_grant_id is None and self.pending is pending:self.pending=None
        return {'revoked':True,'expected_grant_id':expected_grant_id,'native_command_called':False}
    def challenge(self,value):
        selected=binding(value);frame,metadata=self._frame()
        with self.station.lock:
            native,calibration=self._native()
            leader_calibration=self._demo_leader(selected,native,fresh=True)
            if native['mode']!='idle':raise ValueError('Explicit original station STOP/idle required before observed challenge')
            with self.lock:
                if self.grant is not None and not self.grant['revoked'].is_set():raise ValueError('Revoke current observed permission before a new challenge')
                if self.grant is not None:self._retire(self.grant,'PREVIOUS_PERMISSION_REVOKED')
                if self.pending is not None:self.pending['revoked'].set()
                self.revision+=1;identity=uuid.uuid4().hex
                self.pending={'data':{'challenge_id':identity,'binding':selected,'binding_sha256':digest(selected),
                    'control_epoch':native['control_epoch'],'frame':metadata,'follower_calibration_sha256':calibration,
                    'provider_process':self.process},'frame':frame,'revoked':threading.Event()}
                if leader_calibration is not None:self.pending['data']['leader_calibration_sha256']=leader_calibration
                return {'schema':'explorer_station_observed_challenge_v1',**self._data(self.pending),
                    'frame_path':'/station/observed-test/challenges/'+identity+'/frame','execution_permission':False,
                    'native_command_called':False,'required_confirmations':['observing','robot_workspace_checked','user_observed_test_confirmed']}
    def frame(self,challenge_id):
        with self.lock:
            pending=self.pending
            if pending is None or pending['revoked'].is_set() or pending['data']['challenge_id']!=challenge_id:
                raise ValueError('Exact current observed challenge required')
            frame=pending['frame']
            if not 0<=self.clock()-frame.monotonic_ns<=self._ttl():raise ValueError('Pinned Pi display challenge expired')
            return frame.jpeg,deepcopy(pending['data']['frame'])
    def authorize(self,body):
        selected=binding(body.get('binding'));actor=body.get('actor_id')
        if not isinstance(actor,str) or not 0<len(actor)<=128:raise ValueError('Authenticated server-derived operator identity required')
        if not all(body.get(key) is True for key in ('observing','robot_workspace_checked','user_observed_test_confirmed')):
            raise ValueError('Separate explicit USER-observed arm test confirmation required')
        pending=self.pending
        if pending is None:raise ValueError('Current exact displayed Pi challenge required')
        self._frame()  # cached camera remains live; no lock/native command.
        with self.station.lock:
            native,calibration=self._native()
            leader_calibration=self._demo_leader(selected,native,fresh=True)
            with self.lock:
                item=pending['data'];frame=pending['frame']
                if (self.pending is not pending or pending['revoked'].is_set() or self.grant is not None or
                        item['challenge_id']!=body.get('challenge_id') or item['binding']!=selected or
                        item['frame']['jpeg_sha256']!=body.get('displayed_jpeg_sha256') or
                        item['control_epoch']!=body.get('expected_control_epoch') or native['control_epoch']!=item['control_epoch'] or
                        native['mode']!='idle' or calibration!=item['follower_calibration_sha256'] or
                        leader_calibration!=item.get('leader_calibration_sha256') or
                        not 0<=self.clock()-frame.monotonic_ns<=self._ttl()):
                    raise ValueError('Observed challenge revoked/expired or native source/model binding changed')
                now=self.clock();data={'schema':'explorer_station_observed_grant_v1','grant_id':uuid.uuid4().hex,
                    'challenge_id':item['challenge_id'],
                    'binding':selected,'binding_sha256':digest(selected),'actor_id':actor,'control_epoch':native['control_epoch'],
                    'native_mode':'idle','follower_calibration_sha256':calibration,'provider_process':self.process,
                    'clock_domain':self.station.clock_domain,'boot_id':self.station.boot,'created_monotonic_ns':now,
                    'observed_monotonic_ns':now,'observation_deadline_ns':frame.monotonic_ns+self._ttl(),
                    'displayed_frame':deepcopy(item['frame']),'execution_permission':True,'hardware_success_claimed':False,
                    'permission_scope':'explicit_current_operator_not_camera_or_measured_pose_alone'}
                if leader_calibration is not None:data['leader_calibration_sha256']=leader_calibration
                self.grant={'data':data,'revoked':threading.Event()};self.pending=None;self.revision+=1
                return self._data(self.grant)
    def _current_locked(self,grant_id,selected,native,calibration,expected_epoch=None):
        record=self.grant
        if (record is None or record['revoked'].is_set() or record['data']['grant_id']!=grant_id or
                record['data']['binding']!=selected):raise ValueError('Exact current observed grant/model binding required')
        data=record['data']
        if expected_epoch is not None and data['control_epoch']!=expected_epoch:raise ValueError('Old request cannot operate/revoke newer native epoch')
        try:leader_calibration=self._demo_leader(selected,native)
        except ValueError:
            self._retire(record,'DEMONSTRATION_LEADER_CALIBRATION_UNAVAILABLE');raise
        if (data['control_epoch']!=native['control_epoch'] or
                data['native_mode']!=native['mode'] or calibration!=data['follower_calibration_sha256'] or
                leader_calibration!=data.get('leader_calibration_sha256') or
                data['boot_id']!=self.station.boot or not self.clock()<data['observation_deadline_ns']):
            self._retire(record,'NATIVE_EPOCH_MODE_CALIBRATION_OR_OBSERVATION_EXPIRED')
            raise ValueError('Current native epoch/mode or explicit operator observation expired; new challenge required')
        return record
    def check(self,grant_id,value,expected_control_epoch=None):
        selected=binding(value)
        try:self._frame()
        except (ValueError,OSError):self.revoke('CAMERA_SOURCE_UNAVAILABLE',expected_grant_id=grant_id,expected_binding=selected);raise
        with self.station.lock:
            try:native,calibration=self._native()
            except (ValueError,OSError):self.revoke('NATIVE_SOURCE_UNAVAILABLE',expected_grant_id=grant_id,expected_binding=selected);raise
            with self.lock:
                record=self._current_locked(grant_id,selected,native,calibration,expected_control_epoch)
                result=self._data(record);result['remaining_observation_s']=max(0,(result['observation_deadline_ns']-self.clock())/1e9)
                return result
    def observe(self,body):
        selected=binding(body.get('binding'));grant_id=body.get('grant_id')
        if body.get('observing') is not True:raise ValueError('Fresh explicit browser/operator observation required')
        if (not isinstance(body.get('frame_id'),str) or not body['frame_id'] or
                not re.fullmatch('[0-9a-f]{64}',str(body.get('displayed_jpeg_sha256'))) or not body.get('expected_control_epoch')):
            raise ValueError('Exact current displayed frame/SHA/native epoch required; sticky observation booleans are insufficient')
        frame,metadata=self._frame(body.get('frame_id'),body.get('displayed_jpeg_sha256'))
        self._frame()
        with self.station.lock:
            native,calibration=self._native()
            with self.lock:
                record=self._current_locked(grant_id,selected,native,calibration,body.get('expected_control_epoch'))
                if body.get('actor_id')!=record['data']['actor_id']:raise ValueError('Current authenticated observer identity differs')
                record['data'].update(observed_monotonic_ns=self.clock(),observation_deadline_ns=frame.monotonic_ns+self._ttl(),displayed_frame=metadata)
                return self._data(record)
    def command(self,grant_id,value,operation,body):
        selected=binding(value)
        if operation not in OPERATIONS or not isinstance(body,dict):raise ValueError('Known native station operation/body required')
        if operation=='stop':return self._stop(grant_id,selected,body)
        mode=selected.get('input_mode','policy')
        if (operation.startswith('policy/') or operation=='action') and mode!='policy' or operation.startswith('teleop/') and mode!='leader':
            raise ValueError('Native operation must match the explicitly observed model/input binding')
        self.command_guard(operation);self._frame()
        with self.station.lock:
            native,calibration=self._native()
            with self.lock:
                record=self._current_locked(grant_id,selected,native,calibration,body.get('expected_control_epoch',body.get('control_epoch')))
                if operation in ('policy/start','teleop/start') and native['mode']!='idle':raise ValueError('Explicit original STOP/idle required before a new native start')
                if operation in ('policy/start','teleop/start') and self.cooperative_interlock is not None:
                    self.cooperative_interlock.require_start_admission(self._data(record),operation)
                epoch=record['data']['control_epoch'];payload=deepcopy(body)
                payload.update(observing=True,operator_present=False)
                if operation in ('policy/start','teleop/start','teleop/recenter'):payload['expected_control_epoch']=epoch
                else:payload['control_epoch']=epoch
                try:
                    result=self.station.command(operation,payload)
                    if (not isinstance(result,dict) or result.get('control_epoch')!=self.station.control_epoch or
                            result.get('mode')!=self.station.mode):raise ValueError('Original native result epoch/mode correlation unavailable')
                except Exception:
                    self._retire(record,'ORIGINAL_NATIVE_COMMAND_RETURN_UNKNOWN')
                    # Still under the same original RLock: nobody can have
                    # installed a newer owner between this call and cleanup.
                    try:self.station.command('stop',{'expected_control_epoch':self.station.control_epoch})
                    except Exception:record['data']['scoped_stop_return_unknown']=True
                    raise
                new_epoch=result.get('control_epoch')
                if record['revoked'].is_set() or self.grant is not record or not self.clock()<record['data']['observation_deadline_ns']:
                    # One already-entered bounded native call may have returned.
                    # Cleanup is scoped to its exact new epoch, never a new user.
                    if self.station.control_epoch==new_epoch:
                        self.station.command('stop',{'expected_control_epoch':new_epoch})
                    self._retire(record,'LATE_NATIVE_COMMAND_AFTER_PERMISSION_REVOCATION')
                    raise ValueError('Late native command revoked and scoped stopped; no permission replay')
                record['data'].update(control_epoch=new_epoch,native_mode=result.get('mode'))
                return {'status':result,'grant':self._data(record),'native_command_called':True,
                    'dispatch_claimed_from_HTTP_acceptance':False}
    def _stop(self,grant_id,selected,body):
        record=self.grant if self.grant and self.grant['data']['grant_id']==grant_id else self.retired.get(grant_id)
        if record is None or record['data']['binding']!=selected:raise ValueError('Exact owned current/revoked grant required for scoped STOP')
        if not body.get('expected_control_epoch') or body['expected_control_epoch']!=record['data']['control_epoch']:
            raise ValueError('Old STOP cannot revoke a newer native epoch; exact expected_control_epoch required')
        with self.publication_gate.publication_lock if self.publication_gate is not None else nullcontext():
            record['revoked'].set()
        with self.station.lock:
            with self.lock:
                if (self.grant is not None and self.grant is not record or self.station.control_epoch!=record['data']['control_epoch'] or
                        body.get('expected_control_epoch',record['data']['control_epoch'])!=record['data']['control_epoch']):
                    raise ValueError('Old STOP cannot affect a newer grant/native owner epoch')
                result=self.station.command('stop',{'expected_control_epoch':record['data']['control_epoch']})
                self._retire(record,'EXPLICIT_USER_SCOPED_STOP')
                return {'status':result,'grant':self._data(record),'native_command_called':True,'dispatch_claimed_from_HTTP_acceptance':False}
    def status(self):
        current=self.grant
        valid=False;reason=None
        if current is not None:
            try:self.check(current['data']['grant_id'],current['data']['binding']);valid=True
            except (ValueError,OSError) as error:reason=str(error)
        with self.lock:
            valid=bool(valid and self.grant is current and current and not current['revoked'].is_set() and
                self.clock()<current['data']['observation_deadline_ns'])
            grant=self._data(current) if valid else None
        result={'schema':'explorer_station_observed_permission_status_v1','execution_permission':valid,
            'grant':grant,
            'native_publication_permission_gate_installed':self.publication_gate is not None,
            'publication_gate':self.publication_gate.status() if self.publication_gate is not None else None,
            'cooperative_hold_interlock':self.cooperative_interlock.status() if self.cooperative_interlock is not None else None,
            'provider_process':self.process,'reason':reason,'native_command_called':False,
            'freshness_basis':'existing_camera_stale_s_not_motor_lease','controller_settings_written':False,
            'permission_is_not_grasp_stationarity_or_physical_success':True}
        result['source_stamp']={'monotonic_ns':self.clock(),'boot_id':self.station.boot,'clock_domain':self.station.clock_domain}
        return result

    def owns_native_epoch(self):
        """Metadata ownership, including revoked tests awaiting original STOP.

Caller holds the original station RLock around this and any legacy command.
An old ungated policy client must not borrow this epoch after permission loss.
"""
        with self.lock:
            records=([self.grant] if self.grant else [])+list(self.retired.values())
            return any(record['data']['control_epoch']==self.station.control_epoch for record in records)
