"""method_id: LELAB_CHECKPOINT_IMPORT
KNOWN_BASELINE / ENGINEERING_GLUE: real geometric transfer and protocol tests.

These jobs never communicate with motors. A virtual reference trajectory is not
a learned SO-101 policy and protocol-test oracles are not physical evidence.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from explorer_shared.handover import HandoverFSM, HoldEvidence
from explorer_shared.kinematics import ObjectRegistration, URDFChain


SOURCE_REVISION = 'a758567c3978dfeefe282ede0500085a48fe8f78'
LELAB_REVISION = '9b5afaee33baa1d773e499937564f3831d25de22'
SO101_NAMES = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')


def file_sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def inspect_lerobot_source(dataset_path, checkpoint_path, semantics):
    """Inspect real artifacts without modifying originals or importing robot IO.

    Processor files must be interpreted by their matching official LeRobot
    version before actions are extracted. Merely matching six dimensions is not
    permission to replay a checkpoint action head on ROSMASTER.
    """
    dataset, checkpoint = Path(dataset_path), Path(checkpoint_path)
    paths = {'dataset_info': dataset / 'meta/info.json', 'checkpoint_config': checkpoint / 'config.json',
             'weights': checkpoint / 'model.safetensors',
             'preprocessor': checkpoint / 'policy_preprocessor.json',
             'postprocessor': checkpoint / 'policy_postprocessor.json'}
    problems, files = [], {}
    for label, path in paths.items():
        if path.is_file():
            files[label] = dict(path=str(path), sha256=file_sha256(path))
        else:
            problems.append('missing_' + label)
    info = json.loads(paths['dataset_info'].read_text()) if paths['dataset_info'].is_file() else {}
    config = json.loads(paths['checkpoint_config'].read_text()) if paths['checkpoint_config'].is_file() else {}
    action = info.get('features', {}).get('action', {})
    names = tuple(str(name).removesuffix('.pos') for name in action.get('names', []))
    if action.get('shape') != [6] or names != SO101_NAMES:
        problems.append('action_shape_or_joint_names_not_verified')
    if info.get('robot_type') not in ('so101_follower', 'so_follower'):
        problems.append('source_robot_type_not_so101_follower')
    if not str(info.get('codebase_version', '')).startswith(('v2.', 'v3.')):
        problems.append('dataset_codebase_version_not_supported')
    if not isinstance(info.get('fps'), (int, float)) or info.get('fps', 0) <= 0:
        problems.append('dataset_fps_missing')
    if config.get('type') not in ('act', 'diffusion'):
        problems.append('source_policy_type_not_inspected')
    if config.get('output_features', {}).get('action', {}).get('shape') != [6]:
        problems.append('checkpoint_action_output_shape_not_so101_six_dimensions')
    if semantics.get('action_representation') != 'joint_position':
        problems.append('action_representation_must_be_explicit_joint_position')
    if semantics.get('joint_order') != list(SO101_NAMES):
        problems.append('processor_joint_order_not_declared')
    units = semantics.get('joint_units', {})
    if any(units.get(name) not in ('degrees', 'radians') for name in SO101_NAMES[:5]):
        problems.append('arm_units_require_verified_calibration_not_assumed_percent_conversion')
    if units.get('gripper') != 'range_0_100' or semantics.get('gripper_100_is_open') is not True:
        problems.append('gripper_processor_semantics_not_verified')
    if not semantics.get('calibration_id') or not semantics.get('processor_version'):
        problems.append('calibration_and_processor_version_required')
    if not isinstance(semantics.get('normalization'), dict):
        problems.append('processor_normalization_semantics_not_declared')
    processors = {}
    for label in ('preprocessor', 'postprocessor'):
        if paths[label].is_file():
            body = json.loads(paths[label].read_text())
            processors[label] = body
            if not isinstance(body, dict) or not isinstance(body.get('steps'), list):
                problems.append(label + '_steps_schema_not_verified')
    return dict(inspection_passed=not problems, metadata_compatible=not problems, extraction_ready=False,
        processor_runtime_probe_required=True,
        problems=problems, files=files, robot_type=info.get('robot_type'), fps=info.get('fps'),
        dataset_version=info.get('codebase_version'), policy_type=config.get('type'), processors=processors,
        declared_semantics=semantics, original_artifacts_modified=False,
        source_policy_executed=False, direct_action_head_transfer_allowed=False,
        next_step='Use matching official LeRobot processors for policy rollout, then source FK and object-relative target IK.',
        lelab_inspected_revision=LELAB_REVISION, lelab_dependency='lerobot v0.6.0; Python >=3.12')


def _protocol_evidence(statement, stamp, actor='test_oracle'):
    return HoldEvidence('fixture-' + statement + '-' + str(stamp), 'virtual-object', actor,
        'virtual_test_oracle', statement, True, stamp, 'test-source-clock', stamp, 'test-coordinator')


def run_handover_protocol_tests():
    """Deterministic executable protocol cases, not a grasp/physics simulation."""
    fsm = HandoverFSM('virtual-object', mode='virtual', clock_domain='test-coordinator')
    cases = []
    t = 1_000_000_000
    sequence = [
        ('giver_holding', {'evidence': _protocol_evidence('giver_holds_object', t)}),
        ('offer_ready', {'zone_registered': True, 'giver_stationary': True}),
        ('receiver_ready', {'receiver_ready': True}),
        ('receiver_at_object', {'both_stationary': True}),
        ('receiver_closed', {}),
    ]
    for index, (event, facts) in enumerate(sequence):
        fsm.apply(event, event_id='prepare-' + str(index), now_ns=t + index, **facts)
    rejected = False
    try:
        fsm.apply('release_requested', event_id='premature-release', now_ns=t + 6, both_stationary=True)
    except ValueError:
        rejected = True
    cases.append(dict(name='closed_gripper_alone_cannot_authorize_release', passed=rejected and fsm.state == 'RECEIVER_GRASP_CHECK'))
    fsm.apply('receiver_hold_confirmed', event_id='hold', now_ns=t + 7,
        evidence=_protocol_evidence('receiver_holds_object', t + 7))
    fsm.apply('release_requested', event_id='release', now_ns=t + 8, both_stationary=True)
    fsm.apply('giver_released', event_id='released', now_ns=t + 9)
    fsm.apply('transfer_confirmed', event_id='transferred', now_ns=t + 10,
        evidence=_protocol_evidence('object_transferred_to_receiver', t + 10))
    fsm.apply('receiver_finished', event_id='finished', now_ns=t + 11,
        evidence=_protocol_evidence('receiver_finished_task', t + 11))
    cases.append(dict(name='independent_virtual_oracle_full_protocol', passed=fsm.completed))
    fsm.apply('stop', event_id='stop', now_ns=t + 12)
    rejected = False
    try:
        fsm.apply('release_requested', event_id='no-resume', now_ns=t + 13, both_stationary=True)
    except ValueError:
        rejected = True
    cases.append(dict(name='stop_discards_release_authorization', passed=rejected and not fsm.release_permitted(t + 13)))
    physical = HandoverFSM('virtual-object', mode='physical', clock_domain='test-coordinator')
    rejected = False
    try:
        physical.apply('giver_holding', event_id='no-fixture-physical', now_ns=t,
            evidence=_protocol_evidence('giver_holds_object', t))
    except ValueError:
        rejected = True
    cases.append(dict(name='virtual_oracle_rejected_for_physical_protocol', passed=rejected))
    return dict(scope='deterministic_protocol_test', method_ids=['HANDOVER_VERIFIED_FSM'], cases=cases, passed=all(case['passed'] for case in cases),
                trace=fsm.trace, physical_execution=False, physical_success_not_tested=True)


def _plot(path, desired, achieved, errors):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1100, 650), '#0b1723')
    draw = ImageDraw.Draw(image)
    draw.text((42, 25), 'SO-101 -> ROSMASTER: actual kinematic trajectory (virtual frames)', fill='#e6edf3')
    draw.text((42, 50), 'Metres; no hardware commands, collision/contact or physical transfer acceptance', fill='#9cb2c2')
    points = np.vstack((desired, achieved))
    x0, x1 = float(points[:, 0].min()), float(points[:, 0].max())
    z0, z1 = float(points[:, 2].min()), float(points[:, 2].max())
    sx, sz = max(x1 - x0, 0.02), max(z1 - z0, 0.02)
    def point(p):
        return (int(70 + 880 * (p[0] - x0 + 0.1 * sx) / (1.2 * sx)), int(540 - 380 * (p[2] - z0 + 0.1 * sz) / (1.2 * sz)))
    draw.line((70, 120, 70, 540, 990, 540), fill='#466073', width=2)
    draw.text((80, 105), 'Z [m]', fill='#9cb2c2'); draw.text((1010, 535), 'X [m]', fill='#9cb2c2')
    for trajectory, color in ((desired, '#f2ad75'), (achieved, '#77c9a7')):
        projected = [point(p) for p in trajectory]
        if len(projected) > 1:
            draw.line(projected, fill=color, width=3)
    draw.text((75, 580), 'Orange: object-relative target   Green: target FK after bounded IK', fill='#c6d5de')
    draw.text((75, 605), f'Position error median={np.median(errors):.6f} m, max={np.max(errors):.6f} m', fill='#c6d5de')
    image.save(path, quality=90)


def run_virtual_experiment(store, job, config):
    root = Path(config.get('root', Path(__file__).resolve().parents[3]))
    folder = store.root / 'jobs' / job['id']
    folder.mkdir(parents=True, exist_ok=True)
    if job['kind'] == 'handover_test':
        result = run_handover_protocol_tests()
        (folder / 'handover_trace.json').write_text(json.dumps(result, indent=2))
        return result
    options = job.get('config') or {}
    source_path = root / '04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf'
    target_path = root / '01-autonomous-learning/src/legacy/config/explorer.urdf'
    source = URDFChain.from_urdf(source_path, 'base_link', 'gripper_frame_link')
    target = URDFChain.from_urdf(target_path, 'base_footprint', 'Gripping')
    count = int(options.get('points', 24))
    if not 5 <= count <= 200:
        raise ValueError('Virtual transfer requires between 5 and 200 points')
    source_reference = np.array([0.0, -0.6, 0.9, -0.3, 0.0])
    target_reference = np.array([0.0, -0.6, 1.0, -0.4, 0.0])
    if 'source_positions_rad' in options:
        source_positions = np.asarray(options['source_positions_rad'], dtype=float)
        if source_positions.ndim != 2 or source_positions.shape[1] != len(source.names) or not 5 <= len(source_positions) <= 200:
            raise ValueError('Source path must contain 5..200 rows of named SO-101 chain radians')
        if options.get('source_joint_names') != list(source.names):
            raise ValueError('Explicit source joint order is required')
        source_kind = 'externally_declared_joint_trajectory_not_policy_execution'
    else:
        phase = np.linspace(0, 2 * np.pi, count)
        source_positions = np.array([source_reference + np.array([0.08 * np.sin(p), 0.08 * np.sin(p / 2), 0.05 * np.sin(p), -0.06 * np.sin(p / 2), 0.02 * np.sin(p)]) for p in phase])
        source_kind = 'virtual_joint_reference_not_learned_policy'
    source_poses = [source.forward(q) for q in source_positions]
    registration = ObjectRegistration(source.forward(source_reference), target.forward(target_reference), 'virtual_declared')
    if 'registration' in options:
        entry = options['registration']
        registration = ObjectRegistration(entry['source_base_from_object'], entry['target_base_from_object'],
            entry['provenance'], tuple(entry.get('calibration_ids', [])))
    desired, achieved, target_positions, rows = [], [], [], []
    q = target_reference.copy()
    began = time.monotonic()
    tolerance = float(options.get('position_tolerance_m', 0.001))
    orientation_weight = float(options.get('orientation_weight', 0.0))
    for index, pose in enumerate(source_poses):
        goal = registration.transfer(pose)
        solution = target.inverse(goal, q, tolerance_m=tolerance, orientation_weight=orientation_weight, seed=int(options.get('seed', 0)))
        q = solution.positions
        obtained = target.forward(q)
        baseline = None
        try:
            joint_copy = target.forward(source_positions[index])
            baseline = float(np.linalg.norm(joint_copy[:3, 3] - goal[:3, 3]))
        except ValueError:
            pass
        desired.append(goal[:3, 3]); achieved.append(obtained[:3, 3]); target_positions.append(q.copy())
        rows.append(dict(point=index, reached=solution.reached, position_error_m=solution.position_error_m,
            orientation_error_rad=solution.orientation_error_rad, raw_joint_copy_error_m=baseline,
            target_x_m=float(goal[0, 3]), target_y_m=float(goal[1, 3]), target_z_m=float(goal[2, 3])))
        store.update_job(job['id'], progress=(index + 1) / len(source_poses) * 0.95)
    errors = [row['position_error_m'] for row in rows]
    baseline_errors = [row['raw_joint_copy_error_m'] for row in rows if row['raw_joint_copy_error_m'] is not None]
    np.savez(folder / 'kinematic_trajectories.npz', source_positions_rad=source_positions,
        target_positions_rad=target_positions, desired_xyz_m=desired, achieved_xyz_m=achieved)
    with (folder / 'transfer_errors.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    _plot(folder / 'frame.jpg', np.array(desired), np.array(achieved), errors)
    result = dict(scope='kinematic_virtual_transfer', source_type=source_kind, source_policy_executed=False,
        physical_execution=False, physical_skill_success_not_tested=True, collision_check_performed=False,
        orientation_constrained=orientation_weight > 0, position_tolerance_m=tolerance,
        point_count=len(rows), reachable_count=sum(row['reached'] for row in rows),
        unsupported_points=[row['point'] for row in rows if not row['reached']],
        median_position_error_m=float(np.median(errors)), max_position_error_m=float(np.max(errors)),
        raw_joint_copy_virtual_baseline=dict(valid_points=len(baseline_errors),
            median_position_error_m=float(np.median(baseline_errors)) if baseline_errors else None,
            scope='illustrative raw-joint baseline only; no claim against published transfer methods'),
        elapsed_s=time.monotonic() - began, source_joint_names=list(source.names), target_joint_names=list(target.names),
        source_urdf_sha256=source.source_sha256, target_urdf_sha256=target.source_sha256,
        source_revision=SOURCE_REVISION, registration_provenance=registration.provenance,
        gripper_transfer='not_attempted_separate_measured_aperture_calibration_required',
        method_status='KNOWN_BASELINE_ENGINEERING_GLUE', hypotheses_status='UNTESTED_HYPOTHESIS',
        method_ids=['SO101_URDF_FK_IK', 'OBJECT_RELATIVE_RETARGET', 'LELAB_CHECKPOINT_IMPORT'])
    if options.get('dataset_path') and options.get('checkpoint_path'):
        inspection = inspect_lerobot_source(options['dataset_path'], options['checkpoint_path'], options.get('source_semantics', {}))
        (folder / 'source_inspection.json').write_text(json.dumps(inspection, indent=2))
        result['source_inspection_passed'] = inspection['inspection_passed']
    artifacts = {}
    for filename in ('kinematic_trajectories.npz', 'transfer_errors.csv', 'frame.jpg'):
        artifacts[filename] = hashlib.sha256((folder / filename).read_bytes()).hexdigest()
    result['artifact_sha256'] = artifacts
    (folder / 'transfer_report.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    return result
