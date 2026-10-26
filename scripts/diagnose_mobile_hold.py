#!/usr/bin/env python3
"""ENGINEERING_GLUE / EXPLORER_CAD_MUJOCO: measured simulation hold probes.

Read-only trajectory diagnosis plus matched finite-command branches restored from
one recorded native initial state. No physical IO, state forcing during a branch,
actuator/material changes, learned-policy or whole-task-success claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import mujoco
import numpy as np


def setup_source(root):
    sys.path[:0]=[str(root/'01-autonomous-learning/src'),str(root/'shared')]


def write_json(path,value):
    from explorer_sim.pilot import write_json as writer
    writer(path,value)


def checksum(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize_contacts(trace,initial_base,initial_object_height,dt):
    # time, padL, padR, objectXYZ, baseXYZyaw, base linear velocity, jawXYZ,
    # first-five arm tracking norm(rad), gripper tracking error(rad).
    held=np.all(trace[:,1:3]>.0001,axis=1)
    streak=maximum=0
    for value in held:
        streak=streak+1 if value else 0;maximum=max(maximum,streak)
    low=np.flatnonzero(trace[:,5]<initial_object_height-.045)
    anchor=None;carried=0.
    for index,value in enumerate(held):
        if value:
            if anchor is None:anchor=trace[index,6:8].copy()
            carried=max(carried,float(np.linalg.norm(trace[index,6:8]-anchor)))
        else:anchor=None
    relative=trace[:,3:6]-trace[:,12:15]
    return dict(actual_sim_seconds=float(trace[-1,0]-trace[0,0]+dt),
        continuous_bilateral_contact_max_s=maximum*dt,
        final_bilateral_contact=bool(held[-1]),first_height_loss_after_s=float(trace[low[0],0]-trace[0,0]) if len(low) else None,
        held_base_displacement_max_m=carried,base_displacement_m=float(np.linalg.norm(trace[-1,6:8]-initial_base[:2])),
        final_object_xyz_m=trace[-1,3:6].tolist(),final_object_minus_jaw_xyz_m=relative[-1].tolist(),
        object_minus_jaw_change_m=float(np.linalg.norm(relative[-1]-relative[0])),
        first_five_joint_tracking_error_median_rad=float(np.median(trace[:,15])),
        first_five_joint_tracking_error_max_rad=float(trace[:,15].max()),
        gripper_contact_tracking_error_median_rad=float(np.median(abs(trace[:,16]))),
        pad_force_median_N=np.median(trace[:,1:3],axis=0).tolist(),
        physically_validated=False)


def run(source_result,output,source_root,seconds=32):
    setup_source(source_root)
    from explorer_sim.mobile_manipulation_env import MobileManipulationEnv
    from explorer_sim.manipulation_env import STATE_SPEC
    from legacy.simulator.explorer_model import resolve_mesh_dir,servo_to_joint
    source_result=source_result.resolve();original=json.loads(source_result.read_text())
    if original.get('learned_policy') is not False or len(original['evaluation']['episodes'])!=1:
        raise ValueError('Choose one explicitly scripted mobile development source')
    episode=original['evaluation']['episodes'][0]
    trial=Path(original['actual_attempt'])/('trial-'+str(episode['seed']))
    archive=trial/'trajectory.npz'
    if checksum(archive)!=episode['trajectory_sha256']:raise ValueError('Original source trajectory checksum changed')
    output.mkdir(parents=True,exist_ok=False)
    env=MobileManipulationEnv(resolve_mesh_dir(),horizon=10000)
    source_xml=Path(original['actual_attempt'])/'model.xml'
    if hashlib.sha256(env.xml.encode()).hexdigest()!=checksum(source_xml):raise ValueError('Current mechanics XML differs from source; probe refused')
    data=np.load(archive,allow_pickle=False)
    contacts=data['object_contact_substeps'];mobile=data['mobile_contact_substeps']
    held=np.all(contacts[:,1:3]>.0001,axis=1)
    valid=np.flatnonzero(held)
    if not len(valid):raise ValueError('Source has no bilateral contact event to inspect')
    last=int(valid[-1]);dt=env.model.opt.timestep
    window=slice(max(0,last-round(1/dt)),last+1)
    acceleration=np.diff(mobile[:,7:10],axis=0)/dt
    sampled=[]
    scratch=mujoco.MjData(env.model)
    from_time,to_time=contacts[last,0]-1.,contacts[last,0]+.5
    for index,state in enumerate(data['native_integration_state'][:-1]):
        moment=data['pre_action_sim_time_s'][index]
        if from_time<=moment<=to_time:
            mujoco.mj_setState(env.model,scratch,state,STATE_SPEC);mujoco.mj_forward(env.model,scratch)
            jaw=np.mean(scratch.geom_xpos[env.pad_ids],axis=0)
            candy=scratch.body('candy').xpos.copy()
            command=data['state_command9'][index]
            tracking=scratch.qpos[env.qids]-servo_to_joint(command[:6])
            sampled.append(dict(sim_time_s=float(moment),object_minus_jaw_xyz_m=(candy-jaw).tolist(),
                first_five_joint_tracking_error_rad=tracking[:5].tolist(),gripper_contact_tracking_error_rad=float(tracking[5]),
                phase=str(data['phase'][index])))
    source_diagnosis=dict(last_bilateral_sim_time_s=float(contacts[last,0]),last_bilateral_base_xy_yaw=mobile[last,1:4].tolist(),
        last_bilateral_object_xyz=contacts[last,4:7].tolist(),pad_force_prior_one_second_median_N=np.median(contacts[window,1:3],axis=0).tolist(),
        prior_one_second_chassis_acceleration_RMS_m_s2=np.sqrt(np.mean(acceleration[window]**2,axis=0)).tolist(),
        prior_one_second_chassis_acceleration_peak_m_s2=np.max(abs(acceleration[window]),axis=0).tolist(),
        heading_at_last_contact_rad=float(mobile[last,3]),reconstructed_actual_native_state_samples=sampled,
        cause='AMBIGUOUS_before_matched_hold_probe')
    indices=np.flatnonzero(data['phase']=='navigate_held_object_inbound_through_door')
    if not len(indices):raise ValueError('Source has no complete grasp/lift followed by transport')
    source_index=int(indices[0])
    source_substep=source_index*round(env.period/dt)-1
    if (not 0<=source_substep<len(contacts) or not np.all(contacts[source_substep,1:3]>.0001)
            or contacts[source_substep,6]<contacts[0,6]+.045):
        raise ValueError('Matched probe initial source state is not an actual lifted bilateral hold')
    native=data['native_integration_state'][source_index].copy()
    command=data['state_command9'][source_index].copy()
    branches={}
    base_observer=env._substep_evaluate
    tracking_target=servo_to_joint(command[:6])
    try:
        for name in ('stationary_hold','slow_smooth_transport'):
            env.reset(seed=episode['seed'])
            mujoco.mj_setState(env.model,env.data,native,STATE_SPEC);mujoco.mj_forward(env.model,env.data)
            env.command=command.copy();env.record_substeps=True
            initial_base=env.base_pose();initial_object_height=float(env.data.body('candy').xpos[2])
            states=[env.integration_state()];requests=[];rows=[]
            began=time.monotonic()
            def observe_substep():
                base_observer()
                row,m=env.substep_trace[-1],env.mobile_substep_trace[-1]
                tracking=env.data.qpos[env.qids]-tracking_target
                rows.append(np.r_[row[0],row[1:3],row[4:7],m[1:4],m[7:10],env.jaw_center(),np.linalg.norm(tracking[:5]),tracking[5]])
            env._substep_evaluate=observe_substep
            for index in range(round(seconds/env.period)):
                elapsed=index*env.period
                u=min(1.,elapsed/4.)
                blend=u*u*u*(10-15*u+6*u*u)
                requested=np.r_[command[:6],-.025*blend if name=='slow_smooth_transport' else 0.,0.,0.]
                env.step_command(requested)
                requests.append(requested);states.append(env.integration_state())
            env._substep_evaluate=base_observer
            trace=np.asarray(rows)
            folder=output/name;folder.mkdir()
            np.savez_compressed(folder/'trajectory.npz',trace=trace,requested_endpoint9=np.asarray(requests),
                native_integration_state=np.asarray(states),initial_command9=command)
            measured=summarize_contacts(trace,initial_base,initial_object_height,dt)
            measured.update(native_initial_state_sha256=hashlib.sha256(native.tobytes()).hexdigest(),
                wall_seconds=time.monotonic()-began,trajectory_sha256=checksum(folder/'trajectory.npz'),
                jaw_and_joint_tracking_sample_basis='same actual physics substep as contact/object/base channels; 2ms model integration period',
                whole_task_trial=False,physical_io=False,actuator_or_friction_settings_changed=False)
            write_json(folder/'result.json',measured);branches[name]=measured
        statement='AMBIGUOUS'
        if branches['stationary_hold']['first_height_loss_after_s'] is not None and not branches['stationary_hold']['final_bilateral_contact']:
            statement='Contact retention fails even in the matched stationary branch; base navigation alone does not explain the source failure'
        elif branches['slow_smooth_transport']['held_base_displacement_max_m']>=.6 and branches['slow_smooth_transport']['first_height_loss_after_s'] is None:
            statement='Slow smoothly ramped navigation retains the object for >=0.6m in this one matched simulation branch'
        elif branches['slow_smooth_transport']['first_height_loss_after_s'] is not None:
            statement='Stationary branch holds while the smoothly ramped moving branch drops; movement-related cause remains to be isolated'
        result=dict(scope='one matched initial-state simulation diagnostic; not a complete task or physical experiment',
            source_result=str(source_result),source_result_sha256=checksum(source_result),source_trajectory_sha256=checksum(archive),
            source_xml_sha256=checksum(source_xml),source_native_index=source_index,source_initial_hold_verified_from_substep=source_substep,source_sim_time_s=float(data['pre_action_sim_time_s'][source_index]),
            source_diagnosis=source_diagnosis,branches=branches,observed_statement=statement,
            initial_snapshot_restoration_is_explicit=True,state_forcing_during_branch=False,physical_io=False,
            implementation_sha256=checksum(__file__),scientific_hypotheses_status='UNTESTED')
        write_json(output/'result.json',result)
        print(json.dumps({'result':str(output/'result.json'),'observed_statement':statement,'branches':branches},indent=2))
        return result
    finally:env.close();data.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('source_result',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--seconds',type=int,default=32)
    args=parser.parse_args()
    if not 5<=args.seconds<=60:raise ValueError('Choose a finite 5..60second matched hold probe')
    run(args.source_result,args.output,args.source_root,args.seconds)


if __name__=='__main__':main()
