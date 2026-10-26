"""PPO_SB3_2_9 / EXPLORER_CAD_MUJOCO / ENGINEERING_GLUE: BC-warmstarted PPO.

Scripted contact demonstrations supervise a privileged SB3 actor before real
PPO updates. Learned rollouts never invoke the script. This is a fixed-base
full-object teacher baseline, not visual policy mastery or a physical skill.
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
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

from legacy.simulator.explorer_model import resolve_mesh_dir
from .manipulation import raw_policy_action, record_rollout, replay_trajectory, scripted_actions
from .manipulation_env import ManipulationEnv, TASKS
from .pilot import sha, utc, write_json


class StandardizedTeacherFeatures(BaseFeaturesExtractor):
    """Training-only statistics saved in the maintained SB3 policy checkpoint."""
    def __init__(self, observation_space, mean, std):
        super().__init__(observation_space, features_dim=36)
        center, scale = np.asarray(mean,float), np.asarray(std,float)
        if center.shape != (36,) or scale.shape != (36,) or not np.isfinite([center,scale]).all() or np.any(scale <= 0):
            raise ValueError('Explicit finite privileged-observation training statistics required')
        self.register_buffer('center',torch.tensor(center,dtype=torch.float32))
        self.register_buffer('scale',torch.tensor(scale,dtype=torch.float32))

    def forward(self, observations):
        return (observations-self.center)/self.scale


def normalized_dispatch_target(before, after, velocity, period):
    before, after, speed = [np.asarray(value,float) for value in (before,after,velocity)]
    if any(value.shape != (6,) or not np.isfinite(value).all() for value in (before,after,speed)) or np.any(speed <= 0) or not np.isfinite(period) or period <= 0:
        raise ValueError('Named finite endpoint6 and declared velocity/period required')
    target = (after-before)/(speed*period)
    if np.any(np.abs(target) > 1+1e-5):
        raise ValueError('Dispatched deltas violate the simulated executor rate contract')
    return np.clip(target,-1,1).astype(np.float32)


def phase_weights(phases):
    values = np.asarray(phases)
    if values.ndim != 1 or not len(values) or any(not isinstance(value,str) or not value for value in values.tolist()):
        raise ValueError('Every supervised row needs its recorded expert phase')
    names, inverse, counts = np.unique(values,return_inverse=True,return_counts=True)
    weights = 1/counts[inverse].astype(float)
    return weights/weights.sum()


def collect_expert(env, folder, seed, *, record_rgb=True):
    """Record real expert dispatches and original requests as different fields."""
    folder.mkdir(parents=True,exist_ok=False)
    observation,_ = env.reset(seed=seed)
    observations, targets, raw_requests, dispatched, phases, frames, times = [],[],[],[],[],[],[]
    native = [env.integration_state()]
    done = False
    for requested in scripted_actions(env):
        before = env.command.copy()
        observations.append(observation.copy())
        phases.append(env.phase)
        raw_requests.append((np.asarray(requested)-before)/(env.velocity*env.period))
        times.append(float(env.data.time))
        if record_rgb:
            frames.append(np.asarray(Image.fromarray(env.render()).resize((64,64)),dtype=np.uint8))
        observation,_,terminal,_,info = env.step_command(requested)
        targets.append(normalized_dispatch_target(before,env.command,env.velocity,env.period))
        dispatched.append(env.command.copy())
        native.append(env.integration_state())
        done = bool(terminal or info['unstable'])
        if done:
            break
    archive = folder/'expert-trace.npz'
    np.savez_compressed(archive,privileged_observation36=np.asarray(observations,np.float32),
        actual_dispatched_action6=np.asarray(targets,np.float32),raw_requested_delta6=np.asarray(raw_requests),
        dispatched_endpoint6=np.asarray(dispatched),phase=np.asarray(phases),pre_action_sim_time_s=np.asarray(times),
        native_integration_state=np.asarray(native),rgb=np.asarray(frames,np.uint8))
    result = dict(seed=seed,source='scripted_contact_ik_expert_not_learned_policy',
        steps=len(targets),evaluator=env.evaluator.result(),phase_counts={str(name):int(sum(np.asarray(phases)==name)) for name in np.unique(phases)},
        file=archive.name,sha256=sha(archive),physical_run=False,frames=len(frames),
        labels='actual_dispatched_delta6_divided_by_declared_velocity_times_period',
        observations='privileged_simulation_state36_not_visual_student_input')
    write_json(folder/'expert-result.json',result)
    return result


def load_training_traces(folder, descriptors, development_seeds):
    observations, targets, phases, admitted = [],[],[],[]
    for descriptor in descriptors:
        if descriptor['seed'] in development_seeds:
            raise ValueError('Development episodes cannot enter warmstart supervision')
        path = (Path(folder)/descriptor['relative_file']).resolve()
        if not path.is_relative_to(Path(folder).resolve()) or sha(path) != descriptor['sha256']:
            raise ValueError('Expert trace path/hash differs from its immutable descriptor')
        if descriptor['evaluator']['success'] is True:
            with np.load(path,allow_pickle=False) as data:
                observation, target, phase = data['privileged_observation36'],data['actual_dispatched_action6'],data['phase']
                if observation.ndim != 2 or observation.shape[1] != 36 or target.shape != (len(observation),6) or phase.shape != (len(observation),) or not len(observation) or not np.isfinite(observation).all() or not np.isfinite(target).all() or np.any(abs(target)>1+1e-5):
                    raise ValueError('Invalid synchronized expert observation/dispatch trace')
                observations.append(observation);targets.append(target);phases.append(phase);admitted.append(descriptor['seed'])
    if not admitted:
        raise ValueError('No independently complete scripted expert trace for warmstart')
    return np.concatenate(observations),np.concatenate(targets),np.concatenate(phases),admitted


def warmstart_actor(policy, observations, targets, phases, *, steps, seed, batch_size=128, progress=None):
    """Actor-only supervised optimization; no critic loss or scripted rollouts."""
    if type(steps) is not int or steps < 1 or type(batch_size) is not int or batch_size < 1:
        raise ValueError('Positive supervised optimization budget required')
    observations, targets = np.asarray(observations,np.float32),np.asarray(targets,np.float32)
    if observations.ndim != 2 or observations.shape[1] != 36 or targets.shape != (len(observations),6) or not np.isfinite(observations).all() or not np.isfinite(targets).all():
        raise ValueError('Finite named privileged observations and endpoint deltas required')
    weights = phase_weights(phases)
    if len(weights) != len(observations):
        raise ValueError('Expert phases and targets differ in length')
    parameters = [*policy.mlp_extractor.policy_net.parameters(),*policy.action_net.parameters()]
    optimizer = torch.optim.Adam(parameters,lr=5e-4)
    rng = np.random.default_rng(seed)
    before = {name:value.detach().clone() for name,value in policy.state_dict().items()}
    losses = []
    policy.set_training_mode(True)
    for index in range(steps):
        selected = rng.choice(len(observations),size=batch_size,replace=True,p=weights)
        batch = torch.as_tensor(observations[selected],device=policy.device)
        desired = torch.as_tensor(targets[selected],device=policy.device)
        mean = policy.get_distribution(batch).distribution.mean
        loss = torch.mean((mean-desired)**2)
        if not torch.isfinite(loss):
            raise ValueError('Nonfinite supervised actor objective')
        optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(parameters,1.);optimizer.step()
        losses.append(float(loss.detach()))
        if progress and (index+1)%100 == 0:
            progress((index+1)/steps)
    policy.set_training_mode(False)
    changed = [name for name,value in policy.state_dict().items() if not torch.equal(before[name],value)]
    return dict(algorithm='phase_balanced_actor_behavior_cloning_before_PPO',optimizer_steps=steps,
        losses=losses,first_loss=losses[0],last_loss=losses[-1],changed_parameter_names=changed,
        critic_parameters_changed=any('value_net' in name for name in changed),
        samples=len(observations),phase_weight_mass={str(name):float(weights[np.asarray(phases)==name].sum()) for name in np.unique(phases)},
        actual_dispatched_targets=True,physical_run=False)


def parameters(config):
    config = config or {}
    result = {}
    for name,default,low,high in (('seed',8421,0,10**6),('demonstration_episodes',3,1,20),('bc_steps',2000,1,50000),('ppo_steps',2048,128,100000),('evaluation_episodes',2,1,20),('horizon',600,100,1200)):
        value = config.get(name,default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Invalid BC+PPO '+name)
        result[name] = value
    if config.get('task','candy_to_tray') not in TASKS or config.get('device','cpu') != 'cpu':
        raise ValueError('Supported full-object task and CPU required')
    result['task'] = config.get('task','candy_to_tray')
    return result


def _evaluation(env, policy, seeds, folder):
    episodes = [record_rollout(env,folder/str(seed),seed,policy=policy) for seed in seeds]
    return dict(n=len(episodes),successes=sum(row['evaluator']['success'] for row in episodes),
        grasped=sum(row['evaluator']['grasped'] for row in episodes),lifted=sum(row['evaluator']['lifted'] for row in episodes),
        carried=sum(row['evaluator']['carried_above_target'] for row in episodes),episodes=episodes,
        split='matched_development_not_sealed',scripted_actions_during_evaluation=False)


def run_manipulation_curriculum(store,job,config=None):
    options = parameters(job.get('config'))
    job_id = job['id']
    if not isinstance(job_id,str) or Path(job_id).name != job_id or job_id in ('.','..'):
        raise ValueError('Invalid BC+PPO job identity')
    base = Path(store.root)/'jobs'/job_id;base.mkdir(parents=True,exist_ok=True)
    meshes = resolve_mesh_dir((job.get('config') or {}).get('mesh_dir'))
    sources = [Path(__file__),Path(__file__).with_name('manipulation.py'),Path(__file__).with_name('manipulation_env.py'),
        Path(__file__).with_name('pilot.py'),Path(__file__).parents[1]/'legacy/simulator/explorer_model.py',
        Path(__file__).parents[1]/'legacy/simulator/model_config.json',Path(__file__).parents[1]/'legacy/config/explorer.urdf',
        Path(__file__).parents[1]/'legacy/config/explorer.srdf',Path(__file__).parents[1]/'legacy/config/arm-motion.json']
    identity = dict(options=options,source_sha256={str(path):sha(path) for path in sources},
        cad_mesh_sha256={path.name:sha(path) for path in sorted(meshes.glob('*.STL'))},
        versions={name:importlib.metadata.version(name) for name in ('mujoco','numpy','stable-baselines3','torch')})
    signature = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    with (base/'.bc-ppo-owner.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise RuntimeError('BC+PPO attempt already active') from error
        if (base/'bc-ppo-result.json').is_file():
            result = json.loads((base/'bc-ppo-result.json').read_text())
            if result['request_signature'] != signature:
                raise ValueError('Completed BC+PPO implementation/settings/assets differ')
            for name,expected in result['artifact_sha256'].items():
                path = (base/name).resolve()
                if not path.is_relative_to(base.resolve()) or sha(path) != expected:
                    raise ValueError('Completed BC+PPO artifact checksum mismatch')
            return result
        attempt = base/'attempts'/uuid.uuid4().hex;attempt.mkdir(parents=True)
        torch.set_num_threads(1)
        torch.manual_seed(options['seed'])
        env = ManipulationEnv(meshes,options['task'],options['horizon'])
        started = time.monotonic()
        try:
            (attempt/'model.xml').write_text(env.xml)
            snapshot = attempt/'implementation_sources';snapshot.mkdir()
            for source in sources:(snapshot/source.name).write_bytes(source.read_bytes())
            train_seeds = list(range(options['seed'],options['seed']+options['demonstration_episodes']))
            development = list(range(options['seed']+10000,options['seed']+10000+options['evaluation_episodes']))
            write_json(attempt/'protocol.json',dict(train_seeds=train_seeds,development_seeds=development,identity=identity,
                success_predicate='independent_full_contact_grasp_lift_carry_release_settle',physical_run=False))
            def progress(value,phase):
                store.update_job(job_id,progress=value,result=dict(phase=phase,source='actual_CAD_BC_warmstart_PPO',physical_run=False,actual_workers=1))
            descriptors = []
            for index,seed in enumerate(train_seeds):
                descriptor = collect_expert(env,attempt/'expert_demonstrations'/str(seed),seed)
                descriptor['relative_file'] = str(Path('expert_demonstrations')/str(seed)/descriptor['file'])
                descriptors.append(descriptor)
                progress(.2*(index+1)/len(train_seeds),'collecting_scripted_contact_expert_separate_from_learning')
            write_json(attempt/'expert-manifest.json',descriptors)
            observations,targets,phases,admitted = load_training_traces(attempt,descriptors,development)
            mean,std = observations.mean(0),np.maximum(observations.std(0),.03)
            actor = PPO('MlpPolicy',env,n_steps=128,batch_size=64,n_epochs=3,learning_rate=1e-4,
                clip_range=.1,policy_kwargs=dict(net_arch=[128,128],log_std_init=-2.5,
                    features_extractor_class=StandardizedTeacherFeatures,
                    features_extractor_kwargs=dict(mean=mean.tolist(),std=std.tolist())),seed=options['seed'],device='cpu',verbose=0)
            supervision = warmstart_actor(actor.policy,observations,targets,phases,steps=options['bc_steps'],seed=options['seed'],
                progress=lambda value:progress(.2+.25*value,'supervised_phase_balanced_privileged_actor_warmstart'))
            write_json(attempt/'actor-supervision.json',supervision)
            actor.save(attempt/'bc_incumbent')
            bc_hash = sha(attempt/'bc_incumbent.zip')
            loaded_bc = PPO.load(attempt/'bc_incumbent.zip',device='cpu')
            observation,_ = env.reset(seed=development[0])
            reload_error = float(np.max(abs(raw_policy_action(actor,observation)-raw_policy_action(loaded_bc,observation))))
            before = _evaluation(env,loaded_bc,development,attempt/'bc_development')
            progress(.55,'independent_full_object_BC_baseline_evaluated')
            teacher = PPO.load(attempt/'bc_incumbent.zip',env=env,device='cpu')
            initial = {name:value.detach().clone() for name,value in teacher.policy.state_dict().items()}
            optimizer_steps = 0
            def counted_step(optimizer,args,kwargs):
                nonlocal optimizer_steps
                optimizer_steps += 1
            hook = teacher.policy.optimizer.register_step_post_hook(counted_step)
            class TrainingLog(BaseCallback):
                def _on_step(self):
                    info = self.locals['infos'][0]
                    stream.write(json.dumps(dict(timestep=self.num_timesteps,
                        raw_sampled_action6=self.locals['actions'][0].tolist(),
                        sb3_clipped_action6=self.locals['clipped_actions'][0].tolist(),
                        requested_endpoint6=info['requested_endpoint6'],dispatched_endpoint6=info['command9'][:6],
                        post_action_sim_time_s=info['sim_time_s']),allow_nan=False)+'\n')
                    if self.num_timesteps%128 == 0:
                        stream.flush();progress(.55+.25*min(1,self.num_timesteps/options['ppo_steps']),'genuine_PPO_updates_after_BC_no_script')
                    return True
            try:
                with (attempt/'ppo-training-requests.jsonl').open('w') as stream:
                    teacher.learn(total_timesteps=options['ppo_steps'],callback=TrainingLog())
            finally:hook.remove()
            teacher.save(attempt/'bc_plus_ppo_candidate')
            loaded = PPO.load(attempt/'bc_plus_ppo_candidate.zip',device='cpu')
            observation,_ = env.reset(seed=development[0])
            ppo_reload_error = float(np.max(abs(raw_policy_action(teacher,observation)-raw_policy_action(loaded,observation))))
            after = _evaluation(env,loaded,development,attempt/'ppo_development')
            ppo_replay = replay_trajectory(env,attempt/'ppo_development'/str(development[0]))
            write_json(attempt/'ppo-native-replay.json',ppo_replay)
            incumbent_preserved = sha(attempt/'bc_incumbent.zip') == bc_hash
            promoted = after['successes'] == after['n'] and after['successes'] >= before['successes'] and incumbent_preserved and ppo_reload_error == 0 and ppo_replay['within_1e_minus_9']
            result = dict(schema='explorer_BC_warmstarted_PPO_full_object_v1',request_signature=signature,
                created_wall_utc=utc(),actual_attempt=str(attempt),context=identity,train_seeds=train_seeds,admitted_expert_seeds=admitted,
                development_seeds=development,expert_demonstrations=descriptors,supervision=supervision,
                privileged_actor=True,visual_student_implemented=False,scripted_actions_during_PPO=False,
                bc_checkpoint_sha256=bc_hash,bc_reload_action_max_abs_error=reload_error,bc_evaluation=before,
                ppo=dict(actual_timesteps=teacher.num_timesteps,actual_optimizer_steps=optimizer_steps,optimizer_epochs=teacher._n_updates,
                    weights_changed=any(not torch.equal(initial[name],value) for name,value in teacher.policy.state_dict().items()),
                    checkpoint_sha256=sha(attempt/'bc_plus_ppo_candidate.zip'),reload_action_max_abs_error=ppo_reload_error),
                ppo_evaluation=after,ppo_native_replay=ppo_replay,incumbent_file_preserved=incumbent_preserved,
                development_candidate_accepted=bool(promoted),elapsed_s=time.monotonic()-started,physical_ready=False,
                hypotheses_status='UNTESTED_HYPOTHESIS',method_ids=['PPO_SB3_2_9','EXPLORER_CAD_MUJOCO','ENGINEERING_GLUE'],
                limitations=['Privileged simulator observations; this is not visual or hardware deployment',
                    'Small fixed-base development scenes; no mobile kitchen or broad mastery claim',
                    'Scripted BC expert baseline is distinct from scratch PPO and from learned rollouts',
                    'No exact update-level post-reboot PPO continuation; interrupted attempts retained separately',
                    'Two matched development conditions are not a sealed final test'])
            if any(sha(Path(name)) != expected for name,expected in identity['source_sha256'].items()) or {path.name:sha(path) for path in sorted(meshes.glob('*.STL'))} != identity['cad_mesh_sha256']:
                raise ValueError('BC+PPO source/assets changed during execution; attempt retained')
            result['artifact_sha256'] = {str(path.relative_to(base)):sha(path) for path in attempt.rglob('*') if path.is_file()}
            write_json(base/'bc-ppo-result.json',result)
            progress(1,'complete_independent_BC_and_BC_plus_PPO_evaluation')
            return result
        finally:env.close()
