"""SO101_URDF_FK_IK / OBJECT_RELATIVE_RETARGET / ENGINEERING_GLUE.

Measured SO-101 coordinates -> source FK -> relative clutch -> M3 IK -> six
integer target servo endpoints. This module has no device, ROS or network IO.
Registration declarations are not physical task acceptance or a collision model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from .kinematics import Joint, URDFChain, rigid_transform, rotation_vector

SOURCE_NAMES = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
SOURCE_UNITS = ["calibrated_normalized_-100_100"] * 5 + ["normalized_0_100"]
TARGET_NAMES = tuple("arm" + str(index) + "_Joint" for index in range(1, 6))
REQUIRED = ("registration_id", "registration_state", "source_calibration", "source_joint_points",
            "target_joint_points", "target_base_from_source_rotation", "orientation_mode", "gripper", "evidence")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def finite_vector(value, length):
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != length or any(type(item) not in (int, float, np.float64, np.float32) or not np.isfinite(item) for item in value):
        raise ValueError("Expected a finite named coordinate vector")
    return np.asarray(value, float)


def six_servo_goal(value):
    if not isinstance(value, list) or len(value) != 6 or any(type(item) is not int for item in value):
        raise ValueError("Original finite servo ABI requires exactly six integers")
    return list(value)


def affine_points(points):
    value = np.asarray(points, float)
    if value.ndim != 2 or value.shape[1] != 2 or len(value) < 2 or not np.isfinite(value).all() or np.ptp(value[:, 0]) <= 0:
        raise ValueError("Named joint registration needs at least two finite paired measurements")
    fit = np.linalg.lstsq(np.column_stack((value[:, 0], np.ones(len(value)))), value[:, 1], rcond=None)[0]
    if abs(fit[0]) < 1e-12:
        raise ValueError("Joint registration cannot have zero scale")
    return fit, float(np.max(np.abs(value[:, 0] * fit[0] + fit[1] - value[:, 1])))


class ArmOnlyGeometry:
    def __init__(self, source_urdf, target_urdf, registration_path=None):
        self.source = URDFChain.from_urdf(source_urdf, "base_link", "gripper_frame_link")
        self.target = URDFChain.from_urdf(target_urdf, "base_footprint", "Gripping")
        if self.source.names != SOURCE_NAMES[:5] or self.target.names != TARGET_NAMES:
            raise ValueError("URDF chains do not match the named SO-101 and M3 arm chains")
        self.profile, self.profile_sha256, self.missing, self.anchor = {}, None, [], None
        self.source_maps, self.target_maps = [], []
        if registration_path is None or not Path(registration_path).is_file():
            self.missing = list(REQUIRED)
            return
        path = Path(registration_path).resolve()
        if path.stat().st_size > 1024**2:
            raise ValueError("Registration metadata exceeds its bound")
        self.profile = json.loads(path.read_text())
        self.profile_sha256 = digest(path)
        self.missing = [name for name in REQUIRED if name not in self.profile]
        if self.profile.get("registration_state") != "measured":
            self.missing.append("registration_state=measured")
        expected_models = {"source": self.source.source_sha256, "target": self.target.source_sha256}
        if self.profile.get("model_sha256") != expected_models:
            self.missing.append("model_sha256_exact_source_and_target")
        if self.missing:
            return
        if self.profile["orientation_mode"] not in {"position_only", "pose"}:
            raise ValueError("Registration must declare position_only or pose orientation semantics")
        evidence = self.profile["evidence"]
        required_evidence = ("source_joint_zero_and_direction", "target_servo_zero_and_direction", "frame_axes", "gripper_aperture")
        for name in required_evidence:
            item = evidence.get(name, {})
            artifact = Path(item.get("path", ""))
            artifact = artifact if artifact.is_absolute() else path.parent / artifact
            if not item.get("calibration_id") or not artifact.is_file() or digest(artifact) != item.get("sha256"):
                self.missing.append("evidence." + name)
        calibration = self.profile["source_calibration"]
        artifact = Path(calibration.get("path", ""))
        artifact = artifact if artifact.is_absolute() else path.parent / artifact
        if not artifact.is_file() or digest(artifact) != calibration.get("sha256"):
            self.missing.append("source_calibration.original_file_sha256")
        if self.missing:
            return
        self.fit_residuals = {}
        for label, names, maps in (("source", self.source.names, self.source_maps), ("target", self.target.names, self.target_maps)):
            points = self.profile[label + "_joint_points"]
            if set(points) != set(names):
                raise ValueError("Joint calibration must use exactly the named URDF chain")
            for name in names:
                fit, residual = affine_points(points[name])
                maps.append(fit)
                self.fit_residuals[label + ":" + name] = residual
        rotation = np.asarray(self.profile["target_base_from_source_rotation"], float)
        pose = np.eye(4)
        if rotation.shape != (3, 3):
            raise ValueError("Measured frame rotation must be3x3")
        pose[:3, :3] = rotation
        self.rotation = rigid_transform(pose)[:3, :3]
        gripper = self.profile["gripper"]
        trigger = finite_vector(gripper["source_trigger_closed_open"], 2)
        apertures = finite_vector(gripper["target_aperture_closed_open_m"], 2)
        curve = np.asarray(gripper["target_aperture_m_to_servo_deg"], float)
        if trigger[0] == trigger[1] or apertures[0] >= apertures[1] or curve.ndim != 2 or curve.shape[1] != 2 or len(curve) < 2 or not np.isfinite(curve).all() or np.any(np.diff(curve[:, 0]) <= 0):
            raise ValueError("Separate trigger/aperture/servo measurements are required")
        if apertures[0] < curve[0, 0] or apertures[1] > curve[-1, 0]:
            raise ValueError("Desired aperture is outside measured target curve")
        self.trigger, self.apertures, self.aperture_curve = trigger, apertures, curve

    def status(self):
        return {"state": "UNREGISTERED" if self.missing else "REGISTERED_DECLARATION",
                "registration_id": self.profile.get("registration_id"), "registration_sha256": self.profile_sha256,
                "missing": list(self.missing), "physical_acceptance_confirmed": False,
                "affine_fit_residuals": getattr(self, "fit_residuals", {}),
                "source_model_sha256": self.source.source_sha256, "target_model_sha256": self.target.source_sha256,
                "controller_configuration_writes": 0, "chassis_requests": 0}

    def _registered(self):
        if self.missing:
            raise ValueError("UNREGISTERED: " + ", ".join(self.missing))

    def source_positions(self, measurement):
        self._registered()
        if measurement.get("role") != "leader" or measurement.get("joint_names") != list(SOURCE_NAMES) or measurement.get("units") != SOURCE_UNITS:
            raise ValueError("Measured leader names/units/role differ from registration")
        if measurement.get("calibration_sha256") != self.profile["source_calibration"]["sha256"] or measurement.get("measured") is not True:
            raise ValueError("Leader measurement must match the original calibration SHA")
        if type(measurement.get("monotonic_ns")) is not int or measurement["monotonic_ns"] < 0 or not isinstance(measurement.get("clock_domain"), str) or not measurement["clock_domain"] or not isinstance(measurement.get("control_epoch"), str) or not measurement["control_epoch"]:
            raise ValueError("Measured leader needs its original source clock/session/timestamp")
        values = finite_vector(measurement.get("positions"), 6)
        q = np.asarray([fit[0] * value + fit[1] for fit, value in zip(self.source_maps, values[:5])])
        self.source.forward(q)
        return q, values[5]

    def target_positions(self, servo):
        self._registered()
        values = finite_vector(servo, 6)
        q = np.asarray([(value - fit[1]) / fit[0] for fit, value in zip(self.target_maps, values[:5])])
        self.target.forward(q)
        return q

    def recenter(self, measurement, target_servo):
        source_q, _ = self.source_positions(measurement)
        target_q = self.target_positions(target_servo)
        self.anchor = (self.source.forward(source_q), self.target.forward(target_q))
        return {"recentered": True, "motion_requested": False, "target_reference_basis": "reported_command_estimate_not_measured"}

    def plan(self, measurement, target_servo):
        source_q, trigger = self.source_positions(measurement)
        target_q = self.target_positions(target_servo)
        if self.anchor is None:
            raise ValueError("Explicit clutch/recenter is required")
        source_pose = self.source.forward(source_q)
        source_anchor, target_anchor = self.anchor
        goal = target_anchor.copy()
        goal[:3, 3] += self.rotation @ (source_pose[:3, 3] - source_anchor[:3, 3])
        delta_rotation = source_pose[:3, :3] @ source_anchor[:3, :3].T
        goal[:3, :3] = self.rotation @ delta_rotation @ self.rotation.T @ target_anchor[:3, :3]
        mode = self.profile.get("orientation_mode")
        if mode not in {"position_only", "pose"}:
            raise ValueError("Registration must declare position_only or pose orientation semantics")
        solve_chain, initial = self.target, target_q
        if mode == "position_only":
            # Match the old M3 position-only IK: retain roll instead of exploiting CAD eccentricity.
            joints = [Joint(j.name, "fixed", j.origin @ j.motion(target_q[4]), j.axis, j.lower, j.upper)
                      if j.name == TARGET_NAMES[4] else j for j in self.target.joints]
            solve_chain = URDFChain(joints, base=self.target.base, tip=self.target.tip, source_sha256=self.target.source_sha256)
            initial = target_q[:4]
        solution = solve_chain.inverse(goal, initial, orientation_weight=.03 if mode == "pose" else 0., restarts=0)
        if not solution.reached:
            return {"state": "UNREACHABLE", "ik": solution.as_dict(), "requested_goal": None, "motion_requested": False}
        solved_q = np.r_[solution.positions, target_q[4]] if mode == "position_only" else solution.positions
        servo = [int(round(fit[0] * q + fit[1])) for fit, q in zip(self.target_maps, solved_q)]
        fraction = (trigger - self.trigger[0]) / (self.trigger[1] - self.trigger[0])
        if not 0 <= fraction <= 1:
            raise ValueError("Source trigger is outside its registered gesture range")
        aperture = self.apertures[0] + fraction * (self.apertures[1] - self.apertures[0])
        servo.append(int(round(np.interp(aperture, self.aperture_curve[:, 0], self.aperture_curve[:, 1]))))
        quantized = self.target.forward(self.target_positions(servo))
        return {"state": "GEOMETRIC_PROPOSAL", "requested_goal": six_servo_goal(servo), "desired_pose": goal.tolist(),
                "ik": {**solution.as_dict(), "positions_rad": solved_q.tolist()}, "quantized_position_error_m": float(np.linalg.norm(quantized[:3, 3] - goal[:3, 3])),
                "quantized_orientation_error_rad": float(np.linalg.norm(rotation_vector(goal[:3, :3].T @ quantized[:3, :3]))),
                "orientation_mode": mode, "gripper": {"source_trigger": float(trigger), "requested_aperture_m": float(aperture), "target_servo_deg": servo[5]},
                "source_measurement": {key: measurement.get(key) for key in ("monotonic_ns", "clock_domain", "control_epoch", "calibration_sha256")},
                "motion_requested": False, "collision_checked_by_this_module": False, "chassis_requests": 0}
