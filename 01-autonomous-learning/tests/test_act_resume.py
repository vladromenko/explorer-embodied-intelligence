"""Restart equivalence uses stochastic batches and actual official ACT training."""
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from torch import nn
from torch.utils.data import Dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from explorer_sim import student
from explorer_sim.pilot import preserve_interrupted_attempt, sha
from explorer_sim.recorded_training import RecordedChunks
from explorer_sim.student import make_act_policy, optimize_act


def seed(value):
    random.seed(value); np.random.seed(value); torch.manual_seed(value)
    torch.set_num_threads(4)


class StochasticRows(Dataset):
    def __init__(self, source):
        self.rows = np.load(source)
        self.resume_context = {"source_signature": sha(source), "source_files": [{"path": str(source), "sha256": sha(source)}]}

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        # Covers Python, global NumPy and Torch CPU RNG, plus model dropout.
        noise = random.random() + float(np.random.normal()) + torch.rand(()).item()
        x = torch.tensor(self.rows[index], dtype=torch.float32) + .01 * noise
        return {"x": x, "target": x[:1] * 2 - x[1:]}


class StochasticPolicy(nn.Module):
    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(optimizer_lr=.001)
        self.layers = nn.Sequential(nn.Linear(2, 12), nn.ReLU(), nn.Dropout(.3), nn.Linear(12, 1))

    def get_optim_params(self):
        return self.parameters()

    def forward(self, batch):
        loss = (self.layers(batch["x"]) - batch["target"]).square().mean()
        return loss, {"actual_mse": loss.detach()}


def same(a, b):
    if isinstance(a, torch.Tensor):
        assert torch.equal(a, b)
    elif isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a:
            same(a[key], b[key])
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            same(x, y)
    else:
        assert a == b


def toy(tmp_path, name):
    source = tmp_path / "immutable.npy"
    if not source.exists():
        np.save(source, np.arange(14, dtype=np.float32).reshape(7, 2) / 20)
    seed(71)
    return StochasticPolicy(), StochasticRows(source), {"device": "cpu", "seed": 23, "batch_size": 3, "act_steps": 12}, tmp_path / name


def logger(*args):
    # Logging must not perturb the learner's random sequence, even here.
    random.random(); np.random.normal(); torch.rand(())


def interrupt_at(folder, step):
    def update(*args):
        logger()
        if json.loads((folder / "act_resume/latest.json").read_text())["completed_steps"] == step:
            raise RuntimeError("injected worker interruption")
    return update


def test_batch_plan_preserves_existing_dataloader_order_and_global_rng():
    loader = torch.utils.data.DataLoader(range(7), batch_size=3, shuffle=True, num_workers=0,
                                        generator=torch.Generator().manual_seed(23))
    expected = [batch.tolist() for _ in range(3) for batch in loader]
    plan = student._BatchPlan(7, 3, 23)
    before = torch.get_rng_state()
    assert [plan.next() for _ in expected] == expected
    assert torch.equal(before, torch.get_rng_state())


def test_duplicate_optimizer_writer_is_rejected(tmp_path):
    policy, rows, config, folder = toy(tmp_path, "owned")
    (folder / "act_resume").mkdir(parents=True)
    with (folder / "act_resume/optimizer.lock").open("a") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Another ACT optimizer"):
            optimize_act(policy, rows, config, folder, logger)


def test_stochastic_cpu_restart_matches_uninterrupted_bit_for_bit(tmp_path):
    reference, rows, config, folder = toy(tmp_path, "continuous")
    ref_optimizer, ref_losses, _ = optimize_act(reference, rows, config, folder, logger, checkpoint_interval=4)
    interrupted, rows, config, folder = toy(tmp_path, "interrupted")
    with pytest.raises(RuntimeError, match="injected"):
        optimize_act(interrupted, rows, config, folder, interrupt_at(folder, 4), checkpoint_interval=4)
    seed(999)
    restarted = StochasticPolicy()  # Different initialization must be replaced by the checkpoint.
    fresh_rows = StochasticRows(tmp_path / "immutable.npy")
    optimizer, losses, _ = optimize_act(restarted, fresh_rows, config, folder, logger, checkpoint_interval=4)
    same(reference.state_dict(), restarted.state_dict())
    same(ref_optimizer.state_dict(), optimizer.state_dict())
    same(ref_losses, losses)
    assert optimizer.explorer_resume_info["resumed_from_step"] == 4
    assert optimizer.explorer_resume_info["optimizer_steps_executed_this_process"] == 8
    assert len(list((folder / "act_resume").glob("state-*.pt"))) == 2


