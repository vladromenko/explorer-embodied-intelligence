#!/usr/bin/env python3
"""Explicitly invoked real SAM3 CUDA smoke on already captured image files.

Never opens cameras/bus, changes controls, synthesizes masks, or enables torque.
This script is intentionally outside pytest discovery and requires --execute.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))


def atomic_json(path, payload):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def available_bytes():
    values = {line.split(":", 1)[0]: int(line.split()[1])*1024 for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line}
    return values["MemAvailable"]


def postprocess_saved_report(report_path: Path):
    """Apply current conservative postprocessing to actual saved SAM outputs.

    The original forward report and original mask files remain unchanged. This
    function never loads torch, weights, cameras or robot hardware.
    """
    import copy
    import hashlib
    import numpy as np
    from PIL import Image
    from so101_lab.annotations import (PROCESSING_CONTRACT, SCHEMA_VERSION,
        mark_category_ambiguities, measure_color, sha256_file,
        source_frame_digest, validate_frame_annotations)
    report = json.loads(report_path.read_text())
    if report.get("status") != "PASS" or report.get("model_constructed") is not True or report.get("kind") != "real_SAM3_CUDA_forward_on_real_camera_snapshots":
        raise ValueError("Нужен успешный оригинальный настоящий SAM forward report")
    derived = copy.deepcopy(report)
    derived["postprocessing_derived_from"] = {"path": str(report_path.relative_to(ROOT)), "sha256": sha256_file(report_path)}
    derived["model_forward_reexecuted"] = False
    derived["postprocessing_contract"] = dict(PROCESSING_CONTRACT)
    provenance = derived["provenance"]
    provenance["processing_contract"] = dict(PROCESSING_CONTRACT)
    identity = {"checkpoint_sha256": provenance["checkpoint_sha256"], "prompts": provenance["prompts"],
                "threshold": provenance["confidence_threshold"], "source": provenance["source"],
                "processing_contract": PROCESSING_CONTRACT, "backend": "sam3"}
    provenance["configuration_hash"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    raw_records = []
    annotation_records = []
    diagnostics = {}
    for camera, image in derived["images"].items():
        path = ROOT/image["path"]
        if sha256_file(path) != image["sha256"]:
            raise ValueError("Input PNG SHA256 changed after SAM forward")
        with Image.open(path) as source:
            rgb = np.asarray(source.convert("RGB"))
        masks = []
        for obj in image["objects"]:
            mask_path = ROOT/obj["mask_path"]
            if sha256_file(mask_path) != obj["mask_sha256"]:
                raise ValueError("Saved SAM mask SHA256 changed")
            with Image.open(mask_path) as source:
                mask = np.asarray(source) > 0
            masks.append(mask)
            obj["color"] = measure_color(rgb, mask)
            obj["touches_image_border"] = bool(mask[0].any() or mask[-1].any() or mask[:,0].any() or mask[:,-1].any())
            obj["mask_encoding"] = "png_binary_0_255_lossless"
        mark_category_ambiguities(image["objects"], masks,
            threshold=PROCESSING_CONTRACT["cross_category_ambiguity_iou"])
        diagnostics[camera] = {
            "raw_sam_detections": len(image["objects"]),
            "category_ambiguous_detections": sum(o["category_ambiguous"] for o in image["objects"]),
            "unambiguous_category_detections": sum(not o["category_ambiguous"] for o in image["objects"]),
            "low_light_color_invalid_detections": sum(not o["color"]["valid"] for o in image["objects"]),
            "limitations": "raw category queries can overlap; color uncalibrated; occlusion/XYZ unknown"}
        metadata = (derived.get("capture_metadata") or {}).get(camera)
        if metadata is not None:
            if metadata.get("sha256") != image["sha256"]:
                raise ValueError("Capture sidecar SHA256 differs")
            raw = {"frame_id": camera+"_acceptance_snapshot", "timestamp_ns": metadata["timestamp_ns"],
                   "observation_timestamp_ns": metadata["timestamp_ns"],
                   "purpose": "real_camera_snapshot_acceptance", "training_eligible": False,
                   "cameras": {camera: {"path": image["path"], "timestamp_ns": metadata["timestamp_ns"],
                                "sha256": image["sha256"], "valid": True}}}
            annotation = {"schema_version": SCHEMA_VERSION, "frame_id": raw["frame_id"],
                          "timestamp_ns": raw["timestamp_ns"], "observation_timestamp_ns": raw["timestamp_ns"],
                          "source_frame_hash": source_frame_digest(raw), "provenance": provenance,
                          "cameras": {camera: {"timestamp_ns": metadata["timestamp_ns"], "raw_sha256": image["sha256"],
                                      "width": image["width"], "height": image["height"],
                                      "valid": True, "objects": image["objects"]}}}
            validate_frame_annotations(annotation, raw, ROOT, require_cameras=(camera,))
            raw_records.append(raw)
            annotation_records.append(annotation)
            diagnostics[camera]["mask_and_lineage_validation"] = "PASS"
    derived["postprocessing_validation"] = "PASS"
    derived["diagnostics"] = diagnostics
    derived["segmentation_quality"] = "NOT_FULLY_VERIFIED: wrist cube/box ambiguity and dark-color uncertainty recorded; operator inspection required"
    folder = report_path.parent
    atomic_json(folder/"report_postprocessing.json", derived)
    (folder/"snapshot_raw_identity.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in raw_records))
    (folder/"snapshot_annotations.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in annotation_records))
    atomic_json(folder/"snapshot_alignment_validation.json", {"status": "PASS", "training_eligible": False,
        "scope": "real SAM outputs/mask files vs individually timestamped real snapshots; not synchronized demonstration",
        "postprocessing_forward_reexecuted": False, "images": diagnostics})
    return diagnostics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Явно разрешить реальный SAM3 GPU forward")
    parser.add_argument("--postprocess-only", action="store_true", help="Только проверить/дополнить уже сохранённый настоящий report без модели")
    parser.add_argument("--images", type=Path, default=ROOT/"validation/acceptance-20261106/cameras")
    parser.add_argument("--output", type=Path, default=ROOT/"validation/acceptance-20261106/sam3")
    parser.add_argument("--minimum-available-gb", type=float, default=10.)
    parser.add_argument("--cuda-memory-fraction", type=float, default=.65)
    parser.add_argument("--threshold", type=float, default=.5)
    parser.add_argument("--cube-prompt", default="cube")
    parser.add_argument("--box-prompt", default="box")
    args = parser.parse_args()
    if not args.execute and not args.postprocess_only:
        parser.error("Нужен явный --execute; никакие модели не загружены")
    if not .1 <= args.cuda_memory_fraction <= .7 or args.minimum_available_gb < 8:
        parser.error("CUDA fraction допустима0.1..0.7, MemAvailable минимум8GiB")
    inputs = args.images.resolve()
    output = args.output.resolve()
    if not inputs.is_relative_to(ROOT) or not output.is_relative_to(ROOT):
        parser.error("Input/output должны находиться в самостоятельном проекте")
    if args.postprocess_only:
        diagnostics = postprocess_saved_report(output/"report.json")
        print(json.dumps({"status": "PASS", "model_loaded": False, "diagnostics": diagnostics}, ensure_ascii=False))
        return 0
    files = {camera: inputs/(camera+".png") for camera in ("top", "wrist")}
    if not all(path.is_file() for path in files.values()):
        parser.error("Нет обоих ранее записанных top.png/wrist.png; камеры скрипт не открывает")
    output.mkdir(parents=True, exist_ok=True)
    report_path = output/"report.json"
    report = {"status": "running", "kind": "real_SAM3_CUDA_forward_on_real_camera_snapshots",
              "hardware_opened_by_this_script": False, "model_constructed": False,
              "images": {}, "cuda_memory_fraction_limit": args.cuda_memory_fraction,
              "minimum_available_gb": args.minimum_available_gb,
              "segmentation_quality": "NOT_TESTED; operator must inspect actual outputs",
              "started_at_unix_s": time.time()}
    atomic_json(report_path, report)
    from so101_lab.resources import ResourceLease, assert_compute_available, snapshot
    from so101_lab.annotations import SAM3Segmenter, CausalAssociator, measure_color, sha256_file
    from so101_lab.geometry import unknown_geometry
    memory_samples = []
    finished = threading.Event()
    def sample_memory():
        while not finished.wait(.05):
            memory_samples.append(available_bytes())
    monitor = threading.Thread(target=sample_memory, name="sam3-acceptance-memory", daemon=True)
    torch = None
    model = None
    try:
        report["resource_preflight"] = assert_compute_available("cuda", minimum_available_gb=args.minimum_available_gb)
        report["capture_metadata"] = json.loads((inputs/"capture.json").read_text()) if (inputs/"capture.json").is_file() else None
        report["capture_timestamp_status"] = "recorded_by_capture_worker; see metadata" if report["capture_metadata"] else "UNKNOWN; image file hashes only, no invented capture timestamps"
        memory_samples.append(available_bytes())
        monitor.start()
        with ResourceLease("gpu_compute"):
            import numpy as np
            from PIL import Image
            import torch as torch_module
            torch = torch_module
            torch.set_num_threads(2)
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA недоступна; CPU fallback не выполняется")
            torch.cuda.set_per_process_memory_fraction(args.cuda_memory_fraction)
            torch.cuda.reset_peak_memory_stats()
            report["torch_version"] = torch.__version__
            report["cuda_device"] = torch.cuda.get_device_name(0)
            report["phase"] = "loading_model"
            atomic_json(report_path, report)
            started = time.monotonic()
            model = SAM3Segmenter({"device": "cuda", "minimum_available_gb": args.minimum_available_gb,
                "confidence_threshold": args.threshold,
                "prompts": {"cube": args.cube_prompt, "box": args.box_prompt}}).load()
            torch.cuda.synchronize()
            report["model_constructed"] = True
            report["load_seconds"] = time.monotonic()-started
            report["provenance"] = model.provenance
            atomic_json(report_path, report)
            for camera, path in files.items():
                if available_bytes() < 1024**3:
                    raise RuntimeError("MemAvailable <1GiB: следующий SAM forward не запущен")
                with Image.open(path) as image:
                    rgb = np.asarray(image.convert("RGB"))
                report["phase"] = "segmenting_"+camera
                atomic_json(report_path, report)
                torch.cuda.synchronize()
                started = time.monotonic()
                detections = model.segment(rgb)
                torch.cuda.synchronize()
                elapsed = time.monotonic()-started
                objects = []
                mask_dir = output/"masks"/camera
                mask_dir.mkdir(parents=True, exist_ok=True)
                for index, detection in enumerate(detections):
                    mask = np.asarray(detection["mask"], dtype=bool)
                    if mask.shape != rgb.shape[:2] or not mask.any():
                        raise RuntimeError("SAM returned wrong/empty mask")
                    mask_path = mask_dir/f"{index:03d}.png"
                    Image.fromarray(mask.astype(np.uint8)*255, mode="L").save(mask_path)
                    ys, xs = np.nonzero(mask)
                    objects.append({"category": detection["category"], "score": detection["score"],
                                    "bbox_xyxy": detection["bbox_xyxy"], "center_xy": [float(xs.mean()), float(ys.mean())],
                                    "area_px": len(xs), "mask_path": str(mask_path.relative_to(ROOT)),
                                    "mask_sha256": sha256_file(mask_path), "color": measure_color(rgb, mask),
                                    "visibility": "visible_mask", "occlusion": {"valid": False, "value": None},
                                    "geometry": unknown_geometry()})
                # Only one snapshot per camera: no claim of video tracking quality.
                objects, lost = CausalAssociator(camera).update(objects, time.monotonic_ns(), sha256_file(path))
                report["images"][camera] = {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path),
                    "width": int(rgb.shape[1]), "height": int(rgb.shape[0]), "forward_seconds": elapsed,
                    "detected_objects": len(objects), "objects": objects, "lost_track_ids": lost,
                    "zero_detections_meaning": "not_detected_not_proven_absent",
                    "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                    "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(), "memory_available_bytes": available_bytes()}
                atomic_json(report_path, report)
                torch.cuda.empty_cache()
            report.update(status="PASS", phase="complete", completed_images=len(report["images"]))
    except Exception as error:
        report.update(status="FAIL", phase="failed", error=str(error), traceback=traceback.format_exc())
        if report.get("model_constructed") is False and "занят" in str(error):
            report["status"] = "BLOCKED"
    finally:
        finished.set()
        if monitor.is_alive():
            monitor.join(timeout=1.)
        report["unified_memory_available_min_bytes"] = min(memory_samples) if memory_samples else available_bytes()
        report["unified_memory_used_max_bytes"] = snapshot()["memory_total_bytes"]-report["unified_memory_available_min_bytes"]
        report["unified_memory_peak_increment_bytes"] = max(0, memory_samples[0]-report["unified_memory_available_min_bytes"]) if memory_samples else 0
        report["finished_at_unix_s"] = time.time()
        if torch is not None and torch.cuda.is_initialized():
            report["cuda_peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
            report["cuda_peak_reserved_bytes"] = torch.cuda.max_memory_reserved()
        atomic_json(report_path, report)
        print(json.dumps({"status": report["status"], "report": str(report_path.relative_to(ROOT)),
                          "completed_images": report.get("completed_images", 0),
                          "error": report.get("error"), "unified_memory_available_min_bytes": report["unified_memory_available_min_bytes"]}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
