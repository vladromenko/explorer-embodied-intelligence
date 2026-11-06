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
PREPROCESSING = "official_Sam3Processor_1008_RGB_v1"
TRACKING_METHOD = "causal_box_IoU_v1"
COLOR_METHOD = "visible_mask_median_RGB_HSV_v2_lowlight_invalid"
PROCESSING_CONTRACT = {"schema": SCHEMA_VERSION, "preprocessing": PREPROCESSING,
                       "tracking": TRACKING_METHOD, "color": COLOR_METHOD,
                       "association_min_iou": .3, "association_ambiguity_margin": .1,
                       "association_max_gap_ns": 1_000_000_000,
                       "cross_category_ambiguity": "mask_IoU_v1", "cross_category_ambiguity_iou": .8,
                       "minimum_color_value": .15}


def annotation_provenance_identity(provenance: dict) -> dict:
    """Meaning of observation features, independent of deployment path/device.

    A checkpoint cache relocation may change a pathname or mtime without changing
    the model. Those are deliberately excluded from train/inference identity.
    """
    keys = ("backend", "checkpoint_sha256", "prompts", "confidence_threshold",
            "preprocessing", "source", "tracking", "processing_contract")
    return {key: copy.deepcopy(provenance.get(key)) for key in keys}


def validate_annotation_provenance(provenance: dict, *, allow_test=False, expected: dict | None = None) -> dict:
    if not isinstance(provenance, dict):
        raise AnnotationError("Отсутствует provenance SAM")
    backend = provenance.get("backend")
    if backend != "sam3":
        if not allow_test or backend != "technical_test":
            raise AnnotationError("Технические аннотации запрещены для реального обучения")
    else:
        digest = provenance.get("checkpoint_sha256")
        source = provenance.get("source")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AnnotationError("Неполный SAM provenance: weights SHA256")
        if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit", ""))) or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("runtime_sha256", ""))):
            raise AnnotationError("Неполный SAM provenance: source commit/runtime SHA256")
        prompts = provenance.get("prompts")
        threshold = provenance.get("confidence_threshold")
        if not isinstance(prompts, dict) or set(prompts) != set(CATEGORIES) or not all(isinstance(x, str) and x.strip() for x in prompts.values()):
            raise AnnotationError("Неполный SAM provenance: prompts")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise AnnotationError("Неполный SAM provenance: threshold")
        if provenance.get("preprocessing") != PREPROCESSING or provenance.get("tracking") != TRACKING_METHOD or provenance.get("processing_contract") != PROCESSING_CONTRACT:
            raise AnnotationError("Несовместимый SAM preprocessing/tracking/color contract")
    if expected is not None and annotation_provenance_identity(provenance) != annotation_provenance_identity(expected):
        raise AnnotationError("Train/inference SAM provenance differs")
    return provenance


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
        return {"valid": False, "label": "unknown", "rgb": None, "hsv": None, "method": COLOR_METHOD, "reason": "Слишком мало видимых пикселей"}
    median = np.median(pixels, axis=0) / 255.
    hue, saturation, value = colorsys.rgb_to_hsv(*median.tolist())
    if value < PROCESSING_CONTRACT["minimum_color_value"]:
        label = "unknown"
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
    return {"valid": value >= PROCESSING_CONTRACT["minimum_color_value"], "label": label,
            "reason": "Недостаточная яркость для цвета; RGB/HSV измерены" if value < PROCESSING_CONTRACT["minimum_color_value"] else None, "rgb": (median*255).tolist(), "rgb_mad": (deviation*255).tolist(),
            "hsv": [hue, saturation, value], "method": COLOR_METHOD, "pixel_count": len(pixels),
            "confidence": "unvalidated_lighting_dependent", "limitations": "Без color-chart калибровки; блики/тени меняют цвет"}


