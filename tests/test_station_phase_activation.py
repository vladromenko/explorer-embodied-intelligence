"""Source-atomic phase CONTROL with ORIGINAL Station/fake buses, never devices."""
from copy import deepcopy
import hashlib
import json
import threading

import pytest

from explorer_shared import station_cooperative_interlock as module
from explorer_shared.station_cooperative_interlock import StationCooperativeInterlock
from test_station_hold_successor import rig,invoke,authorize
from test_station_publication_gate import worker_rig


ACTOR='SOFTWARE-TEST-OPERATOR'


def fixture(tmp_path,monkeypatch,*,denial=False,armed=True,mode='leader'):
    if mode=='leader':values=rig(tmp_path,monkeypatch,denial=denial,armed=armed)
    else:
        # Same exact source model binding through the original policy operation.
        import uuid
        from explorer_shared.station_observed_activation import CONTRACT
        selected=dict(purpose='cooperative05',input_mode='policy',skill_id=str(uuid.uuid4()),skill_version=1,spec_sha256='b'*64,
            action_contract_id=CONTRACT,source_domain='PHYSICAL',cooperative_session_id='fake-session',coordinator_epoch='fake-coordinator',
            object_id='fake-object',model_job_id='1'*32,checkpoint_content_sha256='c'*64)
        station,provider,binding,start,bus,samples,tick,StopAfter,leader=worker_rig(tmp_path,monkeypatch,binding_override=selected)
        monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:tick[0])
        grant=authorize(provider,binding,ACTOR);old=provider.command(grant['grant_id'],binding,'policy/start',{'expected_control_epoch':grant['control_epoch']})['grant']
        station.stop_event=StopAfter();station._work();wrapper=provider.cooperative_interlock
        bound=wrapper.dispatch('bind',{'grant_id':old['grant_id'],'expected_control_epoch':old['control_epoch'],'expected_revision':0},authenticated_actor=ACTOR)
        if armed:wrapper.dispatch('arm',{'grant_id':old['grant_id'],'expected_control_epoch':old['control_epoch'],
            'interlock_id':bound['interlock_id'],'expected_revision':bound['revision']},authenticated_actor=ACTOR)
        initial=wrapper.helper.snapshot_state()
        if denial:
            provider.revoke('EXPLICIT_FIXTURE_REVOKE',expected_grant_id=old['grant_id'],expected_control_epoch=old['control_epoch'])
            station.stop_event=StopAfter();station._work()
        else:provider.command(old['grant_id'],binding,'stop',{'expected_control_epoch':old['control_epoch']})
        tick[0]+=100_000_000;station.stop_event=StopAfter();station._work();fresh=authorize(provider,binding,ACTOR)
        body={'grant_id':fresh['grant_id'],'expected_control_epoch':fresh['control_epoch'],'interlock_id':initial['interlock_id'],
            'expected_revision':initial['revision'],'previous_grant_id':old['grant_id'],'previous_control_epoch':old['control_epoch'],
            'observing':True,'user_observed_test_confirmed':True}
        values=wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    invoke(wrapper,body);state=wrapper.helper.snapshot_state()
    activate={'grant_id':fresh['grant_id'],'expected_control_epoch':fresh['control_epoch'],'interlock_id':state['interlock_id'],
        'expected_revision':state['revision'],'phase_id':'receiver_grasp','observing':True,'user_observed_test_confirmed':True,'neutral':True}
    return wrapper,station,provider,binding,bus,tick,StopAfter,fresh,activate,state


def activate(wrapper,body):return wrapper.dispatch('phase/activate',body,authenticated_actor=ACTOR)


