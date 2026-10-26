"""CPU cache checks use synthetic ROS messages and the byte-pinned pure helper."""
import ast
import configparser
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import numpy as np
from PIL import Image
import pytest

from explorer_shared.nano_camera_cache import (CacheSink, HELPER_SHA256, NativeGuard,
    SOURCE, decode_image, load_helper, readonly_check, validate_runtime_paths)


def image(encoding='rgb8', *, stamp=999.8, frame='camera_color_optical_frame', big=False, padding=0):
    sec = int(stamp)
    if encoding in ('rgb8', 'bgr8'):
        native = np.full((2, 3, 3), [30, 20, 10], np.uint8)
        dtype, width_bytes = np.dtype('u1'), 9
    else:
        dtype = np.dtype(('>' if big else '<') + ('u2' if encoding == '16UC1' else 'f4'))
        native = np.full((2, 3), 1000 if encoding == '16UC1' else 1.25, dtype=dtype)
        width_bytes = 3 * dtype.itemsize
    step = width_bytes + padding
    rows = [row.tobytes() + b'X' * padding for row in native]
    return NS(width=3, height=2, step=step, data=b''.join(rows), is_bigendian=big, encoding=encoding,
        header=NS(frame_id=frame, stamp=NS(sec=sec, nanosec=round((stamp - sec) * 1e9))))


def info(frame='camera_color_optical_frame'):
    return NS(width=3, height=2, k=[500., 0., 1., 0., 500., 1., 0., 0., 1.], d=[0.] * 5,
              distortion_model='plumb_bob', header=NS(frame_id=frame))


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / 'old'
    (root / 'src').mkdir(parents=True)
    (root / 'data').mkdir()
    source = Path(__file__).parent / 'fixtures/audited_rgbd_snapshot.py'
    (root / 'src/rgbd_snapshot.py').write_bytes(source.read_bytes())
    (root / 'data/status.json').write_text('{"controller":"unchanged"}')
    (root / 'config').mkdir()
    (root / 'config/controller-profile.json').write_text('{"settings":"unchanged"}')
    return root


def sink(legacy, *, state='failed', clock=None):
    clock = [1000., 10.] if clock is None else clock
    guard = NativeGuard(legacy / 'data', unit_state=lambda: state, wall=lambda: clock[0], monotonic=lambda: clock[1])
    return CacheSink(legacy / 'data', load_helper(legacy), guard, wall=lambda: clock[0], monotonic=lambda: clock[1])


