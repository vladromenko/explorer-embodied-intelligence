"""ENGINEERING_GLUE: SO101 source learning calibrated geometry and deterministic grasp PREVIEW only.

No camera/device/network/servo owner lives here. Inputs are immutable source
artifacts and cached-frame evidence. RGB/VLM semantics never supply depth.
"""
from .experiments import experiment_identity
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import struct

import numpy as np

from .kinematics import rigid_transform

CALIBRATION_SCHEMA = 'explorer_tabletop_calibration_v1'


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _array(value, shape):
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError('Finite geometry of the declared shape is required')
    return array


def _inside_polygon(point, polygon):
    x, y = point
    inside = False
    for first, second in zip(polygon, np.roll(polygon, -1, axis=0)):
        a, b = second - first, np.asarray(point) - first
        cross = a[0] * b[1] - a[1] * b[0]
        if abs(cross) < 1e-10 and np.dot(b, a) >= 0 and np.dot(b, a) <= np.dot(a, a):
            return True
        if (first[1] > y) != (second[1] > y):
            edge_x = first[0] + (y - first[1]) * (second[0] - first[0]) / (second[1] - first[1])
            if x < edge_x:
                inside = not inside
    return inside


@dataclass(frozen=True)
class CachedFrame:
    camera_id: str
    frame_id: str
    jpeg: bytes
    width: int
    height: int
    clock_domain: str
    boot_id: str
    monotonic_ns: int
    timestamp_basis: str = 'host_receive_estimate'

    def __post_init__(self):
        if (not all(isinstance(item, str) and item for item in
                    (self.camera_id, self.frame_id, self.clock_domain, self.boot_id)) or
                not isinstance(self.jpeg, bytes) or not self.jpeg or min(self.width, self.height) <= 0 or
                type(self.monotonic_ns) is not int or self.monotonic_ns < 0):
            raise ValueError('Explicit cached-frame bytes and original clock identity required')

    @property
    def sha256(self):
        return _sha(self.jpeg)

    def metadata(self):
        return dict(camera_id=self.camera_id, frame_id=self.frame_id, frame_sha256=self.sha256,
                    width=self.width, height=self.height, clock_domain=self.clock_domain,
                    boot_id=self.boot_id, monotonic_ns=self.monotonic_ns,
                    timestamp_basis=self.timestamp_basis)


@dataclass(frozen=True)
class AlignedDepth:
    """Exact existing depth-cache bytes; no capture or RGB-derived depth."""
    data: bytes
    width: int
    height: int
    encoding: str
    rgb_frame_id: str
    rgb_frame_sha256: str
    sigma_m: float
    clock_domain: str
    boot_id: str
    monotonic_ns: int

    def at(self, pixel):
        sizes = {'uint16le_mm': (2, '<H', .001), 'float32le_m': (4, '<f', 1.)}
        if self.encoding not in sizes:
            raise ValueError('Explicit supported depth encoding/units required')
        size, format_string, scale = sizes[self.encoding]
        if (not isinstance(self.data, bytes) or min(self.width, self.height) <= 0 or
                len(self.data) != self.width * self.height * size or
                not isinstance(self.sigma_m, (float, int)) or not math.isfinite(self.sigma_m) or self.sigma_m < 0):
            raise ValueError('Exact depth dimensions/bytes/uncertainty required')
        u, v = _array(pixel, (2,))
        if not (0 <= u < self.width and 0 <= v < self.height):
            raise ValueError('Depth pixel outside aligned frame')
        value = struct.unpack_from(format_string, self.data, (int(v) * self.width + int(u)) * size)[0] * scale
        if not math.isfinite(value) or value <= 0:
            raise ValueError('No valid measured depth at selected pixel')
        return {'camera_z_m': value, 'sigma_m': self.sigma_m, 'pixel': list(pixel),
                'source_sha256': _sha(self.data), 'rgb_frame_id': self.rgb_frame_id,
                'rgb_frame_sha256': self.rgb_frame_sha256}


