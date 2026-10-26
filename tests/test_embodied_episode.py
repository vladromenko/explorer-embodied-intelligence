"""Synthetic source records only: no camera, ROS, network or actuator access."""
from copy import deepcopy
import hashlib
import json

import pytest

from explorer_shared.contracts import ACTION_NAMES, ACTION_UNITS
from explorer_shared.embodied_episode import (
    ACTION_CONTRACT_ID, COMMAND_ESTIMATE_PROFILE, COMMAND_ESTIMATE_REQUIRED,
    REQUIRED_CHANNELS, SCHEMA, canonicalize_episode,
    hierarchy_plan, inspect_embodied_episode, observation_channels, sensor_contract,
)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def stamp(tick=10, domain='sim', boot='boot-1'):
    return {'clock_domain': domain, 'boot_id': boot, 'monotonic_ns': tick}


def source_facts():
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    images = {'rgb.bin': b'\x00exact-rgb-source\xff', 'depth.bin': b'\x00exact-depth-source\xff'}
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in images.items()}
    values = {
        'camera_rgb': {'source_file': 'rgb.bin', 'sha256': hashes['rgb.bin'], 'camera_id': 'cam',
                       'frame_id': 'rgb-1', 'encoding': 'test/raw', 'width': 2, 'height': 1},
        'camera_depth': {'source_file': 'depth.bin', 'sha256': hashes['depth.bin'], 'camera_id': 'depthcam',
                         'frame_id': 'depth-1', 'encoding': 'test/u16', 'width': 2, 'height': 1,
                         'units': 'mm', 'aligned_to_rgb_frame_id': 'rgb-1'},
        'camera_calibration': {'calibration_id': 'cal-1', 'camera_id': 'cam', 'frame_id': 'robot',
                               'intrinsics': [1, 0, 1, 0, 1, 1, 0, 0, 1], 'distortion': [0, 0, 0, 0, 0],
                               'extrinsics': identity, 'uncertainty': {'translation_m': 0.001},
                               'depth_camera_id': 'depthcam', 'depth_intrinsics': [1, 0, 1, 0, 1, 1, 0, 0, 1],
                               'depth_distortion': [0, 0, 0, 0, 0], 'depth_to_rgb_extrinsics': identity,
                               'units': 'm', 'valid_from_ns': 0, 'valid_until_ns': 100},
        'lidar': {'ranges': [1., 2.], 'angle_min_rad': 0., 'angle_increment_rad': .01,
                  'units': 'm', 'frame_id': 'lidar', 'state_source': 'simulated'},
        'odometry_pose': {'position': [0., 0., 0.], 'orientation_xyzw': [0., 0., 0., 1.],
                          'frame_id': 'world', 'units': 'm', 'state_source': 'simulated'},
        'base_velocity': {'values': [0., 0., 0.], 'units': ['m/s', 'm/s', 'rad/s'], 'state_source': 'simulated'},
        'imu': {'acceleration': [0., 0., 9.81], 'angular_velocity': [0., 0., 0.],
                'units': ['m/s^2', 'rad/s'], 'frame_id': 'imu', 'state_source': 'simulated'},
        'joint_command': {'values': [0.] * 6, 'units': 'deg'},
        'joint_feedback': {'values': [0.] * 6, 'units': 'deg', 'state_source': 'simulated'},
        'gripper': {'aperture': 0., 'units': 'm', 'state_source': 'simulated',
                    'force': None, 'force_available': False},
        'clocks': {'clock_domain': 'sim', 'boot_id': 'boot-1'},
        'detections': {'detections': [], 'model_id': 'synthetic-detector', 'frame_id': 'rgb-1'},
        'goal': {'goal_id': 'fixture-goal', 'description': 'mock coordinated task'},
        'power_context': {'voltage': 12., 'units': 'V', 'current': None,
                          'current_available': False, 'charging': None},
    }
    channels = {name: {'present': True, 'available': True, 'value': value, 'mask': True,
                       'source_id': 'fixture-' + name, 'stamp': stamp()}
                for name, value in values.items()}
    action_stamp = stamp(20)
    action = {'values': [0.] * 9, 'mask': [True] * 9, 'stage': 'simulated_dispatch',
              'source_id': 'fixture-dynamics', 'command_id': 'command-1', 'stamp': action_stamp,
              'delivery': {'level': 'simulated_dispatch', 'command_id': 'command-1', 'stamp': action_stamp,
                           'observer_kind': 'simulation_step', 'receipt_id': 'receipt-1'}}
    outcome = {'status': 'success', 'independent_of_action_commands': True, 'method': 'simulation_oracle',
               'observer_id': 'independent-fixture-oracle', 'metrics': {'placed': True}}
    facts = {
        'schema': SCHEMA, 'episode_id': 'native-original-id', 'run_id': 'fixture-run', 'project_id': '02',
        'source_kind': 'simulation',
        'episode_scope': 'whole_episode',
        'skill': {'id': 'skill-1', 'version': 1, 'variant_id': 'variant-1', 'session_id': 'session-1'},
        'action_contract': {'id': ACTION_CONTRACT_ID, 'names': list(ACTION_NAMES),
                            'units': list(ACTION_UNITS), 'simultaneous': True},
        'clock_domains': {'sim': {'clock_type': 'monotonic', 'boot_id': 'boot-1'}},
        'causal_protocol': {'max_observation_age_ns': 100},
        'recording': {'status': 'completed', 'source_quality_usable': True, 'operator_outcome': 'success'},
        'samples': [{'sample_id': 'sample-1', 'action': action, 'channels': channels}],
        'phases': [{'id': 'coordinated_reach', 'start_sample': 0, 'end_sample': 0,
                    'transition_reason': 'fixture completion', 'recovery_of': None}],
        'outcomes': {'end_to_end': deepcopy(outcome), 'per_phase': {'coordinated_reach': deepcopy(outcome)}},
        'split': {'partition': 'train', 'scene_id': 'scene-1', 'session_id': 'session-1',
                  'episode_id': 'native-original-id', 'object_instance_id': 'object-1', 'group_id': 'group-1',
                  'group_by': ['scene_id', 'session_id', 'episode_id', 'object_instance_id']},
        'model_input': {'implementation_id': 'fixture/fusion-plan', 'channels': list(REQUIRED_CHANNELS),
                        'action_contract_id': ACTION_CONTRACT_ID, 'output_names': list(ACTION_NAMES),
                        'usage': 'declared_plan'},
    }
    facts['identity'] = {key: deepcopy(facts[key]) for key in
                         ('episode_id', 'run_id', 'project_id', 'source_kind', 'skill', 'episode_scope', 'action_contract')}
    return facts, images


