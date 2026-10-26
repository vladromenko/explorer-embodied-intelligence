"""Full-object MuJoCo manipulation pilot; PPO_SB3_2_9, ENGINEERING_GLUE.

Scripted IK is a separately labelled baseline, never called learned PPO. PPO
sees privileged truth; this module does not claim an independently visual ACT.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import importlib.metadata
import json
import multiprocessing
from pathlib import Path
import resource
import time
import uuid

import mujoco
import numpy as np
from PIL import Image

from .manipulation_env import ManipulationEnv, TASKS, STATE_SPEC, LOW, HIGH
from .pilot import sha, write_json, utc
from legacy.simulator.explorer_model import resolve_mesh_dir, model_metadata, ROOT


def parameters(config):
    result = {}
    for name,default,lower,upper in [('seed',321041,0,10**8),('teacher_steps',1024,64,10**6),
            ('horizon',600,32,2400),('evaluation_episodes',2,1,50),('benchmark_steps',50,8,1000)]:
        value = config.get(name,default)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError('Invalid manipulation '+name)
        result[name] = value
    result['task'] = config.get('task','candy_to_tray')
    if result['task'] not in TASKS or config.get('device','cpu') != 'cpu':
        raise ValueError('Choose a supported task and CPU device')
    result['benchmark'] = config.get('benchmark',True)
    if type(result['benchmark']) is not bool:
        raise ValueError('Invalid benchmark switch')
    return result


def scripted_actions(env):
    """Truth/IK baseline; every yielded command still undergoes finite dynamics."""
    center = env.points().mean(0)
    grasp_z = .017 if env.task == TASKS[1] else max(.014,float(center[2]))
    def move(xyz,grip,duration,down=False):
        start = env.command.copy()
        goal = env.solve_ik(xyz,seed=start[:5],gripper=grip,downward=down)
        count = max(round(duration/env.period),int(np.max(abs(goal-start)/env.velocity)/env.period)+5)
        for index in range(count):
            u = (index+1)/count
            yield start+(goal-start)*(u*u*u*(10-15*u+6*u*u))
        for _ in range(12):
            yield goal
    for phase,xyz,grip,duration,down in [
        ('approach',[*center[:2],.13],30,3,True),
        ('descend',[*center[:2],grasp_z+.007],30,2,True),
        ('close',[*center[:2],grasp_z],170,3,True),
        ('lift',[*center[:2],.25],170,3,False),
        ('carry',[*env.goal[:2],.26],170,3,False),
        ('place',[*env.goal[:2],env.rim+.045],170,2,False),
        ('release',[*env.goal[:2],env.rim+.045],30,3,False),
        ('withdraw',[env.goal[0]-.04,env.goal[1],env.rim+.13],30,2,False)]:
        env.phase = phase
        yield from move(xyz,grip,duration,down)
    env.phase = 'settle'
    for _ in range(40):
        yield env.command.copy()


def raw_policy_action(policy, observation):
    """Preserve the actual PPO mean BEFORE SB3 predict's Box clipping."""
    if hasattr(policy,'policy'):
        import torch
        if policy.policy.squash_output:
            raise ValueError('Squashed policies need an explicit raw-action contract')
        with torch.no_grad():
            batch,_ = policy.policy.obs_to_tensor(observation)
            return policy.policy.get_distribution(batch).get_actions(deterministic=True).cpu().numpy()[0]
    return policy.predict(observation,deterministic=True)[0]


