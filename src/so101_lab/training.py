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


class _HashCache:
    """Кэш успешной проверки SHA-256 raw PNG по (размер, mtime_ns).

    Без него каждая проверка/запуск обучения заново читает и хэширует все PNG
    (несжатые PNG ~0.9 МБ, два ракурса, 30 fps) — preview, API, worker и train читали бы данные 4 раза.
    Кэш лежит в cache/, а не в датасете; любое изменение файла (размер/mtime) делает запись недействительной.
    Только для датасетов внутри data/ лаборатории.
    """
    def __init__(self, episode: Path):
        self.entries = {}
        self.dirty = False
        self.file = None
        episode = Path(episode).resolve()
        if episode.is_relative_to((ROOT / "data").resolve()):
            key = hashlib.sha1(str(episode).encode()).hexdigest()
            self.file = ROOT / "cache/hash-verified" / (key + ".json")
            try:
                self.entries = json.loads(self.file.read_text())
            except (OSError, ValueError):
                self.entries = {}

    def verify(self, image_path: Path, expected: str, root: Path):
        stat = Path(image_path).stat()
        key = str(Path(image_path).relative_to(root))
        cached = self.entries.get(key)
        if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns and cached[2] == expected:
            return True
        if sha256_file(image_path) != expected:
            return False
        self.entries[key] = [stat.st_size, stat.st_mtime_ns, expected]
        self.dirty = True
        return True

    def save(self):
        if self.file is None or not self.dirty:
            return
        try:
            _atomic_json(self.file, self.entries)
        except OSError:
            pass  # кэш — только ускорение


