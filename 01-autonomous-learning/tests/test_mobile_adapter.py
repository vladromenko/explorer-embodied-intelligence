"""Actual-format mobile fixture conversion; no ROS, motor or camera capture calls."""
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
from explorer_sim.dataset_export import export_lerobot_v3, load_episode
from explorer_sim.mobile_adapter import (CONTRACT_SHA256, SAMPLE_CONTRACT,
    MobileEpisodeConversionError, convert_mobile_episode)
from explorer_sim.pilot import sha, write_json
from explorer_sim.recorded_training import train_recorded_act


def mobile_fixture(folder, offsets=(0, .2, .4, .6, .8), outcome="success", split="train"):
    folder.mkdir()
    episode = {"id": "a" * 32, "name": "Software fixture, no physical execution", "state": "complete",
        "source": "operator_mobile_demonstration", "joint_state_source": "commanded_not_measured",
        "format": "explorer_mobile_episode_v3", "physical_sample_rate_hz": 5,
        "sample_contract": copy.deepcopy(SAMPLE_CONTRACT), "sample_contract_sha256": CONTRACT_SHA256,
        "outcome": outcome, "label_source": "operator", "quality": {"usable": True}, "split": split}
    rows = []
    for index, offset in enumerate(offsets):
        filename = f"{index:06d}.jpg"
        Image.fromarray(np.full((24, 32, 3), index * 30, np.uint8)).save(folder / filename)
        state = [90 + index * .3] * 5 + [50 + index * .2, .02 * index, 0, 0]
        action = [100 + index] * 5 + [70 + index, .03 * index, 0, 0]
        rows.append({"schema": "explorer_mobile_sample_v3", "sample_contract": copy.deepcopy(SAMPLE_CONTRACT),
            "sample_contract_sha256": CONTRACT_SHA256, "at": 1000 + offset + .05,
            "image_stamp": 1000 + offset, "state_stamp": 1000 + offset + .04,
            "command_stamp": 1000 + offset + .02, "arm_command_sent_at": 1000 + offset + .02,
            "image_stamp_source": "onboard_camera_acquisition_estimate", "image": filename,
            "observation_state": state, "applied_action": action, "command": action,
            "executed_action_source": "operator_manual_control", "measured_arm_angles": False,
            "observation_arm_source": "command_trajectory_estimate", "velocity_source": "commanded_body_velocity",
            "issued_body_velocity": action[6:], "observed_body_velocity": [9, 9, 9]})
    write_json(folder / "episode.json", episode)
    write_rows(folder, rows)
    return folder / "episode.json", episode, rows


