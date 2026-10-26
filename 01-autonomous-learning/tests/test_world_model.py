"""Causal/split/checkpoint tests; fixtures do not assert physical task success."""
import copy
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from explorer_sim.world_model import CompactDynamics, Transitions, partition, run_world_model, sha


def fixture(directory):
    directory.mkdir()
    manifest = {"format": "explorer_robot_episode_v1", "dataset_id": "fixture-not-hardware",
                "contains_physical_recordings": False, "state_basis": "measured_state",
                "clock_domain": "fixture-only", "action_names": ["x", "y"], "action_units": ["deg", "deg"],
                "action_normalization": {"low": [-2, -2], "high": [2, 2]}, "episodes": []}
    for e in range(4):
        rgb = np.random.default_rng(e).integers(0, 255, (6, 16, 16, 3), dtype=np.uint8)
        state = np.stack([np.linspace(-1, 1, 6), np.full(6, e / 5)], axis=1).astype(np.float32)
        times = np.arange(6, dtype=np.int64) * 100_000_000 + 1_000_000_000
        path = directory / f"episode_{e}.npz"
        np.savez_compressed(path, rgb=rgb, state=state, state_native=state, action_native=state + .2,
                            frame_monotonic_ns=times, state_monotonic_ns=times + 10_000_000,
                            action_monotonic_ns=times + 20_000_000,
                            evaluator_goal_xyz=np.full((6, 3), np.nan))
        manifest["episodes"].append({"id": f"ep{e}", "file": path.name, "sha256": sha(path), "frames": 6,
                                     "split": "train" if e < 2 else "development",
                                     "outcome": {"success": e != 1}})
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


class LocalStore:
    def __init__(self, root):
        self.root, self.updates = root, []

    def update_job(self, ident, **values):
        self.updates.append((ident, values))


def test_targets_are_separate_and_transitions_do_not_cross_episodes(tmp_path):
    path, manifest = fixture(tmp_path / "data")
    selected, _ = partition(manifest, {})
    data = Transitions(path, manifest, selected["train"], 16, 100)
    assert len(data) == 10  # two episodes * (six observations - one)
    row = data[0]
    assert set(row["inputs"]) == {"rgb", "state", "action"}
    assert set(row["targets"]) == {"next_rgb", "next_state"}
    torch.manual_seed(1)
    model = CompactDynamics([-2, -2], [2, 2], 16)
    before = model(**{k: v[None] for k, v in row["inputs"].items()})
    row["targets"]["next_rgb"].fill_(1)
    after = model(**{k: v[None] for k, v in row["inputs"].items()})
    assert torch.equal(before["next_rgb"], after["next_rgb"])
    assert data[4]["frame_index"] == 4 and data[5]["episode_id"] == "ep1"
    assert data.episodes[1]["descriptor"]["outcome"]["success"] is False


def test_sealed_overlap_alias_and_session_rejection(tmp_path):
    _, manifest = fixture(tmp_path / "data")
    sealed = copy.deepcopy(manifest)
    sealed["episodes"][2]["split"] = "sealed_test"
    with pytest.raises(ValueError, match="Sealed"):
        partition(sealed, {"development_episode_ids": ["ep2"]})
    with pytest.raises(ValueError, match="overlap"):
        partition(manifest, {"training_episode_ids": ["ep0"], "development_episode_ids": ["ep0"]})
    duplicate = copy.deepcopy(manifest)
    duplicate["episodes"][2]["sha256"] = duplicate["episodes"][0]["sha256"]
    with pytest.raises(ValueError, match="overlaps"):
        partition(duplicate, {})
    frame = copy.deepcopy(manifest)
    frame["episodes"][0]["frame_source_ids"] = ["same-native-frame"]
    frame["episodes"][2]["frame_source_ids"] = ["same-native-frame"]
    with pytest.raises(ValueError, match="frame overlaps"):
        partition(frame, {})
    session = copy.deepcopy(manifest)
    session["episodes"][0]["source_session_id"] = "same-session"
    session["episodes"][2]["source_session_id"] = "same-session"
    with pytest.raises(ValueError, match="session"):
        partition(session, {})


