"""Concurrent evidence, source scope changes and STOP; no physical owners."""
import threading
import time
import json

import pytest

from explorer_shared.cooperative_workflow05 import CooperativeWorkflow
from test_cooperative_workflow05 import fixture,zone,run_step,DOMAIN


def pending(service,row):return service.snapshot(row['session_id']).get('pending_operation') or {}


def wait_closed(service,row):
    deadline=time.monotonic()+2
    while (pending(service,row).get('native_control_checkpoint') or {}).get('accepting') is not False and time.monotonic()<deadline:
        time.sleep(.005)
    assert (pending(service,row).get('native_control_checkpoint') or {}).get('accepting') is False


def test_evidence_checkpoint_allows_explicit_scope_changes_but_closes_before_any_protocol_advance(tmp_path):
    service,row,inbox,calls,refs=fixture(tmp_path);inbox.block='giver_holds_object'
    result=service.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch']);thread=service.threads[result['operation_id']]
    token=None
    try:
        assert inbox.entered.wait(2) and service.native_phase_change_window(row)
        projected=service.snapshot(row['session_id']);service._save(projected)
        assert 'native_control_checkpoint' not in json.loads(service._path(row['session_id']).read_bytes())['pending_operation']
        token=service.begin_native_phase_change(row['session_id'],row['epoch'],'giver','native_phase_park')
        with pytest.raises(ValueError,match='already'):service.begin_native_phase_change(row['session_id'],row['epoch'],'giver','native_phase_adopt')
        inbox.release.set();wait_closed(service,row)
        assert thread.is_alive() and service.snapshot(row['session_id'])['protocol_state']=='PREPARE'
        with pytest.raises(ValueError,match='checkpoint'):service.begin_native_phase_change(row['session_id'],row['epoch'],'receiver','native_phase_activate')
        assert service.end_native_phase_change(token) and not service.end_native_phase_change(token)
        thread.join(2);assert not thread.is_alive()
        assert service.snapshot(row['session_id'])['protocol_state']=='ROSMASTER_HOLDING' and not calls
    finally:
        inbox.release.set()
        if token:service.end_native_phase_change(token)
        thread.join(3);service.close()


def test_inflight_native_scope_change_prevents_new_step_without_motor_or_evidence_calls(tmp_path):
    service,row,inbox,calls,refs=fixture(tmp_path);token=service.begin_native_phase_change(row['session_id'],row['epoch'],'receiver','native_phase_activate')
    try:
        with pytest.raises(ValueError,match='phase change'):service.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch'])
        assert not inbox.calls and not calls and not pending(service,row)
        changed=dict(token,token_id='foreign')
        assert not service.end_native_phase_change(changed) and service._native_phase_inflight(row['session_id'],row['epoch'])
    finally:service.end_native_phase_change(token);service.close()


def test_stop_does_not_wait_for_reserved_source_reply_and_late_release_cannot_reactivate_protocol(tmp_path):
    service,row,inbox,calls,refs=fixture(tmp_path);inbox.block='giver_holds_object';token=None
    result=service.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch']);thread=service.threads[result['operation_id']]
    try:
        assert inbox.entered.wait(2);token=service.begin_native_phase_change(row['session_id'],row['epoch'],'giver','native_phase_adopt')
        inbox.release.set();wait_closed(service,row);began=time.monotonic()
        service.cancel_session(row['session_id']);assert time.monotonic()-began<1.
        thread.join(1);assert not thread.is_alive()
        stopped=service.snapshot(row['session_id']);assert stopped['status']=='STOPPED' and stopped['protocol_state']=='SAFE_STOP'
        assert not service.native_phase_change_window(row)
        service.end_native_phase_change(token)
        assert service.snapshot(row['session_id'])['epoch']==stopped['epoch'] and len(calls)==2
    finally:
        inbox.release.set()
        if token:service.end_native_phase_change(token)
        thread.join(3);service.close()


def test_rejected_evidence_cancels_immediately_instead_of_waiting_on_a_native_phase_reply(tmp_path):
    service,row,inbox,calls,refs=fixture(tmp_path);entered,release=threading.Event(),threading.Event();token=None
    def reject(*args):entered.set();assert release.wait(3);raise ValueError('EXPLICIT_TEST_OPERATOR_DECLINED')
    service.inbox=reject
    result=service.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch']);thread=service.threads[result['operation_id']]
    try:
        assert entered.wait(2);token=service.begin_native_phase_change(row['session_id'],row['epoch'],'giver','native_phase_park')
        # Preserve the normal inbox cancellation contract without changing the
        # blocked evidence callable invoked by the already running thread.
        service.inbox=inbox;release.set();thread.join(1)
        assert not thread.is_alive() and service.snapshot(row['session_id'])['status']=='STOPPED'
        assert any('EXPLICIT_TEST_OPERATOR_DECLINED' in item['error'] for item in service.snapshot(row['session_id'])['errors'])
    finally:
        release.set()
        if token:service.end_native_phase_change(token)
        thread.join(3);service.close()


def test_actual_native_guard_submission_is_not_an_evidence_change_window(tmp_path):
    entered,release=threading.Event(),threading.Event()
    def arm(row):entered.set();assert release.wait(3);return {'software_guard_armed':True}
    service,row,inbox,calls,refs=fixture(tmp_path,execution_domain='user_observed_physical_test',giver={'arm_release_guard':arm})
    zone(service,row)
    for event in ('giver_holding','offer_ready','receiver_ready'):run_step(service,row,event)
    result=service.request_step(row['session_id'],'receiver_at_object',expected_epoch=row['epoch']);thread=service.threads[result['operation_id']]
    try:
        assert entered.wait(2) and not service.native_phase_change_window(row)
        with pytest.raises(ValueError,match='checkpoint'):service.begin_native_phase_change(row['session_id'],row['epoch'],'giver','native_phase_park')
        release.set();thread.join(2);assert service.snapshot(row['session_id'])['protocol_state']=='RECEIVER_GRASP_CHECK'
    finally:release.set();thread.join(3);service.close()


def test_native_challenge_evidence_window_keeps_its_original_epoch_bound_scope(tmp_path):
    service,row,inbox,calls,refs=fixture(tmp_path)
    try:
        with service.lock:
            current=service._session(row['session_id']);current['pending_operation']={'operation_id':'fixed-operation','native_release_challenge':{'source_challenge':'fixed-source-epoch'}};service._save(current)
        with service._native_evidence_checkpoint(row,'fixed-operation',service._fsm(row)):
            assert not service.native_phase_change_window(row)
            assert pending(service,row)['native_control_checkpoint']['binding_change_blocker']=='SOURCE_CHALLENGE_IS_EPOCH_BOUND'
            with pytest.raises(ValueError,match='checkpoint'):service.begin_native_phase_change(row['session_id'],row['epoch'],'giver','native_phase_adopt')
        assert not calls
    finally:service.close()


def test_restart_discards_reservations_and_interrupts_old_pending_epoch_without_source_replay(tmp_path):
    service,row,inbox,calls,refs=fixture(tmp_path);token=service.begin_native_phase_change(row['session_id'],row['epoch'],'giver','native_phase_park')
    try:
        restored=CooperativeWorkflow(tmp_path,service.registry,evidence_inbox=inbox,clock_domain=DOMAIN)
        try:
            current=restored.snapshot(row['session_id']);assert current['status']=='INTERRUPTED' and current['epoch']!=row['epoch']
            assert not restored.native_phase_changes and not restored.native_phase_change_window(row) and not calls
            assert not restored.end_native_phase_change(token)
        finally:restored.close()
    finally:service.end_native_phase_change(token);service.close()
