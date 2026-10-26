"""Full-object BC+PPO label provenance and maintained actor optimization."""
import hashlib
import os
from pathlib import Path

import gymnasium as gym
import numpy as np
import pytest
import torch
from stable_baselines3 import PPO

from explorer_sim.manipulation_curriculum import (
    StandardizedTeacherFeatures, collect_expert, load_training_traces,
    normalized_dispatch_target, parameters, phase_weights, run_manipulation_curriculum, warmstart_actor,
)
from explorer_sim.manipulation_env import ManipulationEnv


def test_supervision_uses_applied_endpoint_delta_not_an_unbounded_request():
    velocity = np.full(6,20.)
    before = np.full(6,90.)
    raw_requested = before+4*velocity*.05
    dispatched = before+velocity*.05
    np.testing.assert_allclose(normalized_dispatch_target(before,dispatched,velocity,.05),1.)
    assert not np.allclose((raw_requested-before)/(velocity*.05),1.)
    with pytest.raises(ValueError,match='rate contract'):
        normalized_dispatch_target(before,raw_requested,velocity,.05)


def test_phase_balancing_does_not_drown_rare_release_in_long_approach():
    phases = np.array(['approach']*100+['grasp']*20+['release']*2)
    weights = phase_weights(phases)
    for phase in np.unique(phases):
        assert weights[phases==phase].sum() == pytest.approx(1/3)
    for invalid in ([], ['approach',''], [[1,2]]):
        with pytest.raises(ValueError):phase_weights(invalid)


def test_training_trace_cannot_read_development_seed_failed_expert_or_modified_data(tmp_path):
    archive = tmp_path/'trace.npz'
    np.savez_compressed(archive,privileged_observation36=np.zeros((3,36),np.float32),
        actual_dispatched_action6=np.zeros((3,6),np.float32),phase=np.array(['approach','grasp','release']))
    descriptor = dict(seed=41,relative_file=archive.name,sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),evaluator=dict(success=True))
    with pytest.raises(ValueError,match='Development'):
        load_training_traces(tmp_path,[descriptor],[41])
    with pytest.raises(ValueError,match='No independently complete'):
        load_training_traces(tmp_path,[dict(descriptor,evaluator=dict(success=False))],[1041])
    observations,targets,phases,admitted = load_training_traces(tmp_path,[descriptor],[1041])
    assert observations.shape == (3,36) and targets.shape == (3,6) and admitted == [41]
    archive.write_bytes(b'overwritten-original-must-not-enter-BC')
    with pytest.raises(ValueError,match='hash'):
        load_training_traces(tmp_path,[descriptor],[1041])


class SmallEnv(gym.Env):
    def __init__(self):
        self.observation_space = gym.spaces.Box(-np.inf,np.inf,(36,),np.float32)
        self.action_space = gym.spaces.Box(-1,1,(6,),np.float32)
    def reset(self,seed=None,options=None):
        super().reset(seed=seed)
        return np.zeros(36,np.float32),{}
    def step(self,action):
        return np.zeros(36,np.float32),0.,False,False,{}


def test_real_actor_warmstart_changes_actor_not_critic_and_reduces_unseen_target_error():
    torch.set_num_threads(1)
    torch.manual_seed(71)
    policy = PPO('MlpPolicy',SmallEnv(),n_steps=8,batch_size=8,seed=71,
        policy_kwargs=dict(features_extractor_class=StandardizedTeacherFeatures,
            features_extractor_kwargs=dict(mean=[0]*36,std=[1]*36)),device='cpu')
    rng = np.random.default_rng(71)
    inputs = rng.normal(0,.3,(256,36)).astype(np.float32)
    targets = .2*inputs[:,:6]+.1
    unseen = torch.tensor(rng.normal(0,.3,(64,36)),dtype=torch.float32)
    unseen_target = .2*unseen[:,:6]+.1
    with torch.no_grad():
        before = torch.mean((policy.policy.get_distribution(unseen).distribution.mean-unseen_target)**2).item()
    result = warmstart_actor(policy.policy,inputs,targets,['approach']*240+['release']*16,steps=180,seed=71,batch_size=64)
    with torch.no_grad():
        after = torch.mean((policy.policy.get_distribution(unseen).distribution.mean-unseen_target)**2).item()
    assert after < before*.3
    assert result['optimizer_steps'] == 180 and result['changed_parameter_names']
    assert not result['critic_parameters_changed']
    assert all('policy_net' in name or 'action_net' in name for name in result['changed_parameter_names'])


def test_invalid_budgets_and_job_identity_are_rejected_without_artifacts(tmp_path):
    from types import SimpleNamespace
    for config in ({'ppo_steps':True},{'task':'cookie_delivery'},{'device':'cuda'},{'demonstration_episodes':0}):
        with pytest.raises(ValueError):parameters(config)
    with pytest.raises(ValueError,match='identity'):
        run_manipulation_curriculum(SimpleNamespace(root=tmp_path),dict(id='../escape',config={}))
    assert not list(tmp_path.iterdir())


def test_normalization_rejects_missing_or_nonfinite_training_statistics():
    space = SmallEnv().observation_space
    for mean,std in (([0]*35,[1]*36),([0]*36,[0]*36),([np.nan]*36,[1]*36)):
        with pytest.raises(ValueError):StandardizedTeacherFeatures(space,mean,std)


@pytest.fixture
def meshes():
    value = os.environ.get('EXPLORER_SIM_MESH_DIR')
    if not value:pytest.skip('Checksum-qualified CAD assets explicitly required')
    return value


def test_actual_contact_expert_targets_reconstruct_dispatched_deltas_and_full_outcome(meshes,tmp_path):
    env = ManipulationEnv(meshes,'candy_to_tray')
    try:
        result = collect_expert(env,tmp_path/'expert',8421,record_rgb=False)
        with np.load(tmp_path/'expert/expert-trace.npz',allow_pickle=False) as data:
            assert data['privileged_observation36'].shape == (result['steps'],36)
            assert data['actual_dispatched_action6'].shape == (result['steps'],6)
            assert len(data['native_integration_state']) == result['steps']+1
            assert np.max(np.abs(data['actual_dispatched_action6'])) <= 1
            assert {'grasped','lifted','carried_above_target','success'}.issubset(result['evaluator'])
            assert 'release' in set(data['phase']) and result['evaluator']['success']
            assert len(data['rgb']) == 0 and result['frames'] == 0
    finally:env.close()
