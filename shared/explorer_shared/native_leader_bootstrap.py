"""ENGINEERING_GLUE: Existing calibration/URDF bootstrap, without device IO or measured-pose claims."""
import json

import numpy as np

from .kinematics import transform
from .native_stream03 import RelativeLeaderGeometry
from .retarget_recording import canonical
from .native_leader_registration import sha

PROVENANCE = 'derived_from_existing_controller_and_lerobot_calibration'
FRAME_YAW = {'aligned_urdf_base_axes': 0., 'source_base_yaw_plus90': np.pi / 2,
             'source_base_yaw_minus90': -np.pi / 2, 'source_base_yaw_180': np.pi}


def expected_leader_sha(root):
    manifest = json.loads((root / '_registry/so101-calibration-manifest.json').read_bytes())
    return manifest['teleoperators/so_leader/so101_leader.json']['sha256']


def bootstrap_geometry(context, *, frame_convention='aligned_urdf_base_axes'):
    if frame_convention not in FRAME_YAW:
        raise ValueError('Choose a named source/target base orientation convention')
    # This registry binds the original calibrated device. Never replace it with
    # client-declared normalized coordinates or silently accept a different file.
    expected = expected_leader_sha(context.root)
    if context.coordinates.sha256 != expected:
        raise ValueError('Bootstrap leader calibration differs from original registry SHA')
    rotation = transform(rpy=(0., 0., FRAME_YAW[frame_convention]))[:3, :3]
    profile = {
        'schema': 'explorer_native_leader_bootstrap_v1', 'provenance': PROVENANCE,
        'source_calibration_sha256': context.coordinates.sha256,
        'source_urdf_sha256': context.source.source_sha256,
        'target_urdf_sha256': context.target.source_sha256,
        'source_coordinate_profile_sha256': sha(canonical(context.profile)),
        'target_servo_coordinates_sha256': sha(context.native_source),
        'target_gripper_reference_sha256': sha(context.gripper_raw),
        'frame_convention': frame_convention,
        'target_base_from_source_rotation': rotation.tolist(),
        'frame_basis': 'declared_URDF_base_axis_convention_installation_unverified',
        'translation_basis': 'each_explicit_clutch_from_current_native_command_estimate',
        'target_state_source': 'command_estimate', 'target_measured': False,
        'source_CAD_bounds': 'diagnostic_for_observed_encoder_FK_not_a_motor_limit_or_clamp',
        'source_joint_coordinate_clipping': False,
        'leader_normalized_to_target_gripper_servo_deg': [[0., 170.], [100., 30.]],
        'gripper_basis': 'provisional_official_normalized_closed_open_and_original_target_command_reference',
        'physical_mount_verified': False, 'gripper_direction_physically_verified': False,
        'physical_acceptance': False, 'metric_gripper_aperture_measured': False,
        'controller_configuration_writes': 0, 'physical_commands': 0,
        'acceptance_remaining': ['small_xyz_directions', 'wrist_orientation', 'gripper_direction'],
    }
    profile['sha256'] = sha(canonical(profile))

    def gripper(value):
        if not np.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('Leader gripper must retain its original normalized0..100 contract')
        return 170. - 1.4 * value

    # A genuine calibrated encoder observation can exceed a nominal CAD bound.
    # FK is still a finite rigid transform there. Do not clip/rescale the source
    # observation or change any hardware/target IK limit to hide that difference.
    geometry = RelativeLeaderGeometry(context.source, context.target,
        context.coordinates.arm_encoder_delta_radians,
        gripper, context.to_radians, context.to_servo, registration=profile,
        source_mapper_uses_full_leader=True, target_base_from_source_rotation=rotation,
        source_forward=lambda q: context.source.forward(q, enforce_limits=False))
    geometry.context = context
    return geometry


def source_diagnostics(geometry, native6):
    q = geometry.source_mapper(native6)
    excess = np.maximum(geometry.source.lower - q, 0.) + np.maximum(q - geometry.source.upper, 0.)
    return {'nominal_source_radians': q.tolist(), 'CAD_limit_excess_rad': excess.tolist(),
            'source_base_from_tool': geometry.source_forward(q).tolist(),
            'basis': 'original_calibrated_encoder_delta_and_URDF_nominal_geometry',
            'actual_unit_pose_measured': False, 'source_clipped': False, 'physical_commands': 0}


def configured_bootstrap(context, config_dir):
    from pathlib import Path
    path = Path(config_dir) / 'native-leader-bootstrap.json'
    convention = 'aligned_urdf_base_axes'
    if path.exists():
        if path.stat().st_size > 4096:
            raise ValueError('Bootstrap orientation metadata exceeds its bound')
        body = json.loads(path.read_bytes())
        if (not isinstance(body, dict) or set(body) != {'frame_convention'}):
            raise ValueError('Only a named bootstrap frame convention is configurable')
        convention = body['frame_convention']
    return bootstrap_geometry(context, frame_convention=convention)
