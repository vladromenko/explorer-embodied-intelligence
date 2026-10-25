"""Pure, timestamp-checked station recording conversion. ENGINEERING_GLUE.

Reads existing images, measured states and accepted/dispatched actions only;
there are no motor, camera capture, network or calibration write operations.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .pilot import sha, utc, write_json


def convert_station_episode(source_manifest_path, target_dir):
    source_path, target = Path(source_manifest_path).resolve(), Path(target_dir).resolve()
    source_digest = sha(source_path)
    source = json.loads(source_path.read_text())
    episode_id, split = source.get("episode_id"), source.get("split", "train")
    if not isinstance(episode_id, str) or not episode_id or split not in ("train", "imitation_train", "development", "validation", "sealed_test", "test"):
        raise ValueError("Explicit episode identity and valid split are required")
    if source.get("source_type") != "station_demonstration" or source.get("state_basis") not in ("measured_state", "command_estimate"):
        raise ValueError("An explicit station demonstration state basis is required")
    if source.get("action_basis") not in ("accepted_action", "dispatched_command"):
        raise ValueError("Requested/operator-input actions are not accepted as applied demonstration labels")
    samples = source.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Camera-only recordings have no synchronized state/action samples")
    names, units = source.get("action_names"), source.get("action_units")
    if not isinstance(names, list) or len(names) not in (6, 9) or not isinstance(units, list) or len(units) != len(names):
        raise ValueError("Explicit names and units for six or nine action dimensions are required")
    dimension = len(names)
    normalization = source.get("normalization", {})
    low, high = np.asarray(normalization.get("low"), np.float32), np.asarray(normalization.get("high"), np.float32)
    if low.shape != (dimension,) or high.shape != low.shape or not np.isfinite(low).all() or not np.isfinite(high).all() or np.any(high <= low):
        raise ValueError("Explicit finite normalization ranges are required")
    fps = source.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not np.isfinite(fps) or fps <= 0:
        raise ValueError("Recording decision FPS is required")
    states, actions, frame_times, state_times, action_times, frames = [], [], [], [], [], []
    domains = set()
    for sample in samples:
        frame = sample.get("frame", {})
        if not isinstance(frame.get("frame_id"), str) or not frame["frame_id"]:
            raise ValueError("An immutable source camera frame ID is required")
        image_path = Path(frame.get("file", ""))
        image_path = image_path if image_path.is_absolute() else source_path.parent / image_path
        if not image_path.is_file() or sha(image_path) != frame.get("sha256"):
            raise ValueError("Station source frame checksum mismatch")
        state, action = np.asarray(sample.get("state"), np.float32), np.asarray(sample.get("action"), np.float32)
        if state.shape != (dimension,) or action.shape != state.shape or not np.isfinite(state).all() or not np.isfinite(action).all():
            raise ValueError("Station state/action dimensions or values differ")
        if np.any(state < low - 1e-3) or np.any(state > high + 1e-3) or np.any(action < low - 1e-3) or np.any(action > high + 1e-3):
            raise ValueError("Source values do not match declared normalization units")
        ft, st, at = frame.get("monotonic_ns"), sample.get("state_monotonic_ns"), sample.get("action_monotonic_ns")
        if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (ft, st, at)) or not ft <= st <= at:
            raise ValueError("Frame/state/action causal timestamp ordering differs")
        frame_domain, sample_domain = frame.get("clock_domain"), sample.get("clock_domain")
        if not frame_domain or frame_domain != sample_domain:
            raise ValueError("Frame and servo clocks cannot be silently merged")
        domains.add(frame_domain)
        frames.append((image_path, frame))
        states.append(state)
        actions.append(action)
        frame_times.append(ft)
        state_times.append(st)
        action_times.append(at)
    if len(domains) != 1 or np.any(np.diff(action_times) <= 0):
        raise ValueError("An episode must remain in one boot clock with increasing actions")
    decision_deltas = np.diff(action_times) / 1e9
    if len(decision_deltas) and not np.allclose(decision_deltas, 1 / fps, rtol=.25, atol=.01):
        raise ValueError("Irregular decision intervals need explicit resampling before a fixed-FPS dataset")
    if target.exists():
        existing = target / "manifest.json"
        if existing.is_file():
            saved = json.loads(existing.read_text())
            if saved.get("source_manifest_sha256") == source_digest and sha(target / saved["episodes"][0]["file"]) == saved["episodes"][0]["sha256"]:
                return saved
        raise ValueError("Conversion target already exists; source data will not be overwritten")
    target.mkdir(parents=True)
    rgb_path, npz_path = target / ".rgb-memory.npy", target / "episode_0000.npz"
    rgb = None
    try:
        with Image.open(frames[0][0]) as image:
            shape = (image.height, image.width, 3)
        rgb = np.lib.format.open_memmap(rgb_path, mode="w+", dtype=np.uint8, shape=(len(samples), *shape))
        for index, (path, descriptor) in enumerate(frames):
            if sha(path) != descriptor["sha256"]:
                raise ValueError("Source frame changed during conversion")
            with Image.open(path) as image:
                image = image.convert("RGB")
                if (image.height, image.width, 3) != shape:
                    raise ValueError("Station image resolution changed within episode")
                rgb[index] = np.asarray(image)
        rgb.flush()
        states, actions = np.asarray(states), np.asarray(actions)
        np.savez_compressed(npz_path, rgb=rgb, state_native=states, action_native=actions,
                            state=2 * (states - low) / (high - low) - 1,
                            action=2 * (actions - low) / (high - low) - 1,
                            frame_monotonic_ns=np.asarray(frame_times, np.int64),
                            state_monotonic_ns=np.asarray(state_times, np.int64),
                            action_monotonic_ns=np.asarray(action_times, np.int64))
    finally:
        if rgb is not None:
            del rgb
        rgb_path.unlink(missing_ok=True)
    if sha(source_path) != source_digest:
        raise ValueError("Source station manifest changed during conversion")
    result = {"schema_version": 1, "format": "explorer_robot_episode_v1", "dataset_id": "station-" + episode_id,
              "source": "station_demonstration", "created_wall_utc": utc(), "source_manifest_sha256": source_digest,
              "source_manifest_path": str(source_path), "source_calibration_metadata": {
                  key: value for key, value in source.items() if "calibrat" in key.lower()},
              "action_names": names, "action_units": units, "action_normalization": {"low": low.tolist(), "high": high.tolist()},
              "state_basis": source["state_basis"], "action_basis": source["action_basis"], "clock_domain": next(iter(domains)),
              "fps": fps, "contains_physical_recordings": True, "joint_measurements_claimed": source["state_basis"] == "measured_state",
              "physical_success_inferred": False, "task": source.get("task", "Imitate the recorded robot demonstration"),
              "frame_source_ids": [frame["frame_id"] for _, frame in frames],
              "timestamp_uncertainty": "Host receive/read/dispatch timestamps; exposure and motor application time are not measured",
              "episodes": [{"id": episode_id, "source_episode_id": episode_id, "file": npz_path.name, "sha256": sha(npz_path),
                            "frames": len(samples), "split": split, "outcome": source.get("outcome"),
                            "clock_domain": next(iter(domains)), "frame_source_ids": [frame["frame_id"] for _, frame in frames]}],
              "method_ids": ["ENGINEERING_GLUE"]}
    write_json(target / "manifest.json", result)
    return result
