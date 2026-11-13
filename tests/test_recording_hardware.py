import copy
import hashlib
import json
import time
from pathlib import Path

import pytest

from so101_lab.hardware import HardwareSource, JOINTS, calibration_snapshot, hardware_settings


def test_start_requires_explicit_action_before_any_hardware(monkeypatch):
    source=HardwareSource()
    def forbidden(*args,**kwargs): pytest.fail('lease/device opened without explicit action')
    monkeypatch.setattr('so101_lab.hardware.ResourceLease',forbidden)
    with pytest.raises(PermissionError): source.start()
    assert source.started is False


def test_snapshot_independent_hash_and_live_not_claimed():
    snapshot=calibration_snapshot()
    assert snapshot['status']=='copied_unverified_until_hardware_readback'
    for role,entry in snapshot['files'].items():
        assert entry['verified_live'] is False
        assert len(entry['sha256'])==64
    assert snapshot['camera_geometry']['wrist']=='unknown_not_validated'
    assert snapshot['camera_geometry']['top']=='unknown_camera_moved'


def test_unsafe_settings_are_rejected():
    for settings in ({'max_step_degrees':20},{'max_step_degrees':0},{'width':1280},
                     {'fps':60},{'max_speed_degrees_s':100},{'max_gripper_step':10}):
        with pytest.raises(ValueError): hardware_settings(settings)


def test_wrist_only_settings_require_and_open_only_wrist_camera():
    settings=hardware_settings({'camera_mode':'wrist_only'})
    assert tuple(settings['cameras'])==('wrist',)
    assert tuple(settings['camera_formats'])==('wrist',)


def test_limits_are_bounded_without_ordered_id_feature():
    source=HardwareSource()
    source.last_command_ns=time.monotonic_ns()-33_000_000
    targets,limited=source._safe_targets([999,-999,999,-999,999,999],[0,0,0,0,0,50],time.monotonic_ns())
    assert set(targets)==set(JOINTS)
    assert len(limited)==6
    assert all(abs(targets[n])<=2 for n in JOINTS[:5])
    assert 50<=targets['gripper']<=53
    with pytest.raises(ValueError): source._safe_targets([float('nan')]*6,[0]*6,time.monotonic_ns())


class Motor:
    def __init__(self,id): self.id=id