def test_explicit_reservation_does_not_change_original_splits(tmp_path):
    _, manifest = fixture(tmp_path / "data")
    for episode in manifest["episodes"]:
        episode["split"] = "imitation_train"
    original = copy.deepcopy(manifest)
    with pytest.raises(ValueError, match="Reserve"):
        partition(manifest, {})
    selected, splits = partition(manifest, {"development_episode_ids": ["ep3"]})
    assert len(selected["train"]) == 3 and splits[-1]["split"] == "development"
    assert manifest == original


def test_future_frame_timing_is_checked_not_grid_index_alone(tmp_path):
    path, manifest = fixture(tmp_path / "data")
    descriptor = manifest["episodes"][0]
    npz = path.parent / descriptor["file"]
    with np.load(npz) as arrays:
        content = {k: arrays[k].copy() for k in arrays.files}
    # A repeated past camera capture on a later conversion grid is not a future observation.
    content["frame_monotonic_ns"][1] = content["frame_monotonic_ns"][0]
    np.savez_compressed(npz, **content)
    descriptor["sha256"] = sha(npz)
    data = Transitions(path, manifest, [descriptor], 16, 100)
    assert data.dropped_nonfuture == 1 and len(data) == 4
    assert data[0]["frame_index"] == 1
    content["action_monotonic_ns"][0] = content["frame_monotonic_ns"][0] - 1
    np.savez_compressed(npz, **content)
    descriptor["sha256"] = sha(npz)
    with pytest.raises(ValueError, match="Causal mismatch"):
        Transitions(path, manifest, [descriptor], 16, 100)


def test_action_agnostic_model_cannot_see_action():
    torch.manual_seed(5)
    model = CompactDynamics([-2, -2], [2, 2], 16, False)
    # Make heads nonzero so the test covers dependency, not just zero initialization.
    torch.nn.init.normal_(model.state_head.weight)
    torch.nn.init.normal_(model.decoder[-1].weight)
    rgb, state = torch.rand(1, 3, 16, 16), torch.zeros(1, 2)
    first = model(rgb, state, torch.zeros(1, 2))
    second = model(rgb, state, torch.ones(1, 2))
    assert all(torch.equal(first[k], second[k]) for k in first)


def test_actual_optimizer_reload_source_immutability_and_idempotence(tmp_path):
    path, manifest = fixture(tmp_path / "data")
    initial = path.read_bytes()
    store = LocalStore(tmp_path / "state")
    job = {"id": "offline-check", "config": {"dataset_manifest": str(path), "optimizer_steps": 3,
                                             "batch_size": 2, "image_size": 16}}
    result = run_world_model(store, job)
    assert len(store.updates) == 6
    assert set(result["evaluation"]) == {"action_conditioned", "action_agnostic", "persistence"}
    assert result["source"]["state_basis"] == "measured_state"
    for optimized in result["optimization"].values():
        assert optimized["weights_changed"] and optimized["reload_max_abs_error"] == 0
        assert optimized["optimizer_restored_steps"] == [3]
    assert result["source_transitions"]["train"]["used"] == 10
    assert result["evaluation"]["persistence"]["episodes"] == 2
    assert result["physical_io"] is False and result["task_success_claimed"] is False
    assert path.read_bytes() == initial
    assert run_world_model(store, job) == result and len(store.updates) == 6
    modified = copy.deepcopy(job)
    modified["config"]["optimizer_steps"] = 4
    with pytest.raises(ValueError, match="different sources"):
        run_world_model(store, modified)
    checkpoint = next(iter(result["artifact_sha256"]))
    (store.root / "jobs" / job["id"] / checkpoint).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="checksum"):
        run_world_model(store, job)