def record_rollout(env, folder, seed, policy=None, scripted=False):
    folder.mkdir(parents=True,exist_ok=False)
    observation, initial = env.reset(seed=seed)
    env.record_substeps = True
    rows = []
    images, states, raw, requests, dispatch, native, times, phases, point_rows = [],[],[],[],[],[],[],[],[]
    native.append(env.integration_state())
    start = time.monotonic()
    error = None
    generator = scripted_actions(env) if scripted else None
    done = False
    while not done:
        try:
            action = next(generator) if scripted else raw_policy_action(policy,observation)
        except StopIteration:
            break
        except ValueError as failure:
            error = str(failure)
            break
        images.append(env.render())
        states.append(np.r_[env.command,0,0,0])
        times.append(float(env.data.time))
        phases.append(env.phase if scripted else 'ppo_policy')
        point_rows.append(env.points())
        observation,reward,terminal,truncated,info = env.step_command(action) if scripted else env.step(action)
        raw.append(env.raw_action.copy()); requests.append(env.requested.copy()); dispatch.append(env.command.copy())
        native.append(env.integration_state())
        rows.append(dict(step=len(rows),evaluator=info['evaluator'],unstable=info['unstable']))
        # Script must attempt the full sequence even after an early grasp.
        done = info['unstable'] or terminal or (truncated and not scripted)
    result = dict(seed=seed,source='scripted_truth_ik_baseline' if scripted else 'reloaded_sb3_ppo',
        n_actions=len(raw),error=error,evaluator=env.evaluator.result(),
        wall_seconds=time.monotonic()-start,initial=initial,final=env.info(),physical_run=False)
    np.savez_compressed(folder/'trajectory.npz',rgb=np.asarray(images,dtype=np.uint8),
        state_command9=np.asarray(states),raw_requested_action6=np.asarray(raw),
        requested_endpoint6=np.asarray(requests),dispatched_endpoint6=np.asarray(dispatch),
        native_integration_state=np.asarray(native),pre_action_sim_time_s=np.asarray(times),
        phase=np.asarray(phases),evaluator_object_points=np.asarray(point_rows),
        substep_contact_trace=np.asarray(env.substep_trace))
    env.record_substeps = False
    if images:
        Image.fromarray(env.render()).save(folder/'final.jpg',quality=90)
        result['final_image_sim_time_s'] = float(env.data.time)
    write_json(folder/'substep-summary.json',rows)
    result['trajectory_sha256'] = sha(folder/'trajectory.npz')
    write_json(folder/'result.json',result)
    return result


def replay_trajectory(env, folder):
    """Restore full native initial state and re-execute requests, not state playback."""
    data = np.load(folder/'trajectory.npz',allow_pickle=False)
    env.record_substeps = False
    env.reset(seed=json.loads((folder/'result.json').read_text())['seed'])
    mujoco.mj_setState(env.model,env.data,data['native_integration_state'][0],STATE_SPEC)
    mujoco.mj_forward(env.model,env.data)
    env.command = data['state_command9'][0,:6].copy()
    errors = []
    for index,target in enumerate(data['requested_endpoint6']):
        # Both pathways share identical saturation/rate limiting after request.
        env.step_command(np.clip(target,LOW,HIGH))
        errors.append(float(np.max(abs(env.integration_state()-data['native_integration_state'][index+1]))))
    return dict(actions_reexecuted=len(errors),native_state_spec='mjSTATE_INTEGRATION',
                max_abs_native_state_error=max(errors,default=0.),within_1e_minus_9=max(errors,default=0.)<1e-9,
                evaluator=env.evaluator.result())


