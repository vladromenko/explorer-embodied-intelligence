"""CPU-only technical export fixtures, never successful hardware demonstrations."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from so101_lab.features import sha256_file
from so101_lab.lerobot_export import ExportError, ExportStopped, export_dataset, validate_raw_dataset, verify_official_export


def raw_fixture(root: Path) -> Path:
    root.mkdir(parents=True)
    metadata = {"schema_version": "so101-raw-v1", "source": "synthetic_test", "test_only": True,
                "name": "Технический тест экспорта", "task": "move cube fixture",
                "settings": {"fps": 30}, "calibration": {"valid": False, "reason": "synthetic_fixture_not_a_real_camera"}}
    (root / "dataset.json").write_text(json.dumps(metadata))
    for episode_index in range(2):
        episode = root / "episodes" / f"ep_{episode_index}"
        episode.mkdir(parents=True)
        records = []
        for frame_index, offset in enumerate((0, 33_333_333, 75_000_000)):
            stamp = 5_000_000_000_000 + episode_index*1_000_000_000 + offset
            row = {"frame_id": frame_index, "timestamp_ns": stamp, "observation_timestamp_ns": stamp,
                   "state_timestamp_ns": stamp, "action_timestamp_ns": stamp+1_234_567,
                   "observation.state": [float(frame_index), -2.0, 3.5, -4.25, 5.0, 50.0],
                   "action": [float(frame_index)+.125, -2.125, 3.625, -4.125, 5.125, 51.0],
                   "state_valid": False, "action_valid": False, "action_acknowledged": False,
                   "source": "synthetic_test", "feedback": {"force": {"valid": False, "value": None}}, "cameras": {}}
            for camera_index, camera in enumerate(("top", "wrist")):
                pixels = np.zeros((16, 24, 3), dtype=np.uint8)
                pixels[:, :, camera_index] = 30 + frame_index*20 + episode_index
                pixels[4:8, 5:12, 2] = 255
                image_path = episode / "images" / camera / f"{frame_index:08d}.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(pixels).save(image_path)
                row["cameras"][camera] = {"timestamp_ns": stamp-10-camera_index, "valid": True,
                    "sha256": sha256_file(image_path), "path": image_path.relative_to(episode).as_posix(),
                    "reused": False, "dropped_since_previous": 0}
            records.append(row)
        frames = episode / "frames.jsonl"
        frames.write_text("".join(json.dumps(record)+"\n" for record in records))
        info = {"status": "complete", "source": "synthetic_test", "frames": len(records), "frames_sha256": sha256_file(frames),
                "session_id": f"technical_session_{episode_index}", "fps_requested": 30.0, "task": f"technical task {episode_index}"}
        (episode / "episode.json").write_text(json.dumps(info))
    return root


def test_raw_validation_defaults_deny_test_and_preserves_measured_timing(tmp_path):
    raw = raw_fixture(tmp_path / "raw")
    with pytest.raises(ExportError, match="Тестовый"):
        validate_raw_dataset(raw)
    report = validate_raw_dataset(raw, allow_test_data=True)
    assert report["status"] == "valid" and report["test_only"] is True
    assert report["successful_robot_demonstrations_verified"] is False
    assert report["total_frames"] == 6 and report["total_episodes"] == 2
    timing = report["episodes"][0]["timing"]
    assert timing["timestamps_resampled"] is False
    assert timing["nominal_fps"] == 30
    assert timing["observed_mean_fps"] == pytest.approx(2/.075)
    assert timing["max_interval_deviation_from_nominal_ms"] > 8


def test_official_lerobot_cpu_loader_lossless_rgb_timestamps_boundaries(tmp_path):
    import torch
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    raw = raw_fixture(tmp_path / "raw")
    final = tmp_path / "official_lerobot"
    result = export_dataset(raw, final, {"allow_test_data": True})
    assert result["status"] == "complete" and result["test_only"] is True
    assert result["official_verification"]["frames_opened_and_verified"] == 6
    assert result["official_verification"]["rgb_pixels_bit_exact"] is True
    assert result["official_verification"]["cuda_used"] is False
    lineage = json.loads((final / "provenance/export.json").read_text())
    assert lineage["lerobot_version"] == "0.6.1"
    assert lineage["source_hashes"]["dataset.json"] == sha256_file(raw / "dataset.json")
    assert lineage["calibration"]["valid"] is False
    opened = LeRobotDataset(result["repo_id"], root=final, video_backend="pyav")
    assert len(opened) == 6 and opened.num_episodes == 2
    assert float(opened[2]["timestamp"]) == pytest.approx(.075, abs=1e-7)
    assert float(opened[2]["timestamp"]) != pytest.approx(2/30, abs=1e-5)
    assert int(opened[2]["raw.timestamp_ns"]) == 5_000_075_000_000
    assert int(opened[3]["episode_index"]) == 1 and float(opened[3]["timestamp"]) == 0
    assert opened[0]["task"] == "technical task 0"
    assert opened[3]["task"] == "technical task 1"
    assert opened[0]["raw.state_valid"].item() is False
    assert opened[0]["raw.action_valid"].item() is False
    assert opened[0]["observation.images.top"].dtype == torch.float32
    sidecars = [json.loads(line) for line in (final / "provenance/frames.jsonl").read_text().splitlines()]
    assert sidecars[0]["raw_frame"]["feedback"]["force"]["value"] is None
    assert sidecars[0]["raw_frame"]["action_timestamp_ns"] > sidecars[0]["raw_frame"]["state_timestamp_ns"]
    again = export_dataset(raw, final, {"allow_test_data": True})
    assert again["reused_existing"] is True
    assert verify_official_export(final)["frames_opened_and_verified"] == 6
    sidecar_path = final / "provenance/frames.jsonl"
    original_sidecar = sidecar_path.read_bytes()
    sidecar_path.write_bytes(original_sidecar+b"\n")
    with pytest.raises(ExportError, match="sidecar hash"):
        verify_official_export(final)
    sidecar_path.write_bytes(original_sidecar)
    # The image-backed official dataset stays readable without original PNG files.
    old = raw.with_name(raw.name + "_moved")
    raw.rename(old)
    independent = LeRobotDataset(result["repo_id"], root=final, video_backend="pyav", return_uint8=True)
    assert independent[0]["observation.images.wrist"].shape == (3, 16, 24)


def test_corrupt_image_future_timestamp_and_invalid_hardware_fail_closed(tmp_path):
    raw = raw_fixture(tmp_path / "raw")
    image = raw / "episodes/ep_0/images/top/00000000.png"
    image.write_bytes(b"corrupt")
    with pytest.raises(ExportError, match="hash"):
        validate_raw_dataset(raw, allow_test_data=True)
    raw2 = raw_fixture(tmp_path / "raw2")
    metadata = json.loads((raw2 / "dataset.json").read_text())
    metadata["source"] = "hardware"
    (raw2 / "dataset.json").write_text(json.dumps(metadata))
    with pytest.raises(ExportError, match="Смешаны"):
        validate_raw_dataset(raw2)
    raw3 = raw_fixture(tmp_path / "raw3")
    episode = raw3 / "episodes/ep_0"
    records = [json.loads(line) for line in (episode / "frames.jsonl").read_text().splitlines()]
    records[0]["cameras"]["top"]["timestamp_ns"] = records[0]["timestamp_ns"]+1
    (episode / "frames.jsonl").write_text("".join(json.dumps(record)+"\n" for record in records))
    info = json.loads((episode / "episode.json").read_text())
    info["frames_sha256"] = sha256_file(episode / "frames.jsonl")
    (episode / "episode.json").write_text(json.dumps(info))
    with pytest.raises(ExportError, match="будущего"):
        validate_raw_dataset(raw3, allow_test_data=True)


def test_atomic_stop_leaves_only_incomplete_export(tmp_path):
    raw = raw_fixture(tmp_path / "raw")
    final = tmp_path / "official_lerobot"
    states = []
    def progress(event):
        states.append(event["state"])
    with pytest.raises(ExportStopped):
        export_dataset(raw, final, {"allow_test_data": True}, progress=progress,
                       stop=lambda: "exporting_lerobot" in states)
    assert not final.exists()
    incomplete = list(tmp_path.glob(".official_lerobot.partial-*"))
    assert len(incomplete) == 1
    status = json.loads((incomplete[0] / "INCOMPLETE_EXPORT.json").read_text())
    assert status["status"] == "stopped"
    assert (incomplete[0] / "export-traceback.log").exists()
    assert len(list(raw.glob("episodes/*/episode.json"))) == 2


def test_no_partial_or_mixed_fps_export(tmp_path):
    raw = raw_fixture(tmp_path / "raw")
    unfinished = raw / ".partial/ep_current.partial"
    unfinished.mkdir(parents=True)
    with pytest.raises(ExportError, match="незавершённая"):
        export_dataset(raw, tmp_path / "no_export", {"allow_test_data": True})
    unfinished.rmdir()
    info_path = raw / "episodes/ep_1/episode.json"
    info = json.loads(info_path.read_text())
    info["fps_requested"] = 15
    info_path.write_text(json.dumps(info))
    with pytest.raises(ExportError, match="разный requested FPS"):
        validate_raw_dataset(raw, allow_test_data=True)