def mark_category_ambiguities(objects: list[dict], masks: list[np.ndarray], *, threshold: float = .8) -> list[dict]:
    """Flag contradictory text-query detections without guessing a true category.

    Raw SAM category/score/mask stay intact. The policy adapter must omit ambiguous
    detections from categorical slots; these are not two proven physical objects.
    """
    if len(objects) != len(masks):
        raise AnnotationError("Число объектов и масок не совпадает")
    for obj in objects:
        obj["category_ambiguous"] = False
        obj["category_candidates"] = [obj["category"]]
        obj["cross_category_overlaps"] = []
    for i, left in enumerate(objects):
        for j in range(i+1, len(objects)):
            right = objects[j]
            if left["category"] != right["category"]:
                a, b = np.asarray(masks[i], dtype=bool), np.asarray(masks[j], dtype=bool)
                if a.shape != b.shape:
                    raise AnnotationError("Не совпадают размеры масок ambiguity")
                intersection = int(np.count_nonzero(a & b))
                union = int(np.count_nonzero(a | b))
                iou = intersection/union if union else 0.
                if iou >= threshold:
                    left["category_ambiguous"] = right["category_ambiguous"] = True
                    left["category_candidates"] = sorted(set(left["category_candidates"]+[right["category"]]))
                    right["category_candidates"] = sorted(set(right["category_candidates"]+[left["category"]]))
                    left["cross_category_overlaps"].append({"other_index": j, "mask_iou": iou})
                    right["cross_category_overlaps"].append({"other_index": i, "mask_iou": iou})
    return objects


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
    def __init__(self, camera: str, min_iou: float = .3, ambiguity_margin: float = .1, max_gap_ns: int = 1_000_000_000):
        self.camera = camera
        self.min_iou = min_iou
        self.ambiguity_margin = ambiguity_margin
        self.max_gap_ns = int(max_gap_ns)
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
        expired = []
        if self.last_timestamp is not None and timestamp_ns - self.last_timestamp > self.max_gap_ns:
            expired = [o["tracking"]["id"] for o in self.previous]
            self.previous = []
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
        lost = expired + [o["tracking"]["id"] for i,o in enumerate(self.previous) if i not in matched]
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


def _sam_source_provenance() -> dict:
    """Hash own installed Python/tokenizer source; no model import or construction."""
    _check_own_import("sam3")
    spec = importlib.util.find_spec("sam3")
    package = Path(spec.origin).resolve().parent
    digest = hashlib.sha256()
    files = sorted(p for p in package.rglob("*") if p.is_file() and (p.suffix == ".py" or p.suffix == ".gz"))
    for file in files:
        relative = file.relative_to(package).as_posix()
        digest.update(relative.encode() + b"\0" + sha256_file(file).encode() + b"\0")
    root = Path(__file__).resolve().parents[2]
    manifest_paths = (root/"config/sam3-provenance.json", root/"vendor/sam3-provenance.json")
    manifest = next((p for p in manifest_paths if p.is_file()), None)
    if manifest is None:
        raise AnnotationError("Нет закреплённого source manifest SAM3")
    source = json.loads(manifest.read_text())
    source["runtime_sha256"] = digest.hexdigest()
    return source


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
        self.options = copy.deepcopy(options or {})
        self.device = self.options.get("device", "cuda")
        if self.device not in ("cpu", "cuda"):
            raise AnnotationError("SAM3 device должен быть cuda или cpu; скрытый fallback запрещён")
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
        self._checkpoint_sha256 = None
        self._source_provenance = None

    def configuration_hash(self):
        """Content identity; survives cache relocation and detects source changes."""
        if self._configuration_hash is None:
            checkpoint = _find_checkpoint(self.options.get("checkpoint_path"))
            self._checkpoint_sha256 = sha256_file(checkpoint)
            self._source_provenance = _sam_source_provenance()
            identity = {"checkpoint_sha256": self._checkpoint_sha256, "prompts": self.prompts,
                        "threshold": self.threshold, "source": self._source_provenance,
                        "processing_contract": PROCESSING_CONTRACT, "backend": "sam3"}
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
        configuration_hash = self.configuration_hash()
        import torch
        import sam3
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor
        if str(self.device).startswith("cuda") and not torch.cuda.is_available():
            raise AnnotationError("CUDA недоступна; SAM3 не переключается молча на CPU")
        self.model = build_sam3_image_model(device=self.device, checkpoint_path=str(checkpoint), load_from_HF=False, compile=False, enable_inst_interactivity=False)
        self.processor = Sam3Processor(self.model, device=self.device, confidence_threshold=self.threshold)
        self.provenance = {"backend": "sam3", "checkpoint_path": str(checkpoint),
                           "checkpoint_sha256": self._checkpoint_sha256,
                           "source": self._source_provenance, "prompts": self.prompts,
                           "confidence_threshold": self.threshold, "preprocessing": PREPROCESSING,
                           "tracking": TRACKING_METHOD, "processing_contract": dict(PROCESSING_CONTRACT),
                           "device": self.device, "configuration_hash": configuration_hash}
        validate_annotation_provenance(self.provenance)
        return self

    def segment(self, rgb: np.ndarray) -> list[dict]:
        rgb = np.asarray(rgb)
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise AnnotationError("SAM ожидает uint8 RGB HWC без скрытого dtype преобразования")
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
        else:
            rgb = np.asarray(images[camera])
            if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
                raise AnnotationError("SAM ожидает uint8 RGB HWC без скрытого dtype преобразования")
            h,w = rgb.shape[:2]
            objects = []
            segmentation_masks = []
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
                segmentation_masks.append(mask)
            mark_category_ambiguities(objects, segmentation_masks, threshold=PROCESSING_CONTRACT["cross_category_ambiguity_iou"])
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
    validate_annotation_provenance(record.get("provenance"), allow_test=allow_test)
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
        verified_masks = []
        for obj in ann["objects"]:
            if obj.get("category") not in CATEGORIES or not isinstance(obj.get("score"),(float,int)) or not math.isfinite(obj["score"]) or not 0<=obj["score"]<=1:
                raise AnnotationError("Неверный object annotation")
            for key,n in (("bbox_xyxy",4),("center_xy",2)):
                values = obj.get(key)
                if not isinstance(values,list) or len(values)!=n or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in values):
                    raise AnnotationError(f"Неверное {key}")
            box = obj["bbox_xyxy"]
            center = obj["center_xy"]
            if box[0] >= box[2] or box[1] >= box[3] or not (0 <= center[0] < ann["width"] and 0 <= center[1] < ann["height"]):
                raise AnnotationError("Некорректные границы box/center")
            if episode_path is not None and verify_masks:
                if not obj.get("mask_path") or not obj.get("mask_sha256"):
                    raise AnnotationError("Маска не сохранена")
                path = _safe_relative(episode_path,obj["mask_path"])
                if not path.is_file() or sha256_file(path)!=obj["mask_sha256"]:
                    raise AnnotationError("Missing/stale mask")
                with Image.open(path) as im:
                    if im.size != (ann["width"],ann["height"]) or im.mode != "L":
                        raise AnnotationError("Неверный формат маски")
                    mask = np.asarray(im)
                    if not set(np.unique(mask)).issubset({0,255}):
                        raise AnnotationError("Маска не бинарная lossless")
                    ys, xs = np.nonzero(mask)
                    if len(xs) == 0 or obj.get("area_px") != len(xs):
                        raise AnnotationError("Mask area не совпадает с annotation")
                    if not np.allclose(obj["center_xy"], [xs.mean(), ys.mean()], rtol=0, atol=1e-6):
                        raise AnnotationError("Mask center не совпадает с annotation")
                    verified_masks.append(mask > 0)
        if episode_path is not None and verify_masks:
            expected_objects = mark_category_ambiguities(copy.deepcopy(ann["objects"]), verified_masks,
                threshold=PROCESSING_CONTRACT["cross_category_ambiguity_iou"])
            for actual, expected in zip(ann["objects"], expected_objects):
                if actual.get("category_ambiguous") is not expected["category_ambiguous"]:
                    raise AnnotationError("Mask category ambiguity не совпадает с annotation")
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
        progress(copy.deepcopy(payload))


