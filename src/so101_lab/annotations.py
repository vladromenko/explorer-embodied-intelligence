"""Resumable, causal SAM 3 image segmentation sidecars.

SAM inference is deliberately absent from the recording loop. The same
SAM3Segmenter.annotate_observation method is usable by a policy inference worker.
No SAM model, CUDA context or hardware is loaded at import time.
"""
from __future__ import annotations

import colorsys
import copy
import contextlib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import time
import traceback
from typing import Callable

import numpy as np
from PIL import Image

from .geometry import unknown_geometry

SCHEMA_VERSION = "so101.annotations.v1"
DEFAULT_VERSION = "sam3-v1"
CATEGORIES = ("cube", "box")


class AnnotationError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def _atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _safe_relative(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AnnotationError("Путь выходит из папки эпизода")
    return path


def _read_jsonl(path: Path, *, recover_last=False) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open() as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            if recover_last and i == len(lines)-1:
                break
            raise AnnotationError(f"Повреждён JSONL {path.name}, строка {i+1}") from exc
    return rows


def source_frame_digest(frame: dict) -> str:
    """Observation lineage only. Deliberately excludes actions and outcome labels."""
    fid = frame["frame_id"]
    if isinstance(fid,bool) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",str(fid)):
        raise AnnotationError("Неверный frame_id")
    payload = {"frame_id": frame["frame_id"], "timestamp_ns": frame["timestamp_ns"],
               "observation_timestamp_ns": frame.get("observation_timestamp_ns", frame["timestamp_ns"]),
               "cameras": {name: {k: c.get(k) for k in ("path", "timestamp_ns", "valid", "sha256")} for name, c in sorted(frame["cameras"].items())}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def measure_color(rgb: np.ndarray, mask: np.ndarray) -> dict:
    """Median visible-mask RGB/HSV, separate from SAM semantics; no color certainty."""
    pixels = np.asarray(rgb, dtype=np.uint8)[np.asarray(mask, dtype=bool)]
    if len(pixels) < 9:
        return {"valid": False, "label": "unknown", "rgb": None, "hsv": None, "method": "visible_mask_median_RGB_HSV_v1", "reason": "Слишком мало видимых пикселей"}
    median = np.median(pixels, axis=0) / 255.
    hue, saturation, value = colorsys.rgb_to_hsv(*median.tolist())
    if value < .15:
        label = "black"
    elif saturation < .16:
        label = "white" if value > .8 else "gray"
    elif hue < 15/360 or hue >= 345/360:
        label = "red"
    elif hue < 45/360:
        label = "orange"
    elif hue < 75/360:
        label = "yellow"
    elif hue < 165/360:
        label = "green"
    elif hue < 195/360:
        label = "cyan"
    elif hue < 265/360:
        label = "blue"
    else:
        label = "purple"
    deviation = np.median(np.abs(pixels/255. - median), axis=0)
    return {"valid": True, "label": label, "rgb": (median*255).tolist(), "rgb_mad": (deviation*255).tolist(),
            "hsv": [hue, saturation, value], "method": "visible_mask_median_RGB_HSV_v1", "pixel_count": len(pixels),
            "confidence": "unvalidated_lighting_dependent", "limitations": "Без color-chart калибровки; блики/тени меняют цвет"}


def _box_iou(a, b):
    ix = max(0., min(a[2],b[2])-max(a[0],b[0]))
    iy = max(0., min(a[3],b[3])-max(a[1],b[1]))
    intersection = ix*iy
    union = max(0.,a[2]-a[0])*max(0.,a[3]-a[1]) + max(0.,b[2]-b[0])*max(0.,b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.


class CausalAssociator:
    """Image SAM detections + conservative past-only IoU IDs, not SAM video IDs.

    Unmatched previous IDs are lost immediately. Reappearing objects get new IDs;
    occlusion is unknown. Crossing same-category objects can swap identities.
    """
    def __init__(self, camera: str, min_iou: float = .3, ambiguity_margin: float = .1):
        self.camera = camera
        self.min_iou = min_iou
        self.ambiguity_margin = ambiguity_margin
        self.previous = []
        self.counter = 0
        self.last_timestamp = None
        self.last_raw_sha256 = None

    def update(self, objects: list[dict], timestamp_ns: int, raw_sha256: str | None = None) -> tuple[list[dict], list[str]]:
        if self.last_timestamp is not None and timestamp_ns <= self.last_timestamp:
            if timestamp_ns == self.last_timestamp and raw_sha256 and raw_sha256 == self.last_raw_sha256:
                # Same captured image reused by the 30 Hz control loop; do not
                # fabricate motion or a new tracking observation.
                previous = {(o["category"],tuple(o["bbox_xyxy"])):o for o in self.previous}
                for obj in objects:
                    old = previous.get((obj["category"],tuple(obj["bbox_xyxy"])))
                    if old is None:
                        raise AnnotationError("Разная сегментация одного capture timestamp")
                    obj["tracking"] = copy.deepcopy(old["tracking"])
                    obj["tracking"]["status"] = "reused_capture"
                self.previous = objects
                return objects, []
            raise AnnotationError("Немонотонное время causal tracking")
        self.last_timestamp = timestamp_ns
        self.last_raw_sha256 = raw_sha256
        matched = set()
        for obj in sorted(objects, key=lambda x: (-x["score"], *x["bbox_xyxy"])):
            candidates = sorted(((i, _box_iou(obj["bbox_xyxy"], prev["bbox_xyxy"])) for i, prev in enumerate(self.previous) if prev["category"] == obj["category"] and i not in matched), key=lambda x: -x[1])
            best = candidates[0] if candidates else (None,0.)
            gap = best[1] - (candidates[1][1] if len(candidates)>1 else 0.)
            if best[1] >= self.min_iou and gap >= self.ambiguity_margin:
                i, iou = best
                matched.add(i)
                track_id = self.previous[i]["tracking"]["id"]
                status = "associated"
                confidence = iou
            else:
                self.counter += 1
                track_id = f"{self.camera}-{self.counter:06d}"
                status = "new_ambiguous" if best[1] >= self.min_iou else "new"
                confidence = 0.
            obj["tracking"] = {"id": track_id, "confidence": confidence, "status": status,
                               "method": "causal_box_IoU_v1", "identity_guaranteed": False}
        lost = [o["tracking"]["id"] for i,o in enumerate(self.previous) if i not in matched]
        self.previous = objects
        return objects, lost

    def restore(self, camera_annotation: dict):
        self.previous = camera_annotation.get("objects", [])
        self.last_timestamp = camera_annotation["timestamp_ns"]
        self.last_raw_sha256 = camera_annotation.get("raw_sha256")
        self.counter = max(self.counter, int(camera_annotation.get("tracking_counter",0)))
        for obj in self.previous:
            try:
                self.counter = max(self.counter, int(obj["tracking"]["id"].rsplit("-",1)[-1]))
            except (KeyError, ValueError):
                pass


def _check_own_import(module: str):
    spec = importlib.util.find_spec(module)
    if spec is None or not spec.origin:
        raise AnnotationError(f"{module} отсутствует в независимой среде")
    project = Path(__file__).resolve().parents[2]
    if not Path(spec.origin).resolve().is_relative_to(project):
        raise AnnotationError(f"Запрещён runtime import {module} из чужого окружения: {spec.origin}")


def _find_checkpoint(path=None) -> Path:
    if path:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise AnnotationError("Не найден SAM checkpoint")
        return p
    cache = Path.home()/".cache/huggingface/hub/models--facebook--sam3"
    ref = cache/"refs/main"
    if ref.is_file():
        p = cache/"snapshots"/ref.read_text().strip()/"sam3.pt"
        if p.is_file():
            return p
    snapshots = sorted((cache/"snapshots").glob("*/sam3.pt")) if (cache/"snapshots").exists() else []
    if len(snapshots) == 1:
        return snapshots[0]
    raise AnnotationError("SAM3 weights не найдены однозначно в локальном cache; укажите checkpoint_path. Автоскачивание отключено.")


class SAM3Segmenter:
    """Real official SAM3 image model, lazily loaded after root resource guard."""
    backend = "sam3"

    def __init__(self, options: dict | None = None):
        self.options = options or {}
        self.device = self.options.get("device", "cuda")
        self.threshold = float(self.options.get("confidence_threshold", .5))
        if not 0 <= self.threshold <= 1:
            raise AnnotationError("SAM confidence_threshold должен быть 0..1")
        self.prompts = self.options.get("prompts") or {"cube": "cube", "box": "box"}
        if set(self.prompts) != set(CATEGORIES) or not all(isinstance(v,str) and v.strip() for v in self.prompts.values()):
            raise AnnotationError("SAM prompts должны содержать только непустые cube и box")
        self.model = None
        self.processor = None
        self.provenance = None
        self._configuration_hash = None

    def configuration_hash(self):
        """Cheap immutable cache/source identity for resume, without model load.

        Shared HF blob filename is its content digest; explicit checkpoint paths
        include size/mtime plus a real SHA256 calculated before model loading.
        """
        if self._configuration_hash is None:
            checkpoint = _find_checkpoint(self.options.get("checkpoint_path"))
            source_manifest = Path(__file__).resolve().parents[2]/"vendor/sam3-provenance.json"
            identity = {"checkpoint_realpath": str(checkpoint.resolve()), "size":checkpoint.stat().st_size,
                        "mtime_ns":checkpoint.stat().st_mtime_ns, "prompts":self.prompts,
                        "threshold":self.threshold, "preprocessing":"official_Sam3Processor_1008_RGB_v1",
                        "source_manifest":source_manifest.read_text() if source_manifest.is_file() else None,
                        "backend":"sam3", "schema":SCHEMA_VERSION}
            self._configuration_hash = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        return self._configuration_hash

    def load(self):
        if self.model is not None:
            return self
        from .resources import assert_compute_available
        assert_compute_available(self.device, minimum_available_gb=max(8.,float(self.options.get("minimum_available_gb", 8.))))
        _check_own_import("torch")
        _check_own_import("sam3")
        checkpoint = _find_checkpoint(self.options.get("checkpoint_path"))
        import torch
        import sam3
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor
        if str(self.device).startswith("cuda") and not torch.cuda.is_available():
            raise AnnotationError("CUDA недоступна; SAM3 не переключается молча на CPU")
        self.model = build_sam3_image_model(device=self.device, checkpoint_path=str(checkpoint), load_from_HF=False, compile=False, enable_inst_interactivity=False)
        self.processor = Sam3Processor(self.model, device=self.device, confidence_threshold=self.threshold)
        source_manifest = Path(__file__).resolve().parents[2]/"vendor/sam3-provenance.json"
        source = json.loads(source_manifest.read_text()) if source_manifest.is_file() else {"source_path": str(Path(sam3.__file__).resolve()), "version": getattr(sam3,"__version__",None)}
        # Hash once per model lifecycle; this reads the shared cache without changing it.
        self.provenance = {"backend": "sam3", "checkpoint_path": str(checkpoint), "checkpoint_sha256": sha256_file(checkpoint),
                           "source": source, "prompts": self.prompts, "confidence_threshold": self.threshold,
                           "preprocessing": "official_Sam3Processor_1008_RGB_v1", "tracking": "causal_box_IoU_v1", "device": self.device,
                           "configuration_hash": self.configuration_hash()}
        return self

    def segment(self, rgb: np.ndarray) -> list[dict]:
        self.load()
        import torch
        autocast = torch.autocast(device_type="cuda", dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16) if str(self.device).startswith("cuda") else contextlib.nullcontext()
        out = []
        with torch.inference_mode(), autocast:
            state = self.processor.set_image(Image.fromarray(np.asarray(rgb,dtype=np.uint8), mode="RGB"))
            for category, prompt in self.prompts.items():
                self.processor.reset_all_prompts(state)
                output = self.processor.set_text_prompt(state=state, prompt=prompt)
                masks = output["masks"].detach().cpu().numpy()
                boxes = output["boxes"].detach().float().cpu().numpy()
                scores = output["scores"].detach().float().cpu().numpy()
                for mask, box, score in zip(masks, boxes, scores):
                    mask = np.asarray(mask).reshape(rgb.shape[:2]).astype(bool)
                    if mask.any():
                        out.append({"category": category, "score": float(score), "bbox_xyxy": box.tolist(), "mask": mask})
        return out

    def annotate_observation(self, frame: dict, images: dict[str,np.ndarray], associators: dict | None = None, mask_writer=None) -> dict:
        self.load()
        return annotate_observation(frame, images, self, associators=associators, mask_writer=mask_writer)


def annotate_observation(frame: dict, images: dict[str,np.ndarray], segmenter, *, associators: dict | None = None, mask_writer=None) -> dict:
    """Shared train/live annotation contract. No future frames or action labels read."""
    cutoff = int(frame.get("observation_timestamp_ns",frame["timestamp_ns"]))
    cameras = {}
    for camera, raw in sorted(frame["cameras"].items()):
        ts = int(raw["timestamp_ns"])
        if ts > cutoff:
            raise AnnotationError(f"{camera}: кадр из будущего относительно observation")
        if not raw.get("valid",False) or camera not in images:
            cameras[camera] = {"valid": False, "timestamp_ns": ts, "raw_sha256": raw.get("sha256"), "objects": [], "reason": "Нет валидного raw кадра"}
            continue
        rgb = np.asarray(images[camera],dtype=np.uint8)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise AnnotationError("SAM ожидает RGB HWC")
        h,w = rgb.shape[:2]
        objects = []
        detections = segmenter.segment(rgb)
        for detection in detections:
            category = detection["category"]
            score = float(detection["score"])
            if category not in CATEGORIES or not math.isfinite(score) or not 0<=score<=1:
                raise AnnotationError("Неверный category/score SAM")
            mask = np.asarray(detection["mask"], dtype=bool)
            if mask.shape != (h,w) or not mask.any():
                raise AnnotationError("Неверная/пустая SAM mask")
            y,x = np.nonzero(mask)
            bbox = [float(v) for v in detection["bbox_xyxy"]]
            if len(bbox)!=4 or not all(math.isfinite(v) for v in bbox):
                raise AnnotationError("Некорректный SAM box")
            obj = {"category": category, "score": score, "bbox_xyxy": bbox,
                   "center_xy": [float(x.mean()),float(y.mean())], "area_px": len(x), "visibility": "visible_mask",
                   "occlusion": {"valid": False, "value": None, "reason": "Single RGB mask не определяет скрытую площадь"},
                   "touches_image_border": bool(mask[0].any() or mask[-1].any() or mask[:,0].any() or mask[:,-1].any()),
                   "color": measure_color(rgb,mask), "geometry": unknown_geometry(), "mask_encoding": "png_binary_0_255_lossless"}
            if mask_writer:
                obj.update(mask_writer(camera,len(objects),mask))
            else:
                # Live consumers may use an in-memory mask; never serialized as fake path.
                obj["mask"] = mask
            objects.append(obj)
        tracker = associators.get(camera) if associators else None
        if tracker is None:
            tracker = CausalAssociator(camera)
            if associators is not None:
                associators[camera] = tracker
        objects,lost = tracker.update(objects,ts,raw["sha256"])
        cameras[camera] = {"valid": True, "timestamp_ns": ts, "raw_sha256": raw["sha256"], "width": w, "height": h,
                           "objects": objects, "lost_track_ids": lost, "tracking_counter":tracker.counter, "absence_interpretation": "not_detected_not_proven_absent"}
    return {"schema_version": SCHEMA_VERSION, "frame_id": frame["frame_id"], "timestamp_ns": int(frame["timestamp_ns"]),
            "source_frame_hash": source_frame_digest(frame), "observation_timestamp_ns": cutoff, "cameras": cameras,
            "provenance": dict(segmenter.provenance or {"backend":segmenter.backend}),
            "generated_at_monotonic_ns": time.monotonic_ns()}


def validate_frame_annotations(record: dict, raw: dict, episode_path: Path | None = None, *, require_cameras=("top","wrist"), allow_test=False, verify_masks=True) -> dict:
    if record.get("schema_version") != SCHEMA_VERSION:
        raise AnnotationError("Неподдерживаемая annotation schema")
    if record.get("provenance",{}).get("backend") != "sam3" and not allow_test:
        raise AnnotationError("Технические аннотации запрещены для реального обучения")
    if record.get("frame_id") != raw["frame_id"] or record.get("timestamp_ns") != raw["timestamp_ns"] or record.get("source_frame_hash") != source_frame_digest(raw):
        raise AnnotationError("Stale/misaligned annotations: frame/hash/timestamp не совпадают")
    cutoff = int(raw.get("observation_timestamp_ns",raw["timestamp_ns"]))
    if record.get("observation_timestamp_ns",record["timestamp_ns"]) != cutoff:
        raise AnnotationError("Сдвиг observation timestamp")
    for camera in require_cameras:
        ann = record.get("cameras",{}).get(camera)
        source = raw.get("cameras",{}).get(camera)
        if not ann or not source or not ann.get("valid") or not source.get("valid"):
            raise AnnotationError(f"Missing annotation/raw: {camera}")
        if ann.get("timestamp_ns") != source.get("timestamp_ns") or ann["timestamp_ns"] > cutoff or ann.get("raw_sha256") != source.get("sha256"):
            raise AnnotationError(f"Stale/future camera annotation: {camera}")
        if not isinstance(ann.get("objects"),list) or not all(isinstance(ann.get(k),int) and ann[k]>0 for k in ("width","height")):
            raise AnnotationError("Неверный camera annotation")
        if episode_path is not None:
            source_file = _safe_relative(episode_path,source["path"])
            if not source_file.is_file() or sha256_file(source_file) != source["sha256"]:
                raise AnnotationError("Raw image изменено/потеряно")
            with Image.open(source_file) as im:
                if im.size != (ann["width"],ann["height"]):
                    raise AnnotationError("Размер raw image не совпадает с annotation")
        for obj in ann["objects"]:
            if obj.get("category") not in CATEGORIES or not isinstance(obj.get("score"),(float,int)) or not math.isfinite(obj["score"]) or not 0<=obj["score"]<=1:
                raise AnnotationError("Неверный object annotation")
            for key,n in (("bbox_xyxy",4),("center_xy",2)):
                values = obj.get(key)
                if not isinstance(values,list) or len(values)!=n or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
                    raise AnnotationError(f"Неверное {key}")
            if episode_path is not None and verify_masks:
                if not obj.get("mask_path") or not obj.get("mask_sha256"):
                    raise AnnotationError("Маска не сохранена")
                path = _safe_relative(episode_path,obj["mask_path"])
                if not path.is_file() or sha256_file(path)!=obj["mask_sha256"]:
                    raise AnnotationError("Missing/stale mask")
                with Image.open(path) as im:
                    if im.size != (ann["width"],ann["height"]) or im.mode != "L":
                        raise AnnotationError("Неверный формат маски")
                    if not set(np.unique(im)).issubset({0,255}):
                        raise AnnotationError("Маска не бинарная lossless")
    return record


def load_frame_annotations(episode_path: Path, raw_frame: dict, version=DEFAULT_VERSION, require_cameras=("top","wrist"), *, allow_test=False, verify_masks=True) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",version) or version in (".",".."):
        raise AnnotationError("Неверная версия annotations")
    path = Path(episode_path)/"annotations"/version/"frames.jsonl"
    records = _read_jsonl(path)
    matches = [x for x in records if x.get("frame_id")==raw_frame["frame_id"]]
    if not matches:
        raise AnnotationError(f"Missing annotations для {raw_frame['frame_id']}")
    return validate_frame_annotations(matches[-1],raw_frame,Path(episode_path),require_cameras=require_cameras,allow_test=allow_test,verify_masks=verify_masks)


def _emit(progress, payload):
    if progress:
        progress(payload)


def annotate_dataset(dataset_path: Path, options: dict | None = None, progress: Callable | None = None, stop: Callable | None = None) -> dict:
    options = dict(options or {})
    dataset_path = Path(dataset_path).resolve()
    metadata = json.loads((dataset_path/"dataset.json").read_text())
    allow_test = options.get("allow_test") is True
    if metadata.get("source") != "hardware" and not allow_test:
        raise AnnotationError("Test source исключён. Для технической проверки нужно allow_test=True")
    version = options.get("version",DEFAULT_VERSION)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",version) or version in (".",".."):
        raise AnnotationError("Неверная версия annotations")
    # Dependency injection only for explicit technical tests; never exposed as UI option.
    segmenter = options.pop("_test_segmenter",None)
    if segmenter is not None and not allow_test:
        raise AnnotationError("Test segmenter запрещён")
    segmenter = segmenter or SAM3Segmenter(options)
    config_hash = segmenter.configuration_hash() if hasattr(segmenter,"configuration_hash") else hashlib.sha256(json.dumps(segmenter.provenance,sort_keys=True).encode()).hexdigest()
    episodes = sorted(p for p in (dataset_path/"episodes").iterdir() if p.is_dir() and not p.name.endswith(".partial"))
    total = sum(len(_read_jsonl(ep/"frames.jsonl")) for ep in episodes)
    done = 0
    summary = {"status":"running","total_frames":total,"completed_frames":0,"version":version,"backend":segmenter.backend,"episodes":[]}
    dataset_status = dataset_path/"annotation_status.json"
    _atomic_json(dataset_status,summary)
    # Root's worker calls this function only after queue resource checks; additionally
    # acquire a shared compute lease before any actual SAM model construction.
    if segmenter.backend == "sam3":
        from .resources import ResourceLease
        lease = ResourceLease("gpu_compute" if str(options.get("device","cuda")).startswith("cuda") else "cpu_compute")
    else:
        lease = contextlib.nullcontext()
    try:
        with lease:
            for episode in episodes:
                episode_meta = json.loads((episode/"episode.json").read_text())
                if episode_meta.get("status") not in ("complete","saved"):
                    raise AnnotationError(f"Нельзя размечать незавершённый эпизод {episode.name}")
                rows = _read_jsonl(episode/"frames.jsonl")
                outdir = episode/"annotations"/version
                outdir.mkdir(parents=True,exist_ok=True)
                output_path = outdir/"frames.jsonl"
                existing_rows = _read_jsonl(output_path,recover_last=True)
                # Rewrite recovered records atomically, eliminating a torn final line.
                tmp = output_path.with_suffix(".jsonl.tmp")
                with tmp.open("w") as f:
                    for row in existing_rows:
                        f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")
                    f.flush(); os.fsync(f.fileno())
                os.replace(tmp,output_path)
                existing = {row["frame_id"]:row for row in existing_rows}
                trackers = {}
                ep_done = 0
                replay_after_stale = False
                for raw in rows:
                    if stop and stop():
                        summary.update(status="stopped",completed_frames=done)
                        _atomic_json(dataset_status,summary)
                        _emit(progress,summary)
                        return summary
                    record = existing.get(raw["frame_id"])
                    if record and not replay_after_stale:
                        try:
                            validate_frame_annotations(record,raw,episode,allow_test=allow_test)
                            if record.get("provenance",{}).get("configuration_hash") != config_hash:
                                raise AnnotationError("Изменилась конфигурация SAM/preprocessing/source")
                            for cam, ann in record["cameras"].items():
                                if ann.get("valid"):
                                    trackers.setdefault(cam,CausalAssociator(cam)).restore(ann)
                            done += 1; ep_done += 1
                            summary.update(completed_frames=done)
                            _emit(progress,{**summary,"episode_id":episode.name,"resumed":True})
                            continue
                        except AnnotationError:
                            replay_after_stale = True
                            # Explicitly replace stale record, never silently use it.
                            _emit(progress,{"status":"reprocessing_stale","episode_id":episode.name,"frame_id":raw["frame_id"]})
                    images = {}
                    for cam, info in raw["cameras"].items():
                        if not info.get("valid"):
                            raise AnnotationError(f"{episode.name}/{raw['frame_id']} {cam}: missing raw frame")
                        path = _safe_relative(episode,info["path"])
                        if sha256_file(path) != info["sha256"]:
                            raise AnnotationError("Raw SHA256 не совпадает")
                        with Image.open(path) as im:
                            images[cam] = np.asarray(im.convert("RGB"))
                    def mask_writer(camera,index,mask):
                        file = outdir/"masks"/camera/f"{raw['frame_id']}_{index:03d}.png"
                        file.parent.mkdir(parents=True,exist_ok=True)
                        tmp = file.with_suffix(".png.tmp")
                        Image.fromarray(mask.astype(np.uint8)*255,mode="L").save(tmp,format="PNG")
                        with tmp.open("rb") as f:
                            os.fsync(f.fileno())
                        os.replace(tmp,file)
                        return {"mask_path":file.relative_to(episode).as_posix(),"mask_sha256":sha256_file(file)}
                    record = annotate_observation(raw,images,segmenter,associators=trackers,mask_writer=mask_writer)
                    record["provenance"]["configuration_hash"] = config_hash
                    validate_frame_annotations(record,raw,episode,allow_test=allow_test)
                    with output_path.open("a") as f:
                        f.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+"\n")
                        f.flush(); os.fsync(f.fileno())
                    done += 1; ep_done += 1
                    summary.update(completed_frames=done)
                    _atomic_json(dataset_status,summary)
                    _emit(progress,{**summary,"episode_id":episode.name,"frame_id":raw["frame_id"]})
                _atomic_json(outdir/"manifest.json",{"schema_version":SCHEMA_VERSION,"status":"complete","frames":ep_done,"version":version,"provenance":(record["provenance"] if rows else segmenter.provenance),"causal":True,"configuration_hash":config_hash})
                summary["episodes"].append({"episode_id":episode.name,"frames":ep_done})
        summary.update(status="complete",completed_frames=done)
        _atomic_json(dataset_status,summary)
        _emit(progress,summary)
        return summary
    except Exception as exc:
        summary.update(status="failed",completed_frames=done,error=str(exc),traceback=traceback.format_exc())
        _atomic_json(dataset_status,summary)
        _emit(progress,{k:v for k,v in summary.items() if k!="traceback"})
        raise
