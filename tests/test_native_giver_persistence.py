"""Existing ORIGINAL Publisher proxy with temp metadata only; no ROS/devices."""
from copy import deepcopy
import json
import threading
import time

import pytest

from explorer_shared import giver_hold_persistence05 as wal
from explorer_shared import native_cooperative05 as cooperative
from explorer_shared.native_cooperative05 import NativeCooperativeOwner,ExistingBaseDriveBoundary
from explorer_shared.native_stream03 import NativeDispatchTap,NativeTeleopMux
from test_native_cooperative05 import OriginalArm,Activation,start,confirmed,message
from test_native_stream03 import Teleop


def fixture(tmp_path,*,bind=True,boot='boot'):
    tick=[10.];arm=OriginalArm();publisher=arm.pub;teleop=Teleop(arm)
    tap=NativeDispatchTap(boot_id=boot,clock_domain='nano:'+boot,clock=lambda:int(tick[0]*1e9))
    mux=NativeTeleopMux(teleop,arm,tap,clock=lambda:tick[0]);activation=Activation(tap.clock)
    mux.observation_permit=activation.allowed;mux.install();path=tmp_path/'giver-hold.json'
    owner=NativeCooperativeOwner(mux,activation,lambda:{'servo_deg':[90]*6,'boot_id':boot,'measured':False},target_limits=[(0,180)]*6,state_path=path)
    mux.chassis_permit=lambda:not owner.guard.base_motion_blocked()
    teleop.drive=ExistingBaseDriveBoundary(teleop.drive,owner.guard)
    mux.publication_evidence=lambda:deepcopy(owner.guard.last_publication)
    binding={'run_id':'run-1','object_id':'block-1','coordinator_epoch':'coord-1','authorization_id':'auth','observing':True}
    if bind:owner.guard.bind(binding)
    return owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path


def armed(tmp_path):
    values=fixture(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    start(owner,mux,binding);owner.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})
    return values


def reset_body(owner,binding):
    state=owner.guard.status()
    return {**binding,'object_safe_no_dual_hold':True,'expected_state_id':state['persistence']['state_id'],'expected_revision':state['revision']}


def test_constructor_status_readonly_and_armed_barrier_sealed_before_any_handover_publication(tmp_path):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=fixture(tmp_path,bind=False)
    assert not path.exists() and not publisher.messages
    status=owner.guard.status()['persistence'];assert status['installed'] and status['healthy'] and not status['initialized']
    owner.guard.bind(binding);start(owner,mux,binding)
    status=owner.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})['persistence']
    saved=json.loads(path.read_bytes())
    assert status['healthy'] and status['initialized'] and not status['pending'] and not status['recovery_required']
    assert saved['state']['armed'] and saved['pending'] is None and saved['state']['latest_pose_not_qualified_for_automatic_restart']
    assert arm.pub.original is publisher and len(publisher.messages)==1


def test_fresh_owner_restores_latch_but_never_permissions_or_latest_pose_and_blocks_closing_legacy_new_grant(tmp_path):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    challenge=owner.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'})
    owner.guard.authorize(confirmed(binding,challenge));arm.pub.publish(message(80))
    assert owner.guard.last_pose[5]==80 and json.loads(path.read_bytes())['state']['last_pose'][5]==90
    restored=fixture(tmp_path,bind=False);new,newmux,newteleop,newarm,newpub,_,newactivation,_,_,_=restored
    state=new.guard.status();assert state['armed'] and state['persistence']['restored_armed'] and state['persistence']['recovery_required']
    assert state['grant'] is None and state['pending_challenge'] is None and new.guard.used=={'new-operator-receipt'}
    newactivation.id='fresh-user-authorization';newactivation.valid=True
    for pose in (message(100),message(80),message(90)):
        with pytest.raises(ValueError,match='DURABLE_GIVER'):newarm.pub.publish(pose)
        newactivation.valid=True
    assert not newpub.messages and new.guard.armed and new.guard.reference_fault is not None
    with pytest.raises(ValueError,match='safe-object recovery'):new.prepare({**binding,'authorization_id':newactivation.id,'neutral':True})


