"""Real LeRobot SmolVLA flow-matching training with an explicit SAM state adapter.

Heavy libraries are imported only inside worker calls. Nothing in this module
opens hardware, downloads models by default or substitutes a controller.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from .features import FeatureContract, action_state, object_state, safe_child, sha256_file, validate_raw_frame

ROOT = Path(__file__).resolve().parents[2]
BASE_REVISION = ""  # resolved cached snapshot revision and recorded in lineage
BACKBONE_REVISION = "7b375e1b73b11138ff12fe22c8f2822d8fe03467"


def _json(path):
    return json.loads(Path(path).read_text())


def _atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    os.replace(temp, path)


def cached_snapshot(repo: str, *, revision: str | None = None, require_weights=True) -> Path:
    """Read-only cached models. Never changes existing HF cache or credentials."""
    base = Path.home() / ".cache/huggingface/hub" / ("models--" + repo.replace("/", "--"))
    if revision:
        paths = [base / "snapshots" / revision]
    else:
        ref = base / "refs/main"
        paths = [base / "snapshots" / ref.read_text().strip()] if ref.exists() else sorted((base / "snapshots").glob("*"))
    for path in paths:
        if (path / "config.json").is_file() and (not require_weights or (path / "model.safetensors").is_file()):
            return path
    raise FileNotFoundError(f"Локальная модель {repo} не найдена полностью. Нужен cache/access; автоматической публикации/загрузки нет.")


def _copy_backbone_assets(source: Path, dest: Path, *, tiny=False):
    dest.mkdir(parents=True, exist_ok=True)
    for file in source.iterdir():
        if file.name.endswith((".json", ".txt", ".model")):
            shutil.copyfile(file, dest / file.name)
    if tiny:
        cfg = _json(dest / "config.json")
        text = cfg["text_config"]
        text.pop("torch_dtype", None)
        text.pop("dtype", None)
        text.update(hidden_size=64, intermediate_size=128, num_hidden_layers=2,
                    num_attention_heads=4, num_key_value_heads=2, head_dim=16,
                    max_position_embeddings=512, attention_dropout=0.0)
        vision = cfg["vision_config"]
        vision.update(hidden_size=32, intermediate_size=64, num_hidden_layers=1,
                      num_attention_heads=4, image_size=32, patch_size=8)
        cfg.update(scale_factor=2, pad_token_id=2)
        cfg.pop("torch_dtype", None)
        _atomic_json(dest / "config.json", cfg)


class RawEpisodeDataset:
    """Strict raw index; action chunks never cross episode boundaries."""
    def __init__(self, dataset_path: Path, contract: FeatureContract, *, chunk_size=50, allow_test_data=False):
        self.path = Path(dataset_path).resolve()
        self.meta = _json(self.path / "dataset.json")
        self.contract = contract
        self.chunk_size = int(chunk_size)
        self.allow_test_data = bool(allow_test_data)
        if self.meta.get("source") != "hardware" and not allow_test_data:
            raise ValueError("Тестовый источник исключён из обычного обучения")
        self.episodes = []
        self.samples = []
        self.rejections = []
        self.annotation_provenance = None
        for ep in sorted((self.path / "episodes").glob("*")):
            if not ep.is_dir() or ep.name.endswith(".partial") or not (ep / "episode.json").exists():
                continue
            info = _json(ep / "episode.json")
            if info.get("status") != "complete":
                continue
            if info.get("source", self.meta.get("source")) != "hardware" and not allow_test_data:
                continue
            rows = [_json_line(x) for x in (ep / "frames.jsonl").read_text().splitlines() if x.strip()]
            if not rows:
                continue
            times = [int(r["timestamp_ns"]) for r in rows]
            if any(b <= a for a, b in zip(times, times[1:])):
                raise ValueError(f"Немонoтонные raw timestamps: {ep.name}")
            annotations = None
            if contract.mode == "sam_objects":
                from .annotations import validate_frame_annotations
                annotation_path = ep / "annotations" / contract.annotation_version / "frames.jsonl"
                annotation_index = {str(a["frame_id"]): a for a in (_json_line(line) for line in annotation_path.read_text().splitlines() if line.strip())}
                annotations = []
                for row in rows:
                    ann = annotation_index.get(str(row["frame_id"]))
                    if ann is None:
                        raise ValueError(f"Missing SAM annotations for {ep.name}/{row['frame_id']}")
                    validate_frame_annotations(ann, row, ep, require_cameras=contract.cameras, allow_test=allow_test_data)
                    # Strict loader + feature validation detects stale source/time/provenance.
                    object_state(row, ann, contract, allow_test=allow_test_data)
                    provenance = ann.get("provenance", {})
                    if self.annotation_provenance is None:
                        self.annotation_provenance = provenance
                    elif _annotation_identity(provenance) != _annotation_identity(self.annotation_provenance):
                        raise ValueError("Смешаны SAM модели/config/preprocessing: нужна единая разметка")
                    annotations.append(ann)
            episode_index = len(self.episodes)
            valid = []
            for index, row in enumerate(rows):
                try:
                    validate_raw_frame(row, contract, training=True, allow_test=allow_test_data)
                    for cam in contract.cameras:
                        image_path = safe_child(ep, row["cameras"][cam]["path"])
                        if annotations is None and sha256_file(image_path) != row["cameras"][cam]["sha256"]:
                            raise ValueError(f"Raw image hash mismatch: {image_path}")
                    valid.append(index)
                except (ValueError, FileNotFoundError, KeyError) as exc:
                    self.rejections.append({"episode": ep.name, "frame": row.get("frame_id"), "reason": str(exc)})
            if not valid:
                continue
            # Invalid target actions never silently enter chunks.
            for row in rows:
                action_state(row)
                if row.get("action_valid", True) is not True and not allow_test_data:
                    raise ValueError(f"Недействительный action в эпизоде {ep.name}")
            self.episodes.append({"path": ep, "info": info, "rows": rows, "annotations": annotations, "valid": valid})
            self.samples.extend((episode_index, i) for i in valid)
        if not self.samples:
            raise ValueError("Нет пригодных завершённых эпизодов")

    def __len__(self):
        return len(self.samples)

    def vector(self, ep_index, frame_index):
        ep = self.episodes[ep_index]
        annotation = ep["annotations"][frame_index] if ep["annotations"] is not None else None
        return object_state(ep["rows"][frame_index], annotation, self.contract, allow_test=self.allow_test_data)

    def __getitem__(self, index):
        import numpy as np
        import torch
        from PIL import Image
        ei, fi = self.samples[index]
        ep = self.episodes[ei]
        row = ep["rows"][fi]
        result = {"observation.state": torch.tensor(self.vector(ei, fi), dtype=torch.float32),
                  "task": ep["info"].get("task", self.meta.get("task", ""))}
        for cam in self.contract.cameras:
            with Image.open(safe_child(ep["path"], row["cameras"][cam]["path"])) as image:
                pixels = np.array(image.convert("RGB"), copy=True)
            result[f"observation.images.{cam}"] = torch.from_numpy(pixels).permute(2, 0, 1).float() / 255
        end = min(fi + self.chunk_size, len(ep["rows"]))
        chunk = [action_state(r) for r in ep["rows"][fi:end]]
        pad_count = self.chunk_size - len(chunk)
        result["action"] = torch.tensor(chunk + [chunk[-1]] * pad_count, dtype=torch.float32)
        result["action_is_pad"] = torch.tensor([False] * len(chunk) + [True] * pad_count)
        return result

    def split(self, seed=42, validation_fraction=.2):
        if len(self.episodes) < 2:
            raise ValueError("Для независимой validation нужны минимум 2 целых эпизода, желательно 2 сессии")
        sessions = {}
        for i, ep in enumerate(self.episodes):
            sessions.setdefault(ep["info"].get("session_id", ep["path"].name), []).append(i)
        groups = list(sessions.values()) if len(sessions) >= 2 else [[i] for i in range(len(self.episodes))]
        random.Random(seed).shuffle(groups)
        num_val = max(1, min(len(groups)-1, math.ceil(len(groups)*validation_fraction)))
        val_ep = {i for group in groups[:num_val] for i in group}
        train_indices = [i for i, (ei, _) in enumerate(self.samples) if ei not in val_ep]
        val_indices = [i for i, (ei, _) in enumerate(self.samples) if ei in val_ep]
        manifest = {"unit": "session" if len(sessions) >= 2 else "episode", "seed": seed,
                    "train": [e["path"].name for i, e in enumerate(self.episodes) if i not in val_ep],
                    "validation": [e["path"].name for i, e in enumerate(self.episodes) if i in val_ep],
                    "train_samples": len(train_indices), "validation_samples": len(val_indices),
                    "warning": None if len(sessions) >= 2 else "Одна сессия: split по эпизодам, нужен независимый сбор для оценки обобщения"}
        return train_indices, val_indices, manifest

    def stats(self, indices):
        import torch
        states = torch.tensor([self.vector(*self.samples[i]) for i in indices])
        # Normalize measured joints only. Validity, boxes, score and target stay 0..1.
        state_mean = torch.zeros(self.contract.state_dim)
        state_std = torch.ones(self.contract.state_dim)
        state_mean[:6] = states[:, :6].mean(0)
        state_std[:6] = states[:, :6].std(0, correction=0).clamp_min(1e-3)
        actions = torch.tensor([action_state(self.episodes[ei]["rows"][fi]) for i in indices for ei, fi in [self.samples[i]]])
        return {"observation.state": {"mean": state_mean, "std": state_std},
                "action": {"mean": actions.mean(0), "std": actions.std(0, correction=0).clamp_min(1e-3)}}


def _annotation_identity(provenance):
    selected = {key: provenance.get(key) for key in ("backend", "checkpoint_sha256", "prompts", "confidence_threshold", "preprocessing", "source", "configuration_hash")}
    return json.dumps(selected, sort_keys=True)


def _json_line(line):
    return json.loads(line)


def _batch(dataset, indices):
    import torch
    samples = [dataset[i] for i in indices]
    return {key: [s[key] for s in samples] if key == "task" else torch.stack([s[key] for s in samples]) for key in samples[0]}


def _seed(seed):
    import numpy as np
    import torch
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _config(contract, backbone, options, *, tiny=False, base=None):
    from lerobot.configs import FeatureType, PolicyFeature
    from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
    cfg = SmolVLAConfig.from_pretrained(str(base), local_files_only=True) if base else SmolVLAConfig()
    cfg.device = options.get("device", "cpu" if tiny else "cuda")
    cfg.vlm_model_name = str(backbone)
    cfg.load_vlm_weights = False  # All weights loaded once from full policy file.
    cfg.input_features = {"observation.state": PolicyFeature(type=FeatureType.STATE, shape=(contract.state_dim,))}
    image_size = int(options.get("image_size", 32 if tiny else 256))
    cfg.input_features.update({f"observation.images.{cam}": PolicyFeature(type=FeatureType.VISUAL, shape=(3, image_size, image_size)) for cam in contract.cameras})
    cfg.output_features = {"action": PolicyFeature(type=FeatureType.ACTION, shape=(6,))}
    cfg.chunk_size = int(options.get("chunk_size", 2 if tiny else 50))
    cfg.n_action_steps = 1  # Reobserve each step. No stale cached action queue/RTC.
    cfg.resize_imgs_with_padding = (image_size, image_size)
    cfg.num_steps = int(options.get("denoising_steps", 2 if tiny else 10))
    cfg.freeze_vision_encoder = True
    cfg.train_expert_only = True
    cfg.train_state_proj = True
    cfg.push_to_hub = False
    cfg.rtc_config = None
    cfg.compile_model = False
    if tiny:
        cfg.num_vlm_layers = 2
        cfg.num_expert_layers = 2
        cfg.prefix_length = 0
        cfg.tokenizer_max_length = 16
    return cfg


def _code_revision():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "uncommitted"


def _lineage(dataset, contract, split, options, base, backbone):
    import importlib.metadata
    paths = [dataset.path / "dataset.json"] + [e["path"] / "frames.jsonl" for e in dataset.episodes]
    if contract.mode == "sam_objects":
        paths += [e["path"] / "annotations" / contract.annotation_version / "frames.jsonl" for e in dataset.episodes]
    hashes = {str(p.relative_to(dataset.path)): sha256_file(p) for p in paths}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    versions = {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "lerobot", "transformers")}
    return {"schema_version": "so101.training.lineage.v1", "dataset_path": str(dataset.path),
            "dataset_source": dataset.meta.get("source"), "dataset_hashes": hashes, "dataset_sha256": digest,
            "calibration": dataset.meta.get("calibration", {"valid": False}), "code_commit": _code_revision(),
            "code_files_sha256": {p.name: sha256_file(p) for p in sorted((ROOT / "src/so101_lab").glob("*.py"))},
            "feature_contract": contract.to_dict(), "feature_sha256": contract.digest,
            "base_model": str(base) if base else "random_tiny_architecture_technical_test",
            "base_revision": base.name if base else None, "backbone_revision": backbone.name,
            "versions": versions, "annotation_provenance": dataset.annotation_provenance, "split": split, "options": options, "technical_test": bool(options.get("tiny_model"))}


def _save_checkpoint(policy, pre, post, optimizer, scheduler, output, step, contract, lineage, backbone_assets, rng):
    import torch
    from .resources import assert_disk_space
    checkpoint_bytes = sum(t.numel()*t.element_size() for t in policy.state_dict().values())
    optimizer_bytes = 2 * sum(t.numel()*t.element_size() for t in policy.parameters() if t.requires_grad)
    assert_disk_space(minimum_bytes=int((checkpoint_bytes+optimizer_bytes)*1.15)+256*1024**2)
    output = Path(output)
    checkpoints = output / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    final = checkpoints / f"step_{step:08d}"
    temp = checkpoints / f".step_{step:08d}.partial"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir()
    pretrained = temp / "pretrained_model"
    pretrained.mkdir()
    _copy_backbone_assets(backbone_assets, pretrained / "backbone")
    original_backbone = policy.config.vlm_model_name
    # Use final absolute paths in saved processor/config. Loader rebases on relocation.
    final_backbone = final / "pretrained_model" / "backbone"
    policy.config.vlm_model_name = str(final_backbone)
    for step_obj in pre.steps:
        if hasattr(step_obj, "tokenizer_name"):
            step_obj.tokenizer_name = str(final_backbone)
    try:
        policy.save_pretrained(pretrained, push_to_hub=False)
        pre.save_pretrained(pretrained, config_filename="policy_preprocessor.json")
        post.save_pretrained(pretrained, config_filename="policy_postprocessor.json")
    finally:
        policy.config.vlm_model_name = original_backbone
        for step_obj in pre.steps:
            if hasattr(step_obj, "tokenizer_name"):
                step_obj.tokenizer_name = original_backbone
    _atomic_json(pretrained / "object_adapter.json", contract.to_dict())
    _atomic_json(pretrained / "lineage.json", lineage)
    training_state = temp / "training_state"
    training_state.mkdir()
    torch.save({"optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict(), "step": step,
                "torch_rng": torch.get_rng_state(), "python_rng": random.getstate(),
                "sampler_rng": rng.getstate(), "cuda_rng": torch.cuda.get_rng_state_all() if policy.config.device.startswith("cuda") else None}, training_state / "state.pt")
    _atomic_json(temp / "checkpoint.json", {"status": "complete", "step": step,
                  "technical_test": lineage["technical_test"], "created_unix": time.time(),
                  "feature_sha256": contract.digest, "dataset_sha256": lineage["dataset_sha256"],
                  "model_sha256": sha256_file(pretrained / "model.safetensors")})
    if final.exists():
        # Never overwrite completed scientific evidence.
        raise FileExistsError(f"Checkpoint exists: {final}")
    os.replace(temp, final)
    _atomic_json(output / "latest.json", {"checkpoint": str(final), "step": step})
    return final


def train(dataset_path: Path, output_path: Path, options: dict, progress: Callable | None = None, stop: Callable | None = None) -> dict:
    """Actual worker training. Resource checks precede any torch/model allocation."""
    options = dict(options)
    tiny = bool(options.get("tiny_model", False))
    if options.get("allow_test_data") and not tiny:
        raise ValueError("Test data permitted only for explicitly tiny technical smoke")
    device = options.get("device", "cpu" if tiny else "cuda")
    if tiny and device != "cpu":
        raise ValueError("Технический tiny smoke запускается только CPU")
    from .resources import ResourceLease, assert_compute_available
    assert_compute_available(device, minimum_available_gb=.6 if tiny else 7.0)
    with ResourceLease("gpu_compute" if device.startswith("cuda") else "cpu_compute"):
        return _train(dataset_path, output_path, options, progress, stop)


def _train(dataset_path, output_path, options, progress, stop):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
    from lerobot.policies.smolvla.processor_smolvla import make_smolvla_pre_post_processors
    from .telemetry import DurableMLflow
    tiny = bool(options.get("tiny_model", False))
    device = options.get("device", "cpu" if tiny else "cuda")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA отсутствует в собственной среде; train не запущен")
    torch.set_num_threads(int(options.get("cpu_threads", 2)))
    seed = int(options.get("seed", 42))
    _seed(seed)
    output = Path(output_path).resolve()
    output.mkdir(parents=True, exist_ok=True)
    steps = int(options.get("steps", 2 if tiny else 1000))
    batch_size = int(options.get("batch_size", 1))
    save_every = max(1, int(options.get("save_every", 500)))
    eval_every = max(1, int(options.get("eval_every", 100)))
    if not (1 <= steps <= 1_000_000 and 1 <= batch_size <= 16):
        raise ValueError("Недопустимое число steps/batch_size")
    contract = FeatureContract.from_dict(options)
    dataset = RawEpisodeDataset(Path(dataset_path), contract, chunk_size=int(options.get("chunk_size", 2 if tiny else 50)), allow_test_data=tiny and bool(options.get("allow_test_data")))
    train_idx, val_idx, split = dataset.split(seed, float(options.get("validation_fraction", .2)))
    _atomic_json(output / "split.json", split)
    _atomic_json(output / "rejected_frames.json", dataset.rejections)
    backbone_cache = cached_snapshot("HuggingFaceTB/SmolVLM2-500M-Video-Instruct", require_weights=False)
    backbone = output / "backbone_assets"
    _copy_backbone_assets(backbone_cache, backbone, tiny=tiny)
    base = None if tiny else cached_snapshot("lerobot/smolvla_base", revision=options.get("base_revision"))
    cfg = _config(contract, backbone, options, tiny=tiny, base=base)
    stats = dataset.stats(train_idx)
    resume = options.get("resume_from")
    if resume:
        from .inference import load_checkpoint
        loaded = load_checkpoint(Path(resume), device=device, allow_technical=tiny, resource_check=False, allow_untrained=True)
        if loaded.contract.digest != contract.digest:
            raise ValueError("Resume feature contract differs")
        policy, pre, post = loaded.policy, loaded.preprocessor, loaded.postprocessor
    else:
        policy = SmolVLAPolicy(cfg) if tiny else SmolVLAPolicy.from_pretrained(base, config=cfg, local_files_only=True, strict=True)
        policy.to(device)
        pre, post = make_smolvla_pre_post_processors(policy.config, stats)
    lineage = _lineage(dataset, contract, split, options, base, backbone_cache)
    if resume:
        previous = _json(Path(resume) / "pretrained_model" / "lineage.json")
        if previous["dataset_sha256"] != lineage["dataset_sha256"]:
            raise ValueError("Resume dataset changed; start a new run")
    _atomic_json(output / "lineage.json", lineage)
    params = {"policy": "SmolVLA", "adapter": contract.mode, "camera_mode": contract.camera_mode,
              "dataset_sha256": lineage["dataset_sha256"], "feature_sha256": contract.digest,
              "code_commit": lineage["code_commit"], "steps": steps, "batch_size": batch_size,
              "technical_test": tiny, "device": device, "base_revision": base.name if base else "tiny_random",
              "lr": float(options.get("lr", 1e-4)), "split_unit": split["unit"]}
    telemetry = DurableMLflow(ROOT, run_name=output.name, params=params)
    for path in (output / "lineage.json", output / "split.json", output / "rejected_frames.json"):
        telemetry.log_artifact(path)
    initial_state_projection = policy.model.state_proj.weight.detach().cpu().clone()
    trainable = [p for p in policy.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=params["lr"], betas=(.9, .95), weight_decay=1e-10)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: max(.025, .5*(1+math.cos(math.pi*min(step, steps)/steps))))
    rng = random.Random(seed)
    start_step = 0
    if resume:
        state = torch.load(Path(resume) / "training_state/state.pt", map_location="cpu", weights_only=False)
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        torch.set_rng_state(state["torch_rng"])
        random.setstate(state["python_rng"])
        rng.setstate(state["sampler_rng"])
        if state.get("cuda_rng") and device.startswith("cuda"):
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        start_step = int(state["step"])
    last_checkpoint = None
    last_loss = None
    status = "FINISHED"
    step = start_step
    start_time = time.monotonic()
    try:
        policy.train()
        for step in range(start_step + 1, steps + 1):
            if stop and stop():
                status = "KILLED"
                step -= 1
                break
            before = time.monotonic()
            batch_indices = [rng.choice(train_idx) for _ in range(batch_size)]
            raw_batch = _batch(dataset, batch_indices)
            prepared = pre(raw_batch)
            optimizer.zero_grad(set_to_none=True)
            loss, details = policy(prepared)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"Nonfinite real training loss at step {step}")
            loss.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(trainable, float(options.get("grad_clip", 10)))
            if not torch.isfinite(grad_norm):
                raise FloatingPointError("Nonfinite gradient")
            optimizer.step()
            scheduler.step()
            elapsed = time.monotonic() - before
            last_loss = float(loss.detach().cpu())
            metrics = {"train/loss": last_loss, "train/lr": scheduler.get_last_lr()[0],
                       "train/grad_norm": float(grad_norm), "train/samples_per_second": batch_size/elapsed,
                       "train/step_seconds": elapsed, "resources/rss_mb": _rss_mb(),
                       "train/elapsed_seconds": time.monotonic()-start_time}
            if device.startswith("cuda"):
                metrics["resources/cuda_allocated_mb"] = torch.cuda.memory_allocated()/2**20
            if step % eval_every == 0 or step == steps:
                policy.eval()
                with torch.no_grad():
                    val_batch = pre(_batch(dataset, val_idx[:batch_size]))
                    val_loss, _ = policy(val_batch)
                    metrics["validation/loss"] = float(val_loss.cpu())
                policy.train()
            telemetry.log_metrics(metrics, step=step)
            event = {"state": "training", "step": step, "steps": steps, "metrics": metrics,
                     "technical_test": tiny, "feature_schema": contract.schema_version}
            _atomic_json(output / "progress.json", event)
            if progress:
                progress(event)
            if step % save_every == 0 or step == steps:
                last_checkpoint = _save_checkpoint(policy, pre, post, optimizer, scheduler, output, step, contract, lineage, backbone, rng)
                # Send small checkpoint manifest only; full checkpoint stays local.
                telemetry.log_artifact(last_checkpoint / "checkpoint.json")
        if last_checkpoint is None or _json(last_checkpoint / "checkpoint.json")["step"] != step:
            last_checkpoint = _save_checkpoint(policy, pre, post, optimizer, scheduler, output, step, contract, lineage, backbone, rng)
            telemetry.log_artifact(last_checkpoint / "checkpoint.json")
        result = {"status": "stopped" if status == "KILLED" else "completed", "step": step, "loss": last_loss,
                  "checkpoint": str(last_checkpoint), "technical_test": tiny, "split": split,
                  "mlflow": getattr(telemetry, "run_id", None),
                  "state_projection_update_max_abs": float((policy.model.state_proj.weight.detach().cpu()-initial_state_projection).abs().max())}
        _atomic_json(output / "result.json", result)
        # Full terminal checkpoint once per run; periodic saves log only small manifests.
        telemetry.log_artifact(last_checkpoint)
        return result
    except Exception:
        status = "FAILED"
        raise
    finally:
        telemetry.close(status)


def _rss_mb():
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
