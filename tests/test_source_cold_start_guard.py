"""Frozen original Station and COMMON with fake bus only; no hardware opened."""
from copy import deepcopy

import pytest

from explorer_shared.source_only_runtime import SourceColdStartGuard
from test_source_demo_permission import demo_rig


def rig(tmp_path,monkeypatch):
    station,provider,choice,_,bus,_,tick,_,_=demo_rig(tmp_path,monkeypatch)
    guard=SourceColdStartGuard();guard.install(station);provider.validated_start=guard.validated_start
    challenge=provider.challenge(choice)
    grant=provider.authorize(dict(binding=choice,challenge_id=challenge['challenge_id'],
        displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],expected_control_epoch=challenge['control_epoch'],
        observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True,actor_id='SOFTWARE-ONLY'))
    return station,provider,choice,bus,tick,guard,grant


def test_original_idle_worker_exception_hold_is_blocked_before_any_observed_start(tmp_path,monkeypatch):
    station,provider,choice,bus,tick,guard,grant=rig(tmp_path,monkeypatch)
    def fail_connect():raise OSError('SOFTWARE USB startup failure')
    station._connect=fail_connect;station._work()
    assert station.mode=='idle' and 'USB startup failure' in station.error
    assert 'disarmed' in station.last_hold_error and bus.writes==[]
    status=guard.status();assert status['disarmed_until_observed_start'] and status['blocked_write_attempts']==1
    assert status['forwarded_write_calls']==0 and not status['controller_configuration_written']
    station.close()
    assert bus.writes==[] and guard.status()['disarmed_until_observed_start']


@pytest.mark.parametrize('fault',['foreign_grant','old_epoch','foreign_binding','expired','revoked'])
def test_metadata_or_rejected_start_cannot_unlock_original_bus_publication(tmp_path,monkeypatch,fault):
    station,provider,choice,bus,tick,guard,grant=rig(tmp_path,monkeypatch)
    assert guard.status()['disarmed_until_observed_start'] and provider.status()['execution_permission']
    ident=grant['grant_id'];selected=deepcopy(choice);epoch=grant['control_epoch']
    if fault=='foreign_grant':ident='0'*32
    elif fault=='old_epoch':epoch='old-epoch'
    elif fault=='foreign_binding':selected['spec_sha256']='f'*64
    elif fault=='expired':tick[0]+=3_000_000_000
    else:provider.revoke('SOFTWARE-REVOKE',expected_grant_id=ident,expected_control_epoch=epoch,expected_binding=choice)
    with pytest.raises(ValueError):provider.command(ident,selected,'teleop/start',{'expected_control_epoch':epoch})
    assert guard.status()['disarmed_until_observed_start'] and bus.writes==[] and station.mode=='idle'
    with pytest.raises(RuntimeError,match='disarmed'):station._write([0,0,0,0,0,50])
    assert bus.writes==[]


def test_validated_COMMON_start_preserves_exact_original_hold_torque_target_and_scoped_stop(tmp_path,monkeypatch):
    station,provider,choice,bus,tick,guard,grant=rig(tmp_path,monkeypatch)
    wrapped=station.buses['follower'];assert wrapped is not bus and wrapped._bus is bus
    stopped=provider.command(grant['grant_id'],choice,'stop',{'expected_control_epoch':grant['control_epoch']})
    assert stopped['status']['mode']=='idle' and guard.status()['disarmed_until_observed_start'] and not bus.writes
    challenge=provider.challenge(choice)
    grant=provider.authorize(dict(binding=choice,challenge_id=challenge['challenge_id'],displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],
        expected_control_epoch=challenge['control_epoch'],observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True,actor_id='SOFTWARE-ONLY'))
    started=provider.command(grant['grant_id'],choice,'teleop/start',{'expected_control_epoch':grant['control_epoch']})
    assert not guard.status()['disarmed_until_observed_start']
    assert [row[0] for row in bus.writes]==['Goal_Position','Torque_Enable']
    assert all(value==2048 for value in bus.writes[0][1].values()) and bus.writes[0][2] is False
    assert all(value==1 for value in bus.writes[1][1].values()) and bus.writes[1][2] is False
    target=[1.,2.,3.,4.,5.,51.];station._write(target)
    assert list(bus.writes[-1][1].values())==target and bus.writes[-1][2] is True
    current=started['grant'];provider.command(current['grant_id'],choice,'stop',{'expected_control_epoch':current['control_epoch']})
    assert station.mode=='idle' and bus.writes[-1][0]=='Goal_Position'
    assert list(bus.writes[-1][1].values())==[0.,0.,0.,0.,0.,50.]
    assert guard.status()['forwarded_write_calls']==len(bus.writes)==4


def test_new_bus_assignment_is_guarded_without_second_owner_or_changed_read_interface(tmp_path,monkeypatch):
    station,_,_,bus,_,guard,_=rig(tmp_path,monkeypatch)
    station.buses['leader']=bus
    assert station.buses['leader']._bus is bus
    assert station.buses['leader'].sync_read('Present_Position')==bus.sync_read('Present_Position')
    with pytest.raises(RuntimeError):station.buses['leader'].sync_write('Torque_Enable',{'fake':1},normalize=False)
    assert not bus.writes and guard.status()['guarded_bus_roles']==['follower','leader']
    with pytest.raises(ValueError):guard.install(station)


def test_unknown_non_original_record_cannot_unlock_startup_and_new_guard_forgets_old_permission():
    guard=SourceColdStartGuard()
    with pytest.raises(ValueError):guard.validated_start({'data':{'execution_permission':True,'native_mode':'idle','grant_id':'browser-text'}},'teleop/start')
    assert guard.status()['disarmed_until_observed_start']
    assert SourceColdStartGuard().status()['disarmed_until_observed_start']
