"""EXPLORER_CAD_MUJOCO / ENGINEERING_GLUE: actual mobile kitchen contact baseline.

A scripted privileged navigation/IK controller is explicitly separate from learned
policies. Finite wheel/servo actuation, independent full-task substep scoring and
full integration-state reexecution are required; no hardware IO or object attach.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import time
import uuid

import mujoco
import numpy as np
from PIL import Image

from legacy.simulator.explorer_model import resolve_mesh_dir, ROOT, model_metadata
from .mobile_manipulation_env import MobileManipulationEnv, SOURCE, DESTINATION
from .manipulation_env import STATE_SPEC
from .pilot import sha, write_json, utc


def parameters(config):
    result={}
    for name,default,lower,upper in [('seed',641041,0,1000000),('evaluation_episodes',1,1,20),
            ('horizon',2400,64,10000),('frame_stride',10,1,100)]:
        value=config.get(name,default)
        if type(value) is not int or not lower<=value<=upper:
            raise ValueError('Invalid mobile manipulation '+name)
        result[name]=value
    result['carry_profile']=config.get('carry_profile','high_unconstrained_v1')
    if result['carry_profile'] not in ('high_unconstrained_v1','lower_downward_v2'):
        raise ValueError('Unknown explicitly versioned mobile carry profile')
    if config.get('device','cpu')!='cpu':
        raise ValueError('Mobile contact baseline uses CPU physics only')
    return result


def scripted_mobile_actions(env,carry_profile='high_unconstrained_v1'):
    """Known scene/pose truth baseline; never a deployable visual navigator."""
    def arm_to(xyz,gripper,duration=3.,down=False):
        start=env.command[:6].copy()
        goal=env.solve_ik(xyz,start[:5],gripper=gripper,downward=down)
        count=max(round(duration/env.period),int(np.max(abs(goal-start)/env.velocity)/env.period)+5)
        for index in range(count):
            u=(index+1)/count
            yield np.r_[start+(goal-start)*(u*u*u*(10-15*u+6*u*u)),0.,0.,0.]
        for _ in range(16):
            yield np.r_[goal,0.,0.,0.]

    def drive_to(point,arm,speed=.10,limit=1000):
        arrived=False
        for _ in range(limit):
            pose=env.base_pose();error=np.asarray(point)-pose[:2]
            c,s=math.cos(pose[2]),math.sin(pose[2])
            body=np.array([[c,s],[-s,c]])@error
            velocity=np.clip(1.5*body,-speed,speed)
            yield np.r_[arm,velocity,np.clip(-1.5*pose[2],-.3,.3)]
            if np.linalg.norm(error)<.010 and abs(pose[2])<.03:
                arrived=True;break
        for _ in range(20):
            yield np.r_[arm,0.,0.,0.]
        if not arrived:
            raise ValueError('Finite wheel-actuated route did not reach its waypoint')

    env.phase='navigate_outbound_through_door'
    carry=env.command[:6].copy()
    yield from drive_to([.38,0.],carry)
    center=env.points().mean(0)
    yield from drive_to(center[:2]-[.20,0.],carry)
    env.phase='approach'
    center=env.points().mean(0)
    grasp_z=max(.014,float(center[2]))
    yield from arm_to([*center[:2],.13],30,3,True)
    env.phase='descend'
    yield from arm_to([*center[:2],grasp_z+.007],30,2,True)
    env.phase='close'
    yield from arm_to([*center[:2],grasp_z],170,3,True)
    env.phase='lift'
    center=env.points().mean(0)
    if carry_profile=='lower_downward_v2':
        pose=env.base_pose()
        yield from arm_to([pose[0]+.23,pose[1],.15],170,3,True)
    else:
        yield from arm_to([*center[:2],.25],170,3,False)
    for _ in range(20):
        yield np.r_[env.command[:6],0.,0.,0.]
    if not env.evaluator.lifted:
        raise ValueError('Actual bilateral grasp/lift was not independently confirmed')
    env.phase='navigate_held_object_inbound_through_door'
    carry=env.command[:6].copy()
    yield from drive_to([.82,0.],carry,speed=.045)
    yield from drive_to([.35,0.],carry,speed=.045)
    destination_base=DESTINATION[:2]-[.22,0.]
    yield from drive_to(destination_base,carry,speed=.045)
    env.phase='place_above_tray'
    yield from arm_to([*env.goal[:2],env.rim+.045],170,2,False)
    env.phase='release'
    yield from arm_to([*env.goal[:2],env.rim+.045],30,3,False)
    env.phase='withdraw'
    yield from arm_to([env.goal[0]-.04,env.goal[1],env.rim+.13],30,2,False)
    env.phase='settle'
    for _ in range(80):
        yield np.r_[env.command[:6],0.,0.,0.]


def publish_frame(env,job_folder,phase):
    temporary=job_folder/'frame.tmp.jpg'
    Image.fromarray(env.render()).save(temporary,quality=90);temporary.replace(job_folder/'frame.jpg')
    write_json(job_folder/'frame.json',dict(source='actual_CAD_MuJoCo_mobile_kitchen',phase=phase,
        wall_utc=utc(),sim_time_s=float(env.data.time),simulation_steps=env.steps,actual_workers=1,
        learned_policy=False,physical_run=False))


def record_mobile_rollout(env,folder,seed,*,frame_stride=10,progress=None,carry_profile='high_unconstrained_v1'):
    folder.mkdir(parents=True,exist_ok=False)
    _,initial=env.reset(seed=seed)
    env.record_substeps=True
    native=[env.integration_state()]
    states,raw,issued,times,phases,base,images,image_indices=[],[],[],[],[],[],[],[]
    generator=scripted_mobile_actions(env,carry_profile)
    begin=time.monotonic();error=None;done=False
    while not done:
        try:
            action=next(generator)
        except StopIteration:
            break
        except ValueError as failure:
            error=str(failure);break
        states.append(env.command.copy());raw.append(np.asarray(action,float));times.append(float(env.data.time));phases.append(env.phase)
        base.append(env.base_pose())
        if env.steps%frame_stride==0:
            images.append(env.render());image_indices.append(env.steps)
        try:
            _,_,terminal,truncated,info=env.step_command(action)
        except ValueError as failure:
            error=str(failure);break
        issued.append(env.command.copy());native.append(env.integration_state())
        if progress and env.steps%20==0:
            progress(env)
        done=terminal or truncated
    env.record_substeps=False
    count=len(issued)
    np.savez_compressed(folder/'trajectory.npz',state_command9=np.asarray(states[:count]),
        raw_requested_endpoint9=np.asarray(raw[:count]),rejected_requested_endpoint9=np.asarray(raw[count:]),dispatched_endpoint9=np.asarray(issued),
        native_integration_state=np.asarray(native),pre_action_sim_time_s=np.asarray(times[:count]),
        phase=np.asarray(phases[:count]),pre_action_base_xy_yaw=np.asarray(base[:count]),
        sparse_rgb=np.asarray(images,np.uint8),rgb_action_indices=np.asarray(image_indices,np.int64),
        object_contact_substeps=np.asarray(env.substep_trace),mobile_contact_substeps=np.asarray(env.mobile_substep_trace))
    Image.fromarray(env.render()).save(folder/'final.jpg',quality=90)
    result=dict(seed=seed,n_actions=count,error=error,source='scripted_pose_truth_navigation_and_IK_not_learned',
        initial=initial,final=env.info(),evaluator=env.mobile_evaluator.result(),wall_seconds=time.monotonic()-begin,
        physical_run=False,carry_profile=carry_profile,trajectory_sha256=sha(folder/'trajectory.npz'),
        image_sampling={'actual_RGB_every_n_actions':frame_stride,'sparse_frames':len(images),'direct_ACT_training_dataset':False},
        base_or_object_teleported_during_execution=False,grasp_welds=False)
    write_json(folder/'result.json',result)
    return result


def replay_mobile_trajectory(env,folder):
    """Actual wheel/servo requests reexecuted from complete native initial state."""
    result=json.loads((folder/'result.json').read_text())
    if sha(folder/'trajectory.npz')!=result['trajectory_sha256']:
        raise ValueError('Mobile trajectory SHA changed before native replay')
    with np.load(folder/'trajectory.npz',allow_pickle=False) as data:
        if not len(data['dispatched_endpoint9']):
            return {'actions_reexecuted':0,'within_1e_minus_9':False,'reason':'No actual action was available for replay'}
        env.reset(seed=result['seed']);env.record_substeps=False
        mujoco.mj_setState(env.model,env.data,data['native_integration_state'][0],STATE_SPEC)
        mujoco.mj_forward(env.model,env.data)
        env.command=data['state_command9'][0].copy()
        errors=[]
        for index,request in enumerate(data['raw_requested_endpoint9']):
            env.step_command(request)
            errors.append(float(np.max(abs(env.integration_state()-data['native_integration_state'][index+1]))))
    return dict(actions_reexecuted=len(errors),native_state_spec='mjSTATE_INTEGRATION',
        maximum_native_state_error=max(errors,default=0.),within_1e_minus_9=max(errors,default=0.)<1e-9,
        independent_evaluator=env.mobile_evaluator.result(),physical_run=False)


def run_mobile_manipulation(store,job):
    """One durable finite simulation baseline; no learned or physical skill claim."""
    identity=job['id']
    if not isinstance(identity,str) or Path(identity).name!=identity or identity in ('.','..'):
        raise ValueError('Invalid mobile manipulation job ID')
    settings=parameters(job.get('config') or {})
    root=(Path(store.root)/'jobs'/identity).resolve();root.mkdir(parents=True,exist_ok=True)
    assets=resolve_mesh_dir((job.get('config') or {}).get('mesh_dir'))
    sources=[Path(__file__),Path(__file__).with_name('mobile_manipulation_env.py'),
        Path(__file__).with_name('manipulation_env.py'),ROOT/'simulator/explorer_model.py',
        ROOT/'simulator/environment.py',ROOT/'simulator/model_config.json',ROOT/'config/explorer.urdf',ROOT/'config/arm-motion.json']
    context=dict(settings=settings,source_sha256={str(p):sha(p) for p in sources},
        CAD_mesh_sha256={p.name:sha(p) for p in sorted(assets.glob('*.STL'))},
        versions={name:importlib.metadata.version(name) for name in ('mujoco','numpy')},model_metadata=model_metadata())
    signature=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
    with (root/'.mobile-owner.lock').open('a') as lock:
        try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as failure: raise RuntimeError('Mobile simulation already has an owner') from failure
        completed=root/'mobile-manipulation-result.json'
        if completed.is_file():
            saved=json.loads(completed.read_text())
            if saved['signature']!=signature:raise ValueError('Completed mobile source/settings differ')
            for name,expected in saved['artifact_sha256'].items():
                path=(root/name).resolve()
                if not path.is_relative_to(root) or not path.is_file() or sha(path)!=expected:
                    raise ValueError('Completed mobile artifact checksum mismatch')
            return saved
        folder=root/'attempts'/uuid.uuid4().hex;folder.mkdir(parents=True)
        repository=Path(__file__).resolve().parents[3]
        for source in sources:
            snapshot=folder/'implementation_sources'/source.relative_to(repository)
            snapshot.parent.mkdir(parents=True,exist_ok=True);snapshot.write_bytes(source.read_bytes())
        write_json(folder/'provenance.json',dict(context=context,created=utc(),scripted_not_learned=True,
            scene='estimated two-room floor-level kitchen with real collision doorway/cabinet',
            physical_io=False,method_ids=['EXPLORER_CAD_MUJOCO','ENGINEERING_GLUE']))
        env=MobileManipulationEnv(assets,settings['horizon'])
        (folder/'model.xml').write_text(env.xml)
        episodes,replays=[],[]
        try:
            for index in range(settings['evaluation_episodes']):
                seed=settings['seed']+index
                trial=folder/('trial-'+str(seed))
                def progress(current):
                    publish_frame(current,root,current.phase)
                    store.update_job(identity,progress=min(.95,(index+current.steps/settings['horizon'])/settings['evaluation_episodes']),
                        result={'phase':current.phase,'actual_workers':1,'learned_policy':False,'physical_run':False})
                result=record_mobile_rollout(env,trial,seed,frame_stride=settings['frame_stride'],progress=progress,carry_profile=settings['carry_profile'])
                replay=replay_mobile_trajectory(env,trial)
                write_json(trial/'native-replay.json',replay)
                episodes.append(result);replays.append(replay);publish_frame(env,root,'mobile_baseline_trial_completed')
            if any(sha(Path(name))!=expected for name,expected in context['source_sha256'].items()):
                raise ValueError('Mobile implementation changed during execution')
            result=dict(schema='explorer_mobile_candy_kitchen_contact_baseline_v1',signature=signature,
                status='simulation_baseline_evaluated',task='mobile_wrapped_candy_kitchen_to_tray',
                actual_workers=1,learned_policy=False,trained=False,physical_ready=False,physical_io=False,
                base_fixture=False,grasp_welds=False,chassis_or_object_teleported_during_control=False,
                kitchen_navigation_implemented=True,scene_scope='estimated local two-room floor-level kitchen; known map and object location',
                evaluation={'n':len(episodes),'successes':sum(e['evaluator']['success'] for e in episodes),'episodes':episodes},
                native_replays=replays,context=context,actual_attempt=str(folder),
                limitations=['Scripted privileged baseline, not a learned visual navigation/manipulation policy',
                    'Known map/source location; search and real perception not tested',
                    'Estimated cabinet, roller, friction, inertia and servo dynamics; no physical acceptance',
                    'Floor-level candy pickup and floor tray; raised countertop pickup not implemented',
                    'Sparse preview RGB is explicitly indexed and is not a synchronized ACT training export',
                    'Development runs only; no sealed generalization/scientific novelty claim'])
            result['artifact_sha256']={str(p.relative_to(root)):sha(p) for p in folder.rglob('*') if p.is_file()}
            write_json(completed,result);store.update_job(identity,progress=1)
            return result
        finally:env.close()
