"""Focused actor-data isolation, actual CAD dynamics and training smoke tests."""
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from explorer_sim.pilot import parameters, ReachEnv, sha, completed_result, preserve_interrupted_attempt
from explorer_sim.student import EpisodeChunks, IMAGE_KEY
from explorer_sim.dataset_export import validate_splits, load_episode


def test_parameter_validation_rejects_nontraining_requests():
    assert parameters({})["device"] == "cpu"
    assert parameters({"act_steps": 0})["act_steps"] == 0
    for value in (True, -1, 0, float("nan"), "4096"):
        with pytest.raises(ValueError):
            parameters({"teacher_steps": value})


def dataset_episode(path, start, count=3):
    np.savez_compressed(path, rgb=np.zeros((count, 32, 32, 3), dtype=np.uint8),
        state=np.full((count, 9), start, np.float32), action=np.full((count, 9), start, np.float32),
        evaluator_goal_xyz=np.full((count, 3), 9999.0))
    return {"file": path.name, "sha256": sha(path)}


def test_act_chunks_never_cross_episodes_or_expose_privileged_state(tmp_path):
    one = dataset_episode(tmp_path / "one.npz", 1)
    two = dataset_episode(tmp_path / "two.npz", 2)
    samples = EpisodeChunks(tmp_path, {"episodes": [one, two]}, chunk_size=4)
    value = samples[2]
    assert set(value) == {IMAGE_KEY, "observation.state", "action", "action_is_pad"}
    assert value[IMAGE_KEY].shape == (3, 32, 32)
    assert value["action_is_pad"].tolist() == [False, True, True, True]
    assert value["action"].unique().tolist() == [1]
    assert samples[3]["action"].unique().tolist() == [2]


def test_act_data_checksum_checked_before_training(tmp_path):
    descriptor = dataset_episode(tmp_path / "one.npz", 1)
    (tmp_path / "one.npz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        EpisodeChunks(tmp_path, {"episodes": [descriptor]}, 4)


def test_failed_teacher_rollouts_remain_on_disk_but_are_not_expert_targets(tmp_path):
    success=dataset_episode(tmp_path/'success.npz',1)
    failure=dataset_episode(tmp_path/'failure.npz',2)
    success.update(id='successful',outcome={'success':True})
    failure.update(id='failed',outcome={'success':False})
    samples=EpisodeChunks(tmp_path,{'episodes':[success,failure]},4)
    assert len(samples)==3
    assert samples.excluded_failed_episode_ids==['failed']
    assert (tmp_path/'failure.npz').is_file()


def test_interrupted_attempt_preserves_prior_evidence_and_worker_log(tmp_path):
    (tmp_path / "provenance.json").write_text("{}")
    (tmp_path / "worker.log").write_text("live worker output")
    (tmp_path / "dataset").mkdir()
    (tmp_path / "dataset/episode.npz").write_bytes(b"prior evidence")
    preserve_interrupted_attempt(tmp_path)
    archives = list((tmp_path / "interrupted_attempts").iterdir())
    assert len(archives) == 1
    assert (archives[0] / "dataset/episode.npz").read_bytes() == b"prior evidence"
    assert (tmp_path / "worker.log").read_text() == "live worker output"
    assert not (tmp_path / "provenance.json").exists()


def test_dataset_splits_reject_reused_source_episode():
    manifest = {"episodes": [{"id": "one", "file": "one.npz", "sha256": "abc", "split": "train"},
                             {"id": "copy", "file": "copy.npz", "sha256": "abc", "split": "sealed_test"}]}
    with pytest.raises(ValueError, match="overlaps"):
        validate_splits(manifest)


def test_official_export_rejects_noncausal_action_alignment(tmp_path):
    from explorer_sim.pilot import LOW, HIGH, HOME
    path = tmp_path / "bad.npz"
    state = np.repeat(HOME[None], 3, axis=0)
    action = state.copy()
    action[0, 0] += 5
    np.savez_compressed(path, rgb=np.zeros((3, 32, 32, 3), np.uint8),
                        state=2 * (state - LOW) / (HIGH - LOW) - 1,
                        state_command9=state, command9=action)
    manifest = {"action_normalization": {"low": LOW.tolist(), "high": HIGH.tolist()}}
    with pytest.raises(ValueError, match="Causal"):
        load_episode(tmp_path / "manifest.json", {"file": path.name, "sha256": sha(path)}, manifest)


@pytest.fixture
def cad_meshes():
    folder = os.environ.get("EXPLORER_SIM_MESH_DIR")
    if not folder:
        pytest.skip("Set EXPLORER_SIM_MESH_DIR to checksum-verified real CAD assets")
    return folder


def test_actual_cad_robot_and_deterministic_nontrivial_goal(cad_meshes):
    env = ReachEnv(cad_meshes, horizon=8)
    try:
        assert env.model.nmesh == 20
        assert env.model.nq == 70
        assert env.model.nu == 10
        assert env.model.equality("reach_base_fixture").id >= 0
        first, info = env.reset(seed=42)
        assert info["distance_m"] >= .04
        goal = env.goal.copy()
        assert first.shape == (21,)
        assert np.isfinite(first).all()
        env.reset(seed=42)
        assert np.array_equal(env.goal, goal)
        before = env.data.qpos.copy()
        observation, reward, terminal, truncated, info = env.step(np.array([.5, .5, -.5]))
        assert np.isfinite(observation).all()
        assert np.isfinite(reward)
        assert not np.array_equal(before, env.data.qpos)
        assert info["physical_run"] is False
    finally:
        env.close()


@pytest.mark.skipif(os.environ.get("EXPLORER_RUN_SIM_INTEGRATION") != "1",
                    reason="Explicit real MuJoCo/PPO/ACT integration run")
def test_actual_optimizer_checkpoint_reload_and_independent_rollouts(tmp_path, cad_meshes):
    from explorer_sim.pilot import run_pilot

    class Store:
        root = tmp_path
        cards = []

        def update_job(self, *_args, **_kwargs):
            pass

        def skills(self):
            return self.cards

        def add_skill(self, card):
            self.cards.append(card)

    store = Store()
    result = run_pilot(store, {"id": "integration", "config": {"teacher_steps": 4096,
        "demonstration_episodes": 12, "evaluation_episodes": 1, "horizon": 60,
        "act_steps": 2, "batch_size": 2}}, Path(__file__).resolve().parents[2])
    assert result["teacher"]["weights_changed"]
    assert result["teacher"]["optimizer_updates"] > 0
    assert result["teacher"]["save_reload_passed"]
    assert result["student"]["trained"]
    assert result["student"]["weights_changed"]
    assert result["student"]["save_reload_passed"]
    assert result["student"]["official_module"] == "lerobot.policies.act.modeling_act"
    assert result["dataset"]["official_export"]["official_readback_passed"]
    assert store.cards[0]["evaluation"]["physical_run_ids"] == []
    manifest = json.loads((tmp_path / "jobs/integration/dataset/manifest.json").read_text())
    data_seeds = {item["seed"] for item in manifest["episodes"]}
    evaluation_seeds = {item["seed"] for item in result["student"]["evaluation"]["episodes"]}
    assert not data_seeds.intersection(evaluation_seeds)
    teacher_mtime = (tmp_path / "jobs/integration/teacher_ppo.zip").stat().st_mtime_ns
    from explorer_sim.pilot import completed_result
    saved = completed_result(tmp_path / "jobs/integration")
    assert saved["teacher"]["sha256"] == result["teacher"]["sha256"]
    assert (tmp_path / "jobs/integration/teacher_ppo.zip").stat().st_mtime_ns == teacher_mtime