@pytest.mark.parametrize('failure',['corrupt','array','null_state','pending','missing','missing_marker'])
def test_unknown_pending_corrupt_or_missing_initialized_wal_blocks_all_publisher_targets(tmp_path,failure):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    if failure=='corrupt':path.write_bytes(b'{invalid')
    elif failure=='array':path.write_bytes(b'[]')
    elif failure=='missing':path.unlink()
    elif failure=='missing_marker':owner.guard.journal.marker.unlink()
    else:
        saved=json.loads(path.read_bytes())
        if failure=='null_state':saved['state']=None
        else:saved['pending']={'transaction_id':'SOURCE-CRASH'}
        saved['sha256']=wal.digest({key:value for key,value in saved.items() if key!='sha256'});path.write_bytes(wal.canonical(saved))
    restored,*_=fixture(tmp_path,bind=False);state=restored.guard.status()
    assert state['persistence']['recovery_required'] and not state['persistence']['healthy']
    with pytest.raises(ValueError,match='DURABLE_GIVER'):restored.mux.arm.pub.publish(message(100))
    assert restored.original_publisher.messages==[] and restored.guard.armed


def test_only_explicit_current_user_stop_safe_object_cas_reset_unarms_and_reference_fault_survives(tmp_path):
    armed(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=fixture(tmp_path,bind=False)
    before=path.read_bytes();body=reset_body(owner,binding);fault=deepcopy(owner.guard.reference_fault)
    for bad in (dict(body,object_safe_no_dual_hold=False),dict(body,expected_state_id='old-state'),dict(body,expected_revision=-1)):
        with pytest.raises(ValueError):owner.guard.reset_safe(bad)
        assert owner.guard.armed and path.read_bytes()==before
    result=owner.guard.reset_safe(body)
    assert not result['armed'] and result['persistence']['healthy'] and not result['persistence']['recovery_required']
    assert result['reference_fault']==fault and not publisher.messages and not teleop.native_drive
    restored,*_=fixture(tmp_path,bind=False)
    assert not restored.guard.armed and restored.guard.reference_fault==fault and restored.guard.grant is None


def test_persistence_not_touched_from_publication_or_revoke_and_closing_needs_current_observed_owner(tmp_path,monkeypatch):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    challenge=owner.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'});owner.guard.authorize(confirmed(binding,challenge))
    def prohibited(*args,**kwargs):raise AssertionError('No WAL file IO in original publisher or immediate revoke')
    monkeypatch.setattr(wal,'write',prohibited);monkeypatch.setattr(wal,'read',prohibited)
    arm.pub.publish(message(80));assert len(publisher.messages)==2
    owner.guard.revoke('EXPLICIT_USER_STOP');activation.valid=False
    with pytest.raises(ValueError,match='Current original native'):arm.pub.publish(message(100))
    assert len(publisher.messages)==2 and owner.guard.armed


def test_final_arm_seal_failure_and_first_wal_failure_leave_restart_closed(tmp_path,monkeypatch):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=fixture(tmp_path)
    start(owner,mux,binding);original=wal.write
    def failed(path,value,**kwargs):
        if value.get('schema')==wal.SCHEMA and value.get('pending') is None and (value.get('state') or {}).get('armed'):
            raise OSError('fixture: final armed seal failed')
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',failed)
    with pytest.raises(OSError,match='final armed'):owner.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})
    assert owner.guard.journal.pending and owner.guard.journal.recovery_required
    assert json.loads(path.read_bytes())['pending'] is not None
    restored,*_=fixture(tmp_path,bind=False);assert restored.guard.journal.recovery_required
    monkeypatch.setattr(wal,'write',original)
    fresh_dir=tmp_path/'fresh';fresh_dir.mkdir();fresh,*_=fixture(fresh_dir,bind=False)
    def first_failed(path,value,**kwargs):
        if value.get('schema')==wal.SCHEMA:raise OSError('fixture: first WAL failed')
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',first_failed)
    with pytest.raises(OSError,match='first WAL'):fresh.guard.bind(binding)
    assert fresh.guard.journal.marker.exists() and not fresh.guard.journal.path.exists()
    recovered,*_=fixture(fresh_dir,bind=False);assert recovered.guard.journal.recovery_required