def package(facts=None, images=None):
    if facts is None:
        facts, images = source_facts()
    payloads = dict(images)
    payloads['facts.json'] = encoded(facts)
    digest = hashlib.sha256(payloads['facts.json']).hexdigest()
    envelope = deepcopy(facts)
    envelope['source_files_sha256'] = {name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()}

    def bind(record, pointer):
        record['provenance'] = {'source_file': 'facts.json', 'sha256': digest, 'json_pointer': pointer}

    for key in ('identity', 'causal_protocol', 'recording', 'split', 'model_input'):
        bind(envelope[key], '/' + key)
    for key in ('admission_profile', 'state_estimate_reference', 'input_adapter'):
        if key in envelope:
            bind(envelope[key], '/' + key)
    for name, domain in envelope['clock_domains'].items():
        bind(domain, '/clock_domains/' + name)
    for index, link in enumerate(envelope.get('clock_mappings', [])):
        bind(link, '/clock_mappings/' + str(index))
    for index, sample in enumerate(envelope['samples']):
        prefix = '/samples/' + str(index)
        bind(sample['action'], prefix + '/action')
        bind(sample['action']['delivery'], prefix + '/action/delivery')
        for name, record in sample['channels'].items():
            bind(record, prefix + '/channels/' + name)
    for index, phase in enumerate(envelope['phases']):
        bind(phase, '/phases/' + str(index))
    bind(envelope['outcomes']['end_to_end'], '/outcomes/end_to_end')
    for name, outcome in envelope['outcomes']['per_phase'].items():
        bind(outcome, '/outcomes/per_phase/' + name)
    return envelope, payloads


