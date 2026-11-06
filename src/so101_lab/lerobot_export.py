"""Lossless, atomic raw -> official installed LeRobot 0.6.1 Dataset export.

Images are embedded losslessly into image-backed Parquet through the public
LeRobotDataset.create/save_episode/finalize API. Measured relative timestamps
are passed via save_episode(episode_data=...), because add_frame intentionally
constructs an ideal frame_index/fps clock. Exact monotonic int64 ns and source
validity remain regular features and an independently hashed frame sidecar.
This module never opens hardware, loads a model, uses CUDA or publishes to HF.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import statistics
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .features import JOINTS, action_state, joint_state, safe_child, sha256_file
from .resources import ROOT, assert_disk_space, dataset_lease

EXPORT_SCHEMA = "so101.lerobot_export.v1"
SUPPORTED_LEROBOT = "0.6.1"
CAMERAS = ("top", "wrist")


class ExportError(ValueError):
    """An invalid raw snapshot must never become a completed LeRobot dataset."""


class ExportStopped(RuntimeError):
    """A requested stop preserves only an explicitly incomplete export."""


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ExportError(f"Ожидался JSON object: {path}")
    return value


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _ns(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**63:
        raise ExportError(f"{label}: нужны точные неотрицательные int64 monotonic ns")
    return value


def _nominal_fps(meta: dict, episodes: list[dict], override: Any = None) -> int:
    values = [ep["metadata"].get("fps_requested", meta.get("settings", {}).get("fps")) for ep in episodes]
    present = [float(value) for value in values if value is not None]
    if not present and override is None:
        raise ExportError("Неизвестен requested FPS; укажите fps с явным происхождением")
    chosen = float(override) if override is not None else present[0]
    if not math.isfinite(chosen) or not 1 <= chosen <= 120 or not chosen.is_integer():
        raise ExportError("Экспорт LeRobot требует единый целый nominal/requested FPS (1–120); raw не пересэмплируется")
    if any(not math.isfinite(value) or abs(value-chosen) > 1e-9 for value in present):
        raise ExportError("Сессии имеют разный requested FPS; экспортируйте их в отдельные датасеты")
    return int(chosen)


def _timing_summary(rows: list[dict], fps: int) -> dict:
    times = [row["timestamp_ns"] for row in rows]
    intervals = [(b-a)/1e9 for a, b in zip(times, times[1:])]
    measured = (len(times)-1)/((times[-1]-times[0])/1e9) if len(times) > 1 else None
    jitters = [abs(interval-1/fps) for interval in intervals]
    return {
        "nominal_fps": fps,
        "observed_mean_fps": measured,
        "interval_count": len(intervals),
        "interval_min_ms": min(intervals)*1000 if intervals else None,
        "interval_max_ms": max(intervals)*1000 if intervals else None,
        "interval_median_ms": statistics.median(intervals)*1000 if intervals else None,
        "max_interval_deviation_from_nominal_ms": max(jitters)*1000 if jitters else None,
        "max_nominal_clock_drift_ms": max(abs((stamp-times[0])/1e9-i/fps) for i, stamp in enumerate(times))*1000,
        "timestamps_resampled": False,
        "timestamp_semantics": "measured_observation_monotonic_ns_relative_to_episode_first_frame",
        "single_frame_rate_unknown": len(times) == 1,
        "camera_reused_frames": {cam: sum(int(bool(row["cameras"][cam].get("reused", False))) for row in rows) for cam in CAMERAS},
        "camera_reported_drops": {cam: sum(int(row["cameras"][cam].get("dropped_since_previous", 0)) for row in rows) for cam in CAMERAS},
        "max_camera_age_ms": {cam: max((row["timestamp_ns"]-row["cameras"][cam]["timestamp_ns"])/1e6 for row in rows) for cam in CAMERAS},
        "max_camera_skew_ms": max(abs(row["cameras"]["top"]["timestamp_ns"]-row["cameras"]["wrist"]["timestamp_ns"])/1e6 for row in rows),
        "max_state_to_action_ms": max((row["action_timestamp_ns"]-row["state_timestamp_ns"])/1e6 for row in rows),
        "fixed_rate_delta_query_note": "LeRobot delta_timestamps uses nominal FPS frame offsets; actual raw timing is retained and must be checked before interpreting chunks as uniform physical time",
    }


@dataclass
class _Inspection:
    path: Path
    metadata: dict
    episodes: list[dict]
    source_hashes: dict[str, str]
    shapes: dict[str, tuple[int, int, int]]
    fps: int
    total_frames: int
    source_bytes: int
    source_digest: str
    warnings: list[str]

    def report(self) -> dict:
        return {
            "status": "valid",
            "schema_version": EXPORT_SCHEMA,
            "source_path": str(self.path),
            "source": self.metadata["source"],
            "test_only": self.metadata["source"] != "hardware",
            "successful_robot_demonstrations_verified": False,
            "dataset_sha256": self.source_digest,
            "nominal_fps": self.fps,
            "total_frames": self.total_frames,
            "total_episodes": len(self.episodes),
            "image_shapes_hwc": {cam: list(shape) for cam, shape in self.shapes.items()},
            "source_bytes": self.source_bytes,
            "episodes": [{"source_episode_id": ep["path"].name, "session_id": ep["metadata"].get("session_id"),
                          "frames": len(ep["rows"]), "task": ep["task"], "timing": ep["timing"]} for ep in self.episodes],
            "warnings": self.warnings,
        }


def _inspect_raw(dataset_path: Path, *, allow_test_data: bool, fps: Any = None, stop=None, progress=None) -> _Inspection:
    from PIL import Image

    path = Path(dataset_path).resolve()
    if not path.is_relative_to(ROOT):
        raise ExportError("Raw dataset должен находиться внутри независимой лаборатории")
    meta_path = path / "dataset.json"
    meta = _read_json(meta_path)
    source = meta.get("source")
    if source not in ("hardware", "synthetic_test"):
        raise ExportError("Неизвестный источник raw dataset")
    if source != "hardware" and not allow_test_data:
        raise ExportError("Тестовый источник исключён: allow_test_data=True разрешён только для технической проверки")
    partials = list((path / ".partial").glob("*.partial"))
    if partials:
        raise ExportError("Есть незавершённая запись .partial; сначала Stop/восстановление, затем Finalize")
    episode_paths = sorted(p.parent for p in (path / "episodes").glob("*/episode.json"))
    if not episode_paths:
        raise ExportError("Нет завершённых raw эпизодов")
    hashes = {"dataset.json": sha256_file(meta_path)}
    shapes = {}
    episodes = []
    source_bytes = meta_path.stat().st_size
    total_frames = 0
    for episode_path in episode_paths:
        if stop and stop():
            raise ExportStopped("Экспорт остановлен во время raw validation")
        info = _read_json(episode_path / "episode.json")
        if info.get("status") != "complete":
            raise ExportError(f"Незавершённый эпизод нельзя экспортировать: {episode_path.name}")
        if info.get("source", source) != source:
            raise ExportError("Смешаны hardware и test episodes")
        frame_file = episode_path / "frames.jsonl"
        raw_lines = frame_file.read_text().splitlines()
        rows = [json.loads(line) for line in raw_lines if line.strip()]
        if not rows:
            raise ExportError(f"Пустой эпизод: {episode_path.name}")
        frame_hash = sha256_file(frame_file)
        if info.get("frames_sha256") and info["frames_sha256"] != frame_hash:
            raise ExportError(f"frames_sha256 не совпадает: {episode_path.name}")
        if info.get("frames") is not None and int(info["frames"]) != len(rows):
            raise ExportError(f"Число raw frames не совпадает с metadata: {episode_path.name}")
        task = info.get("task", meta.get("task"))
        if not isinstance(task, str) or not task.strip():
            raise ExportError(f"Нет текста задачи: {episode_path.name}")
        seen_ids = set()
        previous_time = None
        previous_action_time = None
        for row in rows:
            if stop and stop():
                raise ExportStopped("Экспорт остановлен во время проверки raw кадров")
            frame_id = str(row["frame_id"])
            if frame_id in seen_ids:
                raise ExportError(f"Duplicate frame_id: {episode_path.name}/{frame_id}")
            seen_ids.add(frame_id)
            now = _ns(row["timestamp_ns"], "timestamp_ns")
            observation = _ns(row.get("observation_timestamp_ns", now), "observation_timestamp_ns")
            state_time = _ns(row["state_timestamp_ns"], "state_timestamp_ns")
            action_time = _ns(row["action_timestamp_ns"], "action_timestamp_ns")
            if observation != now or state_time > observation or action_time < observation:
                raise ExportError("Неверная причинная связь state/observation/action timestamps")
            if previous_time is not None and now <= previous_time:
                raise ExportError("Немонoтонное время observation")
            if previous_action_time is not None and action_time <= previous_action_time:
                raise ExportError("Немонoтонное время отправленных action")
            if previous_action_time is not None and observation < previous_action_time:
                raise ExportError("Следующее observation предшествует завершению предыдущей отправки action")
            previous_time, previous_action_time = now, action_time
            joint_state(row)
            action_state(row)
            if row.get("source", source) != source:
                raise ExportError("Источник кадра не совпадает с dataset source")
            if source == "hardware" and (row.get("state_valid") is not True or row.get("action_valid") is not True):
                raise ExportError("Hardware frame содержит unmeasured/unsent state/action")
            for cam in CAMERAS:
                raw = row.get("cameras", {}).get(cam)
                if not isinstance(raw, dict) or raw.get("valid") is not True:
                    raise ExportError(f"Отсутствует валидный кадр {cam}")
                camera_time = _ns(raw["timestamp_ns"], f"{cam}.timestamp_ns")
                if camera_time > observation:
                    raise ExportError(f"Кадр {cam} из будущего")
                image_path = safe_child(episode_path, raw["path"])
                if image_path.suffix.lower() != ".png":
                    raise ExportError("Экспорт raw требует lossless PNG RGB")
                image_hash = sha256_file(image_path)
                if raw.get("sha256") != image_hash:
                    raise ExportError(f"Raw image hash mismatch: {episode_path.name}/{cam}/{frame_id}")
                with Image.open(image_path) as image:
                    if image.mode != "RGB" or image.format != "PNG":
                        raise ExportError("Ожидался исходный PNG RGB8 без преобразования цвета")
                    image.load()
                    shape = (image.height, image.width, 3)
                if cam in shapes and shape != shapes[cam]:
                    raise ExportError(f"Разрешение {cam} изменилось между кадрами")
                shapes[cam] = shape
                key = image_path.relative_to(path).as_posix()
                hashes[key] = image_hash
                source_bytes += image_path.stat().st_size
        for file_path in (episode_path / "episode.json", frame_file):
            hashes[file_path.relative_to(path).as_posix()] = sha256_file(file_path)
            source_bytes += file_path.stat().st_size
        total_frames += len(rows)
        episodes.append({"path": episode_path, "metadata": info, "rows": rows, "task": task})
        if progress:
            progress({"state": "validating_raw", "episodes": len(episodes), "frames": total_frames})
    nominal = _nominal_fps(meta, episodes, fps)
    warnings = []
    for episode in episodes:
        episode["timing"] = _timing_summary(episode["rows"], nominal)
        drift = episode["timing"]["max_nominal_clock_drift_ms"]
        if drift > 1000/nominal:
            warnings.append(f"{episode['path'].name}: наблюдаемый clock drift {drift:.2f} ms превышает период nominal FPS; проверьте качество перед train")
    if source != "hardware":
        warnings.append("ТЕХНИЧЕСКИЙ источник: joints/actions не являются измерениями/отправленными командами, успех робота не проверялся")
    return _Inspection(path, meta, episodes, hashes, shapes, nominal, total_frames, source_bytes, _json_digest(hashes), warnings)


def validate_raw_dataset(dataset_path: Path, *, allow_test_data=False, fps=None, progress=None, stop=None) -> dict:
    """Read-only integrity/timing check of a frozen raw dataset; no hardware opens."""
    with dataset_lease(dataset_path):
        return _inspect_raw(dataset_path, allow_test_data=bool(allow_test_data), fps=fps, progress=progress, stop=stop).report()


def _features(inspection: _Inspection) -> dict:
    features = {
        "observation.state": {"dtype": "float32", "shape": (6,), "names": list(JOINTS)},
        "action": {"dtype": "float32", "shape": (6,), "names": list(JOINTS)},
        "raw.frame_id": {"dtype": "string", "shape": (1,), "names": ["source_frame_id"]},
        "raw.session_id": {"dtype": "string", "shape": (1,), "names": ["source_session_id"]},
        "raw.timestamp_ns": {"dtype": "int64", "shape": (1,), "names": ["monotonic_ns"]},
        "raw.state_timestamp_ns": {"dtype": "int64", "shape": (1,), "names": ["monotonic_ns"]},
        "raw.action_timestamp_ns": {"dtype": "int64", "shape": (1,), "names": ["monotonic_ns"]},
        "raw.state_valid": {"dtype": "bool", "shape": (1,), "names": ["measured_state_valid"]},
        "raw.action_valid": {"dtype": "bool", "shape": (1,), "names": ["transmitted_action_valid"]},
        "raw.action_acknowledged": {"dtype": "bool", "shape": (1,), "names": ["per_motor_acknowledgement"]},
    }
    for cam in CAMERAS:
        features[f"observation.images.{cam}"] = {"dtype": "image", "shape": inspection.shapes[cam], "names": ["height", "width", "channels"]}
        features[f"raw.{cam}.timestamp_ns"] = {"dtype": "int64", "shape": (1,), "names": ["monotonic_ns"]}
    return features


def _episode_payload(inspection: _Inspection, episode: dict, episode_index: int, all_features: dict) -> dict:
    import numpy as np

    rows = episode["rows"]
    payload = {key: [] for key in all_features}
    payload.update(size=len(rows), task=[episode["task"]]*len(rows), episode_index=episode_index)
    first_time = rows[0]["timestamp_ns"]
    for index, row in enumerate(rows):
        payload["frame_index"].append(np.array([index], dtype=np.int64))
        payload["timestamp"].append(np.array([(row["timestamp_ns"]-first_time)/1e9], dtype=np.float32))
        payload["observation.state"].append(np.asarray(joint_state(row), dtype=np.float32))
        payload["action"].append(np.asarray(action_state(row), dtype=np.float32))
        payload["raw.frame_id"].append(str(row["frame_id"]))
        payload["raw.session_id"].append(str(episode["metadata"].get("session_id", "unknown")))
        for feature, source_key in (("raw.timestamp_ns", "timestamp_ns"), ("raw.state_timestamp_ns", "state_timestamp_ns"), ("raw.action_timestamp_ns", "action_timestamp_ns")):
            payload[feature].append(np.array([row[source_key]], dtype=np.int64))
        for feature, source_key in (("raw.state_valid", "state_valid"), ("raw.action_valid", "action_valid"), ("raw.action_acknowledged", "action_acknowledged")):
            payload[feature].append(np.array([row.get(source_key, False)], dtype=np.bool_))
        for cam in CAMERAS:
            raw = row["cameras"][cam]
            payload[f"observation.images.{cam}"].append(str(safe_child(episode["path"], raw["path"])))
            payload[f"raw.{cam}.timestamp_ns"].append(np.array([raw["timestamp_ns"]], dtype=np.int64))
    return payload


def _source_unchanged(inspection: _Inspection) -> None:
    for relative, expected in inspection.source_hashes.items():
        if sha256_file(safe_child(inspection.path, relative)) != expected:
            raise ExportError(f"Raw изменился во время экспорта: {relative}")
    complete_ids = {p.parent.name for p in (inspection.path / "episodes").glob("*/episode.json")}
    if complete_ids != {episode["path"].name for episode in inspection.episodes}:
        raise ExportError("Состав эпизодов изменился во время экспорта")


def verify_official_export(export_path: Path, *, verify_all_frames=True) -> dict:
    """Open official LeRobotDataset on CPU and verify arrays, timing and RGB pixels."""
    import numpy as np
    import torch
    from PIL import Image
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    path = Path(export_path).resolve()
    lineage = _read_json(path / "provenance/export.json")
    raw_root = Path(lineage["source_path"])
    sidecar_path = path / "provenance/frames.jsonl"
    if lineage.get("frame_sidecar_sha256") and sha256_file(sidecar_path) != lineage["frame_sidecar_sha256"]:
        raise ExportError("Export frame sidecar hash mismatch")
    rows = [json.loads(line) for line in sidecar_path.read_text().splitlines() if line.strip()]
    dataset = LeRobotDataset(lineage["repo_id"], root=path, video_backend="pyav", return_uint8=True)
    if len(dataset) != lineage["total_frames"] or dataset.num_episodes != lineage["total_episodes"]:
        raise ExportError("Официальный loader: неверное число кадров/эпизодов")
    if dataset.meta.fps != lineage["nominal_fps"]:
        raise ExportError("Официальный loader: FPS не совпадает")
    selected = list(range(len(dataset))) if verify_all_frames else sorted({0, len(dataset)-1})
    for index in selected:
        sidecar = rows[index]
        loaded = dataset[index]
        raw = sidecar["raw_frame"]
        if int(loaded["episode_index"]) != sidecar["lerobot_episode_index"] or int(loaded["frame_index"]) != sidecar["lerobot_frame_index"]:
            raise ExportError("Официальный loader: episode boundary/frame index сдвинут")
        if loaded.get("task") != sidecar["task"]:
            raise ExportError("Официальный loader: task description не совпадает")
        for key, expected in (("observation.state", joint_state(raw)), ("action", action_state(raw))):
            if not torch.equal(loaded[key].cpu(), torch.tensor(expected, dtype=torch.float32)):
                raise ExportError(f"Официальный loader: {key} изменён")
        for feature, source_key in (("raw.timestamp_ns", "timestamp_ns"), ("raw.state_timestamp_ns", "state_timestamp_ns"), ("raw.action_timestamp_ns", "action_timestamp_ns")):
            if int(loaded[feature]) != raw[source_key]:
                raise ExportError("Официальный loader: потеря точных ns timestamps")
        if abs(float(loaded["timestamp"])-sidecar["relative_timestamp_s"]) > max(2e-7, sidecar["relative_timestamp_s"]*2e-7):
            raise ExportError("Официальный loader: measured relative timestamp изменён")
        for cam in CAMERAS:
            with Image.open(safe_child(raw_root / sidecar["source_episode_relative"], raw["cameras"][cam]["path"])) as source:
                expected = np.asarray(source)
            actual = loaded[f"observation.images.{cam}"].permute(1, 2, 0).cpu().numpy()
            if actual.dtype != np.uint8:
                if not np.issubdtype(actual.dtype, np.floating) or not np.isfinite(actual).all() or actual.min() < 0 or actual.max() > 1:
                    raise ExportError(f"Официальный loader: неверный RGB range/dtype {cam}")
                actual = np.rint(actual*255).astype(np.uint8)
            if not np.array_equal(actual, expected):
                raise ExportError(f"Официальный loader: RGB {cam} изменён или отсутствует")
    return {"status": "passed", "loader": "lerobot.datasets.lerobot_dataset.LeRobotDataset", "lerobot_version": importlib.metadata.version("lerobot"),
            "frames_opened_and_verified": len(selected), "total_frames": len(dataset), "total_episodes": dataset.num_episodes,
            "image_shapes_chw": {cam: list(dataset[0][f"observation.images.{cam}"].shape) for cam in CAMERAS},
            "rgb_pixels_bit_exact": True, "image_comparison": "official image-backed float [0,1] decoded to exact uint8 via round(255*x), or native uint8", "measured_timestamps_preserved": True, "precise_monotonic_ns_preserved": True,
            "task_and_episode_boundaries_verified": True, "hardware_test": False, "cuda_used": False}


def export_dataset(dataset_path: Path, output_path: Path | None = None, options: dict | None = None,
                   progress: Callable | None = None, stop: Callable | None = None) -> dict:
    """Create a frozen, independently readable official dataset, never overwrite one.

    options: allow_test_data (default False), fps (only an explicitly sourced
    nominal rate, never a resampling request), verify_all_frames (default True).
    Stop/failure leaves an explicitly incomplete hidden staging folder with logs.
    A completed export becomes visible only after official loader verification.
    """
    options = dict(options or {})
    allow_test = options.get("allow_test_data") is True
    version = importlib.metadata.version("lerobot")
    if version != SUPPORTED_LEROBOT:
        raise ExportError(f"Экспорт закреплён за LeRobot {SUPPORTED_LEROBOT}, установлено {version}; требуется проверка совместимости")
    with dataset_lease(dataset_path):
        inspection = _inspect_raw(dataset_path, allow_test_data=allow_test, fps=options.get("fps"), progress=progress, stop=stop)
        if stop and stop():
            raise ExportStopped("Экспорт остановлен до записи")
        final = Path(output_path).resolve() if output_path is not None else inspection.path / "exports" / ("lerobot_" + inspection.source_digest[:16])
        if not final.is_relative_to(ROOT):
            raise ExportError("Экспорт разрешён только внутри независимой лаборатории")
        reserved = [inspection.path / name for name in ("episodes", ".partial", "quarantine", "images")]
        if any(final == item or final.is_relative_to(item) for item in reserved):
            raise ExportError("Экспорт не должен изменять raw episodes/quarantine/images")
        if final == inspection.path or inspection.path in final.parents and final.parent == inspection.path:
            raise ExportError("Экспорт нельзя помещать в корень raw dataset; используйте exports/<version>")
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            previous = _read_json(final / "provenance/export.json")
            if previous.get("status") == "complete" and previous.get("dataset_sha256") == inspection.source_digest:
                verification = verify_official_export(final, verify_all_frames=bool(options.get("verify_all_frames", True)))
                return {"status": "complete", "path": str(final), "reused_existing": True, "dataset_sha256": inspection.source_digest,
                        "repo_id": previous["repo_id"], "source": inspection.metadata["source"], "test_only": inspection.metadata["source"] != "hardware",
                        "total_frames": inspection.total_frames, "total_episodes": len(inspection.episodes), "nominal_fps": inspection.fps,
                        "warnings": inspection.warnings, "official_verification": verification}
            raise ExportError("Путь экспорта уже существует; существующие данные не перезаписываются")
        assert_disk_space(minimum_bytes=inspection.source_bytes*2 + 128*1024**2, path=final.parent)
        temporary = final.parent / ("." + final.name + ".partial-" + uuid.uuid4().hex)
        repo_name = "so101_" + inspection.source_digest[:24]
        repo_id = ("so101-lab-technical-test/" if allow_test and inspection.metadata["source"] != "hardware" else "so101-lab-local/") + repo_name
        created = None
        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
            created = LeRobotDataset.create(repo_id, fps=inspection.fps, features=_features(inspection), root=temporary,
                                            robot_type="so101_follower" if inspection.metadata["source"] == "hardware" else "synthetic_test_no_robot",
                                            use_videos=False, video_backend="pyav", image_writer_processes=0, image_writer_threads=0)
            provenance = temporary / "provenance"
            provenance.mkdir()
            _atomic_json(provenance / "raw_dataset.json", inspection.metadata)
            lineage = {**inspection.report(), "status": "writing", "repo_id": repo_id, "lerobot_version": version,
                       "lerobot_codebase_version": created.meta.info.codebase_version, "storage": "image_backed_parquet_embedded_lossless_png",
                       "timestamp_adapter": "official_save_episode_episode_data_preserve_measured_relative_seconds_v1",
                       "created_unix": time.time(), "source_hashes": inspection.source_hashes,
                       "joint_units": ["degrees"]*5+["percent_calibrated_range"], "action_semantics": "actually_transmitted_quantized_targets; no per_motor_ACK and not measured achieved position",
                       "calibration": inspection.metadata.get("calibration", {"valid": False, "reason": "unknown"}),
                       "export_code_sha256": sha256_file(Path(__file__).resolve()), "uploads": False,
                       "limitations": ["Nominal FPS does not assert ideal timing; per-frame measured timestamps and jitter are retained", "Single RGB images provide no metric depth", "Raw success labels are not generated by export"]}
            _atomic_json(provenance / "export.json", lineage)
            with (provenance / "frames.jsonl").open("w") as sidecar:
                global_index = 0
                for episode_index, episode in enumerate(inspection.episodes):
                    if stop and stop():
                        raise ExportStopped("Экспорт остановлен между эпизодами")
                    payload = _episode_payload(inspection, episode, episode_index, created.features)
                    created.save_episode(episode_data=payload, parallel_encoding=False)
                    first_time = episode["rows"][0]["timestamp_ns"]
                    for frame_index, row in enumerate(episode["rows"]):
                        record = {"lerobot_index": global_index, "lerobot_episode_index": episode_index, "lerobot_frame_index": frame_index,
                                  "relative_timestamp_s": (row["timestamp_ns"]-first_time)/1e9,
                                  "source_episode_relative": episode["path"].relative_to(inspection.path).as_posix(), "task": episode["task"], "raw_frame": row}
                        sidecar.write(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))+"\n")
                        global_index += 1
                    sidecar.flush()
                    os.fsync(sidecar.fileno())
                    _atomic_json(provenance / f"episode_{episode_index:06d}.json", episode["metadata"])
                    if progress:
                        progress({"state": "exporting_lerobot", "episodes": episode_index+1, "total_episodes": len(inspection.episodes), "frames": global_index, "total_frames": inspection.total_frames})
            created.finalize()
            created = None
            _source_unchanged(inspection)
            if stop and stop():
                raise ExportStopped("Экспорт остановлен перед финальной проверкой")
            if progress:
                progress({"state": "verifying_official_lerobot_loader", "frames": inspection.total_frames})
            verification = verify_official_export(temporary, verify_all_frames=bool(options.get("verify_all_frames", True)))
            lineage.update(status="complete", official_verification=verification, frame_sidecar_sha256=sha256_file(provenance / "frames.jsonl"))
            _atomic_json(provenance / "export.json", lineage)
            readme = "# SO-101 local LeRobot Dataset\n\n" + ("ТЕХНИЧЕСКИЙ ТЕСТ: это не реальные демонстрации робота.\n\n" if inspection.metadata["source"] != "hardware" else "Источник: реальные hardware записи; успешность задачи экспортом не устанавливается.\n\n")
            readme += "Оба исходных RGB ракурса без потерь; measured joints и transmitted action различаются. Точные monotonic ns находятся в raw.* features и provenance/frames.jsonl. Timestamp — измеренное относительное время, FPS — nominal/requested; observed FPS/jitter и калибровки находятся в provenance/export.json. Ничего не отправлено на Hugging Face.\n"
            (temporary / "README.md").write_text(readme)
            _fsync_directory(provenance)
            _fsync_directory(temporary)
            os.replace(temporary, final)
            _fsync_directory(final.parent)
            return {"status": "complete", "path": str(final), "repo_id": repo_id, "dataset_sha256": inspection.source_digest,
                    "source": inspection.metadata["source"], "test_only": inspection.metadata["source"] != "hardware", "total_frames": inspection.total_frames,
                    "total_episodes": len(inspection.episodes), "nominal_fps": inspection.fps, "official_verification": verification, "warnings": inspection.warnings}
        except Exception as error:
            if created is not None:
                try:
                    created.finalize()
                except Exception:
                    pass
            if temporary.exists():
                _atomic_json(temporary / "INCOMPLETE_EXPORT.json", {"status": "stopped" if isinstance(error, ExportStopped) else "failed", "error": str(error),
                            "source_path": str(inspection.path), "final_path": str(final), "dataset_sha256": inspection.source_digest})
                (temporary / "export-traceback.log").write_text(traceback.format_exc())
            raise
