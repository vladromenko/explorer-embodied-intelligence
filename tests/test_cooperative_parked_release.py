"""Actual Root custody/Inbox → authenticated native source, fake original buses."""
from copy import deepcopy
import json
import threading
import time
import uuid

import pytest

from explorer_shared.contracts import digest
from test_cooperative_observed_scopes import Sources,ACTOR
from test_cooperative_giver_phases import real_node


def prepared(tmp_path,monkeypatch,*,policy=False):
    sources=Sources(tmp_path,monkeypatch,policy=policy);service=sources.service
    service._arm_guard(sources.row)
    with service.workflow.lock:
        row=service.workflow.snapshot(sources.row['session_id'])
        row['protocol_state']='RECEIVER_GRASP_CHECK'
        row['pending_operation']={'operation_id':uuid.uuid4().hex,'event':'release_requested'}
        service.workflow._save(row)
    descriptor=service.parked_release.prepare(row,None)
    with service.workflow.lock:
        row=service.workflow.snapshot(row['session_id']);row['pending_operation']['native_release_challenge']=deepcopy(descriptor)
        service.workflow._save(row)
        service.workflow.native_phase_checkpoints[(row['session_id'],row['epoch'],row['pending_operation']['operation_id'])]={
            'checkpoint_id':uuid.uuid4().hex,'accepting':False,'binding_change_blocker':'SOURCE_CHALLENGE_IS_EPOCH_BOUND'}
    sources.row=service.workflow.snapshot(row['session_id'])
    return sources,descriptor


def body(row,descriptor):
    return {'session_id':row['session_id'],'expected_epoch':row['epoch'],'release_bundle_id':descriptor['release_bundle_id'],
        'affirmations':{'receiver_holds_object':True,'giver_stationary':True,'receiver_stationary':True},
        'observing':True,'neutral':True,'user_observed_test_confirmed':True}


@pytest.mark.parametrize('policy',[False,True])
def test_actual_control_installs_source_grant_records_custody_and_never_opens_or_renews(tmp_path,monkeypatch,policy):
    sources,descriptor=prepared(tmp_path,monkeypatch,policy=policy);service=sources.service
    initial=deepcopy(sources.row['observed_test_binding']);before=len(sources.pair.owner.original_publisher.messages);calls=len(sources.pair.calls)
    try:
        result=service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)['result']
        assert result['completion']['source_control_validated']
        assert not result['completion']['native_arm_opening_command_sent']
        assert len(sources.pair.owner.original_publisher.messages)==before
        grant=sources.pair.owner.guard.grant;assert grant==result['completion']['release_grant']
        assert grant['challenge']==descriptor['challenge'] and grant['expires_native_ns']==descriptor['challenge']['expires_native_ns']
        assert grant['operator_confirmation']['state']=='consumed'
        assert service.workflow.snapshot(sources.row['session_id'])['observed_test_binding']==initial
        posts=[path for _,method,path,_ in sources.pair.calls[calls:] if method=='POST']
        assert posts==['/legacy/explorer/cooperative/phase/resume-release']
        assert (sources.row['session_id'],'giver') in service.transports
        completion=service.parked_release.wait(sources.row,descriptor)
        assert completion==result['completion'] and not service.workflow.native_phase_changes
        with pytest.raises(ValueError):service.parked_release.wait(sources.row,descriptor)
        with pytest.raises(ValueError):service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        pointer=result['completion']['source_control_receipt'];path,_=service.artifact('native-control',pointer['receipt_id'])
        document=json.loads(path.read_bytes());assert document['source_response']['release_grant']==grant
    finally:sources.close()