@pytest.mark.parametrize('mode',['leader','policy'])
@pytest.mark.parametrize('denial',[False,True])
def test_atomic_original_start_keeps_latch_and_real_old_receipt_until_first_actual_new_epoch_return(tmp_path,monkeypatch,mode,denial):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch,mode=mode,denial=denial)
    previous=deepcopy(station.last_command);goal=station.goal_deadline;reply=activate(wrapper,body)
    assert reply['native_command_called'] and not reply['dispatch_claimed_from_HTTP_acceptance']
    assert wrapper.helper.armed and not wrapper.status()['parked_observation_only']
    assert station.last_command==previous and station.control_epoch!=fresh['control_epoch']
    event=reply['activation'];assert event['preceding_actual_station_last_command']==previous and not event['dispatch_or_attainment_claimed_from_start']
    assert event['preceding_actual_station_last_command_sha256']==hashlib.sha256(module.canonical(previous)).hexdigest()
    assert wrapper.helper.owner==event['new_owner'] and wrapper.helper.frozen['held_successor']==parked['frozen']['held_successor']
    assert wrapper.helper.frozen['native6']==parked['frozen']['native6'] and wrapper.helper.revision==parked['revision']+1
    assert json.loads(wrapper.path.read_bytes())['pending'] is None
    before=len(bus.writes);station.stop_event=StopAfter();station._work()
    assert station.mode==('teleoperation' if mode=='leader' else 'policy') and station.error is None
    assert len(bus.writes)==before+3 and station.last_command['control_epoch']==reply['grant']['control_epoch']
    assert wrapper.check_publication(station.control_epoch,station.mode,station.last_command['dispatched'])['allowed']
    if mode=='leader':assert station.goal_deadline==goal  # No added goal/lease duration.


