#!/usr/bin/env python3
"""ENGINEERING_GLUE: archive, restore-check and remove an inventoried legacy stack.

Dry-run is the default. Run locally on the selected host; this never uses SSH,
opens a device, changes controllers/networks, or manages system-wide services.
Preserve the complete private archive directory: its manifest, tar and Git
bundles together constitute the checked source/config backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid


ARCHIVE_DIRECTORY = ".local/state/explorer/legacy-archive-20261026"
DIRECTORIES = {
    "pi": ("MILO_Final", "MILO_Next"),
    "homebrain": ("homebrain", ".openclaw", "llama.cpp", ".local/opt/hue",
                  ".config/hue", ".local/src/whisper.cpp"),
}
UNITS = {
    "pi": ("milo-final-agent.service", "milo-final-edge.service", "milo-final-vlm.service",
           "milo-next-agent.service", "milo-next-edge.service", "milo-final.target", "milo-next.target"),
    "homebrain": ("homebrain-llama.service", "hue-audio-hub.service", "hue-brain.service",
                  "hue-dialogue.service", "hue-llm.service", "hue-perception.service",
                  "hue-stt.service", "hue-voice.service", "openclaw-gateway.service"),
}
EXPECTED_HOMES = {"pi": Path("/home/vlados"), "homebrain": Path("/home/jetsonnx")}
GENERATED_DIRECTORIES = {".venv", "venv", "voice-venv", "__pycache__", ".pytest_cache",
                         "node_modules", "cache", "build", "build-hue", "dist"}
MODEL_SUFFIXES = {".gguf", ".ggml", ".onnx", ".hef", ".pt", ".pth", ".safetensors",
                  ".ckpt", ".tflite", ".pb", ".whl"}
MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".wav", ".mp3", ".flac", ".m4a",
                  ".jpg", ".jpeg", ".png", ".webp", ".gif", ".mcap"}
COMPILED_SUFFIXES = {".so", ".a", ".o", ".pyc", ".elf", ".hex"}


class CleanupError(RuntimeError):
    """A prerequisite failed; error text must never include file contents."""

    def __init__(self, message: str, receipt: dict | None = None):
        super().__init__(message)
        self.receipt = receipt


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command(arguments: list[str], label: str, *, cwd: Path | None = None) -> bytes:
    result = subprocess.run(arguments, cwd=cwd, capture_output=True,
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if result.returncode:
        raise CleanupError(label + " failed; command output withheld")
    return result.stdout


def exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def check_ancestors(home: Path, path: Path) -> None:
    try:
        relative = path.relative_to(home)
    except ValueError as error:
        raise CleanupError("Candidate is outside the selected home") from error
    current = home
    if home.is_symlink():
        raise CleanupError("Home must not be a symlink")
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise CleanupError("A candidate ancestor is a symlink")


def guarded_completion(home: Path) -> str:
    path = home / ".openclaw/completions/openclaw.bash"
    return f"[ -f '{path}' ] && source '{path}'"


def build_plan(home: Path, node: str) -> dict:
    home = Path(home).absolute()
    candidates = []
    for relative in DIRECTORIES[node]:
        path = home / relative
        check_ancestors(home, path)
        if exists(path):
            if path.is_symlink() or not path.is_dir():
                raise CleanupError("An inventoried directory changed type")
            candidates.append({"relative": relative, "kind": "directory"})
    for name in UNITS[node]:
        relative = ".config/systemd/user/" + name
        path = home / relative
        check_ancestors(home, path)
        if exists(path):
            if path.is_symlink() or not path.is_file():
                raise CleanupError("An inventoried user unit changed type")
            candidates.append({"relative": relative, "kind": "unit", "unit": name})
    if node == "homebrain":
        link = home / ".local/bin/hue"
        check_ancestors(home, link)
        if exists(link):
            if not link.is_symlink() or os.readlink(link) != str(home / ".local/opt/hue/venv/bin/hue"):
                raise CleanupError("The inventoried hue launcher changed target")
            candidates.append({"relative": ".local/bin/hue", "kind": "launcher"})
    bashrc = home / ".bashrc"
    patch_shell = False
    if node == "homebrain" and bashrc.is_file():
        check_ancestors(home, bashrc)
        if bashrc.is_symlink():
            raise CleanupError("Shell configuration must not be a symlink")
        lines = bashrc.read_bytes().splitlines()
        expected = guarded_completion(home).encode()
        matching = [line for line in lines if b".openclaw/completions/openclaw.bash" in line]
        if matching and matching != [expected]:
            raise CleanupError("Obsolete shell completion differs from the exact inventoried guard")
        patch_shell = matching == [expected]
    return {"home": str(home), "node": node, "candidates": candidates, "patch_shell": patch_shell}


def metadata(path: Path, relative: str) -> dict:
    info = path.lstat()
    if stat.S_ISREG(info.st_mode):
        kind = "file"
    elif stat.S_ISDIR(info.st_mode):
        kind = "directory"
    elif stat.S_ISLNK(info.st_mode):
        kind = "symlink"
    else:
        raise CleanupError("Unexpected special file in legacy content")
    result = dict(path=relative, kind=kind, size=info.st_size if kind != "directory" else 0,
                  mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid,
                  mtime_ns=info.st_mtime_ns, inode=info.st_ino, device=info.st_dev)
    if kind == "symlink":
        result["target"] = os.readlink(path)
    return result


def exclusion(path: Path, relative: str, kind: str) -> str | None:
    parts = PurePosixPath(relative).parts
    if ".git" in parts:
        return "git_history_saved_as_bundle"
    if kind == "directory" and path.name in GENERATED_DIRECTORIES:
        return "generated_environment_build_or_cache"
    suffix = path.suffix.lower()
    if kind == "file":
        if suffix == ".pth" and path.stat().st_size < 4096:
            sample = path.read_bytes()
            if b"\x00" not in sample:
                try:
                    sample.decode("utf-8")
                    return None
                except UnicodeDecodeError:
                    pass
        if suffix in MODEL_SUFFIXES or (suffix == ".bin" and ("models" in parts or path.name.startswith("ggml-"))):
            return "model_checkpoint_or_package_binary"
        if suffix in MEDIA_SUFFIXES:
            return "media_binary"
        if suffix in COMPILED_SUFFIXES:
            return "generated_compiled_binary"
    return None


def all_entries(path: Path, home: Path):
    relative = path.relative_to(home).as_posix()
    item = metadata(path, relative)
    yield path, item
    if item["kind"] == "directory":
        for child in sorted(path.iterdir(), key=lambda p: p.name):
            yield from all_entries(child, home)


def inventory(plan: dict) -> tuple[list[dict], list[dict], list[str], int]:
    home = Path(plan["home"])
    included, excluded, repositories = [], [], []
    total_bytes = 0

    def visit(path: Path):
        nonlocal total_bytes
        relative = path.relative_to(home).as_posix()
        item = metadata(path, relative)
        reason = exclusion(path, relative, item["kind"])
        if reason:
            rows = [row for _, row in all_entries(path, home)]
            size = sum(row["size"] for row in rows)
            excluded.append(dict(path=relative, reason=reason, bytes=size, entries=len(rows)))
            total_bytes += size
            if path.name == ".git":
                repositories.append(path.parent.relative_to(home).as_posix())
                retained = []
                if item["kind"] == "directory":
                    for name in ("config", "HEAD", "description"):
                        git_metadata = path / name
                        if git_metadata.is_file() and not git_metadata.is_symlink():
                            row = metadata(git_metadata, git_metadata.relative_to(home).as_posix())
                            included.append(row)
                            retained.append(row)
                excluded[-1]["retained_metadata"] = [row["path"] for row in retained]
                excluded[-1]["bytes"] -= sum(row["size"] for row in retained)
                excluded[-1]["entries"] -= len(retained)
        else:
            total_bytes += item["size"]
            included.append(item)
            if item["kind"] == "directory":
                for child in sorted(path.iterdir(), key=lambda p: p.name):
                    visit(child)

    for candidate in plan["candidates"]:
        visit(home / candidate["relative"])
    if plan["patch_shell"]:
        included.append(metadata(home / ".bashrc", ".bashrc"))
    return included, excluded, repositories, total_bytes


def assert_inactive_units(plan: dict) -> None:
    for row in plan["candidates"]:
        if row["kind"] == "unit":
            data = command(["systemctl", "--user", "show", row["unit"], "--no-pager",
                            "--property=ActiveState,UnitFileState"], "User unit inspection").decode()
            values = dict(line.split("=", 1) for line in data.splitlines() if "=" in line)
            if values.get("ActiveState") not in {"inactive", "failed"} or values.get("UnitFileState") != "disabled":
                raise CleanupError("A removal candidate is not an inactive disabled user unit")


def assert_no_consumers(plan: dict) -> None:
    home = Path(plan["home"])
    roots = [str(home / row["relative"]) for row in plan["candidates"] if row["kind"] == "directory"]

    def refers(text: str) -> bool:
        return any(re.search(re.escape(root) + r"(?=/|$|[\s'\"])", text) for root in roots)

    for proc in Path("/proc").iterdir():
        if proc.name.isdigit() and int(proc.name) != os.getpid():
            references = []
            try:
                references.append((proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace"))
                references.append((proc / "maps").read_text(errors="replace"))
                references.extend(os.readlink(proc / name) for name in ("exe", "cwd"))
                references.extend(os.readlink(path) for path in (proc / "fd").iterdir())
            except (OSError, PermissionError):
                pass
            if any(refers(value) for value in references):
                raise CleanupError("An active process still consumes legacy content (PID " + proc.name + ")")


def write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def sync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_receipt(destination: Path, receipt: dict) -> None:
    temporary = destination / (".receipt-" + uuid.uuid4().hex)
    write_private(temporary, json.dumps(receipt, indent=2).encode() + b"\n")
    os.replace(temporary, destination / "receipt.json")
    sync_path(destination)


def git_identity(repo: Path) -> dict:
    head = command(["git", "rev-parse", "HEAD"], "Git HEAD inspection", cwd=repo).decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise CleanupError("Git HEAD is not a commit identity")
    refs = command(["git", "for-each-ref", "--format=%(refname) %(objectname)"],
                   "Git reference inspection", cwd=repo).decode().splitlines()
    return dict(head=head, refs=sorted(refs))


def verify_git_sources(home: Path, bundles: list[dict]) -> None:
    for row in bundles:
        if git_identity(home / row["repository"]) != row["source_identity"]:
            raise CleanupError("Git HEAD or references changed after the history backup")


def git_bundles(home: Path, destination: Path, repositories: list[str]) -> list[dict]:
    result = []
    for index, relative in enumerate(repositories):
        repo = home / relative
        identity = git_identity(repo)
        path = destination / ("history-" + str(index) + ".bundle")
        command(["git", "bundle", "create", str(path), "--all", "HEAD"], "Git bundle creation", cwd=repo)
        path.chmod(0o600)
        command(["git", "bundle", "verify", str(path)], "Git bundle verification", cwd=repo)
        sync_path(path)
        if git_identity(repo) != identity:
            raise CleanupError("Git references changed while making the history backup")
        result.append(dict(repository=relative, file=path.name, head=identity["head"], source_identity=identity,
                           sha256=sha256(path), bytes=path.stat().st_size))
    return result


def unchanged(path: Path, item: dict, *, content: bool = False) -> None:
    current = metadata(path, item["path"])
    keys = ("kind", "size", "mode", "mtime_ns", "inode", "device")
    if any(current[key] != item[key] for key in keys) or current.get("target") != item.get("target"):
        raise CleanupError("Legacy source changed after archive inventory")
    if content and item["kind"] == "file" and sha256(path) != item["sha256"]:
        raise CleanupError("Legacy source content changed after its checked backup")


def create_archive(home: Path, destination: Path, entries: list[dict]) -> Path:
    path = destination / "source-config.tar.gz"
    with tarfile.open(path, "w:gz", dereference=False) as bundle:
        for item in entries:
            source = home / item["path"]
            unchanged(source, item)
            info = bundle.gettarinfo(str(source), arcname="payload/" + item["path"])
            if item["kind"] == "file":
                info.type, info.linkname, info.size = tarfile.REGTYPE, "", item["size"]
                descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    digest = hashlib.sha256()
                    contents = tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024)
                    with contents:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                            contents.write(block)
                        if contents.tell() != item["size"]:
                            raise CleanupError("Legacy source size changed while archiving")
                        contents.seek(0)
                        bundle.addfile(info, contents)
                    item["sha256"] = digest.hexdigest()
                unchanged(source, item)
            else:
                bundle.addfile(info)
    path.chmod(0o600)
    sync_path(path)
    return path


def verify_restore(archive: Path, entries: list[dict], bundles: list[dict], destination: Path) -> None:
    expected = {"payload/" + row["path"]: row for row in entries}
    with tempfile.TemporaryDirectory(prefix="restore-check-", dir=destination) as temporary:
        restore = Path(temporary)
        seen = set()
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle:
                if member.name not in expected or member.name in seen:
                    raise CleanupError("Archive has an unexpected or duplicate member")
                item = expected[member.name]
                parts = PurePosixPath(member.name)
                if parts.is_absolute() or ".." in parts.parts:
                    raise CleanupError("Unsafe archive path")
                target = restore.joinpath(*parts.parts)
                parent = target.parent
                if any(ancestor.is_symlink() for ancestor in (target, *target.parents) if ancestor != restore.parent):
                    raise CleanupError("Archive would restore through a symlink")
                parent.mkdir(parents=True, exist_ok=True)
                if item["kind"] == "directory" and member.isdir():
                    target.mkdir(exist_ok=True)
                elif item["kind"] == "symlink" and member.issym() and member.linkname == item["target"]:
                    target.symlink_to(member.linkname)
                    if os.readlink(target) != item["target"]:
                        raise CleanupError("Restored symlink metadata differs")
                elif item["kind"] == "file" and member.isfile():
                    source = bundle.extractfile(member)
                    if source is None:
                        raise CleanupError("Archive file cannot be read")
                    with source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(item["mode"] & 0o777)
                    if sha256(target) != item["sha256"] or target.stat().st_size != item["size"]:
                        raise CleanupError("Restored source checksum differs")
                else:
                    raise CleanupError("Archive member type differs from inventory")
                if member.mode != item["mode"] or member.uid != item["uid"] or member.gid != item["gid"]:
                    raise CleanupError("Archive permission/ownership metadata differs")
                seen.add(member.name)
        if seen != set(expected):
            raise CleanupError("Archive omits an expected source member")
        for index, row in enumerate(bundles):
            path = destination / row["file"]
            if sha256(path) != row["sha256"]:
                raise CleanupError("Git bundle checksum differs")
            clone = restore / ("git-restore-" + str(index))
            command(["git", "clone", "--bare", str(path), str(clone)], "Git history restore")
            command(["git", "cat-file", "-e", row["head"] + "^{commit}"], "Restored Git commit verification", cwd=clone)


def patch_completion(home: Path, original: bytes, destination: Path) -> bool:
    path = home / ".bashrc"
    if path.read_bytes() != original:
        raise CleanupError("Shell configuration changed after backup")
    expected = guarded_completion(home).encode()
    lines = original.splitlines(keepends=True)
    matching = [line for line in lines if line.rstrip(b"\r\n") == expected]
    if len(matching) != 1:
        raise CleanupError("Exact obsolete completion guard is not unique")
    write_private(destination / "bashrc.before", original)
    updated = b"".join(line for line in lines if line.rstrip(b"\r\n") != expected)
    descriptor, temporary = tempfile.mkstemp(prefix=".bashrc.cleanup-", dir=home)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def cleanup(home: Path, node: str, *, apply: bool = False) -> dict:
    plan = build_plan(home, node)
    home = Path(plan["home"])
    entries, excluded, repositories, total_bytes = inventory(plan)
    receipt = dict(node=node, dry_run=not apply, candidate_paths=len(plan["candidates"]),
                   candidate_bytes=total_bytes, archived_entries=len(entries), excluded_groups=len(excluded),
                   excluded_bytes=sum(row["bytes"] for row in excluded), git_repositories=len(repositories),
                   shell_completion_change=plan["patch_shell"], test_restore_passed=False)
    if not apply:
        receipt["paths"] = [row["relative"] for row in plan["candidates"]]
        return receipt
    assert_inactive_units(plan)
    assert_no_consumers(plan)
    os.umask(0o077)
    base = home / ARCHIVE_DIRECTORY
    check_ancestors(home, base)
    if base.is_symlink():
        raise CleanupError("Archive directory must not be a symlink")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    destination = base / (node + "-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8])
    destination.mkdir(mode=0o700)
    bundles = git_bundles(home, destination, repositories)
    archive = create_archive(home, destination, entries)
    verify_restore(archive, entries, bundles, destination)
    receipt.update(archive_directory=str(destination), archive=str(archive),
                   archive_sha256=sha256(archive), archive_bytes=archive.stat().st_size,
                   git_bundle_bytes=sum(row["bytes"] for row in bundles), test_restore_passed=True)
    manifest = dict(schema_version=1, method_id="ENGINEERING_GLUE", plan=plan, entries=entries,
                    excluded=excluded, git_bundles=bundles, receipt=receipt)
    write_private(destination / "manifest.json", json.dumps(manifest, indent=2).encode() + b"\n")
    sync_path(destination)
    sync_path(base)
    receipt.update(removed_paths=0, shell_completion_changed=False, completed=False)
    save_receipt(destination, receipt)
    try:
        for item in entries:
            unchanged(home / item["path"], item, content=True)
        verify_git_sources(home, bundles)
        assert_inactive_units(plan)
        assert_no_consumers(plan)
        if plan["patch_shell"]:
            with tarfile.open(archive, "r:gz") as bundle:
                source = bundle.extractfile("payload/.bashrc")
                if source is None:
                    raise CleanupError("Original shell configuration is missing from the archive")
                with source:
                    original = source.read()
            patch_completion(home, original, destination)
            receipt["shell_completion_changed"] = True
            save_receipt(destination, receipt)
        for row in plan["candidates"]:
            if row["kind"] == "unit":
                (home / row["relative"]).unlink()
                receipt["removed_paths"] += 1
                save_receipt(destination, receipt)
        if any(row["kind"] == "unit" for row in plan["candidates"]):
            command(["systemctl", "--user", "daemon-reload"], "User service reload")
        for row in plan["candidates"]:
            path = home / row["relative"]
            check_ancestors(home, path)
            if row["kind"] == "directory":
                if path.is_symlink():
                    raise CleanupError("Candidate changed into a symlink before removal")
                for git_bundle in bundles:
                    repository = home / git_bundle["repository"]
                    if repository == path or path in repository.parents:
                        verify_git_sources(home, [git_bundle])
                shutil.rmtree(path)
                receipt["removed_paths"] += 1
                save_receipt(destination, receipt)
            elif row["kind"] == "launcher":
                path.unlink()
                receipt["removed_paths"] += 1
                save_receipt(destination, receipt)
    except (CleanupError, OSError, tarfile.TarError) as error:
        raise CleanupError("Cleanup stopped after archiving; inspect the private receipt", receipt) from error
    receipt["completed"] = True
    receipt["estimated_net_bytes_reclaimed"] = total_bytes - receipt["archive_bytes"] - receipt["git_bundle_bytes"]
    save_receipt(destination, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", choices=sorted(DIRECTORIES), required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Archive, restore-check, then remove only allowlisted legacy paths")
    mode.add_argument("--dry-run", action="store_true", help="Inventory only (the default)")
    args = parser.parse_args()
    try:
        home = Path.home().absolute()
        if args.apply and (home != EXPECTED_HOMES[args.node] or os.getuid() == 0):
            raise CleanupError("Apply must run as the inventoried non-root account on its selected host")
        receipt = cleanup(home, args.node, apply=args.apply)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (CleanupError, OSError, tarfile.TarError) as error:
        message = str(error) if isinstance(error, CleanupError) else "Filesystem/archive operation failed; details withheld"
        receipt = error.receipt if isinstance(error, CleanupError) and error.receipt else {}
        print(json.dumps(dict(receipt, node=args.node, error=message, completed=False)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
