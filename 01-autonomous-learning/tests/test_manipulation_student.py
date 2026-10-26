"""Full-object visual actor input causality and actual optional ACT/MuJoCo cycle."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from explorer_sim.manipulation_env import ManipulationEnv
from explorer_sim.manipulation_student import (actor_inputs, parameters, record_visual_rollout,
                                             run_manipulation_student, _complete)
from explorer_sim.recorded_training import RecordedChunks
from explorer_sim.pilot import ACTION_NAMES, LOW, HIGH, sha
from explorer_sim.student import IMAGE_KEY


def test_visual_actor_accepts_only_rgb_and_preceding_named_command_estimate():
    rgb = np.full((240, 320, 3), 90, np.uint8)
    command = np.array([90, 90, 90, 90, 90, 30, 0, 0, 0], np.float32)
    inputs = actor_inputs(rgb, command, 64)
    assert set(inputs) == {IMAGE_KEY, 'observation.state'}
    assert inputs[IMAGE_KEY].shape == (3, 64, 64)
    np.testing.assert_allclose(inputs['observation.state'].numpy(), 2*(command-LOW)/(HIGH-LOW)-1)
    with pytest.raises(ValueError, match='command9'):
        actor_inputs(rgb, np.zeros(36), 64)  # privileged PPO observation cannot cross the boundary.
    with pytest.raises(ValueError, match='RGB'):
        actor_inputs(rgb.astype(float), command, 64)
    with pytest.raises(ValueError, match='command9'):
        actor_inputs(rgb, np.r_[np.nan, command[1:]], 64)


@pytest.mark.parametrize('config', [{'horizon': True}, {'act_steps': 0}, {'device': 'mps'},
                                     {'task': 'cookie_delivery'}, {'evaluation_episodes': -1}])
def test_invalid_or_unqualified_student_config_is_rejected(config):
    with pytest.raises(ValueError):
        parameters(config)


def test_visual_dataset_rejects_future_command_as_pre_action_state(tmp_path):
    commands = np.array([[90, 90, 90, 90, 90, 30, 0, 0, 0],
                         [90, 90, 90, 90, 90, 31, 0, 0, 0]], np.float32)
    states = commands.copy()
    actions = commands.copy(); actions[:, 5] += 1
    rgb = np.full((2, 64, 64, 3), 60, np.uint8)
    source = tmp_path / 'episode.npz'
    np.savez_compressed(source, rgb=rgb, state_native=states, state=2*(states-LOW)/(HIGH-LOW)-1, action_native=actions)
    descriptor = dict(id='causal', file=source.name, sha256=sha(source), split='train')
    manifest = dict(episodes=[descriptor], action_names=ACTION_NAMES,
        action_normalization={'low': LOW.tolist(), 'high': HIGH.tolist()}, state_basis='command_estimate')
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(manifest))
    samples = RecordedChunks(path, manifest, [descriptor], 4, 64)
    np.testing.assert_allclose(samples[1]['observation.state'].numpy(), 2*(states[1]-LOW)/(HIGH-LOW)-1)
    # Same image paired with its just-requested future endpoint must not become observation.
    states = actions.copy()
    np.savez_compressed(source, rgb=rgb, state_native=states, state=2*(states-LOW)/(HIGH-LOW)-1, action_native=actions)
    descriptor['sha256'] = sha(source); path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Causal mismatch'):
        RecordedChunks(path, manifest, [descriptor], 4, 64)


@pytest.mark.skipif(not os.environ.get('EXPLORER_SIM_MESH_DIR'), reason='Actual verified CAD assets required')
def test_closed_loop_actor_observes_no_object_truth_and_keeps_failed_trial(tmp_path):
    env = ManipulationEnv(os.environ['EXPLORER_SIM_MESH_DIR'], 'candy_to_tray', horizon=32)
    class OnlyRGBCommandActor:
        def __init__(self): self.calls = []
        def reset(self): pass
        def predict(self, rgb, command9):
            assert rgb.dtype == np.uint8 and rgb.shape == (240, 320, 3)
            assert command9.shape == (9,)
            self.calls.append(command9.copy())
            return command9.copy(), command9[:6].copy()
    actor = OnlyRGBCommandActor()
    try:
        result = record_visual_rollout(env, tmp_path/'failed-independent-trial', 531091, actor)
        assert result['n_actions'] == len(actor.calls) == 32
        assert not result['evaluator']['success']
        assert not result['privileged_truth_in_actor'] and not result['scripted_actions_during_student']
        with np.load(tmp_path/'failed-independent-trial/trajectory.npz') as raw:
            assert raw['state_command9'].shape == (32, 9)
            assert len(raw['native_integration_state']) == 33
            assert len(raw['substep_contact_trace']) == 800
            assert raw['rgb'].shape == (32, 240, 320, 3)
            np.testing.assert_allclose(raw['state_command9'][1:, :6], raw['dispatched_endpoint6'][:-1])
    finally:
        env.close()


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_MANIPULATION_STUDENT_INTEGRATION') != '1',
                    reason='Explicit real complete contact demonstrations, ACT optimizer and independent rollout')
def test_actual_official_act_full_object_development_cycle(tmp_path):
    class Store:
        root = tmp_path
        cards = []
        def update_job(self, *args, **kwargs): pass
        def skills(self): return self.cards
        def add_skill(self, card): self.cards.append(card)
    result = run_manipulation_student(Store(), dict(id='visual-cycle', config=dict(task='candy_to_tray', seed=321041,
        demonstration_episodes=1, act_steps=8, batch_size=2, chunk_size=4, evaluation_episodes=1, horizon=32,
        device=os.environ.get('EXPLORER_MANIPULATION_STUDENT_TEST_DEVICE','cpu'))))
    assert result['trained'] and result['student']['optimizer_steps'] == 8
    assert result['student']['weights_changed'] and result['student']['save_reload_passed']
    assert result['student']['official_saved_processors_executed']
    assert result['student']['device'] == os.environ.get('EXPLORER_MANIPULATION_STUDENT_TEST_DEVICE','cpu')
    if result['student']['device']=='cuda':
        assert result['student']['parameter_device'].startswith('cuda') and result['student']['TF32_enabled'] is False
    assert result['scripted_baseline']['successes'] == 1
    assert result['evaluation']['n'] == 1 and result['evaluation']['disjoint_training_seeds']
    assert result['evaluation']['episodes'][0]['native_replay']['within_1e_minus_9']
    assert len(Store.cards) == 1 and Store.cards[0]['status'] == 'trained'
    assert not Store.cards[0]['evaluation']['physical_run_ids']
    assert not result['physical_io'] and not result['full_object_skill_mastered']
    assert 'development/ACT-331041/result.json' in result['artifact_sha256']
    assert 'demonstrations/scripted-321041/result.json' in result['artifact_sha256']
    saved = run_manipulation_student(Store(), dict(id='visual-cycle', config=dict(task='candy_to_tray', seed=321041,
        demonstration_episodes=1, act_steps=8, batch_size=2, chunk_size=4, evaluation_episodes=1, horizon=32,
        device=os.environ.get('EXPLORER_MANIPULATION_STUDENT_TEST_DEVICE','cpu'))))
    assert saved['artifact_sha256'] == result['artifact_sha256']


def test_completed_run_checks_nested_evaluator_receipts_not_only_trace_arrays(tmp_path):
    receipt = tmp_path/'development/trial/result.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"success": false}')
    result = dict(provenance={'identity': 'signature'}, artifact_sha256={
        'development/trial/result.json': sha(receipt)})
    (tmp_path/'visual-manipulation-result.json').write_text(json.dumps(result))
    assert _complete(tmp_path, 'signature') == result
    receipt.write_text('{"success": true}')
    with pytest.raises(ValueError, match='checksum mismatch'):
        _complete(tmp_path, 'signature')


def test_zero_action_failed_source_is_retained_without_replay_or_expert_targets(tmp_path, monkeypatch):
    import explorer_sim.manipulation_student as module
    def failed_script(env, folder, seed, scripted):
        folder.mkdir(parents=True)
        np.savez_compressed(folder/'trajectory.npz', rgb=np.empty((0, 240, 320, 3), np.uint8))
        result = dict(seed=seed, n_actions=0, evaluator={'success': False},
            error='IK could not produce an initial request', trajectory_sha256=sha(folder/'trajectory.npz'))
        (folder/'result.json').write_text(json.dumps(result))
        return result
    def forbidden_replay(*args):
        raise AssertionError('A zero-action source has no initial command to replay')
    monkeypatch.setattr(module, 'record_rollout', failed_script)
    monkeypatch.setattr(module, 'replay_trajectory', forbidden_replay)
    monkeypatch.setattr(module, '_publish_frame', lambda *args, **kwargs: None)
    manifest, baseline = module._prepare_dataset(object(), tmp_path, parameters({'demonstration_episodes': 1}), lambda *args: None)
    assert not manifest['episodes'] and manifest['expert_source_count'] == 0
    assert len(baseline) == 1 and baseline[0]['error']
    assert baseline[0]['replay'] is None
    assert (Path(baseline[0]['source_directory'])/'result.json').is_file()
