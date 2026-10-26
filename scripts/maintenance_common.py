"""ENGINEERING_GLUE: private metadata snapshots and isolated restore validation."""
from __future__ import annotations

import importlib
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from urllib.parse import quote

from environment_inventory import digest

TABLES = ("events", "nodes", "jobs", "skills", "sessions", "tickets")


class MaintenanceError(RuntimeError):
    pass


def read_json(path: Path, limit: int = 8 * 1024 * 1024):
    if path.stat().st_size > limit:
        raise MaintenanceError("JSON exceeds the metadata size bound")
    return json.loads(path.read_text())


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=False)
    path.chmod(0o700)


def private_json(path: Path, value) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def replace_private_json(path: Path, value) -> None:
    temporary = path.parent / (".metadata-" + uuid.uuid4().hex)
    private_json(temporary, value)
    os.replace(temporary, path)


def sync_directories(root: Path) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir() and not path.is_symlink()]
    for path in [*sorted(directories, key=lambda value: len(value.parts), reverse=True), root, root.parent]:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def copy_private(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise MaintenanceError("Only regular, non-symlink files can enter metadata backups")
    missing = []
    parent = target.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())


def open_readonly_db(path: Path, *, immutable: bool = False):
    suffix = "&immutable=1" if immutable else ""
    connection = sqlite3.connect("file:" + quote(str(path.resolve()), safe="/") + "?mode=ro" + suffix, uri=True, timeout=10)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    deadline = time.monotonic() + 15
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    return connection


def database_counts(path: Path, *, immutable: bool = False) -> dict:
    with closing(open_readonly_db(path, immutable=immutable)) as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise MaintenanceError("SQLite integrity check failed")
        existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in TABLES if name in existing}
    return {"integrity": "ok", "counts": counts}


def snapshot_database(source: Path, target: Path) -> dict:
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if target.exists():
        raise MaintenanceError("Snapshot target already exists")
    # Source mode=ro retains committed WAL entries. Never use immutable=1 for a live database.
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > 60:
            raise MaintenanceError("SQLite snapshot exceeded its 60-second bound")

    with closing(open_readonly_db(source)) as live:
        with closing(sqlite3.connect(target)) as snapshot:
            live.backup(snapshot, pages=256, progress=progress, sleep=0.05)
            snapshot.commit()
            snapshot.execute("PRAGMA journal_mode=DELETE")
    target.chmod(0o600)
    return database_counts(target, immutable=True)


def safe_relative(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise MaintenanceError("Invalid backup member path")
    return path


def verify_bundle(bundle: Path) -> dict:
    manifest_path = bundle / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("schema") != "explorer_metadata_backup_v1":
        raise MaintenanceError("Unsupported metadata backup schema")
    receipt = read_json(bundle / "receipt.json")
    if receipt.get("manifest_sha256") != digest(manifest_path):
        raise MaintenanceError("Backup manifest checksum mismatch")
    entries = manifest.get("files", [])
    if len(entries) > 50000:
        raise MaintenanceError("Too many backup files")
    seen = set()
    for item in entries:
        relative = safe_relative(item["path"])
        path = bundle / relative
        if relative.as_posix() in seen or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != bundle.parent):
            raise MaintenanceError("Duplicate or linked backup member")
        seen.add(relative.as_posix())
        if not path.is_file() or path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise MaintenanceError("Backup file checksum mismatch")
    db = bundle / "data/research.sqlite3"
    if "data/research.sqlite3" not in seen:
        raise MaintenanceError("Central database is absent from backup")
    actual = database_counts(db, immutable=True)
    if actual != manifest["database"]:
        raise MaintenanceError("Backup database contents disagree with its manifest")
    return manifest


def validate_store_restore(data: Path, project_root: Path, expected: dict) -> dict:
    # Store creates schemas/WAL: this function is used ONLY in a newly allocated rehearsal tree.
    sys.path[:0] = [str(project_root / "00-control-center/src"), str(project_root / "shared")]
    module = importlib.import_module("explorer.store")
    store = module.Store(data)
    actual = database_counts(data / "research.sqlite3")
    if actual != expected:
        raise MaintenanceError("Store rehearsal did not preserve database row counts")
    return {"store_opened": True, **actual}


def restore_files(bundle: Path, destination: Path, manifest: dict) -> None:
    for item in manifest["files"]:
        relative = safe_relative(item["path"])
        copy_private(bundle / relative, destination / relative)
        if digest(destination / relative) != item["sha256"]:
            raise MaintenanceError("Restored file checksum differs from its verified backup member")


def test_restore(bundle: Path, project_root: Path) -> dict:
    manifest = verify_bundle(bundle)
    with tempfile.TemporaryDirectory(prefix="explorer-restore-test-") as temporary:
        destination = Path(temporary)
        destination.chmod(0o700)
        restore_files(bundle, destination, manifest)
        store = validate_store_restore(destination / "data", project_root, manifest["database"])
        for item in manifest["files"]:
            if not item["path"].startswith("data/") and digest(destination / item["path"]) != item["sha256"]:
                raise MaintenanceError("Restored private file checksum mismatch")
    return {"test_restore_passed": True, "store": store, "private_files_sha256_verified": True}
