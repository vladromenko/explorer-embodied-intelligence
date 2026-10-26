"""ENGINEERING_GLUE: source-evidence contract for ONE ROSMASTER mobile manipulator.

This module reads supplied bytes, never devices or paths. Admission verifies a
declared dataset contract; it is not hardware qualification or a success claim.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import PurePosixPath
from types import MappingProxyType

from .contracts import ACTION_NAMES, ACTION_UNITS

SCHEMA = 'explorer_embodied_episode_v1'
ADMISSION_SCHEMA = 'explorer_embodied_episode_admission_v1'
ACTION_CONTRACT_ID = 'rosmaster_joint9_position6_velocity3_v1'
REQUIRED_CHANNELS = (
    'camera_rgb', 'camera_depth', 'camera_calibration', 'lidar',
    'odometry_pose', 'base_velocity', 'imu', 'joint_command',
    'joint_feedback', 'gripper', 'clocks', 'detections', 'goal', 'power_context',
)
OPTIONAL_CHANNELS = ('preceding_command9',)
COMMAND_ESTIMATE_PROFILE = 'rosmaster_command_estimate9_v1'
COMMAND_ESTIMATE_REQUIRED = ('camera_rgb', 'clocks', 'preceding_command9')
_KINDS = {
    'camera_rgb': 'image_asset', 'camera_depth': 'depth_asset_with_units',
    'camera_calibration': 'intrinsics_distortion_extrinsics_uncertainty',
    'lidar': 'range_scan_with_frame_and_units', 'odometry_pose': 'pose_with_frame_and_units',
    'base_velocity': 'measured_velocity3', 'imu': 'measured_imu_with_units',
    'joint_command': 'command6_not_feedback', 'joint_feedback': 'measured_position6',
    'gripper': 'measured_aperture_or_state_not_command', 'clocks': 'clock_and_boot_identity',
    'detections': 'perception_result_including_explicit_empty',
    'goal': 'recorded_task_goal', 'power_context': 'health_context_not_motor_permission',
    'preceding_command9': 'recorded_command_estimate9_not_measured_state',
}
_STAGES = ('native_dispatch', 'measured_execution', 'simulated_dispatch')


def observation_channels():
    return REQUIRED_CHANNELS + OPTIONAL_CHANNELS


def sensor_contract():
    return {name: {'required': name in REQUIRED_CHANNELS, 'kind': _KINDS[name],
                   'required_means_schema_declaration_not_sensor_availability': True,
                   'recorded_admitted_model_used_are_distinct': True}
            for name in observation_channels()}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _without_provenance(value):
    if isinstance(value, dict):
        return {key: _without_provenance(item) for key, item in value.items() if key != 'provenance'}
    if isinstance(value, list):
        return [_without_provenance(item) for item in value]
    return value


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value, length):
    return isinstance(value, list) and len(value) == length and all(_finite(item) for item in value)


def _relative(name):
    return (isinstance(name, str) and bool(name) and '\\' not in name and
            not PurePosixPath(name).is_absolute() and
            all(part not in ('', '.', '..') for part in name.split('/')))


def _pointer(document, pointer):
    if pointer == '':
        return document
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('JSON pointer required')
    value = document
    for token in pointer[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


class _Evidence:
    def __init__(self, envelope, payloads, missing):
        self.envelope = envelope
        self.payloads = payloads
        self.missing = missing
        self.decoded = {}
        declared = _mapping(envelope.get('source_files_sha256'))
        self.valid = {}
        for name, digest in declared.items():
            content = payloads.get(name)
            okay = (_relative(name) and isinstance(digest, str) and len(digest) == 64 and
                    isinstance(content, bytes) and _sha(content) == digest)
            self.valid[name] = okay
            if not okay:
                missing.append('source_files_sha256.' + str(name))
        if not declared:
            missing.append('source_files_sha256')

    def record(self, value, path):
        """Bind every claimed record field to an exact source JSON record."""
        record = _mapping(value)
        proof = _mapping(record.get('provenance'))
        name = proof.get('source_file')
        okay = isinstance(name, str) and self.valid.get(name, False)
        if okay:
            okay = proof.get('sha256') == self.envelope['source_files_sha256'][name]
        if okay:
            try:
                if name not in self.decoded:
                    self.decoded[name] = json.loads(self.payloads[name])
                source = _pointer(self.decoded[name], proof.get('json_pointer'))
                okay = _canonical(source) == _canonical(_without_provenance(record))
            except (ValueError, TypeError, KeyError, IndexError, UnicodeError, OverflowError):
                okay = False
        if not okay:
            self.missing.append(path + '.source_record')
        return okay

    def asset(self, value, path):
        value = _mapping(value)
        name = value.get('source_file')
        okay = (isinstance(name, str) and self.valid.get(name, False) and
                value.get('sha256') == self.envelope['source_files_sha256'].get(name))
        if not okay:
            self.missing.append(path + '.asset')
        return okay


def _stamp(record, domains):
    stamp = _mapping(_mapping(record).get('stamp'))
    name = stamp.get('clock_domain')
    domain = _mapping(domains.get(name)) if isinstance(name, str) else {}
    ticks = stamp.get('monotonic_ns')
    valid = (isinstance(ticks, int) and not isinstance(ticks, bool) and ticks >= 0 and
             _text(stamp.get('boot_id')) and domain.get('boot_id') == stamp.get('boot_id'))
    return stamp, valid


def _causal(observation, action, domains, links, evidence, path, max_age):
    observed, valid_observed = _stamp(observation, domains)
    commanded, valid_action = _stamp(action, domains)
    if not valid_observed or not valid_action:
        evidence.missing.append(path + '.clock_identity')
        return False
    observed_tick = observed['monotonic_ns']
    uncertainty = 0
    if observed['clock_domain'] != commanded['clock_domain']:
        matches = [link for link in links if isinstance(link, dict) and
                   link.get('source_domain') == observed['clock_domain'] and
                   link.get('source_boot_id') == observed['boot_id'] and
                   link.get('target_domain') == commanded['clock_domain'] and
                   link.get('target_boot_id') == commanded['boot_id']]
        if len(matches) != 1:
            evidence.missing.append(path + '.measured_clock_mapping')
            return False
        link = matches[0]
        offset, uncertainty = link.get('offset_ns'), link.get('uncertainty_ns')
        start, end = link.get('source_valid_from_ns'), link.get('source_valid_until_ns')
        valid_link = (evidence.record(link, path + '.clock_mapping') and
                      link.get('method') in ('measured_sync', 'simulation_common_clock') and
                      isinstance(offset, int) and not isinstance(offset, bool) and
                      isinstance(uncertainty, int) and not isinstance(uncertainty, bool) and uncertainty >= 0 and
                      isinstance(start, int) and isinstance(end, int) and
                      start <= observed_tick <= end)
        if not valid_link:
            evidence.missing.append(path + '.measured_clock_mapping')
            return False
        observed_tick += offset
    age_low = commanded['monotonic_ns'] - (observed_tick + uncertainty)
    age_high = commanded['monotonic_ns'] - (observed_tick - uncertainty)
    okay = 0 <= age_low <= age_high <= max_age
    if not okay:
        evidence.missing.append(path + '.causal_freshness')
    return okay


def _channel_shape(name, value):
    item = _mapping(value)
    if name in ('camera_rgb', 'camera_depth'):
        return (all(_text(item.get(key)) for key in ('camera_id', 'frame_id', 'encoding')) and
                all(isinstance(item.get(key), int) and item[key] > 0 for key in ('width', 'height')) and
                (name != 'camera_depth' or (item.get('units') in ('m', 'mm') and _text(item.get('aligned_to_rgb_frame_id')))))
    if name == 'camera_calibration':
        value = _mapping(value)
        return (all(value.get(key) is not None for key in
                    ('calibration_id', 'camera_id', 'intrinsics', 'distortion', 'extrinsics',
                     'frame_id', 'uncertainty', 'valid_from_ns', 'valid_until_ns')) and
                _vector(value.get('intrinsics'), 9) and isinstance(value.get('distortion'), list) and
                all(_finite(number) for number in value['distortion']) and
                _vector(value.get('extrinsics'), 16) and value.get('units') == 'm' and
                _text(value.get('depth_camera_id')) and _vector(value.get('depth_intrinsics'), 9) and
                isinstance(value.get('depth_distortion'), list) and
                all(_finite(number) for number in value['depth_distortion']) and
                _vector(value.get('depth_to_rgb_extrinsics'), 16) and
                isinstance(value.get('uncertainty'), dict) and bool(value['uncertainty']))
    if name in ('joint_command', 'joint_feedback'):
        return _vector(_mapping(value).get('values'), 6) and _mapping(value).get('units') == 'deg'
    if name == 'preceding_command9':
        return _vector(_mapping(value).get('values'), 9) and _mapping(value).get('names') == list(ACTION_NAMES)
    if name == 'base_velocity':
        return (_vector(_mapping(value).get('values'), 3) and
                _mapping(value).get('units') == ['m/s', 'm/s', 'rad/s'])
    if name == 'odometry_pose':
        return (_vector(item.get('position'), 3) and _vector(item.get('orientation_xyzw'), 4) and
                item.get('units') == 'm' and _text(item.get('frame_id')))
    if name == 'imu':
        return (_vector(item.get('acceleration'), 3) and _vector(item.get('angular_velocity'), 3) and
                item.get('units') == ['m/s^2', 'rad/s'] and _text(item.get('frame_id')))
    if name == 'lidar':
        return (isinstance(item.get('ranges'), list) and bool(item['ranges']) and
                all(_finite(number) and number >= 0 for number in item['ranges']) and
                _finite(item.get('angle_min_rad')) and _finite(item.get('angle_increment_rad')) and
                item.get('units') == 'm' and _text(item.get('frame_id')))
    if name == 'gripper':
        return (_finite(item.get('aperture')) and item['aperture'] >= 0 and item.get('units') == 'm')
    if name == 'clocks':
        return _text(item.get('clock_domain')) and _text(item.get('boot_id'))
    if name == 'detections':
        return isinstance(item.get('detections'), list) and _text(item.get('model_id')) and _text(item.get('frame_id'))
    if name == 'goal':
        return _text(item.get('goal_id')) and _text(item.get('description'))
    if name == 'power_context':
        return (_finite(item.get('voltage')) and item.get('units') == 'V' and
                'current' in item and 'charging' in item and
                all(item.get(key) in (True, False, None) for key in ('current_available', 'charging')))
    return value is not None


def _measured_channel(name, value):
    if name in ('joint_feedback', 'gripper', 'imu', 'odometry_pose', 'lidar', 'base_velocity'):
        return _mapping(value).get('state_source') in ('measured', 'simulated')
    return True


def _full_mask(mask, value):
    if isinstance(mask, bool):
        return mask
    values = _mapping(value).get('values')
    return (isinstance(mask, list) and isinstance(values, list) and len(mask) == len(values) and
            all(item is True for item in mask))


def _partial_vector_declaration(name, record):
    lengths = {'joint_command': 6, 'joint_feedback': 6, 'base_velocity': 3, 'preceding_command9': 9}
    mask, value = record.get('mask'), _mapping(record.get('value'))
    values = value.get('values')
    if (name not in lengths or not isinstance(mask, list) or not isinstance(values, list) or
            len(mask) != lengths[name] or len(values) != lengths[name] or
            any(type(item) is not bool for item in mask) or
            any(valid and not _finite(number) for valid, number in zip(mask, values))):
        return False
    units_okay = (value.get('units') == 'deg' if name.startswith('joint_') else
                  value.get('units') == ['m/s', 'm/s', 'rad/s'] if name == 'base_velocity' else
                  value.get('names') == list(ACTION_NAMES))
    return units_okay and record.get('available') is any(mask)


def _verified_outcome(record, evidence, path):
    record = _mapping(record)
    return (record.get('status') in ('success', 'failure', 'inconclusive') and
            record.get('independent_of_action_commands') is True and
            record.get('method') in ('operator_observation', 'independent_sensor', 'simulation_oracle') and
            _text(record.get('observer_id')) and isinstance(record.get('metrics'), dict) and
            bool(record['metrics']) and evidence.record(record, path))


@dataclass(frozen=True)
class EvidenceBundle:
    document: dict
    source_bytes: object


def canonicalize_episode(source_envelope, source_payloads=None):
    """Return inspection plus unchanged supplied bytes; never reconstruct sensors.

    Dict inputs have an explicitly labelled canonical serialization, not claimed
    original envelope bytes. Pass bytes to preserve the original JSON envelope.
    """
    supplied = source_payloads if isinstance(source_payloads, dict) else {}
    payloads = {name: value for name, value in supplied.items()
                if isinstance(name, str) and isinstance(value, bytes)}
    original = isinstance(source_envelope, bytes)
    malformed = False
    try:
        envelope = json.loads(source_envelope) if original else deepcopy(source_envelope)
        envelope = envelope if isinstance(envelope, dict) else {}
        envelope_bytes = source_envelope if original else _canonical(envelope)
    except (ValueError, TypeError, UnicodeError):
        envelope, envelope_bytes, malformed = {}, source_envelope if original else b'', True
    missing = []
    evidence = _Evidence(envelope, payloads, missing)
    contract = _mapping(envelope.get('action_contract'))
    contract_okay = (contract.get('id') == ACTION_CONTRACT_ID and
                     contract.get('names') == list(ACTION_NAMES) and
                     contract.get('units') == list(ACTION_UNITS) and contract.get('simultaneous') is True)
    for key in ('episode_id', 'run_id'):
        if not isinstance(envelope.get(key), str) or not envelope[key].strip():
            missing.append(key)
    if envelope.get('schema') != SCHEMA or malformed:
        missing.append('schema')
    if not contract_okay:
        missing.append('action_contract.simultaneous_9d')
    identity = _mapping(envelope.get('identity'))
    identity_fields = ('episode_id', 'run_id', 'project_id', 'source_kind', 'skill', 'episode_scope', 'action_contract')
    identity_okay = (all(identity.get(key) == envelope.get(key) for key in identity_fields) and
                     envelope.get('project_id') in ('02', '03') and envelope.get('source_kind') in ('physical', 'simulation') and
                     evidence.record(identity, 'identity'))
    if not identity_okay:
        missing.append('identity.source_bound_episode_skill_action_contract')
    if envelope.get('episode_scope') not in ('whole_episode', 'targeted_subskill', 'recovery'):
        missing.append('episode_scope')
    skill = _mapping(envelope.get('skill'))
    if not all(skill.get(key) for key in ('id', 'version', 'variant_id', 'session_id')):
        missing.append('skill.id_version_variant_session')
    domains = _mapping(envelope.get('clock_domains'))
    domain_okay = bool(domains)
    for name, domain in domains.items():
        okay = (isinstance(domain, dict) and domain.get('clock_type') == 'monotonic' and
                bool(domain.get('boot_id')) and evidence.record(domain, 'clock_domains.' + str(name)))
        domain_okay = domain_okay and okay
    if not domain_okay:
        missing.append('clock_domains')
    profile = _mapping(envelope.get('admission_profile'))
    command_estimate_profile = profile.get('id') == COMMAND_ESTIMATE_PROFILE
    required_values = COMMAND_ESTIMATE_REQUIRED if command_estimate_profile else REQUIRED_CHANNELS
    if profile and (not command_estimate_profile or profile.get('required_channels') != list(COMMAND_ESTIMATE_REQUIRED) or
                    not evidence.record(profile, 'admission_profile')):
        missing.append('admission_profile.source_bound_supported_requirements')
    reference = _mapping(envelope.get('state_estimate_reference'))
    reference_stamp, reference_clock_okay = _stamp(reference, domains)
    reference_okay = False
    if command_estimate_profile:
        reference_okay = (reference_clock_okay and reference.get('state_source') == 'command_estimate' and
                          _text(reference.get('reference_id')) and _text(reference.get('estimator_id')) and
                          reference.get('reference_kind') in ('factory_startup_reference', 'explicit_operator_reference',
                                                              'simulation_initial_state','native_command_estimate_snapshot') and
                          (reference.get('reference_kind')!='native_command_estimate_snapshot' or
                           (reference.get('native_guard_verified') is True and
                            isinstance(reference.get('prior_reference_sha256'),str) and len(reference['prior_reference_sha256'])==64)) and
                          _vector(reference.get('reference_action9'), 9) and
                          reference.get('units') == list(ACTION_UNITS) and
                          reference.get('command_log_contiguous_since_reference') is True and
                          _text(reference.get('reference_receipt_id')) and
                          evidence.record(reference, 'state_estimate_reference'))
        if not reference_okay:
            missing.append('state_estimate_reference.boot_reference_command_log')
    recording = _mapping(envelope.get('recording'))
    recording_verified = evidence.record(recording, 'recording')
    completed = recording_verified and recording.get('status') == 'completed'
    quality = recording_verified and recording.get('source_quality_usable') is True
    operator_outcome = recording.get('operator_outcome') if recording_verified else None
    if not completed:
        missing.append('recording.completed')
    if not quality:
        missing.append('recording.source_quality_usable')
    if operator_outcome not in ('success', 'failure', 'inconclusive'):
        missing.append('recording.operator_outcome')
    protocol = _mapping(envelope.get('causal_protocol'))
    max_age = protocol.get('max_observation_age_ns')
    protocol_okay = (isinstance(max_age, int) and not isinstance(max_age, bool) and max_age > 0 and
                     evidence.record(protocol, 'causal_protocol'))
    if not protocol_okay:
        missing.append('causal_protocol')
        max_age = 0
    links = envelope.get('clock_mappings', [])
    links = links if isinstance(links, list) else []
    samples = envelope.get('samples', [])
    samples = samples if isinstance(samples, list) else []
    channels = {name: {'present': False, 'available': False, 'mask': False, 'recorded': False,
                       'admitted': False, 'model_used': False, 'source_id': None, 'clock_domain': None,
                       'boot_id': None, 'presence_known': False, 'sample_count': 0, 'valid_sample_count': 0,
                       'sample_masks': [], 'sample_available': [], 'declaration_admitted': True,
                       'diagnostics': []}
                for name in observation_channels()}
    action_rows, action_validity, sample_ids, action_epochs, action_ticks = [], [], set(), set(), []
    for index, sample_value in enumerate(samples):
        sample = _mapping(sample_value)
        prefix = 'samples.' + str(index)
        sample_id = sample.get('sample_id')
        if not isinstance(sample_id, str) or not sample_id or sample_id in sample_ids:
            missing.append(prefix + '.unique_sample_id')
        sample_ids.add(sample_id if isinstance(sample_id, str) else '')
        action = _mapping(sample.get('action'))
        action_rows.append(deepcopy(action))
        stamp, stamp_okay = _stamp(action, domains)
        if stamp_okay:
            action_epochs.add((stamp['clock_domain'], stamp['boot_id']))
            action_ticks.append(stamp['monotonic_ns'])
        action_mask = action.get('mask')
        valid_action = (_vector(action.get('values'), 9) and isinstance(action_mask, list) and
                        len(action_mask) == 9 and all(item is True for item in action_mask) and
                        action.get('stage') in _STAGES and _text(action.get('source_id')) and
                        stamp_okay and evidence.record(action, prefix + '.action'))
        # An HTTP accepted/start receipt is never native dispatch evidence.
        receipt = _mapping(action.get('delivery'))
        observers = {'native_dispatch': 'native_dispatch_callback', 'measured_execution': 'joint_feedback',
                     'simulated_dispatch': 'simulation_step'}
        stage = action.get('stage') if isinstance(action.get('stage'), str) else None
        delivered = (receipt.get('level') in _STAGES and receipt.get('level') == action.get('stage') and
                     receipt.get('command_id') == action.get('command_id') and _text(action.get('command_id')) and
                     receipt.get('observer_kind') == observers.get(stage) and
                     _text(receipt.get('receipt_id')) and receipt.get('stamp') == action.get('stamp') and
                     evidence.record(receipt, prefix + '.action.delivery'))
        valid_action = valid_action and delivered
        if ((envelope.get('source_kind') == 'physical' and stage == 'simulated_dispatch') or
                (envelope.get('source_kind') == 'simulation' and stage != 'simulated_dispatch')):
            valid_action = False
        action_validity.append(valid_action)
        if not valid_action:
            missing.append(prefix + '.actual_simultaneous_9d_delivery')
        if command_estimate_profile and (not reference_okay or
                stamp.get('clock_domain') != reference_stamp.get('clock_domain') or
                stamp.get('boot_id') != reference_stamp.get('boot_id') or
                stamp.get('monotonic_ns', -1) < reference_stamp.get('monotonic_ns', 0)):
            missing.append(prefix + '.command_estimate_reference_clock_boot')
        source_channels = _mapping(sample.get('channels'))
        for name in observation_channels():
            record = _mapping(source_channels.get(name))
            report = channels[name]
            diagnostic_start = len(missing)
            present = record.get('present') is True
            available = (record.get('available') is True and present and record.get('value') is not None and
                         _full_mask(record.get('mask'), record.get('value')))
            recorded = evidence.record(record, prefix + '.' + name) if record else False
            causal = (_causal(record, action, domains, links, evidence, prefix + '.' + name, max_age)
                      if available and stamp_okay else False)
            shape = _channel_shape(name, record.get('value')) and _measured_channel(name, record.get('value'))
            if name in ('joint_feedback', 'gripper', 'imu', 'odometry_pose', 'lidar', 'base_velocity'):
                expected = 'simulated' if envelope.get('source_kind') == 'simulation' else 'measured'
                shape = shape and _mapping(record.get('value')).get('state_source') == expected
            asset = (evidence.asset(record.get('value'), prefix + '.' + name)
                     if available and name in ('camera_rgb', 'camera_depth') else True)
            valid = available and recorded and causal and shape and asset and _text(record.get('source_id'))
            unavailable_declared = (record.get('available') is False and record.get('value') is None and
                                    record.get('mask') is False and _text(record.get('unavailable_reason')))
            declaration_okay = (recorded and (isinstance(record.get('present'), bool) or record.get('present') is None) and
                                isinstance(record.get('available'), bool) and _text(record.get('source_id')) and
                                (unavailable_declared or (present and record.get('available') is True and
                                                          record.get('value') is not None and _full_mask(record.get('mask'), record.get('value')) and shape) or
                                 (present and _partial_vector_declaration(name, record))))
            if name == 'preceding_command9' and command_estimate_profile and valid:
                estimate = _mapping(record.get('value'))
                allowed_receipts = {reference['reference_receipt_id']} if _text(reference.get('reference_receipt_id')) else set()
                allowed_receipts.update(item for row in action_rows[:-1]
                                        if _text(item := _mapping(row.get('delivery')).get('receipt_id')))
                valid = (reference_okay and estimate.get('state_source') == 'command_estimate' and
                         estimate.get('reference_id') == reference.get('reference_id') and
                         estimate.get('estimator_id') == reference.get('estimator_id') and
                         _text(estimate.get('last_dispatch_receipt_id')) and
                         estimate.get('last_dispatch_receipt_id') in allowed_receipts)
                if not valid:
                    missing.append(prefix + '.' + name + '.reference_and_prior_dispatch')
            if name == 'camera_calibration' and valid:
                value = record['value']
                tick = record['stamp']['monotonic_ns']
                valid = (isinstance(value.get('valid_from_ns'), int) and isinstance(value.get('valid_until_ns'), int) and
                         value['valid_from_ns'] <= tick <= value['valid_until_ns'])
            report['present'] = report['present'] or present
            if name in REQUIRED_CHANNELS or record:
                report['declaration_admitted'] = report['declaration_admitted'] and declaration_okay
            report['presence_known'] = report['presence_known'] or isinstance(record.get('present'), bool)
            report['available'] = report['available'] or (record.get('available') is True and present and
                                                          record.get('value') is not None)
            report['recorded'] = report['recorded'] or recorded
            report['sample_count'] += 1
            report['valid_sample_count'] += int(valid)
            report['sample_masks'].append(deepcopy(record.get('mask', False)))
            report['sample_available'].append(bool(available and shape))
            if record:
                report['source_id'] = record.get('source_id')
                report['clock_domain'] = _mapping(record.get('stamp')).get('clock_domain')
                report['boot_id'] = _mapping(record.get('stamp')).get('boot_id')
            diagnostics = missing[diagnostic_start:]
            del missing[diagnostic_start:]
            report['diagnostics'].extend(diagnostics)
            if (name in REQUIRED_CHANNELS or name in required_values) and not declaration_okay:
                missing.append(prefix + '.' + name + '.explicit_source_measurement_or_unavailable_declaration')
            if name in required_values and not valid:
                missing.extend(diagnostics)
                missing.append(prefix + '.' + name + '.complete_source_causal_measurement')
        rgb = _mapping(_mapping(source_channels.get('camera_rgb')).get('value'))
        depth = _mapping(_mapping(source_channels.get('camera_depth')).get('value'))
        calibration = _mapping(_mapping(source_channels.get('camera_calibration')).get('value'))
        clocks = _mapping(_mapping(source_channels.get('clocks')).get('value'))
        if (('camera_depth' in required_values or 'camera_calibration' in required_values) and
                (not rgb.get('camera_id') or rgb.get('camera_id') != calibration.get('camera_id') or
                depth.get('camera_id') != calibration.get('depth_camera_id') or
                depth.get('aligned_to_rgb_frame_id') != rgb.get('frame_id'))):
            missing.append(prefix + '.rgb_depth_calibration_identity')
        if (clocks.get('clock_domain') != stamp.get('clock_domain') or clocks.get('boot_id') != stamp.get('boot_id')):
            missing.append(prefix + '.clock_channel_action_identity')
    if not samples:
        missing.append('samples')
    if len(action_epochs) != 1:
        missing.append('single_action_clock_boot')
    if any(second <= first for first, second in zip(action_ticks, action_ticks[1:])):
        missing.append('strictly_increasing_action_stamps')
    for report in channels.values():
        report['admitted'] = bool(samples) and report['valid_sample_count'] == len(samples)
        # Measurement validity does not depend on whether a model uses it or
        # whether the episode's unrelated split/outcome evidence is complete.
        report['mask'] = bool(samples) and all(report['sample_available'])
    phases = envelope.get('phases', [])
    phases = phases if isinstance(phases, list) else []
    phase_ids = [phase.get('id') for phase in phases if isinstance(phase, dict) and _text(phase.get('id'))]
    phase_valid = bool(phases) and len(phase_ids) == len(phases) and len(set(phase_ids)) == len(phases)
    last_end = -1
    for index, phase_value in enumerate(phases):
        phase = _mapping(phase_value)
        start, end = phase.get('start_sample'), phase.get('end_sample')
        okay = (isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and
                not isinstance(end, bool) and start == last_end + 1 and start <= end < len(samples) and
                _text(phase.get('transition_reason')) and phase.get('recovery_of') in (None, *phase_ids[:index]) and
                evidence.record(phase, 'phases.' + str(index)))
        phase_valid = phase_valid and okay
        if isinstance(end, int):
            last_end = end
    if phases:
        phase_valid = phase_valid and _mapping(phases[0]).get('start_sample') == 0 and last_end == len(samples) - 1
    if not phase_valid:
        missing.append('phases.transitions_recovery_coverage')
    outcomes = _mapping(envelope.get('outcomes'))
    phase_outcomes = _mapping(outcomes.get('per_phase'))
    outcome_okay = _verified_outcome(outcomes.get('end_to_end'), evidence, 'outcomes.end_to_end')
    for phase_id in phase_ids:
        outcome_okay = _verified_outcome(phase_outcomes.get(phase_id), evidence,
                                        'outcomes.per_phase.' + str(phase_id)) and outcome_okay
    if not outcome_okay:
        missing.append('independent_end_to_end_and_per_phase_outcomes')
    split = _mapping(envelope.get('split'))
    split_okay = (split.get('partition') in ('train', 'development', 'sealed_test') and
                  all(split.get(key) for key in ('scene_id', 'session_id', 'object_instance_id', 'group_id')) and
                  split.get('session_id') == skill.get('session_id') and
                  split.get('episode_id') == envelope.get('episode_id') and
                  split.get('group_by') == ['scene_id', 'session_id', 'episode_id', 'object_instance_id'] and
                  evidence.record(split, 'split'))
    if not split_okay:
        missing.append('scene_session_episode_object_split')
    model = _mapping(envelope.get('model_input'))
    used = model.get('channels', [])
    model_okay = (isinstance(used, list) and bool(used) and all(isinstance(name, str) for name in used) and
                  len(set(used)) == len(used) and
                  all(name in channels and channels[name]['admitted'] for name in used) and
                  model.get('action_contract_id') == ACTION_CONTRACT_ID and
                  model.get('output_names') == list(ACTION_NAMES) and
                  model.get('usage') in ('declared_plan', 'executed') and
                  (model.get('usage') != 'executed' or _text(model.get('execution_receipt_id'))) and
                  bool(model.get('implementation_id')) and evidence.record(model, 'model_input'))
    if not model_okay:
        missing.append('model_input.explicit_source_bound_9d_plan')
    for name in channels:
        channels[name]['model_used'] = model_okay and model.get('usage') == 'executed' and name in used
    missing = sorted(set(missing))
    admitted = not missing and all(action_validity) and all(channels[name]['admitted'] for name in required_values)
    document = {
        'schema': ADMISSION_SCHEMA, 'source_schema': envelope.get('schema'),
        'episode_id': envelope.get('episode_id'), 'run_id': envelope.get('run_id'),
        'action_contract_id': ACTION_CONTRACT_ID, 'unified_action_names': list(ACTION_NAMES),
        'simultaneous_9d': contract_okay, 'episode_scope': envelope.get('episode_scope'),
        'admitted_for_training': admitted, 'analysis_only': not admitted,
        'admitted_for_dataset': admitted,
        'optimizer_partition_allowed': admitted and split.get('partition') == 'train',
        'eligible_as_successful_bc_expert': (admitted and split.get('partition') == 'train' and
                                             operator_outcome == 'success' and
                                             _mapping(outcomes.get('end_to_end')).get('status') == 'success'),
        'reasons': [] if admitted else ['INCOMPLETE_OR_UNVERIFIED_UNIFIED_EPISODE'],
        'missing_facts': missing, 'channels': channels, 'sample_count': len(samples),
        'raw_simultaneous_actions': action_rows,
        'phases_verified': bool(phase_valid), 'outcomes_verified': bool(outcome_okay),
        'scene_split_verified': bool(split_okay),
        'cross_episode_split_checked': False,
        'source_kind': envelope.get('source_kind') if identity_okay else None,
        'completed_recording': bool(completed), 'source_quality_usable': bool(quality),
        'operator_outcome': operator_outcome, 'operator_success': operator_outcome == 'success',
        'session_id': skill.get('session_id'), 'scene_id': split.get('scene_id'),
        'split': deepcopy(split) if split_okay else None,
        'action9_verified': bool(samples) and all(action_validity) and contract_okay,
        'causal_verified': bool(samples) and all(channels[name]['admitted'] for name in REQUIRED_CHANNELS),
        'rgb_verified': channels['camera_rgb']['admitted'],
        'model_input_plan': {'implementation_id': model.get('implementation_id'),
                             'channels': list(used) if isinstance(used, list) else [],
                             'usage': model.get('usage'),
                             'actually_used_channels': [name for name, report in channels.items() if report['model_used']],
                             'verified': bool(model_okay), 'output_names': list(ACTION_NAMES),
                             'unused_recorded_channels': [name for name, report in channels.items()
                                                          if report['recorded'] and not report['model_used']],
                             'scope': 'one_unified_mobile_manipulator'},
        'source_integrity': {'all_declared_payloads_verified': bool(evidence.valid) and all(evidence.valid.values()),
                             'envelope_sha256': _sha(envelope_bytes),
                             'envelope_bytes_original': original,
                             'source_files_sha256': {name: _sha(value) for name, value in payloads.items()},
                             'qualification_scope': 'source_declarations_not_hardware_attestation'},
        'physical_qualification': False,
        'admission_profile': COMMAND_ESTIMATE_PROFILE if command_estimate_profile else 'full_multimodal_measurement_v1',
        'required_value_channels': list(required_values),
        'command_estimate_reference_verified': bool(reference_okay),
        'measured_joint_feedback_required': 'joint_feedback' in required_values,
        'measured_attainment_or_contact_claimed': False,
    }
    document['causal_verified'] = bool(samples) and all(channels[name]['admitted'] for name in required_values)
    document['capability_admission'] = _capability_admission(document)
    adapter = _mapping(envelope.get('input_adapter'))
    if adapter:
        # Optional provenance is diagnostic: never infer it from project/page,
        # and an unrelated adapter declaration cannot promote dataset admission.
        adapter_missing = []
        adapter_evidence = _Evidence(envelope, payloads, adapter_missing)
        adapter_verified = (all(_text(adapter.get(key)) for key in ('id', 'version', 'input_mode')) and
                            adapter_evidence.record(adapter, 'input_adapter'))
        proof = _mapping(adapter.get('provenance'))
        document['input_adapter_provenance'] = {
            'verified': bool(adapter_verified), 'id': adapter.get('id'), 'version': adapter.get('version'),
            'input_mode': adapter.get('input_mode'), 'source_file': proof.get('source_file'),
            'source_sha256': proof.get('sha256'),
        }
    else:
        document['input_adapter_provenance'] = None
    originals = {'envelope': envelope_bytes, 'payloads': MappingProxyType(dict(payloads))}
    return EvidenceBundle(document, MappingProxyType(originals))


def inspect_embodied_episode(source_envelope, source_payloads=None):
    return canonicalize_episode(source_envelope, source_payloads).document


def _capability_admission(evidence):
    channels = _mapping(evidence.get('channels'))
    contact = (COMMAND_ESTIMATE_REQUIRED if evidence.get('admission_profile') == COMMAND_ESTIMATE_PROFILE else
               ('camera_rgb', 'clocks', 'joint_feedback', 'base_velocity'))
    requirements = {
        'perception_3d': ('camera_rgb', 'camera_depth', 'camera_calibration', 'detections', 'goal'),
        'navigation': ('lidar', 'odometry_pose', 'base_velocity', 'goal', 'clocks'),
        'contact_act_bc': contact,
    }
    result = {}
    for capability, required in requirements.items():
        missing = [name for name in required if not _mapping(channels.get(name)).get('admitted')]
        result[capability] = {'required_channels': list(required), 'missing_channels': missing,
                              'evidence_admitted': evidence.get('admitted_for_dataset') is True and not missing,
                              'implementation_verified': False}
    return result


def hierarchy_plan(spec, evidence=None):
    """Describe internal components; this function implements no controller."""
    evidence = _mapping(evidence)
    channels = _mapping(evidence.get('channels'))
    capabilities = _capability_admission(evidence)
    coordinator = sorted({name for capability in capabilities.values() for name in capability['required_channels']})
    definitions = tuple((name, capability['required_channels']) for name, capability in capabilities.items()) + (
        ('coordinator', coordinator),)
    stages = []
    for name, required in definitions:
        stages.append({'id': name, 'required_channels': list(required),
                       'recorded_channels': [key for key in required if _mapping(channels.get(key)).get('recorded')],
                       'admitted_channels': [key for key in required if _mapping(channels.get(key)).get('admitted')],
                       'model_used_channels': [key for key in required if _mapping(channels.get(key)).get('model_used')],
                       'implementation_status': 'NOT_IMPLEMENTED_BY_THIS_CONTRACT'})
    missing = [key for key in coordinator if not _mapping(channels.get(key)).get('admitted')]
    return {'schema': 'explorer_embodied_hierarchy_plan_v1', 'action_contract_id': ACTION_CONTRACT_ID,
            'unified_action_names': list(ACTION_NAMES), 'simultaneous_9d': True,
            'resources': ['arm', 'gripper', 'base'], 'stages': stages,
            'full_hierarchy_status': ('EVIDENCE_DECLARED' if evidence.get('admitted_for_dataset') is True and not missing else 'BLOCKED'),
            'missing_channels': missing,
            'scope': 'internal_components_of_one_mobile_manipulator_not_separate_tasks'}
