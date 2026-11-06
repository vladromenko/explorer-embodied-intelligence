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

MAX_OBSERVATION_AGE_MS = 2000


@dataclass
class LoadedPolicy:
    policy: object
    preprocessor: object
    postprocessor: object
    contract: FeatureContract
    checkpoint: Path
    technical_test: bool
    device: str
    annotation_provenance: dict | None = None

    def prepare_observation(self, frame: dict, images: dict, annotation: dict | None, task: str, *, allow_test=False):
        import numpy as np
        import torch
        validate_raw_frame(frame, self.contract, allow_test=allow_test)
        if self.contract.mode == "sam_objects":
            from .annotations import validate_frame_annotations,validate_annotation_provenance
            validate_frame_annotations(annotation, frame, require_cameras=self.contract.cameras,
                                       allow_test=allow_test, verify_masks=False)
            validate_annotation_provenance(annotation.get("provenance"),allow_test=allow_test,expected=self.annotation_provenance)
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
            raise ValueError("Технический checkpoint не разрешён для аппаратного inference")
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
        raise ValueError("Это технический checkpoint, не обученная политика для робота")
    contract = FeatureContract.from_dict(json.loads((folder / "object_adapter.json").read_text()))
    if contract.digest != manifest["feature_sha256"]:
        raise ValueError("Object adapter hash differs from trained checkpoint")
    if sha256_file(folder / "model.safetensors") != manifest["model_sha256"]:
        raise ValueError("Policy weights checksum mismatch")
    files=manifest.get('files_sha256')
    if not files and not technical:raise ValueError('Checkpoint не содержит integrity manifest processors/config; требуется повторное сохранение')
    for relative,digest in (files or {}).items():
        component=(folder/relative).resolve()
        if not component.is_relative_to(folder) or sha256_file(component)!=digest:raise ValueError('Checkpoint component checksum mismatch: '+relative)
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
    lineage=json.loads((folder/"lineage.json").read_text())
    return LoadedPolicy(policy, pre, post, contract, checkpoint, technical, device,lineage.get("annotation_provenance"))


def _load_segmenter(loaded):
    if loaded.contract.mode != "sam_objects":
        return None
    from .annotations import SAM3Segmenter, validate_annotation_provenance
    provenance = loaded.annotation_provenance or {}
    if provenance.get("backend") != "sam3":
        raise ValueError("Inference requires trained real SAM3 provenance")
    segmenter = SAM3Segmenter({"device": loaded.device,
        "checkpoint_path": provenance.get("checkpoint_path"), "prompts": provenance.get("prompts"),
        "confidence_threshold": provenance.get("confidence_threshold", .5)}).load()
    validate_annotation_provenance(segmenter.provenance, expected=provenance)
    return segmenter


def benchmark_observation(loaded, frame, images, task, segmenter=None, *, stop=None):
    """A frozen recorded observation, two actual model forwards, zero device I/O.

    First pass includes warmup; second is a latency estimate, not proof of live
    control or grasp quality. Original timestamps/hashes stay unchanged.
    """
    samples = []
    for index in range(2):
        if stop and stop():return {"status": "stopped", "samples": samples, "physical_actions": False}
        before = time.monotonic()
        annotation = segmenter.annotate_observation(frame, images, {}) if segmenter else None
        after_sam = time.monotonic()
        if stop and stop():return {"status": "stopped", "samples": samples, "physical_actions": False}
        action = loaded.predict(frame, images, annotation, task)
        ended = time.monotonic()
        samples.append({"warmup": index == 0, "sam_ms": (after_sam-before)*1000,
                        "policy_ms": (ended-after_sam)*1000, "total_ms": (ended-before)*1000})
    ready = samples[-1]["total_ms"] < MAX_OBSERVATION_AGE_MS
    return {"status": "PASS" if ready else "LATENCY_EXCEEDS_LIMIT", "samples": samples,
            "observation_age_limit_ms": MAX_OBSERVATION_AGE_MS, "latency_within_limit": ready,
            "physical_actions": False, "last_prediction": action,
            "limitations": "Архивный кадр: без USB/torque. Это оценка задержки моделей; live USB/STOP и качество захвата проверяются оператором"}