class TableCalibration:
    """Measured or explicitly synthetic Brown-Conrady calibration artifact."""
    def __init__(self, original_bytes, source_payloads):
        if not isinstance(original_bytes, bytes):
            raise ValueError('Exact original calibration bytes required')
        body = json.loads(original_bytes)
        if (body.get('schema') != CALIBRATION_SCHEMA or body.get('units') != 'm' or
                body.get('provenance') not in ('measured', 'synthetic') or
                not all(body.get(key) for key in ('calibration_id', 'camera_id', 'robot_id', 'base_frame',
                                                 'clock_domain', 'boot_id', 'mount_id'))):
            raise ValueError('Identified metric table/camera/robot calibration required')
        self.K = _array(body.get('intrinsics'), (3, 3))
        if (self.K[0, 0] <= 0 or self.K[1, 1] <= 0 or
                not np.allclose(self.K[2], [0, 0, 1]) or abs(np.linalg.det(self.K)) < 1e-12):
            raise ValueError('Pinhole camera intrinsics required')
        self.distortion = _array(body.get('distortion_k1_k2_p1_p2_k3'), (5,))
        self.base_from_camera = rigid_transform(body.get('base_from_camera'))
        self.base_from_table = rigid_transform(body.get('base_from_table'))
        self.table_from_base = np.linalg.inv(self.base_from_table)
        polygon = np.asarray(body.get('workspace_polygon_table_xy_m'), dtype=float)
        if polygon.ndim != 2 or polygon.shape[1] != 2 or len(polygon) < 3 or not np.isfinite(polygon).all():
            raise ValueError('Measured table workspace polygon required')
        area = np.sum(polygon[:, 0] * np.roll(polygon[:, 1], -1) - polygon[:, 1] * np.roll(polygon[:, 0], -1)) / 2
        if abs(area) < 1e-10:
            raise ValueError('Nondegenerate workspace required')
        self.polygon = polygon
        image_size = body.get('image_size')
        if not isinstance(image_size, list) or len(image_size) != 2 or any(type(size) is not int or size <= 0 for size in image_size):
            raise ValueError('Original calibration image size required')
        uncertainty = body.get('uncertainty', {})
        self.uncertainty = {key: float(uncertainty[key]) for key in
                            ('pixel_sigma', 'translation_sigma_m', 'rotation_sigma_rad', 'plane_sigma_m')}
        if any(not math.isfinite(value) or value < 0 for value in self.uncertainty.values()):
            raise ValueError('Explicit finite uncertainty required')
        if (type(body.get('valid_from_ns')) is not int or type(body.get('valid_until_ns')) is not int or
                not 0 <= body['valid_from_ns'] <= body['valid_until_ns']):
            raise ValueError('Clock-bound calibration validity required')
        artifacts = body.get('source_files_sha256', {})
        if not artifacts or any(not isinstance(source_payloads.get(name), bytes) or
                                _sha(source_payloads[name]) != digest for name, digest in artifacts.items()):
            raise ValueError('Original fiducial/calibration source hashes must verify')
        if not isinstance(body.get('residual_rmse_m'), (int, float)) or not math.isfinite(body['residual_rmse_m']) or body['residual_rmse_m'] < 0:
            raise ValueError('Measured or synthetic fit residual must be reported')
        self.original_bytes, self.source_payloads = original_bytes, dict(source_payloads)
        self.body, self.sha256 = deepcopy(body), _sha(original_bytes)

    def check_frame(self, frame, *, now_ns, clock_domain, boot_id, max_age_ns):
        if (frame.camera_id != self.body['camera_id'] or [frame.width, frame.height] != self.body['image_size'] or
                frame.clock_domain != clock_domain or frame.clock_domain != self.body['clock_domain'] or
                frame.boot_id != boot_id or frame.boot_id != self.body['boot_id'] or
                not self.body['valid_from_ns'] <= frame.monotonic_ns <= self.body['valid_until_ns'] or
                type(now_ns) is not int or type(max_age_ns) is not int or max_age_ns <= 0 or
                not 0 <= now_ns - frame.monotonic_ns <= max_age_ns):
            raise ValueError('Frame, camera mount, calibration, clock/boot or freshness mismatch')

    def _distort(self, xy):
        x, y = xy
        k1, k2, p1, p2, k3 = self.distortion
        radius = x*x + y*y
        radial = 1 + k1*radius + k2*radius**2 + k3*radius**3
        return np.array([x*radial + 2*p1*x*y + p2*(radius + 2*x*x),
                         y*radial + p1*(radius + 2*y*y) + 2*p2*x*y])

    def ray(self, pixel):
        u, v = _array(pixel, (2,))
        width, height = self.body['image_size']
        if not (0 <= u < width and 0 <= v < height):
            raise ValueError('Pixel outside original calibration image')
        distorted = np.linalg.solve(self.K, [u, v, 1.])[:2]
        undistorted = distorted.copy()
        for _ in range(30):
            error = self._distort(undistorted) - distorted
            if np.linalg.norm(error) < 1e-10:
                break
            columns = [(self._distort(undistorted + np.eye(2)[index] * 1e-6) -
                        self._distort(undistorted)) / 1e-6 for index in range(2)]
            undistorted -= np.linalg.solve(np.column_stack(columns), error)
        if not np.isfinite(undistorted).all() or np.linalg.norm(self._distort(undistorted) - distorted) > 1e-7:
            raise ValueError('Undistortion did not converge; no guessed geometry')
        return np.array([*undistorted, 1.])

    def project(self, point_table):
        point = np.linalg.inv(self.base_from_camera) @ self.base_from_table @ np.array([*_array(point_table, (3,)), 1.])
        if point[2] <= 0:
            raise ValueError('Table point behind camera')
        image = self.K @ np.array([*self._distort(point[:2] / point[2]), 1.])
        return image[:2].tolist()

    def ground(self, pixel, *, purpose, depth=None):
        """Return geometry with honest surface semantics; caller checks freshness."""
        if purpose not in ('object', 'receptacle', 'placement_cell'):
            raise ValueError('Explicit selection purpose required')
        ray = self.ray(pixel)
        geometry_sources = [self.sha256]
        if depth is not None:
            value = depth.get('camera_z_m')
            sigma = depth.get('sigma_m')
            if (not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0 or
                    not isinstance(sigma, (float, int)) or not math.isfinite(sigma) or sigma < 0 or
                    not isinstance(depth.get('source_sha256'), str) or len(depth['source_sha256']) != 64 or
                    depth.get('pixel') != list(pixel)):
                raise ValueError('Aligned measured depth with exact pixel/source uncertainty required')
            point_base = self.base_from_camera @ np.array([*(ray * value), 1.])
            method, point_kind = 'aligned_measured_depth', 'observed_surface_point'
            geometry_sources.append(depth['source_sha256'])
        else:
            if purpose != 'placement_cell':
                raise ValueError('RGB does not establish object/receptacle height; measured depth/geometry required')
            table_from_camera = self.table_from_base @ self.base_from_camera
            origin = table_from_camera[:3, 3]
            direction = table_from_camera[:3, :3] @ ray
            if abs(direction[2]) < 1e-10:
                raise ValueError('Ray parallel to calibrated plane')
            distance = -origin[2] / direction[2]
            if distance <= 0:
                raise ValueError('Plane intersection behind camera')
            point_base = self.base_from_table @ np.array([*(origin + distance * direction), 1.])
            sigma, method, point_kind = self.uncertainty['plane_sigma_m'], 'calibrated_table_plane', 'table_placement_surface'
        point_table = self.table_from_base @ point_base
        if not _inside_polygon(point_table[:2], self.polygon) or point_table[2] < -3 * max(sigma, self.uncertainty['plane_sigma_m']):
            raise ValueError('Grounded point outside calibrated workspace or below table')
        camera_distance = float(np.linalg.norm(point_base[:3] - self.base_from_camera[:3, 3]))
        estimate = (self.uncertainty['translation_sigma_m'] + self.uncertainty['rotation_sigma_rad'] * camera_distance +
                    self.uncertainty['pixel_sigma'] * camera_distance / min(self.K[0, 0], self.K[1, 1]) + sigma)
        return {'point_base_m': point_base[:3].tolist(), 'point_table_m': point_table[:3].tolist(),
                'method': method, 'point_kind': point_kind, 'uncertainty_bound_m': estimate,
                'uncertainty_model': 'sum_of_declared_1sigma_terms_not_statistical_confidence',
                'calibration_id': self.body['calibration_id'], 'calibration_sha256': self.sha256,
                'geometry_source_sha256': geometry_sources, 'provenance': self.body['provenance'],
                'robot_id': self.body['robot_id'], 'base_frame': self.body['base_frame'],
                'grasp_contact_verified': False, 'physical_execution_allowed': False}

    def metric_grid(self, cell_size_m):
        if not isinstance(cell_size_m, (float, int)) or not math.isfinite(cell_size_m) or cell_size_m <= 0:
            raise ValueError('Positive metric cell size required')
        low, high = np.min(self.polygon, axis=0), np.max(self.polygon, axis=0)
        dimensions = np.ceil((high-low) / cell_size_m).astype(int)
        if np.prod(dimensions) > 10000:
            raise ValueError('Grid exceeds bounded preview size')
        cells = []
        for row in range(dimensions[1]):
            for column in range(dimensions[0]):
                center = low + (np.array([column, row]) + .5) * cell_size_m
                corners = [center + np.array(offset) * cell_size_m / 2 for offset in ((-1,-1),(1,-1),(1,1),(-1,1))]
                if all(_inside_polygon(corner, self.polygon) for corner in corners):
                    cells.append({'cell_id': str(row) + ':' + str(column), 'center_table_m': [*center.tolist(), 0.],
                                  'corners_pixel': [self.project([*corner, 0.]) for corner in corners],
                                  'occupancy': 'UNCONFIRMED'})
        return {'schema': 'explorer_tabletop_metric_grid_v1', 'cell_size_m': cell_size_m,
                'calibration_sha256': self.sha256, 'cells': cells, 'units': 'm'}