def _benchmark_worker(spec):
    mesh_dir,task,steps,seed = spec
    env = ManipulationEnv(mesh_dir,task,horizon=steps+1)
    observation,_ = env.reset(seed=seed)
    rng = np.random.default_rng(seed)
    started = time.monotonic(); cpu = time.process_time()
    for _ in range(steps):
        observation,_,_,_,_ = env.step(rng.uniform(-.2,.2,6))
    result = dict(actions=steps,substeps=steps*round(env.period/env.model.opt.timestep),
        wall_seconds=time.monotonic()-started,cpu_seconds=time.process_time()-cpu,
        peak_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    env.close()
    return result


def benchmark(mesh_dir,task,steps=50,seed=700041):
    results = []
    for workers in (1,2,4):
        started = time.monotonic()
        with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
            values = list(pool.map(_benchmark_worker,[(str(mesh_dir),task,steps,seed+i) for i in range(workers)]))
        wall = time.monotonic()-started
        results.append(dict(workers=workers,actions=sum(v['actions'] for v in values),wall_seconds=wall,
            actions_per_s_including_spawn=sum(v['actions'] for v in values)/wall,
            steady_actions_per_s=sum(v['actions'] for v in values)/max(v['wall_seconds'] for v in values),
            processes=values,parallel_training=False,thermal_sensor='NOT_MEASURED'))
    return dict(kind='actual_independent_physics_benchmark_not_parallel_training',results=results,
                rss_units='bytes on macOS; KiB on Linux',selected_training_workers=1)


def run_manipulation(store,job):
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
    import torch
    settings = parameters(job.get('config') or {})
    identity = job['id']
    if not isinstance(identity,str) or Path(identity).name != identity or identity in ('.','..'):
        raise ValueError('Invalid manipulation job ID')
    base = (Path(store.root)/'jobs'/identity).resolve()
    base.mkdir(parents=True,exist_ok=True)
    assets = resolve_mesh_dir((job.get('config') or {}).get('mesh_dir'))
    train_seeds = [settings['seed']]
    evaluation_seeds = list(range(settings['seed']+10000,settings['seed']+10000+settings['evaluation_episodes']))
    script_seeds = [settings['seed']+20000]
    context = dict(settings=settings,training_seed=train_seeds,development_seeds=evaluation_seeds,
        scripted_baseline_seeds=script_seeds,split='DEVELOPMENT_not_sealed',created=utc(),
        source_sha256={p.name:sha(p) for p in (Path(__file__),Path(__file__).with_name('manipulation_env.py'))},
        cad_mesh_sha256={p.name:sha(p) for p in sorted(assets.glob('*.STL'))},
        versions={p:importlib.metadata.version(p) for p in ('mujoco','stable-baselines3','torch','numpy')},
        model_metadata=model_metadata(),base_fixture=True,physical_run=False,visual_student_implemented=False)
    sources = [Path(__file__),Path(__file__).with_name('manipulation_env.py'),
        ROOT/'simulator/explorer_model.py',ROOT/'simulator/environment.py',
        ROOT/'simulator/model_config.json',ROOT/'config/explorer.urdf',
        ROOT/'config/explorer.srdf',ROOT/'config/arm-motion.json']
    context['implementation_files_sha256'] = {str(p):sha(p) for p in sources}
    completed = base/'result.json'
    if completed.is_file():
        saved = json.loads(completed.read_text())
        expected = dict(context); previous = dict(saved['context'])
        expected.pop('created'); previous.pop('created')
        if expected != previous:
            raise ValueError('Completed manipulation source/settings changed')
        for name,digest in saved['artifact_sha256'].items():
            artifact = (base/name).resolve()
            if not artifact.is_relative_to(base) or not artifact.is_file() or sha(artifact) != digest:
                raise ValueError('Completed manipulation artifact checksum mismatch')
        return saved
    # Never overwrite an interrupted attempt or its policy/evidence.
    folder = base/'attempts'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    (folder/'implementation_sources').mkdir()
    for source in sources:
        (folder/'implementation_sources'/source.name).write_bytes(source.read_bytes())
    write_json(folder/'provenance.json',context)
    def update(progress,phase):
        temporary = base/'frame.tmp.jpg'
        Image.fromarray(env.render()).save(temporary,quality=90)
        temporary.replace(base/'frame.jpg')
        write_json(base/'frame.json',dict(source='actual_mujoco_manipulation',phase=phase,
            sim_time_s=float(env.data.time),actual_workers=1,wall_utc=utc(),physical_run=False))
        store.update_job(identity,progress=progress,result=dict(phase=phase,source='actual_mujoco_manipulation',actual_workers=1,physical_run=False))
    torch.set_num_threads(2)
    env = ManipulationEnv(assets,settings['task'],settings['horizon'])
    (folder/'model.xml').write_text(env.xml)
    try:
        baseline = record_rollout(env,folder/'scripted_baseline',script_seeds[0],scripted=True)
        replay = replay_trajectory(env,folder/'scripted_baseline') if baseline['n_actions'] else None
        write_json(folder/'scripted_replay.json',replay)
        update(.15,'full_contact_scripted_baseline_separate_from_ppo')
        class Progress(BaseCallback):
            def _on_step(self):
                info = self.locals['infos'][0]
                training_requests.write(json.dumps(dict(timestep=self.num_timesteps,
                    raw_sampled_action6=self.locals['actions'][0].tolist(),
                    sb3_clipped_action6=self.locals['clipped_actions'][0].tolist(),
                    pre_action_command6=info['pre_action_command6'],
                    requested_endpoint6=info['requested_endpoint6'],
                    dispatched_endpoint6=info['command9'][:6],post_action_sim_time_s=info['sim_time_s']),allow_nan=False)+'\n')
                if self.num_timesteps % 64 == 0:
                    training_requests.flush()
                    update(.15+.65*min(1,self.num_timesteps/settings['teacher_steps']),'genuine_sb3_ppo_optimization')
                return True
        teacher = PPO('MlpPolicy',env,n_steps=64,batch_size=32,n_epochs=4,
            learning_rate=3e-4,policy_kwargs=dict(net_arch=[64,64]),seed=settings['seed'],device='cpu',verbose=0)
        before = {k:v.detach().clone() for k,v in teacher.policy.state_dict().items()}
        optimizer_steps = 0
        def counted_step(optimizer,args,kwargs):
            nonlocal optimizer_steps
            optimizer_steps += 1
        handle = teacher.policy.optimizer.register_step_post_hook(counted_step)
        started = time.monotonic()
        try:
            with (folder/'training-action-requests.jsonl').open('w') as training_requests:
                teacher.learn(total_timesteps=settings['teacher_steps'],callback=Progress())
        finally:
            handle.remove()
        training_seconds = time.monotonic()-started
        changed = any(not torch.equal(before[k],v) for k,v in teacher.policy.state_dict().items())
        teacher.save(folder/'teacher_ppo')
        loaded = PPO.load(folder/'teacher_ppo.zip',device='cpu')
        env.reset(seed=evaluation_seeds[0])
        first = raw_policy_action(teacher,env.observation())
        second = raw_policy_action(loaded,env.observation())
        reload_error = float(np.max(abs(first-second)))
        evaluated = [record_rollout(env,folder/f'ppo_development_{seed}',seed,loaded) for seed in evaluation_seeds]
        first_folder = folder/f'ppo_development_{evaluation_seeds[0]}'
        learned_replay = replay_trajectory(env,first_folder)
        write_json(folder/'ppo_replay.json',learned_replay)
        throughput = benchmark(assets,settings['task'],settings['benchmark_steps']) if settings['benchmark'] else None
        result = dict(schema='explorer_full_object_manipulation_v1',status='completed',task=settings['task'],context=context,
            actual_attempt=str(folder),teacher=dict(algorithm='maintained_sb3_PPO',actual_timesteps=teacher.num_timesteps,
                optimizer_epochs=teacher._n_updates,actual_optimizer_steps=optimizer_steps,
                weights_changed=changed,checkpoint_sha256=sha(folder/'teacher_ppo.zip'),
                reload_action_max_abs_error=reload_error,training_seconds=training_seconds,training_workers=1,
                actor_inputs='privileged_sim_truth_not_deployable_visual',scripted_actions_during_ppo=False),
            scripted_baseline=baseline,scripted_replay=replay,ppo_replay=learned_replay,
            evaluation=dict(n=len(evaluated),successes=sum(e['evaluator']['success'] for e in evaluated),episodes=evaluated),
            benchmark=throughput,physical_ready=False,kitchen_navigation_implemented=False,
            limitations=['Estimated contact/material/servo dynamics; no hardware validation',
                'Stationary-base full-object local transfer; not complete mobile kitchen route',
                'PPO privileged teacher only; visual ACT manipulation student not implemented',
                'Fixed development protocol; no sealed, generalization or scientific novelty claim'])
        if any(sha(Path(name)) != digest for name,digest in context['implementation_files_sha256'].items()):
            raise ValueError('Manipulation implementation changed during execution; original attempt retained')
        result['artifact_sha256']={str(p.relative_to(base)):sha(p) for p in folder.rglob('*') if p.is_file()}
        write_json(base/'result.json',result)
        update(1,'complete_full_object_development_evaluation')
        return result
    finally:
        env.close()
