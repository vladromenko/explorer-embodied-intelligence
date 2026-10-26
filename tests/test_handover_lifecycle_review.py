"""Regressions for cancellation and consumption across API/worker scheduling gaps."""
import json
import threading
import time

import pytest

from explorer_shared.handover_adapters import OperatorEvidenceInbox


def test_cancelled_attempt_cannot_create_a_later_challenge_even_in_an_attached_api_process(tmp_path):
    worker=OperatorEvidenceInbox(tmp_path)
    worker.cancel_run('run-1')
    attached=OperatorEvidenceInbox(tmp_path,session_id=worker.session_id)
    with pytest.raises(RuntimeError,match='new attempt'):
        attached('receiver_holds_object','run-1','object-1','receiver','close-1',.1)
    assert attached.pending('run-1')==[]
    # The revocation is scoped to that attempt, not a new explicitly started one.
    assert OperatorEvidenceInbox(tmp_path).session_id!=worker.session_id


def test_predeadline_confirmation_cannot_be_consumed_after_the_challenge_deadline(tmp_path):
    clock_value=[1_000_000_000]
    clock=lambda:clock_value[0]
    worker=OperatorEvidenceInbox(tmp_path,clock=clock)
    api=OperatorEvidenceInbox(tmp_path,session_id=worker.session_id,clock=clock)
    evidence,errors=[],[]
    def waiting():
        try:evidence.append(worker('receiver_holds_object','run-1','object-1','receiver','close-1',2))
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=waiting);thread.start()
    for _ in range(100):
        pending=worker.pending()
        if pending:break
        time.sleep(.005)
    assert pending
    # The worker cannot reacquire its condition while another API process writes
    # an on-time confirmation. Resume it only after the deadline has elapsed.
    with worker.condition:
        clock_value[0]=2_000_000_000
        api.confirm(pending[0]['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='owner')
        clock_value[0]=4_000_000_000
    thread.join(2)
    assert not thread.is_alive() and not evidence and isinstance(errors[0],TimeoutError)
    stored=json.loads((tmp_path/(pending[0]['challenge_id']+'.json')).read_text())
    assert stored['state']=='expired' and stored['received_ns']==2_000_000_000


def test_cancel_after_confirmation_revokes_it_before_worker_consumption(tmp_path):
    worker=OperatorEvidenceInbox(tmp_path)
    api=OperatorEvidenceInbox(tmp_path,session_id=worker.session_id)
    evidence,errors=[],[]
    def waiting():
        try:evidence.append(worker('receiver_holds_object','run-1','object-1','receiver','close-1',2))
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=waiting);thread.start()
    for _ in range(100):
        pending=worker.pending()
        if pending:break
        time.sleep(.005)
    with worker.condition:
        api.confirm(pending[0]['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='owner')
        api.cancel_run('run-1')
    thread.join(2)
    assert not thread.is_alive() and not evidence and isinstance(errors[0],RuntimeError)