def _atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as f:
        f.write(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False));f.flush();os.fsync(f.fileno())
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
    def __init__(self, dataset_path: Path, contract: FeatureContract, *, chunk_size=50, allow_test_data=False, policy_type="smolvla", image_size=256, episode_ids=None):
        self.path = Path(dataset_path).resolve()
        self.meta = _json(self.path / "dataset.json")
        self.contract = contract
        available_cameras=("wrist",) if self.meta.get("camera_mode",self.meta.get("settings",{}).get("camera_mode","two_camera"))=="wrist_only" else ("top","wrist")
        missing_cameras=[camera for camera in contract.cameras if camera not in available_cameras]
        if missing_cameras:raise ValueError("Режим обучения требует камеры, которой нет в датасете: "+", ".join(missing_cameras))
        self.chunk_size = int(chunk_size)
        self.policy_type=policy_type
        self.image_size=image_size
        self.allow_test_data = bool(allow_test_data)
        if self.meta.get("source") != "hardware" and not allow_test_data:
            raise ValueError("Тестовый источник исключён из обычного обучения")
        self.episodes = []
        self.samples = []
        self.rejections = []
        self.annotation_provenance = None
        selected_ids=set(episode_ids) if episode_ids is not None else None
        for ep in sorted((self.path / "episodes").glob("*")):
            if selected_ids is not None and ep.name not in selected_ids:continue
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
            hash_cache = _HashCache(ep)
            for index, row in enumerate(rows):
                try:
                    validate_raw_frame(row, contract, training=True, allow_test=allow_test_data)
                    for cam in contract.cameras:
                        image_path = safe_child(ep, row["cameras"][cam]["path"])
                        if annotations is None and not hash_cache.verify(image_path, row["cameras"][cam]["sha256"], ep.resolve()):
                            raise ValueError(f"Raw image hash mismatch: {image_path}")
                    valid.append(index)
                except (ValueError, FileNotFoundError, KeyError) as exc:
                    self.rejections.append({"episode": ep.name, "frame": row.get("frame_id"), "reason": str(exc)})
            hash_cache.save()
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

    def _images(self, ep_index, frame_index):
        import numpy as np
        from PIL import Image
        ep = self.episodes[ep_index]
        row = ep["rows"][frame_index]
        result = {}
        for cam in self.contract.cameras:
            with Image.open(safe_child(ep["path"], row["cameras"][cam]["path"])) as image:
                result[cam] = np.array(image.convert("RGB"), copy=True)
        return result

    def vector(self, ep_index, frame_index, images=None):
        ep = self.episodes[ep_index]
        annotation = ep["annotations"][frame_index] if ep["annotations"] is not None else None
        if images is None and self.contract.mode == "wrist_color_object":
            images = self._images(ep_index, frame_index)
        return object_state(ep["rows"][frame_index], annotation, self.contract, images=images,
                            allow_test=self.allow_test_data)

    def __getitem__(self, index):
        import torch
        ei, fi = self.samples[index]
        ep = self.episodes[ei]
        row = ep["rows"][fi]
        images = self._images(ei, fi)
        result = {"observation.state": torch.tensor(self.vector(ei, fi, images), dtype=torch.float32),
                  "task": ep["info"].get("task", self.meta.get("task", ""))}
        for cam in self.contract.cameras:
            from .policies import image_tensor
            result[f"observation.images.{cam}"] = image_tensor(images[cam],size=self.image_size if self.policy_type=="act" else None)
        end = min(fi + self.chunk_size, len(ep["rows"]))
        chunk = [action_state(r) for r in ep["rows"][fi:end]]
        pad_count = self.chunk_size - len(chunk)
        result["action"] = torch.tensor(chunk + [chunk[-1]] * pad_count, dtype=torch.float32)
        result["action_is_pad"] = torch.tensor([False] * len(chunk) + [True] * pad_count)
        return result

    def partitions(self, options):
        from .training_options import plan_split
        episodes=[{"id":e["path"].name,"session_id":e["info"].get("session_id"),"frames":len(e["valid"])} for e in self.episodes]
        plan=plan_split(episodes,options)
        indices={k:[i for i,(ei,_) in enumerate(self.samples) if self.episodes[ei]["path"].name in plan[k]] for k in ('train','validation','test')}
        return indices,plan

    def split(self, seed=42, validation_fraction=.2):
        indices,plan=self.partitions({'seed':seed,'validation_fraction':validation_fraction})
        return indices['train'],indices['validation'],plan

    def stats(self, indices):
        import torch
        states = torch.tensor([self.vector(*self.samples[i]) for i in indices])
        # Normalize measured joints only. Validity, boxes, score and target stay 0..1.
        state_mean = torch.zeros(self.contract.state_dim)
        state_std = torch.ones(self.contract.state_dim)
        state_mean[:6] = states[:, :6].mean(0)
        state_std[:6] = states[:, :6].std(0, correction=0).clamp_min(1e-3)
        actions = torch.tensor([action_state(self.episodes[ei]["rows"][fi]) for i in indices for ei, fi in [self.samples[i]]])
        result={"observation.state": {"mean": state_mean, "std": state_std},
                "action": {"mean": actions.mean(0), "std": actions.std(0, correction=0).clamp_min(1e-3)}}
        # Fixed ImageNet normalization is part of the saved processor when the
        # ACT visual backbone starts from the local ImageNet weights.
        image_mean=torch.tensor([.485,.456,.406]).reshape(3,1,1)
        image_std=torch.tensor([.229,.224,.225]).reshape(3,1,1)
        for camera in self.contract.cameras:
            result[f"observation.images.{camera}"]={"mean":image_mean,"std":image_std}
        return result

    def grasp_indices(self, indices, before_frames=75, after_frames=25):
        """Dataset indices around the strongest closing transition per episode."""
        allowed = set(indices)
        selected = []
        for episode_index, ep in enumerate(self.episodes):
            gripper = [action_state(row)[5] for row in ep["rows"]]
            if len(gripper) < 2:
                continue
            close_at = min(range(1, len(gripper)), key=lambda i: gripper[i] - gripper[i - 1])
            if gripper[close_at] >= gripper[close_at - 1]:
                continue
            lo, hi = max(0, close_at - before_frames), min(len(gripper), close_at + after_frames + 1)
            selected.extend(i for i, (ei, fi) in enumerate(self.samples)
                            if i in allowed and ei == episode_index and lo <= fi < hi)
        return selected