def check_checkpoint(path, options, progress=None, stop=None):
    """UI load check also runs the full SAM→policy chain on archived raw, if present."""
    from .resources import ROOT, ResourceLease
    from .features import safe_child
    device = options.get("device", "cuda")
    with ResourceLease("gpu_compute" if device.startswith("cuda") else "cpu_compute"):
        loaded = load_checkpoint(path, device=device)
        result = {"checkpoint": str(path), "loaded": True, "contract": loaded.contract.to_dict(),
                  "physical_actions": False}
        manifest=loaded.checkpoint/'checkpoint.json'
        if manifest.is_file():result['checkpoint_manifest_sha256']=sha256_file(manifest)
        if stop and stop():return {**result, "status": "stopped"}
        folder = loaded.checkpoint / "pretrained_model"
        lineage = json.loads((folder / "lineage.json").read_text())
        dataset = Path(lineage.get("dataset_path", "")).resolve()
        if not dataset.is_relative_to(ROOT / "data") or not dataset.is_dir():
            return {**result, "latency_check": {"status": "NOT_TESTED", "reason": "Исходные raw-данные недоступны. Веса загружены; для проверки задержки нужен сохранённый эпизод"}}
        episode_id = (lineage["split"].get("validation") or lineage["split"]["train"])[0]
        episode = safe_child(dataset / "episodes", episode_id)
        frames_file = episode / "frames.jsonl"
        expected = lineage["dataset_hashes"].get(str(frames_file.relative_to(dataset)))
        if expected != sha256_file(frames_file):raise ValueError("Raw-эпизод изменился после обучения; проверка checkpoint остановлена")
        frame = None
        rejected = 0
        for line in frames_file.read_text().splitlines():
            if line.strip():
                candidate = json.loads(line)
                try:validate_raw_frame(candidate, loaded.contract)
                except (ValueError,KeyError,TypeError):rejected += 1
                else:
                    frame = candidate
                    break
        if frame is None:raise ValueError('В выбранном эпизоде нет пригодного observation для проверки политики')
        result['sample_frame_id']=frame['frame_id']
        result['rejected_observations_before_sample']=rejected
        import numpy as np
        from PIL import Image
        images = {}
        for camera in loaded.contract.cameras:
            source = safe_child(episode, frame["cameras"][camera]["path"])
            if sha256_file(source) != frame["cameras"][camera]["sha256"]:raise ValueError("Raw image hash mismatch")
            with Image.open(source) as image:images[camera] = np.array(image.convert("RGB"), copy=True)
        if progress:progress({"state": "checking_inference", "message": "Проверяем SAM → политику на сохранённом кадре. Руки не открываются"})
        segmenter = _load_segmenter(loaded)
        task = options.get("task") or json.loads((episode / "episode.json").read_text()).get("task", "")
        result["latency_check"] = benchmark_observation(loaded, frame, images, task, segmenter, stop=stop)
        return result


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
        segmenter = _load_segmenter(loaded)  # before bus/torque
    settings = hardware_settings(options.get("hardware", {}))
    steps = 0
    max_steps = int(options.get("max_steps", 300))
    task = str(options.get("task", ""))
    if not task.strip() and loaded.policy.name=="smolvla":
        raise ValueError("Нужен текст задачи для SmolVLA inference")
    if stop and stop():return {"status":"stopped","steps":0,"checkpoint":str(loaded.checkpoint)}
    session = HardwareSource(settings, teleoperate=False)
    try:
        session.start(explicit_user_start=True,stop=stop)
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
            if elapsed > float(options.get("max_observation_age_ms", MAX_OBSERVATION_AGE_MS)):
                raise RuntimeError(f"Policy/SAM observation устарело на {elapsed:.0f} ms; действие не отправлено")
            if stop and stop():
                break
            # Fresh measured state gates execution after potentially slow VLA/SAM.
            # Prediction still comes solely from the recorded-schema learned policy.
            fresh = session.observe()
            if max(abs(a-b) for a,b in zip(fresh["observation.state"], frame["observation.state"])) > .5:
                raise RuntimeError("Robot state changed during inference; action not sent")
            validate_policy_target(action,session.calibration)
            sent = session.send(action, fresh)  # one send, no retry
            steps += 1
            if progress:
                progress({"state": "inference", "steps": steps, "observation_age_ms": elapsed,
                          "action": sent["action"], "policy_checkpoint": str(loaded.checkpoint), "physical_actions": True})
    finally:
        stop_errors=session.close()
        if stop_errors:raise RuntimeError("Снятие torque не подтверждено: проверьте аппаратный STOP. "+str(stop_errors))
    return {"status": "stopped" if stop and stop() else "completed", "steps": steps, "checkpoint": str(loaded.checkpoint)}


def validate_policy_target(action,calibration):
    """Reject an out-of-envelope learned prediction before any servo write."""
    import math
    from .hardware import JOINTS
    if len(action)!=6 or not all(math.isfinite(float(x)) for x in action):raise ValueError('Политика вернула невалидные суставные команды')
    limits=calibration['values']['follower']
    for i,name in enumerate(JOINTS):
        if name=='gripper':low,high=0.,100.
        else:
            cal=limits[name];mid=(cal['range_min']+cal['range_max'])/2
            low=max(-175.,(cal['range_min']-mid)*360/4095);high=min(175.,(cal['range_max']-mid)*360/4095)
        if not low<=float(action[i])<=high:raise ValueError(f'Предсказание политики {name}={action[i]:.3f} вне безопасного диапазона [{low:.3f},{high:.3f}]; команда отклонена')
    return True
