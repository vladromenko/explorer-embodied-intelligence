"""Source review identity/hash gates; local fixtures only."""
from copy import deepcopy
import hashlib
import uuid

import pytest

from explorer.native_capture_approval import source_bytes,validate_source_approval


def approved(recording_id='a'*32,archive_sha256='b'*64,actor='Владелец'):
    receipt={'schema':'explorer_native_capture_review_v1','review_id':uuid.uuid4().hex,'recording_id':recording_id,
        'archive_sha256':archive_sha256,'decision':'approve','actor_id':actor,'reviewed_native_ns':1000,
        'source_boot_id':'fixture-boot','source_clock_domain':'nano:fixture-boot','raw_archive_unchanged':True,'previous_review_sha256':None}
    return {'source_review':receipt,'review_sha256':hashlib.sha256(source_bytes(receipt)).hexdigest(),'transfer_ready':True}


def test_exact_ascii_escaped_source_receipt_is_not_training_or_outcome_claim():
    wrapper=approved();before=deepcopy(wrapper)
    result=validate_source_approval(wrapper,'a'*32,'b'*64,actor='Владелец',expected_review_sha256=wrapper['review_sha256'])
    assert result['verified'] and result['approval_is_not_expert_outcome_or_physical_validation'] and wrapper==before


@pytest.mark.parametrize('field',['missing','archive','episode','actor','hash','clock','numeric_time','mutable','ready','rejected'])
def test_missing_forged_or_rejected_source_review_never_admits_transfer(field):
    wrapper=approved()
    if field=='missing':wrapper['source_review']=None
    elif field=='hash':wrapper['review_sha256']='c'*64
    elif field=='ready':wrapper['transfer_ready']=1
    else:
        key,value={'archive':('archive_sha256','c'*64),'episode':('recording_id','c'*32),'actor':('actor_id','other'),
            'clock':('source_clock_domain',''),'numeric_time':('reviewed_native_ns',True),'mutable':('raw_archive_unchanged',0),
            'rejected':('decision','reject')}[field]
        wrapper['source_review'][key]=value
        wrapper['review_sha256']=hashlib.sha256(source_bytes(wrapper['source_review'])).hexdigest()
        if field=='rejected':wrapper['transfer_ready']=False
    with pytest.raises(ValueError):validate_source_approval(wrapper,'a'*32,'b'*64,actor='Владелец')


def test_central_post_seal_review_is_immutable_idempotent_and_never_relabels_finished_outcome(tmp_path):
    from explorer.store import Store
    from explorer import skill_workflow as flow
    from test_skill_workflow import skill
    store=Store(tmp_path/'state');selected=skill(store);identity=selected['spec']['skill_id']
    session=flow.create_session(store,identity,1,{'split':'train','scene':'mock-scene'})
    capture=flow.begin_capture(store,identity,1,'variant-001',session['session_id'],stationary_chassis_confirmed=True)
    flow.capture_started(store,capture['capture_id'],'a'*32);wrapper=approved()
    with pytest.raises(ValueError,match='unfinished'):flow.capture_reviewed(store,capture['capture_id'],'a'*32,wrapper,authenticated_actor='Владелец')
    finished=flow.capture_finished(store,capture['capture_id'],'a'*32,{'outcome':'unknown'})
    first=flow.capture_reviewed(store,capture['capture_id'],'a'*32,wrapper,authenticated_actor='Владелец')
    assert first==flow.capture_reviewed(store,capture['capture_id'],'a'*32,wrapper,authenticated_actor='Владелец')
    assert first['approval_does_not_change_outcomes'] and not first['automatic_training_started']
    assert finished['outcome']=='unknown' and not finished['full_task_confirmed'] and not store.jobs()
    with pytest.raises(ValueError):flow.capture_reviewed(store,capture['capture_id'],'a'*32,wrapper,authenticated_actor='foreign')
    with store.connect() as db:
        rows=flow._records(db,'capture.reviewed');assert len(rows)==1
        assert flow._records(db,'capture.finished')[0]['payload']==finished