def inspect(facts, images):
    envelope, payloads = package(facts, images)
    return inspect_embodied_episode(envelope, payloads)


def test_complete_mock_canonical_only_episode_and_exact_original_bytes(tmp_path):
    envelope, payloads = package()
    raw = b' \n' + encoded(envelope) + b'\n'
    path = tmp_path / 'original.json'
    path.write_bytes(raw)
    bundle = canonicalize_episode(path.read_bytes(), payloads)
    report = bundle.document
    assert report['admitted_for_training'], report['missing_facts']
    assert bundle.source_bytes['envelope'] == raw
    assert dict(bundle.source_bytes['payloads']) == payloads
    assert report['source_integrity']['envelope_bytes_original']
    assert report['physical_qualification'] is False
    assert report['completed_recording'] and report['source_quality_usable'] and report['operator_success']
    assert report['scene_id'] == 'scene-1' and report['session_id'] == 'session-1'
    assert report['action9_verified'] and report['causal_verified'] and report['rgb_verified']


def test_nine_genuinely_zero_coordinates_are_never_split_or_masked():
    envelope, payloads = package()
    report = inspect_embodied_episode(envelope, payloads)
    assert report['raw_simultaneous_actions'][0]['values'] == [0.] * 9
    assert report['raw_simultaneous_actions'][0]['mask'] == [True] * 9
    assert report['unified_action_names'] == list(ACTION_NAMES)
    assert report['channels']['base_velocity']['mask'] is True
    plan = hierarchy_plan({'task_scope': 'arm_only'}, report)
    assert plan['unified_action_names'] == list(ACTION_NAMES)
    assert plan['resources'] == ['arm', 'gripper', 'base']
    assert all(stage['implementation_status'] == 'NOT_IMPLEMENTED_BY_THIS_CONTRACT' for stage in plan['stages'])


def test_missing_feedback_stays_null_and_blocks_without_masking_available_rgb():
    facts, images = source_facts()
    channel = facts['samples'][0]['channels']['joint_feedback']
    channel.update(value=None, available=False, mask=False)
    report = inspect(facts, images)
    assert report['analysis_only']
    assert report['channels']['joint_feedback']['mask'] is False
    assert report['channels']['camera_rgb']['mask'] is True
    assert report['channels']['camera_rgb']['admitted'] is True
    assert facts['samples'][0]['channels']['joint_feedback']['value'] is None


@pytest.mark.parametrize('field', ['api_accepted', 'host_requested', 'planned'])
def test_api_acceptance_or_intent_is_never_dispatch(field):
    facts, images = source_facts()
    facts['samples'][0]['action']['delivery']['level'] = field
    report = inspect(facts, images)
    assert not report['admitted_for_training'] and not report['action9_verified']
    assert any('actual_simultaneous_9d_delivery' in item for item in report['missing_facts'])


def test_tampering_values_or_any_source_bytes_fails_integrity():
    envelope, payloads = package()
    envelope['samples'][0]['action']['values'][0] = 1.
    assert not inspect_embodied_episode(envelope, payloads)['action9_verified']
    envelope, payloads = package()
    payloads['rgb.bin'] += b'alteration'
    report = inspect_embodied_episode(envelope, payloads)
    assert report['analysis_only'] and not report['source_integrity']['all_declared_payloads_verified']


def test_episode_identity_cannot_be_swapped_or_simulation_relabelled_physical():
    envelope, payloads = package()
    envelope['episode_id'] = 'different-original-id'
    report = inspect_embodied_episode(envelope, payloads)
    assert report['analysis_only']
    assert 'identity.source_bound_episode_skill_action_contract' in report['missing_facts']
    facts, images = source_facts()
    facts['source_kind'] = facts['identity']['source_kind'] = 'physical'
    assert not inspect(facts, images)['action9_verified']


@pytest.mark.parametrize('ticks', [21, -1])
def test_future_or_negative_observation_clock_rejected(ticks):
    facts, images = source_facts()
    facts['samples'][0]['channels']['camera_rgb']['stamp']['monotonic_ns'] = ticks
    assert not inspect(facts, images)['channels']['camera_rgb']['admitted']


