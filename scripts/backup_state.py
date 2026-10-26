#!/usr/bin/env python3
"""ENGINEERING_GLUE: private central metadata backup with an actual Store restore test."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import stat
import time

from environment_inventory import collect_environment, digest, source_inventory
from maintenance_common import (MaintenanceError, copy_private, private_directory, private_json,
                                read_json, replace_private_json, snapshot_database, sync_directories, test_restore)

CONFIG_FILES = ("config.json", "node.env", "arms.json", "access-code.txt")


def selected_state(config: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser()
    path = config / "config.json"
    value = read_json(path).get("data_dir") if path.is_file() else None
    if value is not None and not isinstance(value, str):
        raise MaintenanceError("Configured data_dir must be a path string")
    return Path(value).expanduser() if value else Path.home() / ".local/share/explorer/state"


def create_backup(state: Path, config: Path, calibration: Path, root: Path, destination: Path) -> dict:
    state, config, calibration, root = (value.resolve() for value in (state, config, calibration, root))
    if not (state / "research.sqlite3").is_file():
        raise MaintenanceError("A central SQLite database is required")
    if destination.is_symlink() or destination.exists() or not destination.parent.is_dir():
        raise MaintenanceError("Backup destination must be a new directory with an existing parent")
    destination = destination.resolve()
    private_directory(destination)
    try:
        database = snapshot_database(state / "research.sqlite3", destination / "data/research.sqlite3")
        copied = []
        for name in CONFIG_FILES:
            source = config / name
            if source.exists():
                if source.stat().st_size > 2 * 1024 * 1024:
                    raise MaintenanceError("Project configuration exceeds its metadata size bound")
                target = destination / "private/config" / name
                copy_private(source, target)
                copied.append(target)
        calibration_count = 0
        if calibration.is_dir():
            candidates = sorted(calibration.rglob("*.json"))
            if len(candidates) > 256:
                raise MaintenanceError("Calibration file count exceeds its explicit bound")
            for source in candidates:
                if source.stat().st_size > 1024 * 1024 or any(parent.is_symlink() for parent in source.parents if parent != calibration.parent):
                    raise MaintenanceError("Calibration is linked or exceeds its metadata size bound")
                target = destination / "private/calibration" / source.relative_to(calibration)
                copy_private(source, target)
                copied.append(target)
                calibration_count += 1
        private_json(destination / "environment.json", collect_environment())
        private_json(destination / "source-inventory.json", source_inventory(root))
        entries = []
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                entries.append({"path": path.relative_to(destination).as_posix(), "bytes": path.stat().st_size,
                                "sha256": digest(path), "mode": stat.S_IMODE(path.stat().st_mode)})
        manifest = {"schema": "explorer_metadata_backup_v1", "created_at_unix": time.time(),
                    "source_state": str(state), "source_config": str(config), "source_calibration": str(calibration), "project_root": str(root),
                    "database": database, "files": entries,
                    "scope": "Central SQLite plus private config/calibration and source/environment inventories.",
                    "artifact_payloads_included": False,
                    "external_payloads": "Datasets, recordings, jobs/checkpoints, CAD meshes, source checkout and venvs require a separate storage backup; inventory hashes are not payload copies."}
        private_json(destination / "manifest.json", manifest)
        receipt = {"backup_directory": str(destination), "manifest_sha256": digest(destination / "manifest.json"),
                   "file_count": len(entries), "bytes": sum(item["bytes"] for item in entries),
                   "private_config_or_calibration_files": len(copied), "artifact_payloads_included": False,
                   "calibration_files": calibration_count, "calibration_source_present": calibration.is_dir(),
                   "test_restore_passed": False}
        private_json(destination / "receipt.json", receipt)
        result = test_restore(destination, root)
        # Receipt itself is not a payload member; update only after the actual isolated restore succeeds.
        receipt.update(result)
        replace_private_json(destination / "receipt.json", receipt)
        sync_directories(destination)
        return receipt
    except Exception:
        # Keep partial private evidence for inspection; never remove a previous backup or source data.
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, help="Override config.json data_dir explicitly")
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/explorer")
    parser.add_argument("--calibration", type=Path, default=Path.home() / ".cache/huggingface/lerobot/calibration")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path, required=True, help="New private backup folder on the same or a separately backed-up disk")
    args = parser.parse_args()
    try:
        print(json.dumps(create_backup(selected_state(args.config, args.state), args.config, args.calibration, args.root, args.destination), indent=2))
    except (MaintenanceError, OSError, ValueError, sqlite3.Error, ImportError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "detail": "Metadata backup did not complete; source data was not changed."}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
