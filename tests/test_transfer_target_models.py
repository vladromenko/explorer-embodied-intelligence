"""Structural SOFTWARE artifacts only; shared validation/evaluator, no real weights."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from explorer import transfer_target as target,transfer_target_models as models
from explorer_shared.contracts import ACTION_NAMES,canonical,digest
from explorer_shared.learning.artifacts import sha
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from test_transfer_target_api import prepared


def model_fixture(tmp_path,monkeypatch):
    app,store,client,csrf,wizard,dataset_id=prepared(tmp_path,monkeypatch)
    job=target.enqueue_training(store,wizard['wizard_id'],dataset_id,dict(mode='target_only',demonstrations=5,
        act_steps=2,request_id=str(uuid.uuid4())),actor='OWN_STRUCTURAL_TEST')
    config=job['config'];corpus=CanonicalCorpus(config['dataset_manifest'])
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    checkpoint=folder/'student_act';checkpoint.mkdir();(checkpoint/'fixture-weights').write_bytes(b'DUMMY_NOT_OPTIMIZED_ACT9')
    weights={'fixture-weights':sha(checkpoint/'fixture-weights')}
    document=dict(schema='explorer_rosmaster_skill_model_v1',job_kind=target.JOB_KIND,
        action_contract_id='rosmaster_joint9_position6_velocity3_v1',action_names=list(ACTION_NAMES),
        skill_id=corpus.skill_id,skill_version=corpus.version,skill_spec_sha256=corpus.manifest['skill_spec_sha256'],
        ACT9=dict(state_dimension=106,output_dimension=9),weights_sha256=weights,aggregate_sha256=corpus.sha256,
        compatible_source_projects=['06'],transfer_target_selection=config['target_selection'],
        transfer_initialization=dict(source_model_binding=wizard['source_model_binding'],mode='target_only'),
        preprocessing=dict(image_size=32,normalizer=corpus.normalizer.document()),
        train_original_episode_ids=[r['descriptor']['source_episode_id'] for r in corpus.train],
        development_original_episode_ids=[r['descriptor']['source_episode_id'] for r in corpus.development])
    path=folder/'model_manifest.json';path.write_bytes(canonical(document))
    result=dict(trained=True,weights_changed=True,save_reload_passed=True,optimizer_steps=2,device='cpu',feature_dimension=106,output_action_dimension=9,
        model_manifest=str(path),model_manifest_sha256=sha(path),checkpoint_dir=str(checkpoint),checkpoint_sha256=weights,
        source_manifest_path=config['dataset_manifest'],source_manifest_sha256=corpus.sha256,
        target_selection=config['target_selection'],comparison_mode=config['mode'],fixture_notice='STRUCTURAL_NOT_OPTIMIZER_PROOF')
    (folder/'result.json').write_bytes(canonical(result));store.complete_job(job['id'],result)
    return app,store,client,csrf,wizard,store.job(job['id']),document


def test_target06_saved_model_HTTP_catalogue_and_exact_offline_queue_with_no_model_load(tmp_path,monkeypatch):
    app,store,client,csrf,wizard,trained,_=model_fixture(tmp_path,monkeypatch)
    prefix='/api/transfer/calibration/'+wizard['wizard_id']
    response=client.get(prefix+'/models');assert response.status_code==200,response.text
    listing=response.json();assert listing['models'][0]['compatible'] and not listing['model_loaded']
    assert not listing['live_shadow_available'] and listing['source_network_requests_sent']==0
    body=dict(model_job_id=trained['id'])
    assert client.post(prefix+'/evaluate',json=body).status_code==403
    assert client.post(prefix+'/evaluate',headers=csrf,json=dict(body,partition='sealed_test')).status_code==400
    first=client.post(prefix+'/evaluate',headers=csrf,json=body);assert first.status_code==200,first.text
    second=client.post(prefix+'/evaluate',headers=csrf,json=body)
    assert first.json()['id']==second.json()['id'] and first.json()['kind']==models.EVAL_KIND
    assert first.json()['config']['partition']=='development'
    from explorer.queue_policy import default_metadata,GIB
    budget=default_metadata(first.json())['resources'];assert budget['memory_bytes']==2*GIB and budget['cpu_cores']==2 and budget['gpu_slots']==0


def test_target06_and_old02_share_evaluator_DEV_only_no_optimizer_or_Source_transport(tmp_path,monkeypatch):
    app,store,client,csrf,wizard,trained,document=model_fixture(tmp_path,monkeypatch)
    job=models.enqueue_evaluation(store,wizard['wizard_id'],trained['id']);events=[]
    def loader(path):events.append('load');return SimpleNamespace(policy=SimpleNamespace(config=SimpleNamespace(chunk_size=2)))
    def evaluator(policy,corpus,chunks,guard):
        guard();events.append('evaluate')
        assert all(e['descriptor']['split']=='development' for e in chunks.episodes)
        assert corpus.normalizer.document()==document['preprocessing']['normalizer']
        return dict(sealed_test_used=False,learned_policy_rollout_executed=False,fixture_evaluator=True)
    result=models.run_evaluation(store,job,loader=loader,evaluator=evaluator)
    assert events==['load','evaluate'] and result['optimizer_steps_executed']==0 and result['weights_unchanged']
    assert result['source_network_requests_sent']==result['physical_control_requests_sent']==0 and not result['dispatch_performed']
    assert not result['independent_full_task_success'] and result['project_id']=='06'
    assert result['evaluated_original_episode_ids']==document['development_original_episode_ids']


def test_target_saved_receipt_and_cancel_drift_fail_before_loading_and_keep_originals(tmp_path,monkeypatch):
    app,store,client,csrf,wizard,trained,_=model_fixture(tmp_path,monkeypatch)
    job=models.enqueue_evaluation(store,wizard['wizard_id'],trained['id'])
    store.update_job(job['id'],status='cancel_requested')
    def forbidden(*args,**kwargs):raise AssertionError('Do not load cancelled model')
    with pytest.raises(RuntimeError,match='cancelled'):models.run_evaluation(store,job,loader=forbidden)
    assert not (store.root/'jobs'/job['id']/'offline-evaluation.json').exists()
    receipt=store.root/'jobs'/trained['id']/'result.json';old=receipt.read_bytes();receipt.write_bytes(b'{}')
    with pytest.raises(ValueError,match='training receipt changed'):models.model_reference(store,wizard['wizard_id'],trained['id'])
    assert models.model_versions(store,wizard['wizard_id'])['models'][0]['compatible'] is False
    receipt.write_bytes(old)  # Restore only this deliberately changed test-owned negative fixture.


def test_unfinished_or_cancelled_training_is_not_presented_as_a_saved_model(tmp_path,monkeypatch):
    app,store,client,csrf,wizard,trained,_=model_fixture(tmp_path,monkeypatch)
    queued=store.create_job(target.JOB_KIND,'06',dict(wizard_id=wizard['wizard_id'],mode='vision_adapt',demonstrations=25))
    listing=models.model_versions(store,wizard['wizard_id'])
    assert [row['model_job_id'] for row in listing['models']]==[trained['id']]
    assert [row['model_job_id'] for row in listing['training_jobs']]==[queued['id']]
