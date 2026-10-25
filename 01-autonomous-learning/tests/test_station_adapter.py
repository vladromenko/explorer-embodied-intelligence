"""Pure station conversion, six-dimensional ACT and timing provenance checks."""
import copy
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from explorer_sim.dataset_export import export_lerobot_v3, load_episode, validate_splits
from explorer_sim.pilot import sha, write_json
from explorer_sim.recorded_training import train_recorded_act
from explorer_sim.station_adapter import convert_station_episode


def station_fixture(directory, dimension=6, episode_id="station-fixture", split="train"):
    directory.mkdir(parents=True)
    samples = []
    for index in range(4):
        path = directory / f"frame-{index}.png"
        Image.fromarray(np.full((24, 32, 3), index * 20, np.uint8)).save(path)
        timestamp = 1_000_000_000 + index * 100_000_000
        state = np.full(dimension, index, np.float32)
        action = state + 4  # observed lag: next measured state != previous command
        samples.append({"frame": {"file": path.name, "sha256": sha(path),
                        "frame_id": episode_id + ":" + str(index), "monotonic_ns": timestamp,
                        "clock_domain": "pi:fixture-boot"}, "state": state.tolist(), "action": action.tolist(),
                        "state_monotonic_ns": timestamp + 10_000_000,
                        "action_monotonic_ns": timestamp + 20_000_000, "clock_domain": "pi:fixture-boot"})
    source = {"schema_version": 1, "episode_id": episode_id, "source_type": "station_demonstration",
              "state_basis": "measured_state", "action_basis": "dispatched_command",
              "action_names": ["joint_" + str(index) for index in range(dimension)],
              "action_units": ["calibrated_normalized_-100_100"] * dimension,
              "normalization": {"low": [-100] * dimension, "high": [100] * dimension},
              "fps": 10, "samples": samples, "split": split, "follower_calibration_sha256": "a" * 64,
              "outcome": {"source": "software_fixture_not_physical_test"}}
    path = directory / "station.json"
    write_json(path, source)
    return path, source


@pytest.mark.parametrize("dimension", [6, 9])
def test_native_state_lag_and_source_hashes_preserved(tmp_path, dimension):
    source_path, source = station_fixture(tmp_path / "source", dimension)
    digest = sha(source_path)
    manifest = convert_station_episode(source_path, tmp_path / "converted")
    assert manifest["state_basis"] == "measured_state"
    assert manifest["physical_success_inferred"] is False
    assert manifest["source_calibration_metadata"]["follower_calibration_sha256"] == "a" * 64
    assert sha(source_path) == digest
    episode = load_episode(tmp_path / "converted/manifest.json", manifest["episodes"][0], manifest)
    assert episode["state"].shape == (4, dimension)
    assert not np.array_equal(episode["state"][1:], episode["action"][:-1])
    assert np.array_equal(episode["action"][0], np.full(dimension, 4))
    assert not (tmp_path / "converted/.rgb-memory.npy").exists()


@pytest.mark.parametrize("change", ["requested_action", "mixed_clock", "future_state", "bad_frame_hash"])
def test_invalid_station_labels_rejected_without_output(tmp_path, change):
    path, source = station_fixture(tmp_path / "source")
    if change == "requested_action":
        source["action_basis"] = "operator_input"
    elif change == "mixed_clock":
        source["samples"][0]["clock_domain"] = "another-boot"
    elif change == "future_state":
        source["samples"][0]["state_monotonic_ns"] += 1_000_000_000
    else:
        source["samples"][0]["frame"]["sha256"] = "0" * 64
    write_json(path, source)
    with pytest.raises(ValueError):
        convert_station_episode(path, tmp_path / "converted")
    assert not (tmp_path / "converted").exists()


def test_reused_source_camera_frame_cannot_cross_splits():
    with pytest.raises(ValueError, match="camera frame"):
        validate_splits({"clock_domain": "boot", "episodes": [
            {"id": "a", "file": "a.npz", "sha256": "aaa", "split": "train", "frame_source_ids": ["one"]},
            {"id": "b", "file": "b.npz", "sha256": "bbb", "split": "development", "frame_source_ids": ["one"]}]})


def test_training_requires_action_labelled_inputs_before_starting_any_pilot(tmp_path):
    class Store:
        root = tmp_path
    with pytest.raises(ValueError, match="dataset_manifest"):
        train_recorded_act(Store(), {"id": "empty", "config": {}})
    assert not (tmp_path / "jobs").exists()


def change_native_offsets(path, source, offsets_ms):
    for index, offset in enumerate(offsets_ms):
        timestamp = 1_000_000_000 + offset * 1_000_000
        source["samples"][index]["frame"]["monotonic_ns"] = timestamp
        source["samples"][index]["state_monotonic_ns"] = timestamp + 10_000_000
        source["samples"][index]["action_monotonic_ns"] = timestamp + 20_000_000
    write_json(path, source)


