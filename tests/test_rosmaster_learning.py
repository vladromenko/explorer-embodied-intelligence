"""Temp canonical originals and dummy weights test orchestration, NOT ML success."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from explorer.rosmaster_learning import model_versions,model_reference,evaluation_request,enqueue_evaluation,enqueue_shadow
from explorer.rosmaster_eval_job import run_rosmaster_evaluation
from explorer_shared.contracts import ACTION_NAMES,canonical,digest
from explorer_shared.embodied_episode import ACTION_CONTRACT_ID
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from test_rosmaster_skill_pipeline import fixture_aggregate


def model_fixture(tmp_path):
    aggregate,store=fixture_aggregate(tmp_path,missing_sensors=True);corpus=CanonicalCorpus(aggregate)
    folder=store.root/'models'/'explicit-software-fixture';folder.mkdir(parents=True)
    checkpoint=folder/'student_act';checkpoint.mkdir();(checkpoint/'fixture-weights').write_bytes(b'DUMMY-NOT-ACT-WEIGHTS')
    weights={'fixture-weights':hashlib.sha256((checkpoint/'fixture-weights').read_bytes()).hexdigest()}
    lineage={'skill_id':corpus.skill_id,'skill_version':corpus.version,'spec_sha256':corpus.manifest['skill_spec_sha256'],'aggregate_sha256':corpus.sha256}
    model={'schema':'explorer_rosmaster_skill_model_v1','action_contract_id':ACTION_CONTRACT_ID,'action_names':list(ACTION_NAMES),
        'skill_id':corpus.skill_id,'skill_version':corpus.version,'skill_spec_sha256':lineage['spec_sha256'],
        'ACT9':{'state_dimension':106,'output_dimension':9},'weights_sha256':weights,'aggregate_sha256':corpus.sha256,
        'source_kind':['physical'],'fixture_notice':'entirely synthetic source; not actual physical evidence',
        'preprocessing':{'image_size':32,'normalizer':corpus.normalizer.document()},
        'train_original_episode_ids':[row['descriptor']['source_episode_id'] for row in corpus.train],
        'development_original_episode_ids':[row['descriptor']['source_episode_id'] for row in corpus.development]}
    manifest=folder/'model_manifest.json';manifest.write_bytes(canonical(model))
    job=store.create_job('rosmaster_skill_train','02',{'skill_workflow':lineage})
    result={'trained':True,'weights_changed':True,'save_reload_passed':True,'optimizer_steps':8,'device':'cpu',
        'feature_dimension':106,'output_action_dimension':9,'model_manifest':str(manifest),'model_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'checkpoint_dir':str(checkpoint),'checkpoint_sha256':weights,'source_manifest_path':str(aggregate),'source_manifest_sha256':corpus.sha256}
    store.complete_job(job['id'],result)
    return store,store.job(job['id']),manifest,model,corpus


def test_controlplane_import_has_no_optional_model_or_gpu_modules():
    root=Path(__file__).parents[1]
    code="import sys;sys.path[:0]="+repr([str(root/'shared'),str(root/'00-control-center/src')])+";from explorer.rosmaster_learning import model_versions;assert not any(x in sys.modules for x in ('torch','mujoco','lerobot','cv2'))"
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True)


def test_saved_versions_exact_hashes_and_queue_dedup_no_model_load(tmp_path):
    before=set(sys.modules);store,trained,path,model,corpus=model_fixture(tmp_path)
    versions=model_versions(store,corpus.skill_id,1)
    assert versions['models'][0]['model_job_id']==trained['id'] and versions['models'][0]['compatible_native9_106']
    assert not versions['model_loaded'] and versions['network_requests_sent']==0
    reference=model_reference(store,corpus.skill_id,1,trained['id']);assert reference['state_dimension']==106 and reference['action_dimension']==9
    first=enqueue_evaluation(store,corpus.skill_id,1,trained['id']);second=enqueue_evaluation(store,corpus.skill_id,1,trained['id'])
    assert first['id']==second['id'] and first['config']['partition']=='development' and first['project_id']=='02'
    with store.connect() as db:
        row=db.execute('SELECT * FROM experiment_runtime_identity WHERE entity_kind=? AND entity_id=?',('job',first['id'])).fetchone()
        assert row is not None
    assert 'torch' not in set(sys.modules)-before
    shadow=enqueue_shadow(store,corpus.skill_id,1,trained['id'])
    assert shadow['config']['skill_reference']==reference and shadow['config']['execution_scope']=='shadow_without_dispatch'


@pytest.mark.parametrize('mutation',['state9','contract','foreign_skill','weights','aggregate'])
def test_saved_reference_rejects_self_consistent_metadata_tamper_or_changed_originals(tmp_path,mutation):
    store,trained,path,model,corpus=model_fixture(tmp_path)
    if mutation=='state9':model['ACT9']['state_dimension']=9
    elif mutation=='contract':model['action_contract_id']='old_arm_only_contract'
    elif mutation=='foreign_skill':model['skill_id']='foreign'
    elif mutation=='weights':(Path(trained['result']['checkpoint_dir'])/'fixture-weights').write_bytes(b'changed')
    else:Path(trained['result']['source_manifest_path']).write_bytes(b'changed aggregate')
    if mutation in ('state9','contract','foreign_skill'):
        path.write_bytes(canonical(model));result=trained['result'];result['model_manifest_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        with store.connect() as db:db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result),trained['id']))
    with pytest.raises(ValueError):evaluation_request(store,corpus.skill_id,1,trained['id'])


def test_eval_only_saved_dev_uses_original_train_stats_no_optimizer_network_or_sealed_data(tmp_path):
    store,trained,path,model,corpus=model_fixture(tmp_path);job=enqueue_evaluation(store,corpus.skill_id,1,trained['id']);events=[]
    def loader(path):events.append('load');return SimpleNamespace(policy=SimpleNamespace(config=SimpleNamespace(chunk_size=2)))
    def evaluator(policy,loaded,chunks,guard):
        events.append('evaluate');guard()
        assert loaded.normalizer.document()==model['preprocessing']['normalizer']
        assert {episode['descriptor']['split'] for episode in chunks.episodes}=={'development'}
        assert not {episode['descriptor']['source_episode_id'] for episode in chunks.episodes}&set(loaded.sealed_ids)
        return {'sealed_test_used':False,'learned_policy_rollout_executed':False,'fixture_evaluator':True,'action_dimension':9}
    result=run_rosmaster_evaluation(store,job,loader=loader,evaluator=evaluator)
    assert events==['load','evaluate'] and result['weights_unchanged'] and result['optimizer_steps_executed']==0
    assert result['physical_control_requests_sent']==result['source_network_requests_sent']==0 and not result['dispatch_performed']
    assert not result['independent_full_task_success'] and result['evaluated_original_episode_ids']==model['development_original_episode_ids']
    assert hashlib.sha256(Path(result['evaluation_artifact']).read_bytes()).hexdigest()==result['evaluation_artifact_sha256']


def test_cancel_before_load_and_after_evaluation_preserves_models_without_result_publication(tmp_path):
    store,trained,path,model,corpus=model_fixture(tmp_path);job=enqueue_evaluation(store,corpus.skill_id,1,trained['id'])
    store.update_job(job['id'],status='cancel_requested')
    def forbidden(*args,**kwargs):raise AssertionError('Cancelled evaluation must not load model')
    with pytest.raises(RuntimeError,match='cancelled'):run_rosmaster_evaluation(store,job,loader=forbidden)
    # Independent next request; cancellation belongs to each durable job.
    store.complete_job(job['id'],{})
    job=enqueue_evaluation(store,corpus.skill_id,1,trained['id'])
    def evaluator(policy,loaded,chunks,guard):
        store.update_job(job['id'],status='cancel_requested');return {'sealed_test_used':False,'learned_policy_rollout_executed':False}
    with pytest.raises(RuntimeError,match='cancelled'):
        run_rosmaster_evaluation(store,job,loader=lambda path:SimpleNamespace(policy=SimpleNamespace(config=SimpleNamespace(chunk_size=2))),evaluator=evaluator)
    assert not (store.root/'jobs'/job['id']/'offline-evaluation.json').exists()
