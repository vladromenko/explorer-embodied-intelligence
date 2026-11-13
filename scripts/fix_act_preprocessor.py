"""Add the training-time centered letterbox (256x256) to saved ACT preprocessors.

ACT was trained on letterboxed 256x256 wrist images but checkpoints saved a
preprocessor without that step, so the official LeRobot rollout fed raw 480x640
frames to the network.  Idempotent; originals are kept in pretrained_model.orig.
Usage: python scripts/fix_act_preprocessor.py [--coeff -0.2] CHECKPOINT_DIR...
"""
import argparse, hashlib, json, shutil, sys
from pathlib import Path


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def fix(ckpt: Path, coeff):
    folder = ckpt / "pretrained_model"
    cfg = json.loads((folder / "config.json").read_text())
    if cfg.get("type") != "act":
        return "skip (not act)"
    manifest = json.loads((ckpt / "checkpoint.json").read_text())
    if manifest.get("technical_test"):
        return "skip (technical)"
    pre_path = folder / "policy_preprocessor.json"
    pre = json.loads(pre_path.read_text())
    size = cfg["input_features"]["observation.images.wrist"]["shape"][-1] if "observation.images.wrist" in cfg["input_features"] else 256
    names = [s["registry_name"] for s in pre["steps"]]
    changed = False
    backup = ckpt / "pretrained_model.orig"
    if "so101_centered_letterbox_processor" not in names:
        backup.mkdir(exist_ok=True)
        if not (backup / "policy_preprocessor.json").exists():
            shutil.copy2(pre_path, backup / "policy_preprocessor.json")
        pre["steps"].insert(names.index("normalizer_processor"),
                            {"registry_name": "so101_centered_letterbox_processor", "config": {"height": size, "width": size}})
        pre_path.write_text(json.dumps(pre, indent=2))
        changed = True
    if coeff is not None and cfg.get("temporal_ensemble_coeff") != coeff:
        backup.mkdir(exist_ok=True)
        if not (backup / "config.json").exists():
            shutil.copy2(folder / "config.json", backup / "config.json")
        cfg["temporal_ensemble_coeff"] = coeff
        (folder / "config.json").write_text(json.dumps(cfg, indent=2))
        changed = True
    if changed:
        manifest["files_sha256"] = {str(p.relative_to(folder)): sha(p) for p in sorted(folder.rglob("*")) if p.is_file()}
        (ckpt / "checkpoint.json").write_text(json.dumps(manifest, indent=2))
    return "patched" if changed else "already ok"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--coeff", type=float, default=-0.2)
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    for p in a.paths:
        print(p, fix(Path(p).resolve(), a.coeff))
