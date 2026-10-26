"""Shared official LeRobot ACT software backend (no experiment or hardware IO).

Extracted without optimizer/sampler/RNG/save semantics changes. Source graph
signatures intentionally reject older implementation graphs without overwriting
checkpoints. Former explorer_sim.student names remain compatibility aliases.
"""
import importlib.metadata
import dataclasses
from enum import Enum
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import random
import re
import time
import uuid

import numpy as np
import torch
from torch.utils.data import DataLoader, default_collate
from packaging.version import Version

from .artifacts import sha, state_digest, write_json
from . import artifacts

IMAGE_KEY = "observation.images.overview"

def prepare_rgb(images):
    value = torch.as_tensor(np.asarray(images).copy()).float().permute(0, 3, 1, 2) / 255
    mean = torch.tensor([.485, .456, .406]).view(1, 3, 1, 1)
    std = torch.tensor([.229, .224, .225]).view(1, 3, 1, 1)
    return (value - mean) / std


def make_act_policy(settings, state_dim, action_dim):
    version = importlib.metadata.version("lerobot")
    if version != "0.6.1":
        raise ImportError(f"This shared backend verifies official lerobot==0.6.1; found {version}")
    from lerobot.configs import FeatureType, NormalizationMode, PolicyFeature
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy

    torch.manual_seed(settings["seed"])
    device = settings["device"]
    if device=='cuda':
        torch.set_float32_matmul_precision('highest')
        torch.backends.cuda.matmul.allow_tf32=False
        torch.backends.cudnn.allow_tf32=False
    config = ACTConfig(device=device, input_features={
        IMAGE_KEY: PolicyFeature(type=FeatureType.VISUAL, shape=(3, settings["image_size"], settings["image_size"])),
        "observation.state": PolicyFeature(type=FeatureType.STATE, shape=(state_dim,))},
        output_features={"action": PolicyFeature(type=FeatureType.ACTION, shape=(action_dim,))},
        normalization_mapping={name: NormalizationMode.IDENTITY for name in ("VISUAL", "STATE", "ACTION")},
        chunk_size=settings["chunk_size"], n_action_steps=1, temporal_ensemble_coeff=None,
        dim_model=128, n_heads=4, dim_feedforward=512, n_encoder_layers=2,
        n_decoder_layers=1, n_vae_encoder_layers=2, latent_dim=16,
        use_vae=True, kl_weight=.1, pretrained_backbone_weights=None,
        optimizer_lr=3e-4, optimizer_lr_backbone=1e-4, push_to_hub=False)
    return ACTPolicy(config).to(device)


def _plain(value):
    if dataclasses.is_dataclass(value):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, torch.Tensor) and value.numel() == 1:
        return value.detach().cpu().item()
    if hasattr(value, "value"):
        return _plain(value.value)
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    raise TypeError("Unsupported ACT resume signature value: " + type(value).__name__)


