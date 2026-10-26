"""ENGINEERING_GLUE: source-bound task-relative transfer planning and target traces.

One Skill UUID, a new target version, complete ROSMASTER9 commands. Numerical
planning never sends motor IO; execution is an injected target SIM provider.
SCRIPTED_SIM fixtures are explicit software evidence, not learned source skills.
"""
from __future__ import annotations

from copy import deepcopy
import heapq
import io
import json
import math
import os
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from .contracts import ACTION_NAMES, ACTION_UNITS, canonical, digest
from .experiments import experiment_identity, experiment_id
from .kinematics import rigid_transform, rotation_vector
from .so101_station import NAMES as SOURCE_NAMES
from .transfer_mapping import TransferBlocked, sha
from .transfer_calibration import calibrated_aperture, command_for_aperture, task_registration

TRANSFER_SLUG='cross_embodiment_transfer'
SOURCE_SLUG='so101_source_learning'
SOURCE_CONTRACT='so101_follower_calibrated_position6_v1'
TARGET_CONTRACT='rosmaster_joint9_position6_velocity3_v1'
SCHEMA='explorer_skill_transfer_source_trace_v1'
INITIAL_CAPTURE_SCHEMA='explorer_transfer_initial_capture_request_v1'
INITIAL_CAPTURE_HELPER='explorer_manipulation_stationary_initial_v1'


def initial_state_capture_config(*,source_bindings=None,scenario=None):
    """Explicit server-side opt-in; no provider/model allocation or device IO."""
    from . import sim_initial_state as initial
    if source_bindings is not None and not isinstance(source_bindings,dict):raise TransferBlocked('INITIAL_CAPTURE_SOURCE_BINDINGS_REQUIRED','A')
    if scenario is not None and not isinstance(scenario,dict):raise TransferBlocked('INITIAL_CAPTURE_SCENARIO_REQUIRED','A')
    return dict(schema=INITIAL_CAPTURE_SCHEMA,helper_id=INITIAL_CAPTURE_HELPER,
        helper_sha256=sha(Path(initial.__file__).read_bytes()),source_bindings=deepcopy(source_bindings or {}),scenario=deepcopy(scenario or {}))


def _member(root,name):
    if not isinstance(name,str) or Path(name).is_absolute():raise TransferBlocked('RELATIVE_SOURCE_ARTIFACT_REQUIRED','A')
    path=(root/name).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise TransferBlocked('SOURCE_ARTIFACT_PATH_ESCAPES_OR_MISSING','A',name)
    return path


