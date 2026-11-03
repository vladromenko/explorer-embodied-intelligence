"""Conservative geometry helpers. No depth, dimensions or FK are inferred by SAM."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np


def unknown_geometry(reason: str = "Нет проверенной актуальной геометрической калибровки") -> dict:
    return {
        "valid": False, "reason": reason, "xyz_m": None, "size_m": None,
        "depth_m": None, "grasp": None, "surface": "unknown",
        "uncertainty_m": None, "provenance": "unobserved",
    }


def calibration_fingerprint(calibration: dict) -> str:
    return hashlib.sha256(json.dumps(calibration, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def check_applicability(calibration: dict | None, camera: dict) -> tuple[bool, str]:
    """Require explicit independent verification after cameras or joint offsets moved.

    The mount fingerprint is an operator-confirmed installation identifier, not a
    software claim that an unchanged USB serial proves unchanged extrinsics.
    """
    if not calibration:
        return False, "Нет калибровки"
    required = ("verified_current", "verification", "camera_serial", "resolution", "intrinsics", "distortion", "camera_to_world", "mount_fingerprint", "joint_calibration_sha256")
    if any(k not in calibration for k in required):
        return False, "Неполные provenance/verification калибровки"
    if calibration["verified_current"] is not True:
        return False, "Калибровка не подтверждена для текущей установки"
    verification = calibration["verification"]
    if not isinstance(verification, dict) or not all(verification.get(k) for k in ("by", "at", "method")):
        return False, "Отсутствует независимая проверка применимости"
    for key in ("camera_serial", "mount_fingerprint", "joint_calibration_sha256"):
        if not camera.get(key) or camera[key] != calibration[key]:
            return False, f"Не совпадает {key}"
    resolution = camera.get("resolution") or [camera.get("width"), camera.get("height")]
    if list(resolution) != list(calibration["resolution"]):
        return False, "Не совпадает разрешение"
    try:
        K = np.asarray(calibration["intrinsics"], dtype=float)
        T = np.asarray(calibration["camera_to_world"], dtype=float)
        d = np.asarray(calibration["distortion"], dtype=float)
        if K.shape != (3, 3) or T.shape != (4, 4) or d.ndim != 1 or len(d) not in (0, 4, 5):
            return False, "Неподдерживаемый формат K/T/distortion"
        if not np.isfinite(K).all() or not np.isfinite(T).all() or not np.isfinite(d).all() or min(K[0, 0], K[1, 1]) <= 0:
            return False, "Некорректные числа в калибровке"
        R = T[:3, :3]
        if not np.allclose(R.T @ R, np.eye(3), atol=1e-4) or not np.isclose(np.linalg.det(R), 1., atol=1e-4) or not np.allclose(T[3], [0, 0, 0, 1]):
            return False, "Некорректная rigid transform"
    except (ValueError, TypeError):
        return False, "Некорректный формат калибровки"
    return True, "Подтверждена оператором; точность зависит от указанной проверки"


def _undistorted_ray(pixel_xy, calibration):
    K = np.asarray(calibration["intrinsics"], dtype=float)
    x0, y0, _ = np.linalg.solve(K, [float(pixel_xy[0]), float(pixel_xy[1]), 1.])
    x, y = x0, y0
    ds = list(calibration["distortion"]) + [0.] * 5
    k1, k2, p1, p2, k3 = ds[:5]
    for _ in range(12):
        r2 = x*x + y*y
        radial = 1. + k1*r2 + k2*r2*r2 + k3*r2*r2*r2
        if abs(radial) < 1e-10:
            raise ValueError("Невозможно обратить distortion")
        x = (x0 - (2*p1*x*y + p2*(r2 + 2*x*x))) / radial
        y = (y0 - (p1*(r2 + 2*y*y) + 2*p2*x*y)) / radial
    ray = np.array([x, y, 1.])
    return ray / np.linalg.norm(ray)


def estimate_point_on_plane(pixel_xy, calibration: dict | None, camera: dict, *, surface: str = "table", pixel_sigma_px: float = 2.) -> dict:
    """Intersect a calibrated ray with an explicitly measured plane.

    This is a conditional plane estimate, never a depth measurement. A table plane
    cannot locate the upper surface of an unknown-height cube or any box.
    """
    valid, reason = check_applicability(calibration, camera)
    if not valid:
        return unknown_geometry(reason)
    plane = calibration.get("planes", {}).get(surface)
    if surface not in ("table", "cube_top", "box_surface") or not plane:
        return unknown_geometry("Поверхность не имеет независимо измеренной плоскости")
    if plane.get("measured") is not True or not plane.get("provenance"):
        return unknown_geometry("Плоскость не измерена; нельзя выводить высоту из маски")
    try:
        n = np.asarray(plane["normal_world"], dtype=float)
        offset = float(plane["offset_m"])
        plane_sigma = float(plane["sigma_m"])
        calibration_sigma = float(calibration["position_sigma_m"])
        if n.shape != (3,) or not np.isfinite(n).all() or not np.isfinite([offset, plane_sigma, calibration_sigma, pixel_sigma_px]).all() or min(plane_sigma, calibration_sigma, pixel_sigma_px) < 0:
            raise ValueError("Некорректная uncertainty/plane")
        n_norm = np.linalg.norm(n)
        if n_norm < 1e-8:
            raise ValueError("Нулевая нормаль")
        n, offset = n/n_norm, offset/n_norm
        T = np.asarray(calibration["camera_to_world"], dtype=float)
        origin = T[:3, 3]
        def intersect(xy):
            ray = T[:3, :3] @ _undistorted_ray(xy, calibration)
            denom = float(n @ ray)
            if abs(denom) < 1e-6:
                raise ValueError("Луч почти параллелен плоскости")
            distance = -(float(n @ origin) + offset) / denom
            if distance <= 0:
                raise ValueError("Пересечение позади камеры")
            return origin + distance*ray
        point = intersect(pixel_xy)
        jac = np.stack([(intersect([pixel_xy[0]+.5, pixel_xy[1]]) - intersect([pixel_xy[0]-.5, pixel_xy[1]])), (intersect([pixel_xy[0], pixel_xy[1]+.5]) - intersect([pixel_xy[0], pixel_xy[1]-.5]))], axis=1)
        covariance = (jac @ jac.T) * float(pixel_sigma_px)**2 + np.eye(3)*(plane_sigma**2 + calibration_sigma**2)
    except (ValueError, KeyError, TypeError, np.linalg.LinAlgError) as exc:
        return unknown_geometry(str(exc))
    return {"valid": True, "xyz_m": point.tolist(), "size_m": None, "depth_m": None,
            "surface": surface, "grasp": None, "covariance_m2": covariance.tolist(),
            "uncertainty_m": float(np.sqrt(np.trace(covariance))),
            "method": "calibrated_ray_measured_plane_conditional_estimate",
            "provenance": {"calibration_sha256": calibration_fingerprint(calibration), "plane": plane["provenance"]},
            "limitations": "Не depth sensor. Центр маски не гарантирует физическую точку объекта."}


def forward_kinematics(joints, kinematics: dict | None, *, timestamp_ns: int, joint_calibration_sha256: str | None) -> dict:
    """FK only from explicitly calibrated DH parameters; no bundled guessed SO101 links."""
    if not kinematics or not kinematics.get("verified_current") or not joint_calibration_sha256 or kinematics.get("joint_calibration_sha256") != joint_calibration_sha256:
        return {"valid": False, "tcp_m": None, "reason": "Нет проверенной модели FK и совпадающих offsets", "timestamp_ns": timestamp_ns}
    if kinematics.get("joint_units") != "rad" or len(joints) != len(kinematics.get("dh", [])):
        return {"valid": False, "tcp_m": None, "reason": "FK требует радианы и полную таблицу DH", "timestamp_ns": timestamp_ns}
    T = np.eye(4)
    try:
        for q, link in zip(joints, kinematics["dh"]):
            a, alpha, d = (float(link[k]) for k in ("a_m", "alpha_rad", "d_m"))
            theta = float(q) + float(link["theta_offset_rad"])
            c, s, ca, sa = np.cos(theta), np.sin(theta), np.cos(alpha), np.sin(alpha)
            T = T @ np.array([[c, -s*ca, s*sa, a*c], [s, c*ca, -c*sa, a*s], [0, sa, ca, d], [0, 0, 0, 1]])
        tcp = np.asarray(kinematics["tcp_transform"], dtype=float)
        if tcp.shape != (4,4) or not np.isfinite(tcp).all() or not np.isfinite(T).all():
            raise ValueError("invalid FK transform")
        T = T @ tcp
        sigma = float(kinematics["tcp_sigma_m"])
        if not np.isfinite(sigma) or sigma < 0:
            raise ValueError("invalid uncertainty")
    except (KeyError, ValueError, TypeError):
        return {"valid": False, "tcp_m": None, "reason": "Неполная/некорректная FK модель", "timestamp_ns": timestamp_ns}
    return {"valid": True, "tcp_m": T[:3,3].tolist(), "transform": T.tolist(), "uncertainty_m": sigma,
            "timestamp_ns": timestamp_ns, "method": "DH_FK_from_measured_joints", "provenance": calibration_fingerprint(kinematics), "grasp": None}
