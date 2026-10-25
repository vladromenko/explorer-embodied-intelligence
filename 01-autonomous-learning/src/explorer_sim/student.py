"""Compact official LeRobot ACT training through its public policy API.

No model implementation is copied or substituted. The NPZ episode adapter and
explicit preprocessing are Explorer glue; checkpoint save/reload is official.
"""
import importlib.metadata
from pathlib import Path
import time

import numpy as np
from PIL import Image
import torch
from torch.utils.data import DataLoader, Dataset

from .pilot import HIGH, LOW, sha, state_digest, summarize, write_frame, write_json


IMAGE_KEY = "observation.images.overview"


def prepare_rgb(images):
    value = torch.as_tensor(np.asarray(images).copy()).float().permute(0, 3, 1, 2) / 255
    mean = torch.tensor([.485, .456, .406]).view(1, 3, 1, 1)
    std = torch.tensor([.229, .224, .225]).view(1, 3, 1, 1)
    return (value - mean) / std


class EpisodeChunks(Dataset):
    def __init__(self, directory, manifest, chunk_size):
        self.episodes, self.indices, self.chunk_size = [], [], chunk_size
        for descriptor in manifest["episodes"]:
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
    version = importlib.metadata.version("lerobot")
    if version != "0.6.1":
        raise ImportError(f"This pilot verifies official lerobot==0.6.1; found {version}")
    from lerobot.configs import FeatureType, NormalizationMode, PolicyFeature
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy

    torch.manual_seed(settings["seed"])
    device = settings["device"]
    config = ACTConfig(device=device, input_features={
        IMAGE_KEY: PolicyFeature(type=FeatureType.VISUAL, shape=(3, settings["image_size"], settings["image_size"])),
        "observation.state": PolicyFeature(type=FeatureType.STATE, shape=(9,))},
        output_features={"action": PolicyFeature(type=FeatureType.ACTION, shape=(9,))},
        normalization_mapping={name: NormalizationMode.IDENTITY for name in ("VISUAL", "STATE", "ACTION")},
        chunk_size=settings["chunk_size"], n_action_steps=1, temporal_ensemble_coeff=None,
        dim_model=128, n_heads=4, dim_feedforward=512, n_encoder_layers=2,
        n_decoder_layers=1, n_vae_encoder_layers=2, latent_dim=16,
        use_vae=True, kl_weight=.1, pretrained_backbone_weights=None,
        optimizer_lr=3e-4, optimizer_lr_backbone=1e-4, push_to_hub=False)
    policy = ACTPolicy(config).to(device)
    initial_digest = state_digest(policy)
    samples = EpisodeChunks(folder / "dataset", manifest, settings["chunk_size"])
    loader = DataLoader(samples, batch_size=min(settings["batch_size"], len(samples)), shuffle=True,
                        num_workers=0, generator=torch.Generator().manual_seed(settings["seed"]))
    optimizer = torch.optim.AdamW(policy.get_optim_params(), lr=config.optimizer_lr, weight_decay=1e-4)
    iterator, losses, started = iter(loader), [], time.monotonic()
    for step in range(settings["act_steps"]):
        try:
            batch = next(iterator)
        except StopIteration:
            iterator = iter(loader)
            batch = next(iterator)
        batch = {key: value.to(device) for key, value in batch.items()}
        policy.train()
        optimizer.zero_grad(set_to_none=True)
        loss, terms = policy(batch)
        if not torch.isfinite(loss):
            raise RuntimeError("ACT produced a nonfinite loss")
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(policy.parameters(), 1.0, error_if_nonfinite=True)
        optimizer.step()
        losses.append({"step": step + 1, "loss": float(loss.detach().cpu()),
                       "gradient_norm": float(norm.detach().cpu()), **terms})
        if (step + 1) % 10 == 0 or step + 1 == settings["act_steps"]:
            write_json(folder / "act_training.json", {"losses": losses, "device": device,
                       "elapsed_s": time.monotonic() - started, "official_implementation": "lerobot.policies.act.ACTPolicy"})
            update(.60 + .25 * (step + 1) / settings["act_steps"], "optimizing_official_lerobot_act")
    checkpoint = folder / "student_act"
    policy.eval()
    policy.save_pretrained(checkpoint, push_to_hub=False)
    torch.save(optimizer.state_dict(), folder / "act_optimizer.pt")
    preprocessing = {"schema_version": 1, "image_key": IMAGE_KEY, "rgb_layout": "NCHW",
                     "rgb_scale": "uint8 / 255", "mean": [.485, .456, .406], "std": [.229, .224, .225],
                     "state_basis": "command_estimate", "state_low": LOW.tolist(), "state_high": HIGH.tolist(),
                     "state_transform": "2*(state-low)/(high-low)-1", "action_transform": "same fixed endpoint normalization",
                     "privileged_target_xyz_in_actor": False, "temporal_ensembling": False,
                     "chunk_execution_steps": 1, "pilot_active_action_indices": [0, 1, 2]}
    write_json(checkpoint / "explorer_preprocessing.json", preprocessing)
    reloaded = ACTPolicy.from_pretrained(checkpoint, local_files_only=True, strict=True).to(device).eval()
    evaluation_env.reset(seed=settings["seed"] + 9999)
    batch = observation_batch(evaluation_env, settings["image_size"], device)
    with torch.inference_mode():
        expected = policy.predict_action_chunk(batch)
        actual = reloaded.predict_action_chunk(batch)
    reload_error = float((actual - expected).abs().max().cpu())
    if reload_error > 1e-6:
        raise RuntimeError("Official ACT checkpoint reload mismatch")
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
        write_frame(evaluation_env, folder, "Official ACT: independent RGB-only simulated reach rollout")
        update(.85 + .1 * len(episodes) / len(seeds), "evaluating_reloaded_rgb_act")
    evaluation = summarize(episodes)
    write_json(folder / "act_evaluation.json", evaluation)
    return {"status": "trained_and_simulation_evaluated", "trained": True,
            "algorithm": "official LeRobot ACT", "lerobot_version": version,
            "official_module": ACTPolicy.__module__, "optimizer_steps": len(losses),
            "weights_changed": initial_digest != state_digest(policy),
            "parameter_count": sum(parameter.numel() for parameter in policy.parameters()),
            "device": device, "checkpoint_dir": str(checkpoint),
            "checkpoint_sha256": {path.name: sha(path) for path in sorted(checkpoint.iterdir()) if path.is_file()},
            "save_reload_passed": True, "reload_max_abs_error": reload_error,
            "evaluation": evaluation, "losses_first_last": [losses[0]["loss"], losses[-1]["loss"]],
            "rgb_inputs": [IMAGE_KEY], "state_basis": "command_estimate",
            "privileged_state_in_actor": False, "elapsed_s": time.monotonic() - started,
            "framework_compatibility_note": "Runtime tested; Torch 2.14 / torchvision 0.29 lie outside LeRobot 0.6.1 declared bounds"}