def _annotation_counts(record: dict) -> dict:
    by_camera = {name: len(cam["objects"]) for name, cam in record["cameras"].items() if cam.get("valid")}
    by_category = {name: 0 for name in CATEGORIES}
    for camera in record["cameras"].values():
        for obj in camera.get("objects", []):
            by_category[obj["category"]] += 1
    objects = [obj for cam in record["cameras"].values() for obj in cam.get("objects", [])]
    ambiguous = sum(obj.get("category_ambiguous") is True for obj in objects)
    return {"completed_images": len(by_camera), "detected_objects": sum(by_camera.values()),
            "ambiguous_objects": ambiguous, "usable_category_objects": len(objects)-ambiguous,
            "color_valid_objects": sum(obj.get("color", {}).get("valid") is True for obj in objects),
            "objects_by_camera": by_camera, "objects_by_category": by_category}


def _add_progress_counts(summary: dict, record: dict, *, resumed: bool):
    counts = _annotation_counts(record)
    summary["completed_frames"] += 1
    summary["resumed_frames" if resumed else "new_frames"] += 1
    for field in ("completed_images", "detected_objects", "ambiguous_objects", "usable_category_objects", "color_valid_objects"):
        summary[field] += counts[field]
    for field in ("objects_by_camera", "objects_by_category"):
        for key, count in counts[field].items():
            summary[field][key] = summary[field].get(key, 0) + count


