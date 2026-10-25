from pathlib import Path
import json

import numpy as np
import pytest

from explorer_shared.kinematics import URDFChain, ObjectRegistration, ClutchRetargeter, map_aperture, rigid_transform, transform
from explorer.transfer import run_virtual_experiment, inspect_lerobot_source

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def chains():
    return (URDFChain.from_urdf(ROOT / '04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf', 'base_link', 'gripper_frame_link'),
            URDFChain.from_urdf(ROOT / '01-autonomous-learning/src/legacy/config/explorer.urdf', 'base_footprint', 'Gripping'))


def test_actual_models_have_different_named_five_joint_chains(chains):
    source, target = chains
    assert len(source.names) == len(target.names) == 5
    assert source.names[0] == 'shoulder_pan'
    assert target.names[0] == 'arm1_Joint'
    assert 'gripper' not in source.names
    assert source.source_sha256 == '3a65d2d35e68a8d2f0c2cc176d19b884506543c93ba72980145b80abe276022c'


def test_forward_inverse_actual_rosmaster_round_trip(chains):
    _, chain = chains
    q = np.array([0.1, -.6, 1., -.4, .1])
    target = chain.forward(q)
    result = chain.inverse(target, q + .05, orientation_weight=.03)
    assert result.reached
    assert result.position_error_m < .001
    assert result.orientation_error_rad < .05


def test_unreachable_target_reports_error_without_silent_clamping(chains):
    _, chain = chains
    result = chain.inverse(transform((10, 10, 10)), max_iterations=12, restarts=0)
    assert not result.reached
    assert result.position_error_m > 10
    assert np.all(result.positions >= chain.lower)
    assert np.all(result.positions <= chain.upper)


def test_named_chain_bounds_and_bad_frames_rejected(chains):
    source, _ = chains
    with pytest.raises(ValueError):
        source.forward([100] * 5)
    with pytest.raises(ValueError):
        source.forward([0] * 6)
    with pytest.raises(ValueError):
        rigid_transform(np.diag([-1, 1, 1, 1]))
    with pytest.raises(ValueError):
        URDFChain.from_urdf(ROOT / '04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf', 'gripper_link', 'base_link')


def test_registration_preserves_object_relative_effect():
    source_object = transform((.1, .2, .3), (.1, .2, .3))
    target_object = transform((.4, -.1, .2), (0, .2, -.1))
    effect = transform((.01, 0, .03), (.02, 0, 0))
    registration = ObjectRegistration(source_object, target_object, 'measured', ('source-cal', 'target-cal'))
    assert np.allclose(np.linalg.inv(target_object) @ registration.transfer(source_object @ effect), effect)
    with pytest.raises(ValueError):
        ObjectRegistration(source_object, target_object, 'measured')


def test_clutch_recenter_does_not_jump(chains):
    source, target = chains
    sq, tq = [0, -.6, .9, -.3, 0], [0, -.6, 1, -.4, 0]
    clutch = ClutchRetargeter(source, target)
    assert clutch.update(sq, tq, clutch=True) is None
    result = clutch.update(sq, tq)
    assert result.reached
    assert np.allclose(target.forward(result.positions), target.forward(tq))


def test_gripper_requires_separate_identified_endpoint_calibration():
    with pytest.raises(ValueError):
        map_aperture(50, source_closed=0, source_open=100, target_closed=170, target_open=30, calibration_id='')
    assert map_aperture(50, source_closed=0, source_open=100, target_closed=170, target_open=30, calibration_id='measured-endpoints') == 100


class LocalStore:
    def __init__(self, root):
        self.root, self.updates = root, []

    def update_job(self, identifier, **values):
        self.updates.append((identifier, values))


def test_real_virtual_transfer_artifacts_are_computed(tmp_path):
    store = LocalStore(tmp_path)
    result = run_virtual_experiment(store, dict(id='kinematic-test', kind='virtual_transfer', config={'points': 12}), {'root': str(ROOT)})
    assert result['point_count'] == 12
    assert result['reachable_count'] >= 10
    assert result['max_position_error_m'] < .005
    assert result['physical_execution'] is False
    assert result['source_policy_executed'] is False
    assert result['registration_provenance'] == 'virtual_declared'
    assert len(result['artifact_sha256']) == 3
    assert (tmp_path / 'jobs/kinematic-test/frame.jpg').is_file()
    assert len(store.updates) == 12


def test_checkpoint_inspection_refuses_shape_only_import(tmp_path):
    dataset, checkpoint = tmp_path / 'dataset', tmp_path / 'checkpoint'
    (dataset / 'meta').mkdir(parents=True); checkpoint.mkdir()
    (dataset / 'meta/info.json').write_text(json.dumps({'features': {'action': {'shape': [6]}}}))
    (checkpoint / 'config.json').write_text(json.dumps({'type': 'act'}))
    result = inspect_lerobot_source(dataset, checkpoint, {})
    assert not result['inspection_passed']
    assert not result['direct_action_head_transfer_allowed']
    assert not result['extraction_ready']
    assert result['original_artifacts_modified'] is False