def test_browser_fields_actor_false_flags_and_phase_drift_fail_before_any_new_rpc(tmp_path,monkeypatch):
    sources,descriptor=prepared(tmp_path,monkeypatch);service=sources.service;valid=body(sources.row,descriptor)
    try:
        before=(len(sources.calls),len(sources.pair.calls))
        for key,value in [('authorization_id','foreign'),('control_epoch','foreign'),('phase_id','RELEASE'),('actor_id',ACTOR),('target',[90]*6)]:
            with pytest.raises(ValueError):service.command('native_parked_release_resume',{**valid,key:value},authenticated_actor=ACTOR)
        for key in ('neutral','observing','user_observed_test_confirmed'):
            with pytest.raises(ValueError):service.command('native_parked_release_resume',{**valid,key:False},authenticated_actor=ACTOR)
        for identity in (None,[],{},True,'foreign','0'*32):
            with pytest.raises(ValueError):service.command('native_parked_release_resume',{**valid,'release_bundle_id':identity},authenticated_actor=ACTOR)
        with pytest.raises(ValueError):service.command('native_parked_release_resume',valid,authenticated_actor='foreign-actor')
        with service.workflow.lock:
            row=service.workflow.snapshot(sources.row['session_id']);row['protocol_state']='RELEASE';service.workflow._save(row)
        with pytest.raises(ValueError):service.command('native_parked_release_resume',valid,authenticated_actor=ACTOR)
        assert before==(len(sources.calls),len(sources.pair.calls)) and not service.transports
    finally:sources.close()


@pytest.mark.parametrize('changed',['deadline','restart','source_fault','source_new_authorization'])
def test_original_deadline_process_and_actual_source_context_cannot_be_replaced(tmp_path,monkeypatch,changed):
    sources,descriptor=prepared(tmp_path,monkeypatch);service=sources.service
    try:
        if changed=='deadline':service.parked_release.clock=lambda:descriptor['deadline_nx_ns']+1
        elif changed=='restart':
            from explorer.cooperative_parked_release import CooperativeParkedRelease
            service.parked_release=CooperativeParkedRelease(service)
        elif changed=='source_fault':sources.pair.owner.guard.reference_fault='UNKNOWN_ORIGINAL_REFERENCE'
        else:sources.pair.gate.revoke('EXPLICIT_NEW_USER_CONTEXT')
        before=len(sources.pair.owner.original_publisher.messages);calls=len(sources.pair.calls)
        with pytest.raises(ValueError):service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        assert not any(method=='POST' for _,method,_,_ in sources.pair.calls[calls:])
        assert not service.transports and len(sources.pair.owner.original_publisher.messages)==before
    finally:sources.close()


@pytest.mark.parametrize('changed',['lineage','grant_actor','base','base_bool','feedback','unknown_fault','unknown_park'])
def test_actual_control_reply_tampering_retains_original_custody_but_cannot_attach_or_complete(tmp_path,monkeypatch,changed):
    sources,descriptor=prepared(tmp_path,monkeypatch);service=sources.service;request=service._request
    def routed(role):
        callback=request(role)
        def invoke(method,path,payload):
            result=callback(method,path,payload)
            if role=='giver' and path.endswith('/phase/resume-release'):
                if changed=='lineage':result['release_grant']['source_resume_lineage']['unchanged_arm_receipt_sha256']='f'*64
                elif changed=='grant_actor':result['release_grant']['operator_confirmation']['authenticated_actor']='foreign'
                elif changed=='base':result['release_grant']['source_resume_lineage']['after_resume_original_base_receipt']['velocity']=[1.,0.,0.]
                elif changed=='base_bool':result['release_grant']['source_resume_lineage']['after_resume_original_base_receipt']['velocity']=[False]*3
                elif changed=='unknown_fault':result['interlock'].pop('reference_fault')
                elif changed=='unknown_park':result['interlock'].pop('parked')
                else:result['interlock']['binding']['observed_source_authorization']['actor_id']='foreign'
                result['interlock']['grant']=deepcopy(result['release_grant'])
            return result
        return invoke
    service._request=routed
    try:
        with pytest.raises(ValueError):service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        assert not service.transports and sources.pair.owner.guard.armed and sources.wrapper.helper.armed
        assert service.parked_release.records[descriptor['release_bundle_id']]['state']=='FAILED'
        originals=[json.loads(path.read_bytes()) for path in (service.root/'native-control').glob('*/receipt.json')]
        assert any(item['operation']=='native_parked_release_resume' for item in originals)
        assert sources.pair.teleop.stop_latched
    finally:sources.close()


