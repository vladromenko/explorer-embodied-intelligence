"""ENGINEERING_GLUE: measured/synthetic04 geometry fitting, never motor settings.

Inputs come from source-bound camera/clock providers. Browser pixel clicks do
not themselves create metric coordinates, force or measured joint feedback.
"""
from __future__ import annotations
from copy import deepcopy
import math

import numpy as np

from .contracts import digest
from .kinematics import ObjectRegistration,rigid_transform
from .transfer_mapping04 import TransferBlocked
from .so101_station import NAMES


def fit_joint_reference(coordinates,chain,views,*,provenance):
    if provenance not in ('synthetic','measured') or len(views)<6:raise TransferBlocked('SIX_INDEPENDENT_JOINT_POSE_VIEWS_REQUIRED','B')
    native=[];observed=[];uncertainties=[];sources=[]
    for view in views:
        if not view.get('frame_sha256') or not view.get('calibration_id') or view.get('pose_basis') not in ('calibrated_fiducial','synthetic_camera_geometry'):raise TransferBlocked('EXTERNAL_CALIBRATED_POSE_EVIDENCE_REQUIRED','B')
        measurement=rigid_transform(view['measured_base_from_tool']);values=coordinates._native(view['native6'])
        sigma=view.get('uncertainty_rad')
        if not isinstance(sigma,(int,float)) or not 0<sigma<=.1:raise TransferBlocked('MEASURED_POSE_UNCERTAINTY_REQUIRED','B')
        nominal=coordinates.arm_encoder_delta_radians(values)
        solved=chain.inverse(measurement,nominal,tolerance_m=.0005,orientation_weight=.1,orientation_tolerance_rad=.005,max_iterations=120,restarts=0)
        if not solved.reached:raise TransferBlocked('JOINT_REFERENCE_IK_QUALITY_RETRY','B',str(solved.as_dict()))
        native.append(values[:5]);observed.append(solved.positions);uncertainties.append(float(sigma));sources.append(dict(frame_sha256=view['frame_sha256'],calibration_id=view['calibration_id']))
    native=np.asarray(native);observed=np.asarray(observed);joints={};fit_count=len(native)-2
    if len({row['frame_sha256'] for row in sources})!=len(sources):raise TransferBlocked('DUPLICATE_REFERENCE_VIEW','B')
    for index,name in enumerate(NAMES[:5]):
        if np.ptp(native[:fit_count,index])<5:raise TransferBlocked('JOINT_REFERENCE_POSE_SPAN_RETRY','B',name)
        fit=np.polyfit(native[:fit_count,index],observed[:fit_count,index],1)
        reference=coordinates.calibration[name];known=(reference['range_max']-reference['range_min'])/200*2*math.pi/(coordinates.resolution-1)
        if abs(abs(fit[0])-known)>known*.05:raise TransferBlocked('JOINT_REFERENCE_SCALE_OR_FRAME_INCONSISTENT','B',name)
        scale=known if fit[0]>0 else -known;offset=float(np.mean(observed[:fit_count,index]-scale*native[:fit_count,index]));residual=np.abs(scale*native[:,index]+offset-observed[:,index])
        uncertainty=max(uncertainties)+2*math.pi/(coordinates.resolution-1)
        if residual.max()>uncertainty:raise TransferBlocked('JOINT_REFERENCE_RESIDUAL_RETRY','B',name)
        joints[name]=dict(scale_rad_per_native_unit=scale,offset_rad=offset,reference_pairs=np.c_[native[:,index],observed[:,index]].tolist(),reference_uncertainty_rad=uncertainty,maximum_residual_rad=float(residual.max()),heldout_maximum_residual_rad=float(residual[fit_count:].max()))
    artifact=dict(schema='explorer_skill_transfer_joint_registration_v1',basis='measured_reference_pairs' if provenance=='measured' else 'synthetic_reference_pairs',
        provenance=provenance,source_calibration_sha256=coordinates.sha256,source_urdf_sha256=chain.source_sha256,joint_names=list(NAMES),joints=joints,
        source_views=sources,fit_view_count=fit_count,heldout_view_count=2,controller_calibration_modified=False,physical_execution_authorized=False)
    return dict(artifact,calibration_id='transfer-joints-'+digest(artifact)[:24])