class FakeBus:
    def __init__(self,fail=False):
        self.motors={n:Motor(i) for i,n in enumerate(JOINTS,1)}
        self.calls=[]; self.fail=fail
    def _unnormalize(self,values): return {i:int(v*10) for i,v in values.items()}
    def _normalize(self,values): return {i:v/10 for i,v in values.items()}
    def sync_write(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        if self.fail: raise OSError('ambiguous send failure')


def test_transmitted_quantized_target_not_requested_and_never_retry():
    source=HardwareSource()
    source.started=True
    source.last_command_ns=time.monotonic_ns()-50_000_000
    source.buses['follower']=bus=FakeBus()
    obs={'state_timestamp_ns':time.monotonic_ns(),'observation.state':[0.,0.,0.,0.,0.,50.]}
    actual=source.send([1.239]*5+[51.239],obs)
    assert len(bus.calls)==1
    assert bus.calls[0][1]=={'normalize':False,'num_retry':0}
    assert actual['action']==[1.2]*5+[51.2]
    assert actual['action_acknowledged'] is False
    assert actual['action_raw_ticks']==[12]*5+[512]
    source.buses['follower']=bus=FakeBus(fail=True)
    with pytest.raises(OSError,match='ambiguous'): source.send([0]*6,obs)
    assert len(bus.calls)==1


def test_stale_state_never_sends():
    source=HardwareSource(); source.started=True
    source.buses['follower']=bus=FakeBus()
    with pytest.raises(RuntimeError,match='State слишком старый'):
        source.send([0]*6,{'state_timestamp_ns':time.monotonic_ns()-600_000_000,'observation.state':[0]*6})
    assert not bus.calls


def test_measured_outside_limits_refuses_motion_instead_of_jumping():
    source=HardwareSource(); source.started=True
    source.buses['follower']=bus=FakeBus()
    with pytest.raises(RuntimeError,match='вне безопасных'):
        source.send([0]*6,{'state_timestamp_ns':time.monotonic_ns(),'observation.state':[999,0,0,0,0,50]})
    assert not bus.calls


def test_existing_calibration_accepts_four_tick_resting_boundary_without_recalibration():
    source=HardwareSource()
    before=copy.deepcopy(source.calibration)
    raw={n:int((c['range_min']+c['range_max'])/2) for n,c in source.calibration['values']['follower'].items()}
    raw['shoulder_lift']=830  # Actual read-only reproduction: calibrated lower endpoint is834.
    hold=source._startup_hold_raw(raw)
    assert hold['shoulder_lift']==834
    assert source.startup_boundary_adjustments['shoulder_lift']=={'measured_raw_ticks':830,'hold_raw_ticks':834,'difference_ticks':4,'calibration_changed':False}
    assert source.calibration==before
    for name,target in hold.items():
        cal=source.calibration['values']['follower'][name]
        assert cal['range_min']<=target<=cal['range_max']


@pytest.mark.parametrize('delta',[-8,8])
def test_boundary_feedback_tolerance_never_extends_commanded_limits(delta):
    source=HardwareSource()
    c=source.calibration['values']['follower']['shoulder_lift']
    mid=(c['range_min']+c['range_max'])/2
    measured=[0.]*5+[50.]
    raw=c['range_min']+delta if delta<0 else c['range_max']+delta
    measured[1]=(raw-mid)*360/4095
    requested=[999.]*6
    source.last_command_ns=time.monotonic_ns()-33_000_000
    target,_=source._safe_targets(requested,measured,time.monotonic_ns())
    low,high=(c['range_min']-mid)*360/4095,(c['range_max']-mid)*360/4095
    assert low<=target['shoulder_lift']<=high
    assert abs(target['shoulder_lift']-measured[1])<=source.cfg['max_step_degrees']+1e-8


def test_large_excursion_still_refuses_and_small_step_budget_is_respected():
    source=HardwareSource()
    raw={n:int((c['range_min']+c['range_max'])/2) for n,c in source.calibration['values']['follower'].items()}
    raw['shoulder_lift']=825  # 9ticks, beyond the fixed8tick feedback tolerance.
    with pytest.raises(RuntimeError,match='9 тиков'):
        source._startup_hold_raw(raw)
    source=HardwareSource({'max_step_degrees':.1})
    raw['shoulder_lift']=830
    with pytest.raises(RuntimeError,match='допуск 1'):
        source._startup_hold_raw(raw)


def test_real_installed_sdk_quantization_without_serial_open(monkeypatch):
    import serial
    def forbidden(*args,**kwargs): pytest.fail('SDK test attempted to open serial')
    monkeypatch.setattr(serial,'Serial',forbidden)
    from lerobot.motors import Motor as SDKMotor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    source=HardwareSource()
    calibrations={name:MotorCalibration(**value) for name,value in source.calibration['values']['follower'].items()}
    motors={name:SDKMotor(i,'sts3215',MotorNormMode.RANGE_0_100 if name=='gripper' else MotorNormMode.DEGREES)
            for i,name in enumerate(JOINTS,1)}
    bus=FeetechMotorsBus('/dev/NEVER_OPEN_TEST_ONLY',motors,calibration=calibrations)
    captured=[]
    monkeypatch.setattr(bus,'_sync_write',lambda address,length,values,**kwargs:captured.append((address,length,values,kwargs)))
    # sync_write's decorator checks connection. Mark the inert SDK handler only; no port exists.
    bus.port_handler.is_open=True
    source.buses['follower']=bus;source.started=True
    source.last_command_ns=time.monotonic_ns()-50_000_000
    result=source.send([1.239]*5+[51.239],{'state_timestamp_ns':time.monotonic_ns(),'observation.state':[0]*5+[50]})
    assert len(captured)==1
    assert captured[0][3]['num_retry']==0
    assert list(captured[0][2].values())==result['action_raw_ticks']
    decoded=bus._normalize(captured[0][2])
    assert result['action']==[decoded[motors[name].id] for name in JOINTS]
    assert all(abs(value-1.239)<360/4095 for value in result['action'][:5])
    gripper_resolution=100/(calibrations['gripper'].range_max-calibrations['gripper'].range_min)
    assert abs(result['action'][5]-51.239)<gripper_resolution
    assert result['action']!=[1.239]*5+[51.239]
    bus.port_handler.is_open=False


class ShutdownBus:
    def __init__(self,fail_motor=None):
        self.motors={name:Motor(i) for i,name in enumerate(JOINTS,1)}
        self.is_connected=True;self.fail_motor=fail_motor;self.writes=[];self.disconnects=[]
    def write(self,register,motor,value,**kwargs):
        self.writes.append((register,motor,value,kwargs))
        if register=='Torque_Enable' and motor==self.fail_motor: raise OSError('Torque_Enable injected failure')
    def disconnect(self,**kwargs):
        self.disconnects.append(kwargs);self.is_connected=False


def test_torque_disable_failure_attempts_all_motors_once_and_closes(monkeypatch,tmp_path):
    monkeypatch.setattr('so101_lab.hardware.ROOT',tmp_path)
    source=HardwareSource.__new__(HardwareSource)
    source.started=True;source.closed=False;source.buses={'follower':ShutdownBus(fail_motor=JOINTS[0])}
    source.cameras={};source.lease=None;source._torque_touched={'follower'};source._close_errors=[]
    bus=source.buses['follower']
    errors=source.close()
    assert any('Torque_Enable injected failure' in error for error in errors)
    assert [motor for register,motor,value,kwargs in bus.writes if register=='Torque_Enable']==list(JOINTS)
    assert all(value==0 and kwargs=={'normalize':False,'num_retry':0} for _,_,value,kwargs in bus.writes)
    assert bus.disconnects==[{'disable_torque':False}]
    assert source.close()==errors
    assert len(bus.writes)==12
    assert source.started is False


def test_cleanup_before_torque_authorized_never_changes_torque(monkeypatch,tmp_path):
    monkeypatch.setattr('so101_lab.hardware.ROOT',tmp_path)
    source=HardwareSource.__new__(HardwareSource)
    source.started=False;source.closed=False;source.buses={'follower':ShutdownBus()}
    source.cameras={};source.lease=None;source._torque_touched=set();source._close_errors=[]
    assert source.close()==[]
    assert source.buses['follower'].writes==[]
    assert source.buses['follower'].disconnects==[{'disable_torque':False}]


def test_invalid_future_state_and_unsafe_policy_never_send():
    source=HardwareSource(teleoperate=False);source.started=True
    source.buses['follower']=bus=FakeBus()
    with pytest.raises(RuntimeError,match='будущем'):
        source.send([0]*5+[50],{'state_timestamp_ns':time.monotonic_ns()+1_000_000_000,'observation.state':[0]*5+[50]})
    with pytest.raises(RuntimeError,match='invalid'):
        source.send([0]*5+[50],{'state_timestamp_ns':time.monotonic_ns(),'observation.state':[0]*5+[50],'state_valid':False})
    with pytest.raises(ValueError,match='policy action'):
        source.send([999]*6,{'state_timestamp_ns':time.monotonic_ns(),'observation.state':[0]*5+[50]})
    assert bus.calls==[]


def test_hardware_settings_reject_nonfinite_and_duplicate_devices():
    for settings in ({'max_camera_age_ms':float('nan')},{'max_camera_age_ms':float('inf')},
                     {'max_camera_age_ms':0},{'leader_port':'/dev/so101-follower'},
                     {'cameras':{'top':'/dev/same','wrist':'/dev/same'}}):
        with pytest.raises(ValueError):hardware_settings(settings)


def test_camera_per_device_format_and_readback_without_controls_writes(monkeypatch):
    import cv2
    import threading
    from so101_lab.hardware import CameraCapture
    class Capture:
        def __init__(self,format_name='MJPG',fps=120.101):
            self.properties={cv2.CAP_PROP_FOURCC:cv2.VideoWriter_fourcc(*format_name),
                             cv2.CAP_PROP_FRAME_WIDTH:640,cv2.CAP_PROP_FRAME_HEIGHT:480,
                             cv2.CAP_PROP_FPS:fps,cv2.CAP_PROP_EXPOSURE:156}
            self.writes=[];self.released=False
        def isOpened(self):return True
        def get(self,prop):return self.properties.get(prop,0.)
        def set(self,prop,value):
            self.writes.append((prop,value));self.properties[prop]=value
            if prop==cv2.CAP_PROP_FOURCC:self.properties[cv2.CAP_PROP_EXPOSURE]=38
            return True
        def release(self):self.released=True
    capture=Capture()
    monkeypatch.setattr(cv2,'VideoCapture',lambda *args,**kwargs:capture)
    monkeypatch.setattr('so101_lab.hardware.assert_hardware_available',lambda *args:None)
    def fake_run(camera):
        now=time.monotonic_ns();camera.sequence=2
        camera.frames.append({'timestamp_ns':now,'sequence':2})
        camera.stop_event.wait(2)
    monkeypatch.setattr(CameraCapture,'_run',fake_run)
    camera=CameraCapture('wrist','/dev/FAKE_NOT_OPENED',hardware_settings())
    camera.start()
    assert capture.writes==[(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'YUYV')),(cv2.CAP_PROP_FPS,30.)]
    assert camera.info['pixel_format_reported']=='YUYV'
    assert camera.info['fps_reported']==30.
    assert camera.info['controls_written']==[]
    assert camera.info['controls_changed_by_driver']==['EXPOSURE']
    camera.close();assert capture.released
    matched=Capture(format_name='MJPG',fps=30.)
    monkeypatch.setattr(cv2,'VideoCapture',lambda *args,**kwargs:matched)
    camera=CameraCapture('top','/dev/FAKE_NOT_OPENED',hardware_settings())
    camera.start();camera.close()
    assert matched.writes==[]


