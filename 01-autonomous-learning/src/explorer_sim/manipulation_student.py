"""ACT_LEROBOT_0_6_1 / EXPLORER_CAD_MUJOCO / ENGINEERING_GLUE.

Official visual ACT learns from complete contact-scored scripted demonstrations.
RGB and the preceding command estimate are the only actor inputs. Scripted
truth/IK generation and independent full-task scoring remain separate baselines.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import uuid

import numpy as np
from PIL import Image
import torch

from legacy.simulator.explorer_model import ROOT, model_metadata, resolve_mesh_dir
from .manipulation import record_rollout, replay_trajectory
from .manipulation_env import ManipulationEnv, TASKS, LOW as ARM_LOW, HIGH as ARM_HIGH
from .pilot import ACTION_NAMES, LOW, HIGH, sha, state_digest, utc, write_json
from .recorded_training import RecordedChunks
from .student import (IMAGE_KEY, framework_info, make_act_policy, optimize_act,
                      prepare_rgb, preprocessing_config, save_reload_act)


def parameters(config):
    result = {}
    limits = [('seed', 531041, 0, 1000000), ('demonstration_episodes', 3, 1, 100),
        ('act_steps', 120, 1, 100000), ('batch_size', 8, 1, 32), ('chunk_size', 16, 1, 100),
        ('image_size', 64, 32, 224), ('evaluation_episodes', 2, 1, 50), ('horizon', 700, 32, 2400)]
    for name, default, lower, upper in limits:
        value = config.get(name, default)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError('Invalid visual manipulation ' + name)
        result[name] = value
    result['task'], result['device'] = config.get('task', TASKS[0]), config.get('device', 'cpu')
    if result['task'] not in TASKS or result['device'] not in ('cpu', 'cuda'):
        raise ValueError('Select a supported complete manipulation task and CPU/CUDA runtime')
    return result


def actor_inputs(rgb, command9, image_size):
    """Typed deployable input boundary: no environment or evaluator access."""
    image, state = np.asarray(rgb), np.asarray(command9, np.float32)
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError('Actor requires a real uint8 RGB frame')
    if state.shape != (9,) or not np.isfinite(state).all() or np.any(state < LOW) or np.any(state > HIGH):
        raise ValueError('Actor requires the preceding finite command9 estimate')
    resized = np.asarray(Image.fromarray(image).resize((image_size, image_size)))
    return {IMAGE_KEY: prepare_rgb(resized[None])[0],
            'observation.state': torch.from_numpy(2 * (state - LOW) / (HIGH - LOW) - 1)}


class VisualACTController:
    """Official saved processor pair around explicit fixed Explorer normalization."""
    def __init__(self, policy, checkpoint, image_size, device='cpu'):
        from lerobot.policies.factory import make_pre_post_processors
        self.policy, self.image_size = policy, image_size
        self.device = device
        if str(next(policy.parameters()).device) != device and not (device == 'cuda' and str(next(policy.parameters()).device) == 'cuda:0'):
            raise ValueError('ACT controller device differs from loaded policy parameters')
        self.pre, self.post = make_pre_post_processors(policy.config, pretrained_path=str(checkpoint),
            preprocessor_overrides={'device_processor': {'device': device}})
        self.reset()

    def reset(self):
        self.policy.eval(); self.policy.reset()

    def predict(self, rgb, command9):
        inputs = self.pre(actor_inputs(rgb, command9, self.image_size))
        with torch.inference_mode():
            predicted = self.post(self.policy.select_action(inputs)).detach().cpu().numpy()[0]
        if predicted.shape != (9,) or not np.isfinite(predicted).all():
            raise ValueError('ACT must predict nine finite normalized endpoints')
        raw_native = LOW + (predicted + 1) * (HIGH - LOW) / 2
        # This declared stationary-base task executes only its six arm endpoints.
        # The three unused chassis predictions are retained, never sent anywhere.
        return raw_native, np.clip(raw_native[:6], ARM_LOW, ARM_HIGH)


def _publish_frame(env, folder, phase, *, image=None):
    image = env.render() if image is None else image
    temporary = folder / 'frame.tmp.jpg'
    Image.fromarray(image).save(temporary, quality=90)
    temporary.replace(folder / 'frame.jpg')
    write_json(folder / 'frame.json', dict(source='actual_mujoco_manipulation', phase=phase,
        wall_utc=utc(), sim_time_s=float(env.data.time), simulation_steps=env.steps,
        actual_workers=1, scene=env.task, physical_run=False))


def record_visual_rollout(env, folder, seed, controller, *, progress_frame=None):
    """Closed-loop dynamics, freshly rendered RGB, and independent substep scoring."""
    folder.mkdir(parents=True, exist_ok=False)
    _, initial = env.reset(seed=seed)
    controller.reset(); env.record_substeps = True
    images, states, raw, requests, dispatched, native, times = [], [], [], [], [], [], []
    native.append(env.integration_state())
    begin = time.monotonic(); error = None
    try:
        done = False
        while not done:
            rgb, command9 = env.render(), np.r_[env.command, 0, 0, 0].astype(np.float32)
            raw9, endpoint = controller.predict(rgb, command9)
            images.append(rgb); states.append(command9); raw.append(raw9)
            times.append(float(env.data.time)); requests.append(endpoint)
            _, _, terminal, truncated, info = env.step_command(endpoint)
            dispatched.append(env.command.copy()); native.append(env.integration_state())
            if progress_frame and env.steps % 10 == 0:
                progress_frame(env)
            done = terminal or truncated
    except ValueError as failure:
        error = str(failure)
    finally:
        env.record_substeps = False
    result = dict(seed=seed, source='reloaded_official_visual_ACT', actor_inputs=['RGB', 'preceding_command9_estimate'],
        n_actions=len(dispatched), error=error, evaluator=env.evaluator.result(),
        wall_seconds=time.monotonic()-begin, initial=initial, final=env.info(), physical_run=False,
        scripted_actions_during_student=False, privileged_truth_in_actor=False)
    # Predict can fail before dispatch. Preserve only causally completed transitions.
    count = len(dispatched)
    np.savez_compressed(folder / 'trajectory.npz', rgb=np.asarray(images[:count], np.uint8),
        state_command9=np.asarray(states[:count]), raw_predicted_endpoint9=np.asarray(raw[:count]),
        requested_endpoint6=np.asarray(requests[:count]), dispatched_endpoint6=np.asarray(dispatched),
        native_integration_state=np.asarray(native), pre_action_sim_time_s=np.asarray(times[:count]),
        substep_contact_trace=np.asarray(env.substep_trace))
    Image.fromarray(env.render()).save(folder / 'final.jpg', quality=90)
    result['trajectory_sha256'] = sha(folder / 'trajectory.npz')
    write_json(folder / 'result.json', result)
    return result


def _sources():
    from explorer_shared.learning import act_backend, artifacts, checkpoint_control
    return [Path(__file__), Path(__file__).with_name('manipulation.py'),
        Path(__file__).with_name('manipulation_env.py'), Path(__file__).with_name('student.py'),
        Path(__file__).with_name('recorded_training.py'), Path(__file__).with_name('pilot.py'),
        Path(__file__).with_name('dataset_export.py'), Path(act_backend.__file__), Path(artifacts.__file__),Path(checkpoint_control.__file__),
        ROOT / 'simulator/explorer_model.py',
        ROOT / 'simulator/environment.py', ROOT / 'simulator/model_config.json',
        ROOT / 'config/explorer.urdf', ROOT / 'config/arm-motion.json']


def _complete(folder, identity):
    path = folder / 'visual-manipulation-result.json'
    if not path.is_file():
        return None
    result = json.loads(path.read_text())
    if result['provenance']['identity'] != identity:
        raise ValueError('Completed visual manipulation belongs to changed source/settings')
    for name, expected in result['artifact_sha256'].items():
        artifact = (folder / name).resolve()
        if not artifact.is_relative_to(folder.resolve()) or not artifact.is_file() or sha(artifact) != expected:
            raise ValueError('Completed visual manipulation artifact checksum mismatch')
    return result


def _prepare_dataset(env, folder, settings, update):
    manifest_path, source_path = folder / 'dataset/manifest.json', folder / 'demonstrations.json'
    if manifest_path.is_file() and source_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        baseline = json.loads(source_path.read_text())
        for item in baseline:
            source = Path(item['source_directory'])
            if sha(source / 'trajectory.npz') != item['trajectory_sha256'] or sha(source / 'result.json') != item['source_result_sha256']:
                raise ValueError('Retained demonstration source checksum mismatch')
        return manifest, baseline
    # Preserve a partial generation rather than mixing old/new rollouts or data.
    if (folder / 'demonstrations').exists() or (folder / 'dataset').exists():
        archive = folder / 'interrupted_generations' / uuid.uuid4().hex
        archive.mkdir(parents=True)
        for name in ('demonstrations', 'dataset', 'demonstrations.json'):
            if (folder / name).exists():
                (folder / name).rename(archive / name)
    dataset = folder / 'dataset'; dataset.mkdir()
    episodes, baselines = [], []
    for index in range(settings['demonstration_episodes']):
        seed = settings['seed'] + index
        source = folder / 'demonstrations' / ('scripted-' + str(seed))
        result = record_rollout(env, source, seed, scripted=True)
        replay = replay_trajectory(env, source) if result['n_actions'] else None
        write_json(source / 'native-replay.json', replay)
        result.update(source_directory=str(source), source_result_sha256=sha(source / 'result.json'), replay=replay)
        baselines.append(result)
        if result['evaluator']['success'] and not result['error'] and replay and replay['within_1e_minus_9']:
            with np.load(source / 'trajectory.npz', allow_pickle=False) as raw:
                state = raw['state_command9'].astype(np.float32)
                action = np.column_stack((raw['dispatched_endpoint6'], np.zeros((len(state), 3)))).astype(np.float32)
                target = dataset / ('scripted-' + str(seed) + '.npz')
                rgb = np.stack([np.asarray(Image.fromarray(image).resize((settings['image_size'], settings['image_size']))) for image in raw['rgb']])
                np.savez_compressed(target, rgb=rgb, state=2 * (state-LOW)/(HIGH-LOW)-1,
                    state_native=state, action_native=action, command9=action,
                    pre_action_sim_time_s=raw['pre_action_sim_time_s'])
            episodes.append(dict(id='scripted-'+str(seed), source_episode_id='scripted-'+str(seed),
                file=target.name, sha256=sha(target), split='train', seed=seed, frames=len(state),
                outcome={'success': True, 'basis': 'independent_full_contact_substep_evaluator'},
                source_trajectory_sha256=result['trajectory_sha256']))
        _publish_frame(env, folder, 'scripted_baseline_complete_not_learned_student')
        update(.05 + .25 * (index+1)/settings['demonstration_episodes'], 'collecting_full_contact_scripted_demonstrations')
    manifest = dict(format='explorer_robot_episode_v1', dataset_id='visual-manipulation-'+folder.name,
        task=settings['task'], source='contact_verified_scripted_truth_IK_not_PPO', fps=20,
        action_names=ACTION_NAMES, action_units=['degrees']*6+['m/s', 'm/s', 'rad/s'],
        action_normalization={'low': LOW.tolist(), 'high': HIGH.tolist()}, state_basis='command_estimate',
        contains_physical_recordings=False, episodes=episodes,
        analysis_source_count=len(baselines), expert_source_count=len(episodes), sealed_test_used=False)
    write_json(source_path, baselines); write_json(manifest_path, manifest)
    return manifest, baselines


def _cuda_backend_qualification():
    if not torch.cuda.is_available():
        raise RuntimeError('Requested CUDA is unavailable in this interpreter')
    receipt = Path.home()/'.local/share/explorer/state/qualification/act-cu132-fp32-20261026/qualification.json'
    if not receipt.is_file():
        raise ValueError('Actual compact ACT CUDA backend qualification is missing')
    tested = json.loads(receipt.read_text())
    if (tested.get('passed') is not True or tested.get('tf32_enabled') is not False
            or tested.get('framework_versions', {}).get('torch') != str(torch.__version__)
            or tested.get('compute_capability') != list(torch.cuda.get_device_capability())
            or tested.get('lerobot') != importlib.metadata.version('lerobot')):
        raise ValueError('Installed CUDA backend differs from the actual compact ACT qualification')
    return {'path':str(receipt), 'sha256':sha(receipt), 'scope':tested['scope'],
        'nine_axis_manipulation_not_inferred_from_six_axis_backend_receipt':True}


def _run_locked(store, job, folder, settings, assets):
    context = dict(settings=settings, source_sha256={str(p): sha(p) for p in _sources()},
        cad_mesh_sha256={p.name: sha(p) for p in sorted(assets.glob('*.STL'))},
        versions={p: importlib.metadata.version(p) for p in ('mujoco', 'lerobot', 'torch', 'numpy')},
        cuda_backend_qualification=_cuda_backend_qualification() if settings['device']=='cuda' else None)
    identity = hashlib.sha256(json.dumps(context, sort_keys=True, allow_nan=False).encode()).hexdigest()
    completed = _complete(folder, identity)
    if completed:
        card = completed.get('skill_card')
        if card and not any(item['id'] == card['id'] for item in store.skills()):
            store.add_skill(card)
        return completed
    provenance_path = folder / 'visual-manipulation-provenance.json'
    if provenance_path.exists() and json.loads(provenance_path.read_text())['identity'] != identity:
        raise ValueError('Interrupted visual manipulation source/settings changed; use a fresh job')
    provenance = dict(identity=identity, context=context, created_wall_utc=utc(), base_fixture=True,
        actor_input_contract='RGB+preceding_command9_estimate', target_truth_in_actor=False,
        demonstration_source='scripted_truth_IK_baseline_separate_from_PPO', physical_io=False,
        train_seeds=list(range(settings['seed'], settings['seed']+settings['demonstration_episodes'])),
        development_seeds=list(range(settings['seed']+10000, settings['seed']+10000+settings['evaluation_episodes'])),
        split='DEVELOPMENT_not_sealed', model_metadata=model_metadata(),
        method_ids=['ACT_LEROBOT_0_6_1', 'EXPLORER_CAD_MUJOCO', 'ENGINEERING_GLUE'])
    write_json(provenance_path, provenance)
    repository = Path(__file__).resolve().parents[3]
    for source in _sources():
        snapshot = folder / 'implementation_sources' / source.relative_to(repository)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if snapshot.exists() and sha(snapshot) != context['source_sha256'][str(source)]:
            raise ValueError('Preserved visual manipulation implementation checksum changed')
        if not snapshot.exists():
            snapshot.write_bytes(source.read_bytes())
    def update(progress, phase):
        store.update_job(job['id'], progress=progress, result=dict(phase=phase,
            source='actual_mujoco_visual_manipulation', actual_workers=1, physical_io=False))
    torch.set_num_threads(2)
    env = ManipulationEnv(assets, settings['task'], settings['horizon'])
    (folder / 'visual-manipulation-model.xml').write_text(env.xml)
    began = time.monotonic()
    try:
        manifest, baselines = _prepare_dataset(env, folder, settings, update)
        if not manifest['episodes']:
            result = dict(status='awaiting_successful_complete_demonstrations', trained=False,
                reason='No complete independently scored and exactly replayed demonstration qualified as expert BC targets',
                scripted_baseline=baselines, physical_io=False, provenance=provenance)
        else:
            samples = RecordedChunks(folder / 'dataset/manifest.json', manifest, manifest['episodes'],
                settings['chunk_size'], settings['image_size'])
            preview = samples[0]
            policy = make_act_policy(settings, state_dim=9, action_dim=9)
            initial = state_digest(policy)
            optimizer, losses, started = optimize_act(policy, samples, settings, folder, update, .30, .80)
            inputs = {key: preview[key].unsqueeze(0).to(settings['device']) for key in (IMAGE_KEY, 'observation.state')}
            adapter = preprocessing_config(LOW, HIGH, range(6))
            adapter.update(action_names=ACTION_NAMES, action_units=manifest['action_units'],
                task=settings['task'], base_fixture=True, command_state_not_measured=True)
            reloaded, checkpoint, reload_error = save_reload_act(policy, optimizer, folder, adapter, inputs, settings['device'])
            from lerobot.policies.factory import make_pre_post_processors
            pre, post = make_pre_post_processors(policy.config)
            pre.save_pretrained(checkpoint, push_to_hub=False, config_filename='policy_preprocessor.json')
            post.save_pretrained(checkpoint, push_to_hub=False, config_filename='policy_postprocessor.json')
            controller = VisualACTController(reloaded, checkpoint, settings['image_size'], settings['device'])
            episodes = []
            for index, seed in enumerate(provenance['development_seeds']):
                target = folder / 'development' / ('ACT-'+str(seed))
                if target.exists():
                    archive = folder / 'interrupted_evaluations' / uuid.uuid4().hex
                    archive.parent.mkdir(exist_ok=True); target.rename(archive)
                evaluated = record_visual_rollout(env, target, seed, controller,
                    progress_frame=lambda current: _publish_frame(current, folder, 'closed_loop_reloaded_visual_ACT_development'))
                replay = replay_trajectory(env, target) if evaluated['n_actions'] else None
                write_json(target / 'native-replay.json', replay)
                episodes.append({**evaluated, 'native_replay': replay})
                _publish_frame(env, folder, 'completed_visual_ACT_development_trial')
                update(.80+.18*(index+1)/settings['evaluation_episodes'], 'independent_full_task_visual_ACT_rollouts')
            result = dict(status='trained_and_development_evaluated', trained=True, task=settings['task'],
                source='contact_verified_scripted_IK_to_official_visual_ACT', actual_workers=1,
                student=dict(algorithm='official LeRobot ACT', official_module=type(policy).__module__, device=settings['device'],
                    parameter_device=str(next(policy.parameters()).device),parameter_dtype=str(next(policy.parameters()).dtype), TF32_enabled=bool(torch.backends.cuda.matmul.allow_tf32) if settings['device']=='cuda' else None,
                    optimizer_steps=len(losses), weights_changed=initial != state_digest(policy),
                    checkpoint_dir=str(checkpoint), checkpoint_sha256={p.name: sha(p) for p in checkpoint.iterdir() if p.is_file()},
                    save_reload_passed=True, reload_max_abs_error=reload_error, training_resume=optimizer.explorer_resume_info,
                    training_episode_count=len(samples.descriptors), training_frame_count=len(samples),
                    losses_first_last=[losses[0]['loss'], losses[-1]['loss']], elapsed_s=time.monotonic()-started,
                    actor_inputs=['RGB', 'preceding_command9_estimate'], privileged_state_in_actor=False,
                    temporal_ensembling=False, official_saved_processors_executed=True),
                scripted_baseline=dict(n=len(baselines), successes=sum(item['evaluator']['success'] for item in baselines),
                    episodes=baselines, source='scripted_truth_IK_not_PPO_not_learned'),
                evaluation=dict(n=len(episodes), successes=sum(item['evaluator']['success'] for item in episodes),
                    episodes=episodes, split='DEVELOPMENT_not_sealed', disjoint_training_seeds=True,
                    grasped=sum(item['evaluator']['grasped'] for item in episodes),
                    lifted=sum(item['evaluator']['lifted'] for item in episodes),
                    carried=sum(item['evaluator']['carried_above_target'] for item in episodes)),
                dataset=dict(manifest=str(folder / 'dataset/manifest.json'), manifest_sha256=sha(folder / 'dataset/manifest.json'),
                    expert_episode_count=len(manifest['episodes']), excluded_failed_baseline_count=len(baselines)-len(manifest['episodes'])),
                physical_io=False, physical_ready=False, kitchen_navigation_implemented=False,
                full_object_skill_mastered=False, scientific_hypotheses_status='UNTESTED', provenance=provenance,
                limitations=['Development seeds vary object start within a narrow local stationary scene',
                    'Estimated materials/contact/servo dynamics, no physical validation',
                    'Scripted demonstrations are an explicitly separate teacher baseline, not learned PPO rollouts',
                    'One short-budget training run does not establish mastery or scientific gain'], **framework_info())
        if any(sha(Path(name)) != digest for name, digest in context['source_sha256'].items()):
            raise ValueError('Visual manipulation implementation changed during execution')
        result['elapsed_s'] = time.monotonic()-began
        if result.get('trained') and result['student']['weights_changed']:
            card = dict(id='act-manipulation-'+job['id'], task=settings['task'],
                source_type='sim_scripted_full_contact_distilled_act', status='trained',
                expected_effect='grasp, lift, carry, release and settle the complete object in the target container',
                preconditions=['Stationary CAD base fixture', 'Compatible source scene and nine-endpoint action schema'],
                postconditions=['Independent full-task evaluator confirms every required contact/placement stage'],
                representation={'kind': 'visual_joint_endpoint_policy', 'actor_inputs': ['RGB', 'preceding_command9_estimate']},
                policy_reference={'checkpoint_dir': result['student']['checkpoint_dir'],
                    'checkpoint_sha256': result['student']['checkpoint_sha256']},
                action_contract={'names': ACTION_NAMES, 'units': manifest['action_units'],
                    'state_basis': 'command_estimate', 'base_fixture': True, 'executed_indices': list(range(6))},
                lineage={'job_id': job['id'], 'dataset_id': manifest['dataset_id'],
                    'manifest_sha256': result['dataset']['manifest_sha256'],
                    'method_ids': provenance['method_ids']},
                evaluation={**result['evaluation'], 'physical_run_ids': []},
                limitations=result['limitations']+['Training is recorded even when all independent object trials fail'])
            result['skill_card'] = card
            if not any(item['id'] == card['id'] for item in store.skills()):
                store.add_skill(card)
        ignored = {'visual-manipulation-result.json', 'result.json', '.visual-manipulation-owner.lock', 'worker.log'}
        result['artifact_sha256'] = {str(p.relative_to(folder)): sha(p) for p in folder.rglob('*')
            if p.is_file() and str(p.relative_to(folder)) not in ignored and not p.name.endswith('.tmp')}
        write_json(folder / 'visual-manipulation-result.json', result)
        update(1, 'visual_manipulation_development_completed')
        return result
    finally:
        env.close()


def run_manipulation_student(store, job):
    """Serial learner, stable optimizer resume, qualified backend and no robot IO."""
    identity = job['id']
    if not isinstance(identity, str) or Path(identity).name != identity or identity in ('.', '..'):
        raise ValueError('Invalid visual manipulation job ID')
    settings = parameters(job.get('config') or {})
    folder = (Path(store.root) / 'jobs' / identity).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    assets = resolve_mesh_dir((job.get('config') or {}).get('mesh_dir'))
    with (folder / '.visual-manipulation-owner.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Visual manipulation already has an active owner') from error
        return _run_locked(store, job, folder, settings, assets)
