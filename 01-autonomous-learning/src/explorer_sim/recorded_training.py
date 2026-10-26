"""Explicit episode ACT training without simulated or physical execution.

Methods: ACT_LEROBOT_0_6_1, ENGINEERING_GLUE. Offline chunk errors are diagnostic
and never establish a robot skill's physical success or measured grasp force.
"""
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
import torch
from torch.utils.data import DataLoader, Dataset

from .dataset_export import load_episode, validate_splits
from .pilot import HIGH, LOW, parameters, preserve_interrupted_attempt, sha, state_digest, utc, write_json
from explorer_shared.learning.act_backend import (IMAGE_KEY, framework_info, make_act_policy, optimize_act,
                      prepare_rgb, preprocessing_config, save_reload_act)


class RecordedChunks(Dataset):
    """At most one compressed source episode is cached, never the whole corpus."""
    def __init__(self, manifest_path, manifest, descriptors, chunk_size, image_size):
        self.path, self.manifest, self.descriptors = Path(manifest_path), manifest, descriptors
        self.resume_context = {"source_signature": sha(self.path),
            "source_files": [{"path": str(self.path.resolve()), "sha256": sha(self.path)}] +
                [{"path": str((self.path.parent / d["file"]).resolve()), "sha256": d["sha256"]} for d in descriptors],
            "training_episode_ids": [d.get("id", d["file"]) for d in descriptors],
            "chunk_size": chunk_size, "image_size": image_size}
        self.chunk_size, self.image_size = chunk_size, image_size
        self.cache, self.cached_episode, self.indices = None, None, []
        for episode_index, descriptor in enumerate(descriptors):
            source = load_episode(self.path, descriptor, manifest)
            self.indices.extend((episode_index, frame_index) for frame_index in range(len(source["rgb"])))
        if not self.indices:
            raise ValueError("No synchronized training frames")
        normalization = manifest.get("action_normalization", {"low": LOW.tolist(), "high": HIGH.tolist()})
        self.low, self.high = np.asarray(normalization["low"], np.float32), np.asarray(normalization["high"], np.float32)
        if self.low.ndim != 1 or self.high.shape != self.low.shape or not np.isfinite(self.low).all() or not np.isfinite(self.high).all() or np.any(self.high <= self.low):
            raise ValueError("Invalid actor normalization ranges")

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, index):
        episode_index, frame_index = self.indices[index]
        if episode_index != self.cached_episode:
            self.cache = load_episode(self.path, self.descriptors[episode_index], self.manifest)
            self.cached_episode = episode_index
        source = self.cache
        positions = frame_index + np.arange(self.chunk_size)
        padded = positions >= len(source["action"])
        positions = np.minimum(positions, len(source["action"]) - 1)
        image = np.asarray(Image.fromarray(source["rgb"][frame_index]).resize((self.image_size, self.image_size)))
        state = 2 * (source["state"][frame_index] - self.low) / (self.high - self.low) - 1
        action = 2 * (source["action"][positions] - self.low) / (self.high - self.low) - 1
        return {IMAGE_KEY: prepare_rgb(image[None])[0], "observation.state": torch.from_numpy(state),
                "action": torch.from_numpy(action), "action_is_pad": torch.from_numpy(padded)}


def offline_evaluation(policy, samples, device, names, units, low, high):
    if samples is None:
        return {"domain": "offline_demonstration_chunks", "independent_episode_split": False,
                "available": False, "reason": "No development episodes supplied", "physical_run_ids": []}
    total, count, batches = np.zeros(len(names), np.float64), 0, 0
    policy.eval()
    for batch in DataLoader(samples, batch_size=8, shuffle=False, num_workers=0):
        inputs = {key: batch[key].to(device) for key in (IMAGE_KEY, "observation.state")}
        with torch.inference_mode():
            predicted = policy.predict_action_chunk(inputs).cpu().numpy()
        mask = ~batch["action_is_pad"].numpy()
        error = np.abs(predicted - batch["action"].numpy())
        total += (error * mask[..., None]).sum(axis=(0, 1))
        count += int(mask.sum())
        batches += 1
    mae = total / max(1, count)
    return {"domain": "offline_demonstration_chunks", "independent_episode_split": True,
            "available": True, "valid_chunk_actions": count, "batches": batches,
            "mean_absolute_error_normalized": float(mae.mean()),
            "action_mae": {name: {"value": float(mae[index] * (high[index] - low[index]) / 2), "unit": units[index]}
                           for index, name in enumerate(names)}, "physical_run_ids": [],
            "rollout_success_claimed": False, "sealed_test_used": False}