def test_camera_rejects_unsupported_fps_before_reader_starts(monkeypatch):
    import cv2
    from so101_lab.hardware import CameraCapture
    class Capture:
        released=False
        def isOpened(self):return True
        def get(self,prop):
            return {cv2.CAP_PROP_FOURCC:cv2.VideoWriter_fourcc(*'YUYV'),cv2.CAP_PROP_FRAME_WIDTH:640,
                    cv2.CAP_PROP_FRAME_HEIGHT:480,cv2.CAP_PROP_FPS:120.101}.get(prop,0.)
        def set(self,*args):return False
        def release(self):self.released=True
    capture=Capture()
    monkeypatch.setattr(cv2,'VideoCapture',lambda *args,**kwargs:capture)
    monkeypatch.setattr('so101_lab.hardware.assert_hardware_available',lambda *args:None)
    monkeypatch.setattr(CameraCapture,'_run',lambda *args:pytest.fail('unsupported FPS launched reader'))
    camera=CameraCapture('wrist','/dev/FAKE_NOT_OPENED',hardware_settings())
    with pytest.raises(RuntimeError,match='FPS readback'):camera.start()
    assert capture.released


@pytest.mark.parametrize('capture_cameras',[True,False])
def test_partial_enable_failure_closes_all_motors_without_motion_retry(monkeypatch,tmp_path,capture_cameras):
    import serial
    from types import SimpleNamespace
    import lerobot.motors.feetech
    import so101_lab.hardware as module
    def forbidden(*args,**kwargs):pytest.fail('Test attempted real serial open')
    monkeypatch.setattr(serial,'Serial',forbidden)
    monkeypatch.setattr(module,'assert_hardware_available',lambda *args:None)
    monkeypatch.setattr(module.fcntl,'ioctl',lambda *args:None)
    instances={}
    class Lease:
        def __init__(self,*args):self.closed=False
        def __enter__(self):return self
        def __exit__(self,*args):self.closed=True
    class Camera:
        def __init__(self,name,*args):
            assert capture_cameras,'Bus-only startup must never construct a camera'
            self.info={};self.closed=False
        def start(self):pass
        def close(self):self.closed=True
        def latest_before(self,now):return {'timestamp_ns':now}
    class Bus(ShutdownBus):
        def __init__(self,port,motors,calibration):
            super().__init__();self.port=port;self.calibration=calibration;self.is_connected=False
            self.motors=motors;self.port_handler=SimpleNamespace(ser=SimpleNamespace(fileno=lambda:999999))
            self.seed_writes=[];self.enables=0;instances[port]=self
        def connect(self,**kwargs):assert kwargs=={'handshake':False};self.is_connected=True
        def _handshake(self):pass
        def read_calibration(self):return self.calibration
        def read(self,*args,**kwargs):return 0
        def disable_torque(self,**kwargs):assert kwargs=={'num_retry':0}
        def sync_read(self,*args,**kwargs):return {name:int((cal.range_min+cal.range_max)/2) for name,cal in self.calibration.items()}
        def _normalize(self,values):return {i:(50. if i==6 else 0.) for i in values}
        def sync_write(self,*args,**kwargs):self.seed_writes.append((args,kwargs))
        def enable_torque(self,**kwargs):
            assert kwargs=={'num_retry':0};self.enables+=1
            self.write('Torque_Enable',JOINTS[0],1,num_retry=0)
            raise OSError('partial Torque_Enable failure')
    monkeypatch.setattr(module,'ResourceLease',Lease)
    monkeypatch.setattr(module,'CameraCapture',Camera)
    monkeypatch.setattr(lerobot.motors.feetech,'FeetechMotorsBus',Bus)
    source=HardwareSource(capture_cameras=capture_cameras)
    with pytest.raises(OSError,match='partial Torque_Enable'):source.start(explicit_user_start=True)
    follower=instances[source.cfg['follower_port']]
    assert follower.enables==1 and len(follower.seed_writes)==1
    assert follower.seed_writes[0][1]=={'normalize':False,'num_retry':0}
    assert [motor for register,motor,value,_ in follower.writes if register=='Torque_Enable' and value==0]==list(JOINTS)
    assert follower.disconnects==[{'disable_torque':False}]
    assert all(camera.closed for camera in source.cameras.values())
    assert source.close()==[]


def test_start_cancelled_before_resource_lease(monkeypatch):
    source=HardwareSource()
    monkeypatch.setattr('so101_lab.hardware.ResourceLease',lambda *args:pytest.fail('Cancelled startup acquired resource'))
    with pytest.raises(InterruptedError,match='отменён'):
        source.start(explicit_user_start=True,stop=lambda:True)
