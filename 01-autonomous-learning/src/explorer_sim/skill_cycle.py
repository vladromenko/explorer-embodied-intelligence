"""ENGINEERING_GLUE: executed simulation skill-library cycle, with no physical I/O.

Methods: SKILL_CYCLE_SIM_VALIDATION, DAGGER_ROSS_2011, ACT_LEROBOT_0_6_1,
EXPLORER_CAD_MUJOCO. Actual SQLite cards, checkpoint retrieval, bounded learned
adaptation, two-stage inference and independent development/retention trials.
No cloth, grasping, H5, learned retrieval or sealed-final-test claim.
"""
from __future__ import annotations

import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

import mujoco
import numpy as np
from PIL import Image
import torch

from .dagger import (LABEL, _train_round, model_identity, rollout_student,
                    source_bundle, verify_source_files)
from .pilot import (ACTION_NAMES, HIGH, HOME, LOW, ReachEnv, canonical, resolve_mesh_dir,
                    sha, state_digest, utc, write_frame, write_json)
from .student import IMAGE_KEY, framework_info, prepare_rgb, preprocessing_config

METHODS = ['SKILL_CYCLE_SIM_VALIDATION', 'DAGGER_ROSS_2011', 'ACT_LEROBOT_0_6_1', 'EXPLORER_CAD_MUJOCO', 'ENGINEERING_GLUE']
TASK = 'fixed_base_visible_target_reach'


def _int(config, key, default, low, high):
    value = config.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid skill-cycle parameter: ' + key)
    return value


def source_dagger(store, config):
    identity, explicit = config.get('source_job_id'), config.get('source_result')
    if bool(identity) == bool(explicit):
        raise ValueError('Supply one completed DAgger source_job_id or explicit source_result')
    if identity:
        if not isinstance(identity, str) or Path(identity).name != identity or identity in ('.', '..'):
            raise ValueError('Invalid source job identity')
        job = store.job(identity)
        if not job or job.get('kind') != 'dagger' or job.get('status') != 'completed':
            raise ValueError('Skill cycle needs a completed actual DAgger job')
        path = Path(store.root) / 'jobs' / identity / 'result.json'
    else:
        path = Path(explicit).expanduser()
    path = path.resolve(strict=True)
    result = json.loads(path.read_text())
    if result.get('task') != 'one_round_dagger_fixed_base_reach' or result.get('physical_io') is not False or result.get('sealed_final_test') is not False:
        raise ValueError('Unsupported DAgger source domain')
    checked = {str(path): sha(path)}
    for name, digest in result['artifact_sha256'].items():
        artifact = (path.parent / name).resolve()
        if not artifact.is_relative_to(path.parent) or not artifact.is_file() or sha(artifact) != digest:
            raise ValueError('Source DAgger artifact checksum/path mismatch')
        checked[str(artifact)] = digest
    for key in ('initial_training', 'aggregated_training'):
        value = result[key]
        if not value.get('weights_changed') or not value.get('save_reload_passed') or value.get('optimizer_steps', 0) <= 0:
            raise ValueError('Source has no actual learned before/after checkpoints')
    verify_source_files(result['source_context']['source_files_sha256'])
    checked.update(result['source_context']['source_files_sha256'])
    return dict(path=path, result=result, checked=checked)


def training_records(store, config, source):
    paths = [name for name in source['result']['source_context']['source_files_sha256'] if Path(name).name == 'result.json']
    if not paths:
        raise ValueError('Original expert pilot lineage is missing')
    original = source_bundle(store, {'source_result': config.get('pilot_source_result') or paths[0]})
    directory = source['path'].parent / 'corrections'
    manifest = json.loads((directory / 'manifest.json').read_text())
    if sha(directory / 'manifest.json') != source['result']['correction_manifest_sha256'] or manifest.get('label_semantics') != LABEL:
        raise ValueError('Typed teacher-query correction manifest differs')
    records = list(original['records'])
    seeds = set(source['result']['seed_plan']['collection'])
    for item in manifest['episodes']:
        path = (directory / item['file']).resolve()
        if not path.is_relative_to(directory.resolve()) or sha(path) != item['sha256'] or item['seed'] not in seeds or item['split'] != 'imitation_train':
            raise ValueError('Correction training identity/hash/split mismatch')
        with np.load(path, allow_pickle=False) as saved:
            rows = {name: saved[name].copy() for name in saved.files}
        if not np.array_equal(rows['teacher_query_simulator_time_s'], rows['simulator_time_s']) or not np.allclose(rows['state_native'][1:], rows['executed_action_native'][:-1], atol=1e-6):
            raise ValueError('Correction teacher-query/dispatch causal identities differ')
        records.append(dict(**rows, role='teacher_query', label_semantics=LABEL, split='imitation_train',
            rollout_success=item['rollout_outcome']['success'], id=item['id']))
    return records, original


