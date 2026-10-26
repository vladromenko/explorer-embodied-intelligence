"""Official Diffusion-specific durable optimizer; no ACT loss/AdamW assumptions.

Uses the existing shared atomic checkpoint/RNG/sampler primitives while binding
actual official optimizer preset, Diffusion/scheduler source and history view.
No hardware, source generation, deployment, camera or network operation.
"""
import fcntl
import importlib.metadata
import inspect
import json
from pathlib import Path
import time

import torch
from torch.utils.data import default_collate
from .artifacts import sha,state_digest,write_json
from . import diffusion_backend
from . import checkpoint_control
from .act_backend import (_plain,_resume_signature,_BatchPlan,_save_resume,_restore_rng,_rng_state)

def optimize_diffusion(policy, samples, settings, folder, update, progress_start=.60, progress_end=.85,
                 resume_context=None, checkpoint_interval=25):
    """One optimizer writer per job; preserve the existing public call/return."""
    directory = Path(folder) / "diffusion_resume"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "optimizer.lock").open("a") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another Diffusion optimizer owns this job directory") from error
        return _optimize_diffusion_locked(policy, samples, settings, folder, update, progress_start, progress_end,
                                    resume_context, checkpoint_interval)


def _optimize_diffusion_locked(policy, samples, settings, folder, update, progress_start, progress_end,
                         resume_context, checkpoint_interval):
    """Actual optimizer continuation; untyped legacy Datasets remain non-resumable."""
    device, config = settings["device"], policy.config
    if device != 'cpu':
        raise ValueError('Diffusion optimizer currently has CPU qualification scope only')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if len(samples) < 1 or settings["optimizer_steps"] < 1 or settings["batch_size"] < 1:
        raise ValueError("Diffusion optimization requires nonempty samples and positive steps/batch size")
    if isinstance(checkpoint_interval, bool) or not isinstance(checkpoint_interval, int) or checkpoint_interval < 1:
        raise ValueError("Diffusion checkpoint interval must be a positive integer")
    context = resume_context if resume_context is not None else getattr(samples, "resume_context", None)
    if not isinstance(context, dict):
        raise ValueError("Source-pinned Diffusion resume context is required")
    context = _plain(context)
    preset = config.get_optimizer_preset()
    scheduler_source = inspect.getsourcefile(type(policy.diffusion.noise_scheduler)) if hasattr(policy,"diffusion") else None
    implementation_files = [__file__, diffusion_backend.__file__]
    if scheduler_source:
        implementation_files.append(scheduler_source)
    context["diffusion_optimizer"] = {"preset": _plain(preset), "diffusers_version": importlib.metadata.version("diffusers"),
        "implementation_sha256": {str(path): sha(path) for path in implementation_files}}
    context["source_files"] = context.get("source_files", []) + [{"path": str(path), "sha256": sha(path)} for path in implementation_files]
    signature, metadata = _resume_signature(policy, samples, settings, context, checkpoint_interval) if context is not None else (None, None)
    optimizer = preset.build(policy.get_optim_params())
    plan = _BatchPlan(len(samples), min(settings["batch_size"], len(samples)), settings["seed"])
    losses, completed, elapsed_before = [], 0, 0.0
    directory = folder / "diffusion_resume"
    pointer_path = directory / "latest.json"
    loaded = pointer_path.is_file()
    if loaded:
        pointer = json.loads(pointer_path.read_text())
        name = pointer["checkpoint"]
        if Path(name).name != name or sha(directory / name) != pointer["sha256"]:
            raise ValueError("Diffusion resume checkpoint path/checksum mismatch; retained for diagnosis")
        if signature is None or pointer["signature"] != signature:
            raise ValueError("Diffusion resume source/settings/implementation signature changed; checkpoint retained")
        payload = torch.load(directory / name, map_location="cpu", weights_only=True)
        if (payload.get("schema_version") != 1 or payload["signature"] != signature or payload["metadata"] != metadata
                or pointer["completed_steps"] != payload["completed_steps"]):
            raise ValueError("Diffusion resume payload signature mismatch")
        losses, completed = payload["losses"], payload["completed_steps"]
        if (len(losses) != completed or completed > settings["optimizer_steps"]
                or [row["step"] for row in losses] != list(range(1, completed + 1))):
            raise ValueError("Diffusion resume loss/optimizer step history is inconsistent")
        steps = [int(v["step"].item()) for v in payload["optimizer"]["state"].values() if "step" in v]
        if (completed and (not steps or max(steps) != completed)) or (not completed and steps):
            raise ValueError("Diffusion resume optimizer state does not match the durable step history")
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
    control=checkpoint_control.OptimizerCheckpointControl(folder,'diffusion',signature,completed)
    def validate_pause_source():
        current,_=_resume_signature(policy,samples,settings,context,checkpoint_interval)
        if current!=signature:raise ValueError('Diffusion source changed before durable pause')
    control.maybe_pause(completed,save,validate_pause_source)
    for step in range(completed, settings["optimizer_steps"]):
        batch = default_collate([samples[index] for index in plan.next()])
        batch = {key: value.to(device) for key, value in batch.items()}
        policy.train()
        optimizer.zero_grad(set_to_none=True)
        loss, terms = policy(batch)
        if not torch.isfinite(loss):
            raise RuntimeError("Diffusion produced a nonfinite loss")
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(policy.parameters(), preset.grad_clip_norm, error_if_nonfinite=True)
        optimizer.step()
        losses.append(_plain({"step": step + 1, "loss": float(loss.detach().cpu()),
                       "gradient_norm": float(norm.detach().cpu()), **(terms or {})}))
        completed = step + 1
        checkpoint_due = completed % checkpoint_interval == 0 or completed == settings["optimizer_steps"]
        if signature is not None and checkpoint_due:
            if completed == settings["optimizer_steps"]:
                final_signature, _ = _resume_signature(policy, samples, settings, context, checkpoint_interval)
                if final_signature != signature:
                    raise ValueError("Diffusion source/settings/implementation changed during optimization")
            save()
        if completed % 10 == 0 or checkpoint_due:
            write_json(folder / "diffusion_training.json", {"losses": losses, "device": device,
                       "elapsed_s": time.monotonic() - started, "official_implementation": type(policy).__module__ + "." + type(policy).__qualname__})
            # UI/logging callbacks cannot change the learner's random sequence.
            rng = _rng_state()
            try:
                update(progress_start + (progress_end - progress_start) * completed / settings["optimizer_steps"], "optimizing_official_lerobot_diffusion")
            finally:
                _restore_rng(rng)
        if completed==1 or checkpoint_due:control.announce('optimizing',completed)
        control.maybe_pause(completed,save,validate_pause_source)
    control.announce('optimizer_finished',completed)
    optimizer.explorer_resume_info = {"supported": signature is not None, "checkpoint_loaded": loaded,
        "resumed_from_step": resumed_from, "completed_steps": completed,
        "optimizer_steps_executed_this_process": completed - resumed_from,
        "checkpoint_interval": checkpoint_interval, "signature": signature,
        "active_optimizer_elapsed_s": time.monotonic() - started,
        "non_durable_updates_may_be_replayed": True, "retained_history_is_not_total_compute_across_failed_attempts": True,
        "rng_sources": ["python", "numpy_global", "torch_cpu", "all_initialized_cuda", "explicit_batch_sampler"]}
    write_json(folder / "diffusion_training.json", {"losses": losses, "device": device,
        "elapsed_s": time.monotonic() - started, "official_implementation": type(policy).__module__ + "." + type(policy).__qualname__,
        "training_resume": optimizer.explorer_resume_info})
    return optimizer, losses, started