def _bound_observation(frame, observation):
    return (observation.get('frame_id') == frame.frame_id and
            observation.get('frame_sha256') == frame.sha256 and observation.get('camera_id') == frame.camera_id and
            isinstance(observation.get('model_sha256'), str) and len(observation['model_sha256']) == 64 and
            bool(observation.get('track_session_id')))


def select_target(frame, observation, pixel, *, purpose, track_id=None):
    """Select an actual source mask; a box is not silently turned into a mask."""
    if not _bound_observation(frame, observation):
        raise ValueError('Detection/mask result must match exact displayed source frame')
    u, v = _array(pixel, (2,))
    if not (0 <= u < frame.width and 0 <= v < frame.height):
        raise ValueError('Selection outside source frame')
    candidates = []
    for item in observation.get('detections', []):
        mask = np.asarray(item.get('mask'))
        if mask.shape != (frame.height, frame.width) or mask.dtype != np.bool_:
            raise ValueError('A genuine full-resolution Boolean instance mask is required')
        if mask[int(v), int(u)] and (track_id is None or item.get('track_id') == track_id):
            candidates.append(item)
    if purpose == 'placement_cell':
        if candidates:
            raise ValueError('Placement click intersects a detected object mask')
        item = None
    elif purpose in ('object', 'receptacle'):
        if len(candidates) != 1:
            raise ValueError('Selection must resolve exactly one tracked instance')
        item = candidates[0]
        if item.get('track_id') is None:
            raise ValueError('Track identity is required')
    else:
        raise ValueError('Unknown selection purpose')
    mask_sha = _sha(np.packbits(np.asarray(item['mask'], dtype=bool)).tobytes()) if item else None
    return {'schema': 'explorer_tabletop_selection_v1', **frame.metadata(), 'pixel': [float(u), float(v)],
            'purpose': purpose, 'track_id': item.get('track_id') if item else None,
            'track_session_id': observation['track_session_id'], 'mask_sha256': mask_sha,
            'box': deepcopy(item.get('box')) if item else None, 'label': item.get('label') if item else None,
            'score': item.get('score') if item else None, 'model_sha256': observation['model_sha256'],
            'occupancy': 'NO_DETECTED_MASK_AT_CLICK_NOT_PROOF_OF_EMPTY' if item is None else 'SELECTED_INSTANCE',
            'semantic_coordinates_used': False, 'physical_execution_allowed': False}