@pytest.mark.parametrize('policy',[False,True])
def test_stop_cancels_actual_attempt_before_unattached_return_and_retains_late_original(tmp_path,monkeypatch,policy):
    sources,descriptor=prepared(tmp_path,monkeypatch,policy=policy);service=sources.service
    entered,release,stopped=threading.Event(),threading.Event(),threading.Event();errors=[];request=service._request
    def routed(role):
        callback=request(role)
        def invoke(method,path,payload):
            result=callback(method,path,payload)
            if role=='giver' and path.endswith('/phase/resume-release'):entered.set();assert release.wait(5)
            return result
        return invoke
    service._request=routed
    def control():
        try:service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=control)
    try:
        worker.start();assert entered.wait(2)
        stopper=threading.Thread(target=lambda:(service.command('stop',{'session_id':sources.row['session_id'],'expected_epoch':sources.row['epoch']},authenticated_actor=ACTOR),stopped.set()))
        stopper.start();assert stopped.wait(2)
        assert sources.pair.teleop.stop_latched and sources.pair.owner.guard.grant is None
        assert sources.pair.owner.guard.armed and sources.wrapper.helper.armed and not service.transports
        release.set();worker.join(3);stopper.join(3)
        assert not worker.is_alive() and errors and service.workflow.snapshot(sources.row['session_id'])['status']=='STOPPED'
        originals=[json.loads(path.read_bytes()) for path in (service.root/'native-control').glob('*/receipt.json')]
        assert any(item['operation']=='native_parked_release_resume' and item['source_response'].get('native_command_called') is True for item in originals)
        assert service.parked_release.records[descriptor['release_bundle_id']]['state']=='CANCELLED'
    finally:release.set();worker.join(3);sources.close()


def test_real_workflow_waits_for_explicit_control_via_authenticated_node_bridge_and_preserves_native_challenge(tmp_path,monkeypatch):
    from explorer_shared.handover import HandoverFSM,HoldEvidence
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        service._arm_guard(sources.row)
        now=service.clock();fsm=HandoverFSM(sources.row['protocol_object_id'],mode='physical',clock_domain=service.domain,run_id=sources.row['session_id'])
        evidence=HoldEvidence(uuid.uuid4().hex,sources.row['protocol_object_id'],'operator','operator_confirmation','giver_holds_object',False,now,service.domain,now,service.domain)
        for event,facts in [('giver_holding',{'evidence':evidence}),('offer_ready',{'zone_registered':True,'giver_stationary':True}),
                ('receiver_ready',{'receiver_ready':True}),('receiver_at_object',{'both_stationary':True}),('receiver_closed',{})]:
            fsm.apply(event,event_id=uuid.uuid4().hex,now_ns=now,**facts)
        with service.workflow.lock:
            row=service.workflow.snapshot(sources.row['session_id']);row['protocol_state']=fsm.state;row['protocol_trace']=deepcopy(fsm.trace);service.workflow._save(row)
        with real_node(sources.pair,tmp_path,monkeypatch) as native_calls:
            request=service.workflow.request_step(row['session_id'],'release_requested',expected_epoch=row['epoch'])
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                current=service.workflow.snapshot(row['session_id']);pending=current.get('pending_operation') or {}
                if pending.get('native_release_challenge') and pending.get('native_control_checkpoint'):break
                time.sleep(.005)
            assert pending.get('native_release_challenge'),current
            descriptor=pending['native_release_challenge'];assert current['protocol_state']=='RECEIVER_GRASP_CHECK'
            result=service.command('native_parked_release_resume',body(row,descriptor),authenticated_actor=ACTOR)['result']
            service.workflow.threads[request['operation_id']].join(2)
            current=service.workflow.snapshot(row['session_id'])
            assert current['status']=='READY_FOR_OBSERVED_STEPS' and current['protocol_state']=='RELEASE',current
            assert current['pending_operation'] is None and not current['protocol_completed'] and not current['dispatch_receipts']
            assert any('explicit_parked_release_control' in entry for entry in current['observation_receipts'])
            assert result['completion']['release_grant']['challenge']==descriptor['challenge']
            posts=[path for method,path,_ in native_calls if method=='POST']
            assert posts==['/api/explorer/cooperative/phase/release/challenge','/api/explorer/cooperative/phase/resume-release']
            assert sources.pair.owner.guard.armed and sources.wrapper.helper.armed
    finally:sources.close()


