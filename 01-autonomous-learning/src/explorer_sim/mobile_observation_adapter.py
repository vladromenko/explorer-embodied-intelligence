"""ENGINEERING_GLUE: qualify original cache observations into derived command labels.

Never creates a native-v3 original, controls hardware, repairs clocks, reconstructs
unknown arms or treats controller command estimates as measured joint positions.
Audited legacy consumer: mobile_demonstrations.py SHA256 bc792ed1cbb5fcbca486c7fc7aa1b696916d215f6c042924ffd0b14a50cb8cff.
"""
from __future__ import annotations

from collections import Counter
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import uuid

import numpy as np
from PIL import Image

from .dataset_export import load_episode, validate_splits
from .mobile_adapter import ACTION_NAMES, HIGH, LOW, UNITS, _wall_ns
from .pilot import canonical, sha, utc, write_json
from .station_adapter import _causal_segments

FORMAT = "explorer_mobile_observation_episode_v1"
ROW = "explorer_mobile_observation_sample_v1"
CONTRACT = "explorer_derived_mobile_command_history_v1"
MANUAL_SOURCES = {"operator", "coordinated_operator"}
AUDITED_SOURCES = {"manual_arm.py": "8ddadb774fb166878d8856974bcea3c075ad8014af7f3c28247cea0e0b8ecdb8",
                   "arm_velocity.py": "ee5300c7731b47806201e5509e8cad69e275f4e87812fb79d91964638159fd7c",
                   "core.py": "a052ce93b26e7a299b10e5d0b78457e42a5e7ff6be7c8073e92749483f5cea10",
                   "mobile_demonstrations.py": "bc792ed1cbb5fcbca486c7fc7aa1b696916d215f6c042924ffd0b14a50cb8cff"}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _json(path):
    if path.stat().st_size > 128 * 1024**2:
        raise ValueError("Source JSON exceeds its explicit bound")
    return json.loads(path.read_text())


def _member(folder, name):
    if not isinstance(name, str) or Path(name).is_absolute() or not Path(name).parts or ".." in Path(name).parts:
        raise ValueError("Source member path is invalid")
    candidate = folder / name
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(folder) or not candidate.is_file():
        raise ValueError("Source member is missing or linked outside its recording")
    return candidate


def _inputs(path):
    files = {}
    for candidate in sorted(path.parent.rglob("*")):
        if candidate.is_symlink() or (not candidate.is_dir() and not candidate.is_file()):
            raise ValueError("Source recording contains linked/special members")
        if candidate.is_file():
            files[candidate.relative_to(path.parent).as_posix()] = sha(candidate)
    if len(files) > 100_000 or path.name not in files or "samples.jsonl" not in files:
        raise ValueError("Source recording file inventory is incomplete or exceeds its bound")
    return files


def _vector(value, length):
    if not isinstance(value, list) or len(value) != length or not all(_finite(item) for item in value):
        raise ValueError("missing_or_nonfinite_command_coordinates")
    return value


def _raw(row, name, folder):
    descriptor = row.get("source_files", {}).get(name)
    if not isinstance(descriptor, dict):
        raise ValueError("missing_retained_" + name)
    path = _member(folder, descriptor.get("file"))
    if sha(path) != descriptor.get("sha256") or path.stat().st_size != descriptor.get("bytes"):
        raise ValueError("retained_source_checksum_mismatch")
    return path


