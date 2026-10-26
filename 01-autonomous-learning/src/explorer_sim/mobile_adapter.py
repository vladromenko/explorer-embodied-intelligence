"""Pure v3 mobile-command snapshot conversion. ENGINEERING_GLUE.

Inputs are existing files only. Labels are future host-reported target snapshots,
never proven publication, MCU receipt, measured motion or hardware application.
Legacy applied_action/arm_command_sent_at names and contract claims stay unchanged
in source_originals; the derived metadata explicitly limits their interpretation.
"""
from decimal import Decimal, ROUND_HALF_EVEN
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

import numpy as np
from PIL import Image

from .pilot import canonical, sha, utc, write_json
from .station_adapter import _causal_segments

ACTION_NAMES = ["base", "shoulder", "elbow", "wrist_pitch", "wrist_roll", "gripper", "vx", "vy", "wz"]
UNITS = ["deg"] * 6 + ["m/s", "m/s", "rad/s"]
LOW = np.asarray([0, 0, 0, 0, 0, 30, -.8, -.72, -1.67], np.float32)
HIGH = np.asarray([180, 180, 180, 180, 270, 180, .8, .72, 1.67], np.float32)
SAMPLE_CONTRACT = {"schema": "explorer_mobile_sample_v3", "action_order": ACTION_NAMES, "units": UNITS,
                   "arm_action": "applied_servo_endpoint", "base_action": "applied_body_velocity",
                   "arm_observation": "command_estimate", "clock": "unix_seconds",
                   "frame": "base_footprint", "image": "wrist_rgb"}
CONTRACT_SHA256 = hashlib.sha256(json.dumps(SAMPLE_CONTRACT, sort_keys=True).encode()).hexdigest()


