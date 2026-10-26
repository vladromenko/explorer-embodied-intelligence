"""DAGGER_ROSS_2011 / EXPLORER_CAD_MUJOCO / ENGINEERING_GLUE.

Privileged feedback placement queries are distinct from learner dispatches.
Only an independently qualified simulation teacher supplies correction labels;
learned evaluations never call that teacher. No hardware or visual claim.
"""
from __future__ import annotations

import hashlib
import fcntl
import importlib.metadata
import json
import time
import uuid
from pathlib import Path

import mujoco
import numpy as np
import torch
from stable_baselines3 import PPO

from legacy.simulator.explorer_model import servo_to_joint
from .manipulation import raw_policy_action, record_rollout, replay_trajectory
from .manipulation_curriculum import normalized_dispatch_target, load_training_traces, warmstart_actor
from .manipulation_env import HIGH, LOW, ManipulationEnv
from .pilot import sha, utc, write_json
from legacy.simulator.explorer_model import resolve_mesh_dir

LABEL_SEMANTICS = 'counterfactual_current_state_feedback_teacher_endpoint_not_executed'


def _geometric_jaw(env, command, scratch):
    scratch.qpos[:] = env.data.qpos
    q = servo_to_joint(command)
    scratch.qpos[env.qids] = q
    for name,multiplier in (('rlink2_Joint',1),('llink1_Joint',1),('llink2_Joint',-1),('rlink3_Joint',-1),('llink3_Joint',1)):
        scratch.qpos[env.model.joint(name).qposadr[0]] = multiplier*q[5]
    mujoco.mj_kinematics(env.model,scratch)
    return env.jaw_center(scratch).copy()


def feedback_placement_query(env):
    """Read current physics only; never advance, attach or teleport the object."""
    points, jaw = env.points(), env.jaw_center()
    center = points.mean(0)
    stage = env.evaluator.result()
    inside = bool(np.all(np.abs(points[:,:2]-env.goal[:2]) < env.half-.008))
    held = bool(stage['carried_above_target'] and np.all(np.asarray(stage['final'].get('pad_force_N',[0,0])) > .0001))
    opening = bool(env.command[5] < 160)
    release_zone = float(points[:,2].min()) <= env.goal[2]+env.rim+.035
    if not stage['carried_above_target']:
        return dict(valid=False,reason='Placement teacher applies only after an observed carried hold',label_semantics=LABEL_SEMANTICS)
    if not held and not (inside and opening and release_zone):
        return dict(valid=False,reason='Independent current holding evidence is missing outside release/settle',label_semantics=LABEL_SEMANTICS)
    target = env.command.copy()
    if inside and opening and (not held or release_zone):
        phase = 'feedback_release_settle'
        target[5] = 30
        desired_object = center.copy()
    else:
        # Centre the whole finite object before lowering. Pose correction is
        # expressed relative to its actually measured jaw offset, not a raw
        # source joint trajectory or a hidden desired policy phase.
        desired_object = np.r_[env.goal[:2], max(.20,env.rim+.09)]
        phase = 'feedback_carry'
        if inside:
            desired_object[2] = env.goal[2]+env.rim+.021
            phase = 'feedback_lower'
        at_release_height = float(points[:,2].min()) <= env.goal[2]+env.rim+.024
        if inside and at_release_height and float(np.max(env.speeds())) < .05:
            phase = 'feedback_release_settle';target[5] = 30
        else:
            target[5] = max(170.,env.command[5])
            desired_jaw = desired_object-(center-jaw)
            error = desired_jaw-jaw
            scratch = mujoco.MjData(env.model)
            baseline = _geometric_jaw(env,env.command,scratch)
            columns = []
            for axis in range(4):
                perturbed = env.command.copy()
                direction = .05 if perturbed[axis]+.05 <= HIGH[axis] else -.05
                perturbed[axis] += direction
                columns.append((_geometric_jaw(env,perturbed,scratch)-baseline)/direction)
            jacobian = np.column_stack(columns)
            delta = jacobian.T@np.linalg.solve(jacobian@jacobian.T+1e-6*np.eye(3),.45*error)
            scale = max(1.,float(np.max(abs(delta)/(env.velocity[:4]*env.period*.7))))
            target[:4] += delta/scale
    raw_delta = (target-env.command)/(env.velocity*env.period)
    endpoint = np.clip(env.command+np.clip(raw_delta,-1,1)*env.velocity*env.period,LOW,HIGH)
    label = normalized_dispatch_target(env.command,endpoint,env.velocity,env.period)
    return dict(valid=True,phase=phase,label_semantics=LABEL_SEMANTICS,
        counterfactual_endpoint6=endpoint.tolist(),counterfactual_action6=label.tolist(),
        raw_teacher_delta6=raw_delta.tolist(),measured_object_xyz_m=center.tolist(),
        desired_object_xyz_m=desired_object.tolist(),current_independent_held=held,
        source_sim_time_s=float(env.data.time),counterfactual_was_dispatched=False,
        observer_basis='privileged_simulation_contact_and_geometry')


