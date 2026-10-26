"""ENGINEERING_GLUE: offline action-conditioned next-observation baseline.

No policy, simulator, device client, task-success classifier or control output is
executed. Pixel reconstruction supervises actual next RGB, not an unconstrained
learned latent target. This module makes no new algorithm or H1--H5 claim.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import re
import time
import uuid

import numpy as np
from PIL import Image, ImageDraw
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from .dataset_export import load_episode, validate_splits

TRAIN = {"train", "training", "imitation_train"}
DEV = {"development", "validation", "dev"}
SEALED = {"test", "sealed_test", "sealed_final_test"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def settings(config):
    result = {}
    for name, default, low, high in [("optimizer_steps", 120, 1, 20000),
                                    ("batch_size", 8, 1, 128), ("seed", 41, 0, 2**31 - 1),
                                    ("max_source_frames", 20000, 4, 100000)]:
        value = config.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        result[name] = value
    result["image_size"] = config.get("image_size", 32)
    result["device"] = config.get("device", "cpu")
    if (isinstance(result["image_size"], bool) or not isinstance(result["image_size"], int)
            or result["image_size"] not in (16, 32, 64) or result["device"] not in ("cpu", "cuda")):
        raise ValueError("Choose image_size 16/32/64 and device cpu/cuda")
    if result["device"] == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Requested CUDA is unavailable")
    return result


def partition(manifest, requested):
    """Reserve complete episodes before reading/fitting; never reassign tests."""
    if manifest.get("format") not in ("explorer_npz_episode_v1", "explorer_robot_episode_v1"):
        raise ValueError("Expected an Explorer synchronized NPZ episode manifest")
    descriptors = manifest.get("episodes", [])
    ids = [d.get("id") for d in descriptors]
    if not ids or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Unique explicit source episode IDs are required")
    if any(d.get("split") not in TRAIN | DEV | SEALED for d in descriptors):
        raise ValueError("Every source episode needs a recognized explicit split")
    dev_ids = requested.get("development_episode_ids")
    if dev_ids is None:
        dev_ids = [d["id"] for d in descriptors if d["split"] in DEV]
    if (not isinstance(dev_ids, list) or any(not isinstance(i, str) for i in dev_ids)
            or len(dev_ids) != len(set(dev_ids)) or not dev_ids):
        raise ValueError("Reserve distinct development_episode_ids before training; no final tests are selected")
    train_ids = requested.get("training_episode_ids")
    if train_ids is None:
        train_ids = [d["id"] for d in descriptors if d["split"] in TRAIN and d["id"] not in dev_ids]
    if (not isinstance(train_ids, list) or any(not isinstance(i, str) for i in train_ids)
            or len(train_ids) != len(set(train_ids)) or not train_ids):
        raise ValueError("At least one distinct training episode is required")
    if set(train_ids) & set(dev_ids) or (set(train_ids) | set(dev_ids)) - set(ids):
        raise ValueError("Training/development identities overlap or are unknown")
    selected = {"train": [], "development": []}
    effective = []
    sessions = {"train": set(), "development": set()}
    for d in descriptors:
        role = "train" if d["id"] in train_ids else "development" if d["id"] in dev_ids else "unused"
        if role != "unused":
            if d["split"] in SEALED or (role == "train" and d["split"] not in TRAIN):
                raise ValueError("Sealed tests cannot be used and established development cannot be fitted")
            selected[role].append(d)
            effective.append(dict(d, split=role))
            if d.get("source_session_id"):
                sessions[role].add(d["source_session_id"])
    if sessions["train"] & sessions["development"]:
        raise ValueError("A declared source session crosses world-model splits")
    # Uses effective roles to reject aliased source IDs/hashes/native frame IDs.
    split_map = validate_splits(dict(manifest, episodes=effective))
    return selected, split_map


class Transitions(Dataset):
    """Current-only inputs and next-only targets; no cross-episode transitions."""
    def __init__(self, manifest_path, manifest, descriptors, image_size, max_frames):
        self.episodes, self.indices, self.dropped_nonfuture = [], [], 0
        total = 0
        for descriptor in descriptors:
            total += descriptor.get("frames", 0)
            if total > max_frames:
                raise ValueError("Selected source exceeds max_source_frames; choose a bounded offline subset")
            source = load_episode(manifest_path, descriptor, manifest)
            count = len(source["rgb"])
            if count != descriptor.get("frames") or count < 2:
                raise ValueError("Each selected episode needs its declared count and at least two observations")
            basis = "index_order_only_no_native_timestamps"
            valid = np.ones(count - 1, dtype=bool)
            timestamps = source["timestamps"]
            if timestamps:
                prefix = "wall_unix_ns" if "frame_wall_unix_ns" in timestamps else "monotonic_ns"
                valid = timestamps["frame_" + prefix][1:] > timestamps["action_" + prefix][:-1]
                valid &= timestamps["state_" + prefix][1:] >= timestamps["action_" + prefix][:-1]
                basis = "native_" + prefix + "_strictly_post_action_observation"
            elif manifest.get("contains_physical_recordings"):
                raise ValueError("Physical dynamics need native post-action observation timestamps")
            self.dropped_nonfuture += int((~valid).sum())
            rgb = np.stack([np.asarray(Image.fromarray(frame).resize((image_size, image_size),
                             Image.Resampling.BILINEAR)) for frame in source["rgb"]])
            index = len(self.episodes)
            self.episodes.append({"descriptor": descriptor, "rgb": torch.from_numpy(rgb).permute(0, 3, 1, 2).float() / 255,
                                  "state": torch.from_numpy(source["state"]), "action": torch.from_numpy(source["action"]),
                                  "temporal_basis": basis})
            self.indices.extend((index, i) for i in np.flatnonzero(valid).tolist())
        if not self.indices:
            raise ValueError("No strictly post-action, within-episode transitions are available")

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, index):
        episode_index, t = self.indices[index]
        episode = self.episodes[episode_index]
        return {"inputs": {"rgb": episode["rgb"][t], "state": episode["state"][t], "action": episode["action"][t]},
                "targets": {"next_rgb": episode["rgb"][t + 1], "next_state": episode["state"][t + 1]},
                "episode_id": episode["descriptor"]["id"], "frame_index": t}


class CompactDynamics(nn.Module):
    """Known residual CNN/MLP engineering baseline, not an actor or new method."""
    def __init__(self, low, high, image_size=32, condition_on_action=True):
        super().__init__()
        self.condition_on_action = condition_on_action
        self.image_size = image_size
        self.register_buffer("low", torch.tensor(low, dtype=torch.float32))
        self.register_buffer("scale", (torch.tensor(high, dtype=torch.float32) - self.low) / 2)
        dim = len(low)
        self.encoder = nn.Sequential(nn.Conv2d(3, 8, 5, 2, 2), nn.ReLU(), nn.Conv2d(8, 16, 3, 2, 1),
                                     nn.ReLU(), nn.AdaptiveAvgPool2d((4, 4)), nn.Flatten())
        self.fusion = nn.Sequential(nn.Linear(256 + 2 * dim, 96), nn.ReLU(), nn.Linear(96, 64), nn.ReLU())
        self.state_head = nn.Linear(64, dim)
        self.image_seed = nn.Linear(64, 16 * 4 * 4)
        self.decoder = nn.Sequential(nn.ConvTranspose2d(16, 8, 4, 2, 1), nn.ReLU(),
                                     nn.ConvTranspose2d(8, 8, 4, 2, 1), nn.ReLU(), nn.Conv2d(8, 3, 3, padding=1))
        # Both learned models start exactly at the persistence baseline.
        nn.init.zeros_(self.state_head.weight); nn.init.zeros_(self.state_head.bias)
        nn.init.zeros_(self.decoder[-1].weight); nn.init.zeros_(self.decoder[-1].bias)

    def forward(self, rgb, state, action):
        state_normalized = (state - self.low) / self.scale - 1
        action_normalized = (action - self.low) / self.scale - 1
        if not self.condition_on_action:
            action_normalized = torch.zeros_like(action_normalized)
        hidden = self.fusion(torch.cat([self.encoder(rgb), state_normalized, action_normalized], dim=1))
        delta = self.decoder(self.image_seed(hidden).reshape(-1, 16, 4, 4))
        delta = nn.functional.interpolate(delta, size=(self.image_size, self.image_size), mode="bilinear", align_corners=False)
        return {"next_rgb": (rgb + .1 * torch.tanh(delta)).clamp(0, 1),
                "next_state": state + .1 * self.state_head(hidden) * self.scale}


def digest_weights(model):
    h = hashlib.sha256()
    for key, value in sorted(model.state_dict().items()):
        h.update(key.encode()); h.update(value.detach().cpu().contiguous().numpy().tobytes())
    return h.hexdigest()


def load_predictor(directory, device="cpu"):
    directory = Path(directory)
    config = json.loads((directory / "config.json").read_text())
    model = CompactDynamics(config["low"], config["high"], config["image_size"], config["condition_on_action"]).to(device)
    model.load_state_dict(torch.load(directory / "model.pt", weights_only=True, map_location=device))
    model.eval()
    return model


def fit(samples, architecture, config, directory, progress):
    directory.mkdir()
    torch.manual_seed(config["seed"])
    model = CompactDynamics(**architecture).to(config["device"])
    before = digest_weights(model)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    generator = torch.Generator().manual_seed(config["seed"] + 1)
    losses = []
    for step in range(config["optimizer_steps"]):
        indices = torch.randint(len(samples), (config["batch_size"],), generator=generator).tolist()
        rows = [samples[i] for i in indices]
        inputs = {k: torch.stack([r["inputs"][k] for r in rows]).to(config["device"]) for k in ("rgb", "state", "action")}
        target = {k: torch.stack([r["targets"][k] for r in rows]).to(config["device"]) for k in ("next_rgb", "next_state")}
        predicted = model(**inputs)
        rgb_loss = nn.functional.mse_loss(predicted["next_rgb"], target["next_rgb"])
        state_loss = (((predicted["next_state"] - target["next_state"]) / model.scale) ** 2).mean()
        loss = rgb_loss + state_loss
        if not torch.isfinite(loss):
            raise ValueError("World-model loss is not finite")
        optimizer.zero_grad(); loss.backward()
        if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
            raise ValueError("World-model gradient is not finite")
        optimizer.step()
        losses.append({"step": step + 1, "rgb_mse": float(rgb_loss.detach()),
                       "normalized_state_mse": float(state_loss.detach()), "loss": float(loss.detach())})
        if step == 0 or (step + 1) % max(1, config["optimizer_steps"] // 10) == 0:
            progress((step + 1) / config["optimizer_steps"])
    torch.save(model.state_dict(), directory / "model.pt")
    torch.save(optimizer.state_dict(), directory / "optimizer.pt")
    write_json(directory / "config.json", architecture)
    with (directory / "losses.jsonl").open("w") as stream:
        for row in losses:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    restored = load_predictor(directory, config["device"])
    model.eval()
    with torch.inference_mode():
        first, second = model(**inputs), restored(**inputs)
        reload_error = max(float((first[k] - second[k]).abs().max()) for k in first)
    optimizer_restored = torch.optim.Adam(restored.parameters(), lr=1e-3)
    optimizer_restored.load_state_dict(torch.load(directory / "optimizer.pt", weights_only=True, map_location=config["device"]))
    restored_steps = sorted({int(v["step"].item()) for v in optimizer_restored.state.values()})
    if reload_error != 0 or restored_steps != [config["optimizer_steps"]] or digest_weights(model) == before:
        raise ValueError("Changed weights, exact prediction reload and optimizer-step recovery are required")
    return restored, {"optimizer_steps": config["optimizer_steps"], "optimizer_restored_steps": restored_steps,
                      "weights_changed": True, "initial_weights_sha256": before, "final_weights_sha256": digest_weights(model),
                      "reload_max_abs_error": reload_error, "first_loss": losses[0], "last_loss": losses[-1],
                      "parameter_count": sum(p.numel() for p in model.parameters())}


def evaluate(samples, model, low, high, device, names, units, baseline=None):
    """Observed one-step errors, not success or physical/counterfactual outcomes."""
    scale = (torch.tensor(high) - torch.tensor(low)) / 2
    sums = {}
    for batch in DataLoader(samples, batch_size=16, shuffle=False, num_workers=0):
        inputs, target = batch["inputs"], batch["targets"]
        if baseline:
            prediction = {"next_rgb": inputs["rgb"], "next_state": inputs["action"] if baseline == "command_copy" else inputs["state"]}
        else:
            with torch.inference_mode():
                prediction = {k: v.cpu() for k, v in model(**{k: v.to(device) for k, v in inputs.items()}).items()}
        pixel_error = prediction["next_rgb"] - target["next_rgb"]
        state_error = prediction["next_state"] - target["next_state"]
        for i, ident in enumerate(batch["episode_id"]):
            values = np.asarray([float(pixel_error[i].square().mean()), float(pixel_error[i].abs().mean()),
                                 float((state_error[i] / scale).square().mean()), *state_error[i].abs().tolist()], np.float64)
            if ident not in sums:
                sums[ident] = {"n": 0, "totals": np.zeros(len(values), np.float64)}
            sums[ident]["n"] += 1; sums[ident]["totals"] += values
    def metrics(values):
        return {"rgb_mse_0_1": float(values[0]), "rgb_mae_0_1": float(values[1]),
                "normalized_state_mse": float(values[2]),
                "state_mae_native": {name: {"value": float(values[i + 3]), "unit": units[i]} for i, name in enumerate(names)}}
    per_episode = {key: dict(transitions=v["n"], **metrics(v["totals"] / v["n"])) for key, v in sums.items()}
    total = sum(v["n"] for v in sums.values())
    return {"transitions": total, "episodes": len(sums), "transition_weighted": metrics(sum(v["totals"] for v in sums.values()) / total),
            "episode_weighted": metrics(np.mean([v["totals"] / v["n"] for v in sums.values()], axis=0)),
            "per_episode": per_episode, "task_success_evaluated": False, "sealed_test_used": False}


def preview(samples, models, device, directory):
    """Show one held-out prediction without relabelling it a live camera image."""
    row = samples[0]
    images = {"Current": row["inputs"]["rgb"], "Next target": row["targets"]["next_rgb"]}
    with torch.inference_mode():
        for label, model in models.items():
            images[label] = model(**{k: v[None].to(device) for k, v in row["inputs"].items()})["next_rgb"][0].cpu()
    images["No change"] = row["inputs"]["rgb"]
    canvas = Image.new("RGB", (160 * len(images), 184), "white")
    draw = ImageDraw.Draw(canvas)
    for i, (label, rgb) in enumerate(images.items()):
        value = (rgb.permute(1, 2, 0).clamp(0, 1).numpy() * 255).round().astype(np.uint8)
        canvas.paste(Image.fromarray(value).resize((160, 160), Image.Resampling.NEAREST), (i * 160, 24))
        draw.text((i * 160 + 2, 4), label, fill="black")
    canvas.save(directory / "prediction-preview.png")
    write_json(directory / "prediction-preview.json", {"source": "held_out_offline_episode_not_live_camera",
        "episode_id": row["episode_id"], "current_frame_index": row["frame_index"],
        "target_frame_index": row["frame_index"] + 1, "native_rgb_resized_for_model": True,
        "display_upscale": "nearest, no invented detail", "physical_success_inferred": False})


def run_world_model(store, job):
    """Integration entry: local files + store.update_job only; never controls IO."""
    started = time.monotonic()
    requested = job.get("config") or {}
    config = settings(requested)
    path = requested.get("dataset_manifest")
    if not isinstance(path, str) or not path:
        raise ValueError("dataset_manifest is required")
    path = Path(path).expanduser().resolve()
    manifest = json.loads(path.read_text())
    selected, split_map = partition(manifest, requested)
    normalization = manifest.get("action_normalization", {})
    low, high = np.asarray(normalization.get("low"), np.float32), np.asarray(normalization.get("high"), np.float32)
    names = manifest.get("action_names", [])
    units = manifest.get("action_units", ["degrees"] * 6 + ["m/s", "m/s", "rad/s"])
    if (low.ndim != 1 or high.shape != low.shape or not len(low) or len(names) != len(low) or len(units) != len(low)
            or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names)
            or not np.isfinite(low).all() or not np.isfinite(high).all() or np.any(high <= low)):
        raise ValueError("Native action/state names, units and finite normalization ranges must agree")
    ident = job.get("id", "")
    if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", ident):
        raise ValueError("Invalid world-model job ID")
    folder = Path(store.root) / "jobs" / ident
    folder.mkdir(parents=True, exist_ok=True)
    provenance = {"source_manifest_path": str(path), "source_manifest_sha256": sha(path),
                  "source_dataset_id": manifest.get("dataset_id"), "source_episodes": split_map,
                  "source_implementation_sha256": sha(__file__), "settings": config,
                  "physical_io": False, "simulator_executed": False, "method_ids": ["ENGINEERING_GLUE"],
                  "source_domain": ("physical_recordings" if manifest.get("contains_physical_recordings") is True else
                                    "simulated_recordings" if manifest.get("contains_physical_recordings") is False else "UNKNOWN"),
                  "state_basis": manifest.get("state_basis", "command_estimate"),
                  "action_basis": manifest.get("action_basis", "command_endpoint_labels"),
                  "source_splits_unchanged": True, "sealed_test_used": False,
                  "source_declared_splits": {d["id"]: d["split"] for ds in selected.values() for d in ds},
                  "independent_session_split_verified": all(d.get("source_session_id") for ds in selected.values() for d in ds),
                  "clock_domain": manifest.get("clock_domain", "NOT_RECORDED"),
                  "native_timestamp_basis": manifest.get("native_timestamp_basis", "NOT_RECORDED"),
                  "input_keys": ["rgb_t", "native_state_t", "action_t"], "target_keys": ["rgb_t_plus_1", "native_state_t_plus_1"],
                  "normalization": "Fixed source-contract low/high; no development statistics fitted",
                  "outcome_policy": "Failed/unknown episodes may teach dynamics; not relabelled as expert BC targets",
                  "split_scope": "Disjoint episodes within this world-model fit; prior use by other development pipelines is not erased"}
    signature = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    result_path = folder / "result.json"
    if result_path.is_file():
        saved = json.loads(result_path.read_text())
        if saved.get("job_signature") != signature:
            raise ValueError("Completed world-model job has different sources/settings/implementation")
        for p, digest in saved["artifact_sha256"].items():
            candidate = (folder / p).resolve()
            if not candidate.is_relative_to(folder.resolve()) or sha(candidate) != digest:
                raise ValueError("Completed world-model artifact checksum mismatch")
        for role in selected.values():
            for descriptor in role:
                if sha(path.parent / descriptor["file"]) != descriptor["sha256"]:
                    raise ValueError("Completed world-model source checksum mismatch")
        return saved
    torch.set_num_threads(4)
    data = {role: Transitions(path, manifest, descriptors, config["image_size"], config["max_source_frames"])
            for role, descriptors in selected.items()}
    if any(e["state"].shape[1] != len(low) or e["action"].shape[1] != len(low) for d in data.values() for e in d.episodes):
        raise ValueError("Native feature dimension differs from the declared contract")
    attempt = folder / ("offline-world-model-" + uuid.uuid4().hex)
    attempt.mkdir()
    write_json(attempt / "provenance.json", provenance)
    learned, optimization = {}, {}
    for index, label in enumerate(("action_conditioned", "action_agnostic")):
        architecture = {"low": low.tolist(), "high": high.tolist(), "image_size": config["image_size"],
                        "condition_on_action": label == "action_conditioned"}
        def progress(value):
            store.update_job(ident, progress=.05 + .8 * (index + value) / 2,
                             result={"phase": "offline_world_model_" + label, "physical_io": False})
        learned[label], optimization[label] = fit(data["train"], architecture, config, attempt / label, progress)
    evaluation = {label: evaluate(data["development"], model, low, high, config["device"], names, units)
                  for label, model in learned.items()}
    evaluation["persistence"] = evaluate(data["development"], None, low, high, "cpu", names, units, "persistence")
    if provenance["state_basis"] == "command_estimate":
        evaluation["command_copy_plus_rgb_persistence"] = evaluate(data["development"], None, low, high, "cpu", names, units, "command_copy")
    preview(data["development"], learned, config["device"], attempt)
    # Recheck immutable inputs after fitting, before reporting a completed result.
    if sha(path) != provenance["source_manifest_sha256"] or any(sha(path.parent / d["file"]) != d["sha256"] for ds in selected.values() for d in ds):
        raise ValueError("Immutable source changed during offline fitting")
    artifacts = {str(p.relative_to(folder)): sha(p) for p in sorted(attempt.rglob("*")) if p.is_file()}
    result = {"schema_version": 1, "job_signature": signature, "task": "offline_one_step_observation_prediction",
              "method_ids": ["ENGINEERING_GLUE"], "physical_io": False, "simulator_executed": False,
              "task_success_claimed": False, "causal_effect_identified": False, "hypothesis_status": {f"H{i}": "UNTESTED" for i in range(1, 6)},
              "source": provenance, "optimization": optimization, "evaluation": evaluation,
              "source_transitions": {role: {"used": len(d), "dropped_nonfuture_observations": d.dropped_nonfuture,
                  "temporal_basis": {e["descriptor"]["id"]: e["temporal_basis"] for e in d.episodes}} for role, d in data.items()},
              "artifact_directory": str(attempt), "artifact_sha256": artifacts,
              "framework_versions": {"torch": torch.__version__, "numpy": np.__version__},
              "runtime": {"device": config["device"], "platform": platform.system(), "machine": platform.machine(),
                          "torch_num_threads": torch.get_num_threads()},
              "elapsed_s": time.monotonic() - started, "completed_wall_utc": datetime.now(timezone.utc).isoformat(),
              "limits": ["One-step teacher-forced errors are not long-horizon rollout or task success",
                         "Observational trajectories do not establish counterfactual action effects",
                         "Command-state targets may copy previous actions by construction; not measured physical dynamics",
                         "Declared native timestamps do not independently establish sensor exposure or clock calibration",
                         "No policy planning, learned-policy actuation, independent physical validation or H1-H5 result"]}
    write_json(attempt / "evaluation.json", evaluation)
    result["artifact_sha256"][str((attempt / "evaluation.json").relative_to(folder))] = sha(attempt / "evaluation.json")
    write_json(result_path, result)
    return result
