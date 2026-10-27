"""Internal protocol cancellation must clean actual returned fake native owners."""
import threading
import time

import pytest

from test_cooperative_native_sessions import configured,choice,execute
from test_cooperative_unattached_stop import close
from test_cooperative_workflow05 import fixture as workflow_fixture


@pytest.mark.parametrize('role',['receiver','giver'])
def test_internal_step_failure_stops_returned_begin_or_resume_even_before_transport_attach(tmp_path,monkeypatch,role):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        started=execute(service,'native_begin',choice(service,row,role,'native_begin'))
        if role=='giver':started=execute(service,'native_resume',choice(service,row,role,'native_resume'))
        assert not service.transports
        def rejected(fsm):raise ValueError('fixture: protocol phase invalidated while native owner was not attached')
        monkeypatch.setattr(service.workflow,'_phase_current',rejected)
        request=service.workflow.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch'])
        service.workflow.threads[request['operation_id']].join(4)
        assert not service.workflow.threads[request['operation_id']].is_alive()
        actual=service.workflow.snapshot(row['session_id'])
        assert actual['epoch']!=row['epoch'] and actual['status']=='STOPPED'
        assert actual['stop_reason']=='PROTOCOL_STEP_FAILED:ValueError'
        assert any('phase invalidated' in item['error'] for item in actual['errors'])
        receipt=actual['native_owned_session_cleanup']
        assert receipt['coordinator_epoch']==row['epoch'] and receipt['roles'][role]['known_owned_native_session']
        assert not receipt['native_lease_automatically_renewed'] and not receipt['dual_hold_interlocks_unarmed']
        if role=='receiver':
            expected=started['native_receipt']['grant']['control_epoch']
            stops=[body for operation,body in pair.station.calls if operation=='stop']
            assert len(stops)==1 and stops[0]['expected_control_epoch']==expected
            assert pair.station.mode=='idle' and not pair.provider.status()['execution_permission']
        else:
            expected=started['native_receipt']['native']['owner_generation']
            stops=[body for _,method,path,body in pair.calls if method=='POST' and path.endswith('/cooperative/stop')]
            assert len(stops)==1 and stops[0]['expected_native_generation']==expected and pair.teleop.stop_latched
    finally:close(service,pair,client)


def test_internal_cancellation_cannot_adopt_a_foreign_current_source_epoch(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        old=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))['native_receipt']['grant']
        newer=pair.provider.command(old['grant_id'],old['binding'],'teleop/recenter',{'expected_control_epoch':old['control_epoch']})['grant']
        before=list(pair.station.calls);result=service.workflow.cancel_session(row['session_id'],'INTERNAL_PROTOCOL_FAILURE')
        assert result['native_owned_session_cleanup']['roles']['receiver']['source_cleanup_receipt']['new_native_epoch_not_owned']
        assert pair.station.calls==before and pair.provider.status()['grant']['control_epoch']==newer['control_epoch']
    finally:close(service,pair,client)


def test_epoch_revokes_before_delayed_owned_cleanup_and_both_transport_stops_run_without_workflow_lock(tmp_path):
    workflow,row,inbox,calls,refs=workflow_fixture(tmp_path)
    entered,release=threading.Event(),threading.Event();saved=[];results=[]
    def cleanup(previous):
        assert workflow.lock.acquire(blocking=False)
        try:
            assert workflow.snapshot(row['session_id'])['epoch']!=row['epoch']
            saved.append(previous)
        finally:workflow.lock.release()
        entered.set();assert release.wait(3)
        return {'exact_saved_scope':True}
    workflow.native_owned_stop=cleanup
    worker=threading.Thread(target=lambda:results.append(workflow.cancel_session(row['session_id'],'INTERNAL_FAILURE')))
    try:
        worker.start();assert entered.wait(2)
        assert saved[0]['epoch']==row['epoch'] and saved[0]['process_session']==workflow.process
        end=time.monotonic()+1
        while {item[1] for item in calls if item[0]=='stop'}!={'giver','receiver'} and time.monotonic()<end:time.sleep(.005)
        assert {item[1] for item in calls if item[0]=='stop'}=={'giver','receiver'}
        assert workflow.snapshot(row['session_id'])['status']=='STOPPED'
    finally:release.set();worker.join(3);workflow.close()
    assert not worker.is_alive() and results[0]['native_owned_session_cleanup']=={'exact_saved_scope':True}