def load_source_trace(path,expected_sha256):
    """Immutable executed source trace, or explicitly labelled scripted fixture."""
    path=Path(path).resolve();raw=path.read_bytes()
    if sha(raw)!=expected_sha256:raise TransferBlocked('SOURCE_TRACE_SHA_CHANGED','A')
    source=json.loads(raw)
    if source.get('schema')!=SCHEMA or source.get('experiment_slug')!=SOURCE_SLUG:raise TransferBlocked('SOURCE_TRACE_CONTRACT_REQUIRED','A')
    if source.get('action_contract_id')!=SOURCE_CONTRACT or source.get('action_names')!=list(SOURCE_NAMES):raise TransferBlocked('BIMANUAL_OR_OTHER_SOURCE_NEEDS_EXPLICIT_ROLE_PLAN','A','No silent12D→6D reduction')
    if source.get('task_type') in (None,'hold','joint_trajectory','trajectory'):raise TransferBlocked('NEW_QUALIFIED_OBJECT_SOURCE_SKILL_REQUIRED','C','Historical stationary hold is not an object skill')
    if source.get('source_kind') not in ('SCRIPTED_SIM','QUALIFIED_SOURCE_MODEL'):raise TransferBlocked('EXPLICIT_SOURCE_METHOD_REQUIRED','C')
    if source['source_kind']=='QUALIFIED_SOURCE_MODEL':
        qualification=source.get('qualification') or {}
        if (qualification.get('independent_closed_loop_object_success') is not True or not qualification.get('source_model_sha256') or
                not qualification.get('processor_sha256') or not qualification.get('evaluation_run_ids') or qualification.get('raw_episode_replay_only') is not False):
            raise TransferBlocked('SOURCE_MODEL_OBJECT_QUALIFICATION_REQUIRED','C')
    inventory=source.get('source_files_sha256') or {};files={str(path):expected_sha256}
    if not inventory:raise TransferBlocked('SOURCE_PROVENANCE_FILES_REQUIRED','A')
    for name,expected in inventory.items():
        artifact=_member(path.parent,name)
        if artifact.stat().st_size>256*1024**2 or sha(artifact.read_bytes())!=expected:raise TransferBlocked('SOURCE_ARTIFACT_SHA_CHANGED','A',name)
        files[str(artifact)]=expected
    if not source.get('frames') or not source.get('skill_id') or type(source.get('skill_version')) is not int or source['skill_version']<1:raise TransferBlocked('SOURCE_SKILL_AND_ACTUAL_TRACE_REQUIRED','C')
    if source['source_kind']=='QUALIFIED_SOURCE_MODEL':
        qualification=source['qualification']
        if not set(qualification.get(key) for key in ('source_model_sha256','processor_sha256','evaluation_receipt_sha256')).issubset(set(inventory.values())):raise TransferBlocked('QUALIFICATION_MUST_BIND_RETAINED_MODEL_PROCESSOR_EVALUATION','C')
    try:valid_uuid=str(uuid.UUID(source['skill_id']))==source['skill_id']
    except (ValueError,TypeError,AttributeError):valid_uuid=False
    split=source.get('split') or {}
    if not valid_uuid or split.get('partition') not in ('train','development','sealed_test') or any(not split.get(key) for key in ('episode_id','scene_id','session_id','group_id')):raise TransferBlocked('SOURCE_UUID_AND_ORIGINAL_SPLIT_GROUPS_REQUIRED','A')
    for index,frame in enumerate(source['frames']):
        values=np.asarray(frame.get('native_action6'),float)
        if values.shape!=(6,) or not np.isfinite(values).all() or np.any(abs(values[:5])>100) or not 0<=values[5]<=100 or not frame.get('phase_id') or frame.get('reference') not in ('object','goal'):raise TransferBlocked('NAMED_SOURCE_FRAME_REQUIRED','A',str(index))
        if type(frame.get('minimum_target_steps',1)) is not int or not 1<=frame.get('minimum_target_steps',1)<=200:raise TransferBlocked('BOUNDED_SOURCE_DWELL_STEPS_REQUIRED','A')
        if 'maximum_target_steps' in frame and (type(frame['maximum_target_steps']) is not int or not 1<=frame['maximum_target_steps']<=2000):raise TransferBlocked('BOUNDED_PHASE_EXECUTION_BUDGET_REQUIRED','A')
        if 'advance_on_verified_phase' in frame and type(frame['advance_on_verified_phase']) is not bool:raise TransferBlocked('EXPLICIT_PHASE_COMPLETION_MODE_REQUIRED','A')
        if frame.get('source_kind')!=source['source_kind']:raise TransferBlocked('SOURCE_TRACE_METHOD_CANNOT_CHANGE_PER_FRAME','C')
    source_rgb_pixels=[]
    for frame in source['frames']:
        name=frame.get('source_rgb_file')
        if name:
            if name not in inventory:raise TransferBlocked('SOURCE_RGB_MUST_BE_RETAINED_AND_HASH_BOUND','A')
            image=Image.open(_member(path.parent,name));image.load()
            source_rgb_pixels.append(sha(np.asarray(image.convert('RGB')).tobytes()))
    return dict(document=source,source_files_sha256=files,manifest_path=str(path),manifest_sha256=expected_sha256,source_rgb_pixel_sha256=source_rgb_pixels)


def _free_grid(map_document):
    grid=np.asarray(map_document.get('cells'))
    resolution=map_document.get('resolution_m');radius=map_document.get('robot_radius_m')
    if (grid.ndim!=2 or not grid.size or grid.size>65536 or not np.isin(grid,[-1,0,1]).all() or
            not isinstance(resolution,(float,int)) or not 0<resolution<=1 or not isinstance(radius,(float,int)) or not 0<radius<=2 or
            not map_document.get('source_id') or not map_document.get('robot_geometry_sha256') or map_document.get('frame_id')!='world'):
        raise TransferBlocked('SOURCE_BOUND_OBSERVED_NAVIGATION_MAP_REQUIRED','A')
    origin=np.asarray(map_document.get('origin_xy_m'),float)
    if origin.shape!=(2,) or not np.isfinite(origin).all():raise TransferBlocked('MAP_ORIGIN_REQUIRED','A')
    free=grid==0;inflated=free.copy();padding=int(math.ceil(radius/resolution+.5))
    # Unknown/outside are occupied; inflation uses actual robot footprint radius.
    for y,x in np.argwhere(free):
        for dy in range(-padding,padding+1):
            for dx in range(-padding,padding+1):
                if max(abs(dx)-.5,0)**2+max(abs(dy)-.5,0)**2<=(radius/resolution)**2 and (not 0<=y+dy<grid.shape[0] or not 0<=x+dx<grid.shape[1] or not free[y+dy,x+dx]):inflated[y,x]=False
    return inflated,float(resolution),origin


def _path(free,start,goal):
    queue=[(0.,0.,start)];cost={start:0.};parent={}
    while queue:
        unused,distance,current=heapq.heappop(queue)
        if current==goal:
            result=[current]
            while current in parent:current=parent[current];result.append(current)
            return list(reversed(result))
        if distance==cost[current]:
            y,x=current
            for nxt in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                if 0<=nxt[0]<free.shape[0] and 0<=nxt[1]<free.shape[1] and free[nxt] and distance+1<cost.get(nxt,float('inf')):
                    cost[nxt]=distance+1;parent[nxt]=current
                    priority=distance+1+abs(nxt[0]-goal[0])+abs(nxt[1]-goal[1]);heapq.heappush(queue,(priority,distance+1,nxt))
    return None