def _rng_state():
    numpy = np.random.get_state()
    return {"python": random.getstate(), "numpy": {"kind": numpy[0], "keys": numpy[1].tolist(),
            "position": numpy[2], "has_gauss": numpy[3], "cached_gaussian": numpy[4]},
            "torch_cpu": torch.get_rng_state(),
            "cuda_initialized": torch.cuda.is_initialized(),
            "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_initialized() else []}


def _restore_rng(value):
    random.setstate(value["python"])
    numpy = value["numpy"]
    np.random.set_state((numpy["kind"], np.asarray(numpy["keys"], np.uint32), numpy["position"],
                         numpy["has_gauss"], numpy["cached_gaussian"]))
    torch.set_rng_state(value["torch_cpu"].cpu())
    if value["cuda_initialized"]:
        if not torch.cuda.is_available() or len(value["torch_cuda"]) != torch.cuda.device_count():
            raise ValueError("ACT resume CUDA RNG device set changed")
        torch.cuda.set_rng_state_all([v.cpu() for v in value["torch_cuda"]])


class _BatchPlan:
    """One-process deterministic sampler; complete permutation/cursor is durable."""
    def __init__(self, count, batch_size, seed):
        self.count, self.batch_size = count, batch_size
        self.generator = torch.Generator().manual_seed(seed)
        self.order, self.cursor, self.epoch = [], 0, 0

    def next(self):
        if self.cursor == len(self.order):
            # Preserve the former public DataLoader(generator=..., shuffle=True)
            # index sequence, including its iterator/epoch generator draws. The
            # index-only loader never reads source samples or global RNG.
            orders = list(DataLoader(range(self.count), batch_size=self.count, shuffle=True,
                                    num_workers=0, generator=self.generator))
            self.order = orders[0].tolist()
            self.cursor = 0
            self.epoch += 1
        end = min(self.count, self.cursor + self.batch_size)
        indices = self.order[self.cursor:end]
        self.cursor = end
        return indices

    def state_dict(self):
        return {"order": self.order, "cursor": self.cursor, "epoch": self.epoch,
                "generator_state": self.generator.get_state()}

    def load_state_dict(self, value):
        order = value["order"]
        if (order and sorted(order) != list(range(self.count))) or not 0 <= value["cursor"] <= len(order):
            raise ValueError("ACT resume sampler permutation/cursor is invalid")
        self.order, self.cursor, self.epoch = order, value["cursor"], value["epoch"]
        self.generator.set_state(value["generator_state"].cpu())


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_file(path, writer):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("wb") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _resume_signature(policy, samples, settings, context, interval):
    if (not isinstance(context.get("source_signature"), str) or len(context["source_signature"]) != 64
            or any(c not in "0123456789abcdef" for c in context["source_signature"])):
        raise ValueError("ACT resume_context requires a SHA256 source_signature")
    for item in context.get("source_files", []):
        if not Path(item["path"]).is_file() or sha(item["path"]) != item["sha256"]:
            raise ValueError("ACT resume source checksum changed: " + str(item["path"]))
    implementations = {"act_backend": sha(__file__), "artifacts": sha(artifacts.__file__)}
    for label, cls in [("dataset", type(samples)), ("policy", type(policy))]:
        path = inspect.getsourcefile(cls)
        if path and Path(path).is_file():
            implementations[label] = sha(path)
    packages = {}
    for name in ("torchvision", "lerobot"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "NOT_INSTALLED"
    metadata = {"schema_version": 1, "source_context": _plain(context), "settings": _plain(settings),
        "model_config": _plain(policy.config), "dataset_class": type(samples).__module__ + "." + type(samples).__qualname__,
        "dataset_length": len(samples), "checkpoint_interval": interval, "implementation_sha256": implementations,
        "parameters": [(k, list(v.shape), str(v.dtype)) for k, v in policy.state_dict().items()],
        "trainable_parameters": [name for name, value in policy.named_parameters() if value.requires_grad],
        "runtime": {"torch": str(torch.__version__), "numpy": np.__version__,
                    "packages": packages,
                    "torch_num_threads": torch.get_num_threads(),
                    "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
                    "matmul_precision": torch.get_float32_matmul_precision(),
                    "cuda_matmul_tf32": torch.backends.cuda.matmul.allow_tf32,
                    "cudnn_tf32": torch.backends.cudnn.allow_tf32, "cudnn_benchmark": torch.backends.cudnn.benchmark,
                    "cudnn_deterministic": torch.backends.cudnn.deterministic}}
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, allow_nan=False).encode()).hexdigest(), metadata


def _save_resume(directory, payload):
    directory.mkdir(exist_ok=True)
    _fsync_directory(directory.parent)
    name = f"state-{payload['completed_steps']:08d}-{uuid.uuid4().hex}.pt"
    path = directory / name
    old = json.loads((directory / "latest.json").read_text()) if (directory / "latest.json").is_file() else None
    _atomic_file(path, lambda stream: torch.save(payload, stream))
    pointer = {"checkpoint": name, "sha256": sha(path), "completed_steps": payload["completed_steps"],
               "signature": payload["signature"], "previous_checkpoint": old["checkpoint"] if old else None}
    encoded = json.dumps(pointer, sort_keys=True).encode()
    _atomic_file(directory / "latest.json", lambda stream: stream.write(encoded))
    keep = {name, pointer["previous_checkpoint"]}
    for candidate in directory.glob("state-*.pt"):
        if (candidate.name not in keep and not candidate.is_symlink()
                and re.fullmatch(r"state-[0-9]{8}-[0-9a-f]{32}\.pt", candidate.name)):
            candidate.unlink()
    _fsync_directory(directory)
    return pointer