def restore_learner_prefix(env, episode_folder, pre_action_index):
    """Replay actual learner commands, including evaluator history, to a native state."""
    folder = Path(episode_folder)
    result = json.loads((folder/'result.json').read_text())
    if sha(folder/'trajectory.npz') != result['trajectory_sha256']:
        raise ValueError('Original learner trajectory checksum mismatch')
    with np.load(folder/'trajectory.npz',allow_pickle=False) as data:
        if type(pre_action_index) is not int or not 0 <= pre_action_index < len(data['state_command9']):
            raise ValueError('Explicit saved pre-action state index required')
        env.reset(seed=result['seed'])
        for endpoint in data['requested_endpoint6'][:pre_action_index]:
            env.step_command(np.clip(endpoint,LOW,HIGH))
        expected = data['native_integration_state'][pre_action_index]
        difference = float(np.max(abs(env.integration_state()-expected)))
        if difference > 1e-9 or not np.allclose(env.command,data['state_command9'][pre_action_index,:6],atol=1e-9):
            raise ValueError('Restored learner state differs from original native physics')
    return dict(source_seed=result['seed'],pre_action_index=pre_action_index,
        source_trajectory_sha256=result['trajectory_sha256'],max_abs_native_state_error=difference,
        evaluator_history='reexecuted_actual_prefix_not_invented_flags')


def verify_recovery(env, episode_folder, index, folder, *, horizon=400):
    folder = Path(folder);folder.mkdir(parents=True,exist_ok=False)
    restoration = restore_learner_prefix(env,episode_folder,index)
    initial = env.evaluator.result()
    env.horizon = env.steps+horizon
    rows, observations, dispatched_labels, phases, times = [],[],[],[],[]
    native = [env.integration_state()]
    for _ in range(horizon):
        before = env.command.copy()
        query = feedback_placement_query(env)
        if not query['valid']:
            rows.append(dict(query=query,evaluator=env.evaluator.result()))
            break
        observations.append(env.observation().copy());phases.append(query['phase']);times.append(float(env.data.time))
        _,_,terminal,_,info = env.step(query['counterfactual_action6'])
        dispatched = normalized_dispatch_target(before,env.command,env.velocity,env.period)
        dispatched_labels.append(dispatched.copy())
        rows.append(dict(query=query,teacher_was_executed_in_qualification=True,actual_recovery_dispatch6=env.command.tolist(),
            actual_recovery_delta6=dispatched.tolist(),evaluator=info['evaluator']))
        native.append(env.integration_state())
        if terminal or info['unstable']:
            break
    write_json(folder/'recovery-trace.json',rows)
    np.savez_compressed(folder/'recovery-native-states.npz',states=np.asarray(native))
    np.savez_compressed(folder/'recovery-demonstration.npz',privileged_observation36=np.asarray(observations,np.float32),
        executed_feedback_recovery_action6=np.asarray(dispatched_labels,np.float32),phase=np.asarray(phases),pre_action_sim_time_s=np.asarray(times))
    result = dict(restoration=restoration,initial=initial,final=env.evaluator.result(),
        successful_recovery=env.evaluator.result()['success'],steps=len(rows),
        source_role='teacher_qualification_only_not_automatic_training_admission',
        trace_sha256=sha(folder/'recovery-trace.json'),physical_run=False)
    write_json(folder/'recovery-result.json',result)
    return result