def test_revoke_during_pending_arm_wal_is_immediate_and_late_metadata_cannot_restore_permissions(tmp_path,monkeypatch):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=fixture(tmp_path)
    start(owner,mux,binding);entered,release=threading.Event(),threading.Event();errors=[];original=wal.write
    def delayed(path,value,**kwargs):
        if value.get('schema')==wal.SCHEMA and value.get('pending') is not None:entered.set();assert release.wait(2)
        return original(path,value,**kwargs)
    monkeypatch.setattr(wal,'write',delayed)
    def arm_guard():
        try:owner.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=arm_guard);thread.start();assert entered.wait(1)
    revoked=owner.guard.revoke('EXPLICIT_SOURCE_STOP');assert revoked['revoked'] and not owner.guard.grant
    release.set();thread.join(2)
    assert errors and not thread.is_alive() and owner.guard.journal.recovery_required
    assert len(publisher.messages)==1


def test_fsync_is_outside_original_guard_teleop_and_publication_locks(tmp_path,monkeypatch):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=fixture(tmp_path,bind=False);original=wal.write
    def observed(*args,**kwargs):
        assert not owner.guard.lock._is_owned() and not teleop.lock._is_owned()
        return original(*args,**kwargs)
    monkeypatch.setattr(wal,'write',observed)
    owner.guard.bind(binding);start(owner,mux,binding)
    owner.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})


def test_previous_boot_reference_fault_is_retained_without_cross_boot_timestamp_comparison(tmp_path):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    tick[0]=1_000_000.;activation.valid=False
    with pytest.raises(ValueError):arm.pub.publish(message(100))
    old_fault=deepcopy(owner.guard.reference_fault);assert old_fault['invalidated_native_ns']==int(tick[0]*1e9)
    teleop.stop_latched=True;activation.valid=True
    owner.guard.reset_safe(reset_body(owner,binding))
    restored,*_=fixture(tmp_path,bind=False,boot='genuine-new-boot')
    fault=restored.guard.reference_fault
    assert fault is not None and fault['invalidated_native_ns']==10_000_000_000
    assert fault['previous_native_boot_id']=='boot' and fault['native_boot_id']=='genuine-new-boot'
    assert fault['previous_fault_sha256']==wal.digest(old_fault) and not fault['previous_boot_timestamps_compared_to_current_boot']
    assert not restored.guard.armed and restored.original_publisher.messages==[]


def test_one_admitted_original_opening_return_survives_concurrent_revoke_but_cannot_recover_authority(tmp_path):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    challenge=owner.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'})
    grant=owner.guard.authorize(confirmed(binding,challenge));entered,release=threading.Event(),threading.Event();original=publisher.publish
    def pending(message):entered.set();assert release.wait(2);return original(message)
    publisher.publish=pending;published=threading.Thread(target=lambda:arm.pub.publish(message(80)))
    published.start();assert entered.wait(1);revision=owner.guard.revision
    revoked=threading.Thread(target=lambda:owner.guard.revoke('EXPLICIT_USER_STOP'));revoked.start();deadline=time.monotonic()+1
    while owner.guard.revision==revision and time.monotonic()<deadline:time.sleep(.001)
    assert owner.guard.revision>revision
    release.set();published.join(2);revoked.join(2)
    assert not published.is_alive() and not revoked.is_alive() and len(publisher.messages)==2
    receipt=owner.guard.last_publication
    assert receipt['opening'] and receipt['guard_grant']['grant_id']==grant['grant_id'] and receipt['bounded_endpoint_may_finish_after_stop']
    assert owner.guard.armed and owner.guard.grant is None
    restored,*_=fixture(tmp_path,bind=False)
    assert restored.guard.journal.recovery_required and restored.guard.grant is None and restored.original_publisher.messages==[]


def test_existing_keyboard_and_direct_base_drive_cannot_bypass_latch_but_real_zero_stop_still_delegates(tmp_path):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    before=deepcopy(teleop.native_drive);lease=teleop.lease
    for values in ([.1,0.,0.],[0.,float('nan'),0.],None,[0.,0.],[True,0.,0.]):
        with pytest.raises(PermissionError):teleop.drive(values)
    assert teleop.native_drive==before and teleop.lease==lease
    original_update=teleop.update
    def old_keyboard_update(source,inputs,observing=True,precision=False):
        return teleop.drive([.1,0.,0.])  # Actual old executor's direct-drive path, no new backend.
    teleop.update=old_keyboard_update
    with pytest.raises(PermissionError):teleop.update('keyboard',{'KeyW':1},True)
    teleop.update=original_update
    teleop.drive([0.,0.,0.])
    assert teleop.native_drive==before+[[0.,0.,0.]] and tap.base_history[-1]['velocity']==[0.,0.,0.] and teleop.lease==lease
    restored,*_=fixture(tmp_path,bind=False);restored.activation.id='NEW-OBSERVED-AUTHORIZATION'
    with pytest.raises(PermissionError):restored.mux.teleop.drive([.1,0.,0.])
    assert not restored.mux.chassis_permit()
    restored.mux.teleop.drive([0.,0.,0.]);assert restored.mux.teleop.native_drive==[[0.,0.,0.]]