def plan(config, source):
    seed = _int(config, 'seed', 72041, 0, 1_000_000)
    n = _int(config, 'evaluation_episodes', 6, 1, 100)
    count = _int(config, 'composition_episodes', 2, 1, 50)
    declared = {'acceptance': [seed+10000+i for i in range(n)], 'retention': [seed+20000+i for i in range(n)],
        'composition': [[seed+30000+i, seed+40000+i] for i in range(count)]}
    old = source['result']['seed_plan']
    blocked = set(old['blocked_source_seeds']) | set(old['collection']) | set(old['development'])
    blocked.add(source['result']['source_context']['settings']['seed'])
    selected = declared['acceptance'] + declared['retention'] + [x for pair in declared['composition'] for x in pair]
    if len(selected) != len(set(selected)) or set(selected) & blocked:
        raise ValueError('Skill-cycle development seeds overlap source training/development or each other')
    gate = dict(min_candidate_successes=_int(config, 'min_candidate_successes', (2*n+2)//3, 1, n),
        min_composition_successes=_int(config, 'min_composition_successes', count, 1, count),
        require_no_success_regression_against_retrieved=True, max_retention_success_drop=0,
        declared_before_trials=True, selection_scope='development_only_not_physical_admission')
    return dict(seed=seed, seeds=declared, blocked_source_seeds=sorted(blocked), gate=gate,
        adaptation_steps=_int(config, 'adaptation_steps', 64, 1, 1024), sealed_final_test=False,
        development_observations_fit=False, continuation_on_failed_composition_stage=True)


def contract(asset_hashes):
    return dict(task=TASK, embodiment='rosmaster_m3_pro_cad_fixed_base', cad_mesh_sha256=asset_hashes,
        names=ACTION_NAMES, state_basis='command_estimate', action_basis='absolute_command_endpoint',
        low=LOW.tolist(), high=HIGH.tolist(), active_indices=[0, 1, 2],
        arm_unit='degrees', base_units=['m/s', 'm/s', 'rad/s'], physical_ready=False)


def put_card(store, card):
    matches = [old for old in store.skills() if old['id'] == card['id']]
    if matches and (len(matches) != 1 or matches[0] != card):
        raise ValueError('Immutable skill identity already has a different body')
    if not matches:
        store.add_skill(card)
    return next(old for old in store.skills() if old['id'] == card['id'])


def store_checkpoint(store, source, hashes):
    """Persist real model bytes in the local library, independent of the source job locator."""
    if any(Path(name).name!=name or name in ('.','..') for name in hashes):
        raise ValueError('Invalid stored checkpoint filename')
    signature=hashlib.sha256(canonical(hashes)).hexdigest()
    library=Path(store.root)/'artifacts/skill_library'
    library.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=library/signature
    def verify(directory):
        if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()}!=set(hashes):
            raise ValueError('Stored checkpoint directory members differ')
        verify_source_files({str(directory/name):digest for name,digest in hashes.items()})
    if target.exists():
        verify(target)
        return target
    staged=Path(tempfile.mkdtemp(prefix='.checkpoint-',dir=library))
    try:
        for name,digest in hashes.items():
            original=Path(source)/name
            if original.is_symlink() or sha(original)!=digest:
                raise ValueError('Source checkpoint bytes differ')
            copied=staged/name
            shutil.copyfile(original,copied);copied.chmod(0o600)
            if sha(copied)!=digest or sha(original)!=digest:
                raise ValueError('Checkpoint changed while entering library')
            with copied.open('rb') as stream:
                os.fsync(stream.fileno())
        verify(staged)
        staged.rename(target)
        descriptor=os.open(library,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return target


def retrieve(store, expected_contract, allowed_ids):
    matches = [card for card in store.skills() if card['id'] in allowed_ids and card.get('task') == TASK
        and card.get('action_contract') == expected_contract and card.get('status') == 'simulation_source_evaluated']
    if not matches:
        raise ValueError('No task/embodiment/action-compatible stored learned skill')
    # Metadata retrieval is deterministic; no learned embedding or final-test oracle.
    chosen = sorted(matches, key=lambda card: (-card['evaluation']['success_rate'], card['id']))[0]
    verify_source_files({str(Path(chosen['lineage']['checkpoint_dir'])/name): digest
                         for name, digest in chosen['lineage']['checkpoint_sha256'].items()})
    return chosen


def load_policy(card):
    if importlib.metadata.version('lerobot') != '0.6.1':
        raise ImportError('Skill cycle verifies official LeRobot0.6.1 only')
    from lerobot.policies.act.modeling_act import ACTPolicy
    path = Path(card['lineage']['checkpoint_dir'])
    verify_source_files({str(path/name): digest for name, digest in card['lineage']['checkpoint_sha256'].items()})
    pre = json.loads((path/'explorer_preprocessing.json').read_text())
    if any(pre.get(key)!=value for key,value in preprocessing_config().items()):
        raise ValueError('Stored checkpoint preprocessing/action contract differs')
    policy = ACTPolicy.from_pretrained(path, local_files_only=True, strict=True).to('cpu').eval()
    if set(policy.config.input_features) != {IMAGE_KEY, 'observation.state'} or policy.config.input_features['observation.state'].shape != (9,) or policy.config.output_features['action'].shape != (9,):
        raise ValueError('Stored ACT has incompatible actor inputs/actions')
    if state_digest(policy) != card['lineage']['weights_sha256']:
        raise ValueError('Reloaded skill tensor digest differs')
    return policy


def integration_state(env):
    spec = mujoco.mjtState.mjSTATE_INTEGRATION
    value = np.empty(mujoco.mj_stateSize(env.model, spec))
    mujoco.mj_getState(env.model, env.data, value, spec)
    return value


def next_goal_without_reset(env, seed):
    """Only reference-data FK and target/task bookkeeping change; active state stays intact."""
    before, command, stamp = integration_state(env), env.command.copy(), float(env.data.time)
    rng, target = np.random.default_rng(seed), None
    for _ in range(32):
        candidate = HOME.copy()
        candidate[:3] += rng.uniform([-18, -15, -15], [18, 15, 15])
        env._pose(env.reference, candidate)  # Resetting separate reference MjData does not reset active physics.
        proposed = env.reference.site_xpos[env.site_id].copy()
        if np.linalg.norm(env.data.site_xpos[env.site_id] - proposed) >= .04:
            target = proposed
            break
    if target is None:
        raise RuntimeError('Cannot sample a nontrivial second reachable goal')
    env.goal = target
    env.model.site_pos[env.target_id] = target
    mujoco.mj_forward(env.model, env.data)
    if not np.array_equal(before, integration_state(env)) or not np.array_equal(command, env.command) or env.data.time != stamp:
        raise RuntimeError('Stage transition changed persistent integration state')
    env.steps, env.hold, env.last_distance = 0, 0, env.distance()
    return dict(goal_seed=seed, goal_xyz=target.tolist(), simulator_time_s=stamp,
        integration_state_unchanged=True, command_unchanged=True, physics_reset=False,
        state_before_sha256=hashlib.sha256(before.tobytes()).hexdigest(),
        state_after_sha256=hashlib.sha256(integration_state(env).tobytes()).hexdigest())


def _stage(policy, env, configured, path):
    policy.reset()
    rows = {name: [] for name in ('rgb', 'state_command9', 'requested_action_normalized', 'dispatched_command9',
                                  'simulator_time_s', 'next_simulator_time_s', 'evaluator_qpos')}
    done, rewards, initial = False, [], env.distance()
    while not done:
        image = np.asarray(Image.fromarray(env.render()).resize((configured['image_size'], configured['image_size'])))
        command = env.command.copy()
        batch = {IMAGE_KEY: prepare_rgb(image[None]),
            'observation.state': torch.from_numpy((2*(command-LOW)/(HIGH-LOW)-1)[None])}
        with torch.inference_mode():
            output = policy.select_action(batch)[0].detach().cpu().numpy()
        if output.shape != (9,) or not np.isfinite(output).all():
            raise ValueError('Composition policy produced an invalid endpoint')
        rows['rgb'].append(image); rows['state_command9'].append(command)
        rows['requested_action_normalized'].append(output.copy()); rows['simulator_time_s'].append(float(env.data.time))
        rows['evaluator_qpos'].append(env.data.qpos.copy())
        native = (np.clip(output,-1,1)+1)*.5*(HIGH-LOW)+LOW
        _, reward, terminal, truncated, info = env.step_command(native)
        rows['dispatched_command9'].append(env.command.copy()); rows['next_simulator_time_s'].append(float(env.data.time))
        rewards.append(reward); done = terminal or truncated
    np.savez_compressed(path, **{name: np.asarray(values) for name, values in rows.items()})
    return dict(initial_distance_m=initial, **info, **{'return':float(sum(rewards))},
        trace_file=path.name, trace_sha256=sha(path), actual_inference_steps=len(rewards), actor_privileged_inputs=False)


def composition(first, second, env, seeds, configured, folder, preview):
    folder.mkdir(parents=True,exist_ok=True)
    episodes = []
    for index, pair in enumerate(seeds):
        env.reset(seed=pair[0])  # Exactly one active-physics reset for the whole two-stage episode.
        stage0 = _stage(first, env, configured, folder/f'episode_{index:03d}_stage0.npz')
        end_time, end_state, end_command = float(env.data.time), integration_state(env), env.command.copy()
        transition = next_goal_without_reset(env, pair[1])
        assert np.array_equal(end_state,integration_state(env)) and np.array_equal(end_command,env.command)
        stage1 = _stage(second, env, configured, folder/f'episode_{index:03d}_stage1.npz')
        with np.load(folder/stage0['trace_file'],allow_pickle=False) as left, np.load(folder/stage1['trace_file'],allow_pickle=False) as right:
            if not np.array_equal(left['dispatched_command9'][-1],right['state_command9'][0]) or left['next_simulator_time_s'][-1]!=right['simulator_time_s'][0]:
                raise RuntimeError('Saved composition traces do not preserve stage-boundary causality')
        episodes.append(dict(goal_seeds=pair, stages=[stage0,stage1], transition=transition,
            active_physics_resets=1, stage_boundary_time_s=end_time,
            saved_trace_boundary_causality_verified=True,
            success=bool(stage0['success'] and stage1['success']), independent_from_training_episodes=True))
        write_frame(env,preview,'Actual two-stage MuJoCo composition; physics continuous across target change')
    summary=dict(episodes=episodes,n=len(episodes),successes=sum(x['success'] for x in episodes),
        success_rate=sum(x['success'] for x in episodes)/len(episodes), evaluation_domain='simulation',
        split_role='engineering_development_composition', sealed_final_test=False, physical_run_ids=[],
        stage_continuation_on_failure=True, semantic_scope='two_visible_reaches_not_grasp_or_cloth')
    write_json(folder/'summary.json',summary)
    return summary


def acceptance(gate, incumbent, candidate, retention_before, retention_after, composed):
    measured = all(isinstance(value,dict) and type(value.get('successes')) is int
                   for value in (incumbent,candidate,retention_before,retention_after,composed))
    if not measured:
        return dict(accepted=False,conditions=None,gate=gate,missing_measurements=True,
                    physical_ready=False,sealed_final_test=False)
    conditions=dict(candidate_min_successes=candidate['successes']>=gate['min_candidate_successes'],
        candidate_no_regression=candidate['successes']>=incumbent['successes'],
        incumbent_retention=retention_after['successes']>=retention_before['successes']-gate['max_retention_success_drop'],
        composition_min_successes=composed['successes']>=gate['min_composition_successes'])
    return dict(accepted=all(conditions.values()),conditions=conditions,gate=gate,
        physical_ready=False,sealed_final_test=False,
        retention_scope='immutable_incumbent_checkpoint_reexecuted_after_candidate_adaptation_not_single_network_forgetting')


def run_skill_cycle(store, job):
    config=job.get('config') or {}
    source=source_dagger(store,config)
    declared=plan(config,source)
    source_settings=source['result']['source_context']['settings']
    root=Path(__file__).resolve().parents[3]
    assets=resolve_mesh_dir(config.get('mesh_dir') or os.environ.get('EXPLORER_SIM_MESH_DIR') or root/'_registry/private/assets/m3_reference/meshes')
    asset_hashes={p.name:sha(p) for p in sorted(assets.glob('*.STL'))}
    if asset_hashes!=source['result']['source_context']['cad_mesh_sha256']:
        raise ValueError('Skill-cycle embodiment CAD differs from learned source')
    identity=job['id']
    if not isinstance(identity,str) or Path(identity).name!=identity or identity in ('.','..'):
        raise ValueError('Invalid skill-cycle job identity')
    folder=(Path(store.root)/'jobs'/identity).resolve()
    if any(Path(name).is_relative_to(folder) for name in source['checked']):
        raise ValueError('Skill-cycle output would overwrite source')
    context=dict(source_result_sha256=sha(source['path']),source_files_sha256=source['checked'],plan=declared,
        skill_cycle_source_sha256=sha(Path(__file__)),student_source_sha256=sha(Path(__file__).with_name('student.py')))
    saved=folder/'result.json'
    if saved.is_file():
        result=json.loads(saved.read_text())
        if result['context']!=context:
            raise ValueError('Completed skill cycle belongs to different source/plan/code')
        verify_source_files({str(folder/name):digest for name,digest in result['artifact_sha256'].items()})
        for card in result['skill_cards']:
            put_card(store,card)
            verify_source_files({str(Path(card['lineage']['checkpoint_dir'])/name):digest
                                 for name,digest in card['lineage']['checkpoint_sha256'].items()})
        return result
    attempt=folder/'attempts'/uuid.uuid4().hex
    attempt.mkdir(parents=True)
    write_json(attempt/'provenance.json',dict(context=context,created_wall_utc=utc(),method_ids=METHODS,
        physical_io=False,gate_declared_before_trials=declared['gate'],
        interrupted_attempts_retained_at_original_paths=True,actor_inputs=['RGB','command_estimate9']))
    action_contract=contract(asset_hashes)
    cards=[]
    source_prefix='sim-reach-'+sha(source['path'])[:16]
    for label,training,evaluation,directory in [('before','initial_training','before','round0_expert'),('after','aggregated_training','after','round1_aggregated')]:
        learned=source['result'][training]
        stored_checkpoint=store_checkpoint(store,source['path'].parent/directory/'student_act',learned['checkpoint_sha256'])
        card=dict(id=source_prefix+'-'+label,task=TASK,source_type='sim_dagger_act',status='simulation_source_evaluated',
            action_contract=action_contract,evaluation=source['result'][evaluation],
            lineage=dict(source_result=str(source['path']),source_result_sha256=sha(source['path']),
                checkpoint_dir=str(stored_checkpoint),checkpoint_sha256=learned['checkpoint_sha256'],
                weights_sha256=learned['weights_after_sha256'],method_ids=METHODS),
            limitations=['Development-only fixed-base CAD reach; no physical readiness or cloth/grasp qualification'])
        cards.append(put_card(store,card))
    selected=retrieve(store,action_contract,{card['id'] for card in cards})
    write_json(attempt/'retrieval.json',dict(selected_skill_id=selected['id'],candidate_ids=[c['id'] for c in cards],
        contract=action_contract,ranking='source_development_success_rate_then_id',actual_checkpoint_hash_verified=True))
    incumbent=load_policy(selected)
    old_skill=load_policy(next(card for card in cards if card['id'].endswith('-before')))
    records,original=training_records(store,config,source)
    configured=dict(source_settings,device='cpu',seed=declared['seed'],act_steps=declared['adaptation_steps'])
    torch.set_num_threads(4);torch.manual_seed(declared['seed'])
    started=time.monotonic()
    def update(progress,phase):
        store.update_job(identity,progress=progress,result=dict(phase=phase,source='actual_mujoco_skill_cycle',physical_io=False))
    env=ReachEnv(assets,configured['horizon'])
    try:
        if model_identity(env.xml)!=model_identity((source['path'].parent/'model.xml').read_text()):
            raise ValueError('Skill-cycle model differs from source learned model')
        incumbent_eval,_,_=rollout_student(incumbent,env,declared['seeds']['acceptance'],configured,attempt/'incumbent_acceptance')
        retention_before,_,_=rollout_student(incumbent,env,declared['seeds']['retention'],configured,attempt/'retention_reference')
        update(.10,'stored_retrieved_and_executed_real_incumbent')
        # A separately loaded copy: adaptation must never mutate the incumbent used for retention.
        adapting=load_policy(selected)
        candidate,adaptation=_train_round(records,configured,attempt/'adaptation',update,policy=adapting,progress_range=(.15,.45))
        if not adaptation['weights_changed']:
            raise RuntimeError('Retrieved policy did not actually learn during adaptation')
        pending=dict(id='sim-cycle-'+identity+'-'+attempt.name+'-pending',task=TASK,source_type='sim_adapted_act',
            status='simulation_candidate_pending',action_contract=action_contract,
            evaluation=dict(evaluation_domain='not_yet_evaluated',physical_run_ids=[],sealed_final_test=False),
            lineage=dict(parent_skill_id=selected['id'],source_result_sha256=sha(source['path']),
                checkpoint_dir=adaptation['checkpoint_dir'],checkpoint_sha256=adaptation['checkpoint_sha256'],
                weights_sha256=adaptation['weights_after_sha256'],actual_optimizer_steps=adaptation['optimizer_steps'],method_ids=METHODS),
            limitations=['Candidate pending independent development acceptance; physical_ready=false'])
        put_card(store,pending);cards.append(pending)
        candidate=load_policy(next(c for c in store.skills() if c['id']==pending['id']))
        candidate_eval,_,_=rollout_student(candidate,env,declared['seeds']['acceptance'],configured,attempt/'candidate_acceptance')
        update(.60,'new_candidate_independently_executed')
        composed=composition(old_skill,candidate,env,declared['seeds']['composition'],configured,attempt/'composition',folder)
        update(.80,'two_real_stages_executed_without_physics_reset')
        incumbent=load_policy(next(c for c in store.skills() if c['id']==selected['id']))
        retention_after,_,_=rollout_student(incumbent,env,declared['seeds']['retention'],configured,attempt/'retention_after')
        decision=acceptance(declared['gate'],incumbent_eval,candidate_eval,retention_before,retention_after,composed)
        final=dict(pending,id=pending['id'].removesuffix('-pending')+'-evaluated',
            status='simulation_development_accepted' if decision['accepted'] else 'simulation_candidate_rejected',
            evaluation=candidate_eval,lineage=dict(pending['lineage'],pending_card_id=pending['id'],acceptance=decision))
        put_card(store,final);cards.append(final)
        write_frame(env,folder,'Actual incumbent retention rerun after learned candidate adaptation')
        verify_source_files(source['checked']);verify_source_files(original['checked'])
        result=dict(task='executed_simulation_skill_cycle',status='completed_engineering_development_cycle',
            context=context,method_ids=METHODS,physical_io=False,sealed_final_test=False,
            selected_skill_id=selected['id'],skill_cards=cards,adaptation=adaptation,
            incumbent_acceptance=incumbent_eval,candidate_acceptance=candidate_eval,
            retention_before=retention_before,retention_after=retention_after,composition=composed,
            acceptance=decision,source_originals_unchanged=True,artifacts_dir=str(folder),attempt_dir=str(attempt),
            storage_retrieval_backend='actual_Store_SQLite_skills_table',
            adaptation_training_roles=['successful_original_expert','source_dagger_counterfactual_teacher_queries'],
            teacher_query_recovery_executed=False,elapsed_s=time.monotonic()-started,
            limitations=['Development-only engineering route; no H5, sealed, grasp, cloth or physical qualification',
                'Retrieval is metadata filtering/ranking, not learned semantic retrieval',
                'Retention tests stored incumbent version; not a test of forgetting inside one overwritten network',
                'Composition continues stage2 after stage1 failure to measure both stages; overall success requires both'],**framework_info())
        write_json(attempt/'outcomes.json',{key:result[key] for key in ('adaptation','incumbent_acceptance','candidate_acceptance','retention_before','retention_after','composition','acceptance')})
        files=[p for p in attempt.rglob('*') if p.is_file()]+[folder/'frame.jpg',folder/'frame.json']
        result['artifact_sha256']={str(p.relative_to(folder)):sha(p) for p in files}
        write_json(saved,result);update(.99,'actual_skill_cycle_accepted' if decision['accepted'] else 'actual_skill_cycle_candidate_rejected')
        return result
    finally:
        env.close()