def _annotation_identity(provenance):
    from .annotations import annotation_provenance_identity
    return json.dumps(annotation_provenance_identity(provenance),sort_keys=True)


def _json_line(line):
    return json.loads(line)


def _batch(dataset, indices, pool=None):
    import torch
    # PIL/OpenCV декодируют PNG без GIL: пул потоков ускоряет серийную validation без spawn-процессов.
    samples = list(pool.map(dataset.__getitem__, indices)) if pool is not None else [dataset[i] for i in indices]
    return {key: [s[key] for s in samples] if key == "task" else torch.stack([s[key] for s in samples]) for key in samples[0]}


def _seed(seed):
    import numpy as np
    import torch
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _config(contract, backbone, options, *, tiny=False, base=None):
    if options.get("policy_type")=="act":
        from .policies import act_config
        return act_config(contract,options,tiny)
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
    # Wrist-only execution incorporates a fresh observation before the whole
    # predicted chunk is consumed.
    cfg.n_action_steps = int(options["n_action_steps"])
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
    paths = [e["path"] / "frames.jsonl" for e in dataset.episodes]
    if contract.mode == "sam_objects":
        paths += [e["path"] / "annotations" / contract.annotation_version / "frames.jsonl" for e in dataset.episodes]
    hashes = {str(p.relative_to(dataset.path)): sha256_file(p) for p in paths}
    contract_meta={k:dataset.meta.get(k) for k in ('source','schema_version','joints','units','task','calibration','camera_mode','cameras','version','dataset_sha256')}
    hashes['dataset.contract']=hashlib.sha256(json.dumps(contract_meta,sort_keys=True).encode()).hexdigest()
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    versions = {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "lerobot", "transformers")}
    return {"schema_version": "so101.training.lineage.v1", "dataset_path": str(dataset.path),
            "dataset_source": dataset.meta.get("source"), "dataset_metadata_file_sha256":sha256_file(dataset.path/"dataset.json"),"dataset_hashes": hashes, "dataset_sha256": digest,
            "calibration": dataset.meta.get("calibration", {"valid": False}), "code_commit": _code_revision(),
            "code_files_sha256": {p.name: sha256_file(p) for p in sorted((ROOT / "src/so101_lab").glob("*.py"))},
            "feature_contract": contract.to_dict(), "feature_sha256": contract.digest,
            "policy_type":options.get("policy_type","smolvla"),
            "base_model": str(base) if base else ("random_tiny_architecture_technical_test" if options.get("tiny_model") else "ACT random initialization, offline ResNet18"),
            "base_revision": base.name if base else None, "backbone_revision": backbone.name if backbone else None,
            "versions": versions, "annotation_provenance": dataset.annotation_provenance, "split": split, "options": options, "technical_test": bool(options.get("tiny_model"))}


