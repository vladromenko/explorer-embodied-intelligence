#!/usr/bin/env python3
"""ENGINEERING_GLUE: bounded, secret-free environment facts; no hardware imports."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

PACKAGES = ("torch", "torchvision", "lerobot", "mujoco", "stable-baselines3", "numpy", "opencv-python", "opencv-python-headless", "opencv", "hailort", "feetech-servo-sdk", "pyserial")
SKIP = {".git", ".venv", "venv", "servo-venv", "node_modules", "__pycache__", ".pytest_cache", "private", "data", "datasets", "models", "checkpoints"}
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".tsx", ".html", ".css", ".md", ".json", ".toml", ".yaml", ".yml", ".sh", ".service", ".timer", ".txt", ".xml"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(args: list[str], timeout: int = 10) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
        if result.returncode == 0 and len(result.stdout) <= 2 * 1024 * 1024:
            return result.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def safe_atom(value) -> str | None:
    return str(value) if value is not None and re.fullmatch(r"[A-Za-z0-9._+:-]{1,150}", str(value)) else None


def package_facts(name: str) -> dict:
    try:
        dist = metadata.distribution(name)
        result = {"version": safe_atom(dist.version)}
        record = dist.read_text("RECORD")
        if record is not None:
            result["record_sha256"] = hashlib.sha256(record.encode()).hexdigest()
        origin = dist.read_text("direct_url.json")
        if origin:
            direct = json.loads(origin)
            vcs = direct.get("vcs_info", {})
            hashes = direct.get("archive_info", {}).get("hashes", {})
            result["origin"] = {"kind": "vcs" if vcs else "archive_or_local",
                                "commit_id": safe_atom(vcs.get("commit_id", "")),
                                "archive_sha256": hashes.get("sha256") if re.fullmatch(r"[0-9a-f]{64}", str(hashes.get("sha256"))) else None}
        if name in {"torch", "torchvision"}:
            version_file = Path(dist.locate_file(name + "/version.py"))
            if version_file.is_file() and version_file.stat().st_size < 65536:
                fields = {}
                for statement in ast.parse(version_file.read_text()).body:
                    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target] if isinstance(statement, ast.AnnAssign) else []
                    value = statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign)) else None
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id in {"__version__", "cuda", "hip", "git_version"} and isinstance(value, ast.Constant):
                            fields[target.id] = safe_atom(value.value)
                result["static_build_fields"] = fields
                result["version_source_sha256"] = digest(version_file)
        if name == "lerobot":
            result["act_source_sha256"] = {}
            for relative in ("lerobot/policies/act/modeling_act.py", "lerobot/policies/act/configuration_act.py"):
                path = Path(dist.locate_file(relative))
                if path.is_file() and path.stat().st_size < 2 * 1024 * 1024:
                    result["act_source_sha256"][relative] = digest(path)
        return result
    except (metadata.PackageNotFoundError, OSError, ValueError, SyntaxError):
        return {"installed": False}


def collect_environment() -> dict:
    # Keep actual freeze output in memory only: editable/direct URLs may contain credentials.
    raw_freeze = run([sys.executable, "-m", "pip", "freeze", "--all"], timeout=45)
    installed = {}
    for distribution in metadata.distributions():
        name, version = distribution.metadata.get("Name"), distribution.version
        if safe_atom(name) and safe_atom(version):
            installed.setdefault(name.lower().replace("_", "-"), f"{name}=={version}")
    freeze = []
    redacted = 0
    for line in (raw_freeze or "").splitlines():
        if re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+", line):
            freeze.append(line)
        else:
            redacted += 1
    # Include installed versions for URL/editable packages, without retaining their locations.
    normalized = {line.split("==")[0].lower().replace("_", "-") for line in freeze}
    freeze.extend(value for name, value in installed.items() if name not in normalized)
    model_path = Path("/proc/device-tree/model")
    model = model_path.read_bytes().replace(b"\0", b"").decode("utf-8", "replace")[:200] if model_path.exists() else None
    os_release = {}
    release_path = Path("/etc/os-release")
    if release_path.exists():
        for line in release_path.read_text().splitlines():
            key, _, value = line.partition("=")
            if key in {"ID", "VERSION_ID", "PRETTY_NAME"}:
                os_release[key] = value.strip('"')[:200]
    hailo = run(["dpkg-query", "-W", "-f=${db:Status-Abbrev} ${binary:Package}=${Version}\n", "*hailo*"])
    release = Path.home() / ".local/share/explorer/current/RELEASE_COMMIT"
    commit = release.read_text().strip() if release.is_file() and release.stat().st_size <= 256 else ""
    return {"schema": "explorer_environment_inventory_v1", "collected_at_unix": time.time(),
            "python": platform.python_version(), "python_executable": sys.executable,
            "explorer_release_commit": commit if re.fullmatch(r"[0-9a-f]{40,64}", commit) else None,
            "site_package_search_paths": list(dict.fromkeys(str(Path(path).resolve()) for path in sys.path if path.endswith(("site-packages", "dist-packages")))),
            "architecture": platform.machine(), "kernel": platform.release(), "hardware_model": model,
            "os": os_release, "nvidia_l4t_release": Path("/etc/nv_tegra_release").read_text()[:500].strip() if Path("/etc/nv_tegra_release").exists() else None,
            "system_hailo_packages": [line[3:].strip() for line in hailo.splitlines() if line.startswith("ii ")] if hailo else [],
            "pip_freeze_sanitized": sorted(set(freeze), key=str.lower),
            "pip_freeze_available": raw_freeze is not None, "freeze_url_or_editable_lines_redacted": redacted,
            "packages": {name: package_facts(name) for name in PACKAGES},
            "scope": "Installed metadata only. No CUDA/Hailo/servo import, model execution or hardware qualification."}


def source_inventory(root: Path, max_files: int = 20000, max_bytes: int = 512 * 1024 * 1024) -> dict:
    root = root.resolve()
    files, consumed, omitted = [], 0, 0
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in SKIP and not (Path(parent) / name).is_symlink())
        for name in sorted(names):
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            eligible = not path.is_symlink() and (path.suffix in SOURCE_SUFFIXES or name in {"LICENSE", "Makefile", "RELEASE_COMMIT"})
            if eligible and path.is_file():
                size = path.stat().st_size
                if len(files) < max_files and size <= 16 * 1024 * 1024 and consumed + size <= max_bytes:
                    files.append({"path": relative, "bytes": size, "sha256": digest(path)})
                    consumed += size
                else:
                    omitted += 1
    release = root / "RELEASE_COMMIT"
    commit = release.read_text().strip() if release.is_file() else run(["git", "-C", str(root), "rev-parse", "HEAD"])
    return {"root": str(root), "commit": commit if re.fullmatch(r"[a-f0-9]{40,64}", commit or "") else None,
            "files": files, "hashed_bytes": consumed, "eligible_files_omitted_by_bounds": omitted,
            "scope": "Source hashes only; private/generated trees, symlinks, media, datasets and model payloads excluded."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    facts = collect_environment()
    if args.root:
        facts["source"] = source_inventory(args.root)
    print(json.dumps(facts, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
