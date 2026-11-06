#!/usr/bin/env python3
"""Production ACT CPU update + fresh-process checkpoint reload, no hardware/GPU.

An outer supervisor executes isolated train/reload children sequentially and
preserves >=2GiB host RAM. Actual camera/SAM snapshots and independently read
positions are only a technical tensor composite; repeated-pose targets were
never transmitted, and resulting checkpoints are technical_test=True.
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
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DEFAULT = ROOT / "validation/acceptance-20261106"


def atomic_json(path, value):
    temporary = path.with_name(path.name+".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def available_bytes():
    row = next(line for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))
    return int(row.split()[1])*1024


def rss_bytes(pid=None):
    return int(Path(f"/proc/{pid or 'self'}/statm").read_text().split()[1])*os.sysconf("SC_PAGE_SIZE")


def digest(tensor):
    return hashlib.sha256(tensor.detach().cpu().contiguous().numpy().tobytes()).hexdigest()


def predict(policy, pre, post, batch):
    import torch
    inputs = {key: value for key, value in batch.items() if key not in ("action", "action_is_pad")}
    prepared = pre(inputs)
    policy.eval()
    policy.reset()
    with torch.inference_mode():
        normalized = policy.select_action(prepared)
        action = post(normalized).detach().cpu()
    if action.shape != (1, 6) or not torch.isfinite(action).all() or not torch.isfinite(normalized).all():
        raise RuntimeError("ACT inference returned invalid/nonfinite action")
    return normalized.detach().cpu(), action


def train_worker(args, report, report_path):
    import numpy as np
    import torch
    from PIL import Image
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.act.processor_act import make_act_pre_post_processors
    from so101_lab.policies import image_tensor, state_projection
    from so101_lab.training import _config, _save_checkpoint, _code_revision
    from so101_lab.features import sha256_file
    from so101_lab.telemetry import DurableMLflow
    from so101_lab.resources import snapshot
    from pretrained_gpu_acceptance import _snapshot_inputs

    output = args.output
    if (output / "checkpoints/step_00000001/checkpoint.json").exists():
        raise FileExistsError("ACT acceptance checkpoint exists; use --reload or new --output")
    output.mkdir(parents=True, exist_ok=True)
    batch, stats, contract, identity = _snapshot_inputs(args, output)
    for camera in ("top", "wrist"):
        with Image.open(args.images / (camera+".png")) as image:
            pixels = np.array(image.convert("RGB"), copy=True)
        batch[f"observation.images.{camera}"] = image_tensor(pixels, size=128).unsqueeze(0)
    identity.update(policy_type="act", initialization="from_scratch_including_ResNet18_no_download", image_preprocessing="shared_policies.image_tensor_PIL_ImageOps.pad_BILINEAR_RGB_letterbox_128")
    atomic_json(output / "input_identity.json", identity)
    options = {"policy_type": "act", "device": "cpu", "tiny_model": False, "technical_tensor_smoke": True,
               "mode": "sam_objects", "camera_mode": "two_camera", "image_size": 128, "chunk_size": 2,
               "batch_size": 1, "steps": 1, "use_amp": False, "lr": 1e-4, "optimizer": "adamw",
               "act_lr_backbone": 1e-5, "kl_weight": 10., "weight_decay": 1e-4, "seed": 31}
    cfg = _config(contract, None, options, tiny=False)
    architecture = {key: getattr(cfg, key) for key in ("dim_model", "n_heads", "dim_feedforward", "n_encoder_layers", "n_decoder_layers", "n_vae_encoder_layers", "latent_dim", "dropout", "vision_backbone", "pretrained_backbone_weights", "use_vae")}
    if architecture["dim_model"] != 512 or architecture["n_encoder_layers"] != 4 or architecture["n_vae_encoder_layers"] != 4:
        raise RuntimeError("ACT production architecture unexpectedly reduced")
    report.update(phase="initializing_production_ACT_from_scratch", architecture=architecture, options=options)
    atomic_json(report_path, report)
    started = time.monotonic()
    policy = ACTPolicy(cfg).to("cpu")
    pre, post = make_act_pre_post_processors(policy.config, stats)
    report.update(model_constructed=True, model_is_tiny=False, initialization_seconds=time.monotonic()-started,
        total_parameters=sum(parameter.numel() for parameter in policy.parameters()),
        trainable_parameters=sum(parameter.numel() for parameter in policy.parameters() if parameter.requires_grad))
    initial = state_projection(policy).weight.detach().clone()
    trainable = [parameter for parameter in policy.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(policy.get_optim_params(), lr=options["lr"], betas=(.9, .95), weight_decay=1e-4, foreach=False)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: 1.)
    lineage = {"schema_version": "so101.training.lineage.v1", "technical_test": True,
        "technical_test_kind": "production_ACT_CPU_tensor_update_not_demonstration_training",
        "policy_type": "act", "dataset_source": "technical_camera_snapshot_pose_composite_NOT_recorded_actions",
        "dataset_path": str(output / "input_identity.json"), "dataset_sha256": sha256_file(output / "input_identity.json"),
        "dataset_hashes": identity["source_files_sha256"], "calibration": {"geometry_valid": False, "read_only_joint_calibration_match": True, "physical_recording_validated": False},
        "feature_contract": contract.to_dict(), "feature_sha256": contract.digest, "annotation_provenance": identity["sam_provenance"],
        "base_model": "ACT/ResNet18 production config from scratch; offline no pretrained weights", "base_revision": None,
        "code_commit": _code_revision(), "code_files_sha256": {path.name: sha256_file(path) for path in sorted((ROOT / "src/so101_lab").glob("*.py"))},
        "acceptance_script_sha256": sha256_file(Path(__file__)), "options": options, "architecture": architecture,
        "versions": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "lerobot", "transformers")},
        "split": {"train": [], "validation": [], "test": [], "reason": "one technical tensor update, no demonstration dataset or success estimate"},
        "input_provenance": identity, "hardware_actions_sent": 0}
    atomic_json(output / "lineage.json", lineage)
    telemetry = DurableMLflow(ROOT, run_name="acceptance-production-ACT-CPU-20261106", params={
        "policy": "act", "technical_test": True, "model_is_tiny": False, "initialization": "from_scratch",
        "device": "cpu", "batch_size": 1, "chunk_size": 2, "image_size": 128, "state_dim": 32,
        "steps": 1, "use_amp": False, "optimizer": "adamw", "architecture": json.dumps(architecture),
        "target_semantics": identity["target_semantics"], "dataset_sha256": lineage["dataset_sha256"],
        "code_commit": lineage["code_commit"], "feature_sha256": contract.digest})
    telemetry_status = "FAILED"
    try:
        report["phase"] = "real_CPU_forward_backward_optimizer_update"
        atomic_json(report_path, report)
        policy.train()
        prepared = pre(batch)
        started = time.monotonic()
        optimizer.zero_grad(set_to_none=True)
        loss, details = policy(prepared)
        if not torch.isfinite(loss):
            raise FloatingPointError("ACT CPU loss is nonfinite")
        loss.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_(trainable, 10.)
        if not torch.isfinite(gradient_norm):
            raise FloatingPointError("ACT CPU gradient is nonfinite")
        optimizer.step()
        scheduler.step()
        elapsed = time.monotonic()-started
        change = float((state_projection(policy).weight.detach()-initial).abs().max())
        if change <= 0:
            raise RuntimeError("ACT state projection was not updated")
        metrics = {"train/loss": float(loss.detach()), "train/l1_loss": float(details["l1_loss"]),
            "train/kld_loss": float(details.get("kld_loss", 0.)), "train/grad_norm": float(gradient_norm),
            "train/lr": scheduler.get_last_lr()[0], "train/state_projection_update_max_abs": change,
            "train/step_seconds": elapsed, "train/samples_per_second": 1/elapsed,
            "resources/process_rss_bytes": rss_bytes(), "resources/memory_available_bytes": available_bytes()}
        telemetry.log_metrics(metrics, step=1)
        report.update(metrics=metrics, real_forward_backward_update=True, phase="post_update_ACT_inference")
        atomic_json(report_path, report)
        optimizer.zero_grad(set_to_none=True)
        del loss, prepared
        normalized, action = predict(policy, pre, post, batch)
        expected = {"normalized_actions": normalized, "actions": action,
                    "normalized_sha256": digest(normalized), "actions_sha256": digest(action)}
        torch.save({"batch": batch, "expected": expected, "technical_test": True}, output / "smoke_input_and_expected.pt")
        atomic_json(output / "expected_inference.json", {"normalized_actions": normalized.tolist(), "actions": action.tolist(),
            "normalized_sha256": expected["normalized_sha256"], "actions_sha256": expected["actions_sha256"],
            "technical_test": True, "physical_commands_sent": 0})
        report["phase"] = "saving_full_ACT_checkpoint"
        atomic_json(report_path, report)
        checkpoint = _save_checkpoint(policy, pre, post, optimizer, scheduler, output, 1, contract, lineage, None, random.Random(31))
        report.update(status="PASS", phase="complete", checkpoint=str(checkpoint), checkpoint_complete=True,
            production_ACT_config_verified=True, CPU_update_verified=True, inference_actions=action.tolist(),
            normalized_actions_sha256=expected["normalized_sha256"], inference_actions_sha256=expected["actions_sha256"],
            resource_after_update_and_checkpoint=snapshot(), mlflow_spool=str(telemetry.path))
        atomic_json(report_path, report)
        for artifact in (output / "lineage.json", output / "input_identity.json", output / "expected_inference.json", checkpoint / "checkpoint.json"):
            telemetry.log_artifact(artifact)
        telemetry_status = "FINISHED"
    finally:
        telemetry.close(telemetry_status)
        report["mlflow_run_id"] = telemetry.run_id
        atomic_json(report_path, report)


def reload_worker(args, report, report_path):
    import torch
    from so101_lab.inference import load_checkpoint
    from so101_lab.telemetry import DurableMLflow
    from so101_lab.resources import snapshot
    latest = json.loads((args.output / "latest.json").read_text())
    checkpoint = Path(latest["checkpoint"])
    saved = torch.load(args.output / "smoke_input_and_expected.pt", map_location="cpu", weights_only=False)
    if saved.get("technical_test") is not True:
        raise ValueError("Expected explicitly technical trusted input")
    report.update(phase="strict_reload_full_ACT_checkpoint", checkpoint=str(checkpoint), mode="new_process_reload")
    atomic_json(report_path, report)
    started = time.monotonic()
    loaded = load_checkpoint(checkpoint, device="cpu", allow_technical=True, resource_check=False)
    if loaded.policy.name != "act" or loaded.policy.config.dim_model != 512:
        raise RuntimeError("Reloaded policy is not full ACT production architecture")
    normalized, action = predict(loaded.policy, loaded.preprocessor, loaded.postprocessor, saved["batch"])
    expected = saved["expected"]
    normalized_difference = float((normalized-expected["normalized_actions"]).abs().max())
    action_difference = float((action-expected["actions"]).abs().max())
    if not torch.allclose(normalized, expected["normalized_actions"], rtol=1e-6, atol=1e-6) or not torch.allclose(action, expected["actions"], rtol=1e-6, atol=1e-6):
        raise RuntimeError(f"ACT reload actions differ: normalized={normalized_difference}, action={action_difference}")
    telemetry = DurableMLflow(ROOT, run_name="acceptance-production-ACT-CPU-reload-20261106", params={
        "policy": "act", "technical_test": True, "model_is_tiny": False, "mode": "new_process_reload", "device": "cpu", "checkpoint": str(checkpoint)})
    telemetry.log_metrics({"reload/normalized_max_abs_difference": normalized_difference, "reload/action_max_abs_difference": action_difference,
        "reload/seconds": time.monotonic()-started, "resources/process_rss_bytes": rss_bytes()}, step=1)
    report.update(status="PASS", phase="complete", checkpoint_reloaded=True, saved_processors_reloaded=True,
        full_production_ACT=True, model_is_tiny=False, finite_actions=True, actions_equal=True,
        normalized_max_abs_difference=normalized_difference, action_max_abs_difference=action_difference,
        normalized_actions_bitwise_equal=digest(normalized)==expected["normalized_sha256"], actions_bitwise_equal=digest(action)==expected["actions_sha256"],
        normalized_actions_sha256=digest(normalized), actions_sha256=digest(action), actions=action.tolist(),
        checkpoint_technical_test=loaded.technical_test, resource_after_reload=snapshot(), physical_commands_sent=0)
    atomic_json(report_path, report)
    telemetry.log_artifact(report_path)
    telemetry.close("FINISHED")
    report["mlflow_run_id"] = telemetry.run_id


def worker(args):
    from so101_lab.resources import ResourceLease, assert_compute_available, snapshot
    path = args.reports / ("reload_report.json" if args.reload else "report.json")
    report = {"status": "running", "phase": "preflight", "mode": "new_process_reload" if args.reload else "production_ACT_CPU_update",
        "model_is_tiny": False, "technical_test": True, "hardware_opened": False, "cuda_used": False,
        "torque_writes": 0, "goal_position_writes": 0, "physical_commands_sent": 0,
        "demonstration_training": False, "successful_policy_claim": False, "pid": os.getpid(), "started_unix_s": time.time()}
    atomic_json(path, report)
    try:
        report["resource_preflight"] = assert_compute_available("cpu", minimum_available_gb=8.)
        with ResourceLease("gpu_compute"), ResourceLease("cpu_compute"):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            # Keep CUDA unavailable inside this explicitly CPU-only child.
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
            import torch
            random.seed(31)
            torch.manual_seed(31)
            torch.set_num_threads(2)
            report["torch_version"] = torch.__version__
            if args.reload:
                reload_worker(args, report, path)
            else:
                train_worker(args, report, path)
    except Exception as error:
        report.update(status="FAIL", phase="failed", error=str(error), traceback=traceback.format_exc())
    finally:
        report.update(finished_unix_s=time.time(), process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                      process_final_rss_bytes=rss_bytes(), resource_after=snapshot())
        atomic_json(path, report)
        print(json.dumps({"status": report["status"], "report": str(path.relative_to(ROOT)), "checkpoint": report.get("checkpoint"),
            "loss": report.get("metrics", {}).get("train/loss"), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


def guarded_child(args, *, reload_mode):
    command = [sys.executable, str(Path(__file__).resolve()), "--execute", "--worker", "--images", str(args.images),
               "--positions", str(args.positions), "--sam", str(args.sam), "--output", str(args.output), "--reports", str(args.reports)]
    if reload_mode:
        command.append("--reload")
    mode = "reload" if reload_mode else "train"
    guard = {"status": "running", "mode": mode, "cuda_used": False, "hardware_opened": False,
             "minimum_reserve_bytes": 2*1024**3, "baseline_available_bytes": available_bytes()}
    minimum = available_bytes()
    peak_rss = 0
    started = time.monotonic()
    with (ROOT / "logs" / f"acceptance-act-cpu-{mode}.log").open("w") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        guard["pid"] = child.pid
        while child.poll() is None:
            minimum = min(minimum, available_bytes())
            try:
                peak_rss = max(peak_rss, rss_bytes(child.pid))
            except (OSError, IndexError):
                pass
            if minimum < 2*1024**3 or time.monotonic()-started > 900:
                guard["status"] = "ABORTED_TO_PRESERVE_RAM" if minimum < 2*1024**3 else "ABORTED_TIMEOUT"
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            time.sleep(.1)
    guard.update(exit_code=child.returncode, elapsed_seconds=time.monotonic()-started,
                 minimum_available_bytes=minimum, peak_child_rss_bytes=peak_rss)
    if guard["status"] == "running":
        guard["status"] = "PASS" if child.returncode == 0 else "FAIL"
    atomic_json(args.reports / f"{mode}_guard.json", guard)
    child_report_path = args.reports / ("reload_report.json" if reload_mode else "report.json")
    if child_report_path.exists():
        child_report = json.loads(child_report_path.read_text())
        child_report.update(supervisor_guard=guard, memory_available_min_bytes=minimum, process_peak_rss_sampled_bytes=peak_rss)
        if guard["status"] != "PASS":
            child_report["status"] = guard["status"]
        atomic_json(child_report_path, child_report)
    print(json.dumps(guard, ensure_ascii=False))
    return child.returncode or (0 if guard["status"] == "PASS" else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--images", type=Path, default=DEFAULT / "cameras")
    parser.add_argument("--positions", type=Path, default=DEFAULT / "hardware-readonly.json")
    parser.add_argument("--sam", type=Path, default=DEFAULT / "sam3")
    parser.add_argument("--output", type=Path, default=ROOT / "models/acceptance_act_cpu_20261106")
    parser.add_argument("--reports", type=Path, default=DEFAULT / "act_cpu")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Нужен явный --execute; модели не загружены")
    for key in ("images", "positions", "sam", "output", "reports"):
        path = getattr(args, key).resolve()
        if not path.is_relative_to(ROOT):
            parser.error("Все пути должны находиться внутри независимой лаборатории")
        setattr(args, key, path)
    args.reports.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return worker(args)
    result = guarded_child(args, reload_mode=args.reload)
    if result == 0 and not args.reload:
        result = guarded_child(args, reload_mode=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