class MobileEpisodeConversionError(ValueError):
    """A precise unsupported-data report; original files are never changed."""
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def _wall_ns(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0:
        raise ValueError("A finite native Unix timestamp is required")
    result = int((Decimal(str(value)) * 1_000_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))
    if result > np.iinfo(np.int64).max:
        raise ValueError("Native Unix timestamp is outside int64 nanosecond storage")
    return result


def _vector(value, label):
    result = np.asarray(value, np.float32)
    if result.shape != (9,) or not np.isfinite(result).all() or np.any(result < LOW - 1e-3) or np.any(result > HIGH + 1e-3):
        raise ValueError(label + " must contain nine finite values in the declared source units")
    return result


def _read_native(path, fps, split):
    source = json.loads(path.read_text())
    if not isinstance(source, dict):
        raise ValueError("Native episode metadata must be an object")
    if source.get("format") != "explorer_mobile_episode_v3" or source.get("source") != "operator_mobile_demonstration":
        raise ValueError("Expected an operator explorer_mobile_episode_v3 recording")
    if source.get("state") not in ("complete", "interrupted") or source.get("joint_state_source") != "commanded_not_measured":
        raise ValueError("Finish the recording and retain its commanded_not_measured state declaration")
    if source.get("sample_contract") != SAMPLE_CONTRACT or source.get("sample_contract_sha256") != CONTRACT_SHA256:
        raise ValueError("Unknown action/clock contract: Unix camera and command clocks cannot be assumed comparable")
    ident = source.get("id")
    if not isinstance(ident, str) or not ident or split not in ("train", "imitation_train", "development", "validation", "sealed_test", "test"):
        raise ValueError("An explicit episode identity and split are required")
    fps = source.get("physical_sample_rate_hz") if fps is None else fps
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not np.isfinite(fps) or not 0 < fps <= 1e9:
        raise ValueError("A positive declared nominal FPS is required; it is not inferred from an unsynchronized clock")
    jsonl = path.parent / "samples.jsonl"
    rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("No native mobile events are present")
    inputs = {path.name: sha(path), "samples.jsonl": sha(jsonl)}
    native = []
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("Native row must be an object")
            if row.get("schema") != "explorer_mobile_sample_v3" or row.get("sample_contract") != SAMPLE_CONTRACT or row.get("sample_contract_sha256") != CONTRACT_SHA256:
                raise ValueError("Unknown sample action/clock contract")
            if row.get("executed_action_source") != "operator_manual_control" or row.get("measured_arm_angles") is not False:
                raise ValueError("Manual commanded-state provenance is required; measured motion is not inferred")
            if "applied_action" not in row:
                raise ValueError("applied_action is missing; command/proposed_manual_action are not substituted for recorded target labels")
            state, action = _vector(row.get("observation_state"), "observation_state"), _vector(row["applied_action"], "applied_action")
            if "command" in row and not np.allclose(_vector(row["command"], "command"), action, atol=1e-6, rtol=0):
                raise ValueError("Legacy command differs from the documented v3 applied_action snapshot")
            image = row.get("image")
            if not isinstance(image, str) or image in (path.name, "samples.jsonl"):
                raise ValueError("A native JPEG path is required")
            image_path = (path.parent / image).resolve()
            if Path(image).is_absolute() or not image_path.is_relative_to(path.parent) or not image_path.is_file():
                raise ValueError("Native source image escapes the episode or is missing")
            digest = sha(image_path)
            if row.get("image_sha256") not in (None, digest):
                raise ValueError("Recorded source image checksum mismatch")
            if image in inputs:
                raise ValueError("Native image filename is repeated; overwritten capture bytes cannot be recovered")
            inputs[image] = digest
            stamp = {key: _wall_ns(row.get(key)) for key in ("at", "image_stamp", "state_stamp")}
            if not (0 <= stamp["at"] - stamp["image_stamp"] < 2_000_000_000 and
                    0 <= stamp["at"] - stamp["state_stamp"] < 1_000_000_000 and
                    abs(stamp["state_stamp"] - stamp["image_stamp"]) <= 1_500_000_000):
                raise ValueError("Source camera/state freshness bounds or declared Unix-clock comparison failed")
            sent = row.get("arm_command_sent_at")
            stamp["arm_command_sent_at"] = _wall_ns(sent) if sent is not None else -1
            if native and any(stamp[key] < native[-1]["stamp"][key] for key in ("at", "image_stamp", "state_stamp")):
                raise ValueError("Unix wall-clock reversal; no monotonic relabeling or clock repair is performed")
            if native and (stamp["at"] == native[-1]["stamp"]["at"] or stamp["image_stamp"] == native[-1]["stamp"]["image_stamp"]):
                raise ValueError("Recorder/frame timestamps do not increase")
            native.append({"row": row, "state": state, "action": action, "image": image,
                           "path": image_path, "sha256": digest, "stamp": stamp})
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(f"Native row {index}: {error}") from error
    return source, native, inputs, float(fps)


def _pair_native(native):
    pairs, excluded = [], []
    for index in range(len(native) - 1):
        current, future = native[index], native[index + 1]
        ready = max(current["stamp"][key] for key in ("at", "image_stamp", "state_stamp"))
        action_stamp = future["stamp"]["state_stamp"]
        if action_stamp > ready:
            pairs.append({"observation_index": index, "action_index": index + 1, "stamp": action_stamp})
        else:
            excluded.append({"observation_index": index, "action_index": index + 1,
                             "reason": "Next host-target snapshot is not later than the current observation/record timestamp"})
    if not pairs:
        raise ValueError("No causal future host-target snapshot follows an observation; original events remain available for analysis")
    return pairs, excluded


def convert_mobile_episode(source_episode_path, target_dir, *, fps=None, split=None):
    """Convert existing v3 files; never capture, contact a node, or control motors."""
    path, target = Path(source_episode_path).resolve(), Path(target_dir).resolve()
    report = {"source_episode_path": str(path), "original_files_preserved": True}
    try:
        stored = json.loads(path.read_text())
        if not isinstance(stored, dict):
            raise ValueError("Native episode metadata must be an object")
        split = stored.get("split", "train") if split is None else split
        if stored.get("split") in ("sealed_test", "test") and split != stored["split"]:
            raise ValueError("A sealed source split cannot be changed")
        source, native, inputs, fps = _read_native(path, fps, split)
        fingerprint = hashlib.sha256(canonical(inputs)).hexdigest()
        report.update(source_episode_id=source["id"], input_sha256=inputs, source_fingerprint=fingerprint)
        pairs, excluded = _pair_native(native)
        if path.is_relative_to(target) or any(item["path"].is_relative_to(target) for item in native):
            raise ValueError("Conversion target cannot contain the original source files")
        label_times = np.asarray([pair["stamp"] for pair in pairs], np.int64)
        segments, gaps = _causal_segments(label_times, fps)
        target.parent.mkdir(parents=True, exist_ok=True)
        with (target.parent / ("." + target.name + ".conversion.lock")).open("a") as owner:
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX)
            if target.exists():
                saved = _completed(target, fingerprint)
                if saved is not None:
                    return saved
                target.rename(target.with_name(target.name + ".incomplete-" + uuid.uuid4().hex))
            staging = Path(tempfile.mkdtemp(prefix="." + target.name + ".converting-", dir=target.parent))
            try:
                result = _write(staging, path, source, native, inputs, pairs, excluded, segments, gaps, fps, split, fingerprint)
                if sha(path) != inputs[path.name] or sha(path.parent / "samples.jsonl") != inputs["samples.jsonl"]:
                    raise ValueError("Native mobile source changed during conversion")
                write_json(staging / "manifest.json", result)
                os.rename(staging, target)
                return result
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
    except (ValueError, TypeError, KeyError, OSError) as error:
        report["conversion_blocked"] = str(error)
        raise MobileEpisodeConversionError(str(error), report) from error


