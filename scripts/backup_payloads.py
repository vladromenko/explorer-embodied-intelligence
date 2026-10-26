#!/usr/bin/env python3
"""ENGINEERING_GLUE: complete local payload copies, source-window checks and isolated restore.

No hardware imports, service calls, source deletion, live restore or database writes.
The declared source window is not a filesystem or cross-host atomic snapshot.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time

from backup_state import create_backup as create_metadata_backup, selected_state
from maintenance_common import (MaintenanceError, open_readonly_db, private_directory,
                                private_json, read_json, replace_private_json, sync_directories,
                                validate_store_restore, verify_bundle)

PAYLOAD_DIRS = ("jobs", "datasets", "episodes", "artifacts", "assets", "qualification")
TERMINAL = {"completed", "failed", "cancelled"}
SCHEMA = "explorer_full_payload_backup_v1"
BLOCK = 4 * 1024 * 1024
MAX_ENTRIES = 500_000
SHA = re.compile(r"[0-9a-f]{64}")


def identity(value: os.stat_result) -> dict:
    return {"device": value.st_dev, "inode": value.st_ino, "mode": value.st_mode,
            "bytes": value.st_size, "mtime_ns": value.st_mtime_ns, "ctime_ns": value.st_ctime_ns,
            "links": value.st_nlink}


def jobs_window(database: Path) -> dict:
    """Fingerprint every job column, without retaining its config/result or touching events."""
    result, count, states = hashlib.sha256(), 0, {}
    with closing(open_readonly_db(database)) as connection:
        cursor = connection.execute("SELECT * FROM jobs ORDER BY id")
        status_column = [item[0] for item in cursor.description].index("status")
        result.update(json.dumps([item[0] for item in cursor.description]).encode())
        for row in cursor:
            status = row[status_column]
            if status not in TERMINAL:
                raise MaintenanceError("All central jobs must be terminal; queued/paused/unknown is not idle")
            raw = json.dumps(list(row), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            result.update(len(raw).to_bytes(8, "big"))
            result.update(raw)
            count += 1
            states[status] = states.get(status, 0) + 1
    return {"sha256": result.hexdigest(), "count": count, "statuses": states}


def unchanged_jobs(database: Path, expected: dict) -> None:
    if jobs_window(database) != expected:
        raise MaintenanceError("Central job rows changed during the declared backup window")


def relative(name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name:
        raise MaintenanceError("Invalid manifest member")
    value = Path(name)
    if not value.parts or value.is_absolute() or value.as_posix() != name or any(part in {".", "..", ""} for part in value.parts):
        raise MaintenanceError("Invalid manifest member")
    return value


def overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def new_destination(destination: Path, protected: list[Path]) -> Path:
    destination = destination.expanduser()
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise MaintenanceError("Destination must be new with an existing parent")
    destination = destination.resolve()
    if any(overlaps(destination, path.resolve()) for path in protected):
        raise MaintenanceError("Destination overlaps a protected source, runtime or backup tree")
    return destination


def inventory(state: Path) -> tuple[dict, list[str]]:
    """Do not follow links. Every permitted link must resolve into a selected source tree."""
    found, missing = {}, []
    allowed = [state / name for name in PAYLOAD_DIRS]
    central = set()
    for name in ("research.sqlite3", "research.sqlite3-wal", "research.sqlite3-shm"):
        path = state / name
        if path.exists():
            info = path.stat()
            central.add((info.st_dev, info.st_ino))

    def visit(path: Path) -> None:
        info = path.lstat()
        name = path.relative_to(state).as_posix()
        entry = {"identity": identity(info)}
        if stat.S_ISLNK(info.st_mode):
            target = path.resolve(strict=True)
            if not any(target == root or root in target.parents for root in allowed):
                raise MaintenanceError("A payload symlink escapes the selected source trees")
            entry.update(kind="symlink", original_link=os.readlink(path),
                         target=target.relative_to(state).as_posix())
        elif stat.S_ISDIR(info.st_mode):
            entry["kind"] = "directory"
        elif stat.S_ISREG(info.st_mode):
            if (info.st_dev, info.st_ino) in central:
                raise MaintenanceError("Raw central SQLite/WAL/SHM hardlink aliases cannot be payload copies")
            entry["kind"] = "file"
        else:
            raise MaintenanceError("Special device/socket/FIFO payloads are not supported")
        found[name] = entry
        if len(found) > MAX_ENTRIES:
            raise MaintenanceError("Payload entry count exceeds the explicit bound")
        if entry["kind"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child)
        if identity(path.lstat()) != entry["identity"]:
            raise MaintenanceError("Source entry changed while inventorying")

    for name in PAYLOAD_DIRS:
        path = state / name
        if os.path.lexists(path):
            if path.is_symlink() or not path.is_dir():
                raise MaintenanceError("Selected payload roots must be actual directories")
            visit(path)
        else:
            missing.append(name)
    for entry in found.values():
        if entry["kind"] == "symlink" and entry["target"] not in found:
            raise MaintenanceError("Payload symlink target is not an included member")
    return found, missing


def stream_file(source: Path, destination: Path | None = None, expected: dict | None = None) -> tuple[str, int]:
    """O_NOFOLLOW + before/after fd/path identity detects replacement/truncation/in-place writes."""
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    total, checksum = 0, hashlib.sha256()
    with os.fdopen(descriptor, "rb") as src:
        before = identity(os.fstat(src.fileno()))
        if not stat.S_ISREG(before["mode"]) or (expected is not None and before != expected):
            raise MaintenanceError("Source file identity differs from the selected inventory")
        output = None
        try:
            if destination is not None:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                output = os.fdopen(fd, "wb")
            for block in iter(lambda: src.read(BLOCK), b""):
                checksum.update(block)
                total += len(block)
                if output is not None:
                    output.write(block)
            if output is not None:
                output.flush()
                os.fsync(output.fileno())
            if total != before["bytes"] or identity(os.fstat(src.fileno())) != before or identity(source.lstat()) != before:
                raise MaintenanceError("Source mutated/replaced/truncated during streaming")
        finally:
            if output is not None:
                output.close()
    return checksum.hexdigest(), total


def private_parents(path: Path, boundary: Path) -> None:
    if not path.is_relative_to(boundary):
        raise MaintenanceError("Copy target escaped its new private tree")
    current = boundary
    for part in path.relative_to(boundary).parts:
        current = current / part
        if not current.exists():
            current.mkdir(mode=0o700)
        if current.is_symlink() or not current.is_dir():
            raise MaintenanceError("Copy destination ancestor is not an actual directory")


def copy_metadata(source: Path, target: Path) -> dict:
    manifest = verify_bundle(source)
    if read_json(source / "receipt.json").get("test_restore_passed") is not True:
        raise MaintenanceError("Metadata snapshot has no successful isolated restore receipt")
    private_directory(target)
    for name in [*(item["path"] for item in manifest["files"]), "manifest.json", "receipt.json"]:
        path = target / relative(name)
        private_parents(path.parent, target)
        stream_file(source / name, path)
    verify_bundle(target)
    return manifest


def manifest_members(bundle: Path) -> list[dict]:
    entries = []
    for base in (bundle / "data", bundle / "metadata"):
        if base.is_symlink() or not base.is_dir():
            raise MaintenanceError("Backup payload roots must be actual directories")
        for path in [base, *sorted(base.rglob("*"))]:
            name = path.relative_to(bundle).as_posix()
            if path.is_symlink():
                entries.append({"path": name, "kind": "symlink", "target": os.readlink(path)})
            elif path.is_dir():
                entries.append({"path": name, "kind": "directory"})
            else:
                checksum, size = stream_file(path)
                entries.append({"path": name, "kind": "file", "bytes": size, "sha256": checksum})
    return sorted(entries, key=lambda value: value["path"])


def validate_members(manifest: dict) -> dict[str, dict]:
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES + 50_000:
        raise MaintenanceError("Invalid backup member count")
    found = {}
    for item in entries:
        path = relative(item["path"])
        if path.parts[0] not in {"data", "metadata"} or item["path"] in found:
            raise MaintenanceError("Duplicate or outside-root member")
        if path.parts[0] == "data" and len(path.parts) > 1 and path.parts[1] not in PAYLOAD_DIRS:
            raise MaintenanceError("Payload member is outside the exact six-directory allowlist")
        kind = item.get("kind")
        if kind not in {"directory", "file", "symlink"} or (kind == "symlink" and path.parts[0] != "data"):
            raise MaintenanceError("Invalid member kind")
        if kind == "file" and (not isinstance(item.get("bytes"), int) or item["bytes"] < 0 or not SHA.fullmatch(item.get("sha256", ""))):
            raise MaintenanceError("Invalid file checksum/size")
        found[item["path"]] = item
    for name, item in found.items():
        path = Path(name)
        for ancestor in path.parents:
            if ancestor != Path(".") and found.get(ancestor.as_posix(), {}).get("kind") != "directory":
                raise MaintenanceError("Member prefix is missing, linked or not a directory")
        if item["kind"] == "symlink":
            raw = item.get("target")
            if not isinstance(raw, str) or Path(raw).is_absolute():
                raise MaintenanceError("Invalid portable symlink target")
            target = Path(os.path.normpath(path.parent / raw))
            if target.parts[0] != "data" or target.as_posix() not in found or found[target.as_posix()]["kind"] == "symlink":
                raise MaintenanceError("Symlink target escapes or is absent/linked")
    return found


def verify_backup(bundle: Path, *, require_complete: bool = True) -> dict:
    bundle = bundle.resolve()
    manifest = read_json(bundle / "manifest.json", 256 * 1024 * 1024)
    receipt = read_json(bundle / "receipt.json")
    if manifest.get("schema") != SCHEMA or (require_complete and receipt.get("completed") is not True):
        raise MaintenanceError("Unsupported or incomplete full payload backup")
    if stream_file(bundle / "manifest.json")[0] != receipt.get("manifest_sha256"):
        raise MaintenanceError("Payload manifest checksum differs from its receipt")
    expected = validate_members(manifest)
    if {path.name for path in bundle.iterdir()} != {"data", "metadata", "manifest.json", "receipt.json"}:
        raise MaintenanceError("Unexpected top-level backup member")
    actual = manifest_members(bundle)
    if actual != sorted(expected.values(), key=lambda value: value["path"]):
        raise MaintenanceError("Payload member inventory/size/checksum mismatch")
    metadata = verify_bundle(bundle / "metadata")
    if Path(metadata["source_state"]).resolve() != Path(manifest["source_state"]).resolve():
        raise MaintenanceError("Metadata source state differs from the payload source")
    if metadata["database"] != manifest["metadata_database"]:
        raise MaintenanceError("Metadata database identity differs")
    if jobs_window(bundle / "metadata/data/research.sqlite3") != manifest["source_window"]["jobs"]:
        raise MaintenanceError("Metadata job snapshot differs from payload window")
    payload = [item for item in actual if item["kind"] == "file" and item["path"].startswith("data/")]
    if (len(payload), sum(item["bytes"] for item in payload)) != (manifest["payload_file_count"], manifest["payload_bytes"]):
        raise MaintenanceError("Declared payload file/byte counts differ")
    return manifest


def reference_checks(data: Path, source_state: Path, source_aliases: list[str] | None = None) -> dict:
    """Exercise known local dataset/checkpoint hash maps; do not load pickle/weights or execute code."""
    checked, datasets, skipped, external = 0, 0, 0, 0
    seen = set()
    source_roots = sorted({source_state, *(Path(value) for value in (source_aliases or []))}, key=lambda path: len(path.parts), reverse=True)

    def check(base: Path, name: str, expected: str) -> None:
        nonlocal checked
        candidate = base / relative(name)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise MaintenanceError("Restored local dataset/artifact reference is missing") from error
        if not isinstance(expected, str) or not SHA.fullmatch(expected) or not resolved.is_relative_to(data) or stream_file(resolved)[0] != expected:
            raise MaintenanceError("Restored dataset/artifact reference checksum failed")
        if (str(resolved), expected) not in seen:
            checked += 1
            seen.add((str(resolved), expected))

    def local_path(raw: str, base: Path) -> Path | None:
        nonlocal external
        candidate = Path(raw)
        if candidate.is_absolute():
            for source in source_roots:
                if candidate.is_relative_to(source):
                    return data / candidate.relative_to(source)
            external += 1
            return None
        return base / relative(raw)

    hash_bindings = 0
    unlocated_source_digests = 0
    source_snapshot_documents = 0
    generated_test_documents = 0
    detached_qualification_digests = 0

    def source_snapshot(path):
        for parent in path.parents:
            if parent==data:break
            if parent.parent.name=='releases' and re.fullmatch('[0-9a-f]{40}',parent.name):
                marker=parent/'RELEASE_COMMIT'
                if marker.is_file() and marker.read_text().strip()==parent.name:return True
            if parent.name=='implementation_sources':return True
        return False

    def checkpoint_references(value: dict, base: Path, *, library_binding: bool = False) -> None:
        nonlocal hash_bindings,unlocated_source_digests,detached_qualification_digests
        for key in ("files_sha256", "source_files_sha256", "artifact_sha256"):
            hashes = value.get(key)
            if isinstance(hashes, dict):
                for name, checksum in hashes.items():
                    path = local_path(name, base)
                    if path is not None:
                        if key=='source_files_sha256' and not Path(name).is_absolute() and not path.exists():
                            # Admission/report sidecars can retain source-relative
                            # digests while an aggregate's source_artifact_map
                            # locates the actual payload elsewhere. No locator
                            # is invented from the sidecar's directory.
                            if not isinstance(checksum,str) or not SHA.fullmatch(checksum):raise MaintenanceError('Invalid source digest metadata')
                            unlocated_source_digests += 1
                        elif not Path(name).is_absolute() and base.is_relative_to(data/'qualification') and not path.exists():
                            # Qualification summaries may be byte-identical
                            # copies of a result rooted at a different job.
                            # Relative digest names alone do not locate files.
                            if not isinstance(checksum,str) or not SHA.fullmatch(checksum):raise MaintenanceError('Invalid qualification digest metadata')
                            detached_qualification_digests += 1
                        else:check(path.parent, path.name, checksum)
        hashes = value.get("checkpoint_sha256")
        if isinstance(hashes, dict):
            if not isinstance(value.get('checkpoint_dir'),str):
                # A hash binding without a file locator is not a claim that a
                # checkpoint lives in this JSON document's directory. Keep it
                # explicitly unresolved rather than inventing student_act/.
                if not hashes or any(not isinstance(name,str) or not isinstance(checksum,str) or not SHA.fullmatch(checksum) for name,checksum in hashes.items()):
                    raise MaintenanceError('Invalid declarative library checkpoint digest binding')
                hash_bindings += 1
            else:
                directory = local_path(value["checkpoint_dir"], base)
                if directory is not None:
                    for name, checksum in hashes.items():
                        check(directory, name, checksum)
        if isinstance(value.get("checkpoint"), str) and isinstance(value.get("sha256"), str):
            path = local_path(value["checkpoint"], base)
            if path is not None:
                check(path.parent, path.name, value["sha256"])
        if isinstance(value.get("source_manifest_path"), str) and isinstance(value.get("source_manifest_sha256"), str):
            path = local_path(value["source_manifest_path"], base)
            if path is not None:
                check(path.parent, path.name, value["source_manifest_sha256"])
        for child_name,child in value.items():
            if isinstance(child, dict):
                checkpoint_references(child, base,library_binding=child_name=='library_binding')
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        checkpoint_references(item, base)

    for path in sorted(data.rglob("*.json")):
        if not path.is_symlink() and path.is_file():
            if path.relative_to(data).parts[0]=='qualification' and any(part.startswith('test_') for part in path.relative_to(data).parts[:-1]):
                # Generated qualification tests intentionally retain corrupt,
                # missing-reference and failure cases. Back up their exact
                # bytes; admitting those fixtures as healthy runtime datasets
                # is neither requested nor valid.
                generated_test_documents += 1
            elif source_snapshot(path):
                # A preserved executable source tree contains historical docs,
                # not new runtime datasets rooted in each document directory.
                source_snapshot_documents += 1
            elif path.stat().st_size > 32 * 1024 * 1024:
                skipped += 1
            else:
                try:
                    value = read_json(path, 32 * 1024 * 1024)
                except (ValueError, UnicodeError):
                    value = None
                    skipped += 1
                if isinstance(value, dict):
                    if value.get("format") in {"explorer_npz_episode_v1", "explorer_robot_episode_v1"}:
                        datasets += 1
                        for episode in value["episodes"]:
                            check(path.parent, episode["file"], episode["sha256"])
                    checkpoint_references(value, path.parent)
    return {"known_dataset_manifests_checked": datasets, "local_hashed_references_checked": checked,
            "checkpoint_digest_groups_without_file_locator": hash_bindings,
            "source_relative_digests_requiring_their_original_or_aggregate_locator": unlocated_source_digests,
            "preserved_source_snapshot_documents_not_reinterpreted_as_runtime_artifacts": source_snapshot_documents,
            "generated_qualification_test_documents_preserved_without_runtime_admission": generated_test_documents,
            "detached_qualification_relative_digest_entries_without_locator": detached_qualification_digests,
            "unparsed_or_large_json_files": skipped, "original_absolute_provenance_paths_retained": external,
            "arbitrary_model_deserialization_executed": False}


def restore_backup(bundle: Path, root: Path, destination: Path | None = None, *, apply: bool = False,
                   require_complete: bool = True) -> dict:
    bundle, root = bundle.resolve(), root.resolve()
    manifest = verify_backup(bundle, require_complete=require_complete)
    summary = {"verified": True, "mode": "verify_only", "live_data_changed": False,
               "payload_file_count": manifest["payload_file_count"], "payload_bytes": manifest["payload_bytes"],
               "test_restore_passed": False}
    if destination is not None:
        metadata = verify_bundle(bundle / "metadata")
        protected = [bundle, root, Path(manifest["source_state"]), Path(metadata["source_config"]),
                     Path(metadata["source_calibration"]), Path.home() / ".local/share/explorer",
                     Path.home() / ".config/explorer", Path.home() / ".cache/huggingface/lerobot", Path.home() / "Explorer"]
        protected.extend(Path(value) for value in manifest.get("source_state_aliases", []))
        destination = new_destination(destination, protected)
    if apply:
        if destination is None:
            raise MaintenanceError("Actual rehearsal needs an explicit new destination")
        private_directory(destination)
        try:
            for item in manifest["entries"]:
                path = destination / item["path"]
                if item["kind"] == "directory":
                    private_parents(path, destination)
                elif item["kind"] == "file":
                    private_parents(path.parent, destination)
                    checksum, size = stream_file(bundle / item["path"], path)
                    if (checksum, size) != (item["sha256"], item["bytes"]):
                        raise MaintenanceError("Restored payload checksum differs")
            # Links are last: no copied file is ever written through a link.
            for item in manifest["entries"]:
                if item["kind"] == "symlink":
                    (destination / item["path"]).symlink_to(item["target"])
            if manifest_members(destination) != manifest["entries"]:
                raise MaintenanceError("Independent restored-file readback checksum/inventory differs")
            metadata = verify_bundle(destination / "metadata")
            # Put only the backup-API snapshot in the isolated Store, never a copied live WAL.
            stream_file(destination / "metadata/data/research.sqlite3", destination / "data/research.sqlite3")
            summary["references"] = reference_checks(destination / "data", Path(manifest["source_state"]), manifest.get("source_state_aliases"))
            summary["store"] = validate_store_restore(destination / "data", root, metadata["database"])
            summary.update(mode="isolated_rehearsal", test_restore_passed=True, destination=str(destination),
                           all_restored_file_sha256_verified=True)
        except Exception as error:
            private_json(destination / "restore-receipt.json", {"test_restore_passed": False, "error_type": type(error).__name__, "live_data_changed": False})
            raise
        private_json(destination / "restore-receipt.json", summary)
        sync_directories(destination)
    return summary


def create_backup(state: Path, config: Path, calibration: Path, root: Path, destination: Path,
                  *, metadata_backup: Path | None = None) -> dict:
    state_alias = state.expanduser().absolute()
    state, config, calibration, root = (path.expanduser().resolve() for path in (state, config, calibration, root))
    protected = [state, config, calibration, root]
    if metadata_backup is not None:
        metadata_backup = metadata_backup.expanduser().resolve()
        protected.append(metadata_backup)
    destination = new_destination(destination, protected)
    started = time.time()
    initial_jobs = jobs_window(state / "research.sqlite3")
    private_directory(destination)
    receipt = {"schema": SCHEMA, "backup_directory": str(destination), "completed": False,
               "stage": "metadata", "source_writes_performed": False, "test_restore_passed": False,
               "payload_file_count": 0, "payload_bytes": 0}
    private_json(destination / "receipt.json", receipt)
    try:
        if metadata_backup is None:
            create_metadata_backup(state, config, calibration, root, destination / "metadata")
            metadata = verify_bundle(destination / "metadata")
        else:
            metadata = copy_metadata(metadata_backup, destination / "metadata")
        if Path(metadata["source_state"]).resolve() != state:
            raise MaintenanceError("Metadata snapshot belongs to another source state")
        if jobs_window(destination / "metadata/data/research.sqlite3") != initial_jobs:
            raise MaintenanceError("Metadata job snapshot does not match the current terminal jobs")
        unchanged_jobs(state / "research.sqlite3", initial_jobs)
        sources, missing = inventory(state)
        private_directory(destination / "data")
        receipt["stage"] = "copy_payloads"
        replace_private_json(destination / "receipt.json", receipt)
        source_hashes = {}
        next_jobs_check = time.monotonic()
        for name, item in sources.items():
            source, target = state / name, destination / "data" / name
            if item["kind"] == "directory":
                private_parents(target, destination / "data")
            elif item["kind"] == "file":
                if time.monotonic() >= next_jobs_check:
                    unchanged_jobs(state / "research.sqlite3", initial_jobs)
                    next_jobs_check = time.monotonic() + 1
                checksum, size = stream_file(source, target, item["identity"])
                source_hashes[name] = checksum
                receipt["payload_file_count"] += 1
                receipt["payload_bytes"] += size
        for name, item in sources.items():
            if item["kind"] == "symlink":
                target = destination / "data" / name
                target.symlink_to(os.path.relpath(destination / "data" / item["target"], target.parent))
        receipt["stage"] = "final_source_validation"
        replace_private_json(destination / "receipt.json", receipt)
        if inventory(state) != (sources, missing):
            raise MaintenanceError("Final source inventory changed during backup")
        for name, checksum in source_hashes.items():
            if stream_file(state / name, expected=sources[name]["identity"])[0] != checksum:
                raise MaintenanceError("Final source checksum changed during backup")
        unchanged_jobs(state / "research.sqlite3", initial_jobs)
        if inventory(state) != (sources, missing):
            raise MaintenanceError("Source changed during final checksum verification")
        window_end = time.time()
        entries = manifest_members(destination)
        manifest = {"schema": SCHEMA, "source_state": str(state), "source_state_aliases": sorted({str(state), str(state_alias)}), "created_at_unix": window_end,
                    "source_window": {"start_unix": started, "end_unix": window_end, "jobs": initial_jobs,
                                      "all_jobs_terminal_and_unchanged": True, "source_files_rehashed": True,
                                      "atomic_filesystem_snapshot": False, "cross_host_atomic": False,
                                      "events_may_arrive": True, "metadata_snapshot_at": metadata["created_at_unix"]},
                    "selected_directories": list(PAYLOAD_DIRS), "absent_directories": missing,
                    "payload_file_count": receipt["payload_file_count"], "payload_bytes": receipt["payload_bytes"],
                    "source_inventory": sources, "metadata_database": metadata["database"], "entries": entries,
                    "scope": "All regular payload bytes in six selected local state directories plus backup-API metadata/private files. No extension exclusions.",
                    "not_included": ["remote Pi/Nano files not already retained locally", "source checkout", "virtual environments", "operating system", "other unselected state directories"],
                    "hardlinks": "Copied as independent byte-identical regular files; source inode/link identity recorded.",
                    "symlinks": "Internal selected targets only; portable relative links restored after regular files."}
        private_json(destination / "manifest.json", manifest)
        receipt.update(manifest_sha256=stream_file(destination / "manifest.json")[0], stage="isolated_restore")
        replace_private_json(destination / "receipt.json", receipt)
        with tempfile.TemporaryDirectory(prefix="explorer-payload-check-", dir=destination.parent) as temporary:
            temporary_path = Path(temporary)
            temporary_path.chmod(0o700)
            result = restore_backup(destination, root, temporary_path / "rehearsal", apply=True, require_complete=False)
        # Revalidate receipt/manifest payload bytes after the real independent copy and Store opening.
        verify_backup(destination, require_complete=False)
        receipt.update(completed=True, stage="completed", test_restore_passed=result["test_restore_passed"],
                       artifact_payloads_included=True,
                       references=result["references"], store=result["store"],
                       all_restored_file_sha256_verified=result["all_restored_file_sha256_verified"],
                       total_regular_files=sum(item["kind"] == "file" for item in entries),
                       total_bytes=sum(item.get("bytes", 0) for item in entries))
        replace_private_json(destination / "receipt.json", receipt)
        sync_directories(destination)
        return receipt
    except Exception as error:
        receipt.update(completed=False, error_type=type(error).__name__, failed_stage=receipt["stage"], stage="incomplete")
        receipt["reason"] = str(error) if isinstance(error, MaintenanceError) else "A bounded file/database operation failed; no private source contents are printed."
        replace_private_json(destination / "receipt.json", receipt)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    create = modes.add_parser("create", help="Copy full local payloads and actually rehearse restore")
    create.add_argument("--state", type=Path)
    create.add_argument("--config", type=Path, default=Path.home() / ".config/explorer")
    create.add_argument("--calibration", type=Path, default=Path.home() / ".cache/huggingface/lerobot/calibration")
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--metadata-backup", type=Path, help="Reuse a verified same-state atomic metadata snapshot")
    verify = modes.add_parser("verify", help="Hash/verify every member; no restore by default")
    verify.add_argument("--backup", type=Path, required=True)
    restore = modes.add_parser("restore", help="Verify by default; --apply only into a new isolated destination")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--destination", type=Path)
    restore.add_argument("--apply", action="store_true")
    for command in (create, verify, restore):
        command.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        if args.mode == "create":
            result = create_backup(selected_state(args.config, args.state), args.config, args.calibration, args.root,
                                   args.destination, metadata_backup=args.metadata_backup)
        else:
            result = restore_backup(args.backup, args.root, getattr(args, "destination", None), apply=getattr(args, "apply", False))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (MaintenanceError, OSError, ValueError, TypeError, KeyError, ImportError, sqlite3.Error) as error:
        print(json.dumps({"completed": False, "error_type": type(error).__name__,
                          "detail": "Full payload operation refused or incomplete; originals/live data were not changed. Inspect the private receipt if a destination was created."}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
