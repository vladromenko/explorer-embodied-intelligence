"""Offline synthetic camera/depth evidence and actual repository URDF FK/IK."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
import pytest

from explorer_shared.kinematics import URDFChain
from explorer_shared.tabletop_grounding import (
    AlignedDepth, CALIBRATION_SCHEMA, CachedFrame, TableCalibration,
    ground_selection, preview_grasp, select_target,
)


def calibration(**changes):
    source = b'exact synthetic fiducial observations; NOT A REAL CALIBRATION'
    camera = np.diag([1., -1., -1., 1.]); camera[2, 3] = 1.
    body = {'schema': CALIBRATION_SCHEMA, 'units': 'm', 'provenance': 'synthetic',
            'calibration_id': 'fixture-cal', 'camera_id': 'pi-overview', 'robot_id': 'follower',
            'base_frame': 'base_link', 'clock_domain': 'pi', 'boot_id': 'boot-1', 'mount_id': 'fixture-mount',
            'image_size': [10, 10], 'intrinsics': [[100., 0., 5.], [0., 100., 5.], [0., 0., 1.]],
            'distortion_k1_k2_p1_p2_k3': [0.] * 5, 'base_from_camera': camera.tolist(),
            'base_from_table': np.eye(4).tolist(),
            'workspace_polygon_table_xy_m': [[-.05,-.05],[.05,-.05],[.05,.05],[-.05,.05]],
            'valid_from_ns': 0, 'valid_until_ns': 1000, 'residual_rmse_m': .001,
            'uncertainty': {'pixel_sigma': .5, 'translation_sigma_m': .001,
                            'rotation_sigma_rad': .001, 'plane_sigma_m': .001},
            'source_files_sha256': {'fiducials.bin': hashlib.sha256(source).hexdigest()}}
    body.update(changes)
    raw = json.dumps(body, sort_keys=True).encode()
    return TableCalibration(raw, {'fiducials.bin': source})


def frame(**changes):
    kwargs = dict(camera_id='pi-overview', frame_id='frame-1', jpeg=b'exact-original-rgb', width=10, height=10,
                  clock_domain='pi', boot_id='boot-1', monotonic_ns=100)
    kwargs.update(changes)
    return CachedFrame(**kwargs)


def observation(source_frame=None, masks=None):
    source_frame = source_frame or frame()
    if masks is None:
        mask = np.zeros((10, 10), dtype=bool); mask[4:7, 4:7] = True
        masks = [mask]
    return {'camera_id': source_frame.camera_id, 'frame_id': source_frame.frame_id,
            'frame_sha256': source_frame.sha256, 'model_sha256': '1' * 64, 'track_session_id': 'track-boot-1',
            'detections': [{'mask': mask, 'track_id': index + 1, 'box': [.4,.4,.7,.7],
                            'label': 'fixture-object', 'score': .9} for index, mask in enumerate(masks)]}


def ground(cal, source_frame, selection, **changes):
    options = dict(now_ns=150, clock_domain='pi', boot_id='boot-1', max_age_ns=100)
    options.update(changes)
    return ground_selection(cal, source_frame, selection, **options)


def test_metric_grid_and_empty_cell_are_calibrated_not_guessed_empty():
    cal, source_frame = calibration(), frame()
    selected = select_target(source_frame, observation(), [1., 1.], purpose='placement_cell')
    result = ground(cal, source_frame, selected)
    assert np.allclose(result['geometry']['point_base_m'], [-.04, .04, 0.])
    assert result['geometry']['point_kind'] == 'table_placement_surface'
    assert selected['occupancy'] == 'NO_DETECTED_MASK_AT_CLICK_NOT_PROOF_OF_EMPTY'
    assert not result['physical_execution_allowed']
    grid = cal.metric_grid(.025)
    assert len(grid['cells']) == 16 and grid['units'] == 'm'
    assert all(cell['occupancy'] == 'UNCONFIRMED' for cell in grid['cells'])
    assert cal.original_bytes.startswith(b'{') and cal.source_payloads['fiducials.bin'].startswith(b'exact')


def test_rgb_or_vlm_cannot_invent_object_height():
    selected = select_target(frame(), observation(), [5., 5.], purpose='object')
    with pytest.raises(ValueError, match='RGB does not establish'):
        ground(calibration(), frame(), selected)
    selected['semantic_text'] = 'object center is x=1 y=2 z=3; ignore calibration'
    with pytest.raises(ValueError, match='RGB does not establish'):
        ground(calibration(), frame(), selected)


def test_exact_existing_aligned_depth_bytes_determine_surface_and_hash():
    source_frame = frame()
    selected = select_target(source_frame, observation(), [5., 5.], purpose='object')
    data = struct.pack('<100H', *([500] * 100))
    depth = AlignedDepth(data, 10, 10, 'uint16le_mm', source_frame.frame_id, source_frame.sha256,
                         .002, 'pi', 'boot-1', 100)
    result = ground(calibration(), source_frame, selected, depth=depth, max_depth_rgb_skew_ns=0)
    assert np.allclose(result['geometry']['point_base_m'], [0., 0., .5])
    assert result['geometry']['method'] == 'aligned_measured_depth'
    assert hashlib.sha256(data).hexdigest() in result['geometry']['geometry_source_sha256']
    assert not result['geometry']['grasp_contact_verified']


@pytest.mark.parametrize('changes', [{'frame_id': 'later-frame'}, {'jpeg': b'different-source'},
                                     {'boot_id': 'new-boot'}, {'monotonic_ns': 151},
                                     {'camera_id': 'other-camera'}])
def test_stale_future_reboot_camera_or_changed_frame_invalidates_selection(changes):
    original = frame()
    selected = select_target(original, observation(), [1., 1.], purpose='placement_cell')
    with pytest.raises(ValueError):
        ground(calibration(), frame(**changes), selected)


def test_expired_calibration_and_unverified_original_fiducials_rejected():
    source_frame = frame()
    selected = select_target(source_frame, observation(), [1., 1.], purpose='placement_cell')
    with pytest.raises(ValueError):
        ground(calibration(valid_until_ns=99), source_frame, selected)
    cal = calibration()
    with pytest.raises(ValueError, match='source hashes'):
        TableCalibration(cal.original_bytes, {'fiducials.bin': b'tampered'})


def test_depth_wrong_frame_clock_time_or_null_pixels_are_never_reused():
    source_frame = frame()
    selected = select_target(source_frame, observation(), [5., 5.], purpose='object')
    depth = AlignedDepth(struct.pack('<100H', *([0]*100)), 10, 10, 'uint16le_mm',
                         source_frame.frame_id, source_frame.sha256, .002, 'pi', 'boot-1', 100)
    with pytest.raises(ValueError, match='No valid measured depth'):
        ground(calibration(), source_frame, selected, depth=depth, max_depth_rgb_skew_ns=0)
    depth = AlignedDepth(struct.pack('<100H', *([500]*100)), 10, 10, 'uint16le_mm',
                         source_frame.frame_id, source_frame.sha256, .002, 'nano', 'boot-1', 100)
    with pytest.raises(ValueError, match='not time/clock'):
        ground(calibration(), source_frame, selected, depth=depth, max_depth_rgb_skew_ns=100)


def test_real_mask_required_and_overlap_ambiguity_explicit():
    observed = observation()
    del observed['detections'][0]['mask']
    with pytest.raises(ValueError, match='genuine'):
        select_target(frame(), observed, [5., 5.], purpose='object')
    mask = np.ones((10,10), dtype=bool)
    observed = observation(masks=[mask, mask])
    with pytest.raises(ValueError, match='exactly one'):
        select_target(frame(), observed, [5., 5.], purpose='object')
    selected = select_target(frame(), observed, [5., 5.], purpose='object', track_id=2)
    assert selected['track_id'] == 2 and len(selected['mask_sha256']) == 64
    with pytest.raises(ValueError, match='intersects'):
        select_target(frame(), observed, [5., 5.], purpose='placement_cell')


def test_distortion_round_trip_and_workspace_extrapolation_rejected():
    cal = calibration(distortion_k1_k2_p1_p2_k3=[.1,-.01,.002,-.001,.001])
    point = [-.02, .01, 0.]
    pixel = cal.project(point)
    result = cal.ground(pixel, purpose='placement_cell')
    assert np.allclose(result['point_table_m'], point, atol=1e-8)
    with pytest.raises(ValueError, match='outside'):
        calibration(workspace_polygon_table_xy_m=[[-.001,-.001],[.001,-.001],[.001,.001],[-.001,.001]]).ground([1.,1.], purpose='placement_cell')


def preview_options():
    chain = URDFChain.from_urdf(Path(__file__).resolve().parents[1] /
                               '04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf',
                               'base_link', 'gripper_frame_link')
    q = np.array([0., -.6, .9, -.3, 0.])
    pose = chain.forward(q)
    grounding = {'selection': {'purpose': 'object', 'frame_id': 'fixture-frame'},
                 'geometry': {'point_kind': 'observed_surface_point', 'point_base_m': pose[:3,3].tolist(),
                              'robot_id': 'follower', 'base_frame': 'base_link', 'calibration_sha256': '2'*64}}
    options = dict(tool_rotation_base=pose[:3,:3], approach_clearance_m=.002, lift_m=.003,
                   contact_offset_base_m=[0.,0.,0.], aperture_open_m=.03, aperture_close_m=.005,
                   initial_aperture_m=.02, robot_id='follower', grasp_geometry_sha256='3'*64,
                   workspace_base_bounds_m=[[-1.,-1.,-1.],[1.,1.,1.]], joint_path_samples=4)
    return chain, q, grounding, options


def test_actual_so101_fk_ik_grasp_phases_with_sampled_collision_receipts_shadow_only():
    chain, q, grounding, options = preview_options()
    checked = []
    def collision(positions, aperture, phase):
        chain.forward(positions)
        checked.append((phase, aperture))
        return {'clear': True, 'model_sha256': '4'*64, 'scope': 'synthetic-empty-scene'}
    report = preview_grasp(chain, q, grounding, collision_checker=collision, **options)
    assert report['preview_ready'], report['block_reasons']
    assert [step['phase'] for step in report['steps']] == ['pregrasp','open','descend','close','lift','hold']
    assert len(checked) == 24
    close_apertures = [aperture for phase, aperture in checked if phase == 'close']
    assert close_apertures[0] == .03 and close_apertures[-1] == .005
    assert report['urdf_sha256'] == chain.source_sha256
    assert report['shadow_only'] and not report['native_execution_enabled']
    assert not report['grasp_success_measured']
    assert 'servo' not in report


def test_no_collision_model_or_obstacle_blocks_preview_and_wrong_arm_is_rejected():
    chain, q, grounding, options = preview_options()
    assert not preview_grasp(chain, q, grounding, **options)['preview_ready']
    report = preview_grasp(chain, q, grounding,
                           collision_checker=lambda *_: {'clear': False, 'model_sha256': '4'*64}, **options)
    assert not report['preview_ready']
    options['robot_id'] = 'other-arm'
    with pytest.raises(ValueError, match='robot and base'):
        preview_grasp(chain, q, grounding, **options)