def derive_cad_tool_adapter(source_chain,target_chain,*,source_closing_axis_tip,target_closing_axis_tip):
    """Compute approach from the actual terminal wrist→tip CAD translation.

    Closing axes are explicit CAD conventions, not measured physical directions.
    Their projection makes an orthonormal tool frame without matching joint zero
    poses. The result authorizes geometry/SIM only, never a physical movement.
    """
    def derive(chain,closing):
        terminal=chain.joints[-1]
        if terminal.kind!='fixed':raise TransferBlocked('TERMINAL_FIXED_CAD_TOOL_FRAME_REQUIRED','A',chain.tip)
        offset=terminal.origin[:3,3];length=float(np.linalg.norm(offset))
        closing=np.asarray(closing,float)
        if length<.001 or closing.shape!=(3,) or not np.isfinite(closing).all():raise TransferBlocked('DECLARED_CAD_TOOL_APPROACH_AND_CLOSING_AXES_REQUIRED','A')
        approach=terminal.origin[:3,:3].T@offset/length
        projected=closing-approach*float(closing@approach)
        if np.linalg.norm(projected)<1e-6:raise TransferBlocked('TOOL_CLOSING_AXIS_PARALLEL_TO_APPROACH','A')
        y=projected/np.linalg.norm(projected);x=np.cross(y,approach);matrix=np.eye(4);matrix[:3,:3]=np.column_stack([x,y,approach])
        return rigid_transform(matrix).tolist(),dict(terminal_joint=terminal.name,terminal_origin=terminal.origin.tolist(),
            declared_closing_axis_tip=closing.tolist(),approach_axis_tip=approach.tolist(),
            basis='terminal_CAD_translation_and_explicit_closing_convention_not_joint_zero_matching')
    source,source_proof=derive(source_chain,source_closing_axis_tip);target,target_proof=derive(target_chain,target_closing_axis_tip)
    return dict(schema='explorer_skill_transfer_common_tool_adapter_v1',basis='CAD_derived',source_tip=source_chain.tip,target_tip=target_chain.tip,
        source_urdf_sha256=source_chain.source_sha256,target_urdf_sha256=target_chain.source_sha256,
        source_tip_from_common_tool=source,target_tip_from_common_tool=target,source_geometry=source_proof,target_geometry=target_proof,
        physical_tool_registration_verified=False,physical_execution_authorized=False)