@pytest.mark.parametrize('policy',[False,True])
def test_stop_during_immutable_control_fsync_remembers_owned_epoch_before_archive_and_never_waits_for_artifact(tmp_path,monkeypatch,policy):
    import explorer.cooperative_native_receipts as receipts
    sources,descriptor=prepared(tmp_path,monkeypatch,policy=policy);service=sources.service
    entered,release,stopped=threading.Event(),threading.Event(),threading.Event();errors=[];write=receipts.write_original
    def delayed(path,raw):
        if json.loads(raw).get('operation')=='native_parked_release_resume':entered.set();assert release.wait(5)
        return write(path,raw)
    monkeypatch.setattr(receipts,'write_original',delayed)
    def control():
        try:service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=control)
    try:
        worker.start();assert entered.wait(2)
        assert (sources.row['session_id'],sources.row['epoch'],'giver') in service.native_controls.owned
        stopper=threading.Thread(target=lambda:(service.command('stop',{'session_id':sources.row['session_id'],'expected_epoch':sources.row['epoch']},authenticated_actor=ACTOR),stopped.set()))
        stopper.start();assert stopped.wait(2)
        assert sources.pair.teleop.stop_latched and not service.transports and sources.pair.owner.guard.armed
        release.set();worker.join(3);stopper.join(3)
        assert errors and not worker.is_alive()
        originals=[json.loads(path.read_bytes()) for path in (service.root/'native-control').glob('*/receipt.json')]
        assert len([item for item in originals if item['operation']=='native_parked_release_resume'])==1
        assert not service.workflow.native_phase_changes
    finally:release.set();worker.join(3);sources.close()