def test_different_boot_or_domain_requires_measured_bounded_mapping():
    facts, images = source_facts()
    record = facts['samples'][0]['channels']['camera_rgb']
    record['stamp'] = stamp(100, 'pi', 'pi-boot')
    facts['clock_domains']['pi'] = {'clock_type': 'monotonic', 'boot_id': 'pi-boot'}
    assert not inspect(facts, images)['channels']['camera_rgb']['admitted']
    facts['clock_mappings'] = [{'source_domain': 'pi', 'source_boot_id': 'pi-boot',
                              'target_domain': 'sim', 'target_boot_id': 'boot-1',
                              'offset_ns': -90, 'uncertainty_ns': 2, 'source_valid_from_ns': 0,
                              'source_valid_until_ns': 200, 'method': 'measured_sync'}]
    assert inspect(facts, images)['admitted_for_training']
    facts['clock_mappings'][0]['uncertainty_ns'] = 11
    assert not inspect(facts, images)['channels']['camera_rgb']['admitted']
    facts['clock_mappings'][0]['uncertainty_ns'] = 0
    record['stamp']['boot_id'] = 'pi-rebooted'
    assert not inspect(facts, images)['channels']['camera_rgb']['admitted']


def test_no_training_grade_inference_from_busy_false_command_echo_or_power():
    facts, images = source_facts()
    facts['samples'][0]['channels']['joint_feedback']['value']['state_source'] = 'command_echo'
    facts['samples'][0]['channels']['gripper']['value']['state_source'] = 'busy_false'
    report = inspect(facts, images)
    assert report['analysis_only']
    assert not report['channels']['joint_feedback']['admitted']
    assert not report['channels']['gripper']['admitted']
    assert report['channels']['power_context']['admitted']
    assert facts['samples'][0]['channels']['power_context']['value']['charging'] is None


def test_calibration_identity_and_validity_are_not_optional():
    facts, images = source_facts()
    facts['samples'][0]['channels']['camera_calibration']['value']['valid_until_ns'] = 5
    assert not inspect(facts, images)['admitted_for_training']
    facts, images = source_facts()
    facts['samples'][0]['channels']['camera_depth']['value']['aligned_to_rgb_frame_id'] = 'different-frame'
    assert 'samples.0.rgb_depth_calibration_identity' in inspect(facts, images)['missing_facts']


def test_outcomes_independent_per_phase_and_partition_proof_required():
    facts, images = source_facts()
    facts['outcomes']['per_phase']['coordinated_reach']['independent_of_action_commands'] = False
    report = inspect(facts, images)
    assert report['phases_verified'] and not report['outcomes_verified'] and report['analysis_only']
    facts, images = source_facts()
    facts['split']['session_id'] = 'other-session'
    assert not inspect(facts, images)['scene_split_verified']


def test_structurally_recorded_failure_is_not_silently_an_expert_success():
    facts, images = source_facts()
    facts['recording']['operator_outcome'] = 'failure'
    facts['outcomes']['end_to_end']['status'] = 'failure'
    report = inspect(facts, images)
    assert report['admitted_for_training']
    assert report['operator_outcome'] == 'failure' and not report['operator_success']
    assert not report['eligible_as_successful_bc_expert']


def test_sealed_partition_is_retained_and_explicitly_excluded_from_optimization():
    facts, images = source_facts()
    facts['split']['partition'] = 'sealed_test'
    report = inspect(facts, images)
    assert report['admitted_for_dataset'] and report['scene_split_verified']
    assert not report['optimizer_partition_allowed'] and not report['eligible_as_successful_bc_expert']
    assert report['cross_episode_split_checked'] is False


def test_mask_integers_do_not_masquerade_as_valid_booleans():
    facts, images = source_facts()
    facts['samples'][0]['action']['mask'] = [1] * 9
    assert not inspect(facts, images)['action9_verified']


