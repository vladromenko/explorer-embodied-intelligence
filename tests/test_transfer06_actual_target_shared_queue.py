"""Opt-in ACT9 target-only/adapt queue proof, GENERATED target originals only.

Reuses a retained actual ACT6 checkpoint from its original isolated Store. Never
copies/relabels scientific originals, injects production experts or opens devices.
"""
import json
import os
from pathlib import Path
import uuid

import pytest

from explorer.store import Store
from explorer import transfer_geometry as geometry,transfer_calibration as wizard,transfer_target as target
from explorer.queue_policy import GIB
from explorer.scheduler import Supervisor
from native02_queue_helpers import freeze_source,private_config,QueueTrace,pump,stop_fixture_workers,sha
from test_native02_queued_learning import SOURCES
from test_transfer_target_corpus import generated_target_corpus

ROOT=Path(__file__).parents[1]


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_TRANSFER06_TARGET_QUEUE')!='1',reason='Separate bounded ACT9 source-feature/target-only queue qualifier')
def test_actual_shared_ACT9_queue_target_only_and_visual_backbone_adapt(tmp_path,monkeypatch):
    root=Path(os.environ['EXPLORER_TRANSFER06_SOURCE_STATE']).resolve()
    assert root.is_relative_to(Path('/mnt/explorer-data/explorer-work'))
    assert not root.is_relative_to((Path.home()/'.local/share/explorer/state').resolve())
    store=Store(root);ident=os.environ['EXPLORER_TRANSFER06_SOURCE_MODEL']
    assert not [j for j in store.queue_jobs() if j['status'] not in ('completed','failed','cancelled')]
    model=geometry.admitted_model(store,ident);assert model['backend']=='act' and model['source_domain']=='SIMULATION'
    prepared,_=geometry.build_geometry(model,geometry.saved_simulation_calibration(model))
    source_originals={str(Path(model['checkpoint_path'])/name):expected for name,expected in model['checkpoint_sha256'].items()}
    source_originals[str(root/'jobs'/ident/'result.json')]=model['training_receipt_sha256']
    state=wizard.create_session(store,dict(skill_id=prepared['source_model_binding']['skill_id'],
        source_version=1,target_version=2,domain='simulation',request_id=str(uuid.uuid4())),
        audit=prepared['audit'],source_model_binding=prepared['source_model_binding'],authenticated_actor='OWN06_GENERATED_QUEUE')
    path,_,originals=generated_target_corpus(root/'datasets'/('GENERATED-transfer-target-'+uuid.uuid4().hex),
        train_count=25,source_binding=state['source_model_binding'],source_spec=model['manifest']['skill_spec'])
    dataset=store.create_job(target.DATASET_KIND,'06',dict(inputs='GENERATED_SOFTWARE_NOT_FINITE_ROLLOUT',real_experts=0))
    store.complete_job(dataset['id'],dict(dataset_manifest=str(path),dataset_manifest_sha256=sha(path)))
    release,frozen=freeze_source(ROOT,tmp_path/'frozen',SOURCES+[
        '00-control-center/src/explorer/transfer_target.py','shared/explorer_shared/learning/transfer_target_corpus.py',
        'shared/explorer_shared/learning/transfer_target_training.py'])
    config=private_config(tmp_path/'private-config.json',store.root);monkeypatch.setenv('EXPLORER_CONFIG',str(config))
    trace=QueueTrace(store);supervisor=Supervisor({'root':str(release)},store,quantum_s=3600,popen=trace.popen)
    results=[]
    try:
        for mode in ('target_only','vision_adapt'):
            job=target.enqueue_training(store,state['wizard_id'],dataset['id'],dict(mode=mode,demonstrations=5,
                act_steps=2,seed=41,request_id=str(uuid.uuid4())),actor='OWN06_GENERATED_QUEUE')
            pump(supervisor,trace,lambda:store.job(job['id'])['status'] in ('completed','failed','cancelled') and
                 job['id'] not in supervisor.processes,timeout=360,required_job_id=job['id'])
            completed=store.job(job['id']);assert completed['status']=='completed',completed
            result=completed['result'];assert result['optimizer_steps']==2 and result['weights_changed']
            assert result['save_reload_passed'] and result['reload_max_abs_error']<=1e-6
            assert result['feature_dimension']==106 and result['output_action_dimension']==9
            assert result['official_dataset']['original_train_demo_count']==5
            assert not result['physical_io'] and not result['learned_rollout_executed']
            saved=json.loads(Path(result['model_manifest']).read_bytes())
            assert saved['compatible_source_projects']==['06'] and not saved['whole_skill_mastered']
            assert saved['source_kind']==['simulation'] and not saved['physical_execution_qualified']
            initialization=saved['transfer_initialization']
            assert bool(initialization['transferred_tensor_names'])==(mode=='vision_adapt')
            assert not initialization['source_action_head_copied'] and not initialization['source_optimizer_copied']
            assert saved['transfer_target_selection']==result['target_selection']
            budget=trace.admitted[job['id']]['metadata']['resources']
            assert budget['memory_bytes']==4*GIB and budget['cpu_cores']==2 and budget['gpu_slots']==0
            results.append(dict(job_id=job['id'],mode=mode,result=result,model=saved,worker_resources=budget))
        assert results[0]['result']['source_manifest_sha256']==results[1]['result']['source_manifest_sha256']
        assert all(sha(name)==expected for name,expected in dict(originals,**source_originals).items())
        receipt=dict(schema='explorer06_actual_generated_target_shared_queue_v1',source_commit=frozen['source_commit'],
            source_model_job_id=ident,source_training_originals_unchanged=True,target_originals_unchanged=True,
            inputs='GENERATED_SOFTWARE_NOT_REAL_EXPERTS_OR_FINITE_PHYSICS',real_experts=0,
            actual_native_commands=0,actual_native_grants=0,source_object_mastery=False,target_object_mastery=False,
            target_training_performed=True,physical_acceptance=False,results=results,workers=trace.public())
        (tmp_path/'qualification.json').write_text(json.dumps(receipt,indent=2)+'\n')
    finally:stop_fixture_workers(supervisor)
