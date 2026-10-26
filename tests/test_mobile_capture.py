"""ENGINEERING_GLUE observation-recorder tests: file fixtures only, no hardware."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
from explorer_shared.mobile_capture import FORMAT, ROW_SCHEMA, MobileCaptureRecorder, projection, save


def fixture(tmp_path):
    source = tmp_path / "legacy-data"
    source.mkdir()
    # These are explicitly software fixture bytes, not a camera or physical validation.
    jpeg = b"\xff\xd8software JPEG fixture\xff\xd9"
    (source / "frame-raw.jpg").write_bytes(jpeg)
    status = {"at": 1000.123, "monotonic": 44.5, "boot_id": "software-fixture-boot", "mode": "MANUAL",
              "arm_state": None, "arm_command_state": {"phase": "UNKNOWN_AFTER_REBOOT", "measured": False},
              "velocity": [0., .01, 0.], "odom_velocity": [.002, 0., 0.],
              "power": {"state": "LOW_POWER", "charging": None, "filtered_voltage": 11.25},
              "raw_pose": {"x": 1., "y": 2., "yaw": .3}}
    camera = {"at": 1000.12, "image_stamp": 1000.111,
              "ros_header": {"sec": 1000, "nanosec": 111000000, "frame_id": "camera_color_optical_frame"},
              "source": "onboard_ros_cpu_cache_v1", "camera_id": "onboard_rgbd", "frame_id": "source-exact-id",
              "jpeg": "frame-raw.jpg", "jpeg_sha256": hashlib.sha256(jpeg).hexdigest()}
    (source / "status.json").write_bytes(json.dumps(status, indent=2).encode())
    (source / "camera-frame.json").write_bytes(json.dumps(camera, indent=2).encode())
    (source / "rgbd-snapshot.npz").write_bytes(b"opaque original RGB-D NPZ fixture; do not decode")
    (source / "rgbd-cache-metadata.json").write_text(json.dumps({"image_stamp": 1000.111, "depth_stamp": 1000.122}))
    return source, status, camera


def wait_rows(recorder, count=1):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        active = recorder.status()["active"]
        if active and active["samples"] >= count:
            return active
        time.sleep(.01)
    raise AssertionError("Recorder did not write a bounded background sample")


def rows(recorder, ident):
    return [json.loads(line) for line in (recorder.folder(ident) / "samples.jsonl").read_text().splitlines()]


def test_low_power_unknown_arm_is_captured_with_exact_bytes_and_immutable_archive(tmp_path):
    source, status, camera = fixture(tmp_path)
    original = {path.name: path.read_bytes() for path in source.iterdir()}
    recorder = MobileCaptureRecorder(source, tmp_path / "node-state", sample_hz=10)
    try:
        start = recorder.start("Технический тест записи", observing=False)
        assert len(start["id"]) == 32 and start["session"] == start["id"]
        wait_rows(recorder)
        recorder.heartbeat(start["session"], False)
        finish = recorder.finish("unknown", expected_id=start["id"])
        assert finish["state"] == "complete" and finish["format"] == FORMAT
        assert finish["outcome"] == "unknown" and not finish["training_admission"]["source_quality_usable"]
        assert finish["motion_commands"] == 0
        row = rows(recorder, start["id"])[0]
        assert row["schema"] == ROW_SCHEMA and row["telemetry"] == status
        assert row["source_status_at"] == status["at"] and row["source_status_monotonic"] == 44.5
        assert row["reported_state"][:6] == [None] * 6
        assert row["reported_command"][:6] == [None] * 6
        assert row["state_valid_mask"] == [False] * 6 + [True] * 3
        assert row["reported_command"][6:] == status["velocity"]
        assert row["reported_state"][6:] == status["odom_velocity"]
        assert row["image_stamp"] == camera["image_stamp"]
        assert row["camera_metadata_raw"]["ros_header"] == camera["ros_header"]
        assert row["same_frame_hash_verified"] and not row["clock_alignment_verified"]
        assert row["rgbd_stamp_binding_verified"] is False
        assert row["status_source_age_s"] > 100  # Stale source retained, never relabelled fresh.
        assert "sample_contract" not in row and "applied_action" not in row
        info = recorder.archive_info(start["id"])
        archived = Path(info["path"]).read_bytes()
        with tarfile.open(info["path"]) as bundle:
            for name, item in row["source_files"].items():
                assert bundle.extractfile(item["file"]).read() == original[name]
                assert item["sha256"] == hashlib.sha256(original[name]).hexdigest()
            assert json.load(bundle.extractfile("transport.json"))["training_admission"] is False
        assert recorder.finish("success", expected_id=start["id"])["outcome"] == "unknown"
        assert Path(info["path"]).read_bytes() == archived
        assert {path.name: path.read_bytes() for path in source.iterdir()} == original
        assert recorder.status()["active"] is None and recorder.status()["last"]["id"] == start["id"]
    finally:
        recorder.close()


def test_jpeg_timestamp_mismatch_retains_original_image_without_relabelling(tmp_path):
    source, status, camera = fixture(tmp_path)
    camera["jpeg_sha256"] = "a" * 64
    (source / "camera-frame.json").write_text(json.dumps(camera))
    recorder = MobileCaptureRecorder(source, tmp_path / "state")
    try:
        record = recorder.start(observing=True)
        wait_rows(recorder)
        recorder.finish("success")
        row = rows(recorder, record["id"])[0]
        assert row["image"] and row["image_stamp"] is None and not row["same_frame_hash_verified"]
        assert row["camera_metadata_raw"]["image_stamp"] == 1000.111
        assert not recorder.get(record["id"])["training_admission"]["source_quality_usable"]
    finally:
        recorder.close()


def test_missing_invalid_and_changing_source_retains_available_raw_data(tmp_path):
    source, status, camera = fixture(tmp_path)
    (source / "status.json").write_bytes(b'{"invalid":NaN}')
    (source / "rgbd-snapshot.npz").unlink()
    recorder = MobileCaptureRecorder(source, tmp_path / "state", sample_hz=10)
    try:
        record = recorder.start()
        wait_rows(recorder)
        first = rows(recorder, record["id"])[0]
        assert first["telemetry"] == {} and first["reported_state"] == [None] * 9
        assert first["source_available"]["status.json"]
        assert first["source_errors"]["status.json"] == "source_json_parse_error_raw_bytes_retained"
        assert not first["source_available"]["rgbd-snapshot.npz"]
        (source / "status.json").write_text(json.dumps(status))
        wait_rows(recorder, 2)
        recorder.finish()
        second = rows(recorder, record["id"])[1]
        assert second["frame_reused"] is True
        assert second["telemetry"]["power"]["state"] == "LOW_POWER"
    finally:
        recorder.close()


def test_exclusive_owner_session_validation_and_bounded_writer(tmp_path):
    source, status, camera = fixture(tmp_path)
    recorder = MobileCaptureRecorder(source, tmp_path / "state", sample_hz=10, max_bytes=10)
    try:
        with pytest.raises(BlockingIOError):
            MobileCaptureRecorder(source, tmp_path / "state")
        record = recorder.start()
        deadline = time.monotonic() + 3
        while recorder.status()["active"] and time.monotonic() < deadline:
            time.sleep(.01)
        result = recorder.get(record["id"])
        assert result["state"] == "interrupted" and result["outcome"] == "unknown"
        assert result["error"] == "capture_storage_byte_bound_reached"
        assert not result["training_admission"]["source_quality_usable"]
        recorder.archive(record["id"])
        with pytest.raises(ValueError):
            recorder.heartbeat("wrong", True)
    finally:
        recorder.close()
    reopened = MobileCaptureRecorder(source, tmp_path / "state")
    reopened.close()


def test_restart_recovers_exact_journal_with_partial_tail_and_orphan_bytes(tmp_path):
    source, status, camera = fixture(tmp_path)
    recorder = MobileCaptureRecorder(source, tmp_path / "state")
    record = recorder.start()
    wait_rows(recorder)
    recorder.finish()
    ident, folder = record["id"], recorder.folder(record["id"])
    recorder.close()
    # Simulate persisted recording state plus a torn journal tail after sudden process loss.
    stored = json.loads((folder / "episode.json").read_text())
    stored.update(state="recording", outcome="success")
    save(folder / "episode.json", stored)
    with (folder / "samples.jsonl").open("ab") as stream:
        stream.write(b'{"torn":')
    (folder / "sources/orphan.jpg").write_bytes(b"orphan retained bytes")
    (folder.parent / (ident + ".archive.json")).unlink()
    journal_before = (folder / "samples.jsonl").read_bytes()
    recovered = MobileCaptureRecorder(source, tmp_path / "state")
    try:
        result = recovered.get(ident)
        assert result["state"] == "interrupted" and result["outcome"] == "unknown"
        assert result["samples"] == 1 and result["recovered_after_restart"]
        assert (folder / "samples.jsonl").read_bytes() == journal_before
        with tarfile.open(recovered.archive(ident)) as bundle:
            assert bundle.extractfile("samples.jsonl").read() == journal_before
            assert bundle.extractfile("sources/orphan.jpg").read() == b"orphan retained bytes"
    finally:
        recovered.close()


def test_no_symlink_reads_or_legacy_state_writes_and_explicit_finish_identity(tmp_path):
    source, status, camera = fixture(tmp_path)
    external = tmp_path / "unrelated.json"
    external.write_text('{"private":"not-a-source"}')
    (source / "status.json").unlink()
    (source / "status.json").symlink_to(external)
    with pytest.raises(ValueError, match="outside"):
        MobileCaptureRecorder(source, source / "new-state")
    recorder = MobileCaptureRecorder(source, tmp_path / "state")
    try:
        record = recorder.start()
        wait_rows(recorder)
        with pytest.raises(ValueError, match="identity"):
            recorder.finish(expected_id="f" * 32)
        recorder.finish()
        row = rows(recorder, record["id"])[0]
        assert not row["source_available"]["status.json"]
        assert "private" not in json.dumps(row)
        assert external.read_text() == '{"private":"not-a-source"}'
    finally:
        recorder.close()


def test_projection_never_promotes_arm_zeros_or_estimate_to_measured_feedback():
    result = projection({"arm_state": [0] * 6, "arm_command_state": {"phase": "UNKNOWN_AFTER_REBOOT", "measured": False},
                         "velocity": [0, True, float("nan")], "odom_velocity": [0, 0, 0]})
    assert result["reported_state"][:6] == [None] * 6
    assert result["reported_command"] == [None] * 6 + [0, None, None]
    assert result["command_valid_mask"] == [False] * 6 + [True, False, False]
    assert not result["measured_arm_feedback_claimed"] and not result["native_9d_contract_satisfied"]


def test_stage_and_observing_annotations_are_manifest_and_journal_information(tmp_path):
    source, status, camera = fixture(tmp_path)
    recorder = MobileCaptureRecorder(source, tmp_path / "state", sample_hz=10)
    try:
        record = recorder.start(observing=False)
        wait_rows(recorder)
        recorder.stage("Смотрим на сцену")
        recorder.heartbeat(record["session"], observing=True)
        wait_rows(recorder, 2)
        finished = recorder.finish("success")
        assert finished["stage"] == "Смотрим на сцену"
        assert finished["stage_annotations"][0]["stage"] == "Смотрим на сцену"
        row = rows(recorder, record["id"])[1]
        assert row["stage"] == "Смотрим на сцену" and row["observing"] is True and row["frame_reused"]
        assert finished["training_admission"]["operator_success_label"] is True
        assert finished["training_admission"]["source_quality_usable"] is False
        assert finished["motion_commands"] == 0
    finally:
        recorder.close()


def test_interrupted_telemetry_only_recovery_does_not_invent_camera_frames(tmp_path):
    source, status, camera = fixture(tmp_path)
    (source / "frame-raw.jpg").unlink()
    recorder = MobileCaptureRecorder(source, tmp_path / "state")
    record = recorder.start()
    wait_rows(recorder)
    completed = recorder.finish()
    assert completed["samples"] == 1 and completed["frames"] == 0
    folder = recorder.folder(record["id"])
    recorder.close()
    stored = json.loads((folder / "episode.json").read_text())
    stored["state"] = "recording"
    save(folder / "episode.json", stored)
    (folder.parent / (record["id"] + ".archive.json")).unlink()
    recovered = MobileCaptureRecorder(source, tmp_path / "state")
    try:
        result = recovered.get(record["id"])
        assert result["state"] == "interrupted" and result["samples"] == 1 and result["frames"] == 0
        assert result["outcome"] == "unknown"
        recovered.archive(record["id"])
    finally:
        recovered.close()