def test_ordinary_start_cannot_bypass_atomic_route_or_emit_enable_hold(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    before=deepcopy(bus.writes)
    with pytest.raises(ValueError,match='atomic explicit USER'):provider.command(fresh['grant_id'],binding,'teleop/start',{'expected_control_epoch':fresh['control_epoch']})
    assert station.mode=='idle' and bus.writes==before and wrapper.helper.snapshot_state()==parked


@pytest.mark.parametrize('changed',['pose','stale','future','frame','epoch','grant','actor','object','calibration','revision','neutral','confirm','numeric','missing'])
def test_changed_or_unproven_activation_never_calls_native_start(tmp_path,monkeypatch,changed):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    if changed=='pose':station.measurements['follower'][0]=1.
    elif changed=='stale':station.measurements['monotonic_ns']=tick[0]-500_000_001
    elif changed=='future':station.measurements['monotonic_ns']=tick[0]+1
    elif changed=='frame':provider.grant['data']['displayed_frame']['received_monotonic_ns']=0
    elif changed=='epoch':station.control_epoch='foreign-epoch'
    elif changed=='grant':body['grant_id']='foreign-grant'
    elif changed=='actor':provider.grant['data']['actor_id']='foreign-actor'
    elif changed=='object':provider.grant['data']['binding']['object_id']='foreign-object'
    elif changed=='calibration':station.profiles['follower']['calibration_sha256']='f'*64
    elif changed=='revision':body['expected_revision']-=1
    elif changed=='neutral':body['neutral']=False
    elif changed=='confirm':body['user_observed_test_confirmed']=False
    elif changed=='numeric':body['native6']=[0.]*6
    else:body.pop('interlock_id')
    before=deepcopy(bus.writes)
    with pytest.raises(ValueError):activate(wrapper,body)
    assert bus.writes==before and wrapper.helper.snapshot_state()==parked


def test_new_phase_stop_can_adopt_new_actual_hold_without_reusing_previous_origin(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    reply=activate(wrapper,body);station.stop_event=StopAfter();station._work();active=wrapper.helper.snapshot_state()
    provider.command(reply['grant']['grant_id'],binding,'stop',{'expected_control_epoch':reply['grant']['control_epoch']})
    tick[0]+=100_000_000;station.stop_event=StopAfter();station._work();new=authorize(provider,binding,ACTOR)
    result=invoke(wrapper,{'grant_id':new['grant_id'],'expected_control_epoch':new['control_epoch'],'interlock_id':active['interlock_id'],
        'expected_revision':active['revision'],'previous_grant_id':reply['grant']['grant_id'],'previous_control_epoch':reply['grant']['control_epoch'],
        'observing':True,'user_observed_test_confirmed':True})
    lineage=wrapper.helper.frozen['held_successor'];assert result['parked_observation_only'] and wrapper.helper.armed
    assert lineage['origin']['origin_sha256']!=parked['frozen']['held_successor']['origin']['origin_sha256']
    assert lineage['previous_hold_origins']==[parked['frozen']['held_successor']['origin']]
    assert lineage['origin']['raw_receipt']==station.last_command and len(lineage['successions'])==2
    assert wrapper.helper.frozen['phase_activations']==active['frozen']['phase_activations']
    next_state=wrapper.helper.snapshot_state()
    next_reply=activate(wrapper,{**body,'grant_id':new['grant_id'],'expected_control_epoch':new['control_epoch'],
        'expected_revision':next_state['revision'],'phase_id':'receiver_align_release'})
    assert next_reply['phase_activation_current'] and wrapper.helper.armed
    assert len(wrapper.helper.frozen['phase_activations'])==2 and wrapper.helper.frozen['active_phase']['origin_sha256']==lineage['origin']['origin_sha256']


@pytest.mark.parametrize('armed',[False,True])
def test_prepared_wal_precedes_original_start_and_restart_blocks_every_target_even_unarmed(tmp_path,monkeypatch,armed):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch,armed=armed)
    original=station.command;entered=[]
    def observed(operation,payload):
        if operation=='teleop/start':
            saved=json.loads(wrapper.path.read_bytes());assert saved['pending']['operation']=='phase/activate' and saved['state']==parked
            restored=StationCooperativeInterlock(provider,wrapper.path)
            assert restored.status()['all_target_writes_blocked']
            assert not restored.check_publication('fresh-unowned','policy',[0.]*6)['allowed']
            entered.append(True)
        return original(operation,payload)
    monkeypatch.setattr(station,'command',observed)
    assert activate(wrapper,body)['native_command_called'] and entered==[True]


def test_metadata_fsync_never_holds_station_publication_or_provider_lock(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch);original=module._write;calls=[]
    def observed(*args,**kwargs):
        assert not station.lock._is_owned() and not provider.lock._is_owned() and not provider.publication_gate.publication_lock._is_owned()
        calls.append(True);return original(*args,**kwargs)
    monkeypatch.setattr(module,'_write',observed);activate(wrapper,body);assert len(calls)==2


@pytest.mark.parametrize('armed',[False,True])
def test_worker_can_return_real_write_during_final_fsync_but_foreign_epoch_never_uses_prepared_exception(tmp_path,monkeypatch,armed):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch,armed=armed)
    original=module._write;entered,release=threading.Event(),threading.Event();replies=[];errors=[]
    def delayed(path,value):
        if value.get('pending') is None:entered.set();assert release.wait(2)
        return original(path,value)
    def request():
        try:replies.append(activate(wrapper,body))
        except Exception as error:errors.append(error)
    monkeypatch.setattr(module,'_write',delayed);thread=threading.Thread(target=request);thread.start();assert entered.wait(1)
    try:
        assert wrapper.phase_prepared['linked'] and not wrapper.check_publication('foreign','policy',[0.]*6)['allowed']
        old_command=deepcopy(station.last_command);before=len(bus.writes);station.stop_event=StopAfter();station._work()
        assert station.mode=='teleoperation' and len(bus.writes)==before+3
        assert station.last_command!=old_command and station.last_command['control_epoch']==station.control_epoch
        restored=StationCooperativeInterlock(provider,wrapper.path);assert restored.status()['all_target_writes_blocked']
    finally:release.set();thread.join(2)
    assert not thread.is_alive() and not errors and replies and wrapper.phase_prepared is None


