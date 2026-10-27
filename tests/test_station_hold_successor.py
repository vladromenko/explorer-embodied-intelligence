"""Actual original Station worker/STOP and COMMON/WAL, fake buses only."""
from copy import deepcopy
import json
import threading

import pytest

from explorer_shared import station_cooperative_interlock as module
from explorer_shared.station_cooperative_interlock import StationCooperativeInterlock
from test_station_publication_gate import worker_rig


def rig(tmp_path,monkeypatch,*,denial=False,armed=True):
    station,provider,binding,start,bus,samples,tick,StopAfter,leader=worker_rig(tmp_path,monkeypatch)
    monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:tick[0])
    old=start();station.stop_event=StopAfter();station._work();wrapper=provider.cooperative_interlock
    bound=wrapper.dispatch('bind',{'grant_id':old['grant_id'],'expected_control_epoch':old['control_epoch'],'expected_revision':0},
        authenticated_actor=old['actor_id'])
    if armed:wrapper.dispatch('arm',{'grant_id':old['grant_id'],'expected_control_epoch':old['control_epoch'],
        'interlock_id':bound['interlock_id'],'expected_revision':bound['revision']},authenticated_actor=old['actor_id'])
    initial=wrapper.helper.snapshot_state()
    if denial:
        provider.revoke('EXPLICIT_FIXTURE_USER_REVOKE',expected_grant_id=old['grant_id'],expected_control_epoch=old['control_epoch'])
        station.stop_event=StopAfter();station._work()
        assert provider.publication_gate.last_measured_hold['original_position_write_returned']
    else:provider.command(old['grant_id'],binding,'stop',{'expected_control_epoch':old['control_epoch']})
    # Actual idle worker reads refresh the existing measured cache; no target writes.
    tick[0]+=100_000_000;station.stop_event=StopAfter();station._work()
    fresh=authorize(provider,binding,old['actor_id'])
    body={'grant_id':fresh['grant_id'],'expected_control_epoch':fresh['control_epoch'],'interlock_id':initial['interlock_id'],
        'expected_revision':initial['revision'],'previous_grant_id':old['grant_id'],'previous_control_epoch':old['control_epoch'],
        'observing':True,'user_observed_test_confirmed':True}
    return wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial


def authorize(provider,binding,actor):
    challenge=provider.challenge(binding)
    return provider.authorize({'binding':binding,'challenge_id':challenge['challenge_id'],'displayed_jpeg_sha256':challenge['frame']['jpeg_sha256'],
        'expected_control_epoch':challenge['control_epoch'],'actor_id':actor,'observing':True,'robot_workspace_checked':True,'user_observed_test_confirmed':True})


def invoke(wrapper,body,actor='SOFTWARE-TEST-OPERATOR'):return wrapper.dispatch('successor',body,authenticated_actor=actor)


@pytest.mark.parametrize('denial',[False,True])
def test_actual_returned_measured_hold_and_new_user_grant_adopt_without_command_or_unarming(tmp_path,monkeypatch,denial):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch,denial=denial)
    writes=deepcopy(bus.writes);result=invoke(wrapper,body);state=wrapper.helper.snapshot_state()
    assert state['armed'] and state['revision']==initial['revision']+1 and state['interlock_id']==initial['interlock_id']
    assert state['owner']['grant_id']==fresh['grant_id'] and state['owner']['control_epoch']==station.control_epoch
    assert state['frozen']['held_successor']['initial_reference']==initial['frozen'] and state['pending'] is None
    origin=result['source_hold_origin'];assert origin['held_native6']==[0.,0.,0.,0.,0.,50.]
    assert origin['basis']==('original_denial_measured_hold' if denial else 'original_scoped_stop_measured_hold')
    if denial:
        assert origin['reference_view']['sequence'] is None and origin['reference_view']['derived_metadata_view_not_station_last_command']
        assert station.last_command['control_epoch']==old['control_epoch']  # Kept original historical command, never relabelled.
    else:assert origin['reference_view']==station.last_command and origin['reference_view']['sequence']==-1
    assert bus.writes==writes and result['native_commands_sent']==0 and not result['physical_execution_authorized']
    assert wrapper.status()['parked_observation_only'] and json.loads(wrapper.path.read_bytes())['pending'] is None
    for epoch,mode in ((fresh['control_epoch'],'idle'),(old['control_epoch'],'teleoperation'),('unowned','policy')):
        assert not wrapper.check_publication(epoch,mode,[0.,0.,0.,0.,0.,50.])['allowed']


