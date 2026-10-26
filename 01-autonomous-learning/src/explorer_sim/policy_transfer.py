"""method_id: ENGINEERING_GLUE / ACT_LEROBOT_0_6_1 / SO101_URDF_FK_IK / OBJECT_RELATIVE_RETARGET.

Execute a local official ACT checkpoint and its processors, then retarget its
predicted joint positions. This module has no transport, serial or hardware IO.
Offline source inference and virtual endpoint tracking are not object skills.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import fcntl
import uuid
import importlib.metadata
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import numpy as np

from explorer_shared.kinematics import ObjectRegistration, URDFChain, rotation_vector

SO_NAMES = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path, body):
    from .pilot import write_json
    write_json(Path(path), body)


def _files(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


@dataclass(frozen=True)
class JointMapping:
    """Explicit calibration interpretation, not a percent-to-radians heuristic."""
    body: dict

    def problems(self, chain, names, units, physical, calibration_sha256=None):
        b, issues = self.body, []
        if b.get('source_urdf_sha256') != chain.source_sha256:
            issues.append('source_urdf_sha256_mismatch')
        if b.get('joint_names') != list(names) or b.get('joint_units') != list(units):
            issues.append('source_named_native_contract_mismatch')
        kind = b.get('basis')
        if kind not in ('virtual_declared', 'measured_reference_pairs'):
            issues.append('radian_mapping_basis_missing')
        if physical and kind != 'measured_reference_pairs':
            issues.append('physical_source_requires_measured_radian_reference_pairs')
        if physical and (not calibration_sha256 or b.get('source_calibration_sha256') != calibration_sha256):
            issues.append('source_calibration_sha256_not_bound')
        joints = b.get('joints', {})
        for name in chain.names:
            item = joints.get(name, {})
            values = [item.get('scale_rad_per_native_unit'), item.get('offset_rad')]
            if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not np.isfinite(x) for x in values) or values[0] == 0:
                issues.append(name + ':missing_verified_sign_scale_zero')
            if kind == 'measured_reference_pairs':
                pairs = np.asarray(item.get('reference_pairs', []), dtype=float)
                uncertainty = item.get('reference_uncertainty_rad')
                valid = (pairs.ndim == 2 and pairs.shape[0] >= 2 and pairs.shape[1] == 2 and np.isfinite(pairs).all()
                    and np.ptp(pairs[:, 0]) > 1e-6 and isinstance(uncertainty, (int, float))
                    and not isinstance(uncertainty, bool) and np.isfinite(uncertainty) and uncertainty > 0)
                if valid and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in values):
                    valid = bool(np.max(np.abs(pairs[:, 0] * values[0] + values[1] - pairs[:, 1])) <= uncertainty)
                evidence = item.get('reference_evidence', {})
                evidence_path = Path(evidence.get('path', ''))
                valid = valid and evidence_path.is_file() and sha(evidence_path) == evidence.get('sha256')
                if valid:
                    try:
                        observed = json.loads(evidence_path.read_text())
                        valid = (observed.get('kind') == 'joint_radian_reference_measurements'
                            and observed.get('joint_name') == name
                            and observed.get('source_calibration_sha256') == b.get('source_calibration_sha256')
                            and observed.get('source_urdf_sha256') == chain.source_sha256
                            and observed.get('measurement_basis') in ('external_angle_measurement', 'fixture_reference_pose')
                            and observed.get('observer') and observed.get('reference_pairs') == item.get('reference_pairs')
                            and observed.get('uncertainty_rad') == uncertainty)
                    except (ValueError, UnicodeError):
                        valid = False
                if not valid:
                    issues.append(name + ':missing_or_inconsistent_reference_measurements')
        return issues

    def radians(self, native, names, chain):
        values = dict(zip(names, native))
        result = np.array([values[name] * self.body['joints'][name]['scale_rad_per_native_unit']
            + self.body['joints'][name]['offset_rad'] for name in chain.names])
        chain.forward(result)  # finite named values and geometric bounds; never clamp.
        return result


class OfficialACTSource:
    """Actual official policy + processor execution with declared Explorer adapter."""
    def __init__(self, checkpoint, expected, artifact_folder):
        import torch
        from lerobot.policies.act.configuration_act import ACTConfig
        from lerobot.policies.act.modeling_act import ACTPolicy
        from lerobot.policies.factory import make_pre_post_processors
        if importlib.metadata.version('lerobot') != '0.6.1':
            raise ValueError('This importer qualifies official lerobot==0.6.1 only')
        self.folder = Path(checkpoint).resolve()
        self.original = _files(self.folder)
        if not expected or self.original != expected:
            raise ValueError('All source checkpoint/processor files require matching SHA256')
        self.config = ACTConfig.from_pretrained(self.folder, local_files_only=True)
        if list(self.config.output_features['action'].shape) != [6]:
            raise ValueError('SO101 import requires an explicit six-axis checkpoint')
        self.config.device = 'cpu'
        self.policy = ACTPolicy.from_pretrained(self.folder, config=self.config, local_files_only=True, strict=True).cpu().eval()
        self.policy.reset()
        adapter_path = self.folder / 'explorer_preprocessing.json'
        self.adapter = json.loads(adapter_path.read_text()) if adapter_path.is_file() else None
        if self.adapter:
            mapping = self.config.normalization_mapping
            if any(str(getattr(v, 'value', v)).upper() != 'IDENTITY' for v in mapping.values()):
                raise ValueError('Explorer range/ImageNet adapter only matches identity official normalization')
            if self.adapter.get('state_transform') != '2*(state-low)/(high-low)-1' or self.adapter.get('action_transform') != 'same fixed endpoint normalization':
                raise ValueError('Unsupported explicit Explorer adapter semantics')
        persisted = [(self.folder / name).is_file() for name in ('policy_preprocessor.json', 'policy_postprocessor.json')]
        if any(persisted) and not all(persisted):
            raise ValueError('A complete matching official processor pair is required')
        if all(persisted):
            self.pre, self.post = make_pre_post_processors(self.config, pretrained_path=str(self.folder),
                preprocessor_overrides={'device_processor': {'device': 'cpu'}})
            self.processor_origin = 'persisted_official_processor_pair'
        elif self.adapter:
            self.pre, self.post = make_pre_post_processors(self.config)
            self.processor_origin = 'official_factory_identity_pair_plus_explicit_Explorer_adapter'
            artifact_folder.mkdir(parents=True, exist_ok=True)
            self.pre.save_pretrained(artifact_folder, push_to_hub=False, config_filename='policy_preprocessor.json')
            self.post.save_pretrained(artifact_folder, push_to_hub=False, config_filename='policy_postprocessor.json')
        else:
            raise ValueError('Missing official saved processors; no normalization may be guessed')
        self.image_keys = [k for k in self.config.input_features if k.startswith('observation.images.')]
        if len(self.image_keys) != 1:
            raise ValueError('Current recorded importer requires exactly one explicitly named image')
        self.torch = torch

    def infer(self, rgb, native):
        from PIL import Image
        torch = self.torch
        key = self.image_keys[0]
        shape = self.config.input_features[key].shape
        rgb = np.asarray(Image.fromarray(rgb).resize((shape[2], shape[1])))
        image = torch.from_numpy(rgb.copy()).permute(2, 0, 1).float() / 255
        state = torch.as_tensor(np.asarray(native).copy(), dtype=torch.float32)
        if self.adapter:
            low, high = np.asarray(self.adapter['state_low']), np.asarray(self.adapter['state_high'])
            if low.shape != (6,) or high.shape != (6,) or not np.isfinite([low, high]).all() or np.any(high <= low):
                raise ValueError('Invalid saved normalization endpoints')
            state = 2 * (state - torch.tensor(low, dtype=state.dtype)) / torch.tensor(high-low, dtype=state.dtype) - 1
            image = (image-torch.tensor(self.adapter['mean'])[:, None, None]) / torch.tensor(self.adapter['std'])[:, None, None]
        batch = self.pre({key: image, 'observation.state': state})
        with torch.inference_mode():
            normalized = self.policy.select_action(batch)
            predicted = self.post(normalized).detach().cpu().numpy()[0]
        if self.adapter:
            predicted = low + (predicted+1)*(high-low)/2
        if predicted.shape != (6,) or not np.isfinite(predicted).all():
            raise ValueError('Official processor output must be six finite named native coordinates')
        return predicted

    def unchanged(self):
        if self.original != _files(self.folder):
            raise ValueError('Source checkpoint or matching processors changed during inference')


class MujocoTargetVerifier:
    """Independent dynamics tracking of the M3 URDF tool frame; no hardware IO."""
    def __init__(self, target_chain, initial, *, mesh_dir=None, tolerance_m=.025, hold_steps=3, steps_per_point=400):
        import mujoco
        from legacy.simulator.explorer_model import build_mjcf
        xml = ET.fromstring(build_mjcf(mesh_dir=mesh_dir, scene='empty', verify_assets=True))
        body = xml.find(".//body[@name='base_link']")
        body.remove(body.find("freejoint[@name='base_free']"))  # explicit fixed virtual base fixture.
        self.xml = ET.tostring(xml, encoding='unicode')
        self.mj = mujoco
        self.model = mujoco.MjModel.from_xml_string(self.xml)
        self.data = mujoco.MjData(self.model)
        self.ids = [self.model.joint(n).qposadr[0] for n in target_chain.names]
        self.actuators = [self.model.actuator('servo'+str(i+1)).id for i in range(5)]
        self.data.qpos[self.ids] = initial
        self.data.ctrl[self.actuators] = initial
        mujoco.mj_forward(self.model, self.data)
        base = self.data.body('base_link')
        self.world_from_base = np.eye(4)
        self.world_from_base[:3, :3] = base.xmat.reshape(3, 3)
        self.world_from_base[:3, 3] = base.xpos
        self.tip = target_chain.tip
        self.tolerance, self.hold_steps, self.steps = tolerance_m, hold_steps, steps_per_point
        if tolerance_m <= 0 or hold_steps < 1 or steps_per_point < hold_steps:
            raise ValueError('Explicit positive independent task criteria are required')

    def verify(self, joints, desired):
        goal = self.world_from_base @ desired
        self.data.ctrl[self.actuators] = joints
        streak, max_contacts, measured = 0, 0, []
        for _ in range(self.steps):
            self.mj.mj_step(self.model, self.data)
            actual = self.data.body(self.tip)
            error = float(np.linalg.norm(actual.xpos-goal[:3, 3]))
            streak = streak+1 if error <= self.tolerance else 0
            max_contacts = max(max_contacts, int(self.data.ncon))
            measured.append(error)
        actual = self.data.body(self.tip)
        stable = np.isfinite(self.data.qpos).all() and not any(w.number for w in self.data.warning)
        return {'task': 'virtual_tool_position_tracking', 'passed': bool(streak >= self.hold_steps and stable),
            'final_consecutive_steps_within_tolerance': streak, 'finite_warning_free_dynamics': bool(stable),
            'position_tolerance_m': self.tolerance, 'required_consecutive_physics_steps': self.hold_steps,
            'physics_steps': self.steps, 'final_error_m': measured[-1], 'minimum_error_m': min(measured),
            'measured_world_xyz_m': actual.xpos.tolist(), 'desired_world_xyz_m': goal[:3, 3].tolist(),
            'orientation_error_rad_unconstrained': float(np.linalg.norm(rotation_vector(goal[:3, :3].T @ actual.xmat.reshape(3, 3)))),
            'contact_count_max': max_contacts, 'collision_safety_not_established': True,
            'object_grasp_or_transfer_not_tested': True, 'model_xml_sha256': hashlib.sha256(self.xml.encode()).hexdigest()}


def _run_policy_transfer_locked(store, job, config):
    """Root integration entry. Select one explicit source episode; no network IO."""
    root, options = Path(config['root']), job['config']
    folder = Path(store.root) / 'jobs' / job['id']
    folder.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(json.dumps(options, sort_keys=True, allow_nan=False).encode()).hexdigest()
    result_path = folder/'policy-transfer-result.json'
    if result_path.exists():
        saved = json.loads(result_path.read_text())
        lineage = saved['provenance']
        source_manifest = Path(options['dataset_manifest']).resolve()
        descriptors = json.loads(source_manifest.read_text())['episodes']
        source_entry = next(e for e in descriptors if e['id'] == lineage['episode_id'])
        source_episode = (source_manifest.parent/source_entry['file']).resolve()
        if (saved.get('request_signature') != signature or lineage['implementation_sha256'] != sha(__file__)
                or sha(folder/'policy-transfer-trace.json') != saved['trace_sha256']
                or sha(source_manifest) != lineage['manifest_sha256']
                or not source_episode.is_relative_to(source_manifest.parent) or sha(source_episode) != lineage['episode_sha256']
                or (options.get('mapping_profile') and sha(options['mapping_profile']) != lineage['mapping_profile_sha256'])
                or (options.get('checkpoint_contract_path') and sha(options['checkpoint_contract_path']) != lineage['checkpoint_contract_sha256'])
                or (options.get('training_result_path') and sha(options['training_result_path']) != lineage['training_receipt_sha256'])
                or _files(Path(options['checkpoint_path'])) != lineage['checkpoint_files_sha256']):
            raise ValueError('Completed policy transfer source/result signature changed')
        for path,key in ((root/'04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf','source_urdf_sha256'),
                         (root/'01-autonomous-learning/src/legacy/config/explorer.urdf','target_urdf_sha256')):
            if sha(path) != lineage[key]:
                raise ValueError('Completed policy transfer geometric model changed')
        return saved
    if (folder/'policy-transfer-trace.json').exists():
        archive = folder/'interrupted_attempts'/uuid.uuid4().hex
        archive.mkdir(parents=True)
        for name in ('policy-transfer-trace.json', 'constructed_processors'):
            if (folder/name).exists():
                (folder/name).rename(archive/name)
    started = time.monotonic()
    manifest_path = Path(options['dataset_manifest']).resolve()
    manifest_sha = sha(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    training_receipt_sha = None
    if manifest.get('contains_physical_recordings'):
        receipt_path = Path(options['training_result_path'])
        training_receipt_sha = sha(receipt_path)
        if training_receipt_sha != options.get('training_result_sha256'):
            raise ValueError('Physical policy training receipt checksum is required')
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('source_manifest_sha256') != manifest_sha or receipt.get('checkpoint_sha256') != options['checkpoint_sha256']:
            raise ValueError('Physical checkpoint training lineage differs from selected source data')
    names, units = manifest['action_names'], manifest['action_units']
    if tuple(names) != SO_NAMES or len(units) != 6:
        raise ValueError('Source must provide the named six-axis SO101 native contract')
    episode = next(e for e in manifest['episodes'] if e['id'] == options['source_episode_id'])
    if episode.get('split') in ('sealed_test', 'test'):
        raise ValueError('A sealed source cannot be used for transfer development')
    episode_path = (manifest_path.parent / episode['file']).resolve()
    if not episode_path.is_relative_to(manifest_path.parent) or sha(episode_path) != episode['sha256']:
        raise ValueError('Source episode path/checksum mismatch')
    source = URDFChain.from_urdf(root/'04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf', 'base_link', 'gripper_frame_link')
    target = URDFChain.from_urdf(root/'01-autonomous-learning/src/legacy/config/explorer.urdf', 'base_footprint', 'Gripping')
    actor = OfficialACTSource(options['checkpoint_path'], options['checkpoint_sha256'], folder/'constructed_processors')
    from .student import framework_info
    package = Path(importlib.metadata.distribution('lerobot').locate_file('lerobot'))
    official_sources = {name: sha(package/name) for name in ('policies/act/modeling_act.py', 'policies/act/configuration_act.py', 'policies/act/processor_act.py', 'policies/factory.py')}
    if actor.adapter:
        if actor.adapter.get('action_names') != names or actor.adapter.get('action_units') != units or actor.adapter.get('state_basis') != manifest.get('state_basis'):
            raise ValueError('Saved checkpoint named native contract differs from selected source')
    else:
        contract_path = Path(options['checkpoint_contract_path'])
        contract = json.loads(contract_path.read_text())
        if (sha(contract_path) != options.get('checkpoint_contract_sha256')
                or contract.get('source_manifest_sha256') != manifest_sha
                or contract.get('checkpoint_sha256') != options['checkpoint_sha256']
                or contract.get('action_names') != names or contract.get('action_units') != units
                or contract.get('action_representation') != 'joint_position'
                or contract.get('state_basis') != manifest.get('state_basis')):
            raise ValueError('External official checkpoint needs a SHA-bound named action contract, not shape matching')
    profile_path = Path(options['mapping_profile']) if options.get('mapping_profile') else None
    profile_sha = sha(profile_path) if profile_path else None
    mapping = JointMapping(json.loads(profile_path.read_text()) if profile_path else {})
    calibration = manifest.get('source_calibration_metadata', {}).get('calibration_metadata', {}).get('follower', {}).get('calibration_sha256')
    problems = mapping.problems(source, names, units, bool(manifest.get('contains_physical_recordings')), calibration)
    registration = None
    if not problems:
        entry = options['registration']
        registration = ObjectRegistration(entry['source_base_from_object'], entry['target_base_from_object'], entry['provenance'], tuple(entry.get('calibration_ids', [])))
    initial = np.asarray(options.get('target_initial_rad', [0, -.6, 1, -.4, 0]), float)
    target.forward(initial)
    verifier = MujocoTargetVerifier(target, initial, mesh_dir=options.get('mesh_dir')) if not problems else None
    rows, q = [], initial
    with np.load(episode_path, allow_pickle=False) as data:
        if 'state_native' not in data:
            raise ValueError('Native source states are required; normalized values are not radians')
        maximum = options.get('max_observations', 8)
        if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 1000:
            raise ValueError('Choose 1..1000 observations')
        count = min(maximum, len(data['state_native']))
        if count < 1:
            raise ValueError('No synchronized native source observations')
        for i in range(count):
            begin = time.monotonic()
            if manifest.get('contains_physical_recordings'):
                ft, st, at = [int(data[k][i]) for k in ('frame_monotonic_ns', 'state_monotonic_ns', 'action_monotonic_ns')]
                if not 0 <= ft <= st <= at:
                    raise ValueError('Physical source frame/state/dispatch chronology differs')
            action = actor.infer(data['rgb'][i], data['state_native'][i])
            row = {'index': i, 'source_episode_id': episode['id'], 'source_frame_rgb_sha256': hashlib.sha256(data['rgb'][i].tobytes()).hexdigest(),
                'input_state_native': data['state_native'][i].tolist(), 'input_state_basis': manifest.get('state_basis'),
                'predicted_action_native': action.tolist(), 'inference_ms': (time.monotonic()-begin)*1000,
                'source_policy_action_actuated': False, 'source_action_representation': 'joint_position_command'}
            for key in ('frame_monotonic_ns', 'state_monotonic_ns', 'action_monotonic_ns'):
                if key in data:
                    row[key] = int(data[key][i])
            row['source_clock_domain'] = episode.get('clock_domain', manifest.get('clock_domain'))
            if episode.get('frame_source_ids'):
                row['source_frame_id'] = episode['frame_source_ids'][i]
            if registration is not None:
                try:
                    radians = mapping.radians(action, names, source)
                    desired = registration.transfer(source.forward(radians))
                    solution = target.inverse(desired, q, tolerance_m=.001, orientation_weight=0)
                    row.update(source_positions_rad=radians.tolist(), desired_target_pose=desired.tolist(), ik=solution.as_dict())
                    if solution.reached:
                        q = solution.positions
                        row['independent_target_verification'] = verifier.verify(q, desired)
                    else:
                        row['unsupported'] = 'target_ik_unreachable_or_numerical_failure'
                except ValueError as error:
                    row['unsupported'] = str(error)
            rows.append(row)
            _json(folder/'policy-transfer-trace.json', rows)
            store.update_job(job['id'], progress=(i+1)/max(count, 1)*.95)
    actor.unchanged()
    if sha(manifest_path) != manifest_sha or sha(episode_path) != episode['sha256']:
        raise ValueError('Source data changed during transfer')
    if profile_path and sha(profile_path) != profile_sha:
        raise ValueError('Mapping profile changed during transfer')
    checks = [r['independent_target_verification'] for r in rows if 'independent_target_verification' in r]
    result = {'request_signature': signature, 'source_radian_mapping_basis': mapping.body.get('basis'), 'scope': 'offline_policy_output_transfer_with_independent_virtual_tracking', 'source_policy_executed': True,
        'source_observations': 'recorded_replay_not_closed_loop_source_execution', 'physical_execution': False,
        'physical_source_recording': bool(manifest.get('contains_physical_recordings')), 'source_task': manifest.get('task'), 'gripper_transfer': 'not_attempted_separate_aperture_calibration_required', 'orientation_constrained': False,
        'learned_object_skill_transfer_claimed': False, 'physical_skill_success_not_tested': True,
        'status': 'blocked_mapping' if problems else 'virtual_evaluated', 'mapping_problems': problems,
        'predicted_actions': len(rows), 'executed_stages': {'official_ACT_inference': len(rows), 'source_FK_and_target_IK': sum('ik' in r for r in rows), 'independent_MuJoCo_tracking': len(checks)}, 'independent_virtual_attempts': len(checks), 'independent_virtual_passed': sum(c['passed'] for c in checks),
        'unsupported_indices': [r['index'] for r in rows if r.get('unsupported')], 'elapsed_s': time.monotonic()-started,
        'processor_origin': actor.processor_origin, 'matching_official_processors_executed': True,
        'explorer_adapter_applied': actor.adapter is not None, 'source_artifacts_modified': False,
        'provenance': {'checkpoint_files_sha256': actor.original, 'manifest_sha256': manifest_sha,
            'episode_id': episode['id'], 'episode_sha256': episode['sha256'], 'split': episode.get('split'),
            'mapping_profile_sha256': profile_sha, 'training_receipt_sha256': training_receipt_sha,
            'checkpoint_contract_sha256': options.get('checkpoint_contract_sha256'),
            'source_urdf_sha256': source.source_sha256, 'target_urdf_sha256': target.source_sha256,
            'implementation_sha256': sha(__file__), 'official_sources_sha256': official_sources, 'lerobot_version': importlib.metadata.version('lerobot')},
        'method_ids': ['ACT_LEROBOT_0_6_1', 'SO101_URDF_FK_IK', 'OBJECT_RELATIVE_RETARGET', 'EXPLORER_CAD_MUJOCO', 'ENGINEERING_GLUE'],
        'trace_sha256': sha(folder/'policy-transfer-trace.json'), **framework_info()}
    _json(folder/'policy-transfer-result.json', result)
    return result


def run_policy_transfer(store, job, config):
    """Single-owner job entry; partial offline attempts are retained, never lost."""
    identity = job['id']
    if not isinstance(identity,str) or not identity or Path(identity).name != identity or identity in ('.','..'):
        raise ValueError('Invalid policy transfer job identity')
    folder = Path(store.root)/'jobs'/identity;folder.mkdir(parents=True, exist_ok=True)
    with (folder/'.policy-transfer-owner.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Policy transfer already has an active owner') from exc
        return _run_policy_transfer_locked(store, job, config)