def fit_aperture_reference(views,*,role,provenance):
    selected=[view for view in views if view.get('role')==role]
    if role not in ('source','target') or provenance not in ('synthetic','measured') or len(selected)<3:raise TransferBlocked('THREE_METRIC_APERTURE_VIEWS_PER_ROBOT_REQUIRED','B',role)
    samples=[];sources=[]
    for view in selected:
        command,aperture,sigma=view.get('native_gripper'),view.get('aperture_m'),view.get('uncertainty_m')
        if any(not isinstance(value,(int,float)) or not math.isfinite(value) for value in (command,aperture,sigma)) or not 0<=aperture<=.2 or not 0<sigma<=.01 or not view.get('frame_sha256') or not view.get('calibration_id'):raise TransferBlocked('METRIC_APERTURE_NOT_SOURCE_BOUND','B')
        samples.append((float(command),float(aperture),float(sigma)));sources.append(dict(frame_sha256=view['frame_sha256'],calibration_id=view['calibration_id']))
    samples.sort();array=np.asarray(samples);direction=1 if role=='source' else -1
    if len(set(array[:,0]))!=len(array) or np.ptp(array[:,0])<(30 if role=='source' else 50) or np.ptp(array[:,1])<.01 or np.any(np.diff(array[:,1])*direction<=0):raise TransferBlocked('APERTURE_RANGE_OR_MONOTONICITY_RETRY','B',role)
    if len({row['frame_sha256'] for row in sources})!=len(sources):raise TransferBlocked('DUPLICATE_APERTURE_VIEW','B')
    artifact=dict(schema='explorer_skill_transfer_aperture_registration_v1',role=role,provenance=provenance,native_commands=array[:,0].tolist(),apertures_m=array[:,1].tolist(),uncertainty_m=float(array[:,2].max()),
        source_views=sources,force_calibrated=False,empty_closed_is_not_object_squeeze_target=True,physical_execution_authorized=False)
    return dict(artifact,calibration_id='transfer-aperture-'+digest(artifact)[:24])


def calibrated_aperture(profile,native_command):
    values=np.asarray(profile['native_commands']);gaps=np.asarray(profile['apertures_m'])
    if not values[0]<=native_command<=values[-1]:raise TransferBlocked('APERTURE_OUTSIDE_CALIBRATED_NATIVE_RANGE','B')
    return float(np.interp(native_command,values,gaps))


def command_for_aperture(profile,aperture_m):
    gaps=np.asarray(profile['apertures_m']);values=np.asarray(profile['native_commands']);order=np.argsort(gaps)
    if not gaps[order[0]]<=aperture_m<=gaps[order[-1]]:raise TransferBlocked('APERTURE_OUTSIDE_MEASURED_METRIC_RANGE','B')
    return float(np.interp(aperture_m,gaps[order],values[order]))


def register_task_frames(views,*,provenance):
    roles=('source_object','source_goal','target_object','target_goal');selected={}
    for role in roles:
        match=[view for view in views if view.get('role')==role]
        if len(match)!=1:raise TransferBlocked('EXACT_SOURCE_TARGET_OBJECT_AND_GOAL_FRAMES_REQUIRED','B',role)
        view=match[0]
        if not view.get('frame_sha256') or not view.get('calibration_id') or not isinstance(view.get('uncertainty_m'),(int,float)) or not 0<view['uncertainty_m']<=.01:raise TransferBlocked('CALIBRATED_METRIC_TASK_FRAME_REQUIRED','B',role)
        selected[role]=dict(base_from_frame=rigid_transform(view['base_from_frame']).tolist(),frame_sha256=view['frame_sha256'],calibration_id=view['calibration_id'],uncertainty_m=view['uncertainty_m'])
    if provenance not in ('synthetic','measured'):raise TransferBlocked('EXPLICIT_REGISTRATION_PROVENANCE_REQUIRED','B')
    artifact=dict(schema='explorer_skill_transfer_task_registration_v1',provenance=provenance,frames=selected,units='m',physical_execution_authorized=False)
    return dict(artifact,calibration_id='transfer-frames-'+digest(artifact)[:24])


def task_registration(profile,reference_role):
    if reference_role not in ('object','goal'):raise TransferBlocked('TASK_EFFECT_REFERENCE_ROLE_REQUIRED','A')
    frames=profile['frames'];source=frames['source_'+reference_role];target=frames['target_'+reference_role]
    return ObjectRegistration(source['base_from_frame'],target['base_from_frame'],'measured' if profile['provenance']=='measured' else 'virtual_declared',
        (source['calibration_id'],target['calibration_id']))
