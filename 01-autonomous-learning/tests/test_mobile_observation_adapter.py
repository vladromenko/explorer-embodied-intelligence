"""Producer-shaped software fixtures only; no hardware, source edits or replay."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from explorer_sim.dataset_export import export_lerobot_v3, load_episode
from explorer_sim.mobile_observation_adapter import FORMAT, ROW, convert_mobile_observation


def fixture(folder, *, count=14, unknown=False, outcome="success", split="train", offsets=None):
    folder.mkdir()
    (folder / "sources").mkdir()

    def retain(name, raw):
        checksum = hashlib.sha256(raw).hexdigest()
        filename = "sources/" + checksum + Path(name).suffix
        (folder / filename).write_bytes(raw)
        return {"file": filename, "sha256": checksum, "bytes": len(raw), "source_name": name}

    rows = []
    for index in range(count):
        base = 1000 + (offsets[index] if offsets is not None else index * .5)
        arm = {"phase": "command_in_progress", "servo_deg": [100 + index] * 5 + [70 + index],
               "q_estimated_deg": [90 + index * .2] * 5 + [60 + index * .2], "q_commanded_deg": [100 + index] * 5 + [70 + index],
               "at": base + .02, "command_sent_at": base + .02, "boot_id": "fixture-host-boot",
               "command_source": "coordinated_operator", "source": "timed_factory_command_estimate", "owner": "keyboard",
               "operator_step": True, "measured": False, "attained": False, "state_source": "command_trajectory_estimate"}
        if unknown:
            arm = {"phase": "UNKNOWN_AFTER_REBOOT", "measured": False}
        status = {"at": base + .04, "monotonic": 100 + index * .5, "boot_id": "fixture-host-boot", "mode": "MANUAL",
                  "arm_state": None, "arm_command_state": arm, "velocity": [.01 * index, 0., 0.],
                  "odom_velocity": [9., 9., 9.], "power": {"state": "LOW_POWER", "charging": None}}
        stream = io.BytesIO()
        Image.fromarray(np.full((12, 16, 3), index * 10, np.uint8)).save(stream, "JPEG")
        image = retain("frame-raw.jpg", stream.getvalue())
        sec = int(base)
        camera = {"at": base + .03, "image_stamp": base, "jpeg": "frame-raw.jpg", "jpeg_sha256": image["sha256"],
                  "source": "onboard_ros_cpu_cache_v1", "frame_id": "software-fixture-" + str(index),
                  "ros_header": {"sec": sec, "nanosec": int(round((base - sec) * 1e9)), "frame_id": "camera_color_optical_frame"}}
        files = {"frame-raw.jpg": image, "status.json": retain("status.json", json.dumps(status).encode()),
                 "camera-frame.json": retain("camera-frame.json", json.dumps(camera).encode()),
                 "rgbd-snapshot.npz": retain("rgbd-snapshot.npz", b"opaque original NPZ fixture; no decoder is used")}
        rows.append({"schema": ROW, "sequence": index, "collector_wall_unix": base + .06, "collector_monotonic_ns": int((100 + index * .5) * 1e9),
                     "source_files": files, "source_available": {"arm-telemetry-fault.json": False},
                     "source_errors": {"arm-telemetry-fault.json": "FileNotFoundError"}, "telemetry": status,
                     "camera_metadata_raw": camera, "same_frame_hash_verified": True, "image": image["file"], "image_sha256": image["sha256"],
                     "reported_state": [None] * 6 + [9., 9., 9.], "reported_command": [None] * 6 + status["velocity"]})
    episode = {"id": "a" * 32, "format": FORMAT, "name": "Producer-shaped software fixture; no physical claims", "state": "complete",
               "physical_sample_rate_hz": 2, "outcome": outcome, "label_source": "operator", "split": split,
               "quality": {"usable": False}, "training_admission": {"source_quality_usable": False}}
    (folder / "episode.json").write_text(json.dumps(episode))
    write_rows(folder, rows)
    return folder / "episode.json", episode, rows


def write_rows(folder, rows):
    (folder / "samples.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def change_raw(folder, row, name, value):
    raw = json.dumps(value).encode()
    checksum = hashlib.sha256(raw).hexdigest()
    relative = "sources/" + checksum + Path(name).suffix
    (folder / relative).write_bytes(raw)
    row["source_files"][name] = {"file": relative, "sha256": checksum, "bytes": len(raw), "source_name": name}
    if name == "status.json":
        row["telemetry"] = copy.deepcopy(value)
    if name == "camera-frame.json":
        row["camera_metadata_raw"] = copy.deepcopy(value)


def test_current_unknown_arm_is_successful_analysis_only_and_all_raw_bytes_retained(tmp_path):
    path, source, rows = fixture(tmp_path / "source", count=3, unknown=True, outcome="unknown")
    original = {str(p.relative_to(path.parent)): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    report = convert_mobile_observation(path, tmp_path / "converted")
    assert report["analysis_only"] and report["dataset_manifest"] is None and not report["dataset_available"]
    assert report["qualification"]["qualified_rows"] == 0
    assert "UNKNOWN_AFTER_REBOOT" in report["reason"]
    assert not report["training_admission"]["source_quality_usable"]
    assert not (tmp_path / "converted/manifest.json").exists()
    for name, raw in original.items():
        assert (path.parent / name).read_bytes() == raw
        assert (tmp_path / "converted/source_originals" / name).read_bytes() == raw
    assert convert_mobile_observation(path, tmp_path / "converted") == report


def test_known_manual_history_at_low_power_produces_real_causal_npz_without_native_v3_forgery(tmp_path):
    path, source, rows = fixture(tmp_path / "source")
    report = convert_mobile_observation(path, tmp_path / "converted")
    assert report["dataset_available"] and not report["analysis_only"]
    assert report["training_admission"] == {"source_quality_usable": True, "operator_success_label": True, "physical_validation_inferred": False}
    manifest = json.loads(Path(report["dataset_manifest"]).read_text())
    assert manifest["format"] == "explorer_robot_episode_v1" and manifest["source"] == FORMAT
    assert manifest["state_basis"] == "command_estimate" and not manifest["joint_measurements_claimed"]
    assert not manifest["provenance"]["native_v3_original_emitted"]
    descriptor = manifest["episodes"][0]
    loaded = load_episode(report["dataset_manifest"], descriptor, manifest)
    expected_state = rows[0]["telemetry"]["arm_command_state"]["q_estimated_deg"] + rows[0]["telemetry"]["velocity"]
    expected_action = rows[1]["telemetry"]["arm_command_state"]["servo_deg"] + rows[1]["telemetry"]["velocity"]
    np.testing.assert_allclose(loaded["state"][0], expected_state)
    np.testing.assert_allclose(loaded["action"][0], expected_action)
    assert loaded["state"][0, 6] != 9  # Odom values are not command labels.
    assert loaded["timestamps"]["action_wall_unix_ns"][0] > max(loaded["timestamps"]["frame_wall_unix_ns"][0], loaded["timestamps"]["state_wall_unix_ns"][0])
    with np.load(Path(report["dataset_manifest"]).parent / descriptor["file"]) as stored:
        assert stored["source_observation_index"][0] == 0 and stored["source_action_index"][0] == 1
    assert "body-command publication time" in manifest["timestamp_uncertainty"]
    assert manifest["native_timestamp_basis"]["clock_alignment_verified"] is False


@pytest.mark.parametrize("outcome", ["unknown", "failure"])
def test_failed_and_unknown_outcomes_keep_valid_dataset_for_analysis_but_disable_bc(tmp_path, outcome):
    path, source, rows = fixture(tmp_path / "source", outcome=outcome)
    report = convert_mobile_observation(path, tmp_path / "converted")
    assert report["dataset_available"] and report["analysis_only"]
    assert report["training_admission"]["source_quality_usable"]
    assert not report["training_admission"]["operator_success_label"]


@pytest.mark.parametrize("fault", ["policy", "missing_manual", "missing_fault", "newer_fault", "future_publication", "boot", "missing_estimate", "unknown_camera"])
def test_unqualified_command_sources_return_analysis_reasons(tmp_path, fault):
    path, source, rows = fixture(tmp_path / "source", count=3)
    for row in rows:
        status = copy.deepcopy(row["telemetry"])
        arm = status["arm_command_state"]
        if fault == "policy": arm["command_source"] = "supervised_mobile_policy"
        if fault == "missing_manual": arm.pop("command_source")
        if fault == "missing_fault": row["source_available"] = {}
        if fault == "newer_fault":
            row["source_available"]["arm-telemetry-fault.json"] = True
            change_raw(path.parent, row, "arm-telemetry-fault.json", {"at": status["at"] + .1})
        if fault == "future_publication": arm["command_sent_at"] = status["at"] + .1
        if fault == "boot": arm["boot_id"] = "old-boot"
        if fault == "missing_estimate": arm.pop("q_estimated_deg")
        if fault == "unknown_camera":
            camera = copy.deepcopy(row["camera_metadata_raw"])
            camera["source"] = "unverified_clock_producer"
            change_raw(path.parent, row, "camera-frame.json", camera)
        change_raw(path.parent, row, "status.json", status)
    write_rows(path.parent, rows)
    report = convert_mobile_observation(path, tmp_path / "converted")
    assert report["analysis_only"] and report["dataset_manifest"] is None
    assert report["qualification"]["qualified_rows"] == 0 and report["reason"]


def test_reversed_native_wall_clock_is_not_relabelled_and_hash_mismatch_blocks_promotion(tmp_path):
    path, source, rows = fixture(tmp_path / "source", count=4, offsets=[0, .5, .25, 1.])
    report = convert_mobile_observation(path, tmp_path / "reversed")
    assert report["dataset_manifest"] is None
    assert "original_unix_wall_clock_reversal_no_repair" in report["qualification"]["fatal_reasons"]
    row = rows[1]
    (path.parent / row["source_files"]["status.json"]["file"]).write_bytes(b'{"changed":true}')
    report = convert_mobile_observation(path, tmp_path / "corrupt")
    assert not report["dataset_available"]
    assert "source_integrity_mismatch" in report["qualification"]["fatal_reasons"]


def test_unknown_island_and_time_gap_are_segmented_without_future_metadata_input(tmp_path):
    path, source, rows = fixture(tmp_path / "source", count=8, offsets=[0, .5, 1, 1.5, 2, 5, 5.5, 6])
    status = copy.deepcopy(rows[3]["telemetry"])
    status["arm_command_state"] = {"phase": "UNKNOWN_AFTER_REBOOT", "measured": False}
    change_raw(path.parent, rows[3], "status.json", status)
    # Changing a future endpoint does not alter the current observation's estimate.
    status = copy.deepcopy(rows[1]["telemetry"])
    status["arm_command_state"]["servo_deg"] = [150.] * 5 + [100.]
    change_raw(path.parent, rows[1], "status.json", status)
    write_rows(path.parent, rows)
    report = convert_mobile_observation(path, tmp_path / "converted")
    manifest = json.loads(Path(report["dataset_manifest"]).read_text())
    assert len(manifest["episodes"]) >= 2
    all_obs, all_labels = [], []
    for episode in manifest["episodes"]:
        with np.load(Path(report["dataset_manifest"]).parent / episode["file"]) as stored:
            all_obs.extend(stored["source_observation_index"].tolist())
            all_labels.extend(stored["source_action_index"].tolist())
    assert 3 not in all_obs and 3 not in all_labels
    first = load_episode(report["dataset_manifest"], manifest["episodes"][0], manifest)
    assert first["state"][0, 0] == 90 and first["action"][0, 0] == 150
    assert manifest["resampling"]["gaps"]


def test_policy_row_inside_manual_capture_is_excluded_and_cannot_bridge_labels(tmp_path):
    path, source, rows = fixture(tmp_path / "source", count=7)
    status = copy.deepcopy(rows[3]["telemetry"])
    status["arm_command_state"]["command_source"] = "supervised_mobile_policy"
    change_raw(path.parent, rows[3], "status.json", status)
    write_rows(path.parent, rows)
    report = convert_mobile_observation(path, tmp_path / "converted")
    manifest = json.loads(Path(report["dataset_manifest"]).read_text())
    for episode in manifest["episodes"]:
        with np.load(Path(report["dataset_manifest"]).parent / episode["file"]) as data:
            assert 3 not in data["source_observation_index"] and 3 not in data["source_action_index"]
            assert np.all(data["source_action_index"] == data["source_observation_index"] + 1)
    assert "manual_arm_command_provenance_unverified" in report["qualification"]["rejection_counts"]


def test_sealed_split_cannot_be_reassigned(tmp_path):
    path, source, rows = fixture(tmp_path / "source", split="sealed_test")
    with pytest.raises(ValueError, match="sealed"):
        convert_mobile_observation(path, tmp_path / "bad", split="train")
    report = convert_mobile_observation(path, tmp_path / "sealed")
    manifest = json.loads(Path(report["dataset_manifest"]).read_text())
    assert all(item["split"] == "sealed_test" for item in manifest["episodes"])
    assert report["analysis_only"] and not report["training_admission"]["source_quality_usable"]


def test_existing_derived_dataset_cannot_silently_change_fps_or_split(tmp_path):
    path, source, rows = fixture(tmp_path / "source", count=4)
    convert_mobile_observation(path, tmp_path / "converted")
    with pytest.raises(ValueError, match="different qualification settings"):
        convert_mobile_observation(path, tmp_path / "converted", fps=1)


def test_actual_official_lerobot_v3_roundtrip_preserves_images_commands_and_source_sha(tmp_path):
    pytest.importorskip("lerobot")
    path, source, rows = fixture(tmp_path / "source", count=4)
    report = convert_mobile_observation(path, tmp_path / "converted")
    manifest = json.loads(Path(report["dataset_manifest"]).read_text())
    npz = Path(report["dataset_manifest"]).parent / manifest["episodes"][0]["file"]
    before = hashlib.sha256(npz.read_bytes()).hexdigest()
    exported = export_lerobot_v3(report["dataset_manifest"], tmp_path / "official-v3")
    assert exported["official_readback_passed"] and exported["all_frame_state_action_image_checks_passed"]
    assert exported["native_wall_timestamps_preserved"] and exported["conversion_grid_preserved"]
    assert hashlib.sha256(npz.read_bytes()).hexdigest() == before
