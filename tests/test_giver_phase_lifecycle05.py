"""Explicit USER source phases over SAME native mock executor; no devices."""
from copy import deepcopy
import json

import pytest

from explorer_shared.contracts import canonical,digest
from explorer_shared import giver_hold_persistence05 as wal
from test_native_giver_persistence import fixture as original_fixture
from test_native_cooperative05 import start,message
from test_cooperative_action_constraint05 import constraint_for

ACTOR='SOFTWARE-TEST-OPERATOR'


def fixture(tmp_path,*,mode='cooperative_policy',constraint=True,armed=True,auto_park=True):
    values=original_fixture(tmp_path,bind=False)
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    activation.mode=mode;activation.clock=tap.clock;receipts={}
    def mint(identity):
        activation.id=identity;activation.valid=True
        receipts[identity]={'actor_id':ACTOR,'input_mode':mode,'native_monotonic_ns':tap.clock(),
            'camera_frame':{'received_monotonic_ns':tap.clock(),'clock_domain':tap.clock_domain,'boot_id':tap.boot_id,
                'frame_id':'fake-frame-'+identity,'jpeg_sha256':'f'*64}}
    mint('auth');activation.receipt=lambda identity:deepcopy(receipts.get(identity)) if activation.valid and identity==activation.id else None
    def invalidate(reason):
        value=activation.revoke(reason);owner.guard.revoke(reason);return value
    mux.on_invalidate=invalidate
    owner.guard.bind(binding)
    native_reference={'servo_deg':[90]*6,'boot_id':'boot','measured':False}
    tap.reference_header(native_reference,digest(native_reference),native_guard_verified=True,source_bytes=canonical(native_reference))
    model={'model_id':'actual-source-model','checkpoint_sha256':'a'*64,'spec_sha256':'b'*64}
    if mode=='cooperative_policy':
        if constraint:owner.register_constraint({**binding,'constraint':constraint_for(binding,model)})
        start(owner,mux,binding)
    else:
        arm.pub.publish(message(90));owner.manual_resume({**binding,'expected_native_generation':teleop.generation})
    owner.reference=lambda:{'servo_deg':deepcopy(tap.last_publisher['pose_deg']),'boot_id':'boot','measured':False,'phase':'command_elapsed_observation_required'}
    if armed:owner.guard.arm(binding,native_reference)
    resume=teleop.resume
    def original_resume(source,observing):
        result=resume(source,observing);teleop.lease=tick[0]+.45;return result
    teleop.resume=original_resume
    def body(**extra):
        return {**{key:owner.guard.binding[key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')},
            'expected_state_id':owner.guard.journal.state_id,'expected_revision':owner.guard.revision,
            'observing':True,'neutral':True,'user_observed_test_confirmed':True,**extra}
    scope={'control_epoch':mux.epoch} if mode=='cooperative_policy' else {'expected_native_generation':teleop.generation}
    park=owner.park(body(**scope),authenticated_actor=ACTOR) if auto_park else None
    def new_user(identity='fresh-USER'):
        previous=deepcopy(owner.guard.binding);tick[0]+=.1;mint(identity)
        return body(authorization_id=identity,park_request_id=park['park']['park_request_id'],
            previous_authorization_id=previous['authorization_id'],previous_native_generation=previous['native_generation'],
            previous_control_epoch=previous.get('native_control_epoch'))
    return values,body,new_user,park,receipts


@pytest.mark.parametrize('mode',['cooperative_policy','keyboard','browser_gamepad'])
def test_source_stop_park_new_user_adopt_and_explicit_resume_keep_original_latch_and_command_provenance(tmp_path,mode):
    values,body,new_user,park,receipts=fixture(tmp_path,mode=mode);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    assert park['native_command_called'] and owner.guard.armed and owner.guard.parked['stage']=='STOPPED_BARRIER'
    before=len(publisher.messages);drive=deepcopy(teleop.native_drive);lease=teleop.lease;registered=deepcopy(owner.action_constraint)
    adopted=owner.adopt_parked(new_user(),authenticated_actor=ACTOR)
    assert not adopted['native_command_called'] and not adopted['execution_authority']
    assert len(publisher.messages)==before and teleop.native_drive==drive and teleop.lease==lease
    origin=adopted['successor']['origin'];assert origin['state_source']=='command_estimate' and origin['measured_feedback'] is None
    assert origin['measured_feedback_mask']==[False]*6 and origin['actual_issued']['command9'][6:]==[0.,0.,0.]
    assert not origin['measured_hold_or_stationarity_claimed'] and owner.guard.armed
    request=body(park_request_id=park['park']['park_request_id'])
    if mode=='cooperative_policy':
        result=owner.begin_parked(request,authenticated_actor=ACTOR)
        assert result['native']['state']=='CLUTCHED' and teleop.stop_latched
        assert owner.guard.parked['stage']=='BEGIN_CLUTCHED' and teleop.lease==lease
        assert owner.action_constraint['constraint']==registered['constraint']
        assert owner.action_constraint['authorization_id']=='fresh-USER' and owner.action_constraint['previous_authorization_lineage']['previous_registration_sha256']==digest(registered)
        request=body(park_request_id=park['park']['park_request_id'],control_epoch=result['native']['control_epoch'])
    resumed=owner.resume_parked(request,authenticated_actor=ACTOR)
    assert resumed['native_command_called'] and owner.guard.parked is None and owner.guard.armed
    assert teleop.lease==tick[0]+.45 and not teleop.stop_latched and len(publisher.messages)==before
    assert owner.guard.grant is None and json.loads(path.read_bytes())['state']['parked'] is None
    assert owner.guard.phase_history[-1]['kind']=='EXPLICIT_USER_PHASE_RESUMED'
    resumed_origin=owner.guard.phase_history[-1]['source_command_origin']
    assert resumed_origin['actual_issued']['command9']==origin['actual_issued']['command9']
    assert resumed_origin['actual_issued']['arm_receipt']==origin['actual_issued']['arm_receipt']


@pytest.mark.parametrize('changed',['pose_reference','no_base','base_nonzero','inflight','stream','finite','fault','model','constraint','actor','mode','new_epoch','old_image','not_new_auth','state','numeric'])
def test_unproven_or_changed_successor_never_unparks_or_issues_native_commands(tmp_path,changed):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    request=new_user();before=len(publisher.messages);drive=deepcopy(teleop.native_drive);lease=teleop.lease
    if changed=='pose_reference':owner.reference=lambda:{'servo_deg':[91]*6,'boot_id':'boot','measured':False}
    elif changed=='no_base':tap.base_history.clear()
    elif changed=='base_nonzero':tap.base_history[-1]['velocity']=[.1,0,0]
    elif changed=='inflight':tap.publisher_begin()
    elif changed=='stream':arm.streaming_active=True
    elif changed=='finite':arm.lock.acquire()
    elif changed=='fault':owner.guard.reference_fault={'reason':'actual fault'}
    elif changed=='model':owner.policy['checkpoint_sha256']='d'*64
    elif changed=='constraint':owner.action_constraint=None
    elif changed=='actor':receipts['fresh-USER']['actor_id']='foreign-actor'
    elif changed=='mode':receipts['fresh-USER']['input_mode']='keyboard'
    elif changed=='new_epoch':mux.active=True
    elif changed=='old_image':receipts['fresh-USER']['camera_frame']['received_monotonic_ns']=0
    elif changed=='not_new_auth':request['authorization_id']='auth';activation.id='auth'
    elif changed=='state':request['expected_revision']-=1
    else:request['servo_deg']=[90]*6
    try:
        with pytest.raises(ValueError):owner.adopt_parked(request,authenticated_actor=ACTOR)
        assert owner.guard.parked is not None and owner.guard.armed and len(publisher.messages)==before
        assert teleop.native_drive==drive and teleop.lease==lease
    finally:
        if changed=='inflight':tap.publisher_end()
        if changed=='finite':arm.lock.release()


def test_parked_old_original_publisher_and_generic_begin_cannot_bypass_explicit_new_user_phase(tmp_path):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    before=len(publisher.messages)
    with pytest.raises(ValueError,match='parked-phase'):owner.begin({**binding,'neutral':True,'model':owner.policy})
    with pytest.raises(ValueError,match='PARKED_GIVER'):arm.pub.publish(message(100))
    assert len(publisher.messages)==before and owner.guard.armed and owner.guard.parked is not None


def test_restart_keeps_parked_unarmed_barrier_and_never_restores_a_grant(tmp_path):
    values,body,new_user,park,receipts=fixture(tmp_path,armed=False);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    restored,*_=original_fixture(tmp_path,bind=False)
    assert not restored.guard.armed and restored.guard.parked is not None and restored.guard.journal.recovery_required
    with pytest.raises(ValueError,match='DURABLE_GIVER'):restored.mux.arm.pub.publish(message(100))
    assert restored.original_publisher.messages==[] and restored.guard.grant is None


def test_policy_after_successor_uses_exact_carried_constraint_raw9_and_original_executor(tmp_path):
    from explorer_shared.cooperative_action_constraint05 import project_stationary_action
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    owner.adopt_parked(new_user(),authenticated_actor=ACTOR);token=park['park']['park_request_id']
    begun=owner.begin_parked(body(park_request_id=token),authenticated_actor=ACTOR)
    epoch=begun['native']['control_epoch'];owner.resume_parked(body(park_request_id=token,control_epoch=epoch),authenticated_actor=ACTOR)
    raw=[90.]*6+[.1,.2,.3];issued,projection=project_stationary_action(raw,owner.action_constraint['constraint'])
    lease=teleop.lease
    accepted=owner.input({**owner.guard.binding,'control_epoch':epoch,'model':owner.policy,'source_sequence':1,'command_id':'new-phase-target',
        'action9':issued,'raw_policy_action9':raw,'coordinator_action_projection':projection,'source_observation':owner.observation()})
    assert accepted['accepted'] and not accepted['native_dispatched'] and teleop.lease==lease
    record={'command_generation':mux.generation,'input_received_at':arm.stream_intent['received_at']}
    arm._publish_command([90]*6,80,record)
    actual=tap.since()['receipts'][-1]['source_intent_binding']['source']
    assert actual['raw_policy_action9']==raw and actual['requested_target_action9']==issued
    assert actual['native_constraint_registration']['authorization_id']=='fresh-USER' and not actual['measured_feedback_claimed']


def test_new_generation_during_park_persistence_is_not_adopted_or_stopped(tmp_path,monkeypatch):
    values,body,new_user,park,receipts=fixture(tmp_path,auto_park=False);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    original=wal.write;epoch=mux.epoch;intruders=[]
    def replaced(path,value,**kwargs):
        if (value.get('pending') or {}).get('operation')=='park_barrier_after_original_STOP':
            teleop.generation+=1;activation.id='FOREIGN-USER';activation.valid=True;intruders.append(teleop.generation)
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',replaced)
    with pytest.raises(ValueError,match='owner changed'):owner.park(body(control_epoch=epoch),authenticated_actor=ACTOR)
    assert intruders and teleop.generation==intruders[0] and activation.id=='FOREIGN-USER' and activation.valid
    assert teleop.native_stop==1 and owner.guard.parked is None and owner.guard.armed


def test_revoke_during_successor_wal_never_attaches_stale_authorization(tmp_path,monkeypatch):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    request=new_user();original=wal.write;before=deepcopy(owner.guard.binding);drive=deepcopy(teleop.native_drive)
    def revoked(path,value,**kwargs):
        if (value.get('pending') or {}).get('operation')=='new_USER_parked_successor':activation.revoke('EXPLICIT_USER_REVOKE')
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',revoked)
    with pytest.raises(ValueError,match='owner changed'):owner.adopt_parked(request,authenticated_actor=ACTOR)
    assert owner.guard.binding==before and owner.guard.parked is not None and teleop.native_drive==drive and owner.guard.grant is None


@pytest.mark.parametrize('stage',['begin','resume'])
def test_revoke_during_prepared_control_wal_cannot_start_or_resume_old_permission(tmp_path,monkeypatch,stage):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    owner.adopt_parked(new_user(),authenticated_actor=ACTOR);token=park['park']['park_request_id']
    request=body(park_request_id=token)
    if stage=='resume':
        result=owner.begin_parked(request,authenticated_actor=ACTOR);request=body(park_request_id=token,control_epoch=result['native']['control_epoch'])
    lease=teleop.lease;drive=deepcopy(teleop.native_drive);original=wal.write
    def revoked(path,value,**kwargs):
        if (value.get('pending') or {}).get('operation')=='explicit_USER_parked_'+stage:activation.revoke('EXPLICIT_USER_REVOKE')
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',revoked)
    operation=owner.begin_parked if stage=='begin' else owner.resume_parked
    with pytest.raises(ValueError,match='authorization changed'):operation(request,authenticated_actor=ACTOR)
    assert teleop.lease==lease and teleop.native_drive==drive and owner.guard.parked is not None
    assert not owner.phases.in_transition()


@pytest.mark.parametrize('mode',['cooperative_policy','keyboard'])
def test_final_resume_seal_failure_blocks_all_targets_and_scoped_stops_only_its_owned_native_session(tmp_path,monkeypatch,mode):
    values,body,new_user,park,receipts=fixture(tmp_path,mode=mode);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    owner.adopt_parked(new_user(),authenticated_actor=ACTOR);token=park['park']['park_request_id'];request=body(park_request_id=token)
    if mode=='cooperative_policy':
        result=owner.begin_parked(request,authenticated_actor=ACTOR);request=body(park_request_id=token,control_epoch=result['native']['control_epoch'])
    original=wal.write;before=len(publisher.messages)
    def failed(path,value,**kwargs):
        if value.get('pending') is None and value.get('state',{}).get('phase_history',[])[-1:][0].get('kind')=='EXPLICIT_USER_PHASE_RESUMED':
            raise OSError('fixture: final resume seal failed')
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',failed)
    with pytest.raises(OSError,match='resume seal'):owner.resume_parked(request,authenticated_actor=ACTOR)
    assert owner.guard.journal.recovery_required and owner.guard.journal.pending and owner.guard.armed
    assert not mux.active and teleop.stop_latched and not activation.valid and len(publisher.messages)==before


def test_control_history_is_bounded_explicitly_partial_with_monotonic_chain_not_a_dataset(tmp_path):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    for index in range(40):owner.phases._retain(owner.phases._event('SOFTWARE_CONTROL_HISTORY_WINDOW',test_index=index))
    state=owner.guard.status();assert len(state['phase_history'])==32 and not state['complete_phase_event_bytes_retained']
    assert state['phase_event_count']==41 and state['phase_history'][-1]['source_event_ordinal']==41
    assert state['phase_event_chain_sha256']==state['phase_history'][-1]['source_event_sha256']
    assert owner.guard.used==set() and owner.guard.parked is not None and owner.guard.armed


def test_new_generation_during_begin_wal_does_not_leak_internal_transition_permission(tmp_path,monkeypatch):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    owner.adopt_parked(new_user(),authenticated_actor=ACTOR);request=body(park_request_id=park['park']['park_request_id']);before=deepcopy(teleop.native_drive)
    original=wal.write
    def changed(path,value,**kwargs):
        if (value.get('pending') or {}).get('operation')=='explicit_USER_parked_begin':teleop.generation+=1
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',changed)
    with pytest.raises(ValueError,match='generation changed'):owner.begin_parked(request,authenticated_actor=ACTOR)
    assert not owner.phases.in_transition() and owner.guard.parked is not None and teleop.native_drive==before and not mux.active


def test_foreign_new_authorization_after_resume_cannot_be_stopped_by_late_seal_cleanup(tmp_path,monkeypatch):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    owner.adopt_parked(new_user(),authenticated_actor=ACTOR);token=park['park']['park_request_id']
    begun=owner.begin_parked(body(park_request_id=token),authenticated_actor=ACTOR);epoch=begun['native']['control_epoch']
    request=body(park_request_id=token,control_epoch=epoch);original=wal.write;stops=[]
    def foreign(path,value,**kwargs):
        if value.get('pending') is None and (value.get('state') or {}).get('parked') is None:
            activation.id='FOREIGN-NEW-USER';activation.valid=True;stops.append(teleop.native_stop)
            raise OSError('fixture: final seal with a foreign USER')
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',foreign)
    with pytest.raises(OSError,match='foreign USER'):owner.resume_parked(request,authenticated_actor=ACTOR)
    assert activation.id=='FOREIGN-NEW-USER' and activation.valid and stops==[teleop.native_stop]
    assert owner.guard.journal.recovery_required and owner.guard.armed


def test_expired_explicit_lease_during_final_seal_is_not_returned_as_active_phase(tmp_path,monkeypatch):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    owner.adopt_parked(new_user(),authenticated_actor=ACTOR);token=park['park']['park_request_id']
    begun=owner.begin_parked(body(park_request_id=token),authenticated_actor=ACTOR);request=body(park_request_id=token,control_epoch=begun['native']['control_epoch'])
    original=wal.write
    def elapsed(path,value,**kwargs):
        result=original(path,value,**kwargs)
        if value.get('pending') is None and value.get('state',{}).get('parked') is None:tick[0]+=.5
        return result
    monkeypatch.setattr(wal,'write',elapsed)
    with pytest.raises(ValueError,match='Current original native'):owner.resume_parked(request,authenticated_actor=ACTOR)
    assert not mux.active and teleop.stop_latched and owner.guard.armed and owner.guard.journal.fault is None


def test_fs_writes_are_outside_native_guard_and_original_teleop_locks(tmp_path,monkeypatch):
    values,body,new_user,park,receipts=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    original=wal.write
    def checked(*args,**kwargs):
        assert not owner.guard.lock._is_owned() and not teleop.lock._is_owned()
        return original(*args,**kwargs)
    monkeypatch.setattr(wal,'write',checked);owner.adopt_parked(new_user(),authenticated_actor=ACTOR)
    token=park['park']['park_request_id'];begun=owner.begin_parked(body(park_request_id=token),authenticated_actor=ACTOR)
    owner.resume_parked(body(park_request_id=token,control_epoch=begun['native']['control_epoch']),authenticated_actor=ACTOR)
