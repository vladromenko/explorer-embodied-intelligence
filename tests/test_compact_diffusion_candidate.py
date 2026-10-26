"""Offline source-contract tests, not a physical skill or performance benchmark."""
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

from explorer_shared.learning.diffusion_backend import compact_profile,make_compact_diffusion_policy
from explorer_shared.learning import backend_catalogue,get_backend,UnqualifiedBackend


def qualification():
    path=Path(__file__).parents[1]/'scripts/qualify_diffusion.py';spec=importlib.util.spec_from_file_location('diffusion_qualification_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_factory_is_a_small_explicit_CPU_candidate_not_an_executable_general_backend():
    profile=compact_profile();config=profile['configuration']
    assert profile['status']=='QUALIFICATION_CANDIDATE' and not profile['production_dataset_admission'] and not profile['optimizer_resume_qualified']
    assert config['down_dims']==(32,64) and config['num_inference_steps']==4 and config['n_obs_steps']==2 and config['horizon']==8
    assert config['pretrained_backbone_weights'] is None and not config['push_to_hub']
    with pytest.raises(UnqualifiedBackend):get_backend('diffusion')
    with pytest.raises(ValueError,match='CPU qualification'):make_compact_diffusion_policy(dict(device='cuda'),state_dim=9,action_dim=9)


def test_registry_receipt_qualifies_only_actual_9D_CPU_profile_and_never_06_or_general_training():
    descriptor=next(row for row in backend_catalogue()['backends'] if row['id']=='diffusion')
    assert descriptor['status']=='UNQUALIFIED'
    profile=descriptor['qualified_profiles'][0]
    assert profile['device']=='cpu' and profile['state_dimension']==profile['action_dimension']==9 and profile['image_size']==64
    assert not profile['production_queue_ready'] and not profile['physical_execution_authorized']


def test_fixture_retains_all9_native_neutral_actions_and_separate_original_scenes(tmp_path):
    module=qualification();manifest=module.create_source(tmp_path/'originals')
    assert manifest['source_kind']=='SIMULATION' and len(manifest['action_names'])==9
    assert manifest['episodes'][0]['scene']!=manifest['episodes'][1]['scene']
    for descriptor in manifest['episodes']:
        with np.load(tmp_path/'originals'/descriptor['file'],allow_pickle=False) as source:
            assert source['rgb'].shape==(16,64,64,3) and source['state'].shape==source['action'].shape==(16,9)
            assert (source['action_native'][::2,6:]==0).all() and source['valid_mask'].all()
            assert (source['frame_monotonic_ns']<=source['state_monotonic_ns']).all() and (source['state_monotonic_ns']<source['action_monotonic_ns']).all()


def test_actual_observation_history_and_horizon_offsets_use_original_samples_without_future_observations():
    data={'rgb':np.arange(16*64*64*3,dtype=np.uint8).reshape(16,64,64,3),
        'state':np.tile(np.arange(16,dtype=np.float32)[:,None],(1,9)),'action':np.tile(np.arange(16,dtype=np.float32)[:,None],(1,9))}
    module=qualification();sample=module.row(data,4)
    assert sample['observation.state'][:,0].tolist()==[3,4]
    assert sample['action'][:,0].tolist()==list(range(3,11))
    assert sample['action'][1:5,0].tolist()==[4,5,6,7] and not sample['action_is_pad'].any()
    with pytest.raises(ValueError,match='unpadded'):module.row(data,0)
    with pytest.raises(ValueError,match='unpadded'):module.row(data,12)


def test_qualification_refuses_unbounded_or_unrelated_process_before_model_allocation():
    module=qualification()
    with pytest.raises(ValueError,match='own explorer-diffusion-qual'):module.cgroup_limits()