def source_bundle(store,config):
    source_job = config.get('source_job_id')
    if bool(source_job) == bool(config.get('source_result_path')):
        raise ValueError('Choose exactly one completed source job or SHA-bound source result')
    if source_job:
        if not isinstance(source_job,str) or Path(source_job).name != source_job:
            raise ValueError('Invalid source job identity')
        job = store.job(source_job)
        if not job or job['kind'] != 'manipulation_curriculum' or job['status'] != 'completed':
            raise ValueError('Source must be a completed BC+PPO full-object job')
        path = Path(store.root)/'jobs'/source_job/'bc-ppo-result.json'
    else:
        path = Path(config['source_result_path']).resolve()
        if sha(path) != config.get('source_result_sha256'):
            raise ValueError('Explicit source-result SHA256 required')
    path = path.resolve()
    result = json.loads(path.read_text())
    if result.get('schema') != 'explorer_BC_warmstarted_PPO_full_object_v1' or result['context']['options']['task'] != 'candy_to_tray' or result.get('privileged_actor') is not True or result.get('bc_reload_action_max_abs_error') != 0:
        raise ValueError('This recovery teacher is qualified for the declared candy BC source only')
    checked = {str(path):sha(path)}
    for name,expected in result['artifact_sha256'].items():
        artifact = (path.parent/name).resolve()
        if not artifact.is_relative_to(path.parent) or not artifact.is_file() or sha(artifact) != expected:
            raise ValueError('Original BC+PPO source artifact hash mismatch')
        checked[str(artifact)] = expected
    attempt = Path(result['actual_attempt']).resolve()
    checkpoint = attempt/'bc_incumbent.zip'
    if not attempt.is_relative_to(path.parent) or sha(checkpoint) != result['bc_checkpoint_sha256']:
        raise ValueError('Original BC incumbent/checkpoint provenance mismatch')
    original_module = next((expected for name,expected in result['context']['source_sha256'].items() if Path(name).name == 'manipulation_curriculum.py'),None)
    if original_module != sha(Path(__file__).with_name('manipulation_curriculum.py')):
        raise ValueError('Matching original teacher preprocessing implementation required')
    return dict(result=result,result_path=path,attempt=attempt,checkpoint=checkpoint,checked=checked)


def seed_plan(options,source):
    count,number = options['collection_episodes'],options['evaluation_episodes']
    collection = options.get('collection_seeds',[options['seed']+2000+i for i in range(count)])
    development = options.get('development_seeds',[options['seed']+10000+i for i in range(number)])
    blocked = set(source['train_seeds'])|set(source['development_seeds'])
    for values,length in ((collection,count),(development,number)):
        if not isinstance(values,list) or len(values) != length or any(type(value) is not int or not 0 <= value < 2**31 for value in values) or len(set(values)) != length:
            raise ValueError('Distinct finite collection/development seed identities required')
    if (set(collection)|set(development))&blocked or set(collection)&set(development):
        raise ValueError('Correction collection/development overlap source or each other')
    return dict(collection=collection,development=development,blocked_source_seeds=sorted(blocked),
        beta=0.,query_training_on_source_development=False,sealed_final_test=False)