def optimize_act(policy, samples, settings, folder, update, progress_start=.60, progress_end=.85,
                 resume_context=None, checkpoint_interval=25):
    """One optimizer writer per job; preserve the existing public call/return."""
    directory = Path(folder) / "act_resume"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "optimizer.lock").open("a") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another ACT optimizer owns this job directory") from error
        return _optimize_act_locked(policy, samples, settings, folder, update, progress_start, progress_end,
                                    resume_context, checkpoint_interval)


def _optimize_act_locked(policy, samples, settings, folder, update, progress_start, progress_end,
                         resume_context, checkpoint_interval):
    """Actual optimizer continuation; untyped legacy Datasets remain non-resumable."""
    device, config = settings["device"], policy.config
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if len(samples) < 1 or settings["act_steps"] < 1 or settings["batch_size"] < 1:
        raise ValueError("ACT optimization requires nonempty samples and positive steps/batch size")
    if isinstance(checkpoint_interval, bool) or not isinstance(checkpoint_interval, int) or checkpoint_interval < 1:
        raise ValueError("ACT checkpoint interval must be a positive integer")
    context = resume_context if resume_context is not None else getattr(samples, "resume_context", None)
    signature, metadata = _resume_signature(policy, samples, settings, context, checkpoint_interval) if context is not None else (None, None)
    optimizer = torch.optim.AdamW(policy.get_optim_params(), lr=config.optimizer_lr, weight_decay=1e-4)
    plan = _BatchPlan(len(samples), min(settings["batch_size"], len(samples)), settings["seed"])
    losses, completed, elapsed_before = [], 0, 0.0
    directory = folder / "act_resume"
    pointer_path = directory / "latest.json"
    loaded = pointer_path.is_file()
    if loaded:
        pointer = json.loads(pointer_path.read_text())
        name = pointer["checkpoint"]
        if Path(name).name != name or sha(directory / name) != pointer["sha256"]:
            raise ValueError("ACT resume checkpoint path/checksum mismatch; retained for diagnosis")
        if signature is None or pointer["signature"] != signature:
            raise ValueError("ACT resume source/settings/implementation signature changed; checkpoint retained")
        payload = torch.load(directory / name, map_location="cpu", weights_only=True)
        if (payload.get("schema_version") != 1 or payload["signature"] != signature or payload["metadata"] != metadata
                or pointer["completed_steps"] != payload["completed_steps"]):
            raise ValueError("ACT resume payload signature mismatch")
        losses, completed = payload["losses"], payload["completed_steps"]
        if (len(losses) != completed or completed > settings["act_steps"]
                or [row["step"] for row in losses] != list(range(1, completed + 1))):
            raise ValueError("ACT resume loss/optimizer step history is inconsistent")
        steps = [int(v["step"].item()) for v in payload["optimizer"]["state"].values() if "step" in v]
        if (completed and (not steps or max(steps) != completed)) or (not completed and steps):
            raise ValueError("ACT resume optimizer state does not match the durable step history")
        policy.load_state_dict(payload["model"], strict=True)
        optimizer.load_state_dict(payload["optimizer"])
        plan.load_state_dict(payload["sampler"])
        if payload.get("dataset_state") is not None:
            samples.load_resume_state_dict(payload["dataset_state"])
        elapsed_before = payload["active_elapsed_s"]
        _restore_rng(payload["rng"])
    resumed_from = completed
    started = time.monotonic() - elapsed_before
    initial_weights = payload["initial_weights_sha256"] if loaded else state_digest(policy)
    def save():
        return _save_resume(directory, {"schema_version": 1, "signature": signature, "metadata": metadata,
            "completed_steps": completed, "model": policy.state_dict(), "optimizer": optimizer.state_dict(),
            "sampler": plan.state_dict(), "rng": _rng_state(), "losses": losses,
            "initial_weights_sha256": initial_weights, "active_elapsed_s": time.monotonic() - started,
            "dataset_state": samples.resume_state_dict() if hasattr(samples, "resume_state_dict") else None})
    if signature is not None and not loaded:
        save()
    for step in range(completed, settings["act_steps"]):
        batch = default_collate([samples[index] for index in plan.next()])
        batch = {key: value.to(device) for key, value in batch.items()}
        policy.train()
        optimizer.zero_grad(set_to_none=True)
        loss, terms = policy(batch)
        if not torch.isfinite(loss):
            raise RuntimeError("ACT produced a nonfinite loss")
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(policy.parameters(), 1.0, error_if_nonfinite=True)
        optimizer.step()
        losses.append(_plain({"step": step + 1, "loss": float(loss.detach().cpu()),
                       "gradient_norm": float(norm.detach().cpu()), **terms}))
        completed = step + 1
        checkpoint_due = completed % checkpoint_interval == 0 or completed == settings["act_steps"]
        if signature is not None and checkpoint_due:
            if completed == settings["act_steps"]:
                final_signature, _ = _resume_signature(policy, samples, settings, context, checkpoint_interval)
                if final_signature != signature:
                    raise ValueError("ACT source/settings/implementation changed during optimization")
            save()
        if completed % 10 == 0 or checkpoint_due:
            write_json(folder / "act_training.json", {"losses": losses, "device": device,
                       "elapsed_s": time.monotonic() - started, "official_implementation": "lerobot.policies.act.ACTPolicy"})
            # UI/logging callbacks cannot change the learner's random sequence.
            rng = _rng_state()
            try:
                update(progress_start + (progress_end - progress_start) * completed / settings["act_steps"], "optimizing_official_lerobot_act")
            finally:
                _restore_rng(rng)
    optimizer.explorer_resume_info = {"supported": signature is not None, "checkpoint_loaded": loaded,
        "resumed_from_step": resumed_from, "completed_steps": completed,
        "optimizer_steps_executed_this_process": completed - resumed_from,
        "checkpoint_interval": checkpoint_interval, "signature": signature,
        "active_optimizer_elapsed_s": time.monotonic() - started,
        "non_durable_updates_may_be_replayed": True, "retained_history_is_not_total_compute_across_failed_attempts": True,
        "rng_sources": ["python", "numpy_global", "torch_cpu", "all_initialized_cuda", "explicit_batch_sampler"]}
    write_json(folder / "act_training.json", {"losses": losses, "device": device,
        "elapsed_s": time.monotonic() - started, "official_implementation": "lerobot.policies.act.ACTPolicy",
        "training_resume": optimizer.explorer_resume_info})
    return optimizer, losses, started