def test_pinned_helper_load_is_read_only_and_unknown_source_never_executes(legacy, monkeypatch):
    monkeypatch.setattr(NativeGuard, 'read_unit', staticmethod(lambda: 'failed'))
    before = {str(p.relative_to(legacy)): p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
    report = readonly_check(legacy)
    assert report['read_only'] and report['helper_api_verified'] and report['helper_sha256'] == HELPER_SHA256
    assert not report['gpu_imports'] and report['controller_writes'] == 0
    assert {str(p.relative_to(legacy)): p.read_bytes() for p in legacy.rglob('*') if p.is_file()} == before
    assert not (legacy / 'src/__pycache__').exists()
    (legacy / 'src/rgbd_snapshot.py').write_text("raise AssertionError('unapproved code must not execute')")
    with pytest.raises(ValueError, match='hash'):
        load_helper(legacy)


def test_only_the_hashed_bytes_execute_even_if_file_changes_after_read(legacy, monkeypatch):
    path = legacy / 'src/rgbd_snapshot.py'
    original = Path.read_bytes
    def drift_after_read(selected):
        value = original(selected)
        if selected == path:
            selected.write_text("raise AssertionError('unverified replacement must not execute')")
        return value
    monkeypatch.setattr(Path, 'read_bytes', drift_after_read)
    helper = load_helper(legacy)
    assert callable(helper.registered_pair) and callable(helper.save_snapshot)


def test_actual_raw_jpeg_hash_ros_header_and_registered_rgbd_preserved(legacy):
    cache = sink(legacy)
    cache.set_info(info())
    cache.depth(image('16UC1', stamp=999.81, big=True, padding=2))
    cache.color(image('rgb8', padding=4))
    cache.flush_registered()
    metadata = json.loads((legacy / 'data/camera-frame.json').read_text())
    jpeg = legacy / 'data/frame-raw.jpg'
    assert metadata['jpeg_sha256'] == hashlib.sha256(jpeg.read_bytes()).hexdigest()
    assert metadata['source'] == SOURCE
    assert metadata['ros_header'] == {'sec': 999, 'nanosec': 800_000_000, 'frame_id': 'camera_color_optical_frame'}
    assert metadata['image_stamp'] == 999.8
    assert not metadata['sensor_exposure_time_verified'] and not metadata['clock_alignment_verified']
    with Image.open(jpeg) as frame:
        np.testing.assert_allclose(np.asarray(frame)[0, 0], [30, 20, 10], atol=2)
    with np.load(legacy / 'data/rgbd-snapshot.npz', allow_pickle=False) as actual:
        np.testing.assert_array_equal(actual['rgb'][0, 0], [10, 20, 30])  # Legacy BGR format retained.
        np.testing.assert_allclose(actual['depth'], np.ones((2, 3)))
        np.testing.assert_array_equal(actual['k'], np.asarray(info().k).reshape(3, 3))
        assert float(actual['stamp']) == 999.8 and float(actual['depth_stamp']) == 999.81
    assert (legacy / 'data/status.json').read_text() == '{"controller":"unchanged"}'
    assert (legacy / 'config/controller-profile.json').read_text() == '{"settings":"unchanged"}'
    paired = json.loads((legacy / 'data/rgbd-cache-metadata.json').read_text())
    assert paired['color_ros_header']['nanosec'] == 800_000_000 and paired['depth_ros_header']['nanosec'] == 810_000_000
    assert not paired['clock_alignment_verified']


def test_legacy_temporary_symlink_cannot_redirect_registered_cache_write(legacy):
    target = legacy / 'config/controller-profile.json'
    before = target.read_bytes()
    stale = legacy / 'data/rgbd-snapshot.tmp'
    stale.symlink_to(target)
    cache = sink(legacy)
    cache.set_info(info())
    cache.color(image())
    cache.depth(image('16UC1'))
    cache.flush_registered()
    assert target.read_bytes() == before and stale.is_symlink()
    assert cache.rgbd_written == 1
    assert not list((legacy / 'data').glob('.rgbd-*'))


@pytest.mark.parametrize('unit_state', ['active', 'activating', 'deactivating', 'unknown'])
def test_active_or_unknown_native_source_stands_by_without_legacy_writes(legacy, unit_state):
    cache = sink(legacy, state=unit_state)
    cache.set_info(info())
    before = sorted(p.name for p in (legacy / 'data').iterdir())
    cache.color(image())
    cache.depth(image('16UC1'))
    cache.flush_registered()
    assert sorted(p.name for p in (legacy / 'data').iterdir()) == before
    assert cache.raw_written == cache.rgbd_written == 0


def test_fresh_native_cache_stands_by_then_handoff_can_write(legacy):
    clock = [1000., 10.]
    (legacy / 'data/camera-frame.json').write_text(json.dumps({'source': 'onboard_color_callback', 'at': 999.5}))
    cache = sink(legacy, clock=clock)
    cache.color(image())
    assert cache.raw_written == 0
    clock[:] = [1003., 13.]
    cache.color(image(stamp=1002.8))
    assert cache.raw_written == 1


@pytest.mark.parametrize('case', ['future', 'stale', 'wrong_optical_frame', 'no_intrinsics'])
def test_no_fake_registered_pair_for_future_stale_or_unregistered_inputs(legacy, case):
    cache = sink(legacy)
    cache.set_info(info())
    stamp = 1000.1 if case == 'future' else 997. if case == 'stale' else 999.8
    cache.color(image(stamp=stamp))
    cache.depth(image('16UC1', stamp=stamp, frame='unregistered_depth' if case == 'wrong_optical_frame' else 'camera_color_optical_frame'))
    if case == 'no_intrinsics':
        cache.set_info(None)
    cache.flush_registered()
    assert not (legacy / 'data/rgbd-snapshot.npz').exists()
    if case in ('future', 'stale'):
        assert not (legacy / 'data/frame-raw.jpg').exists()


def test_float_depth_endianness_and_invalid_stride():
    decoded, scale = decode_image(image('32FC1', big=True, padding=4))
    np.testing.assert_allclose(decoded, np.full((2, 3), 1.25))
    assert scale == 1
    broken = image()
    broken.step = 1
    with pytest.raises(ValueError, match='stride'):
        decode_image(broken)


def test_receipt_path_cannot_overwrite_controller_status(legacy):
    with pytest.raises(ValueError, match='overlap'):
        validate_runtime_paths(legacy, legacy / 'data')
    root, state = validate_runtime_paths(legacy, legacy.parent / 'new-cache-state')
    assert root == legacy and not state.exists()


def test_module_has_no_gpu_actuator_or_camera_open_path():
    path = Path(__file__).resolve().parents[1] / 'shared/explorer_shared/nano_camera_cache.py'
    tree = ast.parse(path.read_text())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not imported.intersection({'torch', 'tensorrt', 'serial', 'cv2', 'cupy'})
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls.intersection({'create_publisher', 'VideoCapture', 'sync_write', 'write_calibration'})


def test_unit_serializes_cache_handoff_without_camera_lifecycle_dependency():
    root = Path(__file__).resolve().parents[1]
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.read(root / 'scripts/explorer-nano-camera-cache.service')
    assert unit['Unit']['Conflicts'] == 'explorer-perception.service'
    assert 'explorer-perception.service' in unit['Unit']['After'].split()
    assert 'explorer-camera.service' in unit['Unit']['After'].split()
    assert 'Requires' not in unit['Unit'] and 'Wants' not in unit['Unit']
    assert unit['Service']['ExecStart'].endswith('/scripts/run_nano_camera_cache.sh --run')
    assert unit['Service']['UMask'] == '0077'


def test_exact_cached_JPEG_RGBD_binding_and_native_callback_clock_are_retained(legacy):
    cache=sink(legacy);cache.set_info(info());cache.depth(image('16UC1',stamp=999.81));cache.color(image());cache.flush_registered()
    camera=json.loads((legacy/'data/camera-frame.json').read_bytes());pair=json.loads((legacy/'data/rgbd-cache-metadata.json').read_bytes())
    assert pair['native_boot_id']==camera['native_boot_id'] and pair['native_clock_domain']==camera['native_clock_domain']
    assert pair['color_frame_id']==camera['frame_id'] and pair['color_jpeg_sha256']==camera['jpeg_sha256']
    assert pair['snapshot_sha256']==hashlib.sha256((legacy/'data/rgbd-snapshot.npz').read_bytes()).hexdigest()
    assert pair['file_complete_monotonic_ns']>=max(pair['color_received_monotonic_ns'],pair['depth_received_monotonic_ns'])
    # A paired color callback whose JPEG was throttled must never pretend to be
    # the previously displayed JPEG, even though a new RGBD array is available.
    cache.depth(image('16UC1',stamp=999.84));cache.color(image(stamp=999.83));cache.flush_registered()
    newer=json.loads((legacy/'data/rgbd-cache-metadata.json').read_bytes())
    assert newer['color_frame_id'] is None and newer['color_jpeg_sha256'] is None
    assert json.loads((legacy/'data/camera-frame.json').read_bytes())['frame_id']==camera['frame_id']
