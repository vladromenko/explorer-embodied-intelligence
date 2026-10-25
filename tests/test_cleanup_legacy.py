"""Exercise destructive cleanup only against isolated temporary homes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


spec = importlib.util.spec_from_file_location(
    "cleanup_legacy", Path(__file__).resolve().parents[1] / "scripts/cleanup_legacy.py")
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def put(path, content=b"source"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def mock_runtime(monkeypatch):
    monkeypatch.setattr(cleanup, "assert_inactive_units", lambda plan: None)
    monkeypatch.setattr(cleanup, "assert_no_consumers", lambda plan: None)


def test_default_dry_run_does_not_write_archive_or_modify_sources(tmp_path):
    home = tmp_path / "home"
    source = put(home / "MILO_Final/module.py")
    put(home / "MILO_Final/.venv/package.py", b"generated")
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    result = cleanup.cleanup(home, "pi")
    assert result["dry_run"] and not result["test_restore_passed"]
    assert result["excluded_bytes"] == len(b"generated")
    assert source.read_bytes() == b"source"
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*")) == before
    assert not (home / cleanup.ARCHIVE_DIRECTORY).exists()


def test_apply_archives_restores_and_preserves_git_history_and_external_links(tmp_path, monkeypatch):
    mock_runtime(monkeypatch)
    home = tmp_path / "home"
    repo = home / "MILO_Next"
    put(repo / "module.py", b"print('legacy')\n")
    put(repo / "config/settings.json", b'{"private":"sentinel-do-not-print"}')
    put(repo / "vendor/imports.pth", b"import legacy\n")
    put(repo / ".venv/package.py", b"generated")
    put(repo / "assets/models/old.onnx", b"model")
    put(repo / "data/old.mp4", b"media")
    protected = put(home / ".local/share/explorer/state/keep.txt", b"explorer")
    calibration = put(home / ".cache/huggingface/lerobot/calibration/keep.json", b"calibration")
    (repo / "external-link").symlink_to(protected)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "module.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "source"], check=True)
    result = cleanup.cleanup(home, "pi", apply=True)
    assert result["test_restore_passed"] and result["removed_paths"] == 1
    assert not repo.exists()
    assert protected.read_bytes() == b"explorer"
    assert calibration.read_bytes() == b"calibration"
    archive = Path(result["archive"])
    assert cleanup.sha256(archive) == result["archive_sha256"]
    assert archive.stat().st_mode & 0o777 == 0o600
    assert archive.parent.stat().st_mode & 0o777 == 0o700
    manifest = json.loads((archive.parent / "manifest.json").read_text())
    assert manifest["git_bundles"][0]["head"]
    names = {item["path"] for item in manifest["entries"]}
    assert "MILO_Next/config/settings.json" in names
    assert "MILO_Next/.git/config" in names
    assert "MILO_Next/vendor/imports.pth" in names
    assert "MILO_Next/external-link" in names
    assert "sentinel-do-not-print" not in json.dumps(result)
    excluded = {item["path"] for item in manifest["excluded"]}
    assert {"MILO_Next/.venv", "MILO_Next/assets/models/old.onnx", "MILO_Next/data/old.mp4"} <= excluded


def test_corrupt_archive_blocks_all_deletions(tmp_path, monkeypatch):
    mock_runtime(monkeypatch)
    home = tmp_path / "home"
    source = put(home / "MILO_Final/module.py")
    original = cleanup.create_archive

    def corrupt(*args):
        archive = original(*args)
        archive.write_bytes(b"not a tar archive")
        return archive

    monkeypatch.setattr(cleanup, "create_archive", corrupt)
    with pytest.raises(tarfile.TarError):
        cleanup.cleanup(home, "pi", apply=True)
    assert source.read_bytes() == b"source"


def test_only_exact_guarded_completion_line_is_removed_and_saved(tmp_path, monkeypatch):
    mock_runtime(monkeypatch)
    home = tmp_path / "home"
    put(home / ".openclaw/completions/openclaw.bash", b"completion")
    put(home / ".local/opt/hue/venv/bin/hue", b"generated")
    link = home / ".local/bin/hue"
    link.parent.mkdir(parents=True)
    link.symlink_to(home / ".local/opt/hue/venv/bin/hue")
    original = ("export EXAMPLE=preserve\n" + cleanup.guarded_completion(home) + "\n# keep\n").encode()
    put(home / ".bashrc", original)
    result = cleanup.cleanup(home, "homebrain", apply=True)
    assert (home / ".bashrc").read_bytes() == b"export EXAMPLE=preserve\n# keep\n"
    destination = Path(result["archive"]).parent
    assert (destination / "bashrc.before").read_bytes() == original
    assert (destination / "bashrc.before").stat().st_mode & 0o777 == 0o600
    assert not link.is_symlink()


def test_modified_shell_guard_or_candidate_symlink_is_refused(tmp_path):
    home = tmp_path / "home"
    put(home / ".bashrc", b"source ~/.openclaw/completions/openclaw.bash\n")
    with pytest.raises(cleanup.CleanupError, match="exact inventoried guard"):
        cleanup.build_plan(home, "homebrain")
    outside = tmp_path / "protected"
    outside.mkdir()
    (home / "MILO_Final").symlink_to(outside)
    with pytest.raises(cleanup.CleanupError, match="changed type"):
        cleanup.build_plan(home, "pi")


def test_active_units_block_cleanup_before_archive(tmp_path, monkeypatch):
    home = tmp_path / "home"
    source = put(home / "MILO_Final/module.py")
    put(home / ".config/systemd/user/milo-final-edge.service")
    monkeypatch.setattr(cleanup, "command", lambda *args, **kwargs: b"ActiveState=active\nUnitFileState=disabled\n")
    with pytest.raises(cleanup.CleanupError, match="inactive disabled"):
        cleanup.cleanup(home, "pi", apply=True)
    assert source.exists()
    assert not (home / cleanup.ARCHIVE_DIRECTORY).exists()


def test_checksum_mismatch_and_source_changes_block_removal(tmp_path, monkeypatch):
    mock_runtime(monkeypatch)
    home = tmp_path / "home"
    source = put(home / "MILO_Final/module.py", b"original")
    plan = cleanup.build_plan(home, "pi")
    entries, _, _, _ = cleanup.inventory(plan)
    destination = tmp_path / "archive"
    destination.mkdir()
    archive = cleanup.create_archive(home, destination, entries)
    replacement = destination / "changed.tar.gz"
    with tarfile.open(archive) as original, tarfile.open(replacement, "w:gz") as changed:
        for member in original:
            if member.isfile():
                changed.addfile(member, io.BytesIO(b"modified"))
            else:
                changed.addfile(member)
    with pytest.raises(cleanup.CleanupError, match="checksum"):
        cleanup.verify_restore(replacement, entries, [], destination)
    assert source.read_bytes() == b"original"
    verify = cleanup.verify_restore

    def modify_after_restore(*args):
        verify(*args)
        source.write_bytes(b"new data")

    monkeypatch.setattr(cleanup, "verify_restore", modify_after_restore)
    with pytest.raises(cleanup.CleanupError) as failure:
        cleanup.cleanup(home, "pi", apply=True)
    assert failure.value.receipt["test_restore_passed"]
    assert failure.value.receipt["removed_paths"] == 0
    assert not failure.value.receipt["completed"]
    assert source.read_bytes() == b"new data"


def test_preserved_timestamp_does_not_hide_changed_content(tmp_path):
    path = put(tmp_path / "module.py", b"original")
    item = cleanup.metadata(path, "module.py")
    item["sha256"] = cleanup.sha256(path)
    path.write_bytes(b"modified")
    os.utime(path, ns=(path.stat().st_atime_ns, item["mtime_ns"]))
    with pytest.raises(cleanup.CleanupError, match="content changed"):
        cleanup.unchanged(path, item, content=True)


def test_new_git_ref_after_bundle_blocks_removal(tmp_path, monkeypatch):
    mock_runtime(monkeypatch)
    home = tmp_path / "home"
    repo = home / "MILO_Next"
    source = put(repo / "module.py")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "module.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "source"], check=True)
    verify = cleanup.verify_restore

    def add_ref_after_restore(*args):
        verify(*args)
        subprocess.run(["git", "-C", str(repo), "tag", "new-after-bundle"], check=True)

    monkeypatch.setattr(cleanup, "verify_restore", add_ref_after_restore)
    with pytest.raises(cleanup.CleanupError) as failure:
        cleanup.cleanup(home, "pi", apply=True)
    assert failure.value.receipt["removed_paths"] == 0
    assert source.exists()


def test_unborn_git_preserved_completely_without_fabricated_bundle(tmp_path, monkeypatch):
    mock_runtime(monkeypatch)
    home = tmp_path / "home"
    repo = home / "MILO_Next"
    put(repo / "module.py")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    raw = b"\x00exact unborn Git bytes\xff"
    put(repo / ".git/custom-state.bin", raw)
    result = cleanup.cleanup(home, "pi", apply=True)
    assert result["test_restore_passed"]
    assert not repo.exists()
    destination = Path(result["archive_directory"])
    manifest = json.loads((destination / "manifest.json").read_text())
    history = manifest["git_bundles"][0]
    assert history["head"] is None and history["file"] is None
    assert history["storage"] == "complete_git_directory_in_source_archive"
    assert not list(destination.glob("*.bundle"))
    with tarfile.open(result["archive"]) as archive:
        assert archive.extractfile("payload/MILO_Next/.git/custom-state.bin").read() == raw
