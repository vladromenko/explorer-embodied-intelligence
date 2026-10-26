#!/usr/bin/env python3
"""ENGINEERING_GLUE: local managed-release plans; authoritative apply only on Homebrain.

No controller calls or configuration writes. Only current symlink replacement and
the exact Explorer user-unit restart allowlist are permitted. Receipt is stdout;
the deployment's previous-release hint is not overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
import uuid
from urllib.parse import quote

from maintenance_common import open_readonly_db

UNITS = {"homebrain": ("explorer-control-center.service", "explorer-worker.service"),
         "pi": ("explorer-observer.service",), "nano": ("explorer-observer.service",)}
TERMINAL = ("completed", "failed", "cancelled")
COMMIT = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"


class RollbackError(RuntimeError):
    def __init__(self, message, receipt=None):
        super().__init__(message)
        self.receipt = receipt


def owned(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise RollbackError("Managed release paths must be regular, owned by this user and not group/world writable")


def managed_base(home: Path) -> Path:
    base = (home / ".local/share/explorer").resolve(strict=True)
    owned(base, directory=True)
    owned(base / "releases", directory=True)
    return base


def validate_release(base: Path, requested, node: str) -> dict:
    if not isinstance(requested, (str, Path)):
        raise RollbackError("A release commit or managed release path is required")
    value = str(requested).strip()
    target = base / "releases" / value if re.fullmatch(COMMIT, value) else Path(value).expanduser()
    if not target.is_absolute():
        raise RollbackError("Release paths must be absolute or a full commit identity")
    if target.is_symlink():
        raise RollbackError("Managed release directory must not be a symlink")
    target = target.resolve(strict=True)
    if target.parent != base / "releases" or not re.fullmatch(COMMIT, target.name):
        raise RollbackError("Target is outside the managed release root or lacks a full commit identity")
    owned(target, directory=True)
    marker = target / "RELEASE_COMMIT"
    owned(marker, directory=False)
    if marker.stat().st_size > 128 or marker.read_text().strip() != target.name:
        raise RollbackError("Release marker and directory commit do not match")
    entry = target / ("00-control-center/src/explorer/cli.py" if node == "homebrain" else "shared/explorer_shared/node_agent.py")
    if not entry.is_file() or entry.is_symlink() or not entry.resolve().is_relative_to(target):
        raise RollbackError("Target release does not contain the expected local Explorer entry point")
    owned(entry, directory=False)
    for parent in entry.parents:
        if parent == target:
            break
        owned(parent, directory=True)
    return {"commit": target.name, "path": str(target)}


def current_release(base: Path, node: str) -> dict:
    link = base / "current"
    if not link.is_symlink() or link.lstat().st_uid != os.geteuid():
        raise RollbackError("Current release must be a symlink owned by this user")
    target = Path(os.readlink(link))
    return validate_release(base, target if target.is_absolute() else base / target, node)


def active_jobs(database: sqlite3.Connection) -> list[dict]:
    placeholders = ",".join("?" for _ in TERMINAL)
    rows = database.execute(f"SELECT id,kind,status FROM jobs WHERE status IS NULL OR status NOT IN ({placeholders}) ORDER BY created LIMIT 100", TERMINAL).fetchall()
    return [{"id": row[0], "kind": row[1], "status": row[2]} for row in rows]


def job_state(state: Path | None, node: str) -> dict:
    if node != "homebrain" or state is None or not (state / "research.sqlite3").is_file():
        return {"authoritative": False, "active_jobs": None, "reason": "Authoritative central jobs are unavailable locally; apply is refused"}
    try:
        database = open_readonly_db(state / "research.sqlite3")
        try:
            jobs = active_jobs(database)
        finally:
            database.close()
        return {"authoritative": True, "active_jobs": jobs, "job_list_limit": 100}
    except (OSError, sqlite3.Error):
        return {"authoritative": False, "active_jobs": None, "reason": "Central job registry could not be read; apply is refused"}


def atomic_switch(base: Path, target: Path) -> None:
    temporary = base / (".current-rollback-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, base / "current")
        descriptor = os.open(base, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def restart_units(units: tuple[str, ...]) -> None:
    if tuple(units) != UNITS["homebrain"]:
        raise RollbackError("Actual restarts are restricted to the two Homebrain Explorer user units")
    subprocess.run(["systemctl", "--user", "restart", *units], check=True, timeout=45, capture_output=True)


def rollback(home: Path, node: str, *, release=None, state: Path | None = None,
             apply: bool = False, restart=restart_units) -> dict:
    if node not in UNITS:
        raise RollbackError("Unknown local installation")
    base = managed_base(home)
    before = current_release(base, node)
    if release is None:
        hint = base / "previous-release"
        owned(hint, directory=False)
        if hint.stat().st_size > 4096:
            raise RollbackError("Previous release hint exceeds its size bound")
        release = hint.read_text().strip()
    after = validate_release(base, release, node)
    guard = job_state(state, node)
    receipt = {"schema": "explorer_release_rollback_v1", "node": node, "dry_run": not apply,
               "previous": before, "current": before, "requested": after,
               "restart_units": list(UNITS[node]), "jobs": guard, "applied": False,
               "previous_release_hint_changed": False, "controller_settings_changed": False}
    if not apply:
        return receipt
    if node != "homebrain" or not guard["authoritative"]:
        raise RollbackError("Apply requires a local authoritative Homebrain job registry; Pi/Nano support plans only", receipt)
    database_path = state / "research.sqlite3"
    owned(database_path.resolve(), directory=False)
    database = sqlite3.connect("file:" + quote(str(database_path.resolve()), safe="/") + "?mode=rw", uri=True, timeout=10)
    try:
        deadline = time.monotonic() + 15
        database.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        # Serializes job creation with this transition. No rows/schema are written.
        database.execute("BEGIN IMMEDIATE")
        jobs = active_jobs(database)
        receipt["jobs"] = {"authoritative": True, "active_jobs": jobs, "job_list_limit": 100}
        if jobs:
            raise RollbackError("Finish or cancel unfinished jobs before applying a release rollback", receipt)
        if current_release(base, node) != before or validate_release(base, after["path"], node) != after:
            raise RollbackError("Managed release changed while preparing rollback", receipt)
        if before == after:
            receipt["already_current"] = True
            return receipt
        try:
            atomic_switch(base, Path(after["path"]))
            receipt["current"] = after
            restart(UNITS[node])
        except (OSError, subprocess.SubprocessError) as error:
            receipt.update(recovered_symlink=False, recovery_switch_durable=False, restart_completed=False)
            try:
                atomic_switch(base, Path(before["path"]))
                receipt.update(current=before, recovered_symlink=True, recovery_switch_durable=True)
            except OSError:
                receipt["current"] = current_release(base, node)
                receipt["recovered_symlink"] = receipt["current"] == before
            if receipt["recovered_symlink"]:
                try:
                    restart(UNITS[node])
                    receipt["recovery_restart_completed"] = True
                except (OSError, subprocess.SubprocessError):
                    receipt["recovery_restart_completed"] = False
            raise RollbackError("Explorer activation failed; inspect the recorded current release and recovery result", receipt) from error
        receipt.update(applied=True, restart_completed=True)
        return receipt
    finally:
        # Release the job transaction BEFORE callers perform any service/API readiness checks.
        database.rollback()
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True, choices=sorted(UNITS))
    parser.add_argument("--release")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = rollback(Path.home(), args.node, release=args.release, state=args.state, apply=args.apply)
        print(json.dumps(result, indent=2))
    except (RollbackError, OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "detail": str(error) if isinstance(error, RollbackError) else "Release rollback was refused or stopped",
                          "receipt": getattr(error, "receipt", None)}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
