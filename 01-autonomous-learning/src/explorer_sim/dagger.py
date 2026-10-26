"""One real simulation-only DAgger round, Ross et al. 2011 §3 / Algorithm 3.1.

Methods: DAGGER_ROSS_2011, PPO_SB3_2_9, ACT_LEROBOT_0_6_1,
EXPLORER_CAD_MUJOCO, ENGINEERING_GLUE. Counterfactual expert labels and actual
learner dispatches have different typed fields. No robot transport is imported.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time
import uuid
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
import torch
from torch.utils.data import Dataset

from .dataset_export import load_episode, validate_splits
from .pilot import (ACTION_NAMES, HIGH, HOME, LOW, ReachEnv, evaluate_teacher,
                    resolve_mesh_dir, sha, state_digest, summarize, utc, write_frame, write_json)
from .student import (IMAGE_KEY, framework_info, make_act_policy,
                      optimize_act, prepare_rgb, preprocessing_config, save_reload_act)

METHODS = ['DAGGER_ROSS_2011', 'PPO_SB3_2_9', 'ACT_LEROBOT_0_6_1', 'EXPLORER_CAD_MUJOCO', 'ENGINEERING_GLUE']
LABEL = 'counterfactual_current_state_teacher_endpoint_not_executed'
TRAIN_SPLITS = {'train', 'training', 'imitation_train'}
OWN_FILES = ('provenance.json', 'model.xml', 'before.json', 'collection.json', 'after.json', 'frame.jpg', 'frame.json')
OWN_DIRECTORIES = ('round0_expert', 'round1_aggregated', 'corrections', 'development_before', 'development_after')


def _integer(config, name, default, low, high):
    value = config.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid DAgger parameter: ' + name)
    return value


def settings(config, source_settings):
    if config.get('device', 'cpu') != 'cpu':
        raise ValueError('This bounded DAgger engineering pilot uses CPU only')
    return dict(seed=_integer(config, 'seed', 52041, 0, 1_000_000),
        initial_act_steps=_integer(config, 'initial_act_steps', 120, 1, 100_000),
        act_steps=_integer(config, 'act_steps', 120, 1, 100_000),
        collection_episodes=_integer(config, 'collection_episodes', 4, 1, 100),
        evaluation_episodes=_integer(config, 'evaluation_episodes', 6, 1, 100),
        horizon=_integer(config, 'horizon', source_settings.get('horizon', 60), 8, 300),
        image_size=_integer(config, 'image_size', source_settings.get('image_size', 64), 32, 224),
        chunk_size=_integer(config, 'chunk_size', source_settings.get('chunk_size', 8), 1, 100),
        batch_size=_integer(config, 'batch_size', 8, 1, 64), device='cpu')


def seed_plan(config, configured, manifest, provenance):
    """Predeclare collection/development identities before any optimization/evaluation."""
    def seeds(key, default, count):
        value = config.get(key, default)
        if not isinstance(value, list) or len(value) != count or any(type(x) is not int or not 0 <= x <= 2**31 - 1 for x in value) or len(set(value)) != len(value):
            raise ValueError('Invalid unique DAgger seed list: ' + key)
        return value
    collection = seeds('collection_seeds', [configured['seed'] + 2000 + i for i in range(configured['collection_episodes'])], configured['collection_episodes'])
    development = seeds('development_seeds', [configured['seed'] + 10000 + i for i in range(configured['evaluation_episodes'])], configured['evaluation_episodes'])
    prior = {item['seed'] for item in manifest['episodes'] if type(item.get('seed')) is int}
    prior.update(provenance.get('independent_evaluation_seeds', []))
    prior.update(x for x in (provenance.get('training_seed'), provenance.get('settings', {}).get('seed')) if type(x) is int)
    source_seed = provenance.get('settings', {}).get('seed')
    if type(source_seed) is int:
        prior.add(source_seed + 9999)  # Source pilot's reload check, never a new training seed.
    if set(collection) & set(development) or (set(collection) | set(development)) & prior:
        raise ValueError('DAgger collection/development seeds overlap each other or source seed identities')
    return dict(collection=collection, development=development, blocked_source_seeds=sorted(prior),
        collection_role='development_training_only', evaluation_role='engineering_development_evaluation',
        development_observations_fit=False, sealed_final_test=False, beta=0.0, rounds=1)


def source_bundle(store, config):
    identity, explicit = config.get('source_job_id'), config.get('source_result')
    if bool(identity) == bool(explicit):
        raise ValueError('Supply exactly one source_job_id or explicit source_result path')
    if identity:
        if not isinstance(identity, str) or Path(identity).name != identity or identity in ('.', '..'):
            raise ValueError('Invalid source job identity')
        existing = store.job(identity)
        if not existing or existing.get('status') != 'completed' or existing.get('kind') != 'sim_pilot':
            raise ValueError('Source job must be an actually completed sim_pilot')
        result_path = Path(store.root) / 'jobs' / identity / 'result.json'
    else:
        result_path = Path(explicit).expanduser()
    result_path = result_path.resolve(strict=True)
    result = json.loads(result_path.read_text())
    if result.get('task') != 'engineering_fixed_base_reach_pilot' or result.get('physical_io') is not False or not result.get('teacher', {}).get('save_reload_passed'):
        raise ValueError('Source result is not a completed simulation teacher pilot')
    teacher = Path(config.get('teacher_checkpoint') or result_path.parent / 'teacher_ppo.zip').expanduser().resolve(strict=True)
    manifest_path = Path(config.get('dataset_manifest') or result_path.parent / 'dataset/manifest.json').expanduser().resolve(strict=True)
    provenance_path = Path(config.get('source_provenance') or result_path.parent / 'provenance.json').expanduser().resolve(strict=True)
    model_path = Path(config.get('source_model') or result_path.parent / 'model.xml').expanduser().resolve(strict=True)
    if sha(teacher) != result['teacher']['sha256'] or sha(manifest_path) != result['dataset']['manifest_sha256'] or sha(provenance_path) != result['provenance_sha256']:
        raise ValueError('Source teacher/dataset/provenance checksum mismatch')
    manifest, provenance = json.loads(manifest_path.read_text()), json.loads(provenance_path.read_text())
    if manifest.get('source') != 'real_mujoco_ppo_rollouts' or manifest.get('contains_physical_recordings') is not False or manifest.get('format') != 'explorer_npz_episode_v1':
        raise ValueError('DAgger source must be the original simulated PPO episode format')
    bounds = manifest.get('action_normalization', {})
    if manifest.get('action_names') != ACTION_NAMES or not np.array_equal(bounds.get('low'), LOW) or not np.array_equal(bounds.get('high'), HIGH):
        raise ValueError('Source action schema/normalization differs from this CAD reach task')
    validate_splits(manifest)
    records, exclusions, checked = [], [], {}
    for descriptor in manifest['episodes']:
        loaded = load_episode(manifest_path, descriptor, manifest)
        with np.load(loaded['path'], allow_pickle=False) as original:
            expected_state = 2 * (loaded['state'] - LOW) / (HIGH - LOW) - 1
            expected_action = 2 * (loaded['action'] - LOW) / (HIGH - LOW) - 1
            if loaded['state'].shape[1:] != (9,) or original['action'].shape != expected_action.shape or not np.allclose(original['state'], expected_state, atol=1e-6) or not np.allclose(original['action'], expected_action, atol=1e-6):
                raise ValueError('Source normalized state/action is not the recorded command endpoint')
        checked[str(loaded['path'])] = descriptor['sha256']
        success = descriptor.get('outcome', {}).get('success') is True
        if descriptor.get('split') in TRAIN_SPLITS and success:
            records.append(dict(rgb=loaded['rgb'], state_native=loaded['state'], teacher_label_native=loaded['action'],
                role='expert_executed_success', rollout_success=True, split='imitation_train',
                id=descriptor['id'], teacher_label_valid=np.ones(len(loaded['rgb']), bool)))
        else:
            exclusions.append(dict(id=descriptor['id'], split=descriptor.get('split'), success=descriptor.get('outcome', {}).get('success'),
                                   reason='Only explicitly successful training demonstrations are expert targets'))
    if not records:
        raise ValueError('No explicitly successful source expert training episodes')
    checked.update({str(result_path): sha(result_path), str(teacher): sha(teacher), str(manifest_path): sha(manifest_path),
                    str(provenance_path): sha(provenance_path), str(model_path): sha(model_path)})
    return dict(result_path=result_path, result=result, teacher=teacher, manifest_path=manifest_path,
        manifest=manifest, provenance=provenance, model_path=model_path, records=records, exclusions=exclusions, checked=checked)


def verify_source_files(checked):
    for name, digest in checked.items():
        if not Path(name).is_file() or sha(name) != digest:
            raise ValueError('Original DAgger source changed: ' + name)


class DaggerSamples(Dataset):
    """Current-step labels only, for both expert and learner-visited observations.

    Future expert queries at future learner states are not an expert continuation
    from the current state. ACT's later chunk slots therefore stay padded.
    """
    def __init__(self, records, chunk_size, image_size):
        self.records, self.indices = records, []
        self.chunk_size, self.image_size = chunk_size, image_size
        for episode_index, record in enumerate(records):
            expert = record.get('role') == 'expert_executed_success' and record.get('rollout_success') is True
            query = record.get('role') == 'teacher_query' and record.get('label_semantics') == LABEL
            if record.get('split') not in TRAIN_SPLITS or not (expert or query):
                raise ValueError('Only expert training or typed teacher-query records may supervise DAgger')
            count = len(record['rgb'])
            if record['rgb'].dtype != np.uint8 or record['rgb'].ndim != 4 or record['rgb'].shape[-1] != 3 or record['state_native'].shape != (count, 9) or record['teacher_label_native'].shape != (count, 9) or record['teacher_label_valid'].shape != (count,):
                raise ValueError('DAgger actor/teacher sample dimensions differ')
            if not np.isfinite(record['state_native']).all() or not np.isfinite(record['teacher_label_native']).all():
                raise ValueError('DAgger sample is nonfinite')
            self.indices.extend((episode_index, index) for index in range(count) if record['teacher_label_valid'][index])
        if not self.indices:
            raise ValueError('No valid DAgger supervision')

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, index):
        episode_index, position = self.indices[index]
        record = self.records[episode_index]
        rgb = np.asarray(Image.fromarray(record['rgb'][position]).resize((self.image_size, self.image_size)))
        state = 2 * (record['state_native'][position] - LOW) / (HIGH - LOW) - 1
        label = 2 * (record['teacher_label_native'][position] - LOW) / (HIGH - LOW) - 1
        actions = np.broadcast_to(label, (self.chunk_size, 9)).copy().astype(np.float32)
        pad = np.ones(self.chunk_size, bool)
        pad[0] = False
        return {IMAGE_KEY: prepare_rgb(rgb[None])[0], 'observation.state': torch.from_numpy(state.astype(np.float32)),
                'action': torch.from_numpy(actions), 'action_is_pad': torch.from_numpy(pad)}


def teacher_label(teacher, env):
    """Read current privileged simulation state; never advance it to obtain a label."""
    observation = env.observation().copy()
    action, _ = teacher.predict(observation, deterministic=True)
    raw = np.asarray(action, np.float32)
    if raw.shape != (3,) or not np.isfinite(raw).all():
        raise ValueError('Teacher must produce three finite current-state joint increments')
    endpoint = HOME.copy()
    endpoint[:3] = np.clip(env.command[:3] + 2.5 * np.clip(raw, -1, 1), 35, 145)
    return endpoint, raw.copy(), observation


def rollout_student(policy, env, seeds, configured, directory, *, teacher=None):
    directory.mkdir(parents=True, exist_ok=True)
    episodes, records, descriptors = [], [], []
    policy.eval()
    for index, seed in enumerate(seeds):
        _, initial = env.reset(seed=seed)
        policy.reset()
        rows = {name: [] for name in ('rgb', 'state_native', 'requested_action_normalized', 'executed_action_native',
            'reward', 'simulator_time_s', 'next_simulator_time_s')}
        if teacher is not None:
            rows.update({name: [] for name in ('teacher_label_native', 'teacher_raw_increment3', 'teacher_observation', 'teacher_query_simulator_time_s')})
        done = False
        while not done:
            frame = np.asarray(Image.fromarray(env.render()).resize((configured['image_size'], configured['image_size'])))
            state, stamp = env.command.copy(), float(env.data.time)
            if teacher is not None:
                label, increment, privileged = teacher_label(teacher, env)
                rows['teacher_label_native'].append(label)
                rows['teacher_raw_increment3'].append(increment)
                rows['teacher_observation'].append(privileged)
                rows['teacher_query_simulator_time_s'].append(float(env.data.time))
            batch = {IMAGE_KEY: prepare_rgb(frame[None]).to(configured['device']),
                'observation.state': torch.from_numpy((2 * (state - LOW) / (HIGH - LOW) - 1)[None]).to(configured['device'])}
            with torch.inference_mode():
                normalized = policy.select_action(batch)[0].detach().cpu().numpy()
            if normalized.shape != (9,) or not np.isfinite(normalized).all():
                raise ValueError('Student produced an invalid normalized nine-axis endpoint')
            request = (np.clip(normalized, -1, 1) + 1) * .5 * (HIGH - LOW) + LOW
            _, reward, terminal, truncated, info = env.step_command(request)
            rows['rgb'].append(frame)
            rows['state_native'].append(state)
            rows['requested_action_normalized'].append(normalized.copy())
            rows['executed_action_native'].append(env.command.copy())  # Actual slew-limited dispatch, not request/teacher.
            rows['reward'].append(reward)
            rows['simulator_time_s'].append(stamp)
            rows['next_simulator_time_s'].append(float(env.data.time))
            done = terminal or truncated
        arrays = {name: np.asarray(values) for name, values in rows.items()}
        if not np.allclose(arrays['state_native'][1:], arrays['executed_action_native'][:-1], atol=1e-6):
            raise ValueError('Learner trace command causality failed')
        if teacher is not None:
            arrays['teacher_label_valid'] = np.ones(len(arrays['rgb']), bool)
            if not np.array_equal(arrays['teacher_query_simulator_time_s'], arrays['simulator_time_s']):
                raise ValueError('Teacher label was not queried at the actor pre-step state')
        path = directory / f'episode_{index:04d}.npz'
        np.savez_compressed(path, **arrays)
        outcome = dict(seed=seed, initial_distance_m=initial['distance_m'], **info, **{'return': float(np.sum(arrays['reward']))})
        episodes.append(outcome)
        descriptor = dict(id=path.stem, file=path.name, sha256=sha(path), frames=len(arrays['rgb']), seed=seed,
            split='imitation_train' if teacher is not None else 'development', rollout_outcome=outcome,
            valid_teacher_labels=len(arrays['rgb']) if teacher is not None else 0)
        descriptors.append(descriptor)
        if teacher is not None:
            records.append(dict(**arrays, role='teacher_query', label_semantics=LABEL, split='imitation_train',
                id=path.stem, rollout_success=info['success']))
        write_frame(env, directory.parent, 'Actual MuJoCo DAgger learner rollout; teacher query is not dispatched' if teacher is not None else 'Actual independent MuJoCo development rollout')
    manifest = dict(schema='explorer_dagger_teacher_queries_v1' if teacher is not None else 'explorer_dagger_development_trace_v1',
        source='actual_mujoco_student_rollouts', episodes=descriptors, method_ids=METHODS,
        label_semantics=LABEL if teacher is not None else 'no_training_labels',
        executed_action_semantics='actual_student_slew_limited_command_applied_to_simulator_not_measured_joint_state', state_basis='command_estimate',
        action_names=ACTION_NAMES, action_normalization=dict(low=LOW.tolist(), high=HIGH.tolist()),
        actor_inputs=['rgb', 'state_native_command_estimate'],
        teacher_and_evaluator_only=['teacher_observation', 'goal_xyz', 'simulator_joint_state'],
        contains_physical_recordings=False, correction_admission='valid_teacher_query_independent_of_learner_outcome',
        teacher_label_valid_definition='finite_schema_current_state_query_not_verified_recovery',
        teacher_query_recovery_executed=False,
        future_chunk_targets='padded_no_assumed_expert_continuation', generic_executed_episode_export_compatible=False)
    write_json(directory / 'manifest.json', manifest)
    return summarize(episodes), records, manifest


def _archive_interrupted(folder):
    if (folder / 'provenance.json').exists():
        archive = folder / 'interrupted_attempts' / uuid.uuid4().hex
        archive.mkdir(parents=True)
        temporary_names = tuple(name + '.tmp' for name in OWN_FILES if name.endswith('.json')) + ('frame.tmp.jpg',)
        for name in OWN_FILES + OWN_DIRECTORIES + temporary_names:
            path = folder / name
            if path.exists():
                shutil.move(str(path), str(archive / path.name))


def _completed(folder, context):
    path = folder / 'result.json'
    if not path.is_file():
        return None
    result = json.loads(path.read_text())
    if result['source_context'] != context:
        raise ValueError('Completed DAgger job belongs to different source/settings/seed plan')
    for name, digest in result['artifact_sha256'].items():
        artifact = (folder / name).resolve()
        if not artifact.is_relative_to(folder.resolve()) or not artifact.is_file() or sha(artifact) != digest:
            raise ValueError('Completed DAgger artifact checksum mismatch')
    return result


def _train_round(records, configured, folder, update, policy=None, progress_range=(.60, .85)):
    folder.mkdir(parents=True, exist_ok=True)
    samples = DaggerSamples(records, configured['chunk_size'], configured['image_size'])
    policy = make_act_policy(configured) if policy is None else policy
    before = state_digest(policy)
    optimizer, losses, started = optimize_act(policy, samples, configured, folder, update,
        progress_start=progress_range[0], progress_end=progress_range[1])
    first = samples[0]
    check = {key: first[key][None].to(configured['device']) for key in (IMAGE_KEY, 'observation.state')}
    roles = {role: sum(int(np.count_nonzero(r['teacher_label_valid'])) for r in records if r['role'] == role)
             for role in sorted({r['role'] for r in records})}
    preprocessing = dict(preprocessing_config(), supervision='typed_current_step_teacher_endpoints',
        source_role_sample_counts=roles, correction_label_semantics=LABEL, valid_action_chunk_positions=[0],
        expert_targets_executed_in_simulation=True, correction_targets_executed=False,
        training_scope='simulation_development_only')
    reloaded, checkpoint, error = save_reload_act(policy, optimizer, folder, preprocessing, check, configured['device'])
    evidence = dict(optimizer_steps=len(losses), actor_samples=len(samples), weights_before_sha256=before,
        weights_after_sha256=state_digest(policy), weights_changed=before != state_digest(policy),
        save_reload_passed=True, reload_max_abs_error=error, checkpoint_dir=str(checkpoint),
        checkpoint_sha256={p.name: sha(p) for p in sorted(checkpoint.iterdir()) if p.is_file()},
        losses_first_last=[losses[0]['loss'], losses[-1]['loss']], elapsed_s=time.monotonic()-started)
    write_json(folder / 'training_result.json', evidence)
    return reloaded, evidence


def model_identity(xml):
    """Compare frozen reach models while allowing a relocated, hash-identical CAD tree."""
    tree = ET.fromstring(xml)
    for element in tree.iter():
        if 'file' in element.attrib:
            element.attrib['file'] = Path(element.attrib['file']).name
    return ET.tostring(tree, encoding='unicode')


def artifact_hashes(folder):
    """Only our completed artifacts; worker logs/leases may continue changing."""
    files = [folder / name for name in OWN_FILES if (folder / name).is_file()]
    for name in OWN_DIRECTORIES:
        files.extend(path for path in (folder / name).rglob('*') if path.is_file())
    return {str(path.relative_to(folder)): sha(path) for path in sorted(files)}


def run_dagger(store, job):
    """One beta=0 learner-collection/aggregation round; all evaluation is development."""
    from stable_baselines3 import PPO
    config = job.get('config') or {}
    source = source_bundle(store, config)
    configured = settings(config, source['provenance'].get('settings', {}))
    plan = seed_plan(config, configured, source['manifest'], source['provenance'])
    job_id = job['id']
    if not isinstance(job_id, str) or Path(job_id).name != job_id or job_id in ('.', '..'):
        raise ValueError('Invalid DAgger job identity')
    folder = (Path(store.root) / 'jobs' / job_id).resolve()
    if any(Path(name).is_relative_to(folder) for name in source['checked']):
        raise ValueError('DAgger output must not overwrite its source pilot')
    root = Path(__file__).resolve().parents[3]
    assets = resolve_mesh_dir(config.get('mesh_dir') or os.environ.get('EXPLORER_SIM_MESH_DIR') or root / '_registry/private/assets/m3_reference/meshes')
    asset_hashes = {p.name: sha(p) for p in sorted(assets.glob('*.STL'))}
    legacy_model_sha = sha(Path(__file__).parents[1] / 'legacy/simulator/explorer_model.py')
    if asset_hashes != source['provenance'].get('cad_mesh_sha256') or legacy_model_sha != source['provenance'].get('legacy_model_source_sha256'):
        raise ValueError('DAgger CAD assets/model differ from the source teacher environment')
    context = dict(source_files_sha256=source['checked'], settings=configured, seed_plan=plan, cad_mesh_sha256=asset_hashes,
        dagger_source_sha256=sha(Path(__file__)), student_source_sha256=sha(Path(__file__).with_name('student.py')),
        pilot_source_sha256=sha(Path(__file__).with_name('pilot.py')), legacy_model_source_sha256=legacy_model_sha)
    saved = _completed(folder, context)
    if saved is not None:
        return saved
    folder.mkdir(parents=True, exist_ok=True)
    _archive_interrupted(folder)
    torch.set_num_threads(4)
    started = time.monotonic()
    provenance = dict(schema='explorer_dagger_one_round_v1', created_wall_utc=utc(), method_ids=METHODS,
        source_context=context, cad_mesh_sha256=asset_hashes, physical_io=False, sealed_test_used=False,
        primary_source='https://proceedings.mlr.press/v15/ross11a.html',
        source_algorithm='Section3 / Algorithm3.1; one beta=0 aggregation iteration after initial expert fitting',
        teacher_queries='privileged_current_simulator_state_only', actor_inputs=['RGB', 'command_estimate9'],
        correction_teacher_labels_are_executed=False, expert_labels_executed_in_simulation=True,
        teacher_query_recovery_executed=False,
        training_label_scope='one_current_action_for_every_expert_or_query_sample',
        excluded_source_episodes=source['exclusions'],
        versions={name: importlib.metadata.version(name) for name in ('mujoco', 'stable_baselines3', 'lerobot', 'torch', 'numpy')})
    write_json(folder / 'provenance.json', provenance)  # Seed plan is immutable before training/evaluation.
    def update(progress, phase):
        store.update_job(job_id, progress=progress, result=dict(phase=phase, source='actual_mujoco_dagger', physical_io=False))
    teacher = PPO.load(source['teacher'], device='cpu')
    if teacher.observation_space.shape != (21,) or teacher.action_space.shape != (3,):
        raise ValueError('Source teacher observation/action spaces differ')
    env = ReachEnv(assets, configured['horizon'])
    evaluation_env = ReachEnv(assets, configured['horizon'])
    try:
        if model_identity(env.xml) != model_identity(source['model_path'].read_text()):
            raise ValueError('Reach environment XML differs from the completed teacher source model')
        (folder / 'model.xml').write_text(env.xml)
        initial_settings = dict(configured, act_steps=configured['initial_act_steps'])
        baseline, before_training = _train_round(source['records'], initial_settings, folder / 'round0_expert', update, progress_range=(.05, .35))
        before, _, _ = rollout_student(baseline, evaluation_env, plan['development'], configured, folder / 'development_before')
        write_json(folder / 'before.json', before)
        update(.40, 'initial_student_independently_development_evaluated')
        teacher_reference = evaluate_teacher(teacher, evaluation_env, plan['development'], folder)
        collection, corrections, correction_manifest = rollout_student(baseline, env, plan['collection'], configured,
            folder / 'corrections', teacher=teacher)
        write_json(folder / 'collection.json', collection)
        update(.55, 'real_teacher_queries_collected_on_learner_states')
        verify_source_files(source['checked'])
        trained, after_training = _train_round(source['records'] + corrections, configured, folder / 'round1_aggregated', update, baseline)
        after, _, _ = rollout_student(trained, evaluation_env, plan['development'], configured, folder / 'development_after')
        write_json(folder / 'after.json', after)
        verify_source_files(source['checked'])
        result = dict(task='one_round_dagger_fixed_base_reach', status='simulation_development_evaluated',
            physical_io=False, sealed_final_test=False, method_ids=METHODS, source_context=context,
            seed_plan=plan, artifacts_dir=str(folder), before=before, after=after,
            privileged_teacher_reference=teacher_reference, collection=collection,
            initial_training=before_training, aggregated_training=after_training,
            expert_training_episodes=len(source['records']), excluded_source_episodes=source['exclusions'],
            correction_episodes=len(corrections), teacher_query_labels=sum(d['valid_teacher_labels'] for d in correction_manifest['episodes']),
            valid_corrections_from_failed_learner_episodes=sum(len(r['rgb']) for r in corrections if not r['rollout_success']),
            correction_manifest_sha256=sha(folder / 'corrections/manifest.json'),
            learner_executed_actions_used_as_targets=False, original_sources_unchanged=True,
            teacher_query_recovery_executed=False,
            primary_success_rate_delta=after['success_rate']-before['success_rate'],
            mean_final_distance_delta_m=after['mean_final_distance_m']-before['mean_final_distance_m'],
            primary_improvement_observed=after['success_rate'] > before['success_rate'],
            limitations=['One development round; no sealed final test or statistical recovery guarantee',
                'No matched extra-BC-update control; any observed gain cannot be uniquely attributed to aggregation',
                'Fixed simulated base and three active CAD arm axes only; no cloth, kitchen, contact or hardware qualification',
                'Privileged learned PPO teacher can be wrong on learner-visited states; finite queries are not task-success guarantees'],
            elapsed_s=time.monotonic()-started, **framework_info())
        result['artifact_sha256'] = artifact_hashes(folder)
        write_json(folder / 'result.json', result)
        update(.99, 'dagger_development_before_after_completed')
        return result
    finally:
        env.close()
        evaluation_env.close()
