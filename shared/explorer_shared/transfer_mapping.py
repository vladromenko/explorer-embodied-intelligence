"""ENGINEERING_GLUE: provable04 coordinate arithmetic and scoped mapping audit.

No robot classes, experiment imports, calibration writes or device connection.
Native encoder arithmetic is distinguished from physical URDF registration.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .contracts import canonical,digest
from .kinematics import URDFChain,rigid_transform
from .so101_station import NAMES,UNITS

NATIVE_SERVO_COORDINATES_SHA='6dbfbcaa238b04e2ddb61e08d43f1ebee250e8bfd8ca273d83ff2e1008df6b2f'
NEW_CALIBRATION_README_SHA='c6df213e471e4573ba6384cb957e9eee9644f5d671272f98f67d28866333f747'


def sha(raw):return hashlib.sha256(raw).hexdigest()


class TransferBlocked(ValueError):
    def __init__(self,code,category,detail='',evidence=None):
        self.code,self.category,self.detail,self.evidence=code,category,detail,evidence or {}
        super().__init__(code+(': '+detail if detail else ''))
    def document(self):return dict(code=self.code,category=self.category,detail=self.detail,evidence=self.evidence)


class SourceCoordinates:
    """Exact official0.6.1 RANGE_M100_100→encoder→DEGREES arithmetic.

    Firmware homing is ALREADY applied to Present_Position; adding the saved
    homing offset again would invent a different coordinate system.
    """
    def __init__(self,original_bytes,expected_sha256,*,resolution=4096):
        if not isinstance(original_bytes,bytes) or sha(original_bytes)!=expected_sha256:raise TransferBlocked('SOURCE_CALIBRATION_SHA_MISMATCH','A')
        self.calibration=json.loads(original_bytes);self.sha256=expected_sha256;self.resolution=resolution
        if set(self.calibration)!=set(NAMES) or type(resolution) is not int or resolution<2:raise TransferBlocked('SOURCE_NAMED_CALIBRATION_CONTRACT_INVALID','A')
        for index,name in enumerate(NAMES):
            row=self.calibration[name]
            if row.get('id')!=index+1 or row.get('drive_mode') not in (0,1) or any(type(row.get(key)) is not int for key in ('range_min','range_max','homing_offset')) or row['range_max']<=row['range_min']:raise TransferBlocked('SOURCE_CALIBRATION_RECORD_INVALID','A',name)
    def _native(self,values):
        values=np.asarray(values,float)
        if values.shape!=(6,) or not np.isfinite(values).all() or np.any(values[:5]<-100) or np.any(values[:5]>100) or not 0<=values[5]<=100:raise TransferBlocked('SOURCE_POLICY_NATIVE_CONTRACT_VIOLATION','C','Six named normalized source values required; never silently clip a policy')
        return values
    def encoder(self,native,*,quantized=True):
        native=self._native(native);result=[]
        for index,name in enumerate(NAMES):
            row=self.calibration[name];value=native[index]
            if index<5:value=-value if row['drive_mode'] else value;ratio=(value+100)/200
            else:value=100-value if row['drive_mode'] else value;ratio=value/100
            ticks=ratio*(row['range_max']-row['range_min'])+row['range_min'];result.append(int(ticks) if quantized else ticks)
        return np.asarray(result,float)
    def arm_encoder_delta_radians(self,native):
        ticks=self.encoder(native);mid=np.array([(self.calibration[name]['range_min']+self.calibration[name]['range_max'])/2 for name in NAMES[:5]])
        return (ticks[:5]-mid)*(2*math.pi/(self.resolution-1))
    def nominal_urdf_radians(self,native,chain):
        if tuple(chain.names)!=tuple(NAMES[:5]):raise TransferBlocked('SOURCE_URDF_NAMED_CHAIN_MISMATCH','A')
        result=self.arm_encoder_delta_radians(native)
        try:chain.forward(result)
        except ValueError as error:raise TransferBlocked('SOURCE_ENCODER_COORDINATE_OUTSIDE_CAD_LIMITS','A',str(error)) from error
        return result
    def native_from_nominal_radians(self,positions,gripper):
        positions=np.asarray(positions,float)
        if positions.shape!=(5,) or not np.isfinite(positions).all():raise TransferBlocked('SOURCE_RADIAN_TARGET_INVALID','A')
        result=[]
        for index,name in enumerate(NAMES[:5]):
            row=self.calibration[name];scale=(row['range_max']-row['range_min'])/200*(2*math.pi/(self.resolution-1));sign=-1 if row['drive_mode'] else 1
            result.append(float(positions[index]/(sign*scale)))
        return self._native(result+[float(gripper)])
    def profile(self,chain,zero_convention_bytes):
        if sha(zero_convention_bytes)!=NEW_CALIBRATION_README_SHA:raise TransferBlocked('OFFICIAL_NEW_URDF_ZERO_CONVENTION_UNBOUND','A')
        if tuple(chain.names)!=tuple(NAMES[:5]):raise TransferBlocked('SOURCE_URDF_NAMED_CHAIN_MISMATCH','A')
        joints={}
        for name in NAMES[:5]:
            row=self.calibration[name];joints[name]=dict(scale_rad_per_native_unit=(-1 if row['drive_mode'] else 1)*(row['range_max']-row['range_min'])/200*(2*math.pi/(self.resolution-1)),offset_rad=0.,
                encoder_range=[row['range_min'],row['range_max']],homing_offset_reapplied=False,max_quantization_error_rad=2*math.pi/(self.resolution-1))
        body=dict(schema='explorer_source_coordinate_profile04_v1',basis='official_encoder_delta_and_documented_new_URDF_midrange_convention',joint_names=list(NAMES),joint_units=list(UNITS),
            source_calibration_sha256=self.sha256,source_urdf_sha256=chain.source_sha256,zero_convention_document_sha256=sha(zero_convention_bytes),encoder_resolution=self.resolution,joints=joints,
            exact_encoder_arithmetic=True,physical_URDF_registration_measured=False,physical_execution_authorized=False,
            allowed_scopes=['preview','simulation','shadow'],physical_reference_note='Encoder arithmetic is proven; actual unit axis/zero alignment remains separately observed geometry evidence')
        return dict(body,profile_id='mapping04-'+digest(body)[:24])


class TargetCoordinates:
    def __init__(self,original_source_bytes):
        if sha(original_source_bytes)!=NATIVE_SERVO_COORDINATES_SHA:raise TransferBlocked('TARGET_FACTORY_SERVO_COORDINATES_SOURCE_CHANGED','A')
        self.source_sha256=sha(original_source_bytes);self.signs=np.array([-1.,1.,1.,1.,1.])
    def radians(self,servo6):
        values=np.asarray(servo6,float)
        if values.shape!=(6,) or not np.isfinite(values).all():raise TransferBlocked('TARGET_NAMED_SERVO6_REQUIRED','A')
        return np.radians(values[:5]-90)*self.signs
    def servo(self,radians,gripper_command):
        values=np.asarray(radians,float)
        if values.shape!=(5,) or not np.isfinite(values).all() or not math.isfinite(gripper_command):raise TransferBlocked('TARGET_NAMED_ARM5_AND_GRIPPER_REQUIRED','A')
        return np.r_[np.degrees(values)*self.signs+90,float(gripper_command)]


def audit_known_mapping(*,source_chain,target_chain,source_coordinates,source_zero_convention_bytes,target_coordinates,gripper_reference_bytes,handeye_reference_bytes,source_qualification=None):
    source_profile=source_coordinates.profile(source_chain,source_zero_convention_bytes)
    gripper=json.loads(gripper_reference_bytes);handeye=json.loads(handeye_reference_bytes);matrix=rigid_transform(handeye['camera_to_mount_reference'])
    if gripper.get('servo_id')!=6 or any(not isinstance(gripper.get(key),(float,int)) or not math.isfinite(gripper[key]) for key in ('observed_open_command_deg','observed_empty_visually_closed_command_deg')):raise TransferBlocked('KNOWN_TARGET_GRIPPER_REFERENCE_INVALID','A')
    A=[dict(id='source_URDF_and_named_native_contract',status='RESOLVED',source_urdf_sha256=source_chain.source_sha256,names=list(NAMES),units=list(UNITS)),
       dict(id='source_calibration_SHA_and_encoder_delta',status='RESOLVED',calibration_sha256=source_coordinates.sha256,profile_id=source_profile['profile_id'],homing_offset_reapplied=False),
       dict(id='source_new_calibration_midrange_convention',status='RESOLVED_FOR_PREVIEW_SIM_SHADOW',document_sha256=sha(source_zero_convention_bytes),physical_registration_not_inferred=True),
       dict(id='target_factory_native_servo_coordinate_adapter',status='RESOLVED',source_sha256=target_coordinates.source_sha256,target_urdf_sha256=target_chain.source_sha256),
       dict(id='target_observed_gripper_open_close_direction',status='RESOLVED',source_sha256=sha(gripper_reference_bytes),open_command_deg=gripper.get('observed_open_command_deg'),empty_closed_command_deg=gripper.get('observed_empty_visually_closed_command_deg'),metric_aperture_not_inferred=True),
       dict(id='existing_stationary_camera_to_arm4_reference',status='RESOLVED_WITH_ORIGINAL_SCOPE',source_sha256=sha(handeye_reference_bytes),camera_to_mount=matrix.tolist(),validated_joints=handeye.get('validated_joints'),scope=handeye.get('scope'),translation_error_max_m=handeye.get('translation_error_max_m'),moving_frame_use_qualified=False)]
    B=[dict(id='source_URDF_unit_axis_alignment',required_for='physical_registration',reason='Encoder delta is computed, but unit-specific CAD axis/zero agreement is not an observed external pose measurement',wizard_step='joint_reference_views'),
       dict(id='source_and_target_metric_gripper_aperture',required_for='physical_contact_transfer',reason='Known empty-open/closed commands do not establish metric aperture or contact force',wizard_step='gripper_scale_views'),
       dict(id='current_source_target_object_goal_registration',required_for='physical_scene_registration',reason='Source/target robot-camera/object frame identities need current measured artifacts; old stationary arm4 scope is retained',wizard_step='object_goal_views')]
    qualification=source_qualification or {};qualified=qualification.get('independent_object_skill_qualified') is True and qualification.get('source_domain') in ('simulation','physical') and qualification.get('source_policy_closed_loop_evaluated') is True
    C=[] if qualified else [dict(id='qualified06_object_skill_source',reason='A stationary hold / four recorded predictions is not an independent source candy-to-box skill',development_allowed_with='explicit_SCRIPTED_SIM_fixture_not_a_learned_source_skill')]
    return dict(schema='explorer_transfer04_mapping_audit_v1',A=A,B=B,C=C,all_computable_mapping_A_resolved=True,source_profile=source_profile,source_qualification_evidence=deepcopy(qualification),
        historical_blocked_mapping_causes=dict(source_urdf_sha256_mismatch='A_empty_profile_autobound_actual_SHA',source_named_native_contract_mismatch='A_empty_profile_autobound_exact_names_units',radian_mapping_basis_missing='A_encoder_arithmetic_and_nominal_convention_computed',
            physical_source_requires_measured_radian_reference_pairs='B_only_physical_unit_registration_not_a_software_FK_blocker',source_calibration_sha256_not_bound='A_original_bytes_SHA_verified',five_joint_sign_scale_zero='A_numeric_encoder_scale_computed; B_actual_unit_pose_registration_separate'),
        physical_execution_authorized=False,controller_settings_modified=False,source_data_modified=False)


def write_audit(directory,audit):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);body=canonical(audit);identifier=digest(audit);path=directory/(identifier+'.json')
    if path.exists() and path.read_bytes()!=body:raise ValueError('Immutable mapping audit conflict')
    if not path.exists():
        import os
        with path.open('xb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
    return dict(path=str(path),sha256=sha(body),audit_id=identifier)
