"""ENGINEERING_GLUE: official LeRobot/Feetech station adapter.

Uses the original, register-verified SO-101 calibrations. Never writes calibration,
PID, current limits, speed limits or firmware. Only operational position goals and
torque enable for requested position control are sent. Disconnect retains torque.
"""
from dataclasses import asdict
import fcntl
import hashlib
import json
import math
import queue
from pathlib import Path
import re
import threading
import time
import uuid

NAMES = ('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')
LOW = [-100.0] * 5 + [0.0]
HIGH = [100.0] * 6
UNITS = ['calibrated_normalized_-100_100'] * 5 + ['normalized_0_100']


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.replace(path)


def _record_context(body):
    """Optional source-declared teaching identity; never hardware configuration."""
    fields=('skill_id','skill_version','skill_spec_sha256','scene_id','session_id','phase_id','phase','context','task_type')
    context={key:body[key] for key in fields if key in body}
    if any(key in context and (not isinstance(context[key],str) or not context[key] or len(context[key])>240)
           for key in fields if key not in ('skill_version','context','phase')):
        raise ValueError('Named recording source context must contain bounded nonempty text')
    if 'skill_version' in context and (type(context['skill_version']) is not int or context['skill_version']<1):
        raise ValueError('Recording skill version must be a positive integer')
    if 'skill_id' in context or 'skill_version' in context:
        if 'skill_id' not in context or 'skill_version' not in context:raise ValueError('Record skill ID and version together')
    if 'context' in context and not isinstance(context['context'],dict):raise ValueError('Recording context must be an object')
    if 'phase' in context and not isinstance(context['phase'],(str,dict)):raise ValueError('Recording phase must be text or an object')
    raw=json.dumps(context,ensure_ascii=False,allow_nan=False)
    if len(raw.encode())>8192:raise ValueError('Recording source context exceeds 8KiB')
    return json.loads(raw)


