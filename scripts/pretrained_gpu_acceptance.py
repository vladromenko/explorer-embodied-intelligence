#!/usr/bin/env python3
"""Explicit, guarded full pretrained SmolVLA CUDA acceptance, no hardware I/O.

This is a tensor-contract engineering test, not demonstration collection:
real but individually captured images + real read-only follower positions are
combined without claiming temporal synchronization. The technical two-step
target repeats that measured pose; it was NEVER sent to motors. The resulting
checkpoint is marked technical_test=True and cannot drive hardware.

Train:  .venv/bin/python scripts/pretrained_gpu_acceptance.py --execute
Reload in NEW process: same command with --reload --execute
Do not run these concurrently with SAM or another model on Jetson unified RAM.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import resource
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DEFAULT_BASE = ROOT / "validation/acceptance-20261106"


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name+".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def available_bytes() -> int:
    line = next(line for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))
    return int(line.split()[1])*1024


def rss_bytes() -> int:
    return int(Path("/proc/self/statm").read_text().split()[1])*os.sysconf("SC_PAGE_SIZE")


def tensor_digest(tensor) -> str:
    return hashlib.sha256(tensor.detach().cpu().contiguous().numpy().tobytes()).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text())


def _snapshot_inputs(args, output):
    import numpy as np
    import torch
    from PIL import Image
    from so101_lab.features import FeatureContract, JOINTS, object_state, sha256_file
    from so101_lab.annotations import annotation_provenance_identity, validate_annotation_provenance

    captures = _json(args.images / "capture.json")
    positions_report = _json(args.positions)
    position = positions_report["buses"]["follower"]["normalized_positions"]
    state = [float(position[joint]) for joint in JOINTS]
    if not all(np.isfinite(value) for value in state):
        raise ValueError("Read-only follower positions contain missing/nonfinite joints")
    sam_report = _json(args.sam / "report.json")
    if sam_report.get("status") != "PASS":
        raise ValueError("Нужен реальный успешный SAM3 GPU report; подмена masks/boxes не допускается")
    records = [json.loads(line) for line in (args.sam / "snapshot_annotations.jsonl").read_text().splitlines() if line.strip()]
    if not records:
        raise ValueError("Нет настоящих SAM snapshot annotations")
    provenance = records[0]["provenance"]
    validate_annotation_provenance(provenance)
    # Saved masks can receive explicit newer color/ambiguity postprocessing
    # without a second heavy SAM forward. Core segmentation identity must match.
    for key in ("backend", "checkpoint_sha256", "prompts", "confidence_threshold", "preprocessing", "source"):
        if provenance.get(key) != sam_report["provenance"].get(key):
            raise ValueError("Snapshot annotation differs from actual SAM forward identity: "+key)
    cameras = {}
    annotations = {}
    images = {}
    for camera in ("top", "wrist"):
        path = args.images / (camera+".png")
        image_hash = sha256_file(path)
        capture = captures[camera]
        if capture["sha256"] != image_hash:
            raise ValueError("Snapshot PNG differs from read-only capture identity: "+camera)
        matches = [record for record in records if camera in record.get("cameras", {})]
        if len(matches) != 1:
            raise ValueError("Нужна одна настоящая SAM annotation на каждый snapshot")
        ann = matches[0]
        validate_annotation_provenance(ann["provenance"], expected=provenance)
        item = ann["cameras"][camera]
        if item["raw_sha256"] != image_hash or item["timestamp_ns"] != capture["timestamp_ns"]:
            raise ValueError("Real SAM annotation snapshot timestamp/hash mismatch")
        cameras[camera] = {"timestamp_ns": capture["timestamp_ns"], "sha256": image_hash, "valid": True}
        annotations[camera] = item
        with Image.open(path) as image:
            images[camera] = np.array(image.convert("RGB"), copy=True)
    # This identity is explicitly a logical engineering composite. It does NOT
    # replace original capture timestamps or pass the raw synchronized loader.
    logical_time = max(item["timestamp_ns"] for item in cameras.values())
    composite_id = "TECHNICAL_UNSYNCHRONIZED_COMPOSITE_NOT_A_RECORDED_FRAME"
    composite = {"frame_id": composite_id, "timestamp_ns": logical_time, "observation.state": state, "cameras": cameras}
    annotation = {"frame_id": composite_id, "timestamp_ns": logical_time, "cameras": annotations, "provenance": provenance}
    contract = FeatureContract(mode="sam_objects", camera_mode="two_camera")
    numeric = object_state(composite, annotation, contract)
    if len(numeric) != 32:
        raise ValueError("SAM adapter must produce 32-dimensional state")
    batch = {"observation.state": torch.tensor([numeric], dtype=torch.float32),
             "task": ["Pick up the cube and place it into the box."],
             "action": torch.tensor([[state, state]], dtype=torch.float32),
             "action_is_pad": torch.tensor([[False, False]])}
    for camera, image in images.items():
        batch[f"observation.images.{camera}"] = torch.from_numpy(image).permute(2, 0, 1).unsqueeze(0).float()/255
    stats = {"observation.state": {"mean": torch.tensor(state+[0.]*26), "std": torch.ones(32)},
             "action": {"mean": torch.tensor(state), "std": torch.ones(6)}}
    input_identity = {"schema": "so101.acceptance.pretrained_tensor_input.v1", "technical_test": True,
        "raw_dataset": False, "hardware_opened": False, "transmitted_training_actions": False,
        "successful_demonstration": False, "training_eligible_recording": False,
        "input_semantics": "unsynchronized composite of genuine camera snapshots and independently measured follower pose, only for model tensor-contract test",
        "target_semantics": "TECHNICAL repeat measured follower pose twice; NOT actually transmitted motor commands",
        "normalization_semantics": "technical reference pose mean with unit std; object numeric identity; not deployment dataset statistics",
        "logical_composite_timestamp_ns": logical_time,
        "logical_timestamp_semantics": "max constituent snapshot time, not a measured synchronized observation timestamp",
        "capture_metadata": captures, "follower_positions": state,
        "read_only_position_report_sha256": sha256_file(args.positions),
        "source_files_sha256": {str(path.relative_to(ROOT)): sha256_file(path) for path in (args.positions, args.images / "capture.json", args.images / "top.png", args.images / "wrist.png", args.sam / "report.json", args.sam / "snapshot_annotations.jsonl")},
        "sam_provenance": provenance, "sam_provenance_identity": annotation_provenance_identity(provenance),
        "state_32": numeric, "feature_contract": contract.to_dict(),
        "camera_snapshot_skew_ms": abs(cameras["top"]["timestamp_ns"]-cameras["wrist"]["timestamp_ns"])/1e6}
    atomic_json(output / "input_identity.json", input_identity)
    return batch, stats, contract, input_identity


def _predict(policy, pre, post, batch, noise):
    import torch
    inference_batch = {key: value for key, value in batch.items() if key not in ("action", "action_is_pad")}
    prepared = pre(inference_batch)
    policy.eval()
    policy.reset()
    with torch.inference_mode():
        normalized = policy.select_action(prepared, noise=noise)
        actions = post(normalized).detach().cpu()
    if tuple(actions.shape) != (1, 6) or not torch.isfinite(actions).all() or not torch.isfinite(normalized).all():
        raise RuntimeError("Real pretrained inference returned invalid/nonfinite action")
    return normalized.detach().cpu(), actions


def train_once(args, report, report_path):
    import torch
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
    from lerobot.policies.smolvla.processor_smolvla import make_smolvla_pre_post_processors
    from so101_lab.training import _config, _copy_backbone_assets, _save_checkpoint, cached_snapshot, _code_revision
    from so101_lab.features import sha256_file
    from so101_lab.telemetry import DurableMLflow
    from so101_lab.resources import snapshot

    output = args.output
    if (output / "checkpoints/step_00000001/checkpoint.json").exists():
        raise FileExistsError("Acceptance checkpoint already exists; use --reload or choose a new --output")
    output.mkdir(parents=True, exist_ok=True)
    batch, stats, contract, identity = _snapshot_inputs(args, output)
    base = cached_snapshot("lerobot/smolvla_base")
    backbone = cached_snapshot("HuggingFaceTB/SmolVLM2-500M-Video-Instruct", require_weights=False)
    assets = output / "backbone_assets"
    _copy_backbone_assets(backbone, assets)
    options = {"policy_type": "smolvla", "device": "cuda", "tiny_model": False,
               "technical_tensor_smoke": True, "mode": "sam_objects", "camera_mode": "two_camera",
               "image_size": 128, "chunk_size": 2, "denoising_steps": 2, "batch_size": 1,
               "steps": 1, "use_amp": False, "lr": 1e-5, "optimizer": "adamw", "seed": 19,
               "freeze_vision_encoder": True, "train_expert_only": True, "train_state_proj": True}
    cfg = _config(contract, assets, options, tiny=False, base=base)
    report.update(phase="loading_full_cached_pretrained", base_path=str(base), base_revision=base.name,
                  base_weights_sha256=sha256_file(base / "model.safetensors"), backbone_revision=backbone.name,
                  input_identity=str((output / "input_identity.json").relative_to(ROOT)), options=options)
    atomic_json(report_path, report)
    started = time.monotonic()
    policy = SmolVLAPolicy.from_pretrained(base, config=cfg, local_files_only=True, strict=True)
    policy.to("cuda")
    torch.cuda.synchronize()
    report.update(model_constructed=True, model_is_tiny=False, pretrained_loading_strict=True,
                  load_seconds=time.monotonic()-started, total_parameters=sum(parameter.numel() for parameter in policy.parameters()),
                  trainable_parameters=sum(parameter.numel() for parameter in policy.parameters() if parameter.requires_grad))
    pre, post = make_smolvla_pre_post_processors(policy.config, stats)
    initial = policy.model.state_proj.weight.detach().cpu().clone()
    trainable = [parameter for parameter in policy.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=1e-5, betas=(.9, .95), weight_decay=1e-4, foreach=False)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: 1.)
    lineage = {"schema_version": "so101.training.lineage.v1", "technical_test": True,
        "technical_test_kind": "full_pretrained_CUDA_tensor_update_not_demonstration_training",
        "policy_type": "smolvla", "dataset_source": "technical_camera_snapshot_pose_composite_NOT_recorded_actions",
        "dataset_path": str(output / "input_identity.json"), "dataset_sha256": sha256_file(output / "input_identity.json"),
        "dataset_hashes": identity["source_files_sha256"], "calibration": {"geometry_valid": False, "read_only_joint_calibration_match": True, "physical_recording_validated": False},
        "feature_contract": contract.to_dict(), "feature_sha256": contract.digest, "annotation_provenance": identity["sam_provenance"],
        "base_model": str(base), "base_revision": base.name, "base_weights_sha256": report["base_weights_sha256"],
        "backbone_revision": backbone.name, "code_commit": _code_revision(),
        "code_files_sha256": {path.name: sha256_file(path) for path in sorted((ROOT / "src/so101_lab").glob("*.py"))},
        "acceptance_script_sha256": sha256_file(Path(__file__)), "options": options,
        "versions": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "lerobot", "transformers")},
        "split": {"train": [], "validation": [], "test": [], "reason": "single technical tensor update; no demonstration dataset or policy quality estimate"},
        "input_provenance": identity, "hardware_actions_sent": 0}
    atomic_json(output / "lineage.json", lineage)
    telemetry = DurableMLflow(ROOT, run_name="acceptance-full-pretrained-smolvla-cuda-20261106", params={
        "policy": "smolvla", "technical_test": True, "model_is_tiny": False, "pretrained_revision": base.name,
        "pretrained_sha256": report["base_weights_sha256"], "device": "cuda", "batch_size": 1, "chunk_size": 2,
        "image_size": 128, "state_dim": 32, "use_amp": False, "steps": 1, "target_semantics": identity["target_semantics"],
        "dataset_sha256": lineage["dataset_sha256"], "code_commit": lineage["code_commit"], "feature_sha256": contract.digest,
        "sam_configuration_hash": identity["sam_provenance"]["configuration_hash"]})
    telemetry_status = "FAILED"
    try:
        report["phase"] = "real_forward_backward_optimizer_update"
        atomic_json(report_path, report)
        policy.train()
        prepared = pre(batch)
        started = time.monotonic()
        optimizer.zero_grad(set_to_none=True)
        loss, details = policy(prepared)
        if not torch.isfinite(loss):
            raise FloatingPointError("Real pretrained CUDA loss is nonfinite")
        loss.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_(trainable, 10.)
        if not torch.isfinite(gradient_norm):
            raise FloatingPointError("Real pretrained CUDA gradient is nonfinite")
        optimizer.step()
        scheduler.step()
        torch.cuda.synchronize()
        elapsed = time.monotonic()-started
        delta = float((policy.model.state_proj.weight.detach().cpu()-initial).abs().max())
        if delta <= 0:
            raise RuntimeError("No real state projection parameter update occurred")
        metrics = {"train/loss": float(loss.detach().cpu()), "train/lr": scheduler.get_last_lr()[0],
            "train/grad_norm": float(gradient_norm.detach().cpu()), "train/step_seconds": elapsed,
            "train/samples_per_second": 1/elapsed, "train/state_projection_update_max_abs": delta,
            "resources/cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(), "resources/cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            "resources/process_rss_bytes": rss_bytes(), "resources/memory_available_bytes": available_bytes()}
        telemetry.log_metrics(metrics, step=1)
        report.update(metrics=metrics, real_forward_backward_update=True, phase="post_update_fixed_noise_inference")
        atomic_json(report_path, report)
        optimizer.zero_grad(set_to_none=True)
        del loss, prepared
        torch.cuda.empty_cache()
        noise_cpu = torch.randn((1, 2, policy.config.max_action_dim), generator=torch.Generator().manual_seed(123), dtype=torch.float32)
        normalized, actions = _predict(policy, pre, post, batch, noise_cpu.to("cuda"))
        expected = {"normalized_actions": normalized, "actions": actions, "normalized_sha256": tensor_digest(normalized), "actions_sha256": tensor_digest(actions)}
        torch.save({"batch": batch, "fixed_noise": noise_cpu, "expected": expected, "technical_test": True}, output / "smoke_input_and_expected.pt")
        atomic_json(output / "expected_inference.json", {"normalized_actions": normalized.tolist(), "actions": actions.tolist(),
            "normalized_sha256": expected["normalized_sha256"], "actions_sha256": expected["actions_sha256"],
            "fixed_noise_sha256": tensor_digest(noise_cpu), "technical_test": True, "physical_commands_sent": 0})
        report["phase"] = "saving_full_checkpoint"
        atomic_json(report_path, report)
        checkpoint = _save_checkpoint(policy, pre, post, optimizer, scheduler, output, 1, contract, lineage, assets, random.Random(19))
        report.update(checkpoint=str(checkpoint), checkpoint_complete=True, phase="checkpoint_saved", inference_actions=actions.tolist(),
                      normalized_actions_sha256=expected["normalized_sha256"], inference_actions_sha256=expected["actions_sha256"])
        atomic_json(report_path, report)
        for artifact in (output / "lineage.json", output / "input_identity.json", output / "expected_inference.json", checkpoint / "checkpoint.json"):
            telemetry.log_artifact(artifact)
        report["mlflow_spool"] = str(telemetry.path)
        telemetry_status = "FINISHED"
        report["resource_after_update_and_checkpoint"] = snapshot()
        report.update(status="PASS", phase="complete", pretrained_GPU_update_verified=True, reload_new_process="NOT_YET_RUN")
    finally:
        telemetry.close(telemetry_status)
        report["mlflow_run_id"] = telemetry.run_id
        atomic_json(report_path, report)
    return report


def reload_once(args, report, report_path):
    import torch
    from so101_lab.inference import load_checkpoint
    from so101_lab.telemetry import DurableMLflow
    from so101_lab.resources import snapshot

    latest = _json(args.output / "latest.json")
    checkpoint = Path(latest["checkpoint"])
    saved = torch.load(args.output / "smoke_input_and_expected.pt", map_location="cpu", weights_only=False)
    if saved.get("technical_test") is not True:
        raise ValueError("Reload requires explicitly technical trusted smoke input")
    report.update(phase="strict_reload_full_saved_checkpoint", checkpoint=str(checkpoint), mode="reload_new_process",
                  input_path=str(args.output / "smoke_input_and_expected.pt"))
    atomic_json(report_path, report)
    started = time.monotonic()
    loaded = load_checkpoint(checkpoint, device="cuda", allow_technical=True, resource_check=False)
    torch.cuda.synchronize()
    normalized, actions = _predict(loaded.policy, loaded.preprocessor, loaded.postprocessor, saved["batch"], saved["fixed_noise"].to("cuda"))
    expected = saved["expected"]
    normalized_difference = float((normalized-expected["normalized_actions"]).abs().max())
    action_difference = float((actions-expected["actions"]).abs().max())
    equal = torch.allclose(normalized, expected["normalized_actions"], rtol=1e-6, atol=1e-6) and torch.allclose(actions, expected["actions"], rtol=1e-6, atol=1e-6)
    if not equal:
        raise RuntimeError(f"Reload fixed-noise inference differs: normalized={normalized_difference}, action={action_difference}")
    telemetry = DurableMLflow(ROOT, run_name="acceptance-full-pretrained-smolvla-cuda-reload-20261106", params={
        "policy": "smolvla", "technical_test": True, "mode": "reload_new_process", "device": "cuda", "checkpoint": str(checkpoint)})
    telemetry.log_metrics({"reload/normalized_max_abs_difference": normalized_difference, "reload/action_max_abs_difference": action_difference,
        "reload/seconds": time.monotonic()-started, "resources/cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "resources/cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(), "resources/process_rss_bytes": rss_bytes()}, step=1)
    report.update(status="PASS", phase="complete", checkpoint_reloaded=True, saved_processors_reloaded=True,
                  model_is_tiny=False, full_pretrained_checkpoint=True, fixed_noise_actions_equal=True,
                  normalized_actions_bitwise_equal=tensor_digest(normalized)==expected["normalized_sha256"],
                  actions_bitwise_equal=tensor_digest(actions)==expected["actions_sha256"],
                  normalized_max_abs_difference=normalized_difference, action_max_abs_difference=action_difference,
                  normalized_actions_sha256=tensor_digest(normalized), actions_sha256=tensor_digest(actions),
                  finite_actions=True, actions=actions.tolist(), resource_after_reload=snapshot(),
                  checkpoint_technical_test=loaded.technical_test, physical_commands_sent=0)
    atomic_json(report_path, report)
    telemetry.log_artifact(report_path)
    telemetry.close("FINISHED")
    report["mlflow_run_id"] = telemetry.run_id
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reload", action="store_true", help="Load saved full checkpoint in this NEW process; no training")
    parser.add_argument("--images", type=Path, default=DEFAULT_BASE / "cameras")
    parser.add_argument("--positions", type=Path, default=DEFAULT_BASE / "hardware-readonly.json")
    parser.add_argument("--sam", type=Path, default=DEFAULT_BASE / "sam3")
    parser.add_argument("--output", type=Path, default=ROOT / "models/acceptance_pretrained_gpu_20261106")
    parser.add_argument("--reports", type=Path, default=DEFAULT_BASE / "pretrained")
    parser.add_argument("--minimum-available-gb", type=float, default=9.)
    parser.add_argument("--cuda-memory-fraction", type=float, default=.55)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Нужен явный --execute; никакие модели не загружены")
    if not .1 <= args.cuda_memory_fraction <= .55 or args.minimum_available_gb < 9:
        parser.error("CUDA fraction 0.1..0.55, MemAvailable минимум9GiB")
    for name in ("images", "positions", "sam", "output", "reports"):
        path = getattr(args, name).resolve()
        if not path.is_relative_to(ROOT):
            parser.error("Все input/output должны находиться в независимой лаборатории")
        setattr(args, name, path)
    args.reports.mkdir(parents=True, exist_ok=True)
    report_path = args.reports / ("reload_report.json" if args.reload else "report.json")
    report = {"status": "running", "phase": "preflight", "mode": "reload_new_process" if args.reload else "one_real_pretrained_CUDA_update",
        "kind": "genuine_cached_pretrained_SmolVLA_CUDA_acceptance", "model_is_tiny": False, "technical_test": True,
        "hardware_opened": False, "torque_writes": 0, "goal_position_writes": 0, "physical_commands_sent": 0,
        "recorded_demonstration_training": False, "successful_policy_claim": False,
        "cuda_allocator_fraction_limit": args.cuda_memory_fraction, "minimum_available_gb": args.minimum_available_gb,
        "started_unix_s": time.time(), "process_id": os.getpid()}
    atomic_json(report_path, report)
    from so101_lab.resources import ResourceLease, assert_compute_available, snapshot
    torch = None
    readings = []
    finished = threading.Event()
    def monitor():
        while not finished.wait(.05):
            readings.append({"available": available_bytes(), "rss": rss_bytes()})
    thread = threading.Thread(target=monitor, daemon=True, name="pretrained-acceptance-RAM")
    try:
        report["resource_preflight"] = assert_compute_available("cuda", minimum_available_gb=args.minimum_available_gb)
        readings.append({"available": available_bytes(), "rss": rss_bytes()})
        thread.start()
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        with ResourceLease("gpu_compute"):
            import torch as torch_module
            torch = torch_module
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA недоступна; CPU fallback не выполняется")
            torch.set_num_threads(2)
            random.seed(19)
            torch.manual_seed(19)
            torch.backends.cuda.matmul.allow_tf32 = False
            torch.backends.cudnn.allow_tf32 = False
            torch.backends.cudnn.deterministic = True
            torch.cuda.set_per_process_memory_fraction(args.cuda_memory_fraction)
            torch.cuda.reset_peak_memory_stats()
            report.update(torch_version=torch.__version__, cuda_device=torch.cuda.get_device_name(0))
            if args.reload:
                report = reload_once(args, report, report_path)
            else:
                report = train_once(args, report, report_path)
    except Exception as error:
        report.update(status="FAIL", phase="failed", error=str(error), traceback=traceback.format_exc())
    finally:
        finished.set()
        if thread.is_alive():
            thread.join(timeout=1.)
        report.update(finished_unix_s=time.time(), resource_after=snapshot(),
                      memory_available_min_bytes=min(sample["available"] for sample in readings) if readings else available_bytes(),
                      process_rss_peak_sampled_bytes=max(sample["rss"] for sample in readings) if readings else rss_bytes(),
                      process_max_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)
        if torch is not None and torch.cuda.is_initialized():
            report.update(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(), cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved())
        atomic_json(report_path, report)
        print(json.dumps({"status": report["status"], "mode": report["mode"], "report": str(report_path.relative_to(ROOT)),
                          "checkpoint": report.get("checkpoint"), "loss": report.get("metrics", {}).get("train/loss"),
                          "memory_available_min_bytes": report["memory_available_min_bytes"], "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