def _save_checkpoint(policy, pre, post, optimizer, scheduler, output, step, contract, lineage, backbone_assets, rng, scaler=None):
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
    if backbone_assets: _copy_backbone_assets(backbone_assets, pretrained / "backbone")
    original_backbone = getattr(policy.config,"vlm_model_name",None)
    # Use final absolute paths in saved processor/config. Loader rebases on relocation.
    final_backbone = final / "pretrained_model" / "backbone"
    if original_backbone is not None: policy.config.vlm_model_name = str(final_backbone)
    for step_obj in pre.steps:
        if hasattr(step_obj, "tokenizer_name"):
            step_obj.tokenizer_name = str(final_backbone)
    # ACT trains on centered-letterbox squares (dataset.image_tensor), but the live
    # LeRobot rollout hands the processor raw camera frames. Serialize the same
    # letterbox into the preprocessor (identity for already-square training input).
    letterbox_added=None
    if policy.name=="act":
        from .policies import CenteredLetterboxProcessorStep
        size=int(next(iter(f for k,f in policy.config.input_features.items() if "image" in k)).shape[-1])
        letterbox_added=CenteredLetterboxProcessorStep(height=size,width=size)
        position=next((i for i,s in enumerate(pre.steps) if type(s).__name__=="NormalizerProcessorStep"),len(pre.steps))
        pre.steps.insert(position,letterbox_added)
    try:
        policy.save_pretrained(pretrained, push_to_hub=False)
        pre.save_pretrained(pretrained, config_filename="policy_preprocessor.json")
        post.save_pretrained(pretrained, config_filename="policy_postprocessor.json")
    finally:
        if letterbox_added is not None: pre.steps.remove(letterbox_added)
        if original_backbone is not None: policy.config.vlm_model_name = original_backbone
        for step_obj in pre.steps:
            if hasattr(step_obj, "tokenizer_name"):
                step_obj.tokenizer_name = original_backbone
    _atomic_json(pretrained / "object_adapter.json", contract.to_dict())
    _atomic_json(pretrained / "lineage.json", lineage)
    training_state = temp / "training_state"
    training_state.mkdir()
    torch.save({"scaler":scaler.state_dict() if scaler else None,"optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict(), "step": step,
                "torch_rng": torch.get_rng_state(), "python_rng": random.getstate(),
                "sampler_rng": rng.getstate(), "cuda_rng": torch.cuda.get_rng_state_all() if policy.config.device.startswith("cuda") else None}, training_state / "state.pt")
    _atomic_json(temp / "checkpoint.json", {"status": "complete", "step": step,
                  "technical_test": lineage["technical_test"], "policy_type":policy.name, "created_unix": time.time(),
                  "feature_sha256": contract.digest, "dataset_sha256": lineage["dataset_sha256"],
                  "model_sha256": sha256_file(pretrained / "model.safetensors"),
                  "files_sha256":{str(p.relative_to(pretrained)):sha256_file(p) for p in sorted(pretrained.rglob("*")) if p.is_file()}})
    if final.exists():
        # Never overwrite completed scientific evidence.
        raise FileExistsError(f"Checkpoint exists: {final}")
    for file in temp.rglob('*'):
        if file.is_file():
            with file.open('rb') as f:os.fsync(f.fileno())
    os.replace(temp, final)
    fd=os.open(checkpoints,os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    _atomic_json(output / "latest.json", {"checkpoint": str(final), "step": step})
    return final


def train(dataset_path: Path, output_path: Path, options: dict, progress: Callable | None = None, stop: Callable | None = None) -> dict:
    """Actual worker training. Resource checks precede any torch/model allocation."""
    from .training_options import normalize_options
    options = normalize_options(options)
    tiny = bool(options.get("tiny_model", False))
    if options.get("allow_test_data") and not tiny:
        raise ValueError("Test data permitted only for explicitly tiny technical smoke")
    device = options.get("device", "cpu" if tiny else "cuda")
    if tiny and device != "cpu":
        raise ValueError("Технический tiny smoke запускается только CPU")
    from .resources import ResourceLease, assert_compute_available
    assert_compute_available(device, minimum_available_gb=.6 if tiny else 7.0)
    from .resources import dataset_lease
    with dataset_lease(dataset_path), ResourceLease("gpu_compute" if device.startswith("cuda") else "cpu_compute"):
        return _train(dataset_path, output_path, options, progress, stop)


def _train(dataset_path, output_path, options, progress, stop):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from .policies import components,state_projection
    _,Policy,make_processors=components(options["policy_type"])
    from .telemetry import DurableMLflow
    tiny = bool(options.get("tiny_model", False))
    device = options.get("device", "cpu" if tiny else "cuda")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA отсутствует в собственной среде; train не запущен")
    torch.set_num_threads(int(options.get("cpu_threads", 2)))
    if device.startswith("cuda"):
        # Orin (Ampere): fp32 matmul через TF32 tensor cores заметно быстрее, потеря точности пренебрежима для fine-tuning.
        torch.set_float32_matmul_precision("high")
        torch.backends.cudnn.benchmark = True
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
    from .training_options import selected_episode_ids
    dataset = RawEpisodeDataset(Path(dataset_path), contract, chunk_size=int(options.get("chunk_size", 2 if tiny else 50)), allow_test_data=tiny and bool(options.get("allow_test_data")),policy_type=options["policy_type"],image_size=options["image_size"],episode_ids=selected_episode_ids(options))
    partitions,split = dataset.partitions(options)
    train_idx,val_idx,test_idx=[partitions[k] for k in ("train","validation","test")]
    if options.get("expected_split_sha256") and options["expected_split_sha256"]!=split["sha256"]:raise ValueError("Dataset/split изменился после отправки: повторите предварительную проверку")
    _atomic_json(output / "split.json", split)
    _atomic_json(output / "rejected_frames.json", dataset.rejections)
    backbone_cache=None;backbone=None;base=None
    if options["policy_type"]=="smolvla":
        backbone_cache=cached_snapshot("HuggingFaceTB/SmolVLM2-500M-Video-Instruct",require_weights=False)
        backbone=output/"backbone_assets"
        _copy_backbone_assets(backbone_cache,backbone,tiny=tiny)
        base=None if tiny else cached_snapshot("lerobot/smolvla_base",revision=options.get("base_revision"))
    cfg = _config(contract, backbone, options, tiny=tiny, base=base)
    resume = options.get("resume_from")
    # A resumed checkpoint already contains the exact saved processors and
    # normalization statistics. Re-reading every PNG to recompute unused
    # statistics adds several minutes and cannot change the resumed model.
    stats = None if resume else dataset.stats(train_idx)
    if resume:
        from .inference import load_checkpoint
        loaded = load_checkpoint(Path(resume), device=device, allow_technical=tiny, resource_check=False, allow_untrained=True)
        if loaded.policy.config.chunk_size!=options["chunk_size"] or any(f.shape[-1]!=options["image_size"] for f in loaded.policy.config.image_features.values()):raise ValueError("Resume image_size/chunk_size отличается от checkpoint")
        if loaded.contract.digest != contract.digest:
            raise ValueError("Resume feature contract differs")
        if loaded.policy.name!=options["policy_type"]:raise ValueError("Resume policy type differs")
        policy, pre, post = loaded.policy, loaded.preprocessor, loaded.postprocessor
        policy.config.n_action_steps=options["n_action_steps"]
        if policy.name=='act':policy.config.temporal_ensemble_coeff=options.get('temporal_ensemble_coeff')
    else:
        policy = Policy.from_pretrained(base,config=cfg,local_files_only=True,strict=True) if base else Policy(cfg)
        policy.to(device)
        pre, post = make_processors(policy.config, stats)
    lineage = _lineage(dataset, contract, split, options, base, backbone_cache)
    if resume:
        previous = _json(Path(resume) / "pretrained_model" / "lineage.json")
        if previous["split"]["train"]!=split["train"] or previous["split"]["validation"]!=split["validation"] or previous["split"].get("test",[])!=split["test"]:raise ValueError("Resume split differs")
        for key in ('policy_type','optimizer','lr','weight_decay','scheduler','warmup_steps','gradient_accumulation','use_amp','seed','augment_images','kl_weight','act_lr_backbone','n_action_steps','temporal_ensemble_coeff','act_pretrained_backbone'):
            if key in previous.get('options',{}) and previous['options'][key]!=options[key]:raise ValueError(f'Resume параметр {key} отличается; сохраните исходные параметры')
        if previous["dataset_sha256"] != lineage["dataset_sha256"]:
            raise ValueError("Resume dataset changed; start a new run")
    _atomic_json(output / "lineage.json", lineage)
    params = {"policy": options["policy_type"], "adapter": contract.mode, "camera_mode": contract.camera_mode,
              "dataset_sha256": lineage["dataset_sha256"], "feature_sha256": contract.digest,
              "code_commit": lineage["code_commit"], "steps": steps, "batch_size": batch_size,
              "technical_test": tiny, "device": device, "base_revision": base.name if base else "tiny_random",
              "lr": float(options.get("lr", 1e-4)), "split_unit": split["unit"],
              "dataset_id":dataset.path.name,"dataset_version":dataset.meta.get("version",0),
              "calibration_sha256":hashlib.sha256(json.dumps(lineage["calibration"],sort_keys=True).encode()).hexdigest(),
              "policy_library_version":lineage["versions"]["lerobot"]}
    params.update({k:v for k,v in options.items() if isinstance(v,(str,int,float,bool)) and k not in ("resume_from",)})
    telemetry = DurableMLflow(ROOT, run_name=options.get("job_name") or output.name, params=params)
    for path in (output / "lineage.json", output / "split.json", output / "rejected_frames.json"):
        telemetry.log_artifact(path)
    initial_state_projection = state_projection(policy).weight.detach().cpu().clone()
    trainable = [p for p in policy.parameters() if p.requires_grad]
    optim_cls=torch.optim.AdamW if options["optimizer"]=="adamw" else torch.optim.Adam
    groups=policy.get_optim_params() if options["policy_type"]=="act" else trainable
    optimizer = optim_cls(groups, lr=params["lr"], betas=(.9, .95), weight_decay=options["weight_decay"])
    def lr_scale(step):
        warmup=options['warmup_steps']
        if warmup and step<warmup:return max(.001,(step+1)/warmup)
        return 1. if options['scheduler']=='constant' else max(.025,.5*(1+math.cos(math.pi*min(max(0,step-warmup),steps-warmup)/(steps-warmup))))
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer,lr_scale)
    amp=options['use_amp'] and device.startswith('cuda')
    amp_dtype=torch.bfloat16 if options.get('amp_dtype','bf16')=='bf16' else torch.float16
    if amp and amp_dtype is torch.bfloat16 and not torch.cuda.is_bf16_supported():amp_dtype=torch.float16
    # GradScaler нужен только fp16; bf16 имеет диапазон fp32.
    scaler=torch.amp.GradScaler('cuda',enabled=amp and amp_dtype is torch.float16)
    skipped_steps=0;consecutive_skips=0
    eval_pool=None
    if int(options.get('eval_threads',4))>1:
        from concurrent.futures import ThreadPoolExecutor
        eval_pool=ThreadPoolExecutor(max_workers=int(options.get('eval_threads',4)),thread_name_prefix='so101-eval')

    rng = random.Random(seed)
    start_step = 0
    if resume:
        state = torch.load(Path(resume) / "training_state/state.pt", map_location="cpu", weights_only=False)
        optimizer.load_state_dict(state["optimizer"])
        if state.get("scaler"):scaler.load_state_dict(state["scaler"])
        scheduler.load_state_dict(state["scheduler"])
        torch.set_rng_state(state["torch_rng"])
        random.setstate(state["python_rng"])
        rng.setstate(state["sampler_rng"])
        if state.get("cuda_rng") and device.startswith("cuda"):
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        start_step = int(state["step"])
    if start_step>=steps:raise ValueError("Resume steps должны быть больше шага checkpoint")
    last_checkpoint = None
    last_loss = None
    status = "FINISHED"
    step = start_step
    start_time = time.monotonic()
    try:
        batches=training_batches(dataset,train_idx,batch_size,seed,start_step,steps,options)
        grasp_val_idx=dataset.grasp_indices(val_idx) if val_idx else []
        policy.train()
        for step in range(start_step + 1, steps + 1):
            if stop and stop():
                status = "KILLED"
                step -= 1
                break
            before = time.monotonic()
            optimizer.zero_grad(set_to_none=True)
            accumulated_loss=0.
            for micro in range(options['gradient_accumulation']):
                if stop and stop():status='KILLED';break
                raw_batch=next(batches)
                if options['augment_images']:
                    for key in raw_batch:
                        if key.startswith('observation.images.'):
                            from .policies import augment_batch_images
                            raw_batch[key]=augment_batch_images(raw_batch[key])
                prepared=pre(raw_batch)
                with torch.autocast(device_type='cuda',dtype=amp_dtype,enabled=amp):
                    loss,details=policy(prepared)
                if not torch.isfinite(loss):raise FloatingPointError(f'Nonfinite training loss at step {step}')
                scaler.scale(loss/options['gradient_accumulation']).backward()
                accumulated_loss+=float(loss.detach().cpu())/options['gradient_accumulation']
            if status=='KILLED':
                step-=1
                break
            scaler.unscale_(optimizer)
            grad_norm = torch.nn.utils.clip_grad_norm_(trainable, float(options.get("grad_clip", 10)))
            if not torch.isfinite(grad_norm):
                # fp16 + GradScaler: overflow на первых шагах штатен — scaler пропускает шаг и уменьшает масштаб.
                # Без scaler (fp32/bf16) нефинитный градиент означает расхождение обучения.
                consecutive_skips+=1;skipped_steps+=1
                if not scaler.is_enabled() or consecutive_skips>25:
                    raise FloatingPointError("Nonfinite gradient")
            else:
                consecutive_skips=0
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            elapsed = time.monotonic() - before
            last_loss = accumulated_loss
            metrics = {"train/loss": last_loss, "train/lr": scheduler.get_last_lr()[0],
                       "train/grad_norm": float(grad_norm), "train/samples_per_second": batch_size*options["gradient_accumulation"]/elapsed,
                       "train/step_seconds": elapsed, "resources/rss_mb": _rss_mb(), "train/amp_skipped_steps": skipped_steps,
                       "train/elapsed_seconds": time.monotonic()-start_time}
            if not math.isfinite(metrics["train/grad_norm"]):
                metrics.pop("train/grad_norm")  # пропущенный fp16-шаг: inf/nan запрещены в progress.json и MLflow
            if device.startswith("cuda"):
                metrics["resources/cuda_allocated_mb"] = torch.cuda.memory_allocated()/2**20
            if val_idx and (step % eval_every == 0 or step == steps):
                # Промежуточная проверка — равномерная подвыборка; итоговая (последний шаг) — все validation-кадры.
                limit=None if step==steps else (options.get('eval_max_samples') or None)
                metrics['validation/loss']=evaluate(policy,pre,dataset,val_idx,batch_size,max_samples=limit,pool=eval_pool)
                if grasp_val_idx:
                    contact_limit=None if step==steps else min(int(options.get('eval_max_samples') or 128),128)
                    metrics['validation/grasp_window_loss']=evaluate(policy,pre,dataset,grasp_val_idx,batch_size,
                                                                     max_samples=contact_limit,pool=eval_pool)
            for key,value in details.items():
                if isinstance(value,(int,float)) and math.isfinite(value):metrics['train/'+key]=float(value)
            if step%options["log_every"]==0 or step==steps:telemetry.log_metrics(metrics,step=step)
            with (output/"metrics.jsonl").open("a") as log:log.write(json.dumps({"step":step,**metrics})+"\n")
            event = {"state": "training", "step": step, "steps": steps, "metrics": metrics,
                     "technical_test": tiny, "feature_schema": contract.schema_version}
            _atomic_json(output / "progress.json", event)
            if progress:
                progress(event)
            if step % save_every == 0 or step == steps:
                last_checkpoint = _save_checkpoint(policy, pre, post, optimizer, scheduler, output, step, contract, lineage, backbone, rng, scaler)
                # Send small checkpoint manifest only; full checkpoint stays local.
                telemetry.log_artifact(last_checkpoint / "checkpoint.json")
                _prune_checkpoints(output, int(options.get("keep_last_checkpoints", 0)), protect=[resume])
        if last_checkpoint is None or _json(last_checkpoint / "checkpoint.json")["step"] != step:
            last_checkpoint = _save_checkpoint(policy, pre, post, optimizer, scheduler, output, step, contract, lineage, backbone, rng, scaler)
            telemetry.log_artifact(last_checkpoint / "checkpoint.json")
        test_loss=evaluate(policy,pre,dataset,test_idx,batch_size,pool=eval_pool) if test_idx and status=="FINISHED" else None
        if test_loss is not None:telemetry.log_metrics({"test/loss":test_loss},step=step)
        telemetry.log_artifact(output/"metrics.jsonl") if (output/"metrics.jsonl").exists() else None
        result = {"test_loss":test_loss,"policy_type":options["policy_type"],"status": "stopped" if status == "KILLED" else "completed", "step": step, "loss": last_loss,
                  "checkpoint": str(last_checkpoint), "technical_test": tiny, "split": split,
                  "mlflow": getattr(telemetry, "run_id", None),
                  "state_projection_update_max_abs": float((state_projection(policy).weight.detach().cpu()-initial_state_projection).abs().max())}
        _atomic_json(output / "result.json", result)
        # Full terminal checkpoint once per run; periodic saves log only small manifests.
        telemetry.log_artifact(last_checkpoint)
        return result
    except Exception:
        status = "FAILED"
        raise
    finally:
        if eval_pool is not None:eval_pool.shutdown(wait=False)
        telemetry.close(status)


