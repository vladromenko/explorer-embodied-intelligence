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
