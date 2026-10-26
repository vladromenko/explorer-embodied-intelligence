#!/usr/bin/env python3
"""ENGINEERING_GLUE: NEW copied-CAD contact sensitivity experiment, not V9 replay.

Freeze is byte-only. Each finite attempt runs in a fresh bounded child, holds
saved actuator controls constant, captures the supported registered profile,
and retains failures. No rendering, learner, device, controller or deployment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gc
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET

from explorer_shared.contact_diagnosis import (ContactEvidenceRejected, canonical,
    copied_condition_xml, geometry_from_xml, inspect_campaign, probe_protocol,
    sha, signed_world_force, _read, _relative)
from explorer_shared.source_archive import _write, archive_sources

REPO = Path(__file__).resolve().parents[1]
LEGACY=REPO/'01-autonomous-learning/src/legacy'
SOURCE_FILES = ('shared/explorer_shared/contact_diagnosis.py', 'shared/explorer_shared/sim_initial_state.py',
    'shared/explorer_shared/skill_transfer_mujoco.py', 'shared/explorer_shared/transfer_mapping.py',
    'shared/explorer_shared/kinematics.py', 'shared/explorer_shared/source_archive.py',
    '01-autonomous-learning/src/explorer_sim/manipulation_env.py',
    '01-autonomous-learning/src/legacy/simulator/environment.py',
    '01-autonomous-learning/src/legacy/simulator/explorer_model.py',
    '01-autonomous-learning/src/legacy/simulator/model_config.json',
    '01-autonomous-learning/src/legacy/config/arm-motion.json',
    '01-autonomous-learning/src/legacy/config/arm_model.json',
    '01-autonomous-learning/src/legacy/config/explorer.urdf')


def current_sources():
    paths=[Path(__file__).resolve()]+[REPO/name for name in SOURCE_FILES]
    return {str(path):sha(_read(path,maximum=64*1024**2)) for path in paths}


def verify_sources(expected):
    if current_sources()!=expected:
        raise ContactEvidenceRejected('Frozen driver/current physics/profile source changed')


def prepare_assets(original_folder, xml_raw, destination, *, expected_manifest_sha256):
    """Resolve XML asset locators ONLY against the original retained SHA archive."""
    archive=json.loads(_read(original_folder/'source-archive/manifest.json',expected=expected_manifest_sha256))
    root=ET.fromstring(xml_raw);result={};names={}
    for mesh in root.findall('./asset/mesh'):
        locator=mesh.get('file');matches=[entry for entry in archive['files'] if locator in (entry['original_locator'],entry['resolved_path'])]
        identities={(entry['file'],entry['sha256'],entry['bytes']) for entry in matches}
        if len(identities)!=1:
            raise ContactEvidenceRejected('Exact original XML asset/archive binding required')
        member,checksum,size=identities.pop();raw=_read(original_folder/'source-archive'/_relative(member),expected=checksum,maximum=64*1024**2)
        if len(raw)!=size:
            raise ContactEvidenceRejected('Retained CAD size changed')
        name=Path(locator).name
        if name in names and names[name]!=checksum:
            raise ContactEvidenceRejected('Conflicting original CAD basename')
        names[name]=checksum;path=destination/name;_write(path,raw)
        result[str(path)]=checksum
    if not result:
        raise ContactEvidenceRejected('Actual retained full CAD assets required')
    return result


def located_xml(raw, assets):
    root=ET.fromstring(raw);by_name={Path(path).name:path for path in assets}
    for mesh in root.findall('./asset/mesh'):
        name=Path(mesh.get('file')).name
        if name not in by_name:
            raise ContactEvidenceRejected('Missing frozen copied CAD asset')
        mesh.set('file',by_name[name])
    return ET.tostring(root)


def freeze_campaign(campaign, output, expected_receipt_sha256):
    analysis=inspect_campaign(campaign,expected_receipt_sha256=expected_receipt_sha256)
    plan=probe_protocol(analysis,campaign_id='transfer-contact-probe-v10-20261027')
    output=Path(output).absolute()
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    _write(output/'saved-contact-diagnosis.json',canonical(analysis))
    inputs={};assets={}
    for entry in analysis['attempts']:
        identity=entry['scenario']['id'];original=Path(campaign)/identity;folder=output/'inputs'/identity
        xml_raw=_read(original/'target-model.xml',expected=entry['bindings']['target_model_xml_sha256'])
        trace=json.loads(_read(original/'target/target-trace.json',expected=entry['bindings']['target_trace_sha256']))
        native=trace[317]
        if sha(canonical(native['dispatch']['native_integration_state']))!=entry['new_initial_condition_source']['original_native_state_json_sha256']:
            raise ContactEvidenceRejected('Selected native preimpact condition differs')
        copied=prepare_assets(original,xml_raw,output/'assets',expected_manifest_sha256=entry['bindings']['source_archive_manifest_sha256'])
        if assets and assets!=copied:
            raise ContactEvidenceRejected('Prespecified matched scenes must retain the same CAD asset identities')
        assets=copied
        values={'original-model.xml':xml_raw,'selected-native-dispatch.json':canonical(native),
            'original-archive-manifest.json':_read(original/'source-archive/manifest.json',expected=entry['bindings']['source_archive_manifest_sha256'])}
        for name,raw in values.items():_write(folder/name,raw)
        inputs[identity]={name:dict(path=str(folder/name),sha256=sha(raw),bytes=len(raw)) for name,raw in values.items()}
    source=current_sources()
    archive=archive_sources(output/'program-source',source,context=dict(scope='NEW_contact_probe_current_limited_program_graph_not_full_OS',physical_io=False))
    _write(output/'original-campaign-receipt.json',_read(Path(campaign)/'campaign-qualification.json',expected=expected_receipt_sha256))
    plan.update(execution_status='FROZEN_NOT_STARTED', sources=source, inputs=inputs, assets=assets,
        original_campaign_path=str(Path(campaign).absolute()), original_campaign_receipt_sha256=expected_receipt_sha256,
        original_campaign_receipt_path=str(output/'original-campaign-receipt.json'),
        program_source_archive=archive, driver_sha256=source[str(Path(__file__).resolve())],
        output_root=str(output), source_runtime_OS_closure='PARTIAL',
        observation_geometry_stage='separate_kinematics_comPos_comVel_from_exact_poststep_state_no_live_forward',
        contact_stage='raw_mj_step_returned_contacts_and_solver_no_mj_forward_on_live_data',
        archive_asset_locator_rewrite_only=True,
        discarded_constructor_and_reset_native_steps_per_attempt=200,
        declared_contact_native_steps_total=14000,all_native_step_calls_planned=17200,
        discarded_setup_scope='actual_supported_env_constructor_reset100_and_explicit_reset100_before_transplant_not_contact_outcomes')
    verify_sources(source)
    raw=canonical(plan);_write(output/'frozen-protocol.json',raw)
    return dict(protocol_path=str(output/'frozen-protocol.json'),protocol_sha256=sha(raw),attempts=plan['attempts_planned'],
        actual_CAD_loaded=False,new_dynamics_steps=0,physical_io=False)


def load_protocol(output, expected_sha256):
    output=Path(output).absolute();raw=_read(output/'frozen-protocol.json',expected=expected_sha256)
    plan=json.loads(raw)
    if plan.get('schema')!='explorer_controlled_contact_probe_protocol_v1' or plan.get('execution_status')!='FROZEN_NOT_STARTED' or plan.get('output_root')!=str(output):
        raise ContactEvidenceRejected('Exact frozen contact protocol/root required')
    verify_sources(plan['sources'])
    for case in plan['inputs'].values():
        for entry in case.values():_read(entry['path'],expected=entry['sha256'])
    for path,expected in plan['assets'].items():_read(path,expected=expected,maximum=64*1024**2)
    return plan


def resource_snapshot():
    group=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
    folder=Path('/sys/fs/cgroup')/group.lstrip('/')
    return dict(pid=os.getpid(),cgroup=group,
        values={name:(folder/name).read_text().strip() for name in ('cpu.max','memory.max','memory.current','memory.peak','memory.swap.max','memory.events')})


def require_resources(plan):
    actual=resource_snapshot();values=actual['values'];quota,period=values['cpu.max'].split()
    if (quota=='max' or int(quota)>int(period) or values['memory.max']=='max' or
            int(values['memory.max'])>plan['resources']['memory_max_bytes'] or values['memory.swap.max']!='0'):
        raise ContactEvidenceRejected('CPU<=1 Memory<=2GiB swap0 cgroup required before any CAD allocation')
    return actual


def build_provider(plan,case,condition,folder):
    """Use the actual supported class/profile; explicit copied-model/state seed."""
    import mujoco
    import numpy as np
    from explorer_sim.manipulation_env import ManipulationEnv, FullTaskEvaluator
    from explorer_shared.skill_transfer_mujoco import MuJoCoTargetProvider
    if mujoco.__version__!='3.17.0':
        raise ContactEvidenceRejected('Frozen V9 engine ABI requires actual MuJoCo3.17.0')
    identity=case['original_scenario']['id'];inputs=plan['inputs'][identity]
    original=_read(inputs['original-model.xml']['path'],expected=inputs['original-model.xml']['sha256'])
    intervention=copied_condition_xml(original,condition['id'])
    xml=located_xml(intervention,plan['assets']);_write(folder/'condition-model-before-locators.xml',intervention);_write(folder/'compiled-input-model.xml',xml)
    selected=json.loads(_read(inputs['selected-native-dispatch.json']['path'],expected=inputs['selected-native-dispatch.json']['sha256']))
    random.seed(plan['initial_state_semantics']['RNG_seed']);np.random.seed(plan['initial_state_semantics']['RNG_seed'])
    mesh=Path(next(iter(plan['assets']))).parent
    env=ManipulationEnv(mesh,'candy_to_tray',horizon=32)
    env.reset(seed=plan['initial_state_semantics']['RNG_seed'])
    original_model=env.model
    model=mujoco.MjModel.from_xml_string(xml.decode())
    # No guessed remapping of a native vector into a different model ABI.
    for name in ('nq','nv','na','nu','nbody','njnt','ngeom','neq'):
        if getattr(model,name)!=getattr(original_model,name):raise ContactEvidenceRejected('Copied CAD/native shape differs: '+name)
    for name in ('jnt_type','jnt_qposadr','jnt_dofadr','geom_bodyid','actuator_trnid'):
        if not np.array_equal(getattr(model,name),getattr(original_model,name)):raise ContactEvidenceRejected('Copied CAD/native identity differs: '+name)
    if model.opt.integrator!=mujoco.mjtIntegrator.mjINT_DISCRETE:
        raise ContactEvidenceRejected('Actual compiled discrete enum required')
    declared_bin=np.asarray([float(v) for v in ET.fromstring(original).find(".//body[@name='bin']").get('pos').split()])
    if not np.array_equal(model.body('bin').pos,declared_bin):
        raise ContactEvidenceRejected('Actual compiled static bin position must match this original scene XML')
    if abs(float(model.opt.timestep)-condition['timestep_s'])>1e-12:
        raise ContactEvidenceRejected('Actual compiled declared timestep differs')
    del original_model
    env.xml=xml.decode();env.model=model;env.data=mujoco.MjData(model)
    native=np.asarray(selected['dispatch']['native_integration_state'],dtype=np.float64)
    spec=mujoco.mjtState.mjSTATE_INTEGRATION
    if native.shape!=(mujoco.mj_stateSize(model,spec),) or not np.isfinite(native).all():
        raise ContactEvidenceRejected('Source-bound finite native integration ABI required')
    mujoco.mj_setState(model,env.data,native,spec)
    env.data.time=0.;env.data.qacc_warmstart[:]=0.
    if condition['id']=='low-drop-rest':
        mujoco.mj_kinematics(model,env.data)
        current=object_geometry(model,env.data,env.object_geom_ids)['minimum_point_z_m']
        top=geometry_from_xml(xml)['bin_bottom_world_top_z_m']
        qpos=int(model.joint('candy_free').qposadr[0]);dof=int(model.joint('candy_free').dofadr[0])
        env.data.qpos[qpos+2]+=top+.015-current;env.data.qvel[dof:dof+6]=0.
    mujoco.mj_forward(model,env.data)
    env.data.qacc_warmstart[:]=0.  # declared NEW initial warmstart, after setup forward
    env.goal=model.body('bin').pos.copy();env.half=model.geom('bin_bottom').size[:2].copy();env.rim=.044
    env.command=np.asarray(selected['actual_dispatched_action9'][:6],float)
    env.command_before=env.command.copy();env.requested=env.command.copy();env.raw_action=np.zeros(6)
    env.steps=0;env.phase='new_contact_probe';env.record_substeps=False;env.substep_trace=[]
    env.last_distance=float(np.linalg.norm(env.jaw_center()-env.points().mean(0)))
    env.evaluator=FullTaskEvaluator(env.points(),env.goal,env.half,env.rim,model.opt.timestep)
    target=MuJoCoTargetProvider(env,target_urdf_sha256=plan['sources'][str(LEGACY/'config/explorer.urdf')],
        mesh_files=plan['assets'],phase_evidence_map={'new_contact_probe':['settled_in_goal']})
    target.boot='new-contact-'+folder.name
    return target


def object_geometry(model,data,object_geoms):
    import numpy as np
    points=[]
    for gid in sorted(object_geoms):
        half=model.geom_size[gid]
        corners=np.asarray([[x,y,z] for x in (-1,1) for y in (-1,1) for z in (-1,1)])*half
        points.extend(corners@data.geom_xmat[gid].reshape(3,3).T+data.geom_xpos[gid])
    points=np.asarray(points)
    return dict(minimum_point_z_m=float(points[:,2].min()),maximum_point_z_m=float(points[:,2].max()),
        mean_point_xyz_m=points.mean(0).tolist(),minimum_xy_m=points[:,:2].min(0).tolist(),maximum_xy_m=points[:,:2].max(0).tolist(),
        bounds_basis='same_oriented_geom_bounding_corners_as_original_evaluator_not_exact_ellipsoid_surface')


def actual_contacts(target):
    import numpy as np
    mj,env=target.mj,target.env;model,data=env.model,env.data;rows=[];force=np.zeros(6)
    for index,contact in enumerate(data.contact[:data.ncon]):
        pair=list(map(int,contact.geom));sides=[side for side,gid in enumerate(pair) if gid in env.object_geom_ids]
        if sides:
            if len(sides)!=1 or min(pair)<0:raise ContactEvidenceRejected('Unregistered self/flex contact in rigid-candy probe')
            side=sides[0];mj.mj_contactForce(model,data,index,force)
            rows.append(dict(geoms=pair,geom_names=[model.geom(gid).name for gid in pair],
                body_names=[model.body(int(model.geom_bodyid[gid])).name for gid in pair],object_side=side,
                position_m=contact.pos.tolist(),contact_frame_rows=contact.frame.tolist(),distance_m=float(contact.dist),
                local_wrench=force.tolist(),world_force_on_object_N=signed_world_force(contact.frame.tolist(),force.tolist(),object_side=side),
                active_constraint=bool(contact.efc_address>=0),exclude=int(contact.exclude),dim=int(contact.dim),
                friction=contact.friction.tolist(),solref=contact.solref.tolist(),solimp=contact.solimp.tolist()))
    return rows


def solver_record(data):
    niter=data.solver_niter.tolist();width=len(data.solver)//len(niter);iterations=[]
    for island,count in enumerate(niter):
        if count:
            if not 0<=count<=width:raise ContactEvidenceRejected('Native solver statistic ABI bound exceeded')
            values={name:getattr(data.solver,name)[island*width:island*width+count].tolist()
                for name in ('improvement','gradient','lineslope','nactive','nchange','neval','nupdate')}
            iterations.append(dict(island=island,values=values))
    return dict(nisland=int(data.nisland),niter=niter,nnz=data.solver_nnz.tolist(),iterations=iterations,
        warnings=[dict(number=int(item.number),lastinfo=int(item.lastinfo)) for item in data.warning])


def nonfinite_evidence(value):
    """Keep an explicit nonfinite tag in failure JSON; never replace with zero."""
    if isinstance(value,dict):return {key:nonfinite_evidence(child) for key,child in value.items()}
    if isinstance(value,list):return [nonfinite_evidence(child) for child in value]
    if isinstance(value,float) and not math.isfinite(value):return dict(native_nonfinite=repr(value))
    return value


def contact_outcome(rows,geometry):
    window=[row for row in rows if row['post_step_time_s']>=.8-1e-9]
    if not window:raise ContactEvidenceRejected('Whole declared terminal observation window required')
    supported=all(row['object']['minimum_point_z_m']>=geometry['bin_bottom_world_top_z_m']-.001 for row in window)
    settled=all(row['maximum_object_point_speed_m_s']<.05 for row in window)
    free=all(not row['other_object_contacts'] for row in window)
    inside=all(row['inside_bottom_xy'] for row in window)
    upward=sum(row['bottom_signed_world_force_N'][2] for row in window)/len(window)
    return dict(contact_recovered=bool(supported and settled and free and inside and upward>1e-4),
        terminal_supported=supported,terminal_settled=settled,terminal_other_contact_free=free,terminal_inside=inside,
        terminal_mean_bottom_signed_Z_N=upward,terminal_window_samples=len(window),
        minimum_saved_contact_distance_m=min((c['distance_m'] for row in rows for c in row['contacts']),default=None),
        minimum_object_point_z_m=min(row['object']['minimum_point_z_m'] for row in rows),
        maximum_bottom_normal_magnitude_N=max(row['bottom_normal_magnitude_N'] for row in rows),
        maximum_downward_bottom_force_N=max(max(0.,-row['bottom_signed_world_force_N'][2]) for row in rows),
        full_task_success_claimed=False,original_V9_outcome_amended=False)


@contextmanager
def trace_outputs(folder,native,progress):
    """Retain even a warning/nonfinite partial native trajectory, without pickle."""
    import numpy as np
    with (folder/'substeps.jsonl').open('xb') as trace:
        try:yield trace
        finally:
            trace.flush();os.fsync(trace.fileno())
            count=1+progress['native_states_recorded']
            with (folder/'native-states.npy').open('xb') as stream:
                np.save(stream,native[:count],allow_pickle=False);stream.flush();os.fsync(stream.fileno())


def simulate(target,condition,folder,progress):
    import mujoco
    import numpy as np
    env=target.env;model,data=env.model,env.data;spec=mujoco.mjtState.mjSTATE_INTEGRATION
    geometry=geometry_from_xml(env.xml.encode());steps=round(1./float(model.opt.timestep));native=np.empty((steps+1,mujoco.mj_stateSize(model,spec)))
    mujoco.mj_getState(model,data,native[0],spec);scratch=mujoco.MjData(model);held=data.ctrl.copy();rows=[]
    bottom=model.geom('bin_bottom').id;floor=model.geom('floor').id;bin_body=model.body('bin').id
    container=set(np.flatnonzero(model.geom_bodyid==bin_body).tolist());object_id=env.object_id
    with trace_outputs(folder,native,progress) as stream:
        for index in range(steps):
            before=float(data.time)
            if not np.array_equal(data.ctrl,held):raise ContactEvidenceRejected('Declared constant native controls changed')
            mujoco.mj_step(model,data);progress['native_steps']+=1
            mujoco.mj_getState(model,data,native[index+1],spec)
            progress['native_states_recorded']+=1
            # Preserve exact step-returned contacts/solver before any separate
            # geometry recomputation. Never mj_forward the live state here.
            contacts=actual_contacts(target);solver=solver_record(data)
            if not np.isfinite(native[index+1]).all() or any(item['number'] for item in solver['warnings']):
                _write(folder/'rejected-native-step.json',canonical(nonfinite_evidence(dict(index=index,
                    pre_step_time_s=before,post_step_time_s=float(data.time),contacts=contacts,solver=solver,
                    native_bytes_retained_in='native-states.npy',outcome='UNKNOWN_NATIVE_WARNING_OR_NONFINITE'))))
                raise ContactEvidenceRejected('Native nonfinite state/warning; attempt is UNKNOWN')
            mujoco.mj_setState(model,scratch,native[index+1],spec)
            mujoco.mj_kinematics(model,scratch);mujoco.mj_comPos(model,scratch);mujoco.mj_comVel(model,scratch)
            observed=object_geometry(model,scratch,env.object_geom_ids);velocity=np.zeros(6)
            mujoco.mj_objectVelocity(model,scratch,mujoco.mjtObj.mjOBJ_BODY,object_id,velocity,0)
            # Same conservative point-speed criterion as original evaluator.
            points=[]
            for gid in sorted(env.object_geom_ids):
                corners=np.asarray([[x,y,z] for x in (-1,1) for y in (-1,1) for z in (-1,1)])*model.geom_size[gid]
                points.extend(corners@scratch.geom_xmat[gid].reshape(3,3).T+scratch.geom_xpos[gid])
            speeds=np.linalg.norm(velocity[3:]+np.cross(velocity[:3],np.asarray(points)-scratch.xpos[object_id]),axis=1)
            bottom_contacts=[c for c in contacts if bottom in c['geoms']];floor_contacts=[c for c in contacts if floor in c['geoms']]
            other=[c for c in contacts if c['geoms'][1-c['object_side']] not in container|{floor}]
            row=dict(index=index,pre_step_time_s=before,post_step_time_s=float(data.time),contacts=contacts,solver=solver,
                object=observed,object_pose=np.r_[scratch.xpos[object_id],scratch.xquat[object_id]].tolist(),object_velocity_world=velocity.tolist(),
                maximum_object_point_speed_m_s=float(max(speeds)),
                bottom_signed_world_force_N=np.sum([c['world_force_on_object_N'] for c in bottom_contacts],axis=0).tolist() if bottom_contacts else [0.,0.,0.],
                floor_signed_world_force_N=np.sum([c['world_force_on_object_N'] for c in floor_contacts],axis=0).tolist() if floor_contacts else [0.,0.,0.],
                bottom_normal_magnitude_N=sum(max(0.,c['local_wrench'][0]) for c in bottom_contacts),other_object_contacts=other,
                inside_bottom_xy=bool(np.all(np.asarray(observed['minimum_xy_m'])>env.goal[:2]-env.half) and np.all(np.asarray(observed['maximum_xy_m'])<env.goal[:2]+env.half)),
                source='actual_native_mj_step_returned_contacts_plus_separate_poststep_kinematics')
            raw=canonical(row)+b'\n';stream.write(raw);rows.append(row)
            if stream.tell()>64*1024**2:raise ContactEvidenceRejected('Per-attempt contact trace byte budget exceeded')
    return contact_outcome(rows,geometry),steps


def run_attempt(plan,output,index):
    """Retain exact setup/capture failures without entering dynamics fallback."""
    case_index,condition_index=divmod(index,4)
    if not 0<=case_index<4:raise ContactEvidenceRejected('One of the16 frozen attempt indices required')
    case,condition=plan['cases'][case_index],plan['conditions'][condition_index]
    identity='v10-'+case['original_scenario']['id']+'-'+condition['id'];folder=Path(output)/'attempts'/identity
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    summary=dict(attempt_id=identity,case=case['original_scenario'],condition=condition['id'],status='SETUP_STARTED',
        original_success=case['original_success'],native_steps=0,physical_io=False,full_task_success_claimed=False,initial_state_status='NOT_RECORDED')
    _write(folder/'started.json',canonical(summary));started=time.monotonic();target=None
    progress=dict(native_steps=0,native_states_recorded=0)
    try:
        summary['resources_before']=require_resources(plan)
        target=build_provider(plan,case,condition,folder)
        summary['discarded_setup_native_steps']=plan['discarded_constructor_and_reset_native_steps_per_attempt']
        summary['actual_static_bin_position_m']=target.env.model.body('bin').pos.tolist()
        from explorer_shared.sim_initial_state import capture_initial_trial,save_initial_bundle
        bindings={'frozen_probe_protocol':dict(path=str(Path(output)/'frozen-protocol.json'),sha256=sha(_read(Path(output)/'frozen-protocol.json')))}
        bindings['initial_capture_helper']=dict(path=str(REPO/'shared/explorer_shared/sim_initial_state.py'),sha256=plan['sources'][str(REPO/'shared/explorer_shared/sim_initial_state.py')])
        bindings.update({Path(path).name.replace('.','_')+str(n):dict(path=path,sha256=checksum) for n,(path,checksum) in enumerate(plan['sources'].items())})
        bindings['original_scene_xml']=dict(path=plan['inputs'][case['original_scenario']['id']]['original-model.xml']['path'],sha256=case['source_bindings']['target_model_xml_sha256'])
        selected=plan['inputs'][case['original_scenario']['id']]['selected-native-dispatch.json'];bindings['selected_native_dispatch']=dict(path=selected['path'],sha256=selected['sha256'])
        bindings['new_compiled_input_xml']=dict(path=str(folder/'compiled-input-model.xml'),sha256=sha(_read(folder/'compiled-input-model.xml')))
        summary['initial_state_status']='CAPTURE_IN_PROGRESS'
        snapshot=capture_initial_trial(target,scenario=dict(seed=7401,attempt_id=identity,domain='SIMULATION',
            kind='NEW_CONTACT_INITIAL_CONDITION_NOT_OLD_V9_REPLAY',condition=condition,source_case=case),source_bindings=bindings)
        saved=save_initial_bundle(snapshot,folder/'initial-state')
        summary.update(initial_state_status='RECORDED',initial_state_manifest_sha256=saved['manifest_sha256'],
            initial_state_snapshot_files=saved['files'],initial_state_restore_reexecution='UNQUALIFIED',
            initial_state_snapshot_payload_bytes=sum(map(len,snapshot.payloads.values())))
        summary['initial_state']=dict(status='RECORDED',requested=True,helper_id=snapshot.document['profile'],
            helper_sha256=bindings['initial_capture_helper']['sha256'],manifest_path=str(folder/'initial-state/manifest.json'),
            manifest_sha256=saved['manifest_sha256'],payloads=snapshot.document['payloads'],files=saved['files'],
            total_payload_bytes=sum(map(len,snapshot.payloads.values())),restore_reexecution_verified=False,
            initial_RGB_render_reexecution='UNQUALIFIED',original_runtime_OS_closure='PARTIAL',physical_io=False)
        del snapshot;gc.collect()
        summary['status']='DYNAMICS_STARTED';_write(folder/'capture-completed.json',canonical(summary))
        outcome,steps=simulate(target,condition,folder,progress);summary.update(status='COMPLETED',native_steps=steps,outcome=outcome)
    except Exception as error:
        summary.update(status='UNKNOWN_SETUP_OR_EXECUTION_FAILURE',error_type=type(error).__name__,reason=str(error))
        if summary['initial_state_status']=='CAPTURE_IN_PROGRESS':summary['initial_state_status']='CAPTURE_FAILED'
        _write(folder/'failure-traceback.txt',traceback.format_exc().encode())
    finally:
        summary['native_steps']=progress['native_steps'];summary['native_states_recorded']=progress['native_states_recorded']
        if target is not None:
            summary['actual_sim_time_s']=float(target.env.data.time)
            summary['renderer_created']=target.env.renderer is not None
        summary['elapsed_s']=time.monotonic()-started;summary['resources_after']=resource_snapshot()
        _write(folder/'result.json',canonical(summary))
    return summary


def coordinate(plan,output,expected_sha256):
    require_resources(plan);started=time.monotonic();results=[]
    for index in range(plan['attempts_planned']):
        verify_sources(plan['sources'])
        command=[sys.executable,str(Path(__file__).resolve()),'--mode','attempt','--output',str(output),'--protocol-sha256',expected_sha256,'--attempt-index',str(index)]
        env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
        env.pop('MUJOCO_GL',None)
        with (Path(output)/('attempt-%02d.stdout'%index)).open('xb') as stdout,(Path(output)/('attempt-%02d.stderr'%index)).open('xb') as stderr:
            child=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env)
            try:code=child.wait(timeout=max(1.,min(60.,360.-(time.monotonic()-started))))
            except subprocess.TimeoutExpired:child.kill();child.wait();code=-9
        results.append(dict(index=index,pid=child.pid,exit_code=code,reaped=True))
        _write(Path(output)/('children-after-%02d.json'%index),canonical(results))
    rawresults=[]
    for path in sorted((Path(output)/'attempts').glob('*/result.json')):rawresults.append(dict(path=str(path),sha256=sha(_read(path)),result=json.loads(_read(path))))
    summary=dict(schema='explorer_controlled_contact_probe_results_v1',protocol_sha256=expected_sha256,children=results,attempts=rawresults,
        complete_16_attempt_receipts=len(rawresults)==16,all_original_scenarios_retained=True,
        physics_scope='NEW_COPIED_SIM_CONTACT_INITIAL_CONDITIONS_NOT_V9_REPLAY',physical_io=False,learned_policy_executed=False,
        full_task_success_claimed=False,optimizer_admission=False,H4_test=False,elapsed_s=time.monotonic()-started,resources=resource_snapshot())
    _write(Path(output)/'campaign-results.json',canonical(summary));print(canonical(summary).decode())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=('freeze','coordinate','attempt'),required=True)
    parser.add_argument('--campaign',type=Path);parser.add_argument('--campaign-receipt-sha256')
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--protocol-sha256');parser.add_argument('--attempt-index',type=int)
    args=parser.parse_args()
    if args.mode=='freeze':print(canonical(freeze_campaign(args.campaign,args.output,args.campaign_receipt_sha256)).decode())
    else:
        plan=load_protocol(args.output,args.protocol_sha256)
        if args.mode=='attempt':print(canonical(run_attempt(plan,args.output,args.attempt_index)).decode())
        else:coordinate(plan,args.output,args.protocol_sha256)


if __name__=='__main__':main()
