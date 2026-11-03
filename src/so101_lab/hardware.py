"""Explicit-start SO-101 I/O. Importing this module never opens a device.

No foreign project code is imported. Bus commands use the private LeRobot environment.
A bus write is attempted once; a failure terminates the session, never retries motion.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import importlib.metadata
import json
import math
import termios
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .resources import ResourceLease, assert_hardware_available

JOINTS = ('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAMERAS = {
    'top': '/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_D4EA612F-video-index0',
    'wrist': '/dev/v4l/by-id/usb-HD_USB_Camera_HD_USB_Camera-video-index0',
}


def hardware_settings(settings: dict | None = None) -> dict:
    settings = dict(settings or {})
    result = {
        'leader_port': '/dev/so101-leader', 'follower_port': '/dev/so101-follower',
        'cameras': dict(DEFAULT_CAMERAS), 'width': 640, 'height': 480,
        'fps': 30, 'max_camera_age_ms': 150, 'max_step_degrees': 2.0,
        'max_gripper_step': 3.0, 'max_speed_degrees_s': 45.0,
        'calibration_dir': str(ROOT / 'config/calibration_snapshots'),
    }
    for key in result:
        if key in settings: result[key] = settings[key]
    if not 1 <= float(result['fps']) <= 30: raise ValueError('FPS должен быть в пределах 1–30')
    if (int(result['width']),int(result['height'])) != (640,480):
        raise ValueError('Проверяемый режим записи: 640×480; другая геометрия требует отдельной проверки')
    if not 0 < float(result['max_step_degrees']) <= 2.0: raise ValueError('Шаг сустава должен быть 0–2°')
    if not 0 < float(result['max_gripper_step']) <= 5: raise ValueError('Шаг gripper должен быть 0–5%')
    if not 0 < float(result['max_speed_degrees_s']) <= 60: raise ValueError('Скорость должна быть 0–60°/s')
    if set(result['cameras']) != {'top','wrist'}: raise ValueError('Raw recording требует top и wrist')
    return result


def hardware_paths(settings: dict | None = None, leader: bool = True) -> list[str]:
    cfg = hardware_settings(settings)
    paths = [cfg['follower_port'], *cfg['cameras'].values()]
    if leader: paths.append(cfg['leader_port'])
    return paths


def calibration_snapshot(calibration_dir: str | Path | None = None) -> dict:
    directory = Path(calibration_dir or ROOT / 'config/calibration_snapshots').resolve()
    # Calibration must be an independent copy inside this repository.
    if not directory.is_relative_to(ROOT): raise ValueError('Калибровка должна быть внутри лаборатории')
    manifest = json.loads((directory / 'manifest.json').read_text())
    result = dict(manifest)
    result['values'] = {}
    for role in ('leader','follower'):
        entry = manifest['files'][role]
        path = (directory / entry['path']).resolve()
        if not path.is_relative_to(directory): raise ValueError('Некорректный путь calibration snapshot')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f'Изменён calibration snapshot {role}: hash не совпал')
        values = json.loads(raw)
        if set(values) != set(JOINTS): raise ValueError('Неверная схема motor calibration')
        for i, name in enumerate(JOINTS, 1):
            if values[name]['id'] != i or values[name]['range_min'] >= values[name]['range_max']:
                raise ValueError('Неверные ID/ranges motor calibration')
        result['values'][role] = values
    return result


class CameraCapture:
    """Own server camera reader; never adjusts exposure/focus/white balance controls."""
    def __init__(self, name: str, device: str, cfg: dict):
        self.name, self.device, self.cfg = name, device, cfg
        self.frames = deque(maxlen=8)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.error = None
        self.sequence = 0
        self.cap = None
        self.thread = None
        self.info = {}

    def start(self):
        import cv2
        assert_hardware_available([self.device])
        self.cap = cv2.VideoCapture(str(self.device), cv2.CAP_V4L2)
        if not self.cap.isOpened(): raise RuntimeError(f'Камера {self.name} не открылась: {self.device}')
        try:
            # Stream mode is selected only after explicit user start; no autofocus/controls writes.
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.cfg['width']))
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.cfg['height']))
            self.cap.set(cv2.CAP_PROP_FPS, float(self.cfg['fps']))
            self.info = {
                'device': self.device, 'device_resolved': str(Path(self.device).resolve()),
                'width': int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                'height': int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                'fps_reported': self.cap.get(cv2.CAP_PROP_FPS),
                'timestamp_basis': 'host_monotonic_capture_read_end; exposure timestamp unavailable',
                'controls_written': [], 'camera_geometry_valid': False,
            }
            if (self.info['width'],self.info['height']) != (self.cfg['width'],self.cfg['height']):
                raise RuntimeError(f'Камера {self.name}: фактическое разрешение {self.info["width"]}x{self.info["height"]} не совпало')
            self.thread = threading.Thread(target=self._run, name='so101-ai-camera-'+self.name, daemon=True)
            self.thread.start()
            deadline = time.monotonic()+3
            while not self.frames and not self.error and time.monotonic()<deadline:
                self.stop_event.wait(.01)
            if not self.frames: raise RuntimeError(f'Нет кадров {self.name}: {self.error or "timeout"}')
        except Exception:
            self.close()
            raise

    def _run(self):
        import cv2
        try:
            while not self.stop_event.is_set():
                read_start = time.monotonic_ns()
                ok,bgr = self.cap.read()
                read_end = time.monotonic_ns()
                if not ok or bgr is None: raise RuntimeError('V4L2 read returned no image')
                rgb = cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
                if rgb.shape != (self.cfg['height'],self.cfg['width'],3):
                    raise RuntimeError('Размер камеры изменился во время записи')
                rgb.flags.writeable = False
                self.sequence += 1
                frame = {'image':rgb,'timestamp_ns':read_end,'read_start_ns':read_start,
                         'read_end_ns':read_end,'sequence':self.sequence}
                with self.lock: self.frames.append(frame)
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            self.stop_event.set()

    def latest_before(self, cutoff_ns):
        if self.error: raise RuntimeError(f'Камера {self.name}: {self.error}')
        with self.lock:
            for frame in reversed(self.frames):
                if frame['timestamp_ns'] <= cutoff_ns: return dict(frame)
        return None

    def close(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
        if self.cap:
            self.cap.release()
            self.cap = None
        if self.thread and self.thread.is_alive():
            self.error = 'camera reader did not stop within 2s; session stopped'


class HardwareSource:
    """Two-camera leader→follower teleoperation, or follower-only policy I/O.

    Constructing the object is inert. `start(explicit_user_start=True)` is mandatory.
    """
    source = 'hardware'
    def __init__(self, settings: dict | None = None, *, teleoperate: bool = True):
        self.cfg = hardware_settings(settings)
        self.teleoperate = teleoperate
        self.calibration = calibration_snapshot(self.cfg['calibration_dir'])
        self.cameras: dict[str,CameraCapture] = {}
        self.buses: dict[str,Any] = {}
        self.lease = None
        self.started = False
        self.last_command_ns = None
        self.last_camera_sequences = {}
        self.closed = False
        self.verified_calibration = {}

    def start(self, *, explicit_user_start: bool = False):
        if not explicit_user_start: raise PermissionError('Нужен явный запуск записи/политики в UI')
        if self.started: raise RuntimeError('Source already started')
        self.closed = False
        self.lease = ResourceLease('hardware')
        self.lease.__enter__()
        try:
            assert_hardware_available(hardware_paths(self.cfg,leader=self.teleoperate))
            # Imports resolve only to the laboratory .venv, never a foreign worktree.
            from lerobot.motors import Motor, MotorCalibration, MotorNormMode
            from lerobot.motors.feetech import FeetechMotorsBus
            for name,device in self.cfg['cameras'].items():
                camera = CameraCapture(name,device,self.cfg)
                self.cameras[name] = camera
                camera.start()
            for role in (('leader','follower') if self.teleoperate else ('follower',)):
                device = self.cfg[role+'_port']
                assert_hardware_available([device])
                motors = {name:Motor(i,'sts3215',MotorNormMode.RANGE_0_100 if name=='gripper' else MotorNormMode.DEGREES)
                          for i,name in enumerate(JOINTS,1)}
                calibration = {name:MotorCalibration(**v) for name,v in self.calibration['values'][role].items()}
                bus = FeetechMotorsBus(port=device,motors=motors,calibration=calibration)
                self.buses[role] = bus
                bus.connect(handshake=False)
                fcntl.ioctl(bus.port_handler.ser.fileno(),termios.TIOCEXCL)
                bus._handshake()
                actual = bus.read_calibration()
                mismatches = [n for n in JOINTS if any(getattr(actual[n],k) != getattr(calibration[n],k)
                              for k in ('id','homing_offset','range_min','range_max'))]
                if mismatches: raise RuntimeError(f'{role}: калибровка в моторах отличается от snapshot: {mismatches}; запись не запущена')
                for name in JOINTS:
                    if int(bus.read('Operating_Mode',name,normalize=False,num_retry=0)) != 0:
                        raise RuntimeError(f'{role}/{name}: нужен Position mode; автоматическая перенастройка запрещена')
                self.verified_calibration[role] = {'verified_live':True,'timestamp_ns':time.monotonic_ns(),
                                                  'snapshot_sha256':self.calibration['files'][role]['sha256']}
            # Explicit start authorizes torque. Seed the present raw position before enabling it.
            follower = self.buses['follower']
            follower.disable_torque(num_retry=0)
            present_raw = follower.sync_read('Present_Position',normalize=False,num_retry=1)
            follower.sync_write('Goal_Position',present_raw,normalize=False,num_retry=0)
            if self.teleoperate: self.buses['leader'].disable_torque(num_retry=0)
            follower.enable_torque(num_retry=0)
            self.started = True
            self.last_command_ns = time.monotonic_ns()
            return self.provenance()
        except Exception:
            self.close()
            raise

    def provenance(self):
        return {'source':self.source,'lerobot_version':importlib.metadata.version('lerobot'),
                'joints':list(JOINTS),'units':['degrees']*5+['percent_calibrated_range'],
                'calibration_snapshot':self.calibration,'live_calibration':self.verified_calibration,
                'cameras':{n:c.info for n,c in self.cameras.items()},
                'feedback_unavailable':['velocity','current','load','force'],
                'writes_acknowledgement':'Goal_Position sync_write has no per-motor ACK; actual transmitted targets, not measured achievement',
                'motion_retry_count':0,'camera_geometry_valid':False,'wrist_hand_eye_valid':False}

    def observe(self):
        if not self.started: raise RuntimeError('Hardware source is not started')
        read_start = time.monotonic_ns()
        state_dict = self.buses['follower'].sync_read('Present_Position',num_retry=1)
        read_end = time.monotonic_ns()
        images = {}
        for name,camera in self.cameras.items():
            frame = camera.latest_before(read_end)
            if frame is None: raise RuntimeError(f'Нет причинного кадра {name} до observation timestamp')
            age_ms = (read_end-frame['timestamp_ns'])/1e6
            if age_ms>self.cfg['max_camera_age_ms']:
                raise RuntimeError(f'Камера {name} stale: {age_ms:.1f} ms')
            previous = self.last_camera_sequences.get(name)
            frame['reused'] = previous == frame['sequence']
            frame['dropped_since_previous'] = max(0,frame['sequence']-(previous or frame['sequence'])-1)
            self.last_camera_sequences[name] = frame['sequence']
            images[name] = frame
        state = [float(state_dict[n]) for n in JOINTS]
        if not all(math.isfinite(v) for v in state): raise RuntimeError('Не конечная позиция суставов')
        return {'timestamp_ns':read_end,'observation_timestamp_ns':read_end,
                'state_timestamp_ns':read_end,'state_read_start_ns':read_start,'state_read_end_ns':read_end,
                'observation.state':state,'images':images,'state_valid':True,'source':'hardware'}

    def _safe_targets(self, requested, measured, now_ns):
        if len(requested)!=6 or not all(math.isfinite(float(v)) for v in requested):
            raise ValueError('Политика/leader вернула неверный action')
        dt = min(.1,max(.001,(now_ns-(self.last_command_ns or now_ns))/1e9))
        step = min(float(self.cfg['max_step_degrees']),float(self.cfg['max_speed_degrees_s'])*dt)
        result = {}
        limits = []
        for i,name in enumerate(JOINTS):
            cal = self.calibration['values']['follower'][name]
            if name=='gripper': low,high,delta = 0.,100.,float(self.cfg['max_gripper_step'])
            else:
                mid = (cal['range_min']+cal['range_max'])/2
                low,high = (cal['range_min']-mid)*360/4095,(cal['range_max']-mid)*360/4095
                # Conservative additional software bound; live collision safety remains operator responsibility.
                low,high = max(low,-175.),min(high,175.)
                delta = step
            if not math.isfinite(float(measured[i])) or not low-0.1 <= measured[i] <= high+0.1:
                raise RuntimeError(f'{name}: measured state вне безопасных абсолютных пределов; движение остановлено')
            value = float(requested[i])
            bounded = max(low,min(high,value))
            bounded = max(measured[i]-delta,min(measured[i]+delta,bounded))
            result[name] = bounded
            if abs(value-bounded)>1e-6: limits.append(name)
        return result,limits

    def send(self, requested, observation):
        if not self.started: raise RuntimeError('Hardware source is not started')
        start_ns = time.monotonic_ns()
        if start_ns-observation['state_timestamp_ns'] > 500_000_000:
            raise RuntimeError('State слишком старый для движения (>500ms), нужна новая observation')
        safe,limited = self._safe_targets(requested,observation['observation.state'],start_ns)
        bus = self.buses['follower']
        raw_by_id = bus._unnormalize({bus.motors[n].id:v for n,v in safe.items()})
        raw = {name:raw_by_id[bus.motors[name].id] for name in JOINTS}
        # Single send, no automatic retry even after ambiguous USB/ACK failure.
        bus.sync_write('Goal_Position',raw,normalize=False,num_retry=0)
        end_ns = time.monotonic_ns()
        actual_by_id = bus._normalize(raw_by_id)
        actual = [float(actual_by_id[bus.motors[name].id]) for name in JOINTS]
        self.last_command_ns = end_ns
        return {'action':actual,'action_raw_ticks':[raw[n] for n in JOINTS],
                'action_timestamp_ns':end_ns,'action_send_start_ns':start_ns,'action_send_end_ns':end_ns,
                'action_valid':True,'action_acknowledged':False,'action_provenance':'transmitted_quantized_goal_position',
                'limits_applied':limited}

    def sample(self, frame_id):
        observation = self.observe()
        if not self.teleoperate: raise RuntimeError('sample only for leader teleoperation')
        leader_start = time.monotonic_ns()
        requested = self.buses['leader'].sync_read('Present_Position',num_retry=1)
        leader_end = time.monotonic_ns()
        action = self.send([float(requested[n]) for n in JOINTS],observation)
        return {**observation,**action,'frame_id':frame_id,
                'leader_read_start_ns':leader_start,'leader_read_end_ns':leader_end,
                'leader_action_requested':[float(requested[n]) for n in JOINTS]}

    def close(self):
        if self.closed: return
        self.closed = True
        errors = []
        for role,bus in reversed(list(self.buses.items())):
            try:
                if bus.is_connected:
                    try: bus.disable_torque(num_retry=0)
                    except Exception as exc: errors.append(f'{role} torque disable: {exc}')
                    finally: bus.disconnect(disable_torque=False)
            except Exception as exc: errors.append(f'{role} disconnect: {exc}')
        for camera in self.cameras.values():
            try: camera.close()
            except Exception as exc: errors.append(f'camera close: {exc}')
        self.started = False
        if self.lease:
            self.lease.__exit__(None,None,None)
            self.lease = None
        if errors:
            directory = ROOT/'logs'; directory.mkdir(exist_ok=True)
            with (directory/'hardware-stop-errors.jsonl').open('a') as out:
                out.write(json.dumps({'timestamp_ns':time.monotonic_ns(),'errors':errors},ensure_ascii=False)+'\n')
        return errors