def test_jitter_uses_only_past_samples_and_retains_every_native_input(tmp_path):
    path, source = station_fixture(tmp_path / "source")
    change_native_offsets(path, source, [0, 140, 210, 310])
    digest = sha(path)
    target = tmp_path / "converted"
    manifest = convert_station_episode(path, target)
    descriptor = manifest["episodes"][0]
    assert descriptor["source_indices"] == [0, 0, 1, 2]  # Future index 1 is closer to grid slot 1, but unavailable.
    loaded = load_episode(target / "manifest.json", descriptor, manifest)
    np.testing.assert_array_equal(loaded["state"][:, 0], [0, 0, 1, 2])
    np.testing.assert_array_equal(loaded["action"][:, 0], [4, 4, 5, 6])
    assert np.all(loaded["timestamps"]["action_monotonic_ns"] <= loaded["grid_monotonic_ns"])
    assert loaded["timestamps"]["action_monotonic_ns"][0] == loaded["timestamps"]["action_monotonic_ns"][1]
    assert manifest["resampling"]["applied"]
    assert manifest["resampling"]["segments"][0]["dropped_source_indices"] == [3]
    with np.load(target / "source_originals/native_samples.npz") as original:
        np.testing.assert_array_equal(original["state_native"][:, 0], [0, 1, 2, 3])
        np.testing.assert_array_equal(original["action_monotonic_ns"], [sample["action_monotonic_ns"] for sample in source["samples"]])
    for item in manifest["source_files"]:
        assert sha(target / item["retained_file"]) == item["sha256"]
    assert sha(path) == sha(target / "source_originals/manifest.json") == digest
    assert "not new measurements" in manifest["resampling"]["hold_semantics"]


def test_long_gaps_split_episodes_without_changing_outcome_or_sealed_split(tmp_path):
    path, source = station_fixture(tmp_path / "source", split="sealed_test")
    source["outcome"] = {"outcome": "failure", "source": "operator"}
    change_native_offsets(path, source, [0, 100, 900, 1000])
    target = tmp_path / "converted"
    manifest = convert_station_episode(path, target)
    assert len(manifest["episodes"]) == 2
    assert manifest["resampling"]["applied"] and manifest["resampling"]["segmented"]
    assert manifest["resampling"]["original_count"] == manifest["resampling"]["converted_count"] == 4
    assert manifest["resampling"]["gaps"][0]["gap_ns"] == 800_000_000
    assert [item["source_indices"] for item in manifest["episodes"]] == [[0, 1], [2, 3]]
    for descriptor in manifest["episodes"]:
        assert descriptor["split"] == "sealed_test" and descriptor["outcome"] == source["outcome"]
        loaded = load_episode(target / "manifest.json", descriptor, manifest)
        assert np.array_equal(np.diff(loaded["grid_monotonic_ns"]), [100_000_000])
    assert convert_station_episode(path, target) == manifest
    assert not manifest["physical_success_inferred"]


@pytest.mark.parametrize("outcome", ["failure", "unknown"])
def test_non_success_outcomes_remain_exact_labels(tmp_path, outcome):
    path, source = station_fixture(tmp_path / "source", split="sealed_test")
    source["outcome"] = outcome
    change_native_offsets(path, source, [0, 140, 900, 1010])
    manifest = convert_station_episode(path, tmp_path / "converted")
    assert all(item["outcome"] == outcome and item["split"] == "sealed_test" for item in manifest["episodes"])
    assert not manifest["physical_success_inferred"]


def test_uniform_conversion_preserves_native_times_values_and_all_artifact_hashes(tmp_path):
    path, source = station_fixture(tmp_path / "source")
    target = tmp_path / "converted"
    manifest = convert_station_episode(path, target)
    assert manifest["resampling"]["applied"] is False
    loaded = load_episode(target / "manifest.json", manifest["episodes"][0], manifest)
    np.testing.assert_array_equal(loaded["grid_monotonic_ns"], [sample["action_monotonic_ns"] for sample in source["samples"]])
    np.testing.assert_array_equal(loaded["state"], [sample["state"] for sample in source["samples"]])
    for name, digest in manifest["source_files_sha256"].items():
        assert sha(target / name) == digest
    assert convert_station_episode(path, target) == manifest


def test_future_native_action_cannot_be_hidden_by_conversion_grid(tmp_path):
    path, source = station_fixture(tmp_path / "source")
    change_native_offsets(path, source, [0, 140, 210, 310])
    target = tmp_path / "converted"
    manifest = convert_station_episode(path, target)
    descriptor = manifest["episodes"][0]
    episode_path = target / descriptor["file"]
    with np.load(episode_path) as episode:
        values = {key: episode[key].copy() for key in episode.files}
    values["action_monotonic_ns"][1] = values["grid_monotonic_ns"][1] + 1
    np.savez_compressed(episode_path, **values)
    descriptor["sha256"] = sha(episode_path)
    with pytest.raises(ValueError, match="Causal resampling"):
        load_episode(target / "manifest.json", descriptor, manifest)