def collect_queries(env,episode_folder,folder,*,stride=4,teacher_qualified=False):
    """Reexecute beta=0 learner states; queries never become actual dispatches."""
    folder = Path(folder);folder.mkdir(parents=True,exist_ok=False)
    episode_folder = Path(episode_folder)
    outcome = json.loads((episode_folder/'result.json').read_text())
    if sha(episode_folder/'trajectory.npz') != outcome['trajectory_sha256']:
        raise ValueError('Learner query source checksum mismatch')
    observations,labels,valid,phases,times,metadata,actual,raw = [],[],[],[],[],[],[],[]
    with np.load(episode_folder/'trajectory.npz',allow_pickle=False) as original:
        env.reset(seed=outcome['seed'])
        for index,request in enumerate(original['raw_requested_action6']):
            if np.max(abs(env.integration_state()-original['native_integration_state'][index])) > 1e-9:
                raise ValueError('Teacher query is not at its actual source learner state')
            observation = env.observation().copy()
            query = feedback_placement_query(env) if index%stride == 0 else dict(valid=False,reason='not_sampled',label_semantics=LABEL_SEMANTICS)
            admitted = bool(teacher_qualified and query['valid'])
            observations.append(observation);labels.append(query.get('counterfactual_action6',[0.]*6));valid.append(admitted)
            phases.append(query.get('phase','query_abstained'));times.append(float(env.data.time));metadata.append(query)
            raw.append(request.copy())
            env.step(request)
            actual.append(env.command.copy())
            if not np.allclose(env.command,original['dispatched_endpoint6'][index],atol=1e-9):
                raise ValueError('Counterfactual teacher query changed actual learner execution')
    archive = folder/'teacher-queries.npz'
    np.savez_compressed(archive,privileged_observation36=np.asarray(observations,np.float32),
        counterfactual_teacher_action6=np.asarray(labels,np.float32),teacher_label_valid=np.asarray(valid,bool),
        actual_learner_dispatch6=np.asarray(actual),actual_learner_raw_action6=np.asarray(raw),
        phase=np.asarray(phases),pre_action_sim_time_s=np.asarray(times))
    write_json(folder/'teacher-query-metadata.json',metadata)
    result = dict(seed=outcome['seed'],learner_outcome=outcome['evaluator'],
        source_trajectory_sha256=outcome['trajectory_sha256'],samples=len(observations),
        valid_teacher_queries=sum(valid),source_can_be_failed=True,label_semantics=LABEL_SEMANTICS,
        teacher_qualified=teacher_qualified,teacher_queries_actuated_during_learner_rollout=False,
        teacher_query_native_state_parity=True,sha256=sha(archive),file=archive.name)
    write_json(folder/'query-result.json',result)
    return result


class GatedPlacementPolicy:
    """Two learned actors with an explicit privileged carried-stage selector."""
    def __init__(self,incumbent,placement):
        self.incumbent,self.placement = incumbent,placement

    def predict(self,observation,deterministic=True):
        value = np.asarray(observation,np.float32)
        if value.shape != (36,) or not np.isfinite(value).all():
            raise ValueError('Current named privileged state36 required')
        actor = self.placement if value[35] > .5 else self.incumbent
        return raw_policy_action(actor,value),None


