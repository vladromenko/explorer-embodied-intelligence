"""Original fake giver + real durable operator inbox, no ROS or devices."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time

import pytest

from explorer_shared.giver_parked_release05 import GiverParkedRelease
from explorer_shared.handover_adapters import OperatorEvidenceInbox
from explorer_shared import giver_hold_persistence05 as wal
from explorer_shared.legacy_bridge import POST_PATHS
from test_giver_phase_lifecycle05 import fixture as phase_fixture,ACTOR
from test_native_cooperative05 import message
from test_native_giver_phase_http import client_for,TOKEN

DOMAIN='nx:software-test-boot'


def rig(tmp_path,*,mode='cooperative_policy'):
    values,body,new_user,park,receipts=phase_fixture(tmp_path,mode=mode)
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    owner.adopt_parked(new_user(),authenticated_actor=ACTOR)
    if mode=='cooperative_policy':owner.begin_parked(body(park_request_id=park['park']['park_request_id']),authenticated_actor=ACTOR)
    helper=GiverParkedRelease(owner)
    def base(**extra):
        scope={'control_epoch':owner.guard.binding['native_control_epoch']} if mode=='cooperative_policy' else {}
        return body(park_request_id=park['park']['park_request_id'],**scope,**extra)
    def challenge():
        response=helper.challenge(base(release_request_id='root-release-operation',after_receiver_command_id='actual-receiver-close'),authenticated_actor=ACTOR)
        return response,time.monotonic_ns()
    return values,base,helper,challenge


def actual_confirmations(folder,binding):
    inbox=OperatorEvidenceInbox(folder,clock_domain=DOMAIN)
    statements={'receiver_holds_object':'receiver','giver_stationary':'giver','receiver_stationary':'receiver'}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={statement:pool.submit(inbox._wait,statement,binding['run_id'],binding['object_id'],role,
            'actual-receiver-close' if role=='receiver' else None,3.) for statement,role in statements.items()}
        until=time.monotonic()+2
        while len(inbox.pending())<3 and time.monotonic()<until:time.sleep(.005)
        pending=inbox.pending();assert len(pending)==3
        for row in pending:inbox.confirm(row['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor=ACTOR)
        return {statement:future.result(timeout=2) for statement,future in futures.items()}


def request(base,reply,received,proofs):
    return base(release_request_id='root-release-operation',challenge_id=reply['challenge']['challenge_id'],
        operator_confirmations=proofs,coordinator_clock_domain=DOMAIN,challenge_received_coordinator_ns=received,
        proofs_checked_coordinator_ns=time.monotonic_ns(),coordinator_verified_confirmation_created_after_challenge=True)


@pytest.mark.parametrize('mode',['cooperative_policy','keyboard','browser_gamepad'])
def test_parked_challenge_real_new_confirmations_explicit_resume_keeps_original_expiry_and_publication_gate(tmp_path,mode):
    values,base,helper,prepare=rig(tmp_path,mode=mode);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    before=len(publisher.messages);drive=deepcopy(teleop.native_drive);lease=teleop.lease
    reply,received=prepare();original=deepcopy(reply['challenge']);proofs=actual_confirmations(tmp_path/'inbox',owner.guard.binding)
    assert not reply['native_command_called'] and not reply['execution_authority']
    assert len(publisher.messages)==before and teleop.native_drive==drive and teleop.lease==lease and owner.guard.parked
    tick[0]+=.1
    resumed=helper.resume_release(request(base,reply,received,proofs),authenticated_actor=ACTOR)
    grant=resumed['release_grant'];assert owner.guard.parked is None and owner.guard.armed
    assert grant['challenge']==original and grant['expires_native_ns']==original['expires_native_ns']
    assert grant['revision']==owner.guard.revision and teleop.lease==tick[0]+.45
    assert len(publisher.messages)==before and not resumed['actual_arm_opening_command_sent']
    assert grant['source_resume_lineage']['same_original_native_epoch']==original['source_control_epoch']
    arm.pub.publish(message(80));assert len(publisher.messages)==before+1
    publication=owner.guard.last_publication;assert publication['opening'] and publication['guard_grant']==grant
    assert publication['guard_grant']['operator_confirmation']==proofs['receiver_holds_object']
    assert all(row['challenge_id'] in owner.guard.used for row in proofs.values())
    assert grant['challenge']['source_context']['measured_feedback_mask']==[False]*6


@pytest.mark.parametrize('changed',['old_confirmation','actor','receiver_command','run','object','unconsumed','missing_stationary',
    'future','source_domain','expired_native','revision','park','auth','origin','reference_fault','reused','numeric'])
def test_changed_or_old_proof_never_resumes_or_unparks(tmp_path,changed):
    values,base,helper,prepare=rig(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    reply,received=prepare();proofs=actual_confirmations(tmp_path/'inbox',owner.guard.binding);body=request(base,reply,received,proofs)
    proof=body['operator_confirmations']['receiver_holds_object'];before=len(publisher.messages);drive=deepcopy(teleop.native_drive);lease=teleop.lease
    if changed=='old_confirmation':proof['created_ns']=received-1
    elif changed=='actor':proof['authenticated_actor']='another-user'
    elif changed=='receiver_command':proof['after_command_id']='another-command'
    elif changed=='run':proof['run_id']='another-run'
    elif changed=='object':proof['object_id']='another-object'
    elif changed=='unconsumed':proof['state']='confirmed'
    elif changed=='missing_stationary':body['operator_confirmations'].pop('giver_stationary')
    elif changed=='future':proof['consumed_ns']=body['proofs_checked_coordinator_ns']+1
    elif changed=='source_domain':body['coordinator_clock_domain']=tap.clock_domain
    elif changed=='expired_native':tick[0]+=2.001
    elif changed=='revision':body['expected_revision']-=1
    elif changed=='park':body['park_request_id']='foreign-park'
    elif changed=='auth':body['authorization_id']='foreign-auth'
    elif changed=='origin':tap.base_history[-1]['velocity']=[.1,0.,0.]
    elif changed=='reference_fault':owner.guard.reference_fault={'reason':'unknown source reference'}
    elif changed=='reused':owner.guard.used.add(proof['challenge_id'])
    else:body['servo_deg']=[90]*6
    with pytest.raises(ValueError):helper.resume_release(body,authenticated_actor=ACTOR)
    assert owner.guard.parked and owner.guard.grant is None and len(publisher.messages)==before
    assert teleop.native_drive==drive and teleop.lease==lease


def test_confirmations_actually_created_before_the_source_challenge_are_not_retimed(tmp_path):
    values,base,helper,prepare=rig(tmp_path);owner=values[0];teleop=values[2]
    proofs=actual_confirmations(tmp_path/'inbox',owner.guard.binding);reply,received=prepare();lease=teleop.lease
    with pytest.raises(ValueError,match='post-challenge'):
        helper.resume_release(request(base,reply,received,proofs),authenticated_actor=ACTOR)
    assert owner.guard.parked and not owner.guard.grant and teleop.lease==lease


def test_ordinary_resume_discards_parked_challenge_and_never_installs_opening_authority(tmp_path):
    values,base,helper,prepare=rig(tmp_path);owner=values[0];arm=values[3];publisher=values[4]
    reply,received=prepare();owner.resume_parked(base(),authenticated_actor=ACTOR)
    assert owner.guard.pending is None and owner.guard.grant is None
    before=len(publisher.messages)
    with pytest.raises(ValueError,match='NO_FRESH_NATIVE'):arm.pub.publish(message(80))
    assert len(publisher.messages)==before


def test_stop_during_prepared_resume_wal_prevents_original_resume_and_old_nonce_revival(tmp_path,monkeypatch):
    values,base,helper,prepare=rig(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    reply,received=prepare();proofs=actual_confirmations(tmp_path/'inbox',owner.guard.binding);body=request(base,reply,received,proofs)
    entered,release=threading.Event(),threading.Event();errors=[];write=wal.write;count=[0];lease=teleop.lease
    def blocked(*args,**kwargs):
        count[0]+=1
        if count[0]==1:entered.set();assert release.wait(3)
        return write(*args,**kwargs)
    monkeypatch.setattr(wal,'write',blocked)
    def invoke():
        try:helper.resume_release(body,authenticated_actor=ACTOR)
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=invoke)
    try:
        thread.start();assert entered.wait(2);owner.guard.revoke('EXPLICIT_TEST_STOP');activation.revoke('EXPLICIT_TEST_STOP')
        release.set();thread.join(2);assert not thread.is_alive() and errors
        assert teleop.lease==lease and owner.guard.grant is None and owner.guard.parked
    finally:release.set();thread.join(3)


def test_logical_revoke_inside_original_resume_cannot_install_a_new_revision_grant(tmp_path,monkeypatch):
    values,base,helper,prepare=rig(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    reply,received=prepare();proofs=actual_confirmations(tmp_path/'inbox',owner.guard.binding);resume=mux.resume;calls=[]
    def revoked(*args,**kwargs):
        result=resume(*args,**kwargs);calls.append(result);owner.guard.revoke('EXPLICIT_TEST_LOGICAL_REVOKE');return result
    monkeypatch.setattr(mux,'resume',revoked);before=len(publisher.messages)
    with pytest.raises(ValueError,match='context/revoke/expiry'):
        helper.resume_release(request(base,reply,received,proofs),authenticated_actor=ACTOR)
    assert calls and not mux.active and teleop.stop_latched and owner.guard.grant is None
    assert owner.guard.journal.recovery_required and len(publisher.messages)==before


@pytest.mark.parametrize('failure',['final_write','native_expiry','lease_expiry','foreign_new_owner'])
def test_failed_or_expired_final_persistence_never_returns_current_opening_grant_or_stops_foreign_owner(tmp_path,monkeypatch,failure):
    values,base,helper,prepare=rig(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    reply,received=prepare();proofs=actual_confirmations(tmp_path/'inbox',owner.guard.binding);write=wal.write;count=[0];before=len(publisher.messages)
    def changed(*args,**kwargs):
        count[0]+=1
        if count[0]==2:
            if failure=='final_write':raise OSError('EXPLICIT_TEST_FINAL_FSYNC_FAILED')
            if failure=='native_expiry':tick[0]+=2.001
            elif failure=='lease_expiry':tick[0]+=.451
            elif failure=='foreign_new_owner':
                activation.id='foreign-USER';mux.epoch='foreign-native-epoch';teleop.generation+=1;mux.generation=teleop.generation
        return write(*args,**kwargs)
    monkeypatch.setattr(wal,'write',changed)
    with pytest.raises((ValueError,OSError)):
        helper.resume_release(request(base,reply,received,proofs),authenticated_actor=ACTOR)
    assert len(publisher.messages)==before and owner.guard.armed
    if failure=='foreign_new_owner':assert activation.id=='foreign-USER' and mux.epoch=='foreign-native-epoch' and mux.active
    else:assert not mux.active and teleop.stop_latched and owner.guard.grant is None


def test_parked_challenge_and_atomic_resume_have_no_wal_fsync_inside_control_locks(tmp_path,monkeypatch):
    values,base,helper,prepare=rig(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    write=wal.write;calls=[]
    def checked(*args,**kwargs):
        assert not owner.guard.lock._is_owned() and not teleop.lock._is_owned()
        calls.append(True);return write(*args,**kwargs)
    monkeypatch.setattr(wal,'write',checked);reply,received=prepare();proofs=actual_confirmations(tmp_path/'inbox',owner.guard.binding)
    helper.resume_release(request(base,reply,received,proofs),authenticated_actor=ACTOR)
    assert len(calls)==4


def test_fixed_source_http_exposes_metadata_challenge_then_separate_control_without_browser_actor_or_targets(tmp_path):
    values,base,helper,prepare=rig(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    assert not owner.status()['capabilities']['explicit_user_parked_release_http']
    challenge_path='/api/explorer/cooperative/phase/release/challenge';resume_path='/api/explorer/cooperative/phase/resume-release'
    assert all(path in POST_PATHS for path in ('explorer/cooperative/phase/release/challenge','explorer/cooperative/phase/resume-release'))
    with client_for(owner,mux,activation) as client:
        assert client.get('/api/explorer/cooperative/status',headers={'Authorization':'Bearer '+TOKEN}).json()['capabilities']['explicit_user_parked_release_http']
        headers={'Authorization':'Bearer '+TOKEN};payload=base(release_request_id='root-release-operation',after_receiver_command_id='actual-receiver-close')
        assert client.post(challenge_path,json=payload).status_code==401
        assert client.post(resume_path,json={}).status_code==401
        before=(len(publisher.messages),deepcopy(teleop.native_drive),teleop.lease)
        for extra in ({'actor_id':'forged-actor'},{'servo_deg':[90]*6},{'duration':20}):
            assert client.post(challenge_path,json={**payload,**extra},headers=headers).status_code==409
        assert client.post(challenge_path+'?unknown=1',json=payload,headers=headers).status_code==400
        raw=client.post(challenge_path,json=payload,headers=headers);assert raw.status_code==200,raw.text
        received=time.monotonic_ns();reply=raw.json()
        assert not reply['native_command_called'] and before==(len(publisher.messages),teleop.native_drive,teleop.lease)
        proofs=actual_confirmations(tmp_path/'inbox',owner.guard.binding);body=request(base,reply,received,proofs)
        assert client.post(resume_path,json={**body,'actor_id':'forged'},headers=headers).status_code==409
        returned=client.post(resume_path,json=body,headers=headers);assert returned.status_code==200,returned.text
        document=returned.json();assert document['native_command_called'] and not document['actual_arm_opening_command_sent']
        assert document['release_grant']['challenge']==reply['challenge'] and len(publisher.messages)==before[0]
        assert teleop.lease==tick[0]+.45