def ground_selection(calibration, frame, selection, *, now_ns, clock_domain, boot_id, max_age_ns, depth=None,
                     max_depth_rgb_skew_ns=None):
    calibration.check_frame(frame, now_ns=now_ns, clock_domain=clock_domain, boot_id=boot_id, max_age_ns=max_age_ns)
    if any(selection.get(key) != frame.metadata()[key] for key in ('camera_id', 'frame_id', 'frame_sha256')):
        raise ValueError('Selected frame changed; reselect or explicitly validate track on new frame')
    depth_value = None
    if depth is not None:
        if (not isinstance(depth, AlignedDepth) or depth.rgb_frame_id != frame.frame_id or
                depth.rgb_frame_sha256 != frame.sha256 or (depth.width, depth.height) != (frame.width, frame.height) or
                depth.clock_domain != frame.clock_domain or depth.boot_id != frame.boot_id or
                type(max_depth_rgb_skew_ns) is not int or max_depth_rgb_skew_ns < 0 or
                abs(depth.monotonic_ns - frame.monotonic_ns) > max_depth_rgb_skew_ns or
                not 0 <= now_ns - depth.monotonic_ns <= max_age_ns):
            raise ValueError('Depth is not time/clock/boot/pixel aligned to this exact RGB frame')
        depth_value = depth.at(selection['pixel'])
    geometry = calibration.ground(selection['pixel'], purpose=selection['purpose'], depth=depth_value)
    return {'schema': 'explorer_tabletop_grounding_v1', **experiment_identity('so101_source_learning'),
            'selection': deepcopy(selection), 'geometry': geometry, 'physical_execution_allowed': False}


