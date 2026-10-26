"""ENGINEERING_GLUE: CPU-only ROS image/cache sink, with no actuator interface.

The existing Orbbec ROS producer remains the only camera owner. Only the audited
pure RGB-D pairing helper is imported from the legacy project; never perception,
TensorRT, GPU libraries, a controller module, or a camera device driver.
"""
from __future__ import annotations

import argparse
from collections import deque
import fcntl
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import numpy as np
from PIL import Image

HELPER_SHA256 = '659974a9083101fdd6da8df9ca516f64123365b36daf283fea665a6eea43a963'
SOURCE = 'onboard_ros_cpu_cache_v1'
NATIVE_SOURCE = 'onboard_color_callback'
NATIVE_UNIT = 'explorer-perception.service'


def load_helper(legacy_root: Path):
    path = legacy_root / 'src/rgbd_snapshot.py'
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != HELPER_SHA256:
        raise ValueError('Legacy RGB-D helper hash differs from the audited pure helper')
    spec = importlib.util.spec_from_file_location('_explorer_audited_rgbd_snapshot', path)
    module = importlib.util.module_from_spec(spec)
    # Execute the bytes that passed the hash check, without a second source read
    # or a pycache write in the original project.
    exec(compile(data, str(path), 'exec'), module.__dict__)
    if tuple(inspect.signature(module.registered_pair).parameters) != ('rgbs', 'depths', 'info', 'now', 'previous') or tuple(inspect.signature(module.save_snapshot).parameters) != ('path', 'pair'):
        raise ValueError('Legacy RGB-D public helper API differs')
    return module


def atomic_bytes(path: Path, content: bytes):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def header(message):
    stamp = message.header.stamp
    sec, nano = stamp.sec, stamp.nanosec
    if type(sec) is not int or type(nano) is not int or sec < 0 or not 0 <= nano < 1_000_000_000 or not isinstance(message.header.frame_id, str) or not message.header.frame_id:
        raise ValueError('A real ROS header stamp and optical frame are required')
    return {'sec': sec, 'nanosec': nano, 'frame_id': message.header.frame_id}


def decode_image(message):
    """Decode only documented RGB/BGR and metric depth encodings, including padding."""
    width, height, step = message.width, message.height, message.step
    if type(width) is not int or type(height) is not int or type(step) is not int or not 0 < width <= 8192 or not 0 < height <= 8192:
        raise ValueError('Invalid ROS image dimensions')
    encoding = message.encoding.lower()
    if encoding in ('rgb8', 'bgr8'):
        dtype, channels = np.dtype('u1'), 3
    elif encoding in ('16uc1', 'mono16'):
        dtype, channels = np.dtype('>u2' if message.is_bigendian else '<u2'), 1
    elif encoding == '32fc1':
        dtype, channels = np.dtype('>f4' if message.is_bigendian else '<f4'), 1
    else:
        raise ValueError('Unsupported ROS image encoding: ' + str(message.encoding))
    if step < width * channels * dtype.itemsize or len(message.data) < height * step:
        raise ValueError('ROS image buffer/row stride is incomplete')
    shape = (height, width, channels) if channels == 3 else (height, width)
    strides = (step, channels * dtype.itemsize, dtype.itemsize) if channels == 3 else (step, dtype.itemsize)
    result = np.ndarray(shape, dtype=dtype, buffer=message.data, strides=strides).astype(dtype.newbyteorder('='), copy=True)
    if encoding == 'rgb8':
        result = result[:, :, ::-1].copy()  # Legacy rgbd-snapshot stores the bridge's BGR array.
    return result, .001 if encoding in ('16uc1', 'mono16') else 1.0


def calibration(info):
    if info is None:
        return None
    k, d = list(info.k), list(info.d)
    if len(k) != 9 or not np.isfinite(k + d).all() or k[0] <= 0 or k[4] <= 0:
        return None
    value = {'k': k, 'd': d, 'width': info.width, 'height': info.height,
             'frame': info.header.frame_id, 'distortion_model': info.distortion_model}
    signature = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value, signature


