"""Run the ORIGINAL finite Station worker with fake buses/camera only.

No real serial port, physical grant, camera capture, motor or model is opened.
"""
from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest

from explorer_shared.so101_station import SO101Station,NAMES
from explorer_shared.station_observed_activation import StationObservedPermission,CONTRACT
from test_station_operations import make_station


def worker_rig(tmp_path,monkeypatch,*,binding_override=None,actor='SOFTWARE-TEST-OPERATOR'):
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
    if binding_override is not None:selected=deepcopy(binding_override)
    if selected['purpose']=='source04_demo':
        station.profiles['leader']=dict(verified_on_connected_hardware=True,calibration_sha256='c'*64)
        station.measurements=dict(monotonic_ns=tick[0],leader=[0.,0.,0.,0.,0.,50.],follower=[0.,0.,0.,0.,0.,50.])
    def observed_start():
        challenge=provider.challenge(selected)
        grant=provider.authorize(dict(binding=selected,challenge_id=challenge['challenge_id'],displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],
            expected_control_epoch=station.control_epoch,observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True,actor_id=actor))
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


def source04_demo_binding():
    return dict(purpose='source04_demo',input_mode='leader',skill_id=str(uuid.uuid4()),skill_version=1,spec_sha256='b'*64,
        action_contract_id=CONTRACT,source_domain='PHYSICAL',source_session_id='1'*32,demonstration_id='2'*32)


def test_source04_manual_session_survives_web_observation_deadline_until_exact_stop(tmp_path,monkeypatch):
    station,provider,selected,start,bus,samples,tick,StopAfter,_=worker_rig(
        tmp_path,monkeypatch,binding_override=source04_demo_binding())
    grant=start();old=grant['control_epoch']
    assert grant['observation_renewal_required'] is False and grant['manual_session_until_explicit_stop'] is True
    tick[0]+=60_000_000_000
    checked=provider.check(grant['grant_id'],selected,old)
    assert checked['execution_permission'] is True and checked['remaining_observation_s'] is None
    station.stop_event=StopAfter();station._work()
    assert station.mode=='teleoperation' and station.control_epoch==old and len(samples)==len(bus.writes)==3
    stopped=provider.command(grant['grant_id'],selected,'stop',{'expected_control_epoch':old})
    assert stopped['status']['mode']=='idle' and stopped['native_command_called'] is True


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


def test_revoke_after_worker_precheck_before_original_write_admission_blocks_target_and_sample(tmp_path,monkeypatch):
    station,provider,selected,start,bus,samples,tick,StopAfter,_=worker_rig(tmp_path,monkeypatch)
    grant=start();old=station.control_epoch;original=station._write;revoked=[]
    def before_write(target,**kwargs):
        if kwargs.get('observed_context') is not None and not revoked:
            assert bus.writes==[]
            provider.revoke('REVOKED_BEFORE_WRITE_ENTRY',expected_grant_id=grant['grant_id'],expected_control_epoch=old)
            revoked.append(True)
        return original(target,**kwargs)
    station._write=before_write;station.stop_event=StopAfter();station._work()
    assert station.error is None and station.mode=='idle' and revoked==[True]
    assert samples==[] and len(bus.writes)==1
    assert list(bus.writes[0][1].values())==[0.,0.,0.,0.,0.,50.]
    assert provider.publication_gate.status()['last_measured_hold']['denied_native_epoch']==old


def test_revoke_while_target_waits_for_original_bus_lock_is_seen_at_write_admission(tmp_path,monkeypatch):
    import threading
    station,provider,selected,start,bus,samples,tick,StopAfter,_=worker_rig(tmp_path,monkeypatch)
    grant=start();old=station.control_epoch;entered=threading.Event();release=threading.Event();original=station.bus_lock
    class WaitingBusLock:
        count=0
        def __enter__(self):
            self.count+=1
            if self.count==3:  # two original measured reads then the target write
                entered.set();assert release.wait(2)
            return original.__enter__()
        def __exit__(self,*args):return original.__exit__(*args)
    station.bus_lock=WaitingBusLock();station.stop_event=StopAfter()
    software_positions=station._positions
    def measured_read(role):
        with station.bus_lock:return software_positions(role)
    station._positions=measured_read
    worker=threading.Thread(target=station._work);worker.start()
    try:
        assert entered.wait(1) and bus.writes==[]
        assert provider.revoke('REVOKED_DURING_BUS_WAIT',expected_grant_id=grant['grant_id'],expected_control_epoch=old)['revoked']
    finally:release.set();worker.join(3)
    assert not worker.is_alive() and station.error is None and station.mode=='idle'
    assert samples==[] and len(bus.writes)==1
    assert list(bus.writes[0][1].values())==[0.,0.,0.,0.,0.,50.]