@pytest.mark.parametrize('failure',['old_owner','object','calibration','epoch','changed_pose','stale_measurement','future_measurement','old_image','not_explicit','numeric'])
def test_unknown_changed_or_stale_successor_facts_never_change_latch_or_write(tmp_path,monkeypatch,failure):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch)
    if failure=='old_owner':body['previous_grant_id']='wrong-grant'
    elif failure=='object':provider.grant['data']['binding']['object_id']='another-object'
    elif failure=='calibration':station.profiles['follower']['calibration_sha256']='f'*64
    elif failure=='epoch':station.control_epoch='another-idle-epoch'
    elif failure=='changed_pose':station.measurements['follower'][5]=49.
    elif failure=='stale_measurement':station.measurements['monotonic_ns']=tick[0]-500_000_001
    elif failure=='future_measurement':station.measurements['monotonic_ns']=tick[0]+1
    elif failure=='old_image':provider.grant['data']['displayed_frame']['received_monotonic_ns']=station.last_command['monotonic_ns']-1
    elif failure=='not_explicit':body['user_observed_test_confirmed']=False
    elif failure=='numeric':body['held_native6']=[0.]*6
    writes=deepcopy(bus.writes)
    with pytest.raises(ValueError):invoke(wrapper,body)
    assert wrapper.helper.snapshot_state()==initial and wrapper.status()['armed'] and bus.writes==writes


def test_idle_after_unreceipted_ttl_is_not_a_hold_successor(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch)
    # An old historical last_command plus an arbitrary idle epoch is not evidence.
    station.last_command=deepcopy(initial['frozen']['original_last_command'])
    with pytest.raises(ValueError,match='actual original scoped STOP|Actual original scoped STOP'):invoke(wrapper,body)
    assert wrapper.helper.snapshot_state()==initial and wrapper.status()['armed']


@pytest.mark.parametrize('changed',['actor','object','spec','run'])
def test_genuine_new_common_grant_for_different_owner_or_task_cannot_adopt_old_latch(tmp_path,monkeypatch,changed):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch)
    provider.revoke('SOURCE_USER_REPLACED',expected_grant_id=fresh['grant_id'],expected_control_epoch=fresh['control_epoch'])
    selected=deepcopy(binding);actor=old['actor_id']
    if changed=='actor':actor='OTHER-SOFTWARE-OPERATOR'
    elif changed=='object':selected['object_id']='another-object'
    elif changed=='spec':selected['spec_sha256']='f'*64
    else:selected['cooperative_session_id']='another-session'
    new=authorize(provider,selected,actor);body.update(grant_id=new['grant_id'],expected_control_epoch=new['control_epoch'])
    writes=deepcopy(bus.writes)
    with pytest.raises(ValueError,match='previous owner|same-source'):invoke(wrapper,body,actor=actor)
    assert wrapper.helper.snapshot_state()==initial and bus.writes==writes


def test_another_actual_idle_stop_without_a_hold_write_cannot_reuse_the_old_receipt(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch)
    writes=deepcopy(bus.writes);old_hold=deepcopy(station.last_command)
    provider.command(fresh['grant_id'],binding,'stop',{'expected_control_epoch':fresh['control_epoch']})
    assert station.last_command==old_hold and station.control_epoch!=old_hold['control_epoch'] and bus.writes==writes
    tick[0]+=100_000_000;station.stop_event=StopAfter();station._work();new=authorize(provider,binding,old['actor_id'])
    body.update(grant_id=new['grant_id'],expected_control_epoch=new['control_epoch'])
    with pytest.raises(ValueError):invoke(wrapper,body)
    assert wrapper.helper.snapshot_state()==initial and bus.writes==writes


