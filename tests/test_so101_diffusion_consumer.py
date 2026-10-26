"""Explicit source-role integration qualification; no hardware or station IO."""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from explorer_shared.learning.diffusion_backend import PROFILE_ID
from explorer_shared.learning.diffusion_policy import NativeDiffusionPolicy
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH


def test_typed_wrong_role_backend_and_profile_are_rejected_before_source_or_model_allocation():
    experiments=pytest.importorskip('explorer_shared.experiments')
    from explorer.so101_diffusion_training import run_so101_diffusion_train
    with pytest.raises(ValueError,match='semantic source-role'):run_so101_diffusion_train(None,dict(kind='so101_skill_train',project_id=experiments.experiment_id('cross_embodiment_transfer'),config={'backend':'diffusion'}))
    with pytest.raises(ValueError,match='qualified compact CPU'):run_so101_diffusion_train(None,dict(kind='so101_skill_train',project_id=experiments.experiment_id('so101_source_learning'),config={'backend':'diffusion','device':'cuda','profile_id':PROFILE_ID}))


def test_registry_requires_exact_source_contract_and_shapes_without_loading_candidate_as_default():
    from explorer_shared.learning import get_backend,UnqualifiedBackend
    with pytest.raises(UnqualifiedBackend):get_backend('diffusion')
    with pytest.raises(UnqualifiedBackend):get_backend('diffusion',profile_id=PROFILE_ID,source_contract_id='rosmaster_joint9_position6_velocity3_v1',state_dim=6,action_dim=6)
    backend=get_backend('diffusion',profile_id=PROFILE_ID,source_contract_id='so101_follower_calibrated_position6_v1',state_dim=6,action_dim=6)
    assert backend.id=='diffusion'
    with pytest.raises(UnqualifiedBackend):backend.create_policy({'device':'cuda','image_size':64},state_dim=6,action_dim=6)
    with pytest.raises(UnqualifiedBackend):backend.create_policy({'device':'cpu','image_size':64},state_dim=9,action_dim=9)


