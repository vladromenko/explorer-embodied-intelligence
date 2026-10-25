"""Immutable NPZ episode -> official LeRobotDataset v3 image export/readback.

The official writer creates every parquet/metadata file. Source episodes stay
unchanged. Command estimates are not relabelled as measured joint positions.
Methods: ACT_LEROBOT_0_6_1, ENGINEERING_GLUE.
"""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

import numpy as np

from .pilot import ACTION_NAMES, HIGH, LOW, canonical, sha, utc, write_json


IMAGE_KEY = "observation.images.overview"


def load_episode(manifest_path, descriptor, manifest):
    directory = Path(manifest_path).resolve().parent
    path = (directory / descriptor["file"]).resolve()
    if not path.is_relative_to(directory) or not path.is_file() or sha(path) != descriptor["sha256"]:
        raise ValueError("Source episode path/checksum mismatch")
    with np.load(path, allow_pickle=False) as source:
        action_key = "action_native" if "action_native" in source else "command9"
        if action_key not in source or "state" not in source or "rgb" not in source:
            raise ValueError("ACT needs synchronized RGB, command-state and action labels; camera-only recording is insufficient")
        rgb, state_normalized, action = source["rgb"].copy(), source["state"].copy(), source[action_key].copy()
        normalization = manifest.get("action_normalization", {"low": LOW.tolist(), "high": HIGH.tolist()})
        low, high = np.asarray(normalization["low"], np.float32), np.asarray(normalization["high"], np.float32)
        if "state_native" in source:
            state = source["state_native"].copy()
        else:
            state = source["state_command9"].copy() if "state_command9" in source else (state_normalized + 1) * .5 * (high - low) + low
        reward = source["reward"].copy() if "reward" in source else np.zeros(len(rgb), np.float32)
        mono_names = ("frame_monotonic_ns", "state_monotonic_ns", "action_monotonic_ns")
        wall_names = ("frame_wall_unix_ns", "state_wall_unix_ns", "action_wall_unix_ns")
        has_wall = any(name in source for name in wall_names)
        if has_wall and any(name in source for name in mono_names):
            raise ValueError("Native wall and monotonic clocks cannot be silently merged")
        names = wall_names if has_wall else mono_names
        timestamps = {name: source[name].copy() for name in names if name in source}
        grid_name = "grid_wall_unix_ns" if has_wall else "grid_monotonic_ns"
        grid = source[grid_name].copy() if grid_name in source else None
        source_indices = source["source_sample_index"].copy() if "source_sample_index" in source else None
        auxiliary = {name: source[name].copy() for name in ("arm_command_sent_wall_unix_ns",) if name in source}
    count = len(rgb)
    if not count or rgb.dtype != np.uint8 or rgb.ndim != 4 or rgb.shape[-1] != 3:
        raise ValueError("Expected nonempty uint8 NHWC RGB episode")
    if state.ndim != 2 or action.shape != state.shape or len(state) != count or len(reward) != count:
        raise ValueError("State/action/frame alignment mismatch")
    if not np.isfinite(state).all() or not np.isfinite(action).all() or not np.isfinite(reward).all():
        raise ValueError("Nonfinite episode labels")
    resampling = manifest.get("resampling", {})
    held_samples = resampling.get("method") in ("causal_previous_sample_hold", "causal_previous_mobile_pair_hold")
    measured = manifest.get("state_basis", "command_estimate") == "measured_state"
    if measured or held_samples or timestamps:
        if len(timestamps) != 3 or not manifest.get("clock_domain"):
            raise ValueError("Native episodes need one declared clock domain and frame/state/action timestamps")
        frame_time, state_time, action_time = [timestamps[name] for name in names]
        if any(value.shape != (count,) or not np.issubdtype(value.dtype, np.integer) or np.any(value < 0) for value in timestamps.values()):
            raise ValueError("Invalid native event timestamp arrays")
        if has_wall:
            if manifest.get("native_timestamp_basis", {}).get("kind") != "unix_wall_clock":
                raise ValueError("Wall timestamps require an explicit source Unix-wall-clock declaration")
            if any(np.any(np.diff(value) < 0) for value in timestamps.values()):
                raise ValueError("Native Unix wall-clock reversal cannot be relabelled monotonic")
        if np.any(frame_time > action_time) or np.any(state_time > action_time) or (measured and np.any(frame_time > state_time)):
            raise ValueError("Causal mismatch in native frame/state/action timestamps")
        if held_samples:
            period = 1e9 / manifest["fps"]
            if (grid is None or source_indices is None or grid.shape != (count,) or source_indices.shape != (count,)
                    or not np.issubdtype(grid.dtype, np.integer) or not np.issubdtype(source_indices.dtype, np.integer)
                    or np.any(source_indices < 0) or np.any(np.diff(source_indices) < 0)
                    or np.any(grid < 0) or np.any(np.diff(grid) <= 0) or np.any(np.diff(action_time) < 0)
                    or np.any(action_time > grid) or np.any(grid - action_time > 3 * period)
                    or not np.allclose(np.diff(grid), period, rtol=0, atol=1)
                    or source_indices.tolist() != descriptor.get("source_indices")):
                raise ValueError("Causal resampling needs a nominal increasing grid and past native samples")
        elif np.any(np.diff(action_time) <= 0):
            raise ValueError("Causal mismatch in native action timestamps")
        if auxiliary and any(value.shape != (count,) or not np.issubdtype(value.dtype, np.integer) or np.any(value < -1) for value in auxiliary.values()):
            raise ValueError("Invalid optional publication timestamp arrays")
    elif len(state) > 1 and not np.allclose(state[1:], action[:-1], atol=1e-4):
        raise ValueError("Causal mismatch: observation command-state must precede its action")
    return {"path": path, "rgb": rgb, "state": state.astype(np.float32),
            "action": action.astype(np.float32), "reward": reward.astype(np.float32), "timestamps": timestamps,
            "grid_monotonic_ns": grid if not has_wall else None, "grid_wall_unix_ns": grid if has_wall else None,
            "grid_timestamp_key": grid_name, "source_sample_index": source_indices, "auxiliary_timestamps": auxiliary}