class TaskRelativeMapper:
    def __init__(self,source_chain,target_chain,source_coordinates,target_coordinates,*,source_profile,source_aperture,target_aperture,task_frames,target_low,target_high,collision_checker,tool_frame_adapter=None):
        self.source,self.target=source_chain,target_chain;self.source_coordinates,self.target_coordinates=source_coordinates,target_coordinates
        self.source_profile=deepcopy(source_profile);self.source_aperture,self.target_aperture=source_aperture,target_aperture
        self.registrations={role:task_registration(task_frames,role) for role in ('object','goal')}
        adapter=deepcopy(tool_frame_adapter or {})
        if (adapter.get('basis') not in ('CAD_derived','virtual_declared','measured') or adapter.get('source_tip')!=source_chain.tip or adapter.get('target_tip')!=target_chain.tip or
                adapter.get('source_urdf_sha256')!=source_chain.source_sha256 or adapter.get('target_urdf_sha256')!=target_chain.source_sha256):raise TransferBlocked('EXPLICIT_COMMON_TOOL_FRAME_ADAPTER_REQUIRED','A','Different CAD tip axes are not implicitly equal')
        try:
            self.source_tip_from_common=rigid_transform(adapter['source_tip_from_common_tool']);self.target_tip_from_common=rigid_transform(adapter['target_tip_from_common_tool'])
        except (KeyError,ValueError) as error:raise TransferBlocked('COMMON_TOOL_FRAME_SE3_INVALID','A') from error
        if adapter['basis']=='measured' and not adapter.get('calibration_ids'):raise TransferBlocked('MEASURED_TOOL_FRAME_ARTIFACT_IDS_REQUIRED','B')
        self.tool_frame_adapter=adapter
        self.low,self.high=np.asarray(target_low,float),np.asarray(target_high,float);self.collision_checker=collision_checker;self.plan_rejections=[]
        if self.low.shape!=(6,) or self.high.shape!=(6,) or np.any(self.low>=self.high) or not np.isfinite(np.r_[self.low,self.high]).all():raise TransferBlocked('FACTORY_TARGET_LIMITS_REQUIRED','A')
        if (source_profile.get('source_urdf_sha256')!=source_chain.source_sha256 or source_profile.get('source_calibration_sha256')!=source_coordinates.sha256 or
                tuple(source_chain.names)!=tuple(SOURCE_NAMES[:5])):raise TransferBlocked('SOURCE_PROFILE_CAD_OR_CALIBRATION_CHANGED','A')
        if not callable(collision_checker):raise TransferBlocked('TARGET_CAD_COLLISION_PROVIDER_REQUIRED','A')
        # URDF rounded1.5708 differs from exact native90deg. Intersect the
        # solver domain BEFORE IK, without clipping outputs or editing assets.
        self.target_solver=deepcopy(target_chain)
        native_a=target_coordinates.radians(self.low);native_b=target_coordinates.radians(self.high)
        self.target_solver.lower=np.maximum(target_chain.lower,np.minimum(native_a,native_b))
        self.target_solver.upper=np.minimum(target_chain.upper,np.maximum(native_a,native_b))
        if np.any(self.target_solver.lower>=self.target_solver.upper):raise TransferBlocked('NO_COMMON_NATIVE_AND_CAD_IK_DOMAIN','A')
        self.lineage=dict(effective_IK_lower_rad=self.target_solver.lower.tolist(),effective_IK_upper_rad=self.target_solver.upper.tolist(),original_native_low=self.low.tolist(),original_native_high=self.high.tolist(),tool_frame_adapter_sha256=digest(adapter),tool_frame_basis=adapter['basis'],mapper_implementation_sha256=sha(Path(__file__).read_bytes()),source_urdf_sha256=source_chain.source_sha256,target_urdf_sha256=target_chain.source_sha256,
            source_calibration_sha256=source_coordinates.sha256,target_native_coordinate_source_sha256=target_coordinates.source_sha256,
            source_profile_sha256=digest(source_profile),source_aperture_sha256=digest(source_aperture),target_aperture_sha256=digest(target_aperture),task_frames_sha256=digest(task_frames))

    def desired_tool(self,frame):
        native=self.source_coordinates._native(frame['native_action6']);profile=self.source_profile
        positions=np.asarray([profile['joints'][name]['scale_rad_per_native_unit']*native[index]+profile['joints'][name]['offset_rad'] for index,name in enumerate(self.source.names)])
        # Exact calibrated encoder rounding is retained separately from affine
        # geometry; the original source native action is never overwritten.
        self.source.forward(positions)
        source_base_from_common=self.source.forward(positions)@self.source_tip_from_common
        target_base_from_common=self.registrations[frame['reference']].transfer(source_base_from_common)
        return target_base_from_common@np.linalg.inv(self.target_tip_from_common)

    def _solve(self,world_from_tool,world_from_base,prior9,gripper):
        desired=np.linalg.inv(world_from_base)@world_from_tool
        q=self.target_coordinates.radians(prior9[:6]);result=self.target_solver.inverse(desired,q,tolerance_m=.002,orientation_weight=.05,orientation_tolerance_rad=.12,max_iterations=100,restarts=1)
        if not result.reached:
            self.plan_rejections.append(dict(code='TARGET_IK_NOT_REACHED',ik=result.as_dict(),world_from_base=world_from_base.tolist()));return None
        native=self.target_coordinates.servo(result.positions,gripper)
        if np.any(native<self.low-1e-8) or np.any(native>self.high+1e-8):
            self.plan_rejections.append(dict(code='TARGET_NATIVE_LIMITS',requested_native6=native.tolist(),world_from_base=world_from_base.tolist()));return None
        collision=self.collision_checker(q,result.positions,world_from_base)
        if (not isinstance(collision,dict) or collision.get('target_urdf_sha256')!=self.target.source_sha256 or
                not (collision.get('continuous_segment_checked') is True or (collision.get('sampled_segment_checked') is True and 0<collision.get('max_joint_sample_spacing_rad',0)<=.02)) or not collision.get('scene_sha256')):raise TransferBlocked('SOURCE_BOUND_TARGET_CAD_SEGMENT_CHECK_REQUIRED','A')
        if collision.get('collision_free') is not True:
            self.plan_rejections.append(dict(code='TARGET_CAD_COLLISION',collision=deepcopy(collision),world_from_base=world_from_base.tolist()));return None
        return dict(arm_command6=native.tolist(),ik=result.as_dict(),collision=deepcopy(collision))

    def _cartesian_path(self,tool,base,prior,gripper):
        start=base@self.target.forward(self.target_coordinates.radians(prior[:6]))
        relative=rotation_vector(start[:3,:3].T@tool[:3,:3]);angle=float(np.linalg.norm(relative))
        count=max(2,int(np.ceil(np.linalg.norm(tool[:3,3]-start[:3,3])/.015)),int(np.ceil(angle/.08)))
        if count>40:return None
        path=[];seed=prior.copy()
        for index in range(1,count+1):
            fraction=index/count;pose=np.eye(4);pose[:3,3]=start[:3,3]+fraction*(tool[:3,3]-start[:3,3])
            if angle<1e-12:pose[:3,:3]=start[:3,:3]
            else:
                axis=relative/angle;x,y,z=axis;skew=np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
                rotation=np.eye(3)+np.sin(fraction*angle)*skew+(1-np.cos(fraction*angle))*(skew@skew)
                pose[:3,:3]=start[:3,:3]@rotation
            solved=self._solve(pose,base,seed,gripper)
            if solved is None:return None
            path.append(dict(**solved,world_from_tool=pose.tolist()));seed=np.r_[solved['arm_command6'],prior[6:]]
        return path

    def plan(self,frame,observation):
        self.plan_rejections=[]
        prior=np.asarray(observation.get('preceding_command9'),float)
        if prior.shape!=(9,) or not np.isfinite(prior).all():raise TransferBlocked('ACTUAL_PRECEDING_NATIVE_COMMAND9_REQUIRED','A','Never construct prior9 from joint feedback plus measured base velocity')
        base=rigid_transform(observation['world_from_base']);tool=self.desired_tool(frame)
        gap=calibrated_aperture(self.source_aperture,float(frame['native_action6'][5]));gripper=command_for_aperture(self.target_aperture,gap)
        solved=self._solve(tool,base,prior,gripper)
        if solved is not None:
            return dict(**solved,arm_target_is_final=True,desired_world_from_tool=tool.tolist(),navigation_required=False,navigation_path_world_xy=[],base_command3=[0.,0.,0.],
                        target_action9=solved['arm_command6']+[0.,0.,0.],neutral_base_is_explicit_command=True,method='OBJECT_RELATIVE_FK_IK_WITH_APERTURE_AND_CAD_COLLISION',physical_io=False)
        if self.plan_rejections and self.plan_rejections[-1]['code']=='TARGET_CAD_COLLISION':
            path=self._cartesian_path(tool,base,prior,gripper)
            if path:
                first=path[0]
                return dict(**{key:value for key,value in first.items() if key!='world_from_tool'},arm_target_is_final=False,
                    desired_world_from_tool=tool.tolist(),local_waypoint_world_from_tool=first['world_from_tool'],arm_path=path,navigation_required=False,navigation_path_world_xy=[],
                    base_command3=[0.,0.,0.],target_action9=first['arm_command6']+[0.,0.,0.],neutral_base_is_explicit_command=True,
                    method='OBJECT_RELATIVE_CARTESIAN_SEGMENTS_WITH_CAD_COLLISION',physical_io=False)
        map_document=observation.get('observed_navigation_map')
        if not map_document:raise TransferBlocked('ARM_EFFECT_UNREACHABLE_OR_COLLIDING_NEEDS_OBSERVED_BASE_NAVIGATION','A',evidence={'desired_world_from_tool':tool.tolist(),'actual_geometric_rejections':self.plan_rejections[-32:]})
        free,resolution,origin=_free_grid(map_document)
        cell=np.floor((base[:2,3]-origin)/resolution).astype(int);start=(int(cell[1]),int(cell[0]))
        if not (0<=start[0]<free.shape[0] and 0<=start[1]<free.shape[1] and free[start]):raise TransferBlocked('BASE_START_OUTSIDE_OBSERVED_FREE_FOOTPRINT','A')
        candidates=sorted(map(tuple,np.argwhere(free)),key=lambda xy:float(np.linalg.norm(origin+resolution*(np.array([xy[1],xy[0]])+.5)-base[:2,3])))
        solution=None
        for candidate in candidates[:128]:
            path=_path(free,start,candidate)
            if path and len(path)>1:
                world=base.copy();world[:2,3]=origin+resolution*(np.array([candidate[1],candidate[0]])+.5)
                reached=self._solve(tool,world,prior,gripper)
                if reached is not None:
                    solution=dict(**reached,navigation_path_world_xy=[(origin+resolution*(np.array([x,y])+.5)).tolist() for y,x in path],candidate_world_from_base=world.tolist());break
        if solution is None:raise TransferBlocked('OBSERVED_NAVIGATION_HAS_NO_COLLISION_FREE_REACHABLE_BASE_POSE','A',evidence={'actual_geometric_rejections':self.plan_rejections[-32:]})
        waypoint=np.asarray(solution['navigation_path_world_xy'][1]);delta=waypoint-base[:2,3]
        velocity_world=delta*min(1.,.12/max(float(np.linalg.norm(delta)),1e-12));velocity_local=base[:2,:2].T@velocity_world
        # Navigation is a real request inside the one9D action contract. Arm
        # stays at its last literal command, never zero-filled or separate robot.
        return dict(**solution,desired_world_from_tool=tool.tolist(),navigation_required=True,base_command3=[*velocity_local.tolist(),0.],
                    target_action9=prior[:6].tolist()+[*velocity_local.tolist(),0.],neutral_base_is_explicit_command=False,
                    navigation_map_sha256=digest(map_document),method='OBSERVED_ASTAR_BASE_REPOSITION_THEN_TASK_RELATIVE_IK',physical_io=False)


