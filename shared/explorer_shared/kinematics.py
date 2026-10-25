"""ENGINEERING_GLUE: URDF geometry and bounded numerical inverse kinematics.

This module produces geometric solutions, never motor commands. URDF/CAD geometry
and a successful IK solve do not establish physical calibration or collision safety.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def rigid_transform(value):
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError('A finite 4x4 rigid transform is required')
    rotation = matrix[:3, :3]
    if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8) or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(rotation), 1, atol=1e-6):
        raise ValueError('Transform must be in SE(3), without scale or reflection')
    return matrix.copy()


def transform(xyz=(0, 0, 0), rpy=(0, 0, 0)):
    x, y, z = np.asarray(xyz, dtype=float)
    roll, pitch, yaw = np.asarray(rpy, dtype=float)
    cr, sr, cp, sp, cy, sy = np.cos(roll), np.sin(roll), np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
    result = np.eye(4)
    result[:3, :3] = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                      [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                      [-sp, cp * sr, cp * cr]]
    result[:3, 3] = [x, y, z]
    return rigid_transform(result)


def rotation_vector(rotation):
    angle = np.arccos(np.clip((np.trace(rotation) - 1) / 2, -1, 1))
    skew = np.array([rotation[2, 1] - rotation[1, 2], rotation[0, 2] - rotation[2, 0], rotation[1, 0] - rotation[0, 1]])
    if angle < 1e-7:
        return skew / 2
    if np.pi - angle < 1e-5:
        values, vectors = np.linalg.eigh((rotation + rotation.T) / 2)
        return vectors[:, np.argmax(values)] * angle
    return skew * angle / (2 * np.sin(angle))


def _numbers(element, attribute, default):
    return np.fromstring(element.get(attribute, default) if element is not None else default, sep=' ')


@dataclass(frozen=True)
class Joint:
    name: str
    kind: str
    origin: np.ndarray
    axis: np.ndarray
    lower: float
    upper: float
    mimic: tuple | None = None

    def motion(self, position):
        result = np.eye(4)
        if self.kind in ('revolute', 'continuous'):
            x, y, z = self.axis
            skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
            result[:3, :3] = np.eye(3) + np.sin(position) * skew + (1 - np.cos(position)) * (skew @ skew)
        elif self.kind == 'prismatic':
            result[:3, 3] = self.axis * position
        return result


@dataclass
class IKResult:
    positions: np.ndarray
    reached: bool
    position_error_m: float
    orientation_error_rad: float
    iterations: int
    reason: str

    def as_dict(self):
        return dict(positions_rad=self.positions.tolist(), reached=self.reached,
                    position_error_m=self.position_error_m, orientation_error_rad=self.orientation_error_rad,
                    iterations=self.iterations, reason=self.reason)


class URDFChain:
    def __init__(self, joints, *, base, tip, source_sha256):
        self.joints = tuple(joints)
        self.base, self.tip, self.source_sha256 = base, tip, source_sha256
        self.active = tuple(joint for joint in self.joints if joint.kind != 'fixed' and joint.mimic is None)
        self.names = tuple(joint.name for joint in self.active)
        self.lower = np.array([joint.lower for joint in self.active])
        self.upper = np.array([joint.upper for joint in self.active])

    @classmethod
    def from_urdf(cls, path, base, tip):
        content = Path(path).read_bytes()
        robot = ET.fromstring(content)
        links = {link.get('name') for link in robot.findall('link')}
        if base not in links or tip not in links:
            raise ValueError('Requested base/tip link is absent from URDF')
        by_child = {}
        for node in robot.findall('joint'):
            child = node.find('child').get('link')
            if child in by_child:
                raise ValueError('URDF link has multiple parents')
            by_child[child] = node
        nodes, visited, link = [], set(), tip
        while link != base:
            if link in visited or link not in by_child:
                raise ValueError('No acyclic kinematic chain joins the requested frames')
            visited.add(link)
            joint = by_child[link]
            nodes.append(joint)
            link = joint.find('parent').get('link')
        parsed = []
        for node in reversed(nodes):
            kind = node.get('type')
            if kind not in ('fixed', 'revolute', 'continuous', 'prismatic'):
                raise ValueError('Unsupported URDF joint type: ' + str(kind))
            axis = _numbers(node.find('axis'), 'xyz', '1 0 0')
            if axis.shape != (3,) or (kind != 'fixed' and np.linalg.norm(axis) < 1e-12):
                raise ValueError('Invalid URDF joint axis')
            if kind != 'fixed':
                axis /= np.linalg.norm(axis)
            limit = node.find('limit')
            if kind in ('revolute', 'prismatic') and limit is None:
                raise ValueError('Bounded URDF joint has no limit')
            lower = float(limit.get('lower')) if limit is not None and kind != 'continuous' else -np.pi
            upper = float(limit.get('upper')) if limit is not None and kind != 'continuous' else np.pi
            if lower >= upper or not np.isfinite([lower, upper]).all():
                raise ValueError('Invalid URDF joint limits')
            origin = node.find('origin')
            pose = transform(_numbers(origin, 'xyz', '0 0 0'), _numbers(origin, 'rpy', '0 0 0'))
            mimic_node = node.find('mimic')
            mimic = None if mimic_node is None else (mimic_node.get('joint'), float(mimic_node.get('multiplier', '1')), float(mimic_node.get('offset', '0')))
            parsed.append(Joint(node.get('name'), kind, pose, axis, lower, upper, mimic))
        return cls(parsed, base=base, tip=tip, source_sha256=hashlib.sha256(content).hexdigest())

    def forward(self, positions, *, enforce_limits=True):
        q = np.asarray(positions, dtype=float)
        if q.shape != (len(self.active),) or not np.isfinite(q).all():
            raise ValueError('Joint vector must match the named active chain and contain finite radians/metres')
        if enforce_limits and (np.any(q < self.lower - 1e-9) or np.any(q > self.upper + 1e-9)):
            raise ValueError('Joint position outside URDF limits')
        values = dict(zip(self.names, q))
        pose = np.eye(4)
        for joint in self.joints:
            position = 0.0
            if joint.mimic is not None:
                name, multiplier, offset = joint.mimic
                if name not in values:
                    raise ValueError('Mimic driver not specified in this kinematic chain')
                position = values[name] * multiplier + offset
            elif joint.kind != 'fixed':
                position = values[joint.name]
            values[joint.name] = position
            pose = pose @ joint.origin @ joint.motion(position)
        return pose

    def inverse(self, target, initial=None, *, tolerance_m=0.001, orientation_weight=0.0,
                orientation_tolerance_rad=0.05, max_iterations=120, restarts=2, seed=0):
        goal = rigid_transform(target)
        if tolerance_m <= 0 or orientation_weight < 0 or orientation_tolerance_rad <= 0:
            raise ValueError('IK tolerances must be positive and weights nonnegative')
        q0 = np.clip(np.zeros(len(self.active)) if initial is None else np.asarray(initial, dtype=float), self.lower, self.upper)
        if q0.shape != self.lower.shape or not np.isfinite(q0).all():
            raise ValueError('Invalid IK initial configuration')
        rng, best, iterations = np.random.default_rng(seed), None, 0

        def residual(q):
            pose = self.forward(q)
            position = pose[:3, 3] - goal[:3, 3]
            orientation = rotation_vector(goal[:3, :3].T @ pose[:3, :3])
            return np.concatenate((position, orientation_weight * orientation)) if orientation_weight else position

        for attempt in range(restarts + 1):
            q = q0.copy() if attempt == 0 else rng.uniform(self.lower, self.upper)
            damping = 1e-5
            for _ in range(max_iterations):
                iterations += 1
                current = residual(q)
                score = float(current @ current)
                pose = self.forward(q)
                pe = float(np.linalg.norm(pose[:3, 3] - goal[:3, 3]))
                oe = float(np.linalg.norm(rotation_vector(goal[:3, :3].T @ pose[:3, :3])))
                reached = pe <= tolerance_m and (not orientation_weight or oe <= orientation_tolerance_rad)
                if best is None or score < best[0]:
                    best = (score, q.copy(), pe, oe)
                if reached:
                    return IKResult(q, True, pe, oe, iterations, 'within_declared_geometric_tolerances')
                columns = []
                for i in range(len(q)):
                    probe = q.copy()
                    delta = 1e-6 if q[i] + 1e-6 <= self.upper[i] else -1e-6
                    probe[i] += delta
                    columns.append((residual(probe) - current) / delta)
                jacobian = np.column_stack(columns)
                step = -np.linalg.solve(jacobian.T @ jacobian + damping * np.eye(len(q)), jacobian.T @ current)
                step *= min(1.0, 0.3 / max(float(np.linalg.norm(step)), 1e-12))
                improved = False
                for scale in (1.0, 0.5, 0.2, 0.05):
                    candidate = np.clip(q + scale * step, self.lower, self.upper)
                    error = residual(candidate)
                    if float(error @ error) < score - 1e-15:
                        q, improved = candidate, True
                        damping = max(1e-8, damping * 0.5)
                        break
                if not improved:
                    damping *= 10
                if damping > 100:
                    break
        return IKResult(best[1], False, best[2], best[3], iterations, 'unreachable_or_numerical_failure_not_a_physical_command')


@dataclass(frozen=True)
class ObjectRegistration:
    source_base_from_object: np.ndarray
    target_base_from_object: np.ndarray
    provenance: str
    calibration_ids: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'source_base_from_object', rigid_transform(self.source_base_from_object))
        object.__setattr__(self, 'target_base_from_object', rigid_transform(self.target_base_from_object))
        if self.provenance not in ('virtual_declared', 'measured'):
            raise ValueError('Declare measured or virtual registration provenance')
        if self.provenance == 'measured' and not self.calibration_ids:
            raise ValueError('Measured registration requires calibration artifact IDs')

    def transfer(self, source_base_from_tool):
        return self.target_base_from_object @ np.linalg.inv(self.source_base_from_object) @ rigid_transform(source_base_from_tool)


class ClutchRetargeter:
    """Recenter operator-relative geometry; no joint-index identity or output IO."""
    def __init__(self, source, target):
        self.source, self.target = source, target
        self.anchor = None

    def recenter(self, source_q, target_q):
        self.anchor = ObjectRegistration(self.source.forward(source_q), self.target.forward(target_q), 'virtual_declared')

    def update(self, source_q, target_q, *, clutch=False, **ik_options):
        if clutch:
            self.recenter(source_q, target_q)
            return None
        if self.anchor is None:
            raise ValueError('Recenter before retargeting')
        return self.target.inverse(self.anchor.transfer(self.source.forward(source_q)), target_q, **ik_options)


def map_aperture(value, *, source_closed, source_open, target_closed, target_open, calibration_id):
    """Map measured endpoints only; never infer LeRobot percent-to-URDF angles."""
    if not calibration_id or source_closed == source_open:
        raise ValueError('A separate, identified gripper calibration is required')
    points = np.asarray([value, source_closed, source_open, target_closed, target_open], dtype=float)
    if not np.isfinite(points).all():
        raise ValueError('Finite calibrated aperture values are required')
    fraction = (value - source_closed) / (source_open - source_closed)
    if not 0 <= fraction <= 1:
        raise ValueError('Aperture outside calibrated source range')
    return float(target_closed + fraction * (target_open - target_closed))
