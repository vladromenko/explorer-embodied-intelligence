"""ENGINEERING_GLUE: source-only cross-map, reusing calibrated SO101 cache.

Does not recalibrate either SO101, alter factory coordinates, or own a bus.
Unknown normalized->physical axes/zero/gripper mapping stays UNREGISTERED.
"""
import hashlib
import json
from pathlib import Path

import numpy as np

from .arm_only_retarget import affine_points,SOURCE_NAMES
from .kinematics import URDFChain
from .native_stream03 import AUDITED_FACTORY_SHA256,RelativeLeaderGeometry


def original_servo_coordinates(content):
    expected=AUDITED_FACTORY_SHA256['src/servo_coordinates.py']
    if not isinstance(content,bytes) or hashlib.sha256(content).hexdigest()!=expected:
        raise ValueError('Exact original factory servo-coordinate source required')
    # This exact audited535-byte module imports NumPy and defines coordinate
    # transforms only. No ManualArm/ROS/publisher module is imported or created.
    namespace={'__name__':'explorer_audited_factory_servo_coordinates'}
    exec(compile(content,'audited-original-servo-coordinates','exec'),namespace)
    return namespace['to_radians'],namespace['to_servo']


class UnregisteredLeaderGeometry:
    registration=None
    def __init__(self,reason):
        self.reason=reason
    def status(self):
        return {'state':'UNREGISTERED','missing_cross_map':[self.reason],
                'leader_device_recalibration_requested':False,'physical_acceptance':False}


def registered_native_leader_geometry(config_dir,*,root=None):
    root=Path(root or Path(__file__).resolve().parents[2]);config_dir=Path(config_dir)
    path=config_dir/'native-leader-registration.json'
    coordinates=config_dir/'factory-servo-coordinates.py'
    if not path.is_file():
        return UnregisteredLeaderGeometry('NORMALIZED_TO_PHYSICAL_JOINT_AXES_ZERO_SCALE_AND_GRIPPER_REFERENCE')
    try:
        raw=path.read_bytes()
        if len(raw)>128*1024:
            raise ValueError('Cross-map exceeds metadata bound')
        profile=json.loads(raw)
        if profile.get('schema')!='explorer_native_leader_registration_v1' or profile.get('provenance')!='measured':
            raise ValueError('Measured cross-map declaration required; existing SO101 calibration is reused')
        source=URDFChain.from_urdf(root/'04-cross-embodiment-transfer/configs/so101/so101_leader_new_calib.urdf',
                                  'base_link','gripper_frame_link')
        target=URDFChain.from_urdf(root/'01-autonomous-learning/src/legacy/config/explorer.urdf','base_footprint','Gripping')
        if profile.get('source_urdf_sha256')!=source.source_sha256 or profile.get('target_urdf_sha256')!=target.source_sha256:
            raise ValueError('Cross-map URDF source hashes changed')
        if not isinstance(profile.get('source_calibration_sha256'),str) or len(profile['source_calibration_sha256'])!=64:
            raise ValueError('Identify the already calibrated leader profile')
        evidence=profile.get('evidence_source_files_sha256',{})
        if not evidence:
            raise ValueError('Original paired-angle/aperture evidence missing')
        for name,digest in evidence.items():
            artifact=(config_dir/name).resolve()
            if (Path(name).is_absolute() or not artifact.is_relative_to(config_dir.resolve()) or
                    not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest()!=digest):
                raise ValueError('Cross-map original evidence hash/path mismatch')
        points=profile.get('source_joint_points',{})
        if set(points)!=set(SOURCE_NAMES[:5]):
            raise ValueError('Five named physical-radian source joint correspondences required')
        fits=[affine_points(points[name])[0] for name in SOURCE_NAMES[:5]]
        gripper=np.asarray(profile.get('leader_normalized_to_target_gripper_servo_deg'),float)
        if (gripper.ndim!=2 or gripper.shape[1]!=2 or len(gripper)<2 or not np.isfinite(gripper).all() or
                np.any(np.diff(gripper[:,0])<=0)):
            raise ValueError('Separate measured gripper endpoint curve required')
        to_radians,to_servo=original_servo_coordinates(coordinates.read_bytes())
        def source_mapper(values):
            q=np.asarray(values,float)
            if q.shape!=(5,):
                raise ValueError('Exactly five named leader joint coordinates required')
            for value,name in zip(q,SOURCE_NAMES[:5]):
                original=np.asarray(points[name],float)
                if not original[:,0].min()<=value<=original[:,0].max():
                    raise ValueError('Leader outside registered physical correspondence interval')
            return np.asarray([slope*value+offset for value,(slope,offset) in zip(q,fits)])
        def gripper_mapper(value):
            if not gripper[0,0]<=value<=gripper[-1,0]:
                raise ValueError('Leader gripper outside measured correspondence interval')
            return float(np.interp(value,gripper[:,0],gripper[:,1]))
        registration={**profile,'sha256':hashlib.sha256(raw).hexdigest()}
        return RelativeLeaderGeometry(source,target,source_mapper,gripper_mapper,to_radians,to_servo,
                                      registration=registration)
    except (OSError,ValueError,TypeError,KeyError) as error:
        return UnregisteredLeaderGeometry('INVALID_CROSS_MAP:'+type(error).__name__)