def _prune_checkpoints(output, keep, protect=()):
    """Удаляет старые ПОЛНЫЕ checkpoints этого запуска, оставляя keep последних. 0 — не удалять ничего.

    Источник resume и незавершённые .partial не затрагиваются. Только папки step_* внутри checkpoints/ запуска.
    """
    if keep <= 0:
        return []
    protected = {str(Path(p).resolve()) for p in protect if p}
    folders = sorted(p for p in (Path(output) / "checkpoints").glob("step_*") if p.is_dir() and (p / "checkpoint.json").is_file())
    removed = []
    for folder in folders[:-keep]:
        if str(folder.resolve()) in protected:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder.name)
    return removed


def _rss_mb():
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def evaluate(policy,pre,dataset,indices,batch_size,max_samples=None,pool=None):
    """Held-out frames, weighted by actual batch size, no train augmentations.

    По умолчанию — все кадры. max_samples берёт детерминированную равномерную подвыборку
    (соседние кадры 30 fps почти идентичны), одинаковую при каждом вызове — кривые сопоставимы.
    """
    import torch
    if max_samples and len(indices)>max_samples:
        stride=len(indices)/max_samples
        indices=[indices[int(i*stride)] for i in range(max_samples)]
    was_training=policy.training;policy.eval();total=0.;count=0
    # Evaluation randomness never advances training RNG (Smol flow / ACT VAE).
    with torch.random.fork_rng(devices=[torch.cuda.current_device()] if policy.config.device.startswith('cuda') else []):
        torch.manual_seed(12345)
        with torch.inference_mode():
            for start in range(0,len(indices),batch_size):
                ids=indices[start:start+batch_size]
                loss,_=policy(pre(_batch(dataset,ids,pool)))
                if not torch.isfinite(loss):raise FloatingPointError('Nonfinite held-out loss')
                total+=float(loss.cpu())*len(ids);count+=len(ids)
    policy.train(was_training)
    return total/count