class NativeGuard:
    """Stand by for any active/transitioning native unit or freshly written native cache.

The supplied systemd unit additionally enforces Conflicts+After for an ordered,
exclusive handoff. This guard alone is not a transaction against arbitrary
manual launches of legacy perception outside its systemd unit.
"""
    def __init__(self, data_dir, unit_state=None, wall=time.time, monotonic=time.monotonic):
        self.data = Path(data_dir)
        self.wall, self.monotonic = wall, monotonic
        self.state_reader = unit_state or self.read_unit
        self.last_poll, self.state = None, 'unknown'
        self.reason = 'native state has not been checked'

    @staticmethod
    def read_unit():
        try:
            result = subprocess.run(['systemctl', '--user', 'show', NATIVE_UNIT, '--property=ActiveState', '--value'],
                                    capture_output=True, text=True, timeout=2, check=False)
            return result.stdout.strip() if result.returncode == 0 else 'unknown'
        except (OSError, subprocess.TimeoutExpired):
            return 'unknown'

    def allowed(self):
        now = self.monotonic()
        if self.last_poll is None or now - self.last_poll >= .25:
            self.state, self.last_poll = self.state_reader(), now
        if self.state not in ('inactive', 'failed'):
            self.reason = 'native unit is active/transitioning or its state is unknown'
            return False
        try:
            value = json.loads((self.data / 'camera-frame.json').read_text())
            if not isinstance(value, dict):
                raise ValueError("Native camera metadata is not an object")
            age = self.wall() - float(value['at'])
            if value.get('source') == NATIVE_SOURCE and 0 <= age <= 2:
                self.reason = 'native camera source has a fresh frame'
                return False
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.reason = 'native unit inactive/failed and no fresh native cache'
        return True


