"""An approval remains separate from expert labels and current cohort decisions."""
from copy import deepcopy
import hashlib
import json

from explorer import skill_workflow as flow
from explorer.store import Store
from explorer.mobile_dataset_evidence import inspect_mobile_import
from explorer.native_capture_approval import source_bytes
from test_native_capture_approval import approved
from test_skill_workflow import skill,imported,assign


def native_job(store,selected):
    job,path=imported(store,selected,'a'*32)
    wrapper=approved();source=path.parent/'source-review.json';source.write_bytes(source_bytes(wrapper))
    job['config'].update(source_format='native_capture_v1',archive_sha256='b'*64,source_review_sha256=wrapper['review_sha256'])
    job['result'].update(schema='explorer_native_capture_import_v1',source_archive_sha256='b'*64,source_approval=wrapper,
        source_review_sha256=wrapper['review_sha256'],source_approval_path=str(source),source_approval_file_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    with store.connect() as db:db.execute('UPDATE jobs SET config=?,result=? WHERE id=?',(json.dumps(job['config']),json.dumps(job['result']),job['id']))
    return store.job(job['id']),wrapper


def test_missing_source_review_denies_new_native_source_but_retains_canonical_structural_evidence(tmp_path):
    store=Store(tmp_path/'state');selected=skill(store);job,wrapper=native_job(store,selected)
    assert inspect_mobile_import(job,managed_root=store.root)['admitted_for_training']
    job['result'].pop('source_approval_path')
    report=inspect_mobile_import(job,managed_root=store.root)
    assert report['mobile_embodied']['admitted_for_training'] and not report['admitted_for_training']
    assert any('source_approval' in reason for reason in report['reasons'])


def test_later_reject_revokes_cohort_acceptance_without_rewriting_import_or_outcomes(tmp_path):
    store=Store(tmp_path/'state');selected=skill(store);job,wrapper=native_job(store,selected);identity=selected['spec']['skill_id']
    session=flow.create_session(store,identity,1,{'split':'train','scene':'explicit-fixture'})
    capture=flow.begin_capture(store,identity,1,'variant-001',session['session_id'],stationary_chassis_confirmed=True)
    flow.capture_started(store,capture['capture_id'],'a'*32)
    finished=flow.capture_finished(store,capture['capture_id'],'a'*32,{'outcome':'success','completed_stages':list(flow.MANIPULATION_STAGES),
        'full_task_confirmed':True,'object_and_goal_visible':True})
    flow.capture_reviewed(store,capture['capture_id'],'a'*32,wrapper,authenticated_actor='Владелец')
    assign(store,selected,job,'variant-001',session)
    initial=flow.workflow(store,identity,1);assert initial['assignments'][0]['accepted'],initial['assignments'][0]['reasons']
    rejected=deepcopy(wrapper);rejected['source_review'].update(decision='reject',previous_review_sha256=wrapper['review_sha256'],review_id='c'*32)
    rejected['review_sha256']=hashlib.sha256(source_bytes(rejected['source_review'])).hexdigest();rejected['transfer_ready']=False
    flow.capture_reviewed(store,capture['capture_id'],'a'*32,rejected,authenticated_actor='Владелец')
    current=flow.workflow(store,identity,1)
    assert not current['assignments'][0]['accepted'] and 'source_approval_rejected_after_import' in current['assignments'][0]['reasons']
    assert store.job(job['id'])['result']==job['result']
    with store.connect() as db:assert flow._records(db,'capture.finished')[0]['payload']==finished
