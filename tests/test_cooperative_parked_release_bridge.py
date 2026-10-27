"""Real Inbox + authoritative Workflow FSM; explicit SourceControl fixture only."""
from copy import deepcopy
import threading
import time

import pytest

from explorer_shared.cooperative_contract05 import digest
from explorer_shared.handover_adapters import OperatorEvidenceInbox
from test_cooperative_workflow05 import fixture,zone,run_step,DOMAIN


def rig(tmp_path):
    workflow,row,old_inbox,calls,refs=fixture(tmp_path/'workflow')
    zone(workflow,row)
    for event in ('giver_holding','offer_ready','receiver_ready','receiver_at_object','receiver_closed'):
        row=run_step(workflow,row,event);assert row['status']=='READY_FOR_OBSERVED_STEPS'
    row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'USER-fixture'})
    workflow._save(row)
    inbox=OperatorEvidenceInbox(tmp_path/'inbox',clock_domain=DOMAIN);workflow.inbox=inbox
    entered,returned=threading.Event(),threading.Event();state={}
    def prepare(current,after):
        started=time.monotonic_ns();operation=current['pending_operation']['operation_id']
        challenge={'schema':'explorer_native_parked_receiver_hold_challenge_v1','challenge_id':'source-challenge',
            'release_request_id':'release-request','park_request_id':'park-request','run_id':row['session_id'],
            'object_id':row['protocol_object_id'],'actor_id':'USER-fixture','after_receiver_command_id':'actual-native-close',
            'coordinator_epoch':row['epoch'],'native_boot_id':'other-boot',
            'issued_native_ns':10_000_000_000,'expires_native_ns':12_000_000_000,'native_clock_domain':'nano:other-boot'}
        response={'challenge':challenge,'native_command_called':False,'execution_authority':False};received=time.monotonic_ns()
        offered=inbox.prepare_release_bundle(run_id=row['session_id'],object_id=row['protocol_object_id'],
            after_giver_command_id=None,source_response=response,authenticated_actor='USER-fixture',
            request_started_ns=started,response_received_ns=received)
        descriptor={'release_bundle_id':offered['bundle_id'],'source_challenge_sha256':digest(challenge),'challenge':challenge,
            'deadline_nx_ns':offered['deadline_ns'],'received_coordinator_ns':received,'received_clock_domain':DOMAIN,
            'after_receiver_command_id':'actual-native-close','run_id':row['session_id'],'object_id':row['protocol_object_id'],
            'epoch':row['epoch'],'operation_id':operation,'actor_id':'USER-fixture'}
        state.update(descriptor=descriptor);return descriptor
    def wait(current,descriptor):
        entered.set()
        if not returned.wait(1.5):raise TimeoutError('fixture: no explicit USER SourceCONTROL completion')
        return deepcopy(state['completion'])
    workflow.prepare_parked_release=prepare;workflow.wait_parked_release_control=wait
    return workflow,row,inbox,calls,entered,returned,state


def consume(inbox,state):
    descriptor=state['descriptor'];identity=descriptor['release_bundle_id']
    inbox.confirm_release_bundle(identity,{'observing':True,'affirmations':{
        'receiver_holds_object':True,'giver_stationary':True,'receiver_stationary':True}},authenticated_actor='USER-fixture')
    claimed=inbox.wait_release_bundle(identity);proofs=claimed['proofs']
    completion={key:deepcopy(descriptor[key]) for key in ('release_bundle_id','run_id','object_id','epoch','operation_id')}
    completion.update(schema='explorer_cooperative_parked_release_control_completion_v1',source_control_validated=True,
        source_control_receipt={'receipt_id':'e'*32,'sha256':'f'*64,'fixture_only_no_source_command':True},
        operator_confirmations=proofs,release_grant={'grant_id':'actual-source-fixture-grant','challenge':deepcopy(descriptor['challenge']),
            'expires_native_ns':descriptor['challenge']['expires_native_ns'],'operator_confirmation':proofs['receiver_holds_object'],
            'stationary_confirmations':{key:proofs[key] for key in ('giver_stationary','receiver_stationary')}})
    state['completion']=completion;return completion