class CacheSink:
    def __init__(self, data_dir, helper, guard, wall=time.time, monotonic=time.monotonic, *, boot_id=None, clock_domain=None):
        self.data = Path(data_dir)
        if not self.data.is_dir() or self.data.is_symlink():
            raise ValueError('Existing non-symlink legacy data directory is required')
        self.helper, self.guard = helper, guard
        self.boot_id=boot_id or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.clock_domain=clock_domain or 'nano:'+self.boot_id
        self.color_receipts,self.depth_receipts,self.color_bindings={},{},{}
        self.wall, self.monotonic = wall, monotonic
        self.rgbs, self.depths = deque(maxlen=8), deque(maxlen=8)
        self.color_headers, self.depth_headers = {}, {}
        self.info, self.info_calibration = None, None
        self.info_received_monotonic_ns,self.info_completed_monotonic_ns=None,None
        self.last_raw, self.previous_pair = None, 0
        self.raw_written, self.rgbd_written, self.last_error = 0, 0, None
        self.last_raw_wall, self.last_rgbd_wall, self.errors = None, None, {}

    def set_info(self, message):
        received=time.monotonic_ns()
        result = calibration(message)
        self.info, self.info_calibration = (message, result) if result else (None, None)
        self.info_received_monotonic_ns=received if result else None
        self.info_completed_monotonic_ns=time.monotonic_ns() if result else None

    def color(self, message):
        received_wall, received_monotonic_ns = self.wall(), time.monotonic_ns()
        native_header = header(message)
        stamp = native_header['sec'] + native_header['nanosec'] / 1e9
        array, _ = decode_image(message)
        if message.encoding.lower() not in ('rgb8', 'bgr8'):
            raise ValueError('Color topic is not RGB/BGR')
        self.rgbs.append((array, stamp, native_header['frame_id']))
        self.color_headers = {key: value for key, value in self.color_headers.items() if key in {row[1] for row in self.rgbs}}
        self.color_headers[stamp] = native_header
        live={row[1] for row in self.rgbs};self.color_receipts={key:value for key,value in self.color_receipts.items() if key in live};self.color_bindings={key:value for key,value in self.color_bindings.items() if key in live}
        self.color_receipts[stamp]=received_monotonic_ns
        now, mono = self.wall(), self.monotonic()
        if not self.guard.allowed() or not 0 <= now - stamp < 2 or (self.last_raw is not None and mono - self.last_raw < .12):
            return
        encoded = io.BytesIO()
        Image.fromarray(array[:, :, ::-1]).save(encoded, format='JPEG', quality=82)
        jpeg = encoded.getvalue()
        current = self.info_calibration
        matching = current and current[0]['width'] == message.width and current[0]['height'] == message.height and current[0]['frame'] == native_header['frame_id']
        metadata = dict(native_boot_id=self.boot_id,native_clock_domain=self.clock_domain,at=now, image_stamp=stamp, frame=native_header['frame_id'], width=message.width, height=message.height,
            source=SOURCE, camera_id='onboard_rgbd', frame_id=uuid.uuid4().hex,
            camera_info_version=current[1] if matching else None,
            camera_info_received_monotonic_ns=self.info_received_monotonic_ns if matching else None,
            camera_info_completed_monotonic_ns=self.info_completed_monotonic_ns if matching else None,
            intrinsic_k=current[0]['k'] if matching else None, distortion_d=current[0]['d'] if matching else None,
            ros_header=native_header, exposure_time_source='ROS_image_header_timestamp',
            sensor_exposure_time_verified=False, clock_alignment_verified=False,
            received_monotonic_ns=received_monotonic_ns, received_wall_unix=received_wall, receive_time_basis='host_callback_receive_estimate_not_exposure',
            jpeg_sha256=hashlib.sha256(jpeg).hexdigest(), jpeg='frame-raw.jpg', same_frame_hash_verified=True)
        payload = json_bytes(metadata)
        atomic_bytes(self.data / 'frame-raw.jpg', jpeg)
        atomic_bytes(self.data / 'camera-frame.json', payload)
        publication=dict(schema='explorer_CPU_cache_publication_v1',native_boot_id=self.boot_id,native_clock_domain=self.clock_domain,
            metadata_file='camera-frame.json',metadata_sha256=hashlib.sha256(payload).hexdigest(),payload_sha256=metadata['jpeg_sha256'],metadata_complete_monotonic_ns=time.monotonic_ns())
        atomic_bytes(self.data/'camera-cache-publication.json',json_bytes(publication))
        self.color_bindings[stamp]=dict(frame_id=metadata['frame_id'],jpeg_sha256=metadata['jpeg_sha256'])
        self.last_raw, self.raw_written, self.last_raw_wall = mono, self.raw_written + 1, now

    def depth(self, message):
        received_monotonic_ns=time.monotonic_ns()
        native_header = header(message)
        stamp = native_header['sec'] + native_header['nanosec'] / 1e9
        array, scale = decode_image(message)
        if message.encoding.lower() not in ('16uc1', 'mono16', '32fc1'):
            raise ValueError('Depth topic has unsupported units')
        self.depths.append((array, stamp, native_header['frame_id'], scale))
        self.depth_headers = {key: value for key, value in self.depth_headers.items() if key in {row[1] for row in self.depths}}
        self.depth_headers[stamp] = native_header
        live={row[1] for row in self.depths};self.depth_receipts={key:value for key,value in self.depth_receipts.items() if key in live};self.depth_receipts[stamp]=received_monotonic_ns

    def flush_registered(self):
        if not self.guard.allowed():
            return
        pair = self.helper.registered_pair(list(self.rgbs), list(self.depths), self.info,
                                           now=self.wall(), previous=self.previous_pair)
        if pair is not None:
            # The helper's fixed .tmp name must not follow an existing legacy
            # symlink or collide with an interrupted previous native writer.
            staged = self.data / ('.rgbd-' + uuid.uuid4().hex + '.npz')
            try:
                stamp = self.helper.save_snapshot(staged, pair)
                staged.replace(self.data / 'rgbd-snapshot.npz')
            finally:
                staged.unlink(missing_ok=True)
                staged.with_suffix('.tmp').unlink(missing_ok=True)
            rgb, depth, _ = pair
            binding=self.color_bindings.get(rgb[1],{})
            metadata = dict(source=SOURCE,native_boot_id=self.boot_id,native_clock_domain=self.clock_domain,
                snapshot_sha256=hashlib.sha256((self.data/'rgbd-snapshot.npz').read_bytes()).hexdigest(),file_complete_monotonic_ns=time.monotonic_ns(),
                color_received_monotonic_ns=self.color_receipts.get(rgb[1]),depth_received_monotonic_ns=self.depth_receipts.get(depth[1]),
                color_frame_id=binding.get('frame_id'),color_jpeg_sha256=binding.get('jpeg_sha256'),image_stamp=stamp, depth_stamp=depth[1],
                color_ros_header=self.color_headers[rgb[1]], depth_ros_header=self.depth_headers[depth[1]],
                camera_info_version=self.info_calibration[1], same_optical_frame_verified=True,
                registration_basis='Existing camera producer owns registration; audited helper checks frame, shape and skew',
                exposure_time_source='ROS_image_header_timestamp', sensor_exposure_time_verified=False,
                clock_alignment_verified=False)
            raw_metadata=json_bytes(metadata)
            atomic_bytes(self.data / 'rgbd-cache-metadata.json',raw_metadata)
            publication=dict(schema='explorer_CPU_cache_publication_v1',native_boot_id=self.boot_id,native_clock_domain=self.clock_domain,
                metadata_file='rgbd-cache-metadata.json',metadata_sha256=hashlib.sha256(raw_metadata).hexdigest(),payload_sha256=metadata['snapshot_sha256'],metadata_complete_monotonic_ns=time.monotonic_ns())
            atomic_bytes(self.data/'rgbd-cache-publication.json',json_bytes(publication))
            self.previous_pair, self.rgbd_written = stamp, self.rgbd_written + 1
            self.last_rgbd_wall = self.wall()


