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


@pytest.mark.skipif(os.environ.get("EXPLORER_RUN_SIM_INTEGRATION") != "1", reason="Official dataset/ACT integration")
def test_official_six_dimensional_export_and_training_without_mujoco_execution(tmp_path):
    source_path, _ = station_fixture(tmp_path / "source")
    manifest = convert_station_episode(source_path, tmp_path / "converted")
    report = export_lerobot_v3(tmp_path / "converted/manifest.json", tmp_path / "lerobot_v3")
    assert report["official_readback_passed"]
    provenance = json.loads((tmp_path / "lerobot_v3/explorer_provenance.json").read_text())
    assert provenance["native_monotonic_timestamps_preserved"]

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