def test_unadmitted_raw_archive_cannot_make_itself_an_expert_by_setting_manifest_flags(tmp_path):
    from explorer.store import Store
    from explorer.so101_diffusion_training import _aggregate
    from explorer_shared.contracts import digest
    store=Store(tmp_path/'state');spec={'skill_id':'synthetic-unadmitted','version':1,'action_contract':{'id':'so101_follower_calibrated_position6_v1'}}
    document=dict(schema='explorer_so101_skill_dataset_v1',action_contract_id='so101_follower_calibrated_position6_v1',action_names=list(NAMES),action_units=UNITS,
        skill_spec=spec,skill_id=spec['skill_id'],skill_version=1,spec_sha256=digest(spec),source_files_sha256={},
        receipt=dict(full_teaching_provenance_ready=True,object_expert_eligible=True))
    path=store.root/'unadmitted.json';path.write_text(json.dumps(document));config=dict(aggregate_manifest=str(path),aggregate_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),skill_id=spec['skill_id'],skill_version=1,spec_sha256=digest(spec))
    with pytest.raises(ValueError,match='Server-bound'): _aggregate(store,config)


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_SO101_DIFFUSION_QUAL')!='1',reason='One explicit isolated native6 optimizer+restart qualification, not repeated broad-suite work')
def test_same_accepted_whole_skill_official_dataset_DP6_restart_saved_native_loader_and_SIMULATION_card(tmp_path,monkeypatch):
    experiments=pytest.importorskip('explorer_shared.experiments')
    from explorer import lab_skill_workflow as flow
    from explorer.so101_diffusion_training import run_so101_diffusion_train
    from test_lab_skill_workflow import full_cohort
    store,chosen,sources=full_cohort(tmp_path);spec=chosen['spec'];aggregate=flow.build_aggregate(store,spec['skill_id'],spec['version'])
    source_bytes={str(path):path.read_bytes() for _,manifest,*_ in sources for path in manifest.parent.iterdir() if path.is_file()}
    job=store.create_job('so101_skill_train',experiments.experiment_id('so101_source_learning'),dict(backend='diffusion',profile_id=PROFILE_ID,device='cpu',optimizer_steps=8,batch_size=2,image_size=64,seed=41,
        aggregate_manifest=aggregate['aggregate_manifest'],aggregate_manifest_sha256=aggregate['aggregate_manifest_sha256'],skill_id=spec['skill_id'],skill_version=spec['version'],spec_sha256=chosen['spec_sha256']))
    store.update_job(job['id'],status='running');folder=store.root/'jobs'/job['id'];original_update=store.update_job
    def interrupted_update(job_id,**values):
        original_update(job_id,**values)
        if (values.get('result') or {}).get('phase')=='optimizing_official_lerobot_diffusion' and json.loads((folder/'diffusion_resume/latest.json').read_text())['completed_steps']==4:
            raise InterruptedError('isolated worker interruption after durable4')
    with monkeypatch.context() as patch:
        patch.setattr(store,'update_job',interrupted_update)
        with pytest.raises(InterruptedError,match='durable4'):run_so101_diffusion_train(store,store.job(job['id']))
    assert json.loads((folder/'diffusion_resume/latest.json').read_text())['completed_steps']==4
    result=run_so101_diffusion_train(store,store.job(job['id']));store.complete_job(job['id'],result)
    assert result['optimizer_steps']==8 and result['training_resume']['resumed_from_step']==4 and result['training_resume']['optimizer_steps_executed_this_process']==4
    assert result['backend']=='diffusion' and result['source_kind']=='SIMULATION' and result['weights_changed'] and result['save_reload_passed'] and result['reload_max_abs_error']==0
    assert result['RGB_encoder_first_conv_gradient_norm']>0 and result['evaluation']['independent_episode_split'] and not result['evaluation']['rollout_success_claimed']
    assert result['official_dataset_readback']['official_readback_passed'] and not result['sealed_payload_used'] and len(result['training_original_episode_ids'])==3
    assert result['project_id']==experiments.experiment_id('so101_source_learning') and not result['physical_io'] and not result['simulator_executed']
    card=next(row for row in store.skills() if row['id']==result['skill_card']['id'])
    assert card['deployment_scope']=='SIMULATION_ONLY' and card['source_type']=='SIMULATION' and card['lineage']['backend']=='diffusion'
    manifest_path=Path(aggregate['aggregate_manifest']);outer=json.loads(manifest_path.read_text());internal=manifest_path.parent/outer['internal_view'];view=json.loads(internal.read_text())
    heldout=next(row for row in view['episodes'] if row['split']=='development')
    with np.load(internal.parent/heldout['file'],allow_pickle=False) as source:rgb=source['rgb'][:2].copy();state=source['state_native'][:2].copy()
    adapter=NativeDiffusionPolicy(result['checkpoint_dir'],result['checkpoint_sha256'],expected_contract_id=result['action_contract_id'],expected_names=NAMES,expected_units=UNITS,expected_low=LOW,expected_high=HIGH)
    commands=adapter.predict_history(rgb,state,noise_seed=41,valid_mask=np.ones((2,6),bool))
    assert commands.shape==(4,6) and np.isfinite(commands).all() and (commands>=LOW).all() and (commands<=HIGH).all() and not adapter.physical_io
    with pytest.raises(ValueError,match='Unavailable'):adapter.predict_history(rgb,state,valid_mask=np.zeros((2,6),bool))
    model=Path(result['checkpoint_dir'])/'config.json';original=model.read_bytes();model.write_bytes(original+b' ')
    with pytest.raises(ValueError,match='checksum'):NativeDiffusionPolicy(result['checkpoint_dir'],result['checkpoint_sha256'],expected_contract_id=result['action_contract_id'],expected_names=NAMES,expected_units=UNITS,expected_low=LOW,expected_high=HIGH)
    model.write_bytes(original)
    assert all(Path(path).read_bytes()==raw for path,raw in source_bytes.items())
    assert run_so101_diffusion_train(store,store.job(job['id']))==result
    (tmp_path/'qualification.json').write_text(json.dumps(result,indent=2))
