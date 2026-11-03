"""Load exactly the saved policy+SAM adapter+processors. No motion fallback.

Live physical execution is deliberately a separate explicit-start worker. This
module can also run read-only recorded-frame inference for software validation.
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from pathlib import Path

from .features import FeatureContract, object_state, sha256_file, validate_raw_frame


@dataclass
class LoadedPolicy:
    policy: object
    preprocessor: object
    postprocessor: object
    contract: FeatureContract
    checkpoint: Path
    technical_test: bool
    device: str

    def prepare_observation(self, frame: dict, images: dict, annotation: dict | None, task: str, *, allow_test=False):
        import numpy as np
        import torch
        validate_raw_frame(frame, self.contract, allow_test=allow_test)
        if self.contract.mode == "sam_objects":
            from .annotations import validate_frame_annotations
            validate_frame_annotations(annotation, frame, require_cameras=self.contract.cameras,
                                       allow_test=allow_test, verify_masks=False)
        state = object_state(frame, annotation, self.contract, allow_test=allow_test)
        batch = {"observation.state": torch.tensor(state, dtype=torch.float32), "task": task}
        for cam in self.contract.cameras:
            image = np.asarray(images[cam])
            if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
                raise ValueError("Inference requires original uint8 RGB HWC input")
            from .policies import image_tensor
            size=self.policy.config.input_features[f"observation.images.{cam}"].shape[-1] if self.policy.name=="act" else None
            batch[f"observation.images.{cam}"] = image_tensor(image,size=size)
        return self.preprocessor(batch)

    def predict(self, frame, images, annotation=None, task="", *, noise=None, allow_test=False):
        import torch
        if self.technical_test and not allow_test:
            raise ValueError("Tiny random smoke checkpoint не разрешён для аппаратного inference")
        prepared = self.prepare_observation(frame, images, annotation, task, allow_test=allow_test)
        with torch.inference_mode():
            self.policy.reset()  # queue never carries stale actions across fresh observations
            normalized = self.policy.select_action(prepared) if self.policy.name=="act" else self.policy.select_action(prepared, noise=noise)
            action = self.postprocessor(normalized).detach().cpu()
        if action.shape != (1, 6) or not torch.isfinite(action).all():
            raise RuntimeError("Policy returned nonfinite/invalid action")
        return action[0].tolist()


def load_checkpoint(path: Path, device="cpu", *, allow_technical=False, resource_check=True, allow_untrained=False) -> LoadedPolicy:
    path = Path(path).resolve()
    if (path / "pretrained_model").is_dir():
        folder = path / "pretrained_model"
        checkpoint = path
    else:
        folder = path
        checkpoint = path.parent
    manifest = json.loads((checkpoint / "checkpoint.json").read_text())
    if manifest.get("status") != "complete":
        raise ValueError("Checkpoint is incomplete")
    if int(manifest.get("step", 0)) < 1 and not allow_untrained:
        raise ValueError("Checkpoint ещё не содержит ни одного training update; разрешён только resume")
    technical = bool(manifest.get("technical_test"))
    if technical and not allow_technical:
        raise ValueError("Это технический tiny checkpoint, не обученная политика для робота")
    contract = FeatureContract.from_dict(json.loads((folder / "object_adapter.json").read_text()))
    if contract.digest != manifest["feature_sha256"]:
        raise ValueError("Object adapter hash differs from trained checkpoint")
    if sha256_file(folder / "model.safetensors") != manifest["model_sha256"]:
        raise ValueError("Policy weights checksum mismatch")
    if resource_check:
        from .resources import assert_compute_available
        assert_compute_available(device, minimum_available_gb=.6 if technical else 5.0)
    # All model and tokenizer assets local; no HF credentials or download needed.
    import os
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from .policies import components
    from lerobot.processor import PolicyProcessorPipeline
    from lerobot.processor.converters import policy_action_to_transition, transition_to_policy_action
    kind=json.loads((folder/'config.json').read_text()).get('type','smolvla')
    Config,Policy,_=components(kind)
    cfg=Config.from_pretrained(folder,local_files_only=True);cfg.device=device;cfg.n_action_steps=1
    overrides={'device_processor':{'device':device}}
    if kind=='smolvla':
        cfg.vlm_model_name=str(folder/'backbone');cfg.load_vlm_weights=False;cfg.rtc_config=None
        overrides['tokenizer_processor']={'tokenizer_name':str(folder/'backbone')}
    else:cfg.pretrained_backbone_weights=None
    policy=Policy.from_pretrained(folder,config=cfg,local_files_only=True,strict=True)
    pre=PolicyProcessorPipeline.from_pretrained(folder,config_filename='policy_preprocessor.json',local_files_only=True,overrides=overrides)
    post = PolicyProcessorPipeline.from_pretrained(folder, config_filename="policy_postprocessor.json", local_files_only=True,
             to_transition=policy_action_to_transition, to_output=transition_to_policy_action)
    policy.eval()
    policy.reset()
    return LoadedPolicy(policy, pre, post, contract, checkpoint, technical, device)


def run_policy(checkpoint: Path, options: dict, progress=None, stop=None):
    """Explicit-start hardware worker: fresh observation -> real SAM -> policy action.

    No retry of bus writes. No action is sent after a camera/SAM/staleness failure.
    Root job supervisor must only dispatch this in response to the UI Start action.
    """
    from .resources import ResourceLease, assert_compute_available
    device = options.get("device", "cuda")
    assert_compute_available(device, minimum_available_gb=7.0)
    with ResourceLease("gpu_compute" if device.startswith("cuda") else "cpu_compute"):
        loaded = load_checkpoint(checkpoint, device=device, resource_check=False)
        return _run_policy_loaded(loaded, options, progress, stop)


def _run_policy_loaded(loaded, options, progress, stop):
    import hashlib
    import io
    from PIL import Image
    from .hardware import HardwareSource, hardware_settings
    if not options.get("explicit_user_start"):
        raise PermissionError("Нужен явный запуск политики в новом UI")
    segmenter = None
    associators = {}
    if loaded.contract.mode == "sam_objects":
        from .annotations import SAM3Segmenter
        lineage = json.loads((loaded.checkpoint / "pretrained_model/lineage.json").read_text())
        provenance = lineage.get("annotation_provenance") or {}
        if provenance.get("backend") != "sam3":
            raise ValueError("Inference requires trained real SAM3 provenance")
        sam_options = {"device": loaded.device,
                       "checkpoint_path": provenance.get("checkpoint_path"),
                       "prompts": provenance.get("prompts"),
                       "confidence_threshold": provenance.get("confidence_threshold", .5)}
        segmenter = SAM3Segmenter(sam_options).load()  # before bus/torque
        for key in ("checkpoint_sha256", "prompts", "confidence_threshold", "preprocessing", "source", "tracking"):
            if segmenter.provenance.get(key) != provenance.get(key):
                raise ValueError(f"Train/inference SAM {key} differs")
    settings = hardware_settings(options.get("hardware", {}))
    steps = 0
    max_steps = int(options.get("max_steps", 300))
    task = str(options.get("task", ""))
    if not task.strip():
        raise ValueError("Нужен текст задачи для inference")
    session = HardwareSource(settings, teleoperate=False)
    try:
        session.start(explicit_user_start=True)
        while steps < max_steps and not (stop and stop()):
            observed = session.observe()
            images = {cam: item["image"] for cam, item in observed["images"].items()}
            frame = {k: v for k, v in observed.items() if k != "images"}
            frame["frame_id"] = f"live_{steps:08d}"
            frame["cameras"] = {}
            for cam, item in observed["images"].items():
                # Same raw PNG content hash contract as offline recording.
                buffer = io.BytesIO()
                Image.fromarray(item["image"]).save(buffer, format="PNG")
                frame["cameras"][cam] = {"timestamp_ns": item["timestamp_ns"], "valid": True,
                    "sha256": hashlib.sha256(buffer.getvalue()).hexdigest(), "path": None}
            annotation = segmenter.annotate_observation(frame, images, associators) if segmenter else None
            action = loaded.predict(frame, images, annotation, task)
            elapsed = (time.monotonic_ns()-frame["timestamp_ns"])/1e6
            if elapsed > float(options.get("max_observation_age_ms", 2000)):
                raise RuntimeError(f"Policy/SAM observation устарело на {elapsed:.0f} ms; действие не отправлено")
            if stop and stop():
                break
            # Fresh measured state gates execution after potentially slow VLA/SAM.
            # Prediction still comes solely from the recorded-schema learned policy.
            fresh = session.observe()
            if max(abs(a-b) for a,b in zip(fresh["observation.state"], frame["observation.state"])) > .5:
                raise RuntimeError("Robot state changed during inference; action not sent")
            sent = session.send(action, fresh)  # one send, no retry
            steps += 1
            if progress:
                progress({"state": "inference", "steps": steps, "observation_age_ms": elapsed,
                          "action": sent["action"], "policy_checkpoint": str(loaded.checkpoint), "physical_actions": True})
    finally:
        session.close()
    return {"status": "stopped" if stop and stop() else "completed", "steps": steps, "checkpoint": str(loaded.checkpoint)}
