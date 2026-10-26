"""Run the ORIGINAL finite Station worker with fake buses/camera only.

No real serial port, physical grant, camera capture, motor or model is opened.
"""
from types import SimpleNamespace
import uuid

import pytest

from explorer_shared.so101_station import SO101Station,NAMES
from explorer_shared.station_observed_activation import StationObservedPermission,CONTRACT
from test_station_operations import make_station


def worker_rig(tmp_path,monkeypatch):
    station=make_station(tmp_path,monkeypatch);tick=[10_000_000_000];leader_active=[False]
    class Camera:
        camera_id='SOFTWARE-camera';stale_s=2.
        def latest(self):return SimpleNamespace(jpeg=b'\xff\xd8EXPLICIT-SOFTWARE-FRAME\xff\xd9',frame_id='fake-frame',monotonic_ns=tick[0],timestamp_basis='software-test')
    station.camera=Camera();station.profiles={'leader':{},'follower':dict(verified_on_connected_hardware=True,calibration_sha256='a'*64)}
    station._positions=lambda role:[10. if role=='leader' and leader_active[0] else 0.,0.,0.,0.,0.,50.]
    station._connect=lambda:None;station.owners=[]
    bus=station.buses['follower'];bus.is_connected=True;bus.disconnect=lambda **kwargs:None
    samples=[];station._sample=lambda *args:samples.append(args)
    provider=StationObservedPermission(station,station.camera,clock=lambda:tick[0])
    selected=dict(purpose='cooperative05',input_mode='leader',skill_id=str(uuid.uuid4()),skill_version=1,spec_sha256='b'*64,
        action_contract_id=CONTRACT,source_domain='PHYSICAL',cooperative_session_id='fake-session',coordinator_epoch='fake-coordinator',object_id='fake-object')
    def observed_start():
        challenge=provider.challenge(selected)
        grant=provider.authorize(dict(binding=selected,challenge_id=challenge['challenge_id'],displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],
            expected_control_epoch=station.control_epoch,observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True,actor_id='SOFTWARE-TEST-OPERATOR'))
        reply=provider.command(grant['grant_id'],selected,'teleop/start',{'expected_control_epoch':grant['control_epoch']})
        leader_active[0]=True;bus.writes.clear()
        return reply['grant']
    class StopAfter:
        def __init__(self,after_tick=None):self.count=0;self.after_tick=after_tick
        def is_set(self):return self.count>=3
        def wait(self,timeout):
            self.count+=1
            if self.count==1 and self.after_tick:self.after_tick()
    return station,provider,selected,observed_start,bus,samples,tick,StopAfter,leader_active


@pytest.mark.parametrize('reason',['expired_observation','explicit_revoke'])
def test_original_worker_stops_owned_leader_on_permission_loss_even_with_fresh_camera_without_false_samples(tmp_path,monkeypatch,reason):
    station,provider,selected,start,bus,samples,tick,StopAfter,_=worker_rig(tmp_path,monkeypatch)
    grant=start();old=station.control_epoch
    def interrupt():
        if reason=='expired_observation':tick[0]+=3_000_000_000
        else:provider.revoke('SOFTWARE_EXPLICIT_REVOKE',expected_grant_id=grant['grant_id'],expected_control_epoch=old)
    station.stop_event=StopAfter(interrupt);station._work()
    assert station.error is None and station.stop_event.count==3  # permission loss did not kill the original worker
    assert station.mode=='idle' and station.control_epoch!=old
    assert len(samples)==1 and len(bus.writes)==2
    assert list(bus.writes[0][1].values())==[10.,0.,0.,0.,0.,50.]
    assert list(bus.writes[1][1].values())==[0.,0.,0.,0.,0.,50.]  # actual original measured hold
    assert station.last_command['control_epoch']==old and station.last_command['requested']==[10.,0.,0.,0.,0.,50.]
    result=provider.publication_gate.status()
    assert result['last_decision']['allowed'] is False
    assert result['last_measured_hold']['denied_native_epoch']==old
    assert result['last_measured_hold']['ended_native_epoch']==station.control_epoch
    assert result['last_measured_hold']['original_position_write_returned'] is True
    assert result['last_measured_hold']['old_target_sample_emitted'] is False
    assert result['last_measured_hold']['MCU_attainment_verified'] is False


def test_existing_legacy_unowned_leader_path_is_unchanged_by_optional_permission_hook(tmp_path,monkeypatch):
    station,provider,selected,start,bus,samples,tick,StopAfter,active=worker_rig(tmp_path,monkeypatch)
    station.command('teleop/start',dict(expected_control_epoch=station.control_epoch,observing=True))
    active[0]=True;bus.writes.clear();station.stop_event=StopAfter();station._work()
    assert station.error is None and station.mode=='teleoperation'
    assert len(samples)==len(bus.writes)==3
    assert all(list(row[1].values())==[10.,0.,0.,0.,0.,50.] for row in bus.writes)
    assert provider.publication_gate.status()['last_decision'] is None


def test_one_already_entered_native_write_returning_after_revoke_remains_truthfully_recorded_and_next_target_is_blocked(tmp_path,monkeypatch):
    station,provider,selected,start,bus,samples,tick,StopAfter,_=worker_rig(tmp_path,monkeypatch)
    grant=start();original=bus.sync_write;revoked=[]
    def in_flight(name,values,normalize=True):
        if not revoked:
            provider.revoke('REVOKED_DURING_ALREADY_ENTERED_SERIAL_WRITE',expected_grant_id=grant['grant_id'],expected_control_epoch=station.control_epoch)
            revoked.append(True)
        original(name,values,normalize)
    bus.sync_write=in_flight;station.stop_event=StopAfter();station._work()
    assert station.error is None and station.mode=='idle'
    assert len(samples)==1 and len(bus.writes)==2 and revoked==[True]
    assert list(bus.writes[0][1].values())==station.last_command['dispatched']==[10.,0.,0.,0.,0.,50.]
    assert list(bus.writes[1][1].values())==[0.,0.,0.,0.,0.,50.]
