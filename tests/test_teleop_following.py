"""Stock LeRobot compatibility with inert SDK handlers; never opens serial."""
import time
from types import SimpleNamespace

import pytest

from so101_lab.hardware import HardwareSource, JOINTS, ROOT, hardware_paths


def test_direct_follow_matches_stock_lerobot_for_every_axis_and_sign(monkeypatch):
    import serial
    monkeypatch.setattr(serial, 'Serial', lambda *a, **kw: pytest.fail('No serial acquisition'))
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    leader=SO101Leader(SO101LeaderConfig(port='/dev/NEVER_OPEN_LEADER',id='leader',calibration_dir=ROOT/'config/calibration_snapshots'))
    follower=SO101Follower(SO101FollowerConfig(port='/dev/NEVER_OPEN_FOLLOWER',id='follower',calibration_dir=ROOT/'config/calibration_snapshots'))
    values={}
    captured=[]
    monkeypatch.setattr(leader.bus,'sync_read',lambda *a, **kw:dict(values))
    def capture(register, commands, normalize=True, **kwargs):
        assert register=='Goal_Position'
        raw=follower.bus._unnormalize({follower.bus.motors[n].id:v for n,v in commands.items()}) if normalize else {follower.bus.motors[n].id:v for n,v in commands.items()}
        captured.append(raw)
    monkeypatch.setattr(follower.bus,'sync_write',capture)
    leader.bus.port_handler.is_open=True;follower.bus.port_handler.is_open=True
    source=HardwareSource(capture_cameras=False);source.started=True;source.teleop_phase='following'
    source.buses={'leader':leader.bus,'follower':follower.bus}
    def observation():
        stamp=time.monotonic_ns()
        return {'timestamp_ns':stamp,'state_timestamp_ns':stamp,'state_valid':True,'observation.state':[0.]*5+[50.],'images':{}}
    monkeypatch.setattr(source,'observe',observation)
    try:
        for sign in (-1,1):
            # Reverse dictionary insertion order to detect accidental joint swaps.
            values.update({n:(50.+sign*20 if n=='gripper' else sign*(i+1)*5.) for i,n in reversed(list(enumerate(JOINTS)))})
            follower.send_action(leader.get_action())
            stock=captured[-1]
            sample=source.sample(1)
            assert captured[-1]==stock
            assert sample['leader_action_requested']==[values[n] for n in JOINTS]
            assert sample['limits_applied']==[]
            assert abs(sample['action'][4])>20  # Previous2°/45°-s limiter must not slow direct following.
    finally:
        leader.bus.port_handler.is_open=False;follower.bus.port_handler.is_open=False


def test_initial_alignment_advances_command_instead_of_feedback_clamping():
    source=HardwareSource(capture_cameras=False)
    source.last_teleop_target=[0.]*5+[50.]
    target=[20.]*5+[50.]
    measured=[0.]*5+[50.]
    previous=0.
    for step in range(4):
        source.last_command_ns=time.monotonic_ns()-33_000_000
        command,_=source._safe_targets(target,measured,time.monotonic_ns())
        assert 0<command['shoulder_pan']-previous<=2
        previous=command['shoulder_pan']
        source.last_teleop_target=[command[n] for n in JOINTS]
    assert previous>5  # Old measured+delta limiter stalled near1.5° with the same feedback.


def test_policy_keeps_its_original_bounded_action_limits():
    source=HardwareSource(teleoperate=False,capture_cameras=False)
    source.last_command_ns=time.monotonic_ns()-33_000_000
    command,_=source._safe_targets([20.]*5+[70.],[0.]*5+[50.],time.monotonic_ns())
    assert all(abs(command[n])<=2 for n in JOINTS[:5])
    assert command['gripper']<=53


def test_bus_only_observation_has_no_camera_dependency():
    source=HardwareSource(capture_cameras=False);source.started=True
    source.buses={'follower':SimpleNamespace(sync_read=lambda *a, **kw:{n:0. for n in JOINTS})}
    assert source.observe()['images']=={}
    assert set(hardware_paths(include_cameras=False))=={'/dev/so101-leader','/dev/so101-follower'}


def test_camera_preview_can_run_while_servo_resource_is_held(monkeypatch,tmp_path):
    from so101_lab import preview,resources
    monkeypatch.setattr(resources,'ROOT',tmp_path)
    monkeypatch.setattr(preview,'assert_hardware_available',lambda paths:None)
    class Camera:
        def __init__(self,*args):self.error=None;self.sequence=1;self.info={}
        def start(self):pass
        def close(self):pass
        def latest_before(self,now):return None
    monkeypatch.setattr(preview,'CameraCapture',Camera)
    cameras=preview.CameraPreview()
    with resources.ResourceLease('hardware'):
        try:assert cameras.start(explicit_user_start=True)['active']
        finally:cameras.stop()
