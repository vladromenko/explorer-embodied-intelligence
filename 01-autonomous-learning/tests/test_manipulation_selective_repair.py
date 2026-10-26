"""Counterfactual query provenance, stage composition and heldout isolation."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import explorer_sim.manipulation_selective_repair as module
from explorer_sim.manipulation_selective_repair import (
    GatedPlacementPolicy, LABEL_SEMANTICS, collect_queries, feedback_placement_query,
    restore_learner_prefix, seed_plan, source_bundle, verify_recovery,
)
from explorer_sim.manipulation_env import ManipulationEnv
from explorer_sim.manipulation import record_rollout


def fake_teacher_state(monkeypatch,*,height=.26,grip=150,held=True,carried=True,inside=True):
    center = np.array([.22 if inside else .40,.19,height])
    points = np.array([center+[x,y,z] for x in (-.01,.01) for y in (-.01,.01) for z in (-.01,.01)])
    state = dict(carried_above_target=carried,final=dict(pad_force_N=[1.,1.] if held else [0.,0.]))
    env = SimpleNamespace(points=lambda:points.copy(),jaw_center=lambda:center+np.array([0,0,.01]),
        goal=np.array([.22,.19,0.]),half=np.array([.073,.063]),rim=.044,
        command=np.array([90.,90.,90.,90.,90.,grip]),velocity=np.array([48.,48.,48.,60.,60.,72.]),period=.05,
        evaluator=SimpleNamespace(result=lambda:state),speeds=lambda:np.zeros(len(points)),model=None,data=SimpleNamespace(time=3.))
    monkeypatch.setattr(module.mujoco,'MjData',lambda _:None)
    monkeypatch.setattr(module,'_geometric_jaw',lambda env,command,scratch:np.asarray(command[:3])*.001)
    return env


def test_carried_flag_without_current_hold_is_not_a_valid_expert_target(monkeypatch):
    env = fake_teacher_state(monkeypatch,held=False,inside=False)
    before = env.command.copy()
    result = feedback_placement_query(env)
    assert not result['valid'] and 'holding evidence' in result['reason']
    assert result['label_semantics'] == LABEL_SEMANTICS
    np.testing.assert_array_equal(env.command,before)


def test_high_object_and_partly_open_learner_gripper_cannot_trigger_premature_release(monkeypatch):
    env = fake_teacher_state(monkeypatch,height=.26,grip=150,held=True)
    before = env.command.copy()
    result = feedback_placement_query(env)
    assert result['valid'] and result['phase'] == 'feedback_lower'
    assert result['counterfactual_endpoint6'][5] > before[5]
    assert not result['counterfactual_was_dispatched']
    assert env.data.time == 3
    np.testing.assert_array_equal(env.command,before)


def test_release_requires_current_low_pose_and_stable_actual_object(monkeypatch):
    env = fake_teacher_state(monkeypatch,height=.065,grip=170,held=True)
    result = feedback_placement_query(env)
    assert result['valid'] and result['phase'] == 'feedback_release_settle'
    assert result['counterfactual_endpoint6'][5] < 170
    assert max(abs(np.asarray(result['counterfactual_action6']))) <= 1
    env.speeds = lambda:np.ones(8)
    moving = feedback_placement_query(env)
    assert moving['phase'] == 'feedback_lower' and moving['counterfactual_endpoint6'][5] == 170


def test_before_carried_stage_teacher_abstains_instead_of_inventing_grasp_labels(monkeypatch):
    env = fake_teacher_state(monkeypatch,carried=False)
    result = feedback_placement_query(env)
    assert not result['valid'] and 'counterfactual_action6' not in result


class Actor:
    def __init__(self,value):self.value=value;self.calls=0
    def predict(self,observation,deterministic=True):
        self.calls += 1
        return np.full(6,self.value),None


def test_learned_composition_uses_only_current_stage_and_preserves_raw_actor_output():
    original,placement = Actor(.25),Actor(2.)
    policy = GatedPlacementPolicy(original,placement)
    observation = np.zeros(36,np.float32)
    np.testing.assert_allclose(policy.predict(observation)[0],.25)
    assert original.calls == 1 and placement.calls == 0
    observation[35] = 1
    np.testing.assert_allclose(policy.predict(observation)[0],2.)
    assert original.calls == 1 and placement.calls == 1
    with pytest.raises(ValueError):policy.predict(np.zeros(35))


def test_source_train_and_development_states_cannot_enter_dagger_collection():
    source = dict(train_seeds=[8421,8422,8423],development_seeds=[18421,18422])
    options = dict(seed=13241,collection_episodes=2,evaluation_episodes=2)
    plan = seed_plan(options,source)
    assert not (set(plan['collection'])|set(plan['development']))&set(source['development_seeds'])
    with pytest.raises(ValueError,match='overlap'):
        seed_plan(dict(options,collection_seeds=[18421,18422]),source)
    with pytest.raises(ValueError,match='overlap'):
        seed_plan(dict(options,collection_seeds=[15241,15242],development_seeds=[15241,15242]),source)
    assert not plan['query_training_on_source_development']


def test_external_source_requires_explicit_matching_checksum(tmp_path):
    descriptor = tmp_path/'source.json';descriptor.write_text('{}')
    with pytest.raises(ValueError,match='SHA256'):
        source_bundle(SimpleNamespace(root=tmp_path),dict(source_result_path=str(descriptor),source_result_sha256='wrong'))
    with pytest.raises(ValueError):source_bundle(SimpleNamespace(root=tmp_path),{})


@pytest.fixture
def meshes():
    value = os.environ.get('EXPLORER_SIM_MESH_DIR')
    if not value:pytest.skip('Checksum-qualified CAD assets explicitly required')
    return value


def test_actual_query_replay_preserves_learner_dispatch_and_masks_unqualified_labels(meshes,tmp_path):
    env = ManipulationEnv(meshes,'candy_to_tray',horizon=24)
    try:
        policy = Actor(0.)
        outcome = record_rollout(env,tmp_path/'learner',61341,policy=policy)
        result = collect_queries(env,tmp_path/'learner',tmp_path/'queries',stride=4,teacher_qualified=False)
        assert result['teacher_query_native_state_parity']
        assert not result['teacher_queries_actuated_during_learner_rollout']
        assert result['valid_teacher_queries'] == 0
        with np.load(tmp_path/'queries/teacher-queries.npz',allow_pickle=False) as data:
            assert data['privileged_observation36'].shape == (24,36)
            assert not data['teacher_label_valid'].any()
            assert 'actual_learner_dispatch6' in data and 'counterfactual_teacher_action6' in data
        restoration = restore_learner_prefix(env,tmp_path/'learner',10)
        assert restoration['max_abs_native_state_error'] == 0
        archive = tmp_path/'learner/trajectory.npz';archive.write_bytes(b'changed-source')
        with pytest.raises(ValueError,match='checksum'):
            restore_learner_prefix(env,tmp_path/'learner',10)
    finally:env.close()


@pytest.mark.skipif(not os.environ.get('EXPLORER_BC_PLACEMENT_SOURCE'),reason='Explicit retained BC trace for actual held-object recovery')
def test_actual_closed_loop_teacher_recovers_a_displaced_held_object(meshes,tmp_path):
    source = Path(os.environ['EXPLORER_BC_PLACEMENT_SOURCE'])
    rows = json.loads((source/'substep-summary.json').read_text())
    index = next(row['step']+1 for row in rows if row['evaluator']['carried_above_target'] and min(row['evaluator']['final']['pad_force_N'])>.0001)
    env = ManipulationEnv(meshes,'candy_to_tray',horizon=1000)
    try:
        result = verify_recovery(env,source,index,tmp_path/'recovery',horizon=400)
        assert result['restoration']['max_abs_native_state_error'] == 0
        assert result['successful_recovery']
        assert result['final']['settled_seconds'] >= .5
        assert result['final']['final']['bottom_supported'] and result['final']['final']['free_of_fingers']
        with np.load(tmp_path/'recovery/recovery-demonstration.npz',allow_pickle=False) as data:
            assert data['privileged_observation36'].shape[0] == result['steps']
            assert data['executed_feedback_recovery_action6'].shape == (result['steps'],6)
    finally:env.close()


def test_free_high_object_inside_tray_projection_is_not_a_settling_expert_label(monkeypatch):
    env = fake_teacher_state(monkeypatch,height=.26,grip=100,held=False,inside=True)
    result = feedback_placement_query(env)
    assert not result['valid'] and 'counterfactual_action6' not in result