def _validate_retained_originals(manifest_path, manifest):
    directory = Path(manifest_path).resolve().parent
    for name, digest in manifest.get("source_files_sha256", {}).items():
        artifact = (directory / name).resolve()
        if not artifact.is_relative_to(directory) or not artifact.is_file() or sha(artifact) != digest:
            raise ValueError("Retained native source artifact checksum mismatch")


def validate_splits(manifest):
    seen = {}
    episodes = []
    for index, descriptor in enumerate(manifest["episodes"]):
        identity = descriptor.get("source_episode_id", descriptor.get("id", descriptor["file"]))
        split = descriptor.get("split", "train")
        for key in ("id:" + identity, "sha256:" + descriptor["sha256"]):
            if key in seen and seen[key] != split:
                raise ValueError("Source episode overlaps dataset splits")
            seen[key] = split
        for frame_id in descriptor.get("frame_source_ids", []):
            key = "frame:" + descriptor.get("clock_domain", manifest.get("clock_domain", "")) + ":" + frame_id
            if key in seen and seen[key] != split:
                raise ValueError("Source camera frame overlaps dataset splits")
            seen[key] = split
        episodes.append({"episode_index": index, "source_episode_id": identity,
                         "source_sha256": descriptor["sha256"], "split": split,
                         "source_seed": descriptor.get("seed"), "source_outcome": descriptor.get("outcome")})
    return episodes