def _check_raw_observation_order(rows: list[dict]):
    previous_observation = None
    previous_capture = {}
    for row in rows:
        source_frame_digest(row)  # validates a safe, exact frame identity before mask paths
        cutoff = row.get("observation_timestamp_ns", row["timestamp_ns"])
        if isinstance(cutoff, bool) or not isinstance(cutoff, int) or cutoff != row["timestamp_ns"]:
            raise AnnotationError("Некорректное время raw observation")
        if previous_observation is not None and cutoff <= previous_observation:
            raise AnnotationError("Немонотонные raw observations")
        previous_observation = cutoff
        for camera, raw in row["cameras"].items():
            timestamp = raw.get("timestamp_ns")
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp > cutoff:
                raise AnnotationError("Кадр из будущего/неверное raw camera время")
            old = previous_capture.get(camera)
            if old and (timestamp < old[0] or (timestamp == old[0] and raw.get("sha256") != old[1])):
                raise AnnotationError("Немонотонный/изменённый raw camera capture")
            previous_capture[camera] = (timestamp, raw.get("sha256"))


def annotate_dataset(dataset_path: Path, options: dict | None = None, progress: Callable | None = None, stop: Callable | None = None) -> dict:
    options = dict(options or {})
    dataset_path = Path(dataset_path).resolve()
    metadata = json.loads((dataset_path/"dataset.json").read_text())
    allow_test = options.get("allow_test") is True
    if metadata.get("source") != "hardware" and not allow_test:
        raise AnnotationError("Test source исключён. Для технической проверки нужно allow_test=True")
    version = options.get("version", DEFAULT_VERSION)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", version) or version in (".", ".."):
        raise AnnotationError("Неверная версия annotations")
    # Dependency injection only for explicit technical tests; never a UI option.
    segmenter = options.pop("_test_segmenter", None)
    if segmenter is not None and not allow_test:
        raise AnnotationError("Test segmenter запрещён")
    segmenter = segmenter or SAM3Segmenter(options)
    config_hash = segmenter.configuration_hash() if hasattr(segmenter, "configuration_hash") else hashlib.sha256(json.dumps(segmenter.provenance, sort_keys=True).encode()).hexdigest()
    episodes = sorted(p for p in (dataset_path/"episodes").iterdir() if p.is_dir() and not p.name.endswith(".partial"))
    if not episodes:
        raise AnnotationError("Нет завершённых эпизодов для SAM-разметки")
    total = sum(len(_read_jsonl(ep/"frames.jsonl")) for ep in episodes)
    if total == 0:
        raise AnnotationError("Нет raw кадров для SAM-разметки")
    summary = {"status": "running", "phase": "validating", "total_frames": total,
               "completed_frames": 0, "completed_images": 0, "detected_objects": 0,
               "ambiguous_objects": 0, "usable_category_objects": 0, "color_valid_objects": 0,
               "new_frames": 0, "resumed_frames": 0, "version": version, "backend": segmenter.backend,
               "objects_by_category": {name: 0 for name in CATEGORIES}, "objects_by_camera": {},
               "count_semantics": "camera-frame object observations, not unique physical objects",
               "episodes": []}
    dataset_status = dataset_path/"annotation_status.json"
    _atomic_json(dataset_status, summary)
    _emit(progress, summary)
    if segmenter.backend == "sam3":
        from .resources import ResourceLease
        lease = ResourceLease("gpu_compute" if str(options.get("device", "cuda")).startswith("cuda") else "cpu_compute")
    else:
        lease = contextlib.nullcontext()
    current_manifest = None
    try:
        with lease:
            for episode in episodes:
                episode_meta = json.loads((episode/"episode.json").read_text())
                if episode_meta.get("status") not in ("complete", "saved"):
                    raise AnnotationError(f"Нельзя размечать незавершённый эпизод {episode.name}")
                rows = _read_jsonl(episode/"frames.jsonl")
                if not rows:
                    raise AnnotationError(f"Нет raw кадров в эпизоде {episode.name}")
                _check_raw_observation_order(rows)
                outdir = episode/"annotations"/version
                outdir.mkdir(parents=True, exist_ok=True)
                current_manifest = outdir/"manifest.json"
                manifest = {"schema_version": SCHEMA_VERSION, "status": "in_progress", "frames": 0,
                            "version": version, "causal": True, "configuration_hash": config_hash}
                _atomic_json(current_manifest, manifest)
                output_path = outdir/"frames.jsonl"
                existing_rows = _read_jsonl(output_path, recover_last=True)
                # Recover only a torn final JSON line. Interior corruption is fatal.
                tmp = output_path.with_suffix(".jsonl.tmp")
                with tmp.open("w") as file:
                    for record in existing_rows:
                        file.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+"\n")
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(tmp, output_path)
                existing = {row["frame_id"]: row for row in existing_rows}
                trackers = {}
                ep_done = 0
                initial_object_count = summary["detected_objects"]
                initial_image_count = summary["completed_images"]
                replay_after_gap = False
                for raw in rows:
                    if stop and stop():
                        summary.update(status="stopped", phase="stopped")
                        manifest.update(status="stopped", frames=ep_done)
                        _atomic_json(current_manifest, manifest)
                        _atomic_json(dataset_status, summary)
                        _emit(progress, summary)
                        return summary
                    record = existing.get(raw["frame_id"])
                    resumed = False
                    if record and not replay_after_gap:
                        try:
                            validate_frame_annotations(record, raw, episode, allow_test=allow_test)
                            if record.get("provenance", {}).get("configuration_hash") != config_hash:
                                raise AnnotationError("Изменилась конфигурация SAM/preprocessing/source")
                            for camera, ann in record["cameras"].items():
                                if ann.get("valid"):
                                    trackers.setdefault(camera, CausalAssociator(camera)).restore(ann)
                            resumed = True
                        except AnnotationError:
                            _emit(progress, {**summary, "phase": "reprocessing_stale", "episode_id": episode.name, "frame_id": raw["frame_id"]})
                    if not resumed:
                        # A missing interior row invalidates every later tracking
                        # association, even if its raw image itself is unchanged.
                        replay_after_gap = True
                        summary["phase"] = "segmenting"
                        _emit(progress, {**summary, "episode_id": episode.name, "frame_id": raw["frame_id"]})
                        from .resources import assert_disk_space
                        assert_disk_space(minimum_bytes=32*1024**2, path=outdir)
                        images = {}
                        for camera, info in raw["cameras"].items():
                            if not info.get("valid"):
                                raise AnnotationError(f"{episode.name}/{raw['frame_id']} {camera}: missing raw frame")
                            path = _safe_relative(episode, info["path"])
                            if sha256_file(path) != info["sha256"]:
                                raise AnnotationError("Raw SHA256 не совпадает")
                            with Image.open(path) as image:
                                images[camera] = np.asarray(image.convert("RGB"))
                        def mask_writer(camera, index, mask):
                            file = outdir/"masks"/camera/f"{raw['frame_id']}_{index:03d}.png"
                            file.parent.mkdir(parents=True, exist_ok=True)
                            temporary = file.with_suffix(".png.tmp")
                            Image.fromarray(mask.astype(np.uint8)*255, mode="L").save(temporary, format="PNG")
                            with temporary.open("rb") as output:
                                os.fsync(output.fileno())
                            os.replace(temporary, file)
                            return {"mask_path": file.relative_to(episode).as_posix(), "mask_sha256": sha256_file(file)}
                        record = annotate_observation(raw, images, segmenter, associators=trackers, mask_writer=mask_writer)
                        record["provenance"]["configuration_hash"] = config_hash
                        validate_frame_annotations(record, raw, episode, allow_test=allow_test)
                        with output_path.open("a") as file:
                            file.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+"\n")
                            file.flush()
                            os.fsync(file.fileno())
                    ep_done += 1
                    _add_progress_counts(summary, record, resumed=resumed)
                    summary["phase"] = "annotating"
                    _atomic_json(dataset_status, summary)
                    _emit(progress, {**summary, "episode_id": episode.name, "frame_id": raw["frame_id"], "resumed": resumed})
                manifest.update(status="complete", frames=ep_done, provenance=record["provenance"],
                                detected_objects=summary["detected_objects"]-initial_object_count,
                                completed_images=summary["completed_images"]-initial_image_count)
                _atomic_json(current_manifest, manifest)
                summary["episodes"].append({"episode_id": episode.name, "frames": ep_done,
                    "detected_objects": manifest["detected_objects"], "completed_images": manifest["completed_images"]})
        summary.update(status="complete", phase="complete")
        _atomic_json(dataset_status, summary)
        _emit(progress, summary)
        return summary
    except Exception as exc:
        summary.update(status="failed", phase="failed", error=str(exc), traceback=traceback.format_exc())
        if current_manifest is not None:
            manifest.update(status="failed", error=str(exc), frames=ep_done)
            _atomic_json(current_manifest, manifest)
        _atomic_json(dataset_status, summary)
        _emit(progress, {key: value for key, value in summary.items() if key != "traceback"})
        raise
