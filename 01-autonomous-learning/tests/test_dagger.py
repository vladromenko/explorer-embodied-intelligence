"""DAgger semantic boundaries; no hardware, network or random demonstration mocks."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from explorer_sim.dagger import (DaggerSamples, LABEL, _archive_interrupted, _completed, rollout_student,
    artifact_hashes, model_identity, seed_plan, settings, source_bundle, teacher_label, verify_source_files)
from explorer_sim.dataset_export import load_episode
from explorer_sim.pilot import ACTION_NAMES, HIGH, HOME, LOW, sha, write_json
from explorer_sim.student import IMAGE_KEY


def record(role='teacher_query', success=False, split='imitation_train'):
    state = np.repeat(HOME[None], 2, axis=0)
    return dict(rgb=np.zeros((2, 32, 32, 3), np.uint8), state_native=state,
        teacher_label_native=state + np.array([1, 0, 0, 0, 0, 0, 0, 0, 0], np.float32),
        executed_action_native=state - 2, teacher_observation=np.full((2, 21), 999),
        teacher_label_valid=np.ones(2, bool), role=role, label_semantics=LABEL,
        rollout_success=success, split=split)


def test_failed_learner_corrections_retained_and_only_query_targets_supervised():
    row = record()
    samples = DaggerSamples([row], 4, 32)
    assert len(samples) == 2
    item = samples[0]
    assert set(item) == {IMAGE_KEY, 'observation.state', 'action', 'action_is_pad'}
    assert item['action_is_pad'].tolist() == [False, True, True, True]
    expected = 2 * (row['teacher_label_native'][0] - LOW) / (HIGH - LOW) - 1
    np.testing.assert_allclose(item['action'][0].numpy(), expected)
    assert not np.allclose(item['action'][0], 2 * (row['executed_action_native'][0] - LOW) / (HIGH - LOW) - 1)
    assert 'teacher_observation' not in item


@pytest.mark.parametrize('role,success,split', [('expert_executed_success', False, 'imitation_train'),
    ('teacher_query', False, 'development'), ('teacher_query', False, 'sealed_test'), ('student_executed', True, 'imitation_train')])
def test_failed_expert_heldout_or_learner_dispatch_cannot_become_targets(role, success, split):
    with pytest.raises(ValueError, match='Only expert'):
        DaggerSamples([record(role, success, split)], 4, 32)


def test_seed_plan_predeclares_disjoint_development_and_collection():
    configured = settings({'collection_episodes': 2, 'evaluation_episodes': 2}, {})
    manifest = {'episodes': [{'seed': 2041}]}
    provenance = {'settings': {'seed': 41}, 'independent_evaluation_seeds': [10041]}
    plan = seed_plan({}, configured, manifest, provenance)
    assert not set(plan['development']) & set(plan['collection'])
    assert not plan['sealed_final_test'] and not plan['development_observations_fit']
    for config in ({'development_seeds': plan['collection']}, {'collection_seeds': [2041, 2042]}, {'development_seeds': [10041, 10042]}):
        with pytest.raises(ValueError, match='overlap'):
            seed_plan(config, configured, manifest, provenance)


class Env:
    def reset(self, seed=None):
        self.command, self.data, self.steps = HOME.copy(), SimpleNamespace(time=0.), 0
        return self.observation(), {'distance_m': .1}
    def observation(self):
        return np.concatenate([self.command, np.zeros(12, np.float32)]).astype(np.float32)
    def render(self):
        return np.zeros((32, 32, 3), np.uint8)
    def step_command(self, command):
        self.steps += 1
        target = HOME.copy()
        target[:3] = self.command[:3] + np.clip(np.clip(command[:3], 35, 145)-self.command[:3], -2.5, 2.5)
        self.command, self.data.time = target, self.data.time + .08
        return self.observation(), -1., False, self.steps == 2, {'success': False, 'distance_m': .2, 'physical_run': False}


class Teacher:
    def __init__(self):
        self.observations = []
    def predict(self, observation, deterministic=True):
        self.observations.append(observation.copy())
        return np.ones(3, np.float32), None


class Learner:
    def eval(self):
        return self
    def reset(self):
        pass
    def select_action(self, batch):
        assert set(batch) == {IMAGE_KEY, 'observation.state'}
        return torch.full((1, 9), -1.)


def test_teacher_current_state_query_is_read_only_and_not_dispatched():
    env = Env()
    env.reset(seed=1)
    teacher = Teacher()
    label, raw, observation = teacher_label(teacher, env)
    np.testing.assert_array_equal(env.command, HOME)
    assert env.data.time == 0 and env.steps == 0
    np.testing.assert_array_equal(label[:3], [92.5, 92.5, 92.5])
    np.testing.assert_array_equal(observation, teacher.observations[0])


def test_actual_learner_trace_separates_teacher_labels_and_dispatched_actions(tmp_path, monkeypatch):
    import explorer_sim.dagger as module
    monkeypatch.setattr(module, 'write_frame', lambda *args: None)
    env, teacher = Env(), Teacher()
    result, records, manifest = rollout_student(Learner(), env, [54041],
        {'image_size': 32, 'device': 'cpu'}, tmp_path, teacher=teacher)
    assert result['successes'] == 0 and len(DaggerSamples(records, 4, 32)) == 2
    path = tmp_path / manifest['episodes'][0]['file']
    with np.load(path, allow_pickle=False) as source:
        np.testing.assert_array_equal(source['teacher_label_native'][:, 0], [92.5, 90])
        np.testing.assert_array_equal(source['executed_action_native'][:, 0], [87.5, 85])
        np.testing.assert_array_equal(source['state_native'][:, 0], [90, 87.5])
        np.testing.assert_array_equal(source['teacher_observation'][:, 0], [90, 87.5])
        np.testing.assert_array_equal(source['teacher_query_simulator_time_s'], source['simulator_time_s'])
    assert manifest['label_semantics'] == LABEL
    assert not manifest['generic_executed_episode_export_compatible']
    with pytest.raises(ValueError, match='action labels'):
        load_episode(tmp_path / 'manifest.json', manifest['episodes'][0], manifest)


def test_source_checksum_and_completed_artifact_binding(tmp_path):
    source = tmp_path / 'original.txt'
    source.write_text('original')
    verify_source_files({str(source): sha(source)})
    context = {'input': 'fixed'}
    write_json(tmp_path / 'result.json', {'source_context': context, 'artifact_sha256': {'original.txt': sha(source)}})
    assert _completed(tmp_path, context)
    with pytest.raises(ValueError, match='different source'):
        _completed(tmp_path, {'input': 'other'})
    source.write_text('changed')
    with pytest.raises(ValueError, match='checksum'):
        _completed(tmp_path, context)


def source_fixture(tmp_path):
    folder = tmp_path / 'source'
    directory = folder / 'dataset'
    directory.mkdir(parents=True)
    teacher = folder / 'teacher_ppo.zip'
    teacher.write_bytes(b'only source verification; never loaded as a model')
    (folder / 'model.xml').write_text('<mujoco/>')
    episodes = []
    for index, (success, split) in enumerate([(True, 'imitation_train'), (False, 'imitation_train'), (True, 'development')]):
        state = np.repeat(HOME[None], 2, axis=0)
        p = directory / f'episode{index}.npz'
        np.savez(p, rgb=np.full((2, 32, 32, 3), index, np.uint8), state_command9=state, command9=state,
            state=2*(state-LOW)/(HIGH-LOW)-1, action=2*(state-LOW)/(HIGH-LOW)-1)
        episodes.append({'id': p.stem, 'file': p.name, 'sha256': sha(p), 'split': split, 'outcome': {'success': success}, 'seed': 2000+index})
    manifest = dict(source='real_mujoco_ppo_rollouts', contains_physical_recordings=False, format='explorer_npz_episode_v1',
        episodes=episodes, action_names=ACTION_NAMES, action_normalization=dict(low=LOW.tolist(), high=HIGH.tolist()))
    write_json(directory / 'manifest.json', manifest)
    write_json(folder / 'provenance.json', {'settings': {'seed': 41}})
    write_json(folder / 'result.json', dict(task='engineering_fixed_base_reach_pilot', physical_io=False,
        teacher=dict(save_reload_passed=True, sha256=sha(teacher)), dataset=dict(manifest_sha256=sha(directory/'manifest.json')),
        provenance_sha256=sha(folder/'provenance.json')))
    return folder


def test_source_rejects_failed_and_development_targets_and_detects_tampering(tmp_path):
    folder = source_fixture(tmp_path)
    bundle = source_bundle(SimpleNamespace(root=tmp_path), {'source_result': str(folder/'result.json')})
    assert len(bundle['records']) == 1 and len(bundle['exclusions']) == 2
    assert len(DaggerSamples(bundle['records'], 4, 32)) == 2
    (folder/'dataset/episode1.npz').write_bytes(b'changed failed source still must verify')
    with pytest.raises(ValueError, match='checksum'):
        source_bundle(SimpleNamespace(root=tmp_path), {'source_result': str(folder/'result.json')})


def test_model_identity_allows_asset_relocation_but_rejects_physics_change():
    first = '<mujoco><asset><mesh name="arm" file="/old/arm.STL"/></asset><option timestep=".001"/></mujoco>'
    moved = first.replace('/old/', '/new/')
    assert model_identity(first) == model_identity(moved)
    assert model_identity(first) != model_identity(moved.replace('.001', '.01'))


def test_artifact_checks_do_not_bind_mutable_worker_logs(tmp_path):
    (tmp_path / 'worker.log').write_text('worker still appends after run_dagger returns')
    (tmp_path / 'before.json').write_text('{}')
    assert artifact_hashes(tmp_path) == {'before.json': sha(tmp_path / 'before.json')}


def test_interrupted_attempt_preserves_own_evidence_and_leaves_worker_log(tmp_path):
    (tmp_path / 'worker.log').write_text('active logger')
    (tmp_path / 'provenance.json').write_text('{}')
    (tmp_path / 'corrections').mkdir()
    (tmp_path / 'corrections/episode.npz').write_bytes(b'previous teacher queries')
    _archive_interrupted(tmp_path)
    archive, = (tmp_path / 'interrupted_attempts').iterdir()
    assert (archive / 'corrections/episode.npz').read_bytes() == b'previous teacher queries'
    assert (tmp_path / 'worker.log').read_text() == 'active logger'
    assert not (tmp_path / 'provenance.json').exists()