def train_recorded_act(store, job):
    requested = job.get("config") or {}
    source_path = requested.get("dataset_manifest")
    if not isinstance(source_path, str) or not source_path:
        raise ValueError("dataset_manifest is required: choose synchronized action-labelled episodes; camera-only video cannot train ACT")
    source_path = Path(source_path).expanduser().resolve()
    source_digest = sha(source_path)
    manifest = json.loads(source_path.read_text())
    if manifest.get("format") not in ("explorer_npz_episode_v1", "explorer_robot_episode_v1"):
        raise ValueError("Convert station recordings to a synchronized Explorer episode manifest first")
    split_map = validate_splits(manifest)
    admission=manifest.get('training_admission')
    if admission and not (admission.get('source_quality_usable') and admission.get('operator_success_label')):
        raise ValueError('This mobile recording remains available for analysis; successful operator-labelled usable demonstrations are required for expert BC targets')
    training = [item for item in manifest["episodes"] if item.get("split", "train") in ("train", "imitation_train", "training")]
    excluded_outcomes=[]
    if manifest.get('contains_physical_recordings'):
        excluded_outcomes=[item.get('id') for item in training if item.get('outcome')!='success']
        training=[item for item in training if item.get('outcome')=='success']
    else:
        failed=lambda item:isinstance(item.get('outcome'),dict) and item['outcome'].get('success') is False
        excluded_outcomes=[item.get('id') for item in training if failed(item)]
        training=[item for item in training if not failed(item)]
    development = [item for item in manifest["episodes"] if item.get("split") in ("development", "validation", "dev")]
    if not training:
        raise ValueError("No successful training demonstrations. Failed and unlabelled physical episodes remain available for analysis; sealed tests are never fitted.")
    settings = parameters({key: requested[key] for key in ("seed", "act_steps", "batch_size", "image_size", "chunk_size", "device") if key in requested})
    if settings["act_steps"] <= 0 or settings["device"] not in ("cpu", "cuda"):
        raise ValueError("ACT needs positive optimizer steps and an explicit supported device")
    if settings["device"] == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Requested CUDA is unavailable")
    torch.set_num_threads(4)
    names = manifest.get("action_names")
    if not isinstance(names, list) or not names:
        raise ValueError("Action dimension names are required")
    units = manifest.get("action_units", ["degrees"] * 6 + ["m/s", "m/s", "rad/s"])
    if len(units) != len(names):
        raise ValueError("Action units and names differ")
    folder = store.root / "jobs" / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    result_path = folder / "result.json"
    if result_path.is_file():
        saved = json.loads(result_path.read_text())
        if saved.get("source_manifest_sha256") != source_digest:
            raise ValueError("Completed training belongs to another dataset")
        for descriptor in manifest["episodes"]:
            if sha(source_path.parent / descriptor["file"]) != descriptor["sha256"]:
                raise ValueError("Completed training source episode checksum mismatch")
        for name, digest in saved["checkpoint_sha256"].items():
            if Path(name).name != name or sha(folder / "student_act" / name) != digest:
                raise ValueError("Completed ACT checkpoint checksum mismatch")
        if saved.get("skill_card") and not any(item["id"] == saved["skill_card"]["id"] for item in store.skills()):
            store.add_skill(saved["skill_card"])
        return saved
    preserve_interrupted_attempt(folder)

    def update(progress, phase):
        store.update_job(job["id"], progress=progress, result={"phase": phase, "source": "explicit_recorded_episodes"})

    update(.02, "validating_synchronized_recorded_episodes")
    samples = RecordedChunks(source_path, manifest, training, settings["chunk_size"], settings["image_size"])
    if len(samples.low) != len(names):
        raise ValueError("Actor feature dimensions differ from normalization")
    preview = samples[0]
    source = samples.cache
    Image.fromarray(source["rgb"][0]).save(folder / "frame.jpg")
    write_json(folder / "frame.json", {"source": "actual_recorded_episode_image", "wall_utc": utc(),
                                      "episode_id": training[0].get("id"), "live_camera": False})
    filtered_settings = {key: settings[key] for key in ("seed", "act_steps", "batch_size", "image_size", "chunk_size", "device")}
    provenance = {"schema_version": 1, "source_manifest_sha256": source_digest, "source_manifest_path": str(source_path),
                  "source_dataset_id": manifest["dataset_id"], "episodes": split_map, "settings": filtered_settings,
                  "method_ids": ["ACT_LEROBOT_0_6_1", "ENGINEERING_GLUE"], "simulator_executed": False,
                  "physical_io": False, "sealed_test_used": False, "training_source_count": len(training),
                  "development_source_count": len(development), "created_wall_utc": utc()}
    provenance['excluded_nonexpert_physical_episode_ids']=excluded_outcomes
    write_json(folder / "provenance.json", provenance)
    policy = make_act_policy(settings, len(names), len(names))
    initial_digest, started = state_digest(policy), time.monotonic()
    optimizer, losses, _ = optimize_act(policy, samples, settings, folder, update, .1, .8)
    inputs = {key: preview[key].unsqueeze(0).to(settings["device"]) for key in (IMAGE_KEY, "observation.state")}
    preprocessing = preprocessing_config(samples.low, samples.high, range(len(names)))
    preprocessing["trained_action_indices"] = preprocessing.pop("pilot_active_action_indices")
    preprocessing.update(state_basis=manifest.get("state_basis", "command_estimate"), action_names=names, action_units=units)
    reloaded, checkpoint, reload_error = save_reload_act(policy, optimizer, folder, preprocessing, inputs, settings["device"])
    validation = RecordedChunks(source_path, manifest, development, settings["chunk_size"], settings["image_size"]) if development else None
    evaluation = offline_evaluation(reloaded, validation, settings["device"], names, units, samples.low, samples.high)
    if sha(source_path) != source_digest:
        raise ValueError("Source dataset manifest changed while training")
    for descriptor in manifest["episodes"]:
        if sha(source_path.parent / descriptor["file"]) != descriptor["sha256"]:
            raise ValueError("Source episode changed while training")
    result = {"trained": True, "algorithm": "official LeRobot ACT", "official_module": type(policy).__module__,
              "status": "trained_offline_only", "source_dataset_id": manifest["dataset_id"],
              "source_manifest_sha256": source_digest, "source_manifest_path": str(source_path),
              "feature_dimension": len(names), "state_basis": preprocessing["state_basis"],
              "optimizer_steps": len(losses), "weights_changed": state_digest(policy) != initial_digest,
              "training_resume": optimizer.explorer_resume_info,
              "save_reload_passed": True, "reload_max_abs_error": reload_error,
              "checkpoint_sha256": {path.name: sha(path) for path in sorted(checkpoint.iterdir()) if path.is_file()},
              "evaluation": evaluation, "physical_io": False, "simulator_executed": False,
              "elapsed_s": time.monotonic() - started, **framework_info()}
    card = {"id": "act-offline-" + job["id"], "task": manifest.get("task", "Imitate explicitly selected demonstrations"),
            "source_type": "user_selected_action_labelled_episodes", "status": "trained_offline_only",
            "action_contract": {"names": names, "units": units, "state_basis": preprocessing["state_basis"]},
            "lineage": {"job_id": job["id"], "dataset_id": manifest["dataset_id"], "source_manifest_sha256": source_digest,
                        "methods": ["ACT_LEROBOT_0_6_1", "ENGINEERING_GLUE"]}, "evaluation": evaluation,
            "limitations": ["Offline diagnostic errors do not establish rollout success", "No physical validation from this job",
                            "Sealed test episodes excluded", "No robot or simulator execution"]}
    if result["weights_changed"]:
        result["skill_card"] = card
    write_json(result_path, result)
    if result.get("skill_card") and not any(item["id"] == card["id"] for item in store.skills()):
        store.add_skill(card)
    update(.98, "recorded_episode_act_checkpoint_verified")
    return result