def armed_worker_fixture(tmp_path,monkeypatch):
    station,provider,selected,start,bus,samples,tick,StopAfter,active=worker_rig(tmp_path,monkeypatch)
    monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:tick[0])
    grant=start();station.stop_event=StopAfter();station._work()
    interlock=provider.cooperative_interlock;actor=grant['actor_id']
    body={'grant_id':grant['grant_id'],'expected_control_epoch':grant['control_epoch'],'expected_revision':0}
    bound=interlock.dispatch('bind',body,authenticated_actor=actor)
    armed=interlock.dispatch('arm',{**body,'interlock_id':bound['interlock_id'],'expected_revision':bound['revision']},authenticated_actor=actor)
    assert armed['armed'] and not armed['capability_ready']  # no invented gripper-direction measurement
    samples.clear();bus.writes.clear()
    return station,provider,selected,bus,samples,tick,StopAfter,armed


def test_actual_worker_arm_interlock_denies_new_relative_target_and_retains_latch_after_original_measured_hold(tmp_path,monkeypatch):
    station,provider,selected,bus,samples,tick,StopAfter,armed=armed_worker_fixture(tmp_path,monkeypatch)
    position=station._positions
    station._positions=lambda role:[20.,0.,0.,0.,0.,50.] if role=='leader' else position(role)
    station.stop_event=StopAfter();station._work()
    assert station.error is None and station.mode=='idle' and samples==[]
    assert len(bus.writes)==1 and list(bus.writes[0][1].values())==[0.,0.,0.,0.,0.,50.]
    state=provider.publication_gate.status()
    assert state['last_decision']['cooperative_hold_interlock']['interlock_id']==armed['interlock_id']
    assert state['last_decision']['reason']=='DUAL_HOLD_REQUIRES_EXACT_FROZEN_RECEIVER_ARM5'
    assert provider.cooperative_interlock.status()['armed'] is True
    assert state['last_measured_hold']['original_position_write_returned'] is True


@pytest.mark.parametrize('retained_state',['armed','corrupt_WAL'])
def test_restarted_original_worker_cannot_bypass_retained_interlock_through_unowned_legacy_epoch(tmp_path,monkeypatch,retained_state):
    station,provider,selected,bus,samples,tick,StopAfter,armed=armed_worker_fixture(tmp_path,monkeypatch)
    if retained_state=='corrupt_WAL':provider.cooperative_interlock.path.write_bytes(b'{incomplete-write-ahead-record')
    newer,new_provider,selected,start,new_bus,new_samples,new_tick,NewStopAfter,active=worker_rig(tmp_path,monkeypatch)
    monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:new_tick[0])
    assert new_provider.grant is None and new_provider.cooperative_interlock.status()['armed'] is True
    newer.command('teleop/start',dict(expected_control_epoch=newer.control_epoch,observing=True))
    active[0]=True;new_bus.writes.clear();newer.stop_event=NewStopAfter();newer._work()
    assert newer.error is None and newer.mode=='idle' and new_samples==[]
    assert len(new_bus.writes)==1 and list(new_bus.writes[0][1].values())==[0.,0.,0.,0.,0.,50.]
    decision=new_provider.publication_gate.status()['last_decision']
    assert decision['managed_native_epoch'] is False and decision['grant_id'] is None
    assert decision['cooperative_hold_interlock']['allowed'] is False
    assert new_provider.cooperative_interlock.status()['armed'] is True