def test_failed_owned_cleanup_is_retained_as_unknown_and_does_not_skip_native_transport_stops(tmp_path):
    workflow,row,inbox,calls,refs=workflow_fixture(tmp_path)
    def cleanup(previous):raise OSError('fixture: source STOP delivery unavailable')
    workflow.native_owned_stop=cleanup
    try:
        result=workflow.cancel_session(row['session_id'],'INTERNAL_FAILURE')
        assert result['session']['status']=='STOPPED' and result['physical_stop_confirmed'] is False
        assert result['native_owned_session_cleanup']['delivery_unknown']
        assert 'delivery unavailable' in result['native_owned_session_cleanup']['error']
        assert {item[1] for item in calls if item[0]=='stop'}=={'giver','receiver'}
    finally:workflow.close()


def test_explicit_root_stop_reuses_workflow_owned_cleanup_without_second_native_scope_cancellation(tmp_path,monkeypatch):
    from test_cooperative_observed_service import command
    service,store,row,spec,pair,client=configured(tmp_path);cancel=service.native_controls.cancel_owned_sessions;calls=[]
    try:
        execute(service,'native_begin',choice(service,row,'receiver','native_begin'))
        def recorded(previous,**kwargs):calls.append(previous['epoch']);return cancel(previous,**kwargs)
        monkeypatch.setattr(service.native_controls,'cancel_owned_sessions',recorded)
        result=command(service,row,'stop')
        assert calls==[row['epoch']] and result['native_owned_session_cleanup']['roles']['receiver']['known_owned_native_session']
        assert [operation for operation,_ in pair.station.calls]==['teleop/start','stop']
    finally:close(service,pair,client)


def test_inbox_cancellation_write_failure_cannot_prevent_original_scoped_native_stop(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        started=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))['native_receipt']['grant']
        def failed(run):raise OSError('fixture: cancelled-run marker fsync failed')
        monkeypatch.setattr(service.inbox,'cancel_run',failed)
        result=service.workflow.cancel_session(row['session_id'],'EVIDENCE_IO_FAILURE')
        assert result['session']['status']=='STOPPED' and 'marker fsync failed' in result['session']['evidence_cancellation_error']
        stops=[body for operation,body in pair.station.calls if operation=='stop']
        assert len(stops)==1 and stops[0]['expected_control_epoch']==started['control_epoch'] and pair.station.mode=='idle'
    finally:close(service,pair,client)


@pytest.mark.parametrize('elapsed_ns',[1_999_999_999,2_000_000_001])
def test_original_stationary_freshness_is_rechecked_after_guard_network_and_wal_io(tmp_path,monkeypatch,elapsed_ns):
    import time
    from explorer_shared.handover_runtime import ArmObservation
    from test_cooperative_workflow05 import zone,run_step,DOMAIN
    tick=[time.monotonic_ns()];arms=[]
    def arm(row):
        arms.append(row['session_id']);tick[0]+=elapsed_ns
        return {'both_original_publication_boundaries_armed':True,'fixture_only':True}
    workflow,row,inbox,calls,refs=workflow_fixture(tmp_path,giver={'arm_release_guard':arm})
    try:
        zone(workflow,row)
        for event in ('giver_holding','offer_ready','receiver_ready'):row=run_step(workflow,row,event)
        row['execution_domain']='user_observed_physical_test';workflow._save(row)
        tick[0]=time.monotonic_ns();workflow.clock=lambda:tick[0]
        monkeypatch.setattr(workflow,'_stationary',lambda current,role,after=None:ArmObservation(role,True,tick[0],DOMAIN,{'explicit_USER_fact':True},after))
        current=run_step(workflow,row,'receiver_at_object');assert arms==[row['session_id']]
        if elapsed_ns<2_000_000_000:
            assert current['status']=='READY_FOR_OBSERVED_STEPS' and current['protocol_state']=='RECEIVER_GRASP_CHECK'
        else:
            assert current['status']=='STOPPED' and current['protocol_state']=='SAFE_STOP'
            assert any('expired during native guard arm' in item['error'] for item in current['errors'])
            assert {item[1] for item in calls if item[0]=='stop'}=={'giver','receiver'}
    finally:workflow.close()