def _derive(row, index, folder):
    if not isinstance(row, dict) or row.get("schema") != ROW:
        raise ValueError("unknown_observation_row_schema")
    status = _json(_raw(row, "status.json", folder))
    camera = _json(_raw(row, "camera-frame.json", folder))
    image = _raw(row, "frame-raw.jpg", folder)
    if row.get("telemetry") != status or row.get("camera_metadata_raw") != camera:
        raise ValueError("parsed_row_differs_from_retained_raw_source")
    image_hash = sha(image)
    if camera.get("source") != "onboard_ros_cpu_cache_v1":
        raise ValueError("camera_clock_producer_contract_unverified")
    if camera.get("jpeg") != "frame-raw.jpg" or camera.get("jpeg_sha256") != image_hash or row.get("image_sha256") != image_hash or row.get("same_frame_hash_verified") is not True:
        raise ValueError("jpeg_and_original_metadata_not_hash_bound")
    if status.get("mode") != "MANUAL":
        raise ValueError("source_mode_not_manual")
    arm = status.get("arm_command_state") or {}
    if arm.get("phase") not in {"command_in_progress", "command_elapsed_observation_required"}:
        raise ValueError("unknown_or_unqualified_arm_history:" + str(arm.get("phase", "missing")))
    source = arm.get("command_source", arm.get("source"))
    if source not in MANUAL_SOURCES:
        raise ValueError("manual_arm_command_provenance_unverified")
    if arm.get("measured") is not False:
        raise ValueError("command_only_arm_state_declaration_missing")
    boot = status.get("boot_id")
    if not isinstance(boot, str) or not boot or arm.get("boot_id") != boot:
        raise ValueError("arm_history_boot_identity_mismatch")
    fault_name = "arm-telemetry-fault.json"
    available = row.get("source_available", {})
    if fault_name not in available:
        raise ValueError("arm_telemetry_fault_presence_or_absence_not_retained")
    if available[fault_name] is True:
        fault = _json(_raw(row, fault_name, folder))
        if not _finite(fault.get("at")) or not _finite(arm.get("at")) or fault["at"] > arm["at"]:
            raise ValueError("arm_history_invalidated_by_newer_telemetry_fault")
    elif available[fault_name] is not False or row.get("source_errors", {}).get(fault_name) != "FileNotFoundError":
        raise ValueError("arm_telemetry_fault_state_unreadable")
    state_time, frame_time, collected = (_wall_ns(value) for value in (status.get("at"), camera.get("image_stamp"), row.get("collector_wall_unix")))
    if not (0 <= collected - state_time < 1_000_000_000 and 0 <= collected - frame_time < 2_000_000_000 and abs(state_time - frame_time) <= 1_500_000_000):
        raise ValueError("native_source_camera_or_state_stale_or_future")
    header = camera.get("ros_header", {})
    if not isinstance(header.get("sec"), int) or not isinstance(header.get("nanosec"), int) or not 0 <= header["nanosec"] < 1_000_000_000:
        raise ValueError("original_ros_header_unavailable")
    if abs(header["sec"] * 1_000_000_000 + header["nanosec"] - frame_time) > 256:
        raise ValueError("ros_header_and_source_stamp_disagree")
    endpoint = _vector(arm.get("servo_deg"), 6)
    estimate = arm.get("q_estimated_deg")
    if estimate is None and arm["phase"] == "command_elapsed_observation_required":
        estimate = endpoint
    estimate = _vector(estimate, 6)
    body = _vector(status.get("velocity"), 3)
    state, action = np.asarray(estimate + body, np.float32), np.asarray(endpoint + body, np.float32)
    if np.any(state < LOW - 1e-3) or np.any(state > HIGH + 1e-3) or np.any(action < LOW - 1e-3) or np.any(action > HIGH + 1e-3):
        raise ValueError("command_coordinates_outside_audited_degree_velocity_units")
    sent_raw = arm.get("command_sent_at", arm.get("at"))
    sent = _wall_ns(sent_raw)
    if sent > state_time:
        raise ValueError("arm_host_record_timestamp_after_state_snapshot")
    with Image.open(image) as decoded:
        image_shape = (decoded.height, decoded.width, 3)
        decoded.verify()
    controller_boot = arm.get("controller_boot_id")
    if controller_boot is not None and type(controller_boot) not in (str, int):
        raise ValueError("unrecognized_controller_boot_identity")
    return {"index": index, "row": row, "state": state, "action": action, "image": image,
            "image_relative": image.relative_to(folder).as_posix(),
            "image_shape": image_shape,
            "image_hash": image_hash, "boot": boot, "controller_boot": controller_boot,
            "state_time": state_time, "frame_time": frame_time, "collected": collected, "sent": sent,
            "arm_timestamp_source": "raw_command_sent_at_host_prepublication_record" if "command_sent_at" in arm else "raw_arm_at_host_record_fallback",
            "command_source": source, "arm_estimation_basis": "reported_q_estimated_deg" if arm.get("q_estimated_deg") is not None else "reported_elapsed_endpoint_estimate"}


def _quality(native, pairs, source):
    times = [item["frame_time"] for item in native]
    duration = (times[-1] - times[0]) / 1e9 if len(times) > 1 else 0
    gaps = np.diff(times) / 1e9
    commands = {tuple(item["action"].tolist()) for item in native}
    reasons = []
    if len(native) < 12 or duration < 5:
        reasons.append("legacy_quality_minimum_12_observations_and_5_seconds_not_met")
    if len(commands) < 5:
        reasons.append("legacy_quality_fewer_than_5_distinct_commands")
    if len(gaps) and (max(gaps) > 1.5 or (len(native) - 1) / max(duration, 1e-9) < 1.5):
        reasons.append("legacy_quality_camera_gap_or_rate")
    if not pairs:
        reasons.append("no_strictly_future_adjacent_command_snapshot")
    return {"usable": not reasons, "reasons": reasons, "native_observations": len(native), "causal_pairs": len(pairs),
            "duration_s": duration, "operator_success_label": source.get("outcome") == "success" and source.get("label_source") == "operator"}


