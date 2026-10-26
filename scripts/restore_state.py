#!/usr/bin/env python3
"""ENGINEERING_GLUE: verify by default; restore only into an explicit new rehearsal folder."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from maintenance_common import (MaintenanceError, private_directory, private_json, restore_files,
                                sync_directories, validate_store_restore, verify_bundle)


def validate_destination(destination: Path, bundle: Path, manifest: dict, root: Path, home: Path) -> Path:
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise MaintenanceError("Rehearsal destination must be new and have an existing parent")
    resolved = destination.resolve()
    # Rehearsals never land inside a live runtime, config, calibration, source or old controller tree.
    forbidden = [bundle, root, Path(manifest["source_state"]), Path(manifest["source_config"]),
                 Path(manifest["source_calibration"]), home / ".local/share/explorer", home / ".config/explorer",
                 home / ".cache/huggingface/lerobot", home / "Explorer"]
    forbidden = [path.resolve() for path in forbidden]
    if any(resolved == path or path in resolved.parents or resolved in path.parents for path in forbidden):
        raise MaintenanceError("Rehearsal destination overlaps a protected live, source or backup tree")
    return resolved


def restore(bundle: Path, root: Path, destination: Path | None = None, *, apply: bool = False, home: Path | None = None) -> dict:
    bundle, root = bundle.resolve(), root.resolve()
    manifest = verify_bundle(bundle)
    summary = {"mode": "dry_run", "verified": True, "file_count": len(manifest["files"]),
               "database": manifest["database"], "artifact_payloads_included": False,
               "live_data_changed": False, "controller_settings_changed": False}
    if destination is not None:
        destination = validate_destination(destination, bundle, manifest, root, home or Path.home())
        summary["destination"] = str(destination)
    if apply:
        if destination is None:
            raise MaintenanceError("An explicit new destination is required for rehearsal restore")
        private_directory(destination)
        restore_files(bundle, destination, manifest)
        result = validate_store_restore(destination / "data", root, manifest["database"])
        summary.update(mode="rehearsal_restored", test_restore_passed=True, store=result)
        private_json(destination / "restore-receipt.json", summary)
        sync_directories(destination)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--apply", action="store_true", help="Restore a rehearsal only, never live data")
    args = parser.parse_args()
    try:
        print(json.dumps(restore(args.backup, args.root, args.destination, apply=args.apply), indent=2))
    except (MaintenanceError, OSError, ValueError, sqlite3.Error, ImportError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "detail": "Restore was refused or stopped; live data was not changed."}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