def test_current_act_baseline_uses_only_explicit_rgb_and_preceding_command9():
    facts, images = source_facts()
    facts['samples'][0]['channels']['preceding_command9'] = {
        'present': True, 'available': True, 'mask': [True] * 9, 'stamp': stamp(), 'source_id': 'prior-command',
        'value': {'values': [0.] * 9, 'names': list(ACTION_NAMES), 'state_source': 'command_estimate'},
    }
    facts['model_input'].update(channels=['camera_rgb', 'preceding_command9'], usage='executed',
                                execution_receipt_id='mock-feature-read-receipt')
    report = inspect(facts, images)
    assert report['admitted_for_training']
    assert report['model_input_plan']['actually_used_channels'] == ['camera_rgb', 'preceding_command9']
    assert report['channels']['lidar']['recorded'] and report['channels']['lidar']['admitted']
    assert report['channels']['lidar']['mask'] and not report['channels']['lidar']['model_used']
    del facts['samples'][0]['channels']['preceding_command9']
    report = inspect(facts, images)
    assert report['analysis_only'] and not report['channels']['preceding_command9']['admitted']
    assert 'preceding_command9' not in facts['samples'][0]['channels']


@pytest.mark.parametrize('source', [None, b'not-json', [], {'samples': [None]}])
def test_legacy_or_malformed_input_is_typed_analysis_only(source):
    report = inspect_embodied_episode(source)
    assert report['schema'] == 'explorer_embodied_episode_admission_v1'
    assert not report['admitted_for_training'] and report['missing_facts']
    assert not report['channels']['camera_rgb']['mask']
    assert hierarchy_plan({}, report)['full_hierarchy_status'] == 'BLOCKED'


def test_metadata_is_unified_and_mutating_result_does_not_mutate_input():
    envelope, payloads = package()
    original = deepcopy(envelope)
    report = inspect_embodied_episode(envelope, payloads)
    report['raw_simultaneous_actions'][0]['values'][0] = 99
    assert envelope == original
    assert set(sensor_contract()) == set(observation_channels())
    assert all(sensor_contract()[name]['required'] for name in REQUIRED_CHANNELS)


def command_estimate_facts():
    facts, images = source_facts()
    facts['source_kind'] = facts['identity']['source_kind'] = 'physical'
    facts['project_id'] = facts['identity']['project_id'] = '03'
    facts['admission_profile'] = {'id': COMMAND_ESTIMATE_PROFILE,
                                 'required_channels': list(COMMAND_ESTIMATE_REQUIRED)}
    facts['state_estimate_reference'] = {
        'reference_id': 'factory-known-reference', 'estimator_id': 'original-native-no-readback',
        'reference_kind': 'factory_startup_reference', 'state_source': 'command_estimate',
        'reference_action9': [0.] * 9, 'units': list(ACTION_UNITS), 'stamp': stamp(0),
        'command_log_contiguous_since_reference': True, 'reference_receipt_id': 'reference-receipt',
    }
    channels = facts['samples'][0]['channels']
    for name in REQUIRED_CHANNELS:
        if name not in COMMAND_ESTIMATE_REQUIRED:
            channels[name].update(available=False, value=None, mask=False,
                                   unavailable_reason='INTENTIONALLY_NO_READBACK_OR_SENSOR_NOT_AVAILABLE')
    channels['preceding_command9'] = {
        'present': True, 'available': True, 'mask': [True] * 9, 'source_id': 'native-command-estimator',
        'stamp': stamp(10), 'value': {'values': [0.] * 9, 'names': list(ACTION_NAMES),
                                     'state_source': 'command_estimate', 'reference_id': 'factory-known-reference',
                                     'estimator_id': 'original-native-no-readback',
                                     'last_dispatch_receipt_id': 'reference-receipt'},
    }
    action = facts['samples'][0]['action']
    action['stage'] = action['delivery']['level'] = 'native_dispatch'
    action['delivery']['observer_kind'] = 'native_dispatch_callback'
    facts['model_input'].update(channels=['camera_rgb','preceding_command9'], usage='declared_plan')
    return facts, images


