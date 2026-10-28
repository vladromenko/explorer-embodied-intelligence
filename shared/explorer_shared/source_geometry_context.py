"""Existing SO101 calibration/CAD context shared by leader03 and follower06.

Pure file/coordinate work. No bus/camera owner, physical pose measurement,
controller setting or execution permission is created.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .contracts import canonical
from .kinematics import URDFChain
from .transfer_mapping import SourceCoordinates


def sha(raw):return hashlib.sha256(raw).hexdigest()


class SO101GeometryContext:
    def __init__(self,root,calibration_raw,*,expected_sha256,source_role):
        if source_role not in ('leader','follower'):
            raise ValueError('Explicit existing leader/follower geometry required')
        if not isinstance(calibration_raw,bytes) or len(calibration_raw)>128*1024:
            raise ValueError('Bounded original calibration bytes required')
        self.root=Path(root);self.raw=calibration_raw;self.source_role=source_role
        self.coordinates=SourceCoordinates(self.raw,expected_sha256)
        name='so101_leader_new_calib.urdf' if source_role=='leader' else 'so101_new_calib.urdf'
        self.source=URDFChain.from_urdf(self.root/'shared/robotics/so101'/name,'base_link','gripper_frame_link')
        self.target=URDFChain.from_urdf(self.root/'01-autonomous-learning/src/legacy/config/explorer.urdf','base_footprint','Gripping')
        self.profile=self.coordinates.profile(self.source,(self.root/'shared/robotics/so101/README.md').read_bytes())
        self.native_source=(self.root/'01-autonomous-learning/src/legacy/src/servo_coordinates.py').read_bytes()
        from .native_leader_geometry import original_servo_coordinates
        self.to_radians,self.to_servo=original_servo_coordinates(self.native_source)
        self.gripper_raw=(self.root/'01-autonomous-learning/src/legacy/config/gripper-reference.json').read_bytes()
        baseline=json.loads((self.root/'_registry/controller-config-baseline.json').read_bytes())
        if sha(self.gripper_raw)!=baseline['gripper-reference.json']:
            raise ValueError('Original target gripper command reference changed')
        self.gripper=json.loads(self.gripper_raw)

    def status(self):
        return {'all_computable_A_resolved':True,'source_coordinate_profile':deepcopy(self.profile),
                'source_coordinate_profile_sha256':sha(canonical(self.profile)),
                'target_original_coordinates_sha256':sha(self.native_source),
                'target_gripper_reference_sha256':sha(self.gripper_raw),
                'known_target_gripper_commands':{'open':self.gripper['observed_open_command_deg'],
                    'empty_visually_closed':self.gripper['observed_empty_visually_closed_command_deg']},
                'original_SO101_calibration_retained':True,'device_recalibration_requested':False,
                'metric_aperture_known':False,'force_or_contact_inferred':False,
                'physical_unit_axis_registration_inferred':False}

    def preview(self,native6):
        q=self.coordinates.nominal_urdf_radians(native6,self.source)
        return {'source_q_nominal_rad':q.tolist(),'source_base_from_tool':self.source.forward(q).tolist(),
                'basis':self.profile['basis'],'scope':'preview_shadow_not_measured_unit_pose','physical_commands':0}