class StepBatches:
    def __init__(self,indices,batch_size,seed,start,steps,accum,priority=(),priority_fraction=0.):
        self.indices,self.batch_size,self.seed,self.start,self.steps,self.accum=indices,batch_size,seed,start,steps,accum
        self.priority=list(priority);self.priority_fraction=float(priority_fraction)
    def __iter__(self):
        for step in range(self.start+1,self.steps+1):
            rng=random.Random(self.seed+step*1000003)
            for _ in range(self.accum):
                yield [rng.choice(self.priority) if self.priority and rng.random()<self.priority_fraction else rng.choice(self.indices)
                       for _ in range(self.batch_size)]
    def __len__(self):return (self.steps-self.start)*self.accum

def training_batches(dataset,indices,batch_size,seed,start,steps,options):
    from torch.utils.data import DataLoader
    import torch
    kwargs={}
    if options['num_workers']:
        kwargs.update(multiprocessing_context='spawn',prefetch_factor=2)
    priority=dataset.grasp_indices(indices) if options.get('grasp_oversample_fraction',0) else []
    return iter(DataLoader(dataset,batch_sampler=StepBatches(indices,batch_size,seed,start,steps,
        options['gradient_accumulation'],priority,options.get('grasp_oversample_fraction',0)),
        num_workers=options['num_workers'],generator=torch.Generator().manual_seed(seed),**kwargs))