def test_final_seal_failure_installs_global_fault_before_exact_scoped_stop_and_keeps_real_return(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    original_write=module._write;original_command=station.command;actual=[];stops=[]
    def sealing(path,value):
        if value.get('pending') is None:
            station.stop_event=StopAfter();station._work();actual.append(deepcopy(station.last_command))
            raise OSError('fixture: phase final fsync failed')
        return original_write(path,value)
    def command(operation,payload):
        if operation=='stop':assert wrapper.fault is not None;stops.append(deepcopy(payload))
        return original_command(operation,payload)
    monkeypatch.setattr(module,'_write',sealing);monkeypatch.setattr(station,'command',command)
    with pytest.raises(OSError,match='phase final'):activate(wrapper,body)
    assert actual and stops==[{'expected_control_epoch':actual[0]['control_epoch']}]
    assert station.mode=='idle' and wrapper.helper.armed and wrapper.status()['all_target_writes_blocked']
    assert station.preceding_command==actual[0] and json.loads(wrapper.path.read_bytes())['pending'] is not None
    assert StationCooperativeInterlock(provider,wrapper.path).status()['all_target_writes_blocked']


def test_revoke_and_new_idle_user_during_prepared_wal_cannot_start_or_consume_foreign_grant(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    original=module._write;entered,release=threading.Event(),threading.Event();errors=[]
    def delayed(path,value):
        if value.get('pending') is not None:entered.set();assert release.wait(2)
        return original(path,value)
    def request():
        try:activate(wrapper,body)
        except ValueError as error:errors.append(error)
    monkeypatch.setattr(module,'_write',delayed);thread=threading.Thread(target=request);thread.start();assert entered.wait(1)
    before=deepcopy(bus.writes)
    provider.revoke('EXPLICIT_USER_REPLACED',expected_grant_id=fresh['grant_id'],expected_control_epoch=fresh['control_epoch'])
    foreign=authorize(provider,binding,ACTOR);release.set();thread.join(2)
    assert errors and not thread.is_alive() and provider.grant['data']['grant_id']==foreign['grant_id']
    assert wrapper.helper.snapshot_state()==parked and bus.writes==before and wrapper.phase_prepared is None


def test_changed_native_start_baseline_is_faulted_and_only_its_actual_epoch_is_stopped(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    original=station._positions
    def changed(role):
        result=original(role)
        if role=='follower':result[0]=1.
        return result
    monkeypatch.setattr(station,'_positions',changed)
    with pytest.raises(ValueError,match='baseline changed'):activate(wrapper,body)
    assert station.mode=='idle' and wrapper.status()['all_target_writes_blocked'] and wrapper.helper.snapshot_state()==parked
    assert station.last_command['operation']=='hold_current_position' and station.last_command['dispatched'][0]==1.


def test_unknown_original_start_return_never_promotes_unknown_current_epoch_or_fabricates_new_write(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch);original=station.command;started=[]
    def unknown(operation,payload):
        result=original(operation,payload)
        if operation=='teleop/start':started.append(result['control_epoch']);raise RuntimeError('fixture: original start return unknown')
        return result
    monkeypatch.setattr(station,'command',unknown)
    with pytest.raises(RuntimeError,match='start return unknown'):activate(wrapper,body)
    assert started and station.mode=='idle' and wrapper.helper.snapshot_state()==parked
    assert wrapper.status()['all_target_writes_blocked'] and provider.grant is None
    assert station.last_command['operation']=='hold_current_position' and station.last_command['sequence']==-1


def test_final_seal_failure_cannot_stop_a_new_foreign_user_after_original_phase_was_scoped_stopped(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    original=module._write;foreign=[];written=[]
    def failed(path,value):
        if value.get('pending') is None:
            owned=deepcopy(provider.grant['data'])
            provider.command(owned['grant_id'],binding,'stop',{'expected_control_epoch':owned['control_epoch']})
            foreign.append(authorize(provider,binding,ACTOR));written.append(deepcopy(bus.writes))
            raise OSError('fixture: final fsync failed after new USER grant')
        return original(path,value)
    monkeypatch.setattr(module,'_write',failed)
    with pytest.raises(OSError,match='new USER'):activate(wrapper,body)
    assert provider.grant['data']['grant_id']==foreign[0]['grant_id'] and not provider.grant['revoked'].is_set()
    assert bus.writes==written[0] and station.control_epoch==foreign[0]['control_epoch']
    assert wrapper.status()['all_target_writes_blocked'] and wrapper.helper.armed


def test_permission_expired_during_successful_final_seal_cannot_return_a_current_activation_receipt(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch);original=module._write
    def elapsed(path,value):
        result=original(path,value)
        if value.get('pending') is None:tick[0]+=3_000_000_000
        return result
    monkeypatch.setattr(module,'_write',elapsed)
    with pytest.raises(ValueError,match='expired'):activate(wrapper,body)
    assert station.mode=='idle' and provider.grant is None and wrapper.helper.armed and wrapper.fault is None
    assert not wrapper.status()['binding_current'] and not wrapper.check_publication('unowned','policy',[0.]*6)['allowed']
    assert json.loads(wrapper.path.read_bytes())['pending'] is None
    assert station.last_command['operation']=='hold_current_position'


def test_permission_revoked_inside_one_original_start_is_never_attached_or_replayed(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch);original=station.command
    def revoked(operation,payload):
        result=original(operation,payload)
        if operation=='teleop/start':provider.revoke('EXPLICIT_USER_REVOKE_DURING_START',expected_grant_id=fresh['grant_id'],expected_control_epoch=fresh['control_epoch'])
        return result
    monkeypatch.setattr(station,'command',revoked)
    with pytest.raises(ValueError,match='Late native command'):activate(wrapper,body)
    assert station.mode=='idle' and provider.grant is None and wrapper.helper.snapshot_state()==parked
    assert wrapper.status()['all_target_writes_blocked'] and json.loads(wrapper.path.read_bytes())['pending'] is not None


def test_atomic_phase_keeps_first_five_frozen_and_unknown_gripper_direction_frozen(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    reply=activate(wrapper,body);held=wrapper.helper.frozen['native6'];moved=deepcopy(held);moved[0]+=1
    assert not wrapper.check_publication(station.control_epoch,station.mode,moved)['allowed']
    for gripper in (held[5]-1,held[5]+1):
        changed=deepcopy(held);changed[5]=gripper
        assert not wrapper.check_publication(station.control_epoch,station.mode,changed)['allowed']
    old_position=station._positions
    def leader_moved(role):
        value=old_position(role)
        if role=='leader':value[0]+=1
        return value
    monkeypatch.setattr(station,'_positions',leader_moved);prior=deepcopy(station.last_command)
    before=len(bus.writes);station.stop_event=StopAfter();station._work()
    assert station.mode=='idle' and len(bus.writes)==before+1 and station.last_command==prior
    assert provider.publication_gate.last_measured_hold['denied_native_epoch']==reply['grant']['control_epoch']


def test_initial_preceding_receipt_cannot_be_changed_or_relabelled_to_pass_phase_gate(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,fresh,body,parked=fixture(tmp_path,monkeypatch)
    reply=activate(wrapper,body);held=wrapper.helper.frozen['native6'];actual=deepcopy(station.last_command)
    assert wrapper.check_publication(station.control_epoch,station.mode,held)['allowed']
    # A modified old-epoch command no longer matches the sealed actual source
    # history and has no genuine new-epoch return. The helper cannot infer it.
    changed=deepcopy(actual);changed['source_observation']={'fake':'browser'}
    assert not wrapper.check_publication(station.control_epoch,station.mode,held,last_command=changed)['allowed']
    assert station.last_command==actual and wrapper.helper.armed and provider.grant['data']['grant_id']==reply['grant']['grant_id']