class SO101Station:
    def __init__(self, configuration, state_dir, camera):
        self.configuration = json.loads(Path(configuration).read_text())
        self.root = Path(state_dir) / 'station'
        self.root.mkdir(parents=True, exist_ok=True)
        self.camera = camera
        self.lock = threading.RLock()
        self.bus_lock = threading.RLock()
        self.finish_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.buses, self.owners, self.profiles = {}, [], {}
        self.measurements = {}
        self.mode, self.error, self.record = 'idle', None, None
        self.finishing_record, self.last_finished = None, None
        self.record_queue = queue.Queue(maxsize=64)
        self.record_errors = {}
        self.observation_warning = None
        self.operator_present = False
        self.baseline_leader, self.baseline_follower = None, None
        self.explicit_goal = None
        self.last_command = None
        self.preceding_command = None
        self.action_source = None
        self.last_hold_error = None
        self.control_epoch = uuid.uuid4().hex
        self.action_sequence = -1
        self.goal_deadline = 0
        self.last_periods = []
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.clock_domain = 'pi:' + self.boot
        for path in self.root.glob('recordings/*/manifest.json'):
            try:
                data = json.loads(path.read_text())
                if data.get('state') == 'recording':
                    journal = path.with_name('samples.jsonl')
                    if journal.is_file():
                        recovered=[]
                        for line in journal.read_text().splitlines():
                            try:
                                row=json.loads(line)
                                image=path.parent/row['frame']['file']
                                if hashlib.sha256(image.read_bytes()).hexdigest()!=row['frame']['sha256']:
                                    raise ValueError('Interrupted source image hash mismatch')
                                recovered.append(row)
                            except (OSError,ValueError,KeyError):
                                data['recovery_note']='An incomplete journal tail was excluded'
                                break
                        data['samples']=recovered
                        data['queued_samples']=len(recovered)
                    data.update(state='interrupted',outcome='unknown')
                    save(path,data)
            except (OSError,ValueError):
                pass

    def start(self):
        self.writer = threading.Thread(target=self._record_writer,name='so101-record-writer',daemon=True)
        self.writer.start()
        self.thread = threading.Thread(target=self._work, name='so101-station', daemon=True)
        self.thread.start()

    def _connect(self):
        from lerobot.motors.feetech import FeetechMotorsBus
        from lerobot.motors import Motor, MotorCalibration, MotorNormMode
        for role in ('leader','follower'):
            settings = self.configuration['roles'][role]
            profile_file = Path(settings['calibration'])
            raw = json.loads(profile_file.read_text())
            owner = (self.root / (role + '.lock')).open('w')
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owners.append(owner)
            motors = {name:Motor(raw[name]['id'],'sts3215',MotorNormMode.RANGE_0_100 if name == 'gripper' else MotorNormMode.RANGE_M100_100) for name in NAMES}
            bus = FeetechMotorsBus(settings['port'],motors,calibration={name:MotorCalibration(**raw[name]) for name in NAMES})
            self.buses[role] = bus
            bus.connect()
            if {name:asdict(value) for name,value in bus.read_calibration().items()} != raw:
                raise ValueError(role + ' calibration no longer matches connected motors')
            if any(value != 0 for value in bus.sync_read('Operating_Mode',normalize=False).values()):
                raise ValueError(role + ' is not in its existing position mode')
            with self.lock:
                self.profiles[role] = dict(port=settings['port'],calibration_sha256=hashlib.sha256(profile_file.read_bytes()).hexdigest(),
                    calibration_path=str(profile_file),verified_on_connected_hardware=True)

    def _positions(self, role):
        with self.bus_lock:
            values = self.buses[role].sync_read('Present_Position')
        return [float(values[name]) for name in NAMES]

    def _write(self, target):
        with self.bus_lock:
            self.buses['follower'].sync_write('Goal_Position',dict(zip(NAMES,target)))

    def _enable_hold(self):
        with self.bus_lock:
            self._enable_hold_locked()

    def _enable_hold_locked(self):
        bus = self.buses['follower']
        present = bus.sync_read('Present_Position',normalize=False)
        bus.sync_write('Goal_Position',present,normalize=False)
        torque = bus.sync_read('Torque_Enable',normalize=False)
        disabled = {name:1 for name,value in torque.items() if value == 0}
        if disabled:
            bus.sync_write('Torque_Enable',disabled,normalize=False)

    def _work(self):
        try:
            self._connect()
            previous = time.monotonic()
            while not self.stop_event.is_set():
                begin = time.monotonic()
                frame = self.camera.latest()
                with self.lock:
                    leader, follower = self._positions('leader'), self._positions('follower')
                    measured_at = time.monotonic_ns()
                    self.measurements = dict(leader=leader,follower=follower,monotonic_ns=measured_at,wall_utc=time.time(),clock_domain=self.clock_domain)
                    if frame is not None:
                        self.observation_warning = None
                    if self.mode != 'idle' and frame is None and not self.operator_present:
                        self._end_session()
                        self._write(follower)
                        self.observation_warning = 'Camera observation is unavailable; follower holds its measured position'
                    target = None
                    if self.mode == 'teleoperation':
                        target = [self.baseline_follower[i] + leader[i] - self.baseline_leader[i] for i in range(6)]
                    elif self.mode == 'policy' and begin <= self.goal_deadline:
                        target = self.explicit_goal
                    elif self.mode == 'policy':
                        self._end_session()
                        self._write(follower)
                    if target is not None:
                        accepted = [min(HIGH[i],max(LOW[i],float(value))) for i,value in enumerate(target)]
                        self._write(accepted)
                        dispatched_at = time.monotonic_ns()
                        self.last_command=dict(control_epoch=self.control_epoch,sequence=self.action_sequence,requested=list(target),
                            dispatched=accepted,monotonic_ns=dispatched_at,clock_domain=self.clock_domain,
                            source_observation=dict(self.action_source or {}))
                        self._sample(frame,follower,target,accepted,measured_at,dispatched_at)
                self.last_periods.append(begin - previous)
                self.last_periods = self.last_periods[-300:]
                previous = begin
                self.stop_event.wait(max(0,1/30 - (time.monotonic()-begin)))
        except Exception as error:
            with self.lock:
                self.error = type(error).__name__ + ': ' + str(error)
                self._end_session()
                try:
                    self._write(self._positions('follower'))
                    self.last_hold_error=None
                except Exception as hold_error:
                    self.last_hold_error=type(hold_error).__name__+': '+str(hold_error)
            if self.record:
                self._finish_record('unknown','interrupted')
        finally:
            for bus in self.buses.values():
                if bus.is_connected:
                    bus.disconnect(disable_torque=False)
            for owner in self.owners:
                owner.close()

    def status(self):
        with self.lock:
            periods = sorted(self.last_periods[1:])
            return dict(available=len(self.profiles)==2 and self.error is None,mode=self.mode,error=self.error,control_epoch=self.control_epoch,
                observation_warning=self.observation_warning,roles={name:dict(value) for name,value in self.profiles.items()},measurements=dict(self.measurements),
                measurement_age_ms=(time.monotonic_ns()-self.measurements['monotonic_ns'])/1e6 if self.measurements else None,
                loop_hz=1/(sum(periods)/len(periods)) if periods and sum(periods)>0 else None,
                period_p95_ms=1000*periods[int(.95*(len(periods)-1))] if periods else None,
                recording={key:value for key,value in self.record.items() if key!='samples'} if self.record else None,
                recording_error=self.record_errors.get(self.record['episode_id']) if self.record else None,
                last_command=dict(self.last_command) if self.last_command else None,
                preceding_command=dict(self.preceding_command) if self.preceding_command else None,
                last_hold_error=self.last_hold_error,
                action_names=NAMES,action_units=UNITS,position_hold='existing_position_mode',configuration_writes=0)

    def command(self, operation, body):
        if operation == 'record/finish':
            return self._finish_record(body.get('outcome','unknown'))
        with self.lock:
            if operation == 'stop':
                if body.get('expected_control_epoch') is not None and body['expected_control_epoch']!=self.control_epoch:
                    raise RuntimeError('The requested control session has already ended')
                holding=self.mode!='idle' and bool(self.measurements)
                self.preceding_command=dict(self.last_command) if self.last_command else None
                self._end_session()
                if holding:
                    try:
                        measured=self._positions('follower')
                        self._write(measured)
                        self.last_command=dict(operation='hold_current_position',control_epoch=self.control_epoch,sequence=-1,
                            requested=measured,dispatched=measured,monotonic_ns=time.monotonic_ns(),clock_domain=self.clock_domain,source_observation={})
                        self.last_hold_error=None
                    except Exception as error:
                        self.last_hold_error=type(error).__name__+': '+str(error)
                        raise RuntimeError('Control session ended; measured-position hold was not confirmed') from error
                return self.status()
            if operation == 'record/start':
                return self._start_record(body)
            if not self.status()['available']:
                raise RuntimeError(self.error or 'Arm connections are not ready')
            if not body.get('observing') or (self.camera.latest() is None and not body.get('operator_present')):
                raise ValueError('This operation needs the requested camera observation or confirmed operator presence')
            if operation in ('teleop/start','policy/start','teleop/recenter') and body.get('expected_control_epoch') != self.control_epoch:
                raise ValueError('Station changed after this control request was prepared; request a fresh operation')
            if operation == 'teleop/start':
                self._enable_hold()
                self.baseline_leader = self._positions('leader')
                self.baseline_follower = self._positions('follower')
                self.mode = 'teleoperation'
                self.action_source = None
                self.control_epoch = uuid.uuid4().hex
                self.action_sequence = -1
            elif operation == 'policy/start':
                self._enable_hold()
                self.explicit_goal = self._positions('follower')
                self.goal_deadline = time.monotonic()+1
                self.control_epoch = uuid.uuid4().hex
                self.action_sequence = -1
                self.action_source = None
                self.mode = 'policy'
            elif operation == 'teleop/recenter':
                if self.mode != 'teleoperation':
                    raise ValueError('Recenter belongs to an active leader/follower session')
                self.baseline_leader = self._positions('leader')
                self.baseline_follower = self._positions('follower')
                self.control_epoch = uuid.uuid4().hex
            elif operation == 'action':
                sequence = body.get('sequence')
                if body.get('control_epoch') != self.control_epoch or self.mode!='policy' or not isinstance(sequence,int) or isinstance(sequence,bool) or sequence<=self.action_sequence:
                    raise ValueError('This action belongs to an ended or superseded control session')
                target = body.get('action')
                if body.get('action_names') != list(NAMES) or body.get('action_units') != UNITS:
                    raise ValueError('Policy action contract does not match this station')
                if not isinstance(target,list) or len(target)!=6 or not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in target):
                    raise ValueError('Six finite calibrated SO-101 coordinates are required')
                source_time=body.get('source_frame_monotonic_ns')
                if not isinstance(source_time,int) or body.get('source_clock_domain')!=self.clock_domain:
                    raise ValueError('Policy action is missing the original Pi camera clock')
                source_age=(time.monotonic_ns()-source_time)/1e9
                if not 0<=source_age<=self.camera.stale_s:
                    raise ValueError('Policy action refers to a future or expired camera observation')
                duration=body.get('duration_s',.25)
                if not isinstance(duration,(int,float)) or isinstance(duration,bool) or not math.isfinite(duration) or duration<=0:
                    raise ValueError('Action duration must be a positive finite number')
                self.action_sequence = sequence
                self.action_source=dict(frame_id=body.get('source_frame_id'),monotonic_ns=source_time,clock_domain=self.clock_domain)
                self.explicit_goal = [float(value) for value in target]
                self.goal_deadline = time.monotonic() + min(1,max(.05,duration))
                self.mode = 'policy'
            else:
                raise ValueError('Unknown station operation')
            self.operator_present = bool(body.get('operator_present'))
            return self.status()

    def _end_session(self):
        self.mode='idle'
        self.operator_present=False
        self.control_epoch=uuid.uuid4().hex
        self.action_sequence=-1
        self.action_source=None
        self.explicit_goal=None

    def _start_record(self, body):
        if self.mode == 'idle':
            raise ValueError('Start an observed teleoperation or policy before recording action labels')
        if self.record or self.finishing_record:
            raise ValueError('A station recording is already active')
        context=_record_context(body)
        ident = uuid.uuid4().hex
        folder = self.root/'recordings'/ident
        folder.mkdir(parents=True)
        self.record = dict(schema_version=1,episode_id=ident,source_type='station_demonstration',
            state_basis='measured_state',action_basis='dispatched_command',action_names=list(NAMES),action_units=UNITS,
            normalization={'low':LOW,'high':HIGH},fps=10,split='train',project_id=body.get('project_id','personal'),
            name=str(body.get('name','SO-101 recording'))[:80],state='recording',outcome='unknown',samples=[],queued_samples=0,
            task=str(body.get('task',body.get('name','Imitate the recorded robot demonstration')))[:240],
            demonstration_actor=str(body.get('actor','human_operator'))[:80],execution_mode=self.mode,
            calibration_metadata=json.loads(json.dumps(self.profiles,allow_nan=False)),clock_domain=self.clock_domain,started_wall_utc=time.time(),
            boot_id=self.boot,recording_provenance_version=2,source_context=context,
            source_context_basis='operator_or_server_declared_not_independent_task_success',
            producer_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),producer_runtime_binary_identity_verified=False,
            camera_source=dict(camera_id=getattr(self.camera,'camera_id',None),device=getattr(self.camera,'device',None),
                identity_basis='existing_capture_source_configuration_not_independent_hardware_serial_verification',
                camera_calibration_available=False),camera_calibration=None)
        for key in ('skill_id','skill_version','skill_spec_sha256','scene_id','session_id','phase_id','phase','task_type'):
            if key in context:self.record[key]=context[key]
        save(folder/'manifest.json',self.record)
        self.last_sample_ns = 0
        return dict(episode_id=ident,state='recording',project_id=self.record['project_id'])

    def _sample(self, frame, state, requested, accepted, measured_at, dispatched_at):
        if self.record and frame and measured_at-self.last_sample_ns >= 95_000_000:
            if frame.monotonic_ns > measured_at:
                raise ValueError('Frame is from the future relative to measured state')
            folder = self.root/'recordings'/self.record['episode_id']
            index = self.record.get('queued_samples',0)
            filename = f'{index:06d}.jpg'
            # These are snapshots from the existing cycle, not additional bus reads.
            measurement=self.measurements
            leader=measurement.get('leader')
            leader_valid=(measurement.get('monotonic_ns')==measured_at and measurement.get('clock_domain')==self.clock_domain and measurement.get('follower')==list(state)
                and isinstance(leader,list) and len(leader)==6 and all(type(value) in (int,float) and math.isfinite(value) for value in leader))
            command=self.last_command or {}
            dispatched=(command.get('monotonic_ns')==dispatched_at and command.get('clock_domain')==self.clock_domain and command.get('control_epoch')==self.control_epoch
                and command.get('dispatched')==list(accepted) and command.get('requested')==list(requested))
            receipt=dict(receipt_id=('host-write:'+self.boot+':'+str(dispatched_at)) if dispatched else None,available=dispatched,stage='host_bus_write_completed' if dispatched else None,
                monotonic_ns=dispatched_at if dispatched else None,clock_domain=self.clock_domain,boot_id=self.boot,
                control_epoch=command.get('control_epoch') if dispatched else None,sequence=command.get('sequence') if dispatched else None,
                source_observation=dict(command.get('source_observation') or {}) if dispatched else None,
                basis='existing_sync_write_returned_before_this_sample' if dispatched else 'no_matching_host_write_completion',
                MCU_acknowledgement_verified=False,motor_application_time_verified=False,attainment_verified=False)
            sample = dict(frame=dict(file=filename,sha256=hashlib.sha256(frame.jpeg).hexdigest(),
                frame_id=frame.frame_id,monotonic_ns=frame.monotonic_ns,clock_domain=self.clock_domain,
                camera_id=getattr(self.camera,'camera_id',None),timestamp_basis=getattr(frame,'timestamp_basis',None),
                width=getattr(frame,'width',None),height=getattr(frame,'height',None),
                exposure_timestamp_available=False),
                state=list(state),action=list(accepted),requested_action=list(requested),accepted_action=list(accepted),
                dispatched_action=list(accepted) if dispatched else None,dispatch_receipt=receipt,
                leader_state=list(leader) if leader_valid else None,leader_state_monotonic_ns=measured_at if leader_valid else None,
                leader_state_clock_domain=self.clock_domain if leader_valid else None,
                measured_follower=dict(values=list(state),monotonic_ns=measured_at,clock_domain=self.clock_domain,
                    timing='before_current_action_not_attainment_of_its_target'),
                next_measured_follower=None,next_measured_follower_available=False,
                source_valid_masks=dict(leader_state=[leader_valid]*6,follower_measured=[True]*6,
                    requested_action=[True]*6,accepted_action=[True]*6,dispatched_action=[dispatched]*6),
                source_context=json.loads(json.dumps(self.record.get('source_context') or {},allow_nan=False)),
                boot_id=self.boot,state_monotonic_ns=measured_at,action_monotonic_ns=dispatched_at,clock_domain=self.clock_domain)
            if self.record['episode_id'] not in self.record_errors:
                try:
                    self.record_queue.put_nowait((self.record,folder,filename,frame.jpeg,sample))
                    self.record['queued_samples'] = index+1
                    self.last_sample_ns = measured_at
                except queue.Full:
                    self.record_errors[self.record['episode_id']] = 'Recording storage is not keeping up; queued source samples are retained'

    def _record_writer(self):
        while True:
            item = self.record_queue.get()
            try:
                if item is None:
                    return
                record,folder,filename,jpeg,sample = item
                if record['episode_id'] not in self.record_errors:
                    import os
                    with (folder/filename).open('wb') as stream:
                        stream.write(jpeg)
                        stream.flush()
                        os.fsync(stream.fileno())
                    with (folder/'samples.jsonl').open('a') as stream:
                        stream.write(json.dumps(sample,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
                        stream.flush()
                        os.fsync(stream.fileno())
                    record['samples'].append(sample)
                    if len(record['samples'])%10==0:
                        save(folder/'manifest.json',record)
            except Exception as error:
                self.record_errors[record['episode_id']] = type(error).__name__+': '+str(error)
            finally:
                self.record_queue.task_done()

    def _finish_record(self, outcome, state='complete'):
        with self.finish_lock:
            return self._finish_record_locked(outcome,state)

    def _finish_record_locked(self, outcome, state='complete'):
        if outcome not in ('success','failure','unknown'):
            raise ValueError('Unknown outcome label')
        with self.lock:
            if not self.record and not self.finishing_record:
                if self.last_finished and self.last_finished['outcome']==outcome:
                    return dict(self.last_finished)
                raise ValueError('No active station recording')
            record = self.finishing_record or self.record
            self.finishing_record, self.record = record, None
        self.record_queue.join()
        record.update(state=state,outcome=outcome,ended_wall_utc=time.time(),label_source=record.get('demonstration_actor','operator') if state=='complete' else 'recorder')
        if record['episode_id'] in self.record_errors:
            record.update(state='interrupted',recording_error=self.record_errors[record['episode_id']])
        path = self.root/'recordings'/record['episode_id']/'manifest.json'
        save(path,record)
        result = dict(episode_id=record['episode_id'],project_id=record['project_id'],state=record['state'],outcome=outcome,samples=len(record['samples']),manifest=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        with self.lock:
            self.last_finished, self.finishing_record = result, None
        return result

    def recordings(self):
        result = []
        for path in sorted(self.root.glob('recordings/*/manifest.json'),reverse=True):
            data = json.loads(path.read_text())
            result.append({key:value for key,value in data.items() if key!='samples'})
        return result

    def manifest(self, ident):
        if not re.fullmatch('[0-9a-f]{32}',ident):
            raise ValueError('Invalid recording ID')
        return json.loads((self.root/'recordings'/ident/'manifest.json').read_text())

    def archive(self, ident):
        import tarfile
        data = self.manifest(ident)
        if data['state']=='recording':
            raise ValueError('Finish this recording before transfer')
        folder = self.root/'recordings'/ident
        target = folder/'recording.tar'
        if not target.exists():
            with tarfile.open(target.with_suffix('.tmp'),'w') as archive:
                archive.add(folder/'manifest.json',arcname='manifest.json')
                for row in data['samples']:
                    archive.add(folder/row['frame']['file'],arcname=row['frame']['file'])
            target.with_suffix('.tmp').replace(target)
        return target

    def close(self):
        try:
            self.command('stop',{})
        except (RuntimeError,OSError):
            pass
        self.stop_event.set()
        if hasattr(self,'thread'):
            self.thread.join(timeout=8)
        if self.record:
            self._finish_record('unknown','interrupted')
        self.record_queue.put(None)
        if hasattr(self,'writer'):
            self.writer.join(timeout=8)