def start(values):
    workflow,row,inbox,calls,entered,returned,state=values
    request=workflow.request_step(row['session_id'],'release_requested',expected_epoch=row['epoch'])
    assert entered.wait(1);return request


def finish(values,request):
    workflow,row,inbox,calls,entered,returned,state=values;returned.set()
    workflow.threads[request['operation_id']].join(3)
    assert not workflow.threads[request['operation_id']].is_alive()
    return workflow.snapshot(row['session_id'])


def test_three_consumed_facts_alone_do_not_advance_fsm_until_explicit_control_custody_and_reservation_end(tmp_path):
    values=rig(tmp_path);workflow,row,inbox,calls,entered,returned,state=values
    try:
        request=start(values);descriptor=state['descriptor'];identity=descriptor['release_bundle_id']
        pending=workflow.snapshot(row['session_id']);assert pending['protocol_state']=='RECEIVER_GRASP_CHECK'
        assert pending['pending_operation']['native_release_challenge']==descriptor
        assert not workflow.native_phase_change_window(pending)
        with pytest.raises(ValueError):workflow.begin_native_phase_change(row['session_id'],row['epoch'],'giver','native_attach')
        token=workflow.begin_parked_release_control(row['session_id'],row['epoch'],identity)
        with pytest.raises(ValueError):workflow.begin_parked_release_control(row['session_id'],row['epoch'],identity)
        consume(inbox,state)
        # Acknowledging the three observations is not a SourceCONTROL reply.
        assert workflow.snapshot(row['session_id'])['protocol_state']=='RECEIVER_GRASP_CHECK'
        returned.set()
        assert workflow.threads[request['operation_id']].is_alive()  # Reservation has not ended.
        workflow.end_native_phase_change(token)
        current=finish(values,request)
        assert current['status']=='READY_FOR_OBSERVED_STEPS' and current['protocol_state']=='RELEASE'
        assert current['pending_operation'] is None and current['protocol_completed'] is False
        assert any('explicit_parked_release_control' in item for item in current['observation_receipts'])
        assert not current['dispatch_receipts'] and not calls  # No movement or old authorize call.
    finally:returned.set();workflow.close()


@pytest.mark.parametrize('changed',['actor','bundle','epoch','operation','hash','old_fact','wrong_command','unconsumed','missing_fact',
    'grant_challenge','grant_expiry','grant_proofs','unvalidated','missing_custody','duplicate_question','source_epoch','source_clock','source_ttl'])