def readonly_check(legacy_root):
    root = Path(legacy_root).resolve()
    helper = load_helper(root)
    packages = {name: importlib.util.find_spec(name) is not None for name in ('numpy', 'PIL', 'rclpy', 'sensor_msgs','nav_msgs','std_msgs')}
    return dict(read_only=True, helper_sha256=HELPER_SHA256, helper_api_verified=bool(helper), packages=packages,
                legacy_data_exists=(root / 'data').is_dir(), native_unit_state=NativeGuard.read_unit(),
                controller_writes=0, gpu_imports=False, camera_device_open=False,
                readiness_scope='Static dependency/API check only; no ROS node, capture, cache write or service change')


def validate_runtime_paths(legacy_root, state_dir):
    root, state = Path(legacy_root).resolve(), Path(state_dir).resolve()
    if root == state or root in state.parents or state in root.parents:
        raise ValueError('Cache receipt directory must not overlap legacy source/config/controller data')
    return root, state


def run(legacy_root, state_dir, *, node_id='nano'):
    root, state = validate_runtime_paths(legacy_root, state_dir)
    if not isinstance(node_id,str) or not node_id or ':' in node_id:raise ValueError('Actual native observer node ID required')
    helper = load_helper(root)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'writer.lock').open('a') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Image as RosImage, CameraInfo, Imu, LaserScan
        from nav_msgs.msg import Odometry
        from std_msgs.msg import Float32
        from rclpy.serialization import serialize_message
        from .native_sensor_cache import SensorCacheSink
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        sensors=SensorCacheSink(state/'sensors',clock_domain=node_id+':'+boot_id,boot_id=boot_id)
        sink = CacheSink(root / 'data', helper, NativeGuard(root / 'data'),boot_id=boot_id,clock_domain=node_id+':'+boot_id)
        rclpy.init()
        node = Node('explorer_cpu_camera_cache', enable_rosout=False, start_parameter_services=False)
        def guarded(callback):
            def receive(message=None):
                try:
                    callback(message) if message is not None else callback()
                    sink.errors.pop(callback.__name__, None)
                    if not sink.errors:
                        sink.last_error = None
                except (OSError, ValueError, TypeError, KeyError) as error:
                    sink.last_error = type(error).__name__ + ': ' + str(error)
                    sink.errors[callback.__name__] = sink.last_error
            return receive
        node.create_subscription(RosImage, '/camera/color/image_raw', guarded(sink.color), qos_profile_sensor_data)
        node.create_subscription(RosImage, '/camera/depth/image_raw', guarded(sink.depth), qos_profile_sensor_data)
        node.create_subscription(CameraInfo, '/camera/color/camera_info', guarded(sink.set_info), qos_profile_sensor_data)
        node.create_timer(.05, guarded(sink.flush_registered))
        def sensor_receiver(topic):
            def receive(message):
                received=time.monotonic_ns()
                try:
                    sensors.ingest(topic,message,bytes(serialize_message(message)),received_monotonic_ns=received)
                    sensors.errors.pop(topic,None)
                except (OSError,ValueError,TypeError) as error:
                    sensors.errors[topic]=type(error).__name__+': '+str(error)
                    raise
            receive.__name__='sensor_'+topic.strip('/').replace('/','_')
            return receive
        for topic,kind in (('/odom_raw',Odometry),('/imu/data_raw',Imu),('/scan0',LaserScan),('/scan1',LaserScan),('/battery',Float32),('/camera/depth/camera_info',CameraInfo)):
            node.create_subscription(kind,topic,guarded(sensor_receiver(topic)),qos_profile_sensor_data)
        node.create_timer(.1,guarded(sensors.flush))
        def status():
            allowed = sink.guard.allowed()
            fresh = sink.last_raw_wall is not None and 0 <= time.time() - sink.last_raw_wall < 2
            rgbd_age = None if sink.last_rgbd_wall is None else time.time() - sink.last_rgbd_wall
            atomic_bytes(state / 'status.json', json_bytes(dict(at=time.time(), source=SOURCE,
                mode=('publishing' if fresh else 'waiting_for_fresh_frames') if allowed else 'standby', reason=sink.guard.reason,
                sensor_cache=sensors.status(),raw_frames_written=sink.raw_written, registered_pairs_written=sink.rgbd_written,
                rgbd_ready=allowed and rgbd_age is not None and 0 <= rgbd_age < 2,
                registered_pair_write_age_s=rgbd_age, last_registered_color_ros_stamp=sink.previous_pair or None,
                last_error=sink.last_error, errors=dict(sink.errors), helper_sha256=HELPER_SHA256, controller_writes=0, gpu_used=False)))
        node.create_timer(1, status)
        try:
            rclpy.spin(node)
        finally:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-root', type=Path, default=Path('/home/vlad/Explorer'))
    parser.add_argument('--state-dir', type=Path, default=Path.home() / '.local/share/explorer/node-state/nano-camera-cache')
    parser.add_argument('--node-id',default='nano',help='Same actual observer/native tap ID; does not configure a controller')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Read-only default; no ROS node or cache writes')
    mode.add_argument('--run', action='store_true', help='Subscribe only; use the documented ordered single-writer handoff unit')
    args = parser.parse_args()
    validate_runtime_paths(args.legacy_root, args.state_dir)
    if args.run:
        run(args.legacy_root, args.state_dir,node_id=args.node_id)
    else:
        value = readonly_check(args.legacy_root)
        print(json.dumps(value, sort_keys=True))
        if not all(value['packages'].values()) or not value['legacy_data_exists']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
