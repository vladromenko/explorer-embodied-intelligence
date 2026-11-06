"""Versioned, causal SAM observation adapter shared by train and inference.

26 SAM/target numbers fit SmolVLA's existing padded 32-dimensional state
projection alongside the six measured joints. No new untrained JSON heads.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FEATURE_SCHEMA = "so101.smolvla.objects.v1"
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
CAMERAS = ("top", "wrist")
CATEGORIES = ("cube", "box")
SLOT_FIELDS = ("valid", "center_x_fraction", "center_y_fraction", "width_fraction", "height_fraction", "sam_score")


@dataclass(frozen=True)
class FeatureContract:
    schema_version: str = FEATURE_SCHEMA
    mode: str = "sam_objects"
    camera_mode: str = "two_camera"
    annotation_version: str = "sam3-v1"
    target_category: str = "cube"
    target_color: str | None = None
    max_camera_skew_ms: float = 100.0
    max_state_age_ms: float = 100.0

    def __post_init__(self):
        if self.schema_version != FEATURE_SCHEMA:
            raise ValueError("Несовместимая версия объектного входа")
        if self.mode not in ("sam_objects", "rgb_state"):
            raise ValueError("Режим должен быть sam_objects или rgb_state")
        if self.camera_mode not in ("two_camera", "wrist_only_experimental"):
            raise ValueError("Неизвестный режим камер")
        if self.target_category not in CATEGORIES:
            raise ValueError("Цель должна быть cube или box")

    @property
    def cameras(self):
        return CAMERAS if self.camera_mode == "two_camera" else ("wrist",)

    @property
    def state_dim(self):
        return 32 if self.mode == "sam_objects" else 6

    def to_dict(self):
        data = asdict(self)
        data["state_layout"] = list(JOINTS) + ([f"{c}.{k}.{f}" for c in CAMERAS for k in CATEGORIES for f in SLOT_FIELDS] + ["target.cube", "target.box"] if self.mode == "sam_objects" else [])
        data["id_handling"] = "tracking ids excluded; categorical slots only"
        data["selection"] = "highest score per camera/category, verified target_color filter for cube"
        data["causality"] = "current raw frame only; no future, action, success, phase or done input"
        return data

    @classmethod
    def from_dict(cls, data):
        normalized = dict(data)
        normalized["mode"] = {"baseline": "rgb_state"}.get(normalized.get("mode"), normalized.get("mode", "sam_objects"))
        normalized["camera_mode"] = {"dual": "two_camera", "wrist_only": "wrist_only_experimental"}.get(normalized.get("camera_mode"), normalized.get("camera_mode", "two_camera"))
        return cls(**{k: v for k, v in normalized.items() if k in cls.__dataclass_fields__})

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


def _finite_six(values: Any, label: str) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 6:
        raise ValueError(f"{label}: требуются 6 измеренных величин")
    result = [float(v) for v in values]
    if not all(math.isfinite(v) for v in result):
        raise ValueError(f"{label}: missing/nonfinite values")
    return result


def joint_state(frame: dict) -> list[float]:
    return _finite_six(frame.get("observation.state", frame.get("observation", {}).get("state")), "observation.state")


def action_state(frame: dict) -> list[float]:
    return _finite_six(frame.get("action"), "action")


def validate_raw_frame(frame: dict, contract: FeatureContract, *, training: bool = False, allow_test: bool = False):
    joint_state(frame)
    if frame.get("state_valid", True) is not True and not allow_test:
        raise ValueError("Measured state недействительно")
    now = int(frame["timestamp_ns"])
    state_time = int(frame["state_timestamp_ns"])
    if state_time > now or now - state_time > contract.max_state_age_ms * 1e6:
        raise ValueError("Состояние из будущего или устарело относительно observation")
    times = []
    for cam in contract.cameras:
        item = frame.get("cameras", {}).get(cam, {})
        if not item.get("valid", False):
            raise ValueError(f"Нет валидного raw кадра {cam}")
        ts = int(item["timestamp_ns"])
        if ts > now:
            raise ValueError(f"Кадр {cam} из будущего")
        if now - ts > contract.max_camera_skew_ms * 1e6:
            raise ValueError(f"Устаревший raw кадр {cam}")
        times.append(ts)
        if not item.get("sha256"):
            raise ValueError(f"Нет raw hash {cam}")
    if times and max(times) - min(times) > contract.max_camera_skew_ms * 1e6:
        raise ValueError("Рассинхронизация камер")
    if training:
        action_state(frame)
        action_time = int(frame["action_timestamp_ns"])
        if action_time < state_time:
            raise ValueError("Команда действия предшествует измерению state")
        if frame.get("action_valid", True) is not True and not allow_test:
            raise ValueError("Команда action не подтверждена")


def _select(objects: list[dict], category: str, contract: FeatureContract):
    candidates = [o for o in objects if o.get("category") == category and not o.get("category_ambiguous",False)]
    if contract.target_color and category == "cube":
        candidates = [o for o in candidates if o.get("color", {}).get("valid") and o["color"].get("label") == contract.target_color]
    candidates = [o for o in candidates if math.isfinite(float(o.get("score", -1))) and float(o.get("score", -1)) >= 0]
    return sorted(candidates, key=lambda o: (-float(o["score"]), tuple(o.get("bbox_xyxy", ()))))[0] if candidates else None


def object_state(frame: dict, annotation: dict | None, contract: FeatureContract, *, allow_test: bool = False) -> list[float]:
    """Only explicitly whitelisted current observation values enter the network."""
    values = joint_state(frame)
    if contract.mode == "rgb_state":
        return values
    if annotation is None:
        raise ValueError("SAM annotations missing: train/inference requires exact current frame")
    if str(annotation.get("frame_id")) != str(frame["frame_id"]) or int(annotation["timestamp_ns"]) != int(frame["timestamp_ns"]):
        raise ValueError("SAM annotation frame/time mismatch")
    backend = annotation.get("provenance", {}).get("backend", "unknown")
    if backend != "sam3" and not allow_test:
        raise ValueError(f"Object features require real SAM3, got {backend}")
    for cam in CAMERAS:
        if cam not in contract.cameras:
            values.extend([0.0] * 12)
            continue
        raw = frame["cameras"][cam]
        ann = annotation.get("cameras", {}).get(cam)
        if not ann or not ann.get("valid"):
            raise ValueError(f"SAM annotation missing/invalid: {cam}")
        if int(ann["timestamp_ns"]) != int(raw["timestamp_ns"]) or ann.get("raw_sha256") != raw["sha256"]:
            raise ValueError(f"Stale SAM annotation {cam}: raw timestamp/hash differs")
        width, height = float(ann["width"]), float(ann["height"])
        if width <= 0 or height <= 0:
            raise ValueError("Invalid annotation dimensions")
        for category in CATEGORIES:
            selected = _select(ann.get("objects", []), category, contract)
            if selected is None:
                values.extend([0.0] * 6)
                continue
            x0, y0, x1, y1 = map(float, selected["bbox_xyxy"])
            cx, cy = map(float, selected["center_xy"])
            if not all(math.isfinite(v) for v in (x0,y0,x1,y1,cx,cy)) or x1<=x0 or y1<=y0 or not (0<=cx<width and 0<=cy<height):
                raise ValueError("Invalid SAM bbox/center")
            # Real SAM box regression can extend a fraction of a pixel beyond the image.
            # Preserve the original sidecar; clip only the numerical policy input.
            x0,y0,x1,y1=max(0.,x0),max(0.,y0),min(width,x1),min(height,y1)
            if x1<=x0 or y1<=y0:raise ValueError("SAM box has no intersection with image")
            score = float(selected["score"])
            if not 0 <= score <= 1:
                raise ValueError("Invalid SAM score")
            values.extend([1.0, cx / width, cy / height, (x1-x0) / width, (y1-y0) / height, score])
    values.extend([float(contract.target_category == c) for c in CATEGORIES])
    if len(values) != 32 or not all(math.isfinite(v) for v in values):
        raise ValueError("Object state contract violation")
    return values


def safe_child(root: Path, relative: str) -> Path:
    root = root.resolve()
    child = (root / relative).resolve()
    if child != root and root not in child.parents:
        raise ValueError("Path escapes dataset/checkpoint")
    return child


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