def _completed(target, fingerprint):
    path = target / "manifest.json"
    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text())
    except (ValueError, UnicodeError):
        return None
    if not isinstance(saved, dict) or not saved.get("episodes"):
        return None
    if saved.get("source_fingerprint") != fingerprint:
        raise ValueError("Completed conversion belongs to changed source files; retained data will not be overwritten")
    files = dict(saved.get("source_files_sha256", {}))
    files.update({item["file"]: item["sha256"] for item in saved["episodes"]})
    for name, digest in files.items():
        retained = (target / name).resolve()
        if not retained.is_relative_to(target) or not retained.is_file() or sha(retained) != digest:
            raise ValueError("Converted mobile artifact checksum mismatch")
    return saved


def _write(staging, path, source, native, inputs, pairs, excluded, segments, gaps, fps, split, fingerprint):
    originals = staging / "source_originals"
    originals.mkdir()
    artifacts = {}
    for name, digest in inputs.items():
        destination = originals / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path.parent / name, destination)
        if sha(destination) != digest:
            raise ValueError("Native mobile file changed while retaining originals")
        artifacts[str(destination.relative_to(staging))] = digest
    with Image.open(native[0]["path"]) as image:
        shape = (image.height, image.width, 3)
    episodes, metadata = [], []
    for segment_index, segment in enumerate(segments):
        selected, grid = segment["selected"], segment["grid"]
        observations = [pairs[i]["observation_index"] for i in selected]
        labels = [pairs[i]["action_index"] for i in selected]
        states = np.asarray([native[i]["state"] for i in observations])
        actions = np.asarray([native[i]["action"] for i in labels])
        event_times = {"frame_wall_unix_ns": np.asarray([native[i]["stamp"]["image_stamp"] for i in observations], np.int64),
                       "state_wall_unix_ns": np.asarray([native[i]["stamp"]["state_stamp"] for i in observations], np.int64),
                       "action_wall_unix_ns": np.asarray([pairs[i]["stamp"] for i in selected], np.int64),
                       "arm_command_sent_wall_unix_ns": np.asarray([native[i]["stamp"]["arm_command_sent_at"] for i in labels], np.int64)}
        npz = staging / f"episode_{segment_index:04d}.npz"
        rgb_path, rgb = npz.with_suffix(".rgb-memory.npy"), None
        try:
            rgb = np.lib.format.open_memmap(rgb_path, mode="w+", dtype=np.uint8, shape=(len(selected), *shape))
            for index, original_index in enumerate(observations):
                with Image.open(originals / native[original_index]["image"]) as image:
                    if (image.height, image.width, 3) != shape:
                        raise ValueError("Camera image shape changed in the source recording")
                    rgb[index] = np.asarray(image.convert("RGB"))
            rgb.flush()
            np.savez_compressed(npz, rgb=rgb, state_native=states, action_native=actions,
                state=2 * (states - LOW) / (HIGH - LOW) - 1, action=2 * (actions - LOW) / (HIGH - LOW) - 1,
                grid_wall_unix_ns=grid, source_sample_index=selected,
                source_observation_index=np.asarray(observations, np.int64), source_action_index=np.asarray(labels, np.int64),
                frame_wall_unix_s=np.asarray([native[i]["row"]["image_stamp"] for i in observations], np.float64),
                state_wall_unix_s=np.asarray([native[i]["row"]["state_stamp"] for i in observations], np.float64),
                action_wall_unix_s=np.asarray([native[i]["row"]["state_stamp"] for i in labels], np.float64), **event_times)
        finally:
            if rgb is not None:
                del rgb
            rgb_path.unlink(missing_ok=True)
        identities = ["nano-mobile:" + str(native[i]["row"]["image_stamp"]) + ":" + native[i]["sha256"] for i in observations]
        episodes.append({"id": source["id"] + f"-segment-{segment_index:04d}", "source_episode_id": source["id"],
            "file": npz.name, "sha256": sha(npz), "frames": len(selected), "split": split,
            "outcome": source.get("outcome"), "outcome_label_source": source.get("label_source"),
            "clock_domain": "explorer-nano:declared-unix-wall-clock", "frame_source_ids": identities,
            "source_indices": selected.tolist(), "source_observation_indices": observations, "source_action_indices": labels})
        metadata.append({"episode_file": npz.name, "native_pair_start": segment["start"], "native_pair_end_exclusive": segment["end"],
                         "converted_count": len(selected), "source_indices": selected.tolist()})
    return {"schema_version": 1, "format": "explorer_robot_episode_v1", "dataset_id": "mobile-" + source["id"],
        "source": "explorer_mobile_episode_v3", "created_wall_utc": utc(), "source_manifest_path": str(path),
        "source_manifest_sha256": inputs[path.name], "source_fingerprint": fingerprint, "source_files_sha256": artifacts,
        "source_input_sha256": inputs, "source_hash_basis": "Computed at import; v3 recorder does not store capture-time JPEG hashes", "source_episode_metadata": source, "action_names": ACTION_NAMES, "action_units": UNITS,
        "action_normalization": {"low": LOW.tolist(), "high": HIGH.tolist()}, "state_basis": "command_estimate",
        "state_estimation_method": "source_command_or_trajectory_estimate", "action_basis": "host_reported_target_snapshot",
        "dispatch_confirmed": False,
        "source_action_field": "applied_action",
        "source_contract_claims": {"arm_action": SAMPLE_CONTRACT["arm_action"], "base_action": SAMPLE_CONTRACT["base_action"],
                                   "executed_action_source": "operator_manual_control", "independently_verified": False},
        "clock_domain": "explorer-nano:declared-unix-wall-clock", "native_timestamp_basis": {
            "kind": "unix_wall_clock", "comparison_basis": "v3 source contract explicitly declares unix_seconds",
            "clock_alignment_verified": False, "missing_arm_host_record_timestamp_sentinel": -1, "unit_conversion": "Stored decimal float seconds rounded to ns; raw seconds and JSONL retained",
            "arm_command_sent_wall_unix_ns_basis": "Raw arm_command_sent_at: ManualArm host prepublication record time (source may fall back to arm.at); no publication or MCU receipt is proved",
            "action_time_definition": "Next row state_stamp: host-reported target snapshot, not a newly dispatched 9D action, proven publication, MCU acceptance or physical application"},
        "fps": fps, "contains_physical_recordings": True, "joint_measurements_claimed": False, "physical_success_inferred": False,
        "source_camera_feature": "wrist_rgb", "source_frame_identity_basis": "Source image_stamp plus JPEG SHA; recorder does not retain a frame UUID",
        "timestamp_uncertainty": "ROS image-header acquisition estimate versus host/recorder Unix wall time. No clock offset was invented. Exposure latency, body-command publication time and physical application are unmeasured; raw arm_command_sent_at is a host prepublication record time (possibly arm.at fallback), not dispatch/MCU proof.",
        "resampling": {"method": "causal_previous_mobile_pair_hold", "original_count": len(native), "native_pair_count": len(pairs),
            "converted_count": sum(item["frames"] for item in episodes), "gap_threshold_periods": 3, "gaps": gaps,
            "segments": metadata, "excluded_pairs": excluded, "unpaired_tail_source_index": len(native) - 1,
            "grid_basis": "Nominal wall-time grid of host target snapshot labels; not sensor capture, proven publication or new hardware dispatch",
            "hold_semantics": "Most recent past native pair only. Repeated image/state/command labels are held estimates, not new measurements"},
        "task": source.get("name", "Imitate the recorded mobile demonstration"), "episodes": episodes,
        "training_admission": {"source_quality_usable": isinstance(source.get("quality"), dict) and source["quality"].get("usable") is True,
            "operator_success_label": source.get("outcome") == "success" and source.get("label_source") == "operator",
            "physical_validation_inferred": False}, "method_ids": ["ENGINEERING_GLUE"]}
