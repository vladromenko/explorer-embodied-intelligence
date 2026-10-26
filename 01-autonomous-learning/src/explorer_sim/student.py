"""Experiment01 episode adapters; shared ACT implementation compatibility names.

Policy factory, optimizer, durable RNG/sampler resume, image preprocessing and
save/reload live in explorer_shared.learning.act_backend for all experiments.
Original datasets/models remain source-pinned; no artifact is migrated in place.
"""
import hashlib
import importlib.metadata
import json
import os  # Legacy tests/callers monkeypatch the same stdlib module used by backend.
from pathlib import Path
import time

import numpy as np
from PIL import Image
import torch
from torch.utils.data import DataLoader, Dataset

from .pilot import HIGH, LOW, sha, state_digest, summarize, write_frame, write_json
from explorer_shared.learning.act_backend import (
    IMAGE_KEY, prepare_rgb, optimize_act, save_reload_act, framework_info,
    make_act_policy as _shared_make_act_policy,
    _plain, _rng_state, _restore_rng, _BatchPlan, _fsync_directory, _atomic_file,
    _resume_signature, _save_resume, _optimize_act_locked,
    preprocessing_config as _shared_preprocessing_config,
)


def make_act_policy(settings, state_dim=9, action_dim=9):
    """Legacy default actor shape; shared callers declare their actual dimensions."""
    return _shared_make_act_policy(settings, state_dim, action_dim)


def preprocessing_config(low=LOW, high=HIGH, active_indices=(0, 1, 2)):
    """Preserve legacy Experiment01 default coordinates at the old import path."""
    return _shared_preprocessing_config(low, high, active_indices)


class EpisodeChunks(Dataset):
    def __init__(self, directory, manifest, chunk_size):
        self.episodes, self.indices, self.chunk_size = [], [], chunk_size
        source_files = [{"path": str((Path(directory) / d["file"]).resolve()), "sha256": d["sha256"]}
                        for d in manifest["episodes"]]
        self.resume_context = {"source_signature": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                               "source_files": source_files, "chunk_size": chunk_size}
        self.excluded_failed_episode_ids=[]
        for descriptor in manifest["episodes"]:
            failed=isinstance(descriptor.get('outcome'),dict) and descriptor['outcome'].get('success') is False
            if failed:
                self.excluded_failed_episode_ids.append(descriptor.get('id',descriptor['file']))
            else:
                path = Path(directory) / descriptor["file"]
                if sha(path) != descriptor["sha256"]:
                    raise ValueError("Dataset episode checksum mismatch")
                with np.load(path, allow_pickle=False) as source:
                    # Evaluator-only privileged fields never enter the model batch.
                    episode = {name: source[name].copy() for name in ("rgb", "state", "action")}
                episode_index = len(self.episodes)
                self.episodes.append(episode)
                self.indices.extend((episode_index, index) for index in range(len(episode["action"])))
        if not self.indices:
            raise ValueError("Dataset contains no actor samples")

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, index):
        episode_index, position = self.indices[index]
        episode = self.episodes[episode_index]
        positions = position + np.arange(self.chunk_size)
        padded = positions >= len(episode["action"])
        actions = episode["action"][np.minimum(positions, len(episode["action"]) - 1)]
        return {IMAGE_KEY: prepare_rgb(episode["rgb"][position:position + 1])[0],
                "observation.state": torch.from_numpy(episode["state"][position]),
                "action": torch.from_numpy(actions), "action_is_pad": torch.from_numpy(padded)}


def observation_batch(env, image_size, device):
    image = np.asarray(Image.fromarray(env.render()).resize((image_size, image_size)))
    return {IMAGE_KEY: prepare_rgb(image[None]).to(device),
            "observation.state": torch.from_numpy(env.student_state()[None]).to(device)}


def train_and_evaluate_act(env, evaluation_env, folder, settings, manifest, seeds, update):
    valid=[item for item in manifest['episodes'] if not (isinstance(item.get('outcome'),dict) and item['outcome'].get('success') is False)]
    if not valid:
        return {'status':'awaiting_successful_teacher_demonstrations','trained':False,'algorithm':'official LeRobot ACT',
            'reason':'Failed teacher rollouts are retained for analysis and excluded from expert BC targets',
            'excluded_failed_episode_ids':[item.get('id') for item in manifest['episodes']]}
    device = settings["device"]
    policy = make_act_policy(settings)
    initial_digest = state_digest(policy)
    samples = EpisodeChunks(folder / "dataset", manifest, settings["chunk_size"])
    optimizer, losses, started = optimize_act(policy, samples, settings, folder, update)
    evaluation_env.reset(seed=settings["seed"] + 9999)
    batch = observation_batch(evaluation_env, settings["image_size"], device)
    reloaded, checkpoint, reload_error = save_reload_act(policy, optimizer, folder, preprocessing_config(), batch, device)
    episodes = []
    for seed in seeds:
        _, initial = evaluation_env.reset(seed=seed)
        reloaded.reset()
        done, rewards = False, []
        while not done:
            batch = observation_batch(evaluation_env, settings["image_size"], device)
            with torch.inference_mode():
                normalized = reloaded.select_action(batch)[0].cpu().numpy()
            command = (np.clip(normalized, -1, 1) + 1) * .5 * (HIGH - LOW) + LOW
            _, reward, terminal, truncated, info = evaluation_env.step_command(command)
            rewards.append(reward)
            done = terminal or truncated
        episodes.append({"seed": seed, "initial_distance_m": initial["distance_m"], **info,
                         "return": float(sum(rewards))})
        write_frame(evaluation_env, folder, "Official ACT: RGB + commanded-state independent reach rollout")
        update(.85 + .1 * len(episodes) / len(seeds), "evaluating_reloaded_rgb_act")
    evaluation = summarize(episodes)
    write_json(folder / "act_evaluation.json", evaluation)
    return {"status": "trained_and_simulation_evaluated", "trained": True,
            "algorithm": "official LeRobot ACT", "lerobot_version": importlib.metadata.version("lerobot"),
            "official_module": type(policy).__module__, "optimizer_steps": len(losses),
            "training_resume": optimizer.explorer_resume_info,
            "weights_changed": initial_digest != state_digest(policy),
            "parameter_count": sum(parameter.numel() for parameter in policy.parameters()),
            "device": device, "checkpoint_dir": str(checkpoint),
            "checkpoint_sha256": {path.name: sha(path) for path in sorted(checkpoint.iterdir()) if path.is_file()},
            "save_reload_passed": True, "reload_max_abs_error": reload_error,
            "evaluation": evaluation, "losses_first_last": [losses[0]["loss"], losses[-1]["loss"]],
            "rgb_inputs": [IMAGE_KEY], "state_basis": "command_estimate",
            "privileged_state_in_actor": False, "elapsed_s": time.monotonic() - started,
            "training_episode_count":len(samples.episodes),"excluded_failed_episode_ids":samples.excluded_failed_episode_ids,
            **framework_info()}