def preview_grasp(chain, initial_positions, grounding, *, tool_rotation_base, approach_clearance_m,
                  lift_m, contact_offset_base_m, aperture_open_m, aperture_close_m,
                  initial_aperture_m, robot_id, grasp_geometry_sha256, workspace_base_bounds_m, collision_checker=None,
                  joint_path_samples=12, hold_s=1.):
    """Real FK/IK and sampled collision hooks; produces no native servo values.

    The caller supplies identified contact/tool/aperture geometry. An observed
    depth surface alone is not a grasp contact solution or collision proof.
    """
    geometry = grounding.get('geometry', {})
    if geometry.get('point_kind') != 'observed_surface_point' or geometry.get('robot_id') is None:
        raise ValueError('A grounded observed object surface is required')
    if geometry.get('robot_id') != robot_id or geometry.get('base_frame') != chain.base:
        raise ValueError('Calibrated robot and base frame must match the named IK chain')
    if grounding.get('selection', {}).get('purpose') != 'object':
        raise ValueError('Grasp preview requires the selected object, not an empty table cell')
    if not isinstance(grasp_geometry_sha256, str) or len(grasp_geometry_sha256) != 64:
        raise ValueError('Identified measured/synthetic tool/contact/aperture geometry required')
    parameters = (approach_clearance_m, lift_m, aperture_open_m, aperture_close_m, initial_aperture_m, hold_s)
    if (any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in parameters) or
            min(approach_clearance_m, lift_m, hold_s) <= 0 or not 0 <= aperture_close_m < aperture_open_m or
            initial_aperture_m < 0 or
            type(joint_path_samples) is not int or not 2 <= joint_path_samples <= 1000):
        raise ValueError('Explicit positive geometric plan parameters required')
    orientation = np.eye(4)
    orientation[:3, :3] = _array(tool_rotation_base, (3, 3))
    rigid_transform(orientation)
    point = _array(geometry.get('point_base_m'), (3,)) + _array(contact_offset_base_m, (3,))
    bounds = _array(workspace_base_bounds_m, (2, 3))
    if np.any(bounds[0] >= bounds[1]):
        raise ValueError('Named robot workspace bounds required')
    # Use the caller's calibrated approach direction, never a VLM direction.
    approach_axis = orientation[:3, 2]
    pregrasp, lifted = point - approach_axis * approach_clearance_m, point - approach_axis * lift_m
    targets = (('pregrasp', pregrasp, aperture_open_m), ('open', pregrasp, aperture_open_m),
               ('descend', point, aperture_open_m), ('close', point, aperture_close_m),
               ('lift', lifted, aperture_close_m), ('hold', lifted, aperture_close_m))
    q = _array(initial_positions, (len(chain.names),))
    chain.forward(q)  # validates existing URDF joint limits; never changes them
    steps, blocked, current_aperture = [], [], initial_aperture_m
    for name, xyz, aperture in targets:
        target = orientation.copy()
        target[:3, 3] = xyz
        if np.any(xyz < bounds[0]) or np.any(xyz > bounds[1]):
            blocked.append(name + ':OUTSIDE_WORKSPACE')
        result = chain.inverse(target, q, orientation_weight=.03, restarts=0, seed=0)
        collision = 'UNVERIFIED'
        collision_receipts = []
        path_workspace_clear = None
        if result.reached:
            path_workspace_clear = True
            collision = 'CLEAR_SAMPLED_PATH' if collision_checker is not None else 'UNVERIFIED'
            for positions, path_aperture in zip(np.linspace(q, result.positions, joint_path_samples),
                                                 np.linspace(current_aperture, aperture, joint_path_samples)):
                path_xyz = chain.forward(positions)[:3, 3]
                if np.any(path_xyz < bounds[0]) or np.any(path_xyz > bounds[1]):
                    path_workspace_clear = False
                if collision_checker is not None:
                    receipt = collision_checker(positions.copy(), float(path_aperture), name)
                    if (not isinstance(receipt, dict) or receipt.get('clear') is not True or
                            not isinstance(receipt.get('model_sha256'), str) or len(receipt['model_sha256']) != 64):
                        collision = 'BLOCKED_OR_UNVERIFIED'
                    collision_receipts.append(deepcopy(receipt))
            if not path_workspace_clear:
                blocked.append(name + ':PATH_OUTSIDE_WORKSPACE')
            if collision != 'CLEAR_SAMPLED_PATH':
                blocked.append(name + ':COLLISION_' + collision)
        else:
            blocked.append(name + ':IK_UNREACHABLE')
        steps.append({'phase': name, 'target_base_from_tool': target.tolist(), 'aperture_m': aperture,
                      'ik': result.as_dict(), 'collision_preview': collision,
                      'sampled_tool_path_inside_workspace': path_workspace_clear,
                      'collision_receipts': collision_receipts, 'hold_s': hold_s if name == 'hold' else None})
        if result.reached:
            q, current_aperture = result.positions, aperture
    return {'schema': 'explorer_tabletop_grasp_preview_v1', **experiment_identity('so101_source_learning'),
            'method_id': 'CALIBRATED_GEOMETRIC_GRASP_BASELINE', 'steps': steps,
            'chain_names': list(chain.names), 'urdf_sha256': chain.source_sha256,
            'calibration_sha256': geometry.get('calibration_sha256'),
            'grasp_geometry_sha256': grasp_geometry_sha256, 'source_grounding': deepcopy(grounding),
            'preview_ready': not blocked, 'block_reasons': sorted(set(blocked)),
            'collision_scope': 'sampled_named_model_only_not_continuous_physical_clearance',
            'shadow_only': True, 'native_execution_enabled': False,
            'native_execution_block_reasons': ['SEPARATE_USER_OBSERVED_QUALIFICATION_REQUIRED',
                                               'NO_ACTUATOR_TRANSPORT_IN_THIS_MODULE'],
            'grasp_success_measured': False}