def test_changed_sources_settings_or_checkpoint_fail_without_overwriting(tmp_path):
    policy, rows, config, folder = toy(tmp_path, "interrupted")
    with pytest.raises(RuntimeError, match="injected"):
        optimize_act(policy, rows, config, folder, interrupt_at(folder, 4), checkpoint_interval=4)
    pointer = (folder / "act_resume/latest.json").read_bytes()
    source = tmp_path / "immutable.npy"
    original = source.read_bytes(); source.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="source checksum changed"):
        optimize_act(StochasticPolicy(), rows, config, folder, logger, checkpoint_interval=4)
    source.write_bytes(original)
    with pytest.raises(ValueError, match="signature changed"):
        optimize_act(StochasticPolicy(), rows, dict(config, act_steps=13), folder, logger, checkpoint_interval=4)
    assert (folder / "act_resume/latest.json").read_bytes() == pointer
    path = folder / "act_resume" / json.loads(pointer)["checkpoint"]
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum mismatch"):
        optimize_act(StochasticPolicy(), rows, config, folder, logger, checkpoint_interval=4)
    assert (folder / "act_resume/latest.json").read_bytes() == pointer


def test_atomic_pointer_failure_keeps_last_durable_state(tmp_path, monkeypatch):
    reference, rows, config, folder = toy(tmp_path, "reference")
    _, expected, _ = optimize_act(reference, rows, config, folder, logger, checkpoint_interval=4)
    policy, rows, config, folder = toy(tmp_path, "broken-pointer")
    real_replace = student.os.replace
    def broken_replace(src, dst):
        dst = Path(dst)
        if (dst == folder / "act_resume/latest.json" and dst.is_file()
                and json.loads(dst.read_text())["completed_steps"] == 4):
            raise OSError("injected pointer commit failure")
        return real_replace(src, dst)
    with monkeypatch.context() as patch:
        patch.setattr(student.os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected pointer"):
            optimize_act(policy, rows, config, folder, logger, checkpoint_interval=4)
    assert json.loads((folder / "act_resume/latest.json").read_text())["completed_steps"] == 4
    seed(991)
    restored = StochasticPolicy()
    optimizer, actual, _ = optimize_act(restored, rows, config, folder, logger, checkpoint_interval=4)
    same(reference.state_dict(), restored.state_dict()); same(expected, actual)
    assert optimizer.explorer_resume_info["non_durable_updates_may_be_replayed"]


def official_dataset(path):
    path.mkdir()
    frames, dimension = 4, 6
    rgb = np.random.default_rng(16).integers(0, 255, (frames, 32, 32, 3), dtype=np.uint8)
    state = np.arange(frames, dtype=np.float32)[:, None] + np.full((frames, dimension), 40, np.float32)
    times = np.arange(frames, dtype=np.int64) * 100_000_000 + 1_000_000_000
    source = path / "episode.npz"
    np.savez_compressed(source, rgb=rgb, state=state, state_native=state, action_native=state + .5,
                        frame_monotonic_ns=times, state_monotonic_ns=times + 10_000_000,
                        action_monotonic_ns=times + 20_000_000)
    manifest = {"format": "explorer_robot_episode_v1", "dataset_id": "official-act-resume-fixture",
        "state_basis": "measured_state", "clock_domain": "fixture-only", "action_names": [f"j{i}" for i in range(6)],
        "action_normalization": {"low": [0] * 6, "high": [100] * 6}, "episodes": [
            {"id": "fixture", "file": source.name, "sha256": sha(source), "frames": frames, "split": "train", "outcome": "success"}]}
    target = path / "manifest.json"; target.write_text(json.dumps(manifest))
    return target, manifest


def test_actual_official_act_resume_preserves_optimizer_rng_and_originals(tmp_path):
    pytest.importorskip("lerobot")
    path, manifest = official_dataset(tmp_path / "data")
    originals = {p.name: sha(p) for p in path.parent.iterdir()}
    config = {"device": "cpu", "seed": 41, "batch_size": 2, "act_steps": 4, "image_size": 32, "chunk_size": 2}
    samples = RecordedChunks(path, manifest, manifest["episodes"], 2, 32)
    seed(41); reference = make_act_policy(config, 6, 6)
    expected_optimizer, expected_losses, _ = optimize_act(reference, samples, config, tmp_path / "full", logger, checkpoint_interval=2)
    seed(41); policy = make_act_policy(config, 6, 6)
    folder = tmp_path / "restart"; folder.mkdir()
    (folder / "provenance.json").write_text("{}")
    with pytest.raises(RuntimeError, match="injected"):
        optimize_act(policy, samples, config, folder, interrupt_at(folder, 2), checkpoint_interval=2)
    pointer = (folder / "act_resume/latest.json").read_bytes()
    preserve_interrupted_attempt(folder)
    assert (folder / "act_resume/latest.json").read_bytes() == pointer
    seed(888); restored = make_act_policy(config, 6, 6)
    fresh_samples = RecordedChunks(path, manifest, manifest["episodes"], 2, 32)
    optimizer, losses, _ = optimize_act(restored, fresh_samples, config, folder, logger, checkpoint_interval=2)
    same(reference.state_dict(), restored.state_dict())
    same(expected_optimizer.state_dict(), optimizer.state_dict()); same(expected_losses, losses)
    assert optimizer.explorer_resume_info["resumed_from_step"] == 2
    assert optimizer.explorer_resume_info["optimizer_steps_executed_this_process"] == 2
    assert originals == {p.name: sha(p) for p in path.parent.iterdir()}