def test_retry_preserves_partial_target_and_rebuilds_atomically(tmp_path):
    path, _ = station_fixture(tmp_path / "source")
    target = tmp_path / "converted"
    target.mkdir()
    (target / "episode_0000.npz").write_bytes(b"interrupted-original")
    manifest = convert_station_episode(path, target)
    remnants = list(tmp_path.glob("converted.incomplete-*"))
    assert len(remnants) == 1
    assert (remnants[0] / "episode_0000.npz").read_bytes() == b"interrupted-original"
    assert convert_station_episode(path, target) == manifest
    assert not list(tmp_path.glob(".converted.converting-*"))


def test_failed_conversion_never_publishes_partial_target(tmp_path):
    path, _ = station_fixture(tmp_path / "source")
    target = tmp_path / "converted"
    with patch("explorer_sim.station_adapter._write_segment", side_effect=RuntimeError("disk failure fixture")):
        with pytest.raises(RuntimeError, match="disk failure"):
            convert_station_episode(path, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".converted.converting-*"))
    assert convert_station_episode(path, target)["episodes"]


def test_idempotence_verifies_second_segment_not_only_first(tmp_path):
    path, source = station_fixture(tmp_path / "source")
    change_native_offsets(path, source, [0, 100, 900, 1000])
    target = tmp_path / "converted"
    manifest = convert_station_episode(path, target)
    second = target / manifest["episodes"][1]["file"]
    second.write_bytes(b"corrupt-second-segment")
    with pytest.raises(ValueError, match="checksum"):
        convert_station_episode(path, target)
    assert second.read_bytes() == b"corrupt-second-segment"


def test_idempotence_verifies_all_retained_native_files(tmp_path):
    path, _ = station_fixture(tmp_path / "source")
    target = tmp_path / "converted"
    manifest = convert_station_episode(path, target)
    retained = target / manifest["source_files"][-1]["retained_file"]
    retained.write_bytes(b"changed-original-copy")
    with pytest.raises(ValueError, match="checksum"):
        convert_station_episode(path, target)
    assert retained.read_bytes() == b"changed-original-copy"


def test_completed_conversion_is_not_overwritten_for_other_source(tmp_path):
    path, source = station_fixture(tmp_path / "source")
    target = tmp_path / "converted"
    manifest = convert_station_episode(path, target)
    source["outcome"] = "unknown"
    write_json(path, source)
    with pytest.raises(ValueError, match="different source"):
        convert_station_episode(path, target)
    assert sha(target / manifest["episodes"][0]["file"]) == manifest["episodes"][0]["sha256"]


@pytest.mark.skipif(os.environ.get("EXPLORER_RUN_SIM_INTEGRATION") != "1", reason="Official dataset/ACT integration")
@pytest.mark.parametrize("irregular", [False, True])
def test_official_six_dimensional_export_and_training_without_mujoco_execution(tmp_path, irregular):
    source_path, source = station_fixture(tmp_path / "source")
    source["outcome"] = "success"
    source["outcome_label_source"] = "software_fixture_not_physical_test"
    write_json(source_path, source)
    if irregular:
        change_native_offsets(source_path, source, [0, 140, 210, 310])
    manifest = convert_station_episode(source_path, tmp_path / "converted")
    report = export_lerobot_v3(tmp_path / "converted/manifest.json", tmp_path / "lerobot_v3")
    assert report["official_readback_passed"]
    provenance = json.loads((tmp_path / "lerobot_v3/explorer_provenance.json").read_text())
    assert provenance["native_monotonic_timestamps_preserved"]
    assert provenance["conversion_grid_preserved"]
    assert provenance["causal_resampling"]["applied"] == irregular
    assert provenance["source_original_files_sha256"] == manifest["source_files_sha256"]

    class Store:
        root = tmp_path / "training"
        cards = []

        def update_job(self, *_args, **_kwargs):
            pass

        def skills(self):
            return self.cards

        def add_skill(self, card):
            self.cards.append(card)

    with patch("mujoco.mj_step", side_effect=AssertionError("No simulated execution permitted")):
        result = train_recorded_act(Store(), {"id": "recorded", "config": {
            "dataset_manifest": str(tmp_path / "converted/manifest.json"), "act_steps": 2,
            "batch_size": 2, "image_size": 32, "chunk_size": 4}})
    assert result["feature_dimension"] == 6
    assert result["state_basis"] == "measured_state"
    assert result["weights_changed"]
    assert result["save_reload_passed"]
    assert not result["simulator_executed"]
    assert result["evaluation"]["available"] is False
    assert result["evaluation"]["physical_run_ids"] == []