def test_base_gate_keeps_unknown_binding_none_closed_and_unbound_healthy_old_drive_unchanged(tmp_path):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=fixture(tmp_path,bind=False)
    original=teleop.drive.original;assert getattr(original,'__self__',None) is mux
    teleop.drive([.1,0.,0.]);assert teleop.native_drive==[[.1,0.,0.]]
    owner.guard.journal.fail('SOURCE-WAL-UNKNOWN');assert owner.guard.binding is None
    with pytest.raises(PermissionError):teleop.drive([.1,0.,0.])
    assert teleop.native_drive==[[.1,0.,0.]]
    teleop.drive([0.,0.,0.]);assert tap.base_history[-1]['velocity']==[0.,0.,0.]


@pytest.mark.parametrize('changed',['expired_lease','new_authorization'])
def test_live_armed_legacy_closing_and_base_cannot_use_expired_or_other_user_authority(tmp_path,changed):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    if changed=='expired_lease':tick[0]=teleop.lease+.001
    else:activation.id='OTHER-OBSERVED-AUTHORIZATION'
    before=len(publisher.messages);lease=teleop.lease
    with pytest.raises(ValueError,match='Current original native'):arm.pub.publish(message(100))
    with pytest.raises(PermissionError):teleop.drive([.1,0.,0.])
    assert len(publisher.messages)==before and teleop.lease==lease and owner.guard.armed


def test_revoke_during_confirmation_validation_cannot_stamp_old_challenge_with_new_revision(tmp_path,monkeypatch):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    challenge=owner.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'})
    original=cooperative.identity;threads=[]
    def validating(value):
        if value=='new-operator-receipt':
            thread=threading.Thread(target=lambda:owner.guard.revoke('EXPLICIT_USER_REVOKE_DURING_CONFIRMATION'))
            threads.append(thread);thread.start();deadline=time.monotonic()+1
            while owner.guard.revision==challenge['revision'] and time.monotonic()<deadline:time.sleep(.001)
            assert owner.guard.revision>challenge['revision']
        return original(value)
    monkeypatch.setattr(cooperative,'identity',validating)
    returned=owner.guard.authorize(confirmed(binding,challenge))
    for thread in threads:thread.join(2);assert not thread.is_alive()
    assert returned['revision']==challenge['revision']<owner.guard.revision and owner.guard.grant is None
    assert not owner.guard.journal.pending and owner.guard.journal.fault is None
    with pytest.raises(ValueError,match='NO_FRESH'):arm.pub.publish(message(80))
    assert len(publisher.messages)==1


def test_genuine_zero_stop_does_not_wait_for_an_inflight_arm_publisher_guard(tmp_path):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=armed(tmp_path)
    challenge=owner.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'})
    owner.guard.authorize(confirmed(binding,challenge));entered,release,stopped=threading.Event(),threading.Event(),threading.Event()
    original=publisher.publish
    def blocked(message):entered.set();assert release.wait(2);return original(message)
    publisher.publish=blocked;worker=threading.Thread(target=lambda:arm.pub.publish(message(80)))
    def stop():teleop.drive([0.,0.,0.]);stopped.set()
    worker.start();assert entered.wait(1);zero=threading.Thread(target=stop);zero.start()
    try:
        assert stopped.wait(1) and not release.is_set()
        assert teleop.native_drive[-1]==[0.,0.,0.] and tap.base_history[-1]['velocity']==[0.,0.,0.]
    finally:release.set();worker.join(2);zero.join(2)
    assert not worker.is_alive() and not zero.is_alive() and owner.guard.last_publication['opening']