def export_lerobot_v3(manifest_path, target_dir, repo_id=None):
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.datasets.dataset_metadata import CODEBASE_VERSION

    if importlib.metadata.version("lerobot") != "0.6.1" or CODEBASE_VERSION != "v3.0":
        raise RuntimeError("This export verifies official LeRobot 0.6.1 / dataset v3.0")
    manifest_path, target_dir = Path(manifest_path).resolve(), Path(target_dir).resolve()
    original_manifest_sha = sha(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format") not in ("explorer_npz_episode_v1", "explorer_robot_episode_v1"):
        raise ValueError("A synchronized, action-labelled Explorer episode manifest is required")
    _validate_retained_originals(manifest_path, manifest)
    split_map = validate_splits(manifest)
    if not manifest.get("episodes"):
        raise ValueError("Dataset contains no episodes")
    source_episodes = [load_episode(manifest_path, item, manifest) for item in manifest["episodes"]]
    names = manifest.get("action_names", ACTION_NAMES)
    state_dim, action_dim = source_episodes[0]["state"].shape[1], source_episodes[0]["action"].shape[1]
    if len(names) != action_dim or state_dim != action_dim:
        raise ValueError("Missing consistent state/action dimension names")
    image_shape = tuple(source_episodes[0]["rgb"].shape[1:])
    if any(tuple(item["rgb"].shape[1:]) != image_shape or item["state"].shape[1] != state_dim for item in source_episodes):
        raise ValueError("Episode camera/state schema differs")
    fps = manifest.get("fps")
    fps_basis = "source_manifest"
    if fps is None and manifest.get("source") == "real_mujoco_ppo_rollouts":
        fps, fps_basis = 12.5, "verified_legacy_pilot_period_0.08s"
    if not isinstance(fps, (int, float)) or not np.isfinite(fps) or fps <= 0:
        raise ValueError("Explicit positive recording FPS is required")
    repo_id = repo_id or "local-explorer/" + manifest["dataset_id"]
    provenance_path = target_dir / "explorer_provenance.json"
    if provenance_path.is_file():
        saved = json.loads(provenance_path.read_text())
        if saved["source_manifest_sha256"] != original_manifest_sha:
            raise ValueError("Existing export belongs to different source data")
        return verify_lerobot_v3(target_dir, repo_id, manifest_path, source_episodes, saved)
    if target_dir.exists():
        raise ValueError("Export target already exists without a completed provenance record")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    features = {IMAGE_KEY: {"dtype": "image", "shape": image_shape, "names": ["height", "width", "channels"]},
                "observation.state": {"dtype": "float32", "shape": (state_dim,), "names": names},
                "action": {"dtype": "float32", "shape": (action_dim,), "names": names},
                "next.reward": {"dtype": "float32", "shape": (1,), "names": None},
                "next.done": {"dtype": "bool", "shape": (1,), "names": None}}
    timestamp_names = tuple(source_episodes[0]["timestamps"])
    if any(tuple(source["timestamps"]) != timestamp_names for source in source_episodes):
        raise ValueError("Episode native timestamp schemas differ; clock domains cannot be merged")
    has_timestamps = len(timestamp_names) == 3
    if has_timestamps:
        for name in timestamp_names:
            features["timestamp.source_" + name] = {"dtype": "int64", "shape": (1,), "names": None}
    grid_key = source_episodes[0]["grid_timestamp_key"]
    has_grid = all(source.get(grid_key) is not None and source["grid_timestamp_key"] == grid_key for source in source_episodes)
    grid_feature = "timestamp.conversion_" + grid_key
    auxiliary_names = tuple(source_episodes[0]["auxiliary_timestamps"])
    if any(tuple(source["auxiliary_timestamps"]) != auxiliary_names for source in source_episodes):
        raise ValueError("Episode publication timestamp schema differs")
    for name in auxiliary_names:
        features["timestamp.source_" + name] = {"dtype": "int64", "shape": (1,), "names": None}
    if has_grid:
        features[grid_feature] = {"dtype": "int64", "shape": (1,), "names": None}
        features["provenance.source_sample_index"] = {"dtype": "int64", "shape": (1,), "names": None}
    dataset = LeRobotDataset.create(repo_id, fps=fps, features=features, root=target_dir,
                                    robot_type="rosmaster_m3_cad_reach_pilot" if manifest.get("source") == "real_mujoco_ppo_rollouts" else "explorer_recorded_robot",
                                    use_videos=False, image_writer_processes=0, image_writer_threads=0,
                                    video_backend="pyav", streaming_encoding=False)
    try:
        for source in source_episodes:
            for index in range(len(source["rgb"])):
                frame = {IMAGE_KEY: source["rgb"][index], "observation.state": source["state"][index],
                    "action": source["action"][index], "next.reward": np.asarray([source["reward"][index]], np.float32),
                    "next.done": np.asarray([index == len(source["rgb"]) - 1], bool),
                    "task": manifest.get("task", "Reach the visible green target with the gripper; base fixed")}
                if has_timestamps:
                    frame.update({"timestamp.source_" + name: np.asarray([values[index]], np.int64)
                                  for name, values in source["timestamps"].items()})
                if has_grid:
                    frame[grid_feature] = np.asarray([source[grid_key][index]], np.int64)
                    frame["provenance.source_sample_index"] = np.asarray([source["source_sample_index"][index]], np.int64)
                frame.update({"timestamp.source_" + name: np.asarray([values[index]], np.int64)
                              for name, values in source["auxiliary_timestamps"].items()})
                dataset.add_frame(frame)
            dataset.save_episode()
    finally:
        dataset.finalize()
    provenance = {"schema_version": 1, "official_dataset_format": "LeRobotDataset v3.0",
                  "official_writer": "lerobot.datasets.lerobot_dataset.LeRobotDataset", "lerobot_version": "0.6.1",
                  "created_wall_utc": utc(), "repo_id": repo_id, "use_videos": False,
                  "source_manifest_path": str(manifest_path), "source_manifest_sha256": original_manifest_sha,
                  "state_basis": manifest.get("state_basis", "command_estimate"),
                  "action_basis": manifest.get("action_basis", "applied_command_endpoint"),
                  "action_names": names, "action_units": manifest.get("action_units", ["degrees"] * 6 + ["m/s", "m/s", "rad/s"]),
                  "action_normalization": manifest.get("action_normalization"), "clock_domain": manifest.get("clock_domain"),
                  "source_calibration_metadata": manifest.get("source_calibration_metadata"),
                  "joint_measurements_claimed": manifest.get("state_basis") == "measured_state", "fps": fps, "fps_basis": fps_basis,
                  "episodes": split_map, "source_npz_immutable": True,
                  "files_sha256": {str(path.relative_to(target_dir)): sha(path)
                                   for path in sorted(target_dir.rglob("*")) if path.is_file()},
                  "finalize_called": True, "uploaded_to_hub": False}
    provenance.update(timestamp_uncertainty=manifest.get("timestamp_uncertainty"),
                      source_camera_feature=manifest.get("source_camera_feature"),
                      native_timestamp_basis=manifest.get("native_timestamp_basis"),
                      native_timestamp_keys=timestamp_names, auxiliary_timestamp_keys=auxiliary_names, conversion_grid_key=grid_key,
                      causal_resampling=manifest.get("resampling"), conversion_grid_preserved=has_grid,
                      source_original_files_sha256=manifest.get("source_files_sha256", {}),
                      native_monotonic_timestamps_preserved=has_timestamps and timestamp_names[0].endswith("monotonic_ns"),
                      native_wall_timestamps_preserved=has_timestamps and timestamp_names[0].endswith("wall_unix_ns"),
                      official_timestamp_basis="nominal decision FPS grid; native event times stored separately when present")
    write_json(provenance_path, provenance)
    return verify_lerobot_v3(target_dir, repo_id, manifest_path, source_episodes, provenance)


def verify_lerobot_v3(target_dir, repo_id, manifest_path, source_episodes=None, provenance=None):
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    target_dir, manifest_path = Path(target_dir), Path(manifest_path)
    provenance = provenance or json.loads((target_dir / "explorer_provenance.json").read_text())
    if sha(manifest_path) != provenance["source_manifest_sha256"]:
        raise ValueError("Source manifest changed during conversion")
    manifest = json.loads(manifest_path.read_text())
    _validate_retained_originals(manifest_path, manifest)
    source_episodes = source_episodes or [load_episode(manifest_path, item, manifest) for item in manifest["episodes"]]
    for name, digest in provenance["files_sha256"].items():
        path = target_dir / name
        if not path.resolve().is_relative_to(target_dir.resolve()) or sha(path) != digest:
            raise ValueError("Exported dataset checksum mismatch")
    dataset = LeRobotDataset(repo_id, root=target_dir, video_backend="pyav", download_videos=False)
    if dataset.meta.info.codebase_version != "v3.0" or dataset.meta.total_episodes != len(source_episodes):
        raise ValueError("Official reader metadata mismatch")
    index = 0
    for episode_index, source in enumerate(source_episodes):
        for frame_index in range(len(source["rgb"])):
            item = dataset[index]
            if int(item["episode_index"]) != episode_index or int(item["frame_index"]) != frame_index:
                raise ValueError("Official readback episode boundaries differ")
            if not np.allclose(item["observation.state"].numpy(), source["state"][frame_index], atol=1e-5):
                raise ValueError("Official readback state labels differ")
            if not np.allclose(item["action"].numpy(), source["action"][frame_index], atol=1e-5):
                raise ValueError("Official readback action labels differ")
            rgb = item[IMAGE_KEY]
            recovered = (rgb.permute(1, 2, 0).numpy() * 255).round().astype(np.uint8)
            if not np.array_equal(recovered, source["rgb"][frame_index]):
                raise ValueError("Official image readback differs")
            if not np.isclose(float(item["timestamp"]), frame_index / provenance["fps"], atol=1e-5):
                raise ValueError("Official readback timestamp differs")
            if provenance.get("native_monotonic_timestamps_preserved") or provenance.get("native_wall_timestamps_preserved"):
                for name, values in source["timestamps"].items():
                    if int(item["timestamp.source_" + name]) != int(values[frame_index]):
                        raise ValueError("Native recording timestamp readback differs")
            if provenance.get("conversion_grid_preserved"):
                grid_key = provenance.get("conversion_grid_key", "grid_monotonic_ns")
                if int(item["timestamp.conversion_" + grid_key]) != int(source[grid_key][frame_index]) or int(item["provenance.source_sample_index"]) != int(source["source_sample_index"][frame_index]):
                    raise ValueError("Conversion grid/source sample readback differs")
            for name, values in source["auxiliary_timestamps"].items():
                if int(item["timestamp.source_" + name]) != int(values[frame_index]):
                    raise ValueError("Optional publication timestamp readback differs")
            index += 1
        if sha(source["path"]) != manifest["episodes"][episode_index]["sha256"]:
            raise ValueError("Source NPZ changed during export")
    if len(dataset) != index:
        raise ValueError("Official reader frame count mismatch")
    report = {"format": "LeRobotDataset v3.0", "directory": str(target_dir), "repo_id": repo_id,
              "episodes": len(source_episodes), "frames": index, "fps": provenance["fps"],
              "image_only": True, "finalize_called": True, "official_readback_passed": True,
              "all_frame_state_action_image_checks_passed": True, "causal_alignment_checked": True,
              "source_splits_preserved": True, "source_npz_immutable": True,
              "native_monotonic_timestamps_preserved": provenance.get("native_monotonic_timestamps_preserved", False),
              "native_wall_timestamps_preserved": provenance.get("native_wall_timestamps_preserved", False),
              "conversion_grid_preserved": provenance.get("conversion_grid_preserved", False),
              "source_manifest_sha256": provenance["source_manifest_sha256"],
              "provenance_sha256": sha(target_dir / "explorer_provenance.json"), "uploaded_to_hub": False}
    write_json(target_dir.parent / (target_dir.name + "-readback.json"), report)
    return report