def test_foreign_new_source_user_authorization_is_never_adopted_for_late_attempt_cleanup(tmp_path,monkeypatch):
    from test_native_activation import authorize,TOKEN
    sources,descriptor=prepared(tmp_path,monkeypatch);service=sources.service
    entered,release=threading.Event(),threading.Event();errors=[];request=service._request
    def routed(role):
        callback=request(role)
        def invoke(method,path,payload):
            result=callback(method,path,payload)
            if role=='giver' and path.endswith('/phase/resume-release'):entered.set();assert release.wait(5)
            return result
        return invoke
    service._request=routed
    def control():
        try:service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=control)
    try:
        worker.start();assert entered.wait(2)
        challenge=descriptor['challenge'];sources.pair.giver('POST','/legacy/explorer/cooperative/stop',
            {key:challenge[key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')}|
            {'control_epoch':None,'expected_native_generation':challenge['source_native_generation'],'observing':True})
        foreign,_=authorize(sources.pair.client,input_mode='keyboard',actor_id='different-explicit-USER')
        identity=foreign['authorization_id'];before=len(sources.pair.calls)
        service.command('stop',{'session_id':sources.row['session_id'],'expected_epoch':sources.row['epoch']},authenticated_actor=ACTOR)
        release.set();worker.join(3)
        assert errors and not worker.is_alive() and sources.pair.gate.current_authorization_id()==identity
        assert not any(method=='POST' and path.endswith('/cooperative/stop') for _,method,path,_ in sources.pair.calls[before:])
        assert not service.transports and sources.pair.owner.guard.armed
    finally:release.set();worker.join(3);sources.close()


def test_lost_control_reply_uses_declared_cancel_only_scope_without_fabricating_returned_control_receipt(tmp_path,monkeypatch):
    sources,descriptor=prepared(tmp_path,monkeypatch);service=sources.service;request=service._request
    def routed(role):
        callback=request(role)
        def invoke(method,path,payload):
            result=callback(method,path,payload)
            if role=='giver' and path.endswith('/phase/resume-release'):raise TimeoutError('SOFTWARE: actual parsed response was not delivered to Root')
            return result
        return invoke
    service._request=routed
    try:
        with pytest.raises(TimeoutError):service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        assert sources.pair.teleop.stop_latched and not service.transports and sources.pair.owner.guard.armed
        originals=[json.loads(path.read_bytes()) for path in (service.root/'native-control').glob('*/receipt.json')]
        assert not any(item['operation']=='native_parked_release_resume' for item in originals)
        assert service.parked_release.records[descriptor['release_bundle_id']]['attempted']
    finally:sources.close()


def test_completion_remains_unpublished_until_reservation_end_even_after_spurious_wakeup(tmp_path,monkeypatch):
    sources,descriptor=prepared(tmp_path,monkeypatch);service=sources.service
    entered,release,returned=threading.Event(),threading.Event(),threading.Event();errors=[];end=service.workflow.end_native_phase_change
    def delayed(token):
        result=end(token);entered.set();assert release.wait(2);return result
    monkeypatch.setattr(service.workflow,'end_native_phase_change',delayed)
    def control():
        try:service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        except Exception as error:errors.append(error)
    waiter=threading.Thread(target=lambda:(service.parked_release.wait(sources.row,descriptor),returned.set()))
    worker=threading.Thread(target=control)
    try:
        waiter.start();worker.start();assert entered.wait(2)
        with service.parked_release.condition:service.parked_release.condition.notify_all()
        assert not returned.wait(.05) and service.parked_release.records[descriptor['release_bundle_id']]['state']=='VALIDATED_AWAIT_RELEASE'
        release.set();worker.join(3);waiter.join(3)
        assert not errors and returned.is_set() and not worker.is_alive() and not waiter.is_alive()
    finally:release.set();worker.join(3);waiter.join(3);sources.close()


def test_known_scope_cleanup_error_cannot_skip_declared_inflight_scope_cancellation(tmp_path,monkeypatch):
    sources,descriptor=prepared(tmp_path,monkeypatch);service=sources.service;called=[]
    def failing(*args,**kwargs):raise OSError('SOFTWARE independent known-scope cleanup failure')
    monkeypatch.setattr(service.native_controls,'cancel_owned_sessions',failing)
    monkeypatch.setattr(service.parked_release,'cancel_owned_attempts',lambda row:called.append(row['epoch']) or {'delivery_unknown':True})
    try:
        receipt=service._stop_owned_native(sources.row)
        assert called==[sources.row['epoch']] and receipt['owned_session_cleanup_delivery_unknown']
        assert service.parked_release.records[descriptor['release_bundle_id']]['state']=='CANCELLED'
    finally:sources.close()


@pytest.mark.parametrize('failure',['bundle_fsync','source_final_wal'])
def test_bundle_or_source_wal_failure_never_publishes_current_control_completion(tmp_path,monkeypatch,failure):
    sources,descriptor=prepared(tmp_path,monkeypatch);service=sources.service;before=len(sources.pair.calls)
    if failure=='bundle_fsync':
        import explorer_shared.handover_adapters as inbox_module
        original=inbox_module._write
        def fail(path,value):
            if value.get('state')=='consumed':raise OSError('SOFTWARE bundle atomic fsync failure')
            return original(path,value)
        monkeypatch.setattr(inbox_module,'_write',fail)
    else:
        monkeypatch.setattr(sources.pair.owner.guard.journal,'seal',lambda *args,**kwargs:(_ for _ in ()).throw(OSError('SOFTWARE actual source final WAL failure')))
    try:
        with pytest.raises((ValueError,OSError)):service.command('native_parked_release_resume',body(sources.row,descriptor),authenticated_actor=ACTOR)
        assert not service.transports and sources.pair.owner.guard.armed and sources.wrapper.helper.armed
        assert service.parked_release.records[descriptor['release_bundle_id']]['state']=='FAILED'
        if failure=='bundle_fsync':assert not any(method=='POST' for _,method,_,_ in sources.pair.calls[before:])
        else:assert sources.pair.owner.guard.journal.status(sources.pair.owner.guard.revision)['recovery_required'] and sources.pair.teleop.stop_latched
    finally:sources.close()