def test_no_readback_admission_preserves_nulls_masks_and_full9_without_fake_sensor_values():
    facts, images = command_estimate_facts()
    report = inspect(facts, images)
    assert report['admitted_for_dataset'], report['missing_facts']
    assert report['action9_verified'] and report['causal_verified'] and report['command_estimate_reference_verified']
    assert not report['measured_joint_feedback_required'] and not report['measured_attainment_or_contact_claimed']
    for name in ('joint_feedback','camera_depth','lidar','imu','gripper','power_context'):
        assert report['channels'][name]['recorded'] and report['channels'][name]['declaration_admitted']
        assert not report['channels'][name]['mask'] and not report['channels'][name]['admitted']
        assert facts['samples'][0]['channels'][name]['value'] is None
    assert report['raw_simultaneous_actions'][0]['values'] == [0.] * 9
    assert report['capability_admission']['contact_act_bc']['evidence_admitted']
    assert not report['capability_admission']['navigation']['evidence_admitted']
    assert hierarchy_plan({},report)['full_hierarchy_status'] == 'BLOCKED'


@pytest.mark.parametrize('fault', ['boot', 'reference', 'dispatch', 'unavailable_mask', 'unbound_profile'])
def test_no_readback_profile_still_requires_actual_causal_reference_and_dispatch_proof(fault):
    facts, images = command_estimate_facts()
    if fault == 'boot':
        facts['state_estimate_reference']['stamp']['boot_id'] = 'previous-boot'
    elif fault == 'reference':
        facts['samples'][0]['channels']['preceding_command9']['value']['reference_id'] = 'other-reference'
    elif fault == 'dispatch':
        facts['samples'][0]['action']['delivery']['level'] = 'api_accepted'
    elif fault == 'unavailable_mask':
        facts['samples'][0]['channels']['joint_feedback']['mask'] = True
    envelope, payloads = package(facts, images)
    if fault == 'unbound_profile':
        envelope['admission_profile']['required_channels'] = []
    assert not inspect_embodied_episode(envelope,payloads)['admitted_for_dataset']


def test_no_readback_reference_does_not_synthesize_missing_preceding_command9_or_native_targets():
    facts, images = command_estimate_facts()
    del facts['samples'][0]['channels']['preceding_command9']
    report = inspect(facts, images)
    assert report['analysis_only']
    assert 'preceding_command9' not in facts['samples'][0]['channels']
    assert not report['channels']['joint_feedback']['mask']


def test_partial_optional_joint_feedback_mask_is_retained_without_blocking_command_estimate_dataset():
    facts, images = command_estimate_facts()
    record = facts['samples'][0]['channels']['joint_feedback']
    record.update(available=True, value={'values':[None, 0., 0., None, 0., 0.], 'units':'deg',
                                          'state_source':'measured'}, mask=[False,True,True,False,True,True])
    report = inspect(facts, images)
    assert report['admitted_for_dataset'], report['missing_facts']
    assert report['channels']['joint_feedback']['available']
    assert report['channels']['joint_feedback']['sample_masks'] == [[False,True,True,False,True,True]]
    assert not report['channels']['joint_feedback']['admitted']
    assert facts['samples'][0]['channels']['joint_feedback']['value']['values'][0] is None


def test_unknown_sensor_presence_is_explicit_unknown_not_a_claim_sensor_is_absent():
    facts, images = command_estimate_facts()
    facts['samples'][0]['channels']['lidar']['present'] = None
    report = inspect(facts, images)
    assert report['admitted_for_dataset']
    assert not report['channels']['lidar']['presence_known']
    assert not report['channels']['lidar']['available']


@pytest.mark.parametrize('mode', ['keyboard', 'browser_gamepad', 'so101_leader'])
def test_input_adapter_identity_is_optional_and_source_bound_not_inferred_from_page(mode):
    facts, images = command_estimate_facts()
    facts['input_adapter'] = {'id':mode,'version':'1','input_mode':mode}
    envelope,payloads = package(facts,images)
    report = inspect_embodied_episode(envelope,payloads)
    assert report['admitted_for_dataset']
    assert report['input_adapter_provenance']['verified']
    assert report['input_adapter_provenance']['input_mode'] == mode
    envelope['input_adapter']['input_mode'] = 'unbound-change'
    report = inspect_embodied_episode(envelope,payloads)
    assert not report['input_adapter_provenance']['verified']
    assert report['admitted_for_dataset']  # optional adapter cannot alter sensor/action proof