def _settings(config):
    options = {}
    for name,default,low,high in (('seed',13241,0,10**6),('collection_episodes',2,1,10),('evaluation_episodes',2,1,10),('adaptation_steps',4000,1,50000),('horizon',600,200,1200),('query_stride',4,1,20),('recovery_horizon',400,100,1000)):
        value = config.get(name,default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Invalid selective placement '+name)
        options[name] = value
    if config.get('device','cpu') != 'cpu':
        raise ValueError('This feedback qualification uses CPU only')
    for name in ('collection_seeds','development_seeds'):
        if name in config:options[name]=config[name]
    return options


def _summary(episodes):
    return dict(n=len(episodes),successes=sum(row['evaluator']['success'] for row in episodes),
        grasped=sum(row['evaluator']['grasped'] for row in episodes),lifted=sum(row['evaluator']['lifted'] for row in episodes),
        carried=sum(row['evaluator']['carried_above_target'] for row in episodes),episodes=episodes,
        learned_rollout_teacher_calls=0,split='matched_development_not_sealed')


def run_manipulation_selective_repair(store,job,config=None):
    """One honest finite correction round, preserving all source incumbents."""
    request = job.get('config') or {}
    options = _settings(request)
    job_id = job['id']
    if not isinstance(job_id,str) or Path(job_id).name != job_id or job_id in ('.','..'):
        raise ValueError('Invalid selective-repair job identity')
    source = source_bundle(store,request)
    plan = seed_plan(options,source['result'])
    meshes = resolve_mesh_dir(request.get('mesh_dir'))
    own_sources = [Path(__file__),Path(__file__).with_name('manipulation_curriculum.py'),Path(__file__).with_name('manipulation.py'),
        Path(__file__).with_name('manipulation_env.py'),Path(__file__).with_name('pilot.py'),
        Path(__file__).parents[1]/'legacy/simulator/explorer_model.py',Path(__file__).parents[1]/'legacy/simulator/model_config.json',
        Path(__file__).parents[1]/'legacy/config/explorer.urdf',Path(__file__).parents[1]/'legacy/config/explorer.srdf',Path(__file__).parents[1]/'legacy/config/arm-motion.json']
    context = dict(options=options,seed_plan=plan,source_result_sha256=sha(source['result_path']),
        source_bc_checkpoint_sha256=sha(source['checkpoint']),implementation_sha256={str(path):sha(path) for path in own_sources},
        cad_sha256={path.name:sha(path) for path in sorted(meshes.glob('*.STL'))},
        versions={name:importlib.metadata.version(name) for name in ('mujoco','stable-baselines3','torch','numpy')})
    signature = hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
    base = Path(store.root)/'jobs'/job_id;base.mkdir(parents=True,exist_ok=True)
    with (base/'.selective-repair-owner.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise RuntimeError('Selective-repair attempt already active') from error
        completed = base/'selective-repair-result.json'
        if completed.is_file():
            saved = json.loads(completed.read_text())
            if saved['request_signature'] != signature:
                raise ValueError('Completed selective-repair request/source/settings changed')
            for name,expected in saved['artifact_sha256'].items():
                path = (base/name).resolve()
                if not path.is_relative_to(base.resolve()) or sha(path) != expected:
                    raise ValueError('Completed selective-repair artifact checksum mismatch')
            return saved
        attempt = base/'attempts'/uuid.uuid4().hex;attempt.mkdir(parents=True)
        torch.set_num_threads(1)
        env = ManipulationEnv(meshes,'candy_to_tray',options['horizon'])
        started = time.monotonic()
        try:
            snapshots = attempt/'implementation_sources';snapshots.mkdir()
            for path in own_sources:(snapshots/path.name).write_bytes(path.read_bytes())
            (attempt/'model.xml').write_text(env.xml)
            write_json(attempt/'protocol.json',context)
            incumbent = PPO.load(source['checkpoint'],device='cpu')
            collections,recoveries,queries = [],[],[]
            def progress(value,phase):
                store.update_job(job_id,progress=value,result=dict(phase=phase,source='actual_CAD_selective_placement_repair',actual_workers=1,physical_run=False))
            for index,seed in enumerate(plan['collection']):
                env.horizon = options['horizon']
                episode_folder = attempt/'learner_collection'/str(seed)
                outcome = record_rollout(env,episode_folder,seed,policy=incumbent)
                collections.append(outcome)
                rows = json.loads((episode_folder/'substep-summary.json').read_text())
                state_index = next((row['step']+1 for row in rows if row['step']+1 < outcome['n_actions'] and row['evaluator']['carried_above_target'] and min(row['evaluator']['final']['pad_force_N'])>.0001),None)
                qualified = False
                if state_index is not None:
                    recovery = verify_recovery(env,episode_folder,state_index,attempt/'teacher_qualification'/str(seed),horizon=options['recovery_horizon'])
                    recovery['source_collection_seed'] = seed;recoveries.append(recovery)
                    qualified = recovery['successful_recovery']
                env.horizon = options['horizon']
                query = collect_queries(env,episode_folder,attempt/'queries'/str(seed),stride=options['query_stride'],teacher_qualified=qualified)
                queries.append(query)
                progress(.35*(index+1)/len(plan['collection']),'collecting_actual_learner_states_and_qualified_feedback_queries')
            if not any(item['successful_recovery'] for item in recoveries):
                raise ValueError('Feedback recovery teacher not qualified on new collection states; no adaptation allowed')
            original_obs,original_target,original_phase,_ = load_training_traces(source['attempt'],source['result']['expert_demonstrations'],source['result']['development_seeds'])
            keep = original_obs[:,35]>.5
            observations,targets,phases = [original_obs[keep]],[original_target[keep]],[original_phase[keep]]
            records = [dict(role='original_expert_placement_training',samples=int(keep.sum()))]
            for seed,query in zip(plan['collection'],queries):
                path = attempt/'queries'/str(seed)/'teacher-queries.npz'
                with np.load(path,allow_pickle=False) as data:
                    selected = data['teacher_label_valid']
                    if selected.any():
                        observations.append(data['privileged_observation36'][selected]);targets.append(data['counterfactual_teacher_action6'][selected]);phases.append(data['phase'][selected])
                        records.append(dict(role='counterfactual_current_state_teacher_query',seed=seed,samples=int(selected.sum()),label_semantics=LABEL_SEMANTICS))
                recovery = next((item for item in recoveries if item['source_collection_seed']==seed and item['successful_recovery']),None)
                if recovery:
                    with np.load(attempt/'teacher_qualification'/str(seed)/'recovery-demonstration.npz',allow_pickle=False) as data:
                        observations.append(data['privileged_observation36']);targets.append(data['executed_feedback_recovery_action6']);phases.append(data['phase'])
                        records.append(dict(role='executed_successful_feedback_recovery_continuation',seed=seed,samples=len(data['phase'])))
            observations,targets,phases = np.concatenate(observations),np.concatenate(targets),np.concatenate(phases)
            candidate = PPO.load(source['checkpoint'],device='cpu')
            supervision = warmstart_actor(candidate.policy,observations,targets,phases,steps=options['adaptation_steps'],seed=options['seed'],
                progress=lambda value:progress(.35+.3*value,'adapting_learned_placement_actor_from_qualified_queries'))
            candidate.save(attempt/'placement_candidate')
            candidate = PPO.load(attempt/'placement_candidate.zip',device='cpu')
            write_json(attempt/'adaptation-supervision.json',supervision)
            write_json(attempt/'training-records.json',records)
            env.horizon = options['horizon']
            before,after = [],[]
            gated = GatedPlacementPolicy(incumbent,candidate)
            for index,seed in enumerate(plan['development']):
                before.append(record_rollout(env,attempt/'incumbent_development'/str(seed),seed,policy=incumbent))
                after.append(record_rollout(env,attempt/'selective_development'/str(seed),seed,policy=gated))
                progress(.65+.3*(index+1)/len(plan['development']),'independent_learned_full_object_selective_evaluation_no_teacher')
            replay = replay_trajectory(env,attempt/'selective_development'/str(plan['development'][0]))
            write_json(attempt/'selective-native-replay.json',replay)
            before_summary,after_summary = _summary(before),_summary(after)
            incumbent_unchanged = sha(source['checkpoint']) == context['source_bc_checkpoint_sha256']
            accepted = after_summary['successes']==after_summary['n'] and after_summary['successes']>=before_summary['successes'] and replay['within_1e_minus_9'] and incumbent_unchanged
            result = dict(schema='explorer_full_object_selective_DAgger_round_v1',request_signature=signature,created_wall_utc=utc(),actual_attempt=str(attempt),context=context,
                collection=_summary(collections),teacher_recoveries=recoveries,teacher_queries=queries,training_records=records,supervision=supervision,
                incumbent_development=before_summary,selective_development=after_summary,native_replay=replay,
                incumbent_preserved=incumbent_unchanged,development_candidate_accepted=bool(accepted),
                placement_checkpoint_sha256=sha(attempt/'placement_candidate.zip'),elapsed_s=time.monotonic()-started,
                privileged_actor=True,selection='current_observation36_carried_flag_only',pre_carried_policy='unchanged_source_BC_incumbent',
                teacher_calls_during_learned_evaluation=0,physical_run=False,physical_ready=False,hypotheses_status='UNTESTED_HYPOTHESIS',
                method_ids=['DAGGER_ROSS_2011','EXPLORER_CAD_MUJOCO','ENGINEERING_GLUE'],
                limitations=['Single finite privileged simulation correction round, not a visual deployable policy',
                    'Explicit carried-stage composition of two learned actors; no claim against strong published methods',
                    'Model-based teacher qualification is distinct from learned full-task success',
                    'Small matched development scenes; no sealed or physical generalization',
                    'Contact/geometric validity can reject missing holds; ambiguous states are not expert targets'])
            if any(sha(Path(name)) != expected for name,expected in source['checked'].items()) or any(sha(Path(name)) != expected for name,expected in context['implementation_sha256'].items()) or {path.name:sha(path) for path in sorted(meshes.glob('*.STL'))} != context['cad_sha256']:
                raise ValueError('Original source, teacher implementation or assets changed during execution; attempt retained')
            result['artifact_sha256'] = {str(path.relative_to(base)):sha(path) for path in attempt.rglob('*') if path.is_file()}
            write_json(completed,result)
            progress(1,'complete_qualified_selective_full_object_development_round')
            return result
        finally:env.close()