def test_wrong_or_old_control_completion_never_advances_release_and_scopes_stop(tmp_path,changed):
    values=rig(tmp_path);workflow,row,inbox,calls,entered,returned,state=values
    try:
        request=start(values);completion=consume(inbox,state);proof=completion['operator_confirmations']['receiver_holds_object']
        if changed=='actor':proof['authenticated_actor']='foreign'
        elif changed=='bundle':completion['release_bundle_id']='b'*32
        elif changed=='epoch':completion['epoch']='old-epoch'
        elif changed=='operation':completion['operation_id']='old-operation'
        elif changed=='hash':proof['source_challenge_sha256']='0'*64
        elif changed=='old_fact':proof['created_ns']=state['descriptor']['received_coordinator_ns']-1
        elif changed=='wrong_command':proof['after_command_id']='old-command'
        elif changed=='unconsumed':proof['state']='confirmed'
        elif changed=='missing_fact':completion['operator_confirmations'].pop('giver_stationary')
        elif changed=='grant_challenge':completion['release_grant']['challenge']['park_request_id']='foreign'
        elif changed=='grant_expiry':completion['release_grant']['expires_native_ns']+=1
        elif changed=='grant_proofs':completion['release_grant']['stationary_confirmations']={}
        elif changed=='unvalidated':completion['source_control_validated']=False
        elif changed=='missing_custody':completion['source_control_receipt']=None
        elif changed=='duplicate_question':completion['operator_confirmations']['giver_stationary']['challenge_id']=proof['challenge_id']
        else:
            challenge=state['descriptor']['challenge']
            if changed=='source_epoch':challenge['coordinator_epoch']='another-root-epoch'
            elif changed=='source_clock':challenge['native_clock_domain']=DOMAIN
            else:challenge['expires_native_ns']+=1
            checksum=digest(challenge);state['descriptor']['source_challenge_sha256']=checksum
            # A changed helper-local descriptor cannot replace the original
            # descriptor already retained by the Workflow.
            for fact in completion['operator_confirmations'].values():fact['source_challenge_sha256']=checksum
            completion['release_grant']['challenge']=deepcopy(challenge)
            completion['release_grant']['expires_native_ns']=challenge['expires_native_ns']
        current=finish(values,request)
        assert current['status']=='STOPPED' and current['protocol_state']=='SAFE_STOP'
        assert not any(item['event']=='release_requested' for item in current['protocol_trace'])
        assert {item[1] for item in calls if item[0]=='stop'}=={'giver','receiver'}
    finally:returned.set();workflow.close()


def test_self_consistent_foreign_source_epoch_cannot_be_promoted_by_a_validated_control_flag(tmp_path):
    values=rig(tmp_path);workflow,row,inbox,calls,entered,returned,state=values
    try:
        request=start(values);completion=consume(inbox,state);descriptor=deepcopy(state['descriptor'])
        descriptor['challenge']['coordinator_epoch']='another-root-epoch'
        descriptor['source_challenge_sha256']=digest(descriptor['challenge'])
        for proof in completion['operator_confirmations'].values():proof['source_challenge_sha256']=descriptor['source_challenge_sha256']
        completion['release_grant']['challenge']=deepcopy(descriptor['challenge'])
        with pytest.raises(ValueError,match='Exact source challenge'):
            workflow._parked_release_evidence(row,request['operation_id'],descriptor,completion,'actual-native-close')
        assert workflow.snapshot(row['session_id'])['protocol_state']=='RECEIVER_GRASP_CHECK'
        workflow.cancel_session(row['session_id']);finish(values,request)
    finally:returned.set();workflow.close()


def test_stop_during_reserved_control_does_not_wait_or_accept_late_validated_reply(tmp_path):
    values=rig(tmp_path);workflow,row,inbox,calls,entered,returned,state=values
    try:
        request=start(values);consume(inbox,state)
        token=workflow.begin_parked_release_control(row['session_id'],row['epoch'],state['descriptor']['release_bundle_id'])
        stopped=workflow.cancel_session(row['session_id'])
        assert stopped['session']['epoch']!=row['epoch'] and workflow.threads[request['operation_id']].is_alive()
        assert workflow.end_native_phase_change(token)
        current=finish(values,request)
        assert current['status']=='STOPPED' and current['protocol_state']=='SAFE_STOP'
        assert not any(item['event']=='release_requested' for item in current['protocol_trace'])
    finally:returned.set();workflow.close()


def test_control_reservation_requires_exact_live_bundle_and_original_pending_operation(tmp_path):
    values=rig(tmp_path);workflow,row,inbox,calls,entered,returned,state=values
    try:
        with pytest.raises(ValueError):workflow.begin_parked_release_control(row['session_id'],row['epoch'],'a'*32)
        request=start(values)
        with pytest.raises(ValueError):workflow.begin_parked_release_control(row['session_id'],row['epoch'],'a'*32)
        with pytest.raises(ValueError):workflow.begin_parked_release_control(row['session_id'],'old-epoch',state['descriptor']['release_bundle_id'])
        consume(inbox,state);assert finish(values,request)['protocol_state']=='RELEASE'
    finally:returned.set();workflow.close()