def convert_mobile_observation(source_episode_path, target_dir, *, fps=None, split=None):
    """Return a typed normal analysis result for valid raw but unqualified observations."""
    path, target = Path(source_episode_path).resolve(), Path(target_dir).resolve()
    if target == path.parent or target in path.parents or path.parent in target.parents:
        raise ValueError("Conversion target must be outside the immutable original recording")
    source = _json(path)
    if source.get("format") != FORMAT or source.get("state") not in {"complete", "interrupted"}:
        raise ValueError("Expected a finished observation recording")
    inputs = _inputs(path)
    fingerprint = hashlib.sha256(canonical(inputs)).hexdigest()
    fps = source.get("physical_sample_rate_hz") if fps is None else fps
    if not _finite(fps) or not 0 < fps <= 10:
        raise ValueError("A positive explicit nominal recording FPS is required")
    split = source.get("split", "train") if split is None else split
    if split not in {"train", "training", "imitation_train", "development", "validation", "dev", "test", "sealed_test", "sealed_final_test"} or source.get("split") in {"test", "sealed_test", "sealed_final_test"} and split != source["split"]:
        raise ValueError("Invalid or reassigned sealed source split")
    native, rejected, rows = [], [], []
    raw_row_count = 0
    with (path.parent / "samples.jsonl").open() as journal:
        for index, line in enumerate(journal):
            raw_row_count += 1
            try:
                row = json.loads(line)
                rows.append(row)
                native.append(_derive(row, index, path.parent))
            except (ValueError, TypeError, KeyError, OSError) as error:
                rejected.append({"source_index": index, "reason": str(error)})
    fatal = []
    if any(item["reason"] in {"retained_source_checksum_mismatch", "parsed_row_differs_from_retained_raw_source"} for item in rejected):
        fatal.append("source_integrity_mismatch")
    if len({item["boot"] for item in native}) > 1 or len({item["controller_boot"] for item in native}) > 1:
        fatal.append("host_or_controller_boot_changed")
    if len({item["image_shape"] for item in native}) > 1:
        fatal.append("qualified_camera_resolution_changed_raw_observations_retained")
    for previous, current in zip(native, native[1:]):
        if any(current[key] < previous[key] for key in ("frame_time", "state_time", "collected")):
            fatal.append("original_unix_wall_clock_reversal_no_repair")
    pairs, skipped = [], []
    for current, future in zip(native, native[1:]):
        if future["index"] == current["index"] + 1 and future["state_time"] > max(current["state_time"], current["frame_time"], current["collected"]) and future["frame_time"] > current["frame_time"]:
            pairs.append({"observation": current, "label": future})
        else:
            skipped.append({"source_index": current["index"], "reason": "nonadjacent_or_nonfuture_source_snapshot"})
    if fatal:
        pairs = []
    quality = _quality(native, pairs, source)
    quality["usable"] = quality["usable"] and not fatal
    training_split = split in {"train", "training", "imitation_train"}
    settings = {"fps": fps, "split": split, "derived_contract": CONTRACT, "implementation_sha256": sha(Path(__file__))}
    target.parent.mkdir(parents=True, exist_ok=True)
    with (target.parent / ("." + target.name + ".observation.lock")).open("a") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        if target.exists():
            saved = _json(target / "conversion.json")
            if saved.get("source_fingerprint") != fingerprint:
                raise ValueError("Existing conversion belongs to different immutable source bytes")
            if saved.get("conversion_settings") != settings:
                raise ValueError("Existing conversion uses different qualification settings or source implementation")
            for name, digest in saved["output_sha256"].items():
                if sha(_member(target, name)) != digest:
                    raise ValueError("Existing conversion checksum differs")
            return saved
        staging = Path(tempfile.mkdtemp(prefix="." + target.name + ".observations-", dir=target.parent))
        try:
            originals = staging / "source_originals"
            artifacts = {}
            for name, digest in inputs.items():
                destination = originals / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path.parent / name, destination)
                if sha(destination) != digest:
                    raise ValueError("Original observation source changed while retaining bytes")
                artifacts[str(destination.relative_to(staging))] = digest
            manifest = _dataset(staging, path, source, pairs, artifacts, fingerprint, fps, split, quality) if pairs else None
            if manifest is not None:
                write_json(staging / "manifest.json", manifest)
                validate_splits(manifest)
                for descriptor in manifest["episodes"]:
                    load_episode(staging / "manifest.json", descriptor, manifest)
            if _inputs(path) != inputs:
                raise ValueError("Original observation source changed during qualification")
            report = {"format": "explorer_mobile_observation_import_v1", "derived_contract": CONTRACT,
                      "source_episode_id": source.get("id"), "source_manifest_path": str(path), "source_manifest_sha256": inputs[path.name],
                      "source_fingerprint": fingerprint, "source_original_retained": True, "original_files_unchanged": True,
                      "dataset_available": manifest is not None, "dataset_manifest": str(target / "manifest.json") if manifest else None,
                      "analysis_only": not (manifest and quality["usable"] and quality["operator_success_label"] and training_split),
                      "conversion_settings": settings,
                      "training_admission": {"source_quality_usable": bool(manifest and quality["usable"] and training_split), "operator_success_label": quality["operator_success_label"], "physical_validation_inferred": False},
                      "qualification": {"raw_rows": raw_row_count, "qualified_rows": len(native), "causal_pairs": len(pairs), "quality": quality,
                                        "rejected_rows": rejected, "excluded_pairs": skipped, "fatal_reasons": sorted(set(fatal)),
                                        "rejection_counts": dict(Counter(item["reason"] for item in rejected))},
                      "frames": sum(item["frames"] for item in manifest["episodes"]) if manifest else 0,
                      "action_names": ACTION_NAMES if manifest else [], "state_basis": "command_estimate" if manifest else "partial_raw_observation",
                      "physical_success_inferred": False, "method_ids": ["ENGINEERING_GLUE"]}
            report.update(action_basis="host_reported_target_snapshot" if manifest else None, dispatch_confirmed=False)
            outcome_reason = [] if quality["operator_success_label"] else ["operator_outcome_is_not_explicit_success"]
            split_reason = [] if training_split else ["source_split_is_development_or_sealed_not_BC_training"]
            report["reason"] = "; ".join(sorted(set(fatal + [item["reason"] for item in rejected] + quality["reasons"] + outcome_reason + split_reason))) or "Qualified command-history subset; no physical success or measured arm claim"
            report["output_sha256"] = {str(p.relative_to(staging)): sha(p) for p in sorted(staging.rglob("*")) if p.is_file()}
            write_json(staging / "conversion.json", report)
            for entry in staging.rglob("*"):
                entry.chmod(0o700 if entry.is_dir() else 0o600)
            os.rename(staging, target)
            return report
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def _dataset(folder, path, source, pairs, artifacts, fingerprint, fps, split, quality):
    times = np.asarray([pair["label"]["state_time"] for pair in pairs], np.int64)
    cuts = [0, *(index for index in range(1, len(pairs)) if pairs[index]["observation"]["index"] != pairs[index - 1]["label"]["index"]), len(pairs)]
    segments, gaps = [], []
    for begin, end in zip(cuts[:-1], cuts[1:]):
        local_segments, local_gaps = _causal_segments(times[begin:end], fps)
        for segment in local_segments:
            segment["selected"] += begin
            segments.append(segment)
        gaps.extend(local_gaps)
        if begin:
            gaps.append({"previous_pair_index": begin - 1, "next_pair_index": begin,
                         "reason": "Rejected/noncausal source row separates qualified islands; no hold across it"})
    descriptors = []
    for index, segment in enumerate(segments):
        selected = segment["selected"]
        used = [pairs[i] for i in selected]
        states = np.asarray([pair["observation"]["state"] for pair in used], np.float32)
        actions = np.asarray([pair["label"]["action"] for pair in used], np.float32)
        target = folder / f"episode_{index:04d}.npz"
        first = folder / "source_originals" / used[0]["observation"]["image_relative"]
        with Image.open(first) as image:
            shape = (image.height, image.width, 3)
        memory_path = target.with_suffix(".rgb-memory.npy")
        images = np.lib.format.open_memmap(memory_path, mode="w+", dtype=np.uint8, shape=(len(used), *shape))
        try:
            for row_index, pair in enumerate(used):
                retained_image = folder / "source_originals" / pair["observation"]["image_relative"]
                if sha(retained_image) != pair["observation"]["image_hash"]:
                    raise ValueError("Retained JPEG changed before image decode")
                with Image.open(retained_image) as image:
                    if (image.height, image.width, 3) != shape:
                        raise ValueError("Source JPEG shape changed within qualified island")
                    images[row_index] = np.asarray(image.convert("RGB"))
            images.flush()
            np.savez_compressed(target, rgb=images, state_native=states, action_native=actions,
                            state=2 * (states - LOW) / (HIGH - LOW) - 1, action=2 * (actions - LOW) / (HIGH - LOW) - 1,
                            grid_wall_unix_ns=segment["grid"], source_sample_index=selected,
                            source_observation_index=np.asarray([pair["observation"]["index"] for pair in used], np.int64),
                            source_action_index=np.asarray([pair["label"]["index"] for pair in used], np.int64),
                            frame_wall_unix_ns=np.asarray([pair["observation"]["frame_time"] for pair in used], np.int64),
                            state_wall_unix_ns=np.asarray([pair["observation"]["state_time"] for pair in used], np.int64),
                            action_wall_unix_ns=np.asarray([pair["label"]["state_time"] for pair in used], np.int64),
                                arm_command_sent_wall_unix_ns=np.asarray([pair["label"]["sent"] for pair in used], np.int64))
        finally:
            del images
            memory_path.unlink(missing_ok=True)
        descriptors.append({"id": str(source["id"]) + f"-derived-{index:04d}", "source_episode_id": source["id"],
                            "file": target.name, "sha256": sha(target), "frames": len(used), "split": split,
                            "source_indices": selected.tolist(), "outcome": source.get("outcome"),
                            "frame_source_ids": ["observation:" + str(pair["observation"]["frame_time"]) + ":" + pair["observation"]["image_hash"] for pair in used]})
    return {"schema_version": 1, "format": "explorer_robot_episode_v1", "dataset_id": "mobile-observation-" + source["id"],
            "source": FORMAT, "derived_contract": CONTRACT, "created_wall_utc": utc(),
            "source_manifest_path": str(path), "source_manifest_sha256": sha(path), "source_fingerprint": fingerprint,
            "source_files_sha256": artifacts, "source_episode_metadata": source, "episodes": descriptors,
            "action_names": ACTION_NAMES, "action_units": UNITS, "action_normalization": {"low": LOW.tolist(), "high": HIGH.tolist()},
            "state_basis": "command_estimate", "state_estimation_method": "reported_q_estimated_deg_or_elapsed_endpoint_no_interpolation",
            "action_basis": "host_reported_target_snapshot", "dispatch_confirmed": False, "clock_domain": "explorer-nano:declared-unix-wall-clock",
            "native_timestamp_basis": {"kind": "unix_wall_clock", "comparison_basis": "Audited core status wall Unix and original ROS header seconds, uncalibrated",
                                       "clock_alignment_verified": False, "action_time_definition": "Next retained state.at host target snapshot; no publication, MCU acceptance or physical application is proved",
                                       "arm_command_sent_wall_unix_ns_basis": "Raw command_sent_at (fallback arm.at): host prepublication record time, actual dispatch unconfirmed"},
            "fps": fps, "contains_physical_recordings": True, "joint_measurements_claimed": False, "physical_success_inferred": False,
            "source_camera_feature": "onboard_rgbd_color", "task": source.get("name", "Offline mobile command imitation"),
            "timestamp_uncertainty": "Original ROS header versus core wall Unix; exposure latency, clock alignment, body-command publication time and physical application unmeasured. Raw arm command_sent_at is host prepublication intent (fallback arm.at), not proof of dispatch or MCU receipt.",
            "training_admission": {"source_quality_usable": quality["usable"] and split in {"train", "training", "imitation_train"}, "operator_success_label": quality["operator_success_label"], "physical_validation_inferred": False},
            "resampling": {"method": "causal_previous_mobile_pair_hold", "native_pair_count": len(pairs), "gap_threshold_periods": 3, "gaps": gaps,
                           "grid_basis": "Nominal wall-time grid of future host-reported target snapshots; held past pairs only, no exposure or dispatch claim"},
            "provenance": {"native_v3_original_emitted": False, "power_level_used_as_admission": False,
                           "input_features": ["current JPEG", "current reported arm command estimate", "current base command snapshot"],
                           "future_snapshot_role": "action label only", "manual_sources": sorted(MANUAL_SOURCES),
                           "arm_sent_timestamp_semantics": "Host intent recorded before publication; no MCU receipt/attainment timestamp is available",
                           "source_arm_timestamp_bases": sorted({pair[role]["arm_timestamp_source"] for pair in pairs for role in ("observation", "label")}),
                           "audited_source_sha256": AUDITED_SOURCES, "producer_runtime_binary_identity_verified": False},
            "method_ids": ["ENGINEERING_GLUE"]}