def save_reload_act(policy, optimizer, folder, preprocessing, check_batch, device):
    checkpoint = folder / "student_act"
    policy.eval()
    policy.save_pretrained(checkpoint, push_to_hub=False)
    torch.save(optimizer.state_dict(), folder / "act_optimizer.pt")
    write_json(checkpoint / "explorer_preprocessing.json", preprocessing)
    reloaded = type(policy).from_pretrained(checkpoint, local_files_only=True, strict=True).to(device).eval()
    with torch.inference_mode():
        expected = policy.predict_action_chunk(check_batch)
        actual = reloaded.predict_action_chunk(check_batch)
    reload_error = float((actual - expected).abs().max().cpu())
    if reload_error > 1e-6:
        raise RuntimeError("Official ACT checkpoint reload mismatch")
    return reloaded, checkpoint, reload_error


def preprocessing_config(low, high, active_indices=()):
    # The serialized pilot_active_action_indices spelling is retained for byte
    # compatibility only; adapters supply explicit native endpoints and indices.
    return {"schema_version": 1, "image_key": IMAGE_KEY, "rgb_layout": "NCHW",
                     "rgb_scale": "uint8 / 255", "mean": [.485, .456, .406], "std": [.229, .224, .225],
                     "state_basis": "command_estimate", "state_low": low.tolist(), "state_high": high.tolist(),
                     "state_transform": "2*(state-low)/(high-low)-1", "action_transform": "same fixed endpoint normalization",
                     "privileged_target_xyz_in_actor": False, "temporal_ensembling": False,
                     "chunk_execution_steps": 1, "pilot_active_action_indices": list(active_indices)}


def framework_info():
    framework = {name: importlib.metadata.version(name) for name in ("torch", "torchvision")}
    outside_bounds = Version(framework["torch"].split("+")[0]) >= Version("2.12") or Version(framework["torchvision"].split("+")[0]) >= Version("0.27")
    return {"framework_versions": framework,
            "framework_compatibility_note": "Runtime tested outside LeRobot declared Torch/torchvision upper bounds" if outside_bounds else "Torch/torchvision versions lie within LeRobot 0.6.1 declared bounds"}