def test_sealed_same_hold_can_pass_to_another_explicit_new_user_grant_without_reusing_old_authority(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch)
    first=invoke(wrapper,body);before=wrapper.helper.snapshot_state();writes=deepcopy(bus.writes)
    provider.revoke('EXPLICIT_USER_NEW_OBSERVATION',expected_grant_id=fresh['grant_id'],expected_control_epoch=fresh['control_epoch'])
    tick[0]+=100_000_000;station.stop_event=StopAfter();station._work();new=authorize(provider,binding,old['actor_id'])
    again={**body,'grant_id':new['grant_id'],'expected_control_epoch':new['control_epoch'],'expected_revision':before['revision'],
        'previous_grant_id':fresh['grant_id'],'previous_control_epoch':fresh['control_epoch']}
    result=invoke(wrapper,again)
    assert result['source_hold_origin']==first['source_hold_origin'] and bus.writes==writes
    assert len(wrapper.helper.frozen['held_successor']['successions'])==2 and wrapper.helper.armed
    with pytest.raises(ValueError):invoke(wrapper,again)
    assert wrapper.helper.revision==before['revision']+1


def test_parked_bound_state_cannot_bypass_through_bind_or_arm_and_restart_keeps_all_targets_blocked(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch,armed=False)
    invoke(wrapper,body);state=wrapper.helper.snapshot_state();assert not state['armed'] and state['frozen']['parked_observation_only']
    for operation in ('bind','arm'):
        with pytest.raises(ValueError,match='separate explicit native'):wrapper.dispatch(operation,{'grant_id':fresh['grant_id'],
            'expected_control_epoch':fresh['control_epoch'],'expected_revision':state['revision'],'interlock_id':state['interlock_id']},authenticated_actor=old['actor_id'])
    restored=StationCooperativeInterlock(provider,wrapper.path)
    assert restored.helper.snapshot_state()==state and not restored.check_publication('unowned','policy',[0.]*6)['allowed']
    station.boot='new-boot';station.clock_domain='pi:new-boot';station.control_epoch='fresh-process-idle'
    assert not restored.status()['binding_current'] and not restored.check_publication('fresh-process-idle','idle',[0.]*6)['allowed']


def test_parked_publication_check_is_memory_only_and_blocks_even_exact_same_target(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch)
    invoke(wrapper,body)
    def prohibited(*args,**kwargs):raise AssertionError('No camera/RPC/serial/file operation in parked publication gate')
    monkeypatch.setattr(module,'_read',prohibited);monkeypatch.setattr(provider,'check',prohibited)
    monkeypatch.setattr(station,'_positions',prohibited);monkeypatch.setattr(station.camera,'latest',prohibited)
    assert not wrapper.check_publication(fresh['control_epoch'],'idle',[0.,0.,0.,0.,0.,50.])['allowed']
    assert wrapper.status()['parked_observation_only'] and wrapper.status()['armed']


def test_final_fsync_failure_after_successor_retains_global_latch_and_pending_wal(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch);original=module._write;count=[]
    def failed(path,value):
        count.append(value)
        if len(count)==2:raise OSError('fixture: successor final fsync failure')
        return original(path,value)
    monkeypatch.setattr(module,'_write',failed)
    with pytest.raises(OSError,match='successor final'):invoke(wrapper,body)
    assert wrapper.status()['armed'] and wrapper.status()['all_target_writes_blocked']
    assert json.loads(wrapper.path.read_bytes())['pending'] is not None
    assert StationCooperativeInterlock(provider,wrapper.path).status()['all_target_writes_blocked']


def test_revoke_during_wal_blocks_late_successor_and_does_not_capture_new_owner(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch)
    original=module._write;entered,release=threading.Event(),threading.Event();errors=[]
    def delayed(path,value):
        if value.get('pending') is not None:entered.set();assert release.wait(2)
        return original(path,value)
    monkeypatch.setattr(module,'_write',delayed)
    def successor():
        try:invoke(wrapper,body)
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=successor);thread.start();assert entered.wait(1)
    provider.revoke('EXPLICIT_SOURCE_STOP',expected_grant_id=fresh['grant_id'],expected_control_epoch=fresh['control_epoch'])
    new=authorize(provider,binding,old['actor_id']);release.set();thread.join(2)
    assert errors and not thread.is_alive() and wrapper.helper.snapshot_state()==initial
    assert provider.status()['grant']['grant_id']==new['grant_id'] and wrapper.status()['armed']