def _json_value(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,dict):return {key:_json_value(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [_json_value(item) for item in value]
    return value


def _immutable(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())


def _capture_initial_state(target,folder,request,source_bundle,provider,target_version,target_skill_spec,seed):
    """One registered capture only; before first render/observe/advance."""
    from . import sim_initial_state as initial
    if (not isinstance(request,dict) or set(request)!={'schema','helper_id','helper_sha256','source_bindings','scenario'} or
            request.get('schema')!=INITIAL_CAPTURE_SCHEMA or request.get('helper_id')!=INITIAL_CAPTURE_HELPER):
        raise TransferBlocked('REGISTERED_INITIAL_CAPTURE_REQUEST_REQUIRED','A')
    if initial.PROFILE!=INITIAL_CAPTURE_HELPER:raise TransferBlocked('REGISTERED_INITIAL_CAPTURE_PROFILE_CHANGED','A')
    helper=Path(initial.__file__).resolve();recorder=Path(__file__).resolve()
    if request['helper_sha256']!=sha(helper.read_bytes()):raise TransferBlocked('INITIAL_CAPTURE_HELPER_SOURCE_CHANGED','A')
    if not isinstance(request['source_bindings'],dict) or not isinstance(request['scenario'],dict):raise TransferBlocked('INITIAL_CAPTURE_SOURCE_BINDINGS_AND_SCENARIO_REQUIRED','A')
    if 'seed' in request['scenario'] and (type(request['scenario']['seed']) is not type(seed) or request['scenario']['seed']!=seed):raise TransferBlocked('INITIAL_CAPTURE_DECLARED_SEED_DIFFERS_FROM_RESET','A')
    source=source_bundle['document'];bindings=deepcopy(request['source_bindings'])
    reserved=dict(initial_capture_helper=dict(path=str(helper),sha256=request['helper_sha256']),
        target_trial_recorder=dict(path=str(recorder),sha256=sha(recorder.read_bytes())),
        transfer_source_manifest=dict(path=source_bundle['manifest_path'],sha256=source_bundle['manifest_sha256']))
    for role,entry in reserved.items():
        if role in bindings and bindings[role]!=entry:raise TransferBlocked('INITIAL_CAPTURE_RESERVED_SOURCE_BINDING_CHANGED','A',role)
        bindings[role]=entry
    scenario=dict(seed=seed,task_type=source['task_type'],skill_id=source['skill_id'],source_skill_version=source['skill_version'],
        target_skill_version=target_version,target_skill_spec_sha256=digest(target_skill_spec),source_manifest_sha256=source_bundle['manifest_sha256'],
        target_action_contract_id=TARGET_CONTRACT,target_provider_identity_sha256=digest(provider),declared_trial_scenario=deepcopy(request['scenario']))
    # The helper owns its stationary-profile/native ABI and resource limits.
    # MJB can be ~153MiB: retain files, never inline/copy binary payloads into result.
    bundle=initial.capture_initial_trial(target,scenario=scenario,source_bindings=bindings)
    manifest_sha256=bundle.sha256;initial.validate_bundle(bundle,manifest_sha256)
    destination=folder/'initial-state';receipt=initial.save_initial_bundle(bundle,destination)
    if receipt.get('manifest_sha256')!=manifest_sha256:raise TransferBlocked('INITIAL_CAPTURE_SAVE_SHA_DIFFERS','A')
    manifest=destination/'manifest.json'
    if not manifest.is_file() or manifest.is_symlink():raise TransferBlocked('INITIAL_CAPTURE_MANIFEST_WRITE_UNVERIFIED','A')
    manifest_raw=manifest.read_bytes()
    if sha(manifest_raw)!=manifest_sha256:raise TransferBlocked('INITIAL_CAPTURE_MANIFEST_WRITE_UNVERIFIED','A')
    saved=json.loads(manifest_raw);payloads=deepcopy(saved['payloads'])
    return dict(status='RECORDED',requested=True,helper_id=INITIAL_CAPTURE_HELPER,helper_sha256=request['helper_sha256'],
        boundary='after_target_reset_before_first_observe_or_advance',manifest_path=str(manifest.resolve()),manifest_sha256=manifest_sha256,
        artifact_folder='initial-state',files=receipt['files'],total_payload_bytes=sum(row['bytes'] for row in payloads.values()),
        payloads=payloads,scenario_sha256=digest(_json_value(scenario)),source_bindings=deepcopy(saved.get('source_bindings') or {}),
        restore_reexecution_verified=False,initial_RGB_render_reexecution='UNQUALIFIED',original_runtime_OS_closure='PARTIAL',physical_io=False)


def record_target_trial(source_bundle,mapper,target,folder,*,target_version,target_skill_spec,seed,max_steps=2000,initial_state_capture=None):
    """Finite-dynamics provider only; all actual dispatch/measurements are saved.

    target.reset/observe/advance/report are deliberately injected. Its observation
    contains target-rendered RGB + literal preceding command9, and advance returns
    actual dispatched9 + independent evaluator. No source RGB is used as input.
    Initial capture is an explicit registered opt-in, never a retroactive claim.
    """
    source=source_bundle['document'];folder=Path(folder)
    if folder.exists():raise TransferBlocked('NEW_IMMUTABLE_TARGET_TRIAL_DIRECTORY_REQUIRED','A')
    if type(target_version) is not int or target_version<=source['skill_version']:raise TransferBlocked('SAME_SKILL_NEW_TARGET_VERSION_REQUIRED','A')
    if (target_skill_spec.get('skill_id')!=source['skill_id'] or target_skill_spec.get('version')!=target_version or not target_skill_spec.get('stages')):raise TransferBlocked('TARGET_SAME_SKILL_VERSION_AND_COMPLETE_STAGES_REQUIRED','A')
    required={stage['id'] for stage in target_skill_spec['stages']}
    if not {frame['phase_id'] for frame in source['frames']}.issubset(required):raise TransferBlocked('SOURCE_TO_TARGET_PHASE_MAPPING_REQUIRED','A')
    if type(max_steps) is not int or not 1<=max_steps<=20000:raise TransferBlocked('BOUNDED_TARGET_TRIAL_REQUIRED','A')
    if getattr(target,'physical_io',None) is not False:raise TransferBlocked('ONLY_NONPHYSICAL_TARGET_PROVIDER_AUTHORIZED','B')
    provider=deepcopy(getattr(target,'provider_identity',{}))
    if provider.get('kind') not in ('MOCK','MUJOCO_CAD') or not provider.get('implementation_sha256') or not provider.get('target_model_sha256'):raise TransferBlocked('SOURCE_BOUND_TARGET_PROVIDER_IDENTITY_REQUIRED','A')
    physics_provider=provider['kind']=='MUJOCO_CAD' and bool(provider.get('engine_version')) and bool(provider.get('cad_mesh_sha256'))
    capture_request=deepcopy(initial_state_capture)
    initial_state=dict(status='NOT_RECORDED',requested=capture_request is not None,
        reason='CAPTURE_NOT_REQUESTED' if capture_request is None else 'TARGET_RESET_OR_CAPTURE_NOT_COMPLETED',
        boundary='after_target_reset_before_first_observe_or_advance',restore_reexecution_verified=False,physical_io=False)
    folder.mkdir(parents=True);rows=[];failure=None;source_index=0;seen_frames=set()
    try:
        target.reset(seed=seed)
        if capture_request is not None:
            initial_state.update(status='CAPTURE_FAILED',reason='REGISTERED_CAPTURE_DID_NOT_COMPLETE')
            try:initial_state=_capture_initial_state(target,folder,capture_request,source_bundle,provider,target_version,target_skill_spec,seed)
            except Exception as error:
                initial_state['error_type']=type(error).__name__;initial_state['detail']=str(error)
                manifest=folder/'initial-state'/'manifest.json'
                if manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size<=8*1024**2:
                    initial_state.update(artifact_folder='initial-state',manifest_path=str(manifest.resolve()),
                        manifest_sha256=sha(manifest.read_bytes()),artifact_retained_but_capture_unverified=True)
                if isinstance(error,TransferBlocked):raise
                raise TransferBlocked('INITIAL_STATE_CAPTURE_FAILED_BEFORE_TARGET_DISPATCH','A',str(error)) from error
        for source_index,frame in enumerate(source['frames']):
            reached=False;attempts=0
            phase_budget=frame.get('maximum_target_steps',max_steps)
            while not reached and len(rows)<max_steps and attempts<phase_budget:
                observation=target.observe();image=np.asarray(observation['target_rgb'])
                renderer=observation.get('renderer_receipt') or {}
                if (image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3 or renderer.get('embodiment')!='ROSMASTER' or
                        renderer.get('rendered_from_target_physics') is not True or renderer.get('target_model_sha256')!=provider['target_model_sha256'] or not renderer.get('frame_id')):
                    raise TransferBlocked('FRESH_TARGET_ROSMASTER_RENDER_REQUIRED','A')
                if sha(image.tobytes()) in source_bundle.get('source_rgb_pixel_sha256',[]):raise TransferBlocked('SOURCE_RGB_PIXELS_CANNOT_SUBSTITUTE_TARGET_RENDER','A')
                if renderer['frame_id'] in seen_frames:raise TransferBlocked('FRESH_TARGET_FRAME_ID_REQUIRED','A')
                seen_frames.add(renderer['frame_id'])
                raw=io.BytesIO();Image.fromarray(image).save(raw,format='PNG');filename='rgb/'+str(len(rows)).zfill(6)+'.png'
                _immutable(folder/filename,raw.getvalue())
                _immutable(folder/'observations'/(str(len(rows)).zfill(6)+'.json'),canonical(_json_value({key:value for key,value in observation.items() if key!='target_rgb'})))
                plan=mapper.plan(frame,observation)
                actual_request=plan['target_action9'];dispatch_preparation=None
                if callable(getattr(target,'prepare_dispatch',None)):actual_request,dispatch_preparation=target.prepare_dispatch(actual_request)
                request=dict(source_frame_index=source_index,phase_id=frame['phase_id'],preceding_command9=observation['preceding_command9'],actual_requested_action9=actual_request,dispatch_preparation=dispatch_preparation,plan=plan,renderer_receipt=renderer,target_rgb_file=filename,target_rgb_sha256=sha(raw.getvalue()))
                _immutable(folder/'requests'/(str(len(rows)).zfill(6)+'.json'),canonical(request))
                dispatch=target.advance(actual_request,phase_id=frame['phase_id'])
                _immutable(folder/'dispatch'/(str(len(rows)).zfill(6)+'.json'),canonical(dispatch))
                actual=np.asarray(dispatch.get('dispatched_action9'),float)
                if (actual.shape!=(9,) or not np.isfinite(actual).all() or dispatch.get('physical_io') is not False or
                        dispatch.get('finite_dynamics_step_completed') is not True or not dispatch.get('receipt_id')):
                    raise TransferBlocked('ACTUAL_TARGET_SIMULATED_9D_DISPATCH_REQUIRED','A')
                if physics_provider:
                    captured,before,after=renderer.get('stamp') or {},dispatch.get('pre_action_stamp') or {},dispatch.get('dispatch_stamp') or {}
                    if (not captured.get('clock_domain') or not captured.get('boot_id') or any(item.get('clock_domain')!=captured['clock_domain'] or item.get('boot_id')!=captured['boot_id'] for item in (before,after)) or
                            any(type(item.get('monotonic_ns')) is not int or item['monotonic_ns']<0 for item in (captured,before,after)) or
                            not captured['monotonic_ns']<=before['monotonic_ns']<after['monotonic_ns']):raise TransferBlocked('TARGET_RENDER_TO_DISPATCH_CAUSAL_CLOCK_REQUIRED','A')
                rows.append(dict(index=len(rows),source_frame_index=source_index,source_native_action6=deepcopy(frame['native_action6']),phase_id=frame['phase_id'],
                    target_rgb_file=filename,target_rgb_sha256=sha(raw.getvalue()),renderer_receipt=deepcopy(renderer),
                    preceding_command9=deepcopy(observation['preceding_command9']),requested_action9=actual_request,dispatch_preparation=dispatch_preparation,geometric_target_action9=plan['target_action9'],actual_dispatched_action9=actual.tolist(),
                    plan=plan,dispatch=deepcopy(dispatch),learned_target_actor_executed=False,teacher_controller='privileged_registered_task_relative_geometry'))
                attempts+=1
                # Completion is a literal dispatched endpoint, not evidence of
                # contact/object attainment. Target independent report owns that.
                phase_verified=None
                if frame.get('advance_on_verified_phase') is True:
                    if not physics_provider or not callable(getattr(target,'phase_complete',None)):raise TransferBlocked('INDEPENDENT_TARGET_PHASE_COMPLETION_PROVIDER_REQUIRED','A')
                    phase_verified=target.phase_complete(frame['phase_id']) is True
                reached=not plan['navigation_required'] and plan.get('arm_target_is_final',True) and np.max(np.abs(actual[:6]-plan['arm_command6']))<.05 and attempts>=int(frame.get('minimum_target_steps',1)) and phase_verified is not False
                rows[-1]['independent_phase_completion']=phase_verified
            if not reached:raise TransferBlocked('TARGET_PHASE_OR_TRIAL_BUDGET_EXHAUSTED','A',evidence=dict(phase_id=frame['phase_id'],phase_steps=attempts,phase_budget=phase_budget,independent_phase_confirmed=target.phase_complete(frame['phase_id']) if callable(getattr(target,'phase_complete',None)) else None))
        report=target.report()
    except Exception as error:
        failure=error.document() if isinstance(error,TransferBlocked) else dict(code='TARGET_PROVIDER_FAILURE',error_type=type(error).__name__,detail=str(error))
        try:report=target.report()
        except Exception as report_error:report=dict(source='unavailable',independent_of_action_commands=False,error_type=type(report_error).__name__)
    trace_raw=canonical(rows);_immutable(folder/'target-trace.json',trace_raw)
    independent=physics_provider and report.get('independent_of_action_commands') is True and report.get('source')=='target_finite_physics_evaluator'
    phases={frame['phase_id'] for frame in source['frames']}
    full_success=required.issubset(phases) and independent and report.get('end_to_end_success') is True and all((report.get('per_phase',{}).get(phase) or {}).get('success') is True for phase in required)
    result=dict(schema='explorer_skill_transfer_target_trial_v1',**experiment_identity(TRANSFER_SLUG),source_experiment_slug=SOURCE_SLUG,source_project_id=experiment_id(SOURCE_SLUG),
        source_method=source['source_kind'],source_model_full_skill_claim=source['source_kind']=='QUALIFIED_SOURCE_MODEL',source_manifest_sha256=source_bundle['manifest_sha256'],
        skill_id=source['skill_id'],source_split=deepcopy(source['split']),source_kind='simulation',source_skill_version=source['skill_version'],target_skill_version=target_version,target_skill_spec_sha256=digest(target_skill_spec),seed=seed,
        target_provider=provider,target_embodiment='ROSMASTER_unified_mobile_manipulator',target_action_contract_id=TARGET_CONTRACT,action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),
        simultaneous_9d=True,geometry_lineage=mapper.lineage,target_trace_sha256=sha(trace_raw),frames=len(rows),last_source_frame_index=source_index,failure=failure,independent_evaluation=report,
        initial_state=initial_state,
        full_target_simulation_success=bool(full_success and not failure),eligible_for_target_dataset=bool(full_success and not failure),optimizer_partition_allowed=source['split']['partition']=='train',eligible_as_simulation_expert=bool(full_success and not failure and source['split']['partition']=='train'),
        software_contract_only=not physics_provider,learned_target_actor_executed=False,physical_io=False,physical_success_validated=False,source_RGB_reused_as_target=False,source_files_sha256=source_bundle['source_files_sha256'])
    for path,expected in source_bundle['source_files_sha256'].items():
        try:unchanged=sha(Path(path).read_bytes())==expected
        except OSError:unchanged=False
        if not unchanged:result['failure']=dict(code='SOURCE_CHANGED_DURING_TARGET_TRIAL');result['eligible_as_simulation_expert']=False;result['eligible_for_target_dataset']=False;result['full_target_simulation_success']=False
    _immutable(folder/'result.json',canonical(result));return result