def write_rows(folder, rows):
    (folder / "samples.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_current_inputs_future_accepted_snapshot_and_original_bytes_preserved(tmp_path):
    path, source, rows = mobile_fixture(tmp_path / "source")
    inputs = {p.name: sha(p) for p in path.parent.iterdir()}
    target = tmp_path / "converted"
    result = convert_mobile_episode(path, target)
    assert not result["joint_measurements_claimed"] and not result["physical_success_inferred"]
    assert result["state_basis"] == "command_estimate"
    assert result["action_basis"] == "controller_reported_issued_command_snapshot"
    descriptor = result["episodes"][0]
    loaded = load_episode(target / "manifest.json", descriptor, result)
    np.testing.assert_allclose(loaded["state"][0], rows[0]["observation_state"])
    np.testing.assert_allclose(loaded["action"][0], rows[1]["applied_action"])
    assert not np.array_equal(loaded["state"][1], loaded["action"][0])  # trajectory estimate != previous goal
    with Image.open(path.parent / rows[0]["image"]) as image:
        np.testing.assert_array_equal(loaded["rgb"][0], image.convert("RGB"))
    assert loaded["grid_monotonic_ns"] is None
    assert set(loaded["timestamps"]) == {"frame_wall_unix_ns", "state_wall_unix_ns", "action_wall_unix_ns"}
    assert not result["native_timestamp_basis"]["clock_alignment_verified"]
    assert "body-command publication" in result["timestamp_uncertainty"]
    for name, digest in inputs.items():
        assert sha(path.parent / name) == sha(target / "source_originals" / name) == digest
    assert convert_mobile_episode(path, target) == result


@pytest.mark.parametrize("gap", ["missing_action", "unknown_clock", "wall_reversal", "noncausal_snapshot", "policy_source"])
def test_precise_unsupported_data_gap_leaves_originals_unchanged(tmp_path, gap):
    path, source, rows = mobile_fixture(tmp_path / "source", offsets=(0, .2))
    if gap == "missing_action":
        rows[1].pop("applied_action")
    elif gap == "unknown_clock":
        source["sample_contract"]["clock"] = "undocumented_camera_clock"
    elif gap == "wall_reversal":
        rows[1].update(at=999.95, image_stamp=999.9, state_stamp=999.94)
    elif gap == "noncausal_snapshot":
        rows[1]["state_stamp"] = rows[0]["state_stamp"]
    else:
        rows[1]["executed_action_source"] = "supervised_mobile_policy"
    write_json(path, source)
    write_rows(path.parent, rows)
    hashes = {p.name: sha(p) for p in path.parent.iterdir()}
    with pytest.raises(MobileEpisodeConversionError) as error:
        convert_mobile_episode(path, tmp_path / "converted")
    assert error.value.report["original_files_preserved"]
    assert not (tmp_path / "converted").exists()
    assert {p.name: sha(p) for p in path.parent.iterdir()} == hashes
    expected = {"missing_action": "applied_action is missing", "unknown_clock": "clocks cannot be assumed",
                "wall_reversal": "wall-clock reversal", "noncausal_snapshot": "No causal future", "policy_source": "Manual commanded-state"}
    assert expected[gap] in str(error.value)


def test_parallel_camera_state_times_allowed_but_both_precede_label(tmp_path):
    path, source, rows = mobile_fixture(tmp_path / "source")
    rows[0]["image_stamp"] = rows[0]["state_stamp"] + .001
    write_rows(path.parent, rows)
    target = tmp_path / "converted"
    manifest = convert_mobile_episode(path, target)
    loaded = load_episode(target / "manifest.json", manifest["episodes"][0], manifest)
    assert loaded["timestamps"]["frame_wall_unix_ns"][0] > loaded["timestamps"]["state_wall_unix_ns"][0]
    assert loaded["timestamps"]["frame_wall_unix_ns"][0] < loaded["timestamps"]["action_wall_unix_ns"][0]


def test_hold_snapshot_keeps_old_arm_publication_timestamp_honestly(tmp_path):
    path, source, rows = mobile_fixture(tmp_path / "source")
    for row in rows:
        row["arm_command_sent_at"] = 999
    write_rows(path.parent, rows)
    target = tmp_path / "converted"
    manifest = convert_mobile_episode(path, target)
    loaded = load_episode(target / "manifest.json", manifest["episodes"][0], manifest)
    assert np.all(loaded["auxiliary_timestamps"]["arm_command_sent_wall_unix_ns"] == 999_000_000_000)
    assert "not a newly dispatched 9D action" in manifest["native_timestamp_basis"]["action_time_definition"]


def test_jitter_selects_past_pair_and_splits_large_gaps(tmp_path):
    path, _, rows = mobile_fixture(tmp_path / "source", offsets=(0, .24, .5, 1.8, 2, 2.2), outcome="unknown", split="sealed_test")
    target = tmp_path / "converted"
    manifest = convert_mobile_episode(path, target)
    assert len(manifest["episodes"]) == 2
    assert manifest["resampling"]["gaps"]
    for descriptor in manifest["episodes"]:
        loaded = load_episode(target / "manifest.json", descriptor, manifest)
        assert np.all(loaded["timestamps"]["action_wall_unix_ns"] <= loaded["grid_wall_unix_ns"])
        assert descriptor["outcome"] == "unknown" and descriptor["split"] == "sealed_test"
    with pytest.raises(MobileEpisodeConversionError, match="sealed"):
        convert_mobile_episode(path, tmp_path / "forbidden", split="train")


def test_changed_jsonl_not_reused_and_partial_target_retained(tmp_path):
    path, _, rows = mobile_fixture(tmp_path / "source")
    target = tmp_path / "converted"
    target.mkdir()
    (target / "partial").write_bytes(b"retained interrupted conversion")
    result = convert_mobile_episode(path, target)
    assert next(tmp_path.glob("converted.incomplete-*")).joinpath("partial").read_bytes() == b"retained interrupted conversion"
    rows[0]["recognized_targets"] = {"objects": []}
    write_rows(path.parent, rows)
    with pytest.raises(MobileEpisodeConversionError, match="changed source files"):
        convert_mobile_episode(path, target)
    assert sha(target / result["episodes"][0]["file"]) == result["episodes"][0]["sha256"]


@pytest.mark.parametrize("change", ["future_input", "mixed_clocks"])
def test_loader_rejects_future_actor_input_and_mixed_native_clocks(tmp_path, change):
    path, _, _ = mobile_fixture(tmp_path / "source")
    target = tmp_path / "converted"
    manifest = convert_mobile_episode(path, target)
    descriptor = manifest["episodes"][0]
    file = target / descriptor["file"]
    with np.load(file) as episode:
        arrays = {name: episode[name].copy() for name in episode.files}
    if change == "future_input":
        arrays["frame_wall_unix_ns"][-1] = arrays["action_wall_unix_ns"][-1] + 1
    else:
        arrays["frame_monotonic_ns"] = arrays["frame_wall_unix_ns"].copy()
    np.savez_compressed(file, **arrays)
    descriptor["sha256"] = sha(file)
    with pytest.raises(ValueError, match="Causal mismatch|cannot be silently merged"):
        load_episode(target / "manifest.json", descriptor, manifest)


@pytest.mark.skipif(os.environ.get("EXPLORER_RUN_SIM_INTEGRATION") != "1", reason="Official LeRobot integration")
def test_official_nine_dimensional_wall_time_roundtrip_and_act(tmp_path):
    path, _, _ = mobile_fixture(tmp_path / "source")
    target = tmp_path / "converted"
    manifest = convert_mobile_episode(path, target)
    report = export_lerobot_v3(target / "manifest.json", tmp_path / "lerobot_v3")
    assert report["official_readback_passed"] and report["native_wall_timestamps_preserved"]
    assert not report["native_monotonic_timestamps_preserved"]
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
        result = train_recorded_act(Store(), {"id": "mobile", "config": {
            "dataset_manifest": str(target / "manifest.json"), "act_steps": 2,
            "batch_size": 2, "image_size": 32, "chunk_size": 2}})
    assert result["feature_dimension"] == 9 and result["weights_changed"] and result["save_reload_passed"]
    assert not result["simulator_executed"] and result["state_basis"] == "command_estimate"
