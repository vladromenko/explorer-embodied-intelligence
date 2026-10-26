"""ENGINEERING_GLUE: real byte copies/SQLite/isolated restores; no devices or network."""
import json
import os
from pathlib import Path
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "00-control-center/src"), str(ROOT / "shared")]
import backup_payloads as payloads
import backup_state
from maintenance_common import MaintenanceError, read_json, replace_private_json
from explorer.store import Store


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
    return path


def setup_state(tmp_path, monkeypatch, *, job_status="completed"):
    state = tmp_path / "live"
    store = Store(state)
    job = store.create_job("train_act", "01", {})
    store.update_job(job["id"], status=job_status, result={"fixture": True})
    config = tmp_path / "config"
    put(config / "config.json", json.dumps({"data_dir": str(state), "ingest_token": "PRIVATE_TEST_TOKEN"}))
    calibration = tmp_path / "calibration"
    put(calibration / "original.json", '{"original":true}')
    monkeypatch.setattr(backup_state, "collect_environment", lambda: {"fixture": True})
    monkeypatch.setattr(backup_state, "source_inventory", lambda root: {"fixture": True})
    npz = put(state / "datasets/recorded/episode_0000.npz", os.urandom(1024 * 1024 + 17))
    sha = payloads.stream_file(npz)[0]
    put(state / "datasets/recorded/manifest.json", json.dumps({"format": "explorer_robot_episode_v1", "episodes": [
        {"id": "episode_0000", "file": npz.name, "sha256": sha, "split": "train"}]}))
    put(state / "jobs/finished/model.safetensors", os.urandom(1024 * 1024 + 11))
    put(state / "jobs/finished/optimizer.pt", b"opaque optimizer bytes; never deserialize")
    put(state / "episodes/native/frame.jpg", b"opaque retained original RGB JPEG")
    put(state / "artifacts/export/data.parquet", b"opaque data payload")
    put(state / "assets/meshes/link.STL", b"actual source bytes, no extension exclusions")
    put(state / "qualification/act/result.json", '{"weights_changed":true}')
    (state / "artifacts/empty").mkdir()
    return state, config, calibration, store, job["id"]


def create(tmp_path, monkeypatch, **kwargs):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    destination = tmp_path / "backup"
    result = payloads.create_backup(state, config, calibration, ROOT, destination, **kwargs)
    return state, config, calibration, store, ident, destination, result


def test_complete_payload_bytes_private_copy_verify_and_actual_restore(tmp_path, monkeypatch):
    state, config, calibration, store, ident, bundle, receipt = create(tmp_path, monkeypatch)
    assert receipt["completed"] and receipt["test_restore_passed"]
    assert receipt["all_restored_file_sha256_verified"]
    assert receipt["references"]["known_dataset_manifests_checked"] == 1
    assert receipt["references"]["local_hashed_references_checked"] == 1
    assert receipt["payload_bytes"] > 2 * 1024 * 1024
    assert "PRIVATE_TEST_TOKEN" not in json.dumps(receipt)
    manifest = payloads.verify_backup(bundle)
    assert manifest["selected_directories"] == list(payloads.PAYLOAD_DIRS)
    assert not manifest["source_window"]["atomic_filesystem_snapshot"]
    assert not manifest["source_window"]["cross_host_atomic"]
    assert manifest["source_window"]["events_may_arrive"]
    for entry in manifest["entries"]:
        path = bundle / entry["path"]
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    restored = tmp_path / "actual-rehearsal"
    assert payloads.restore_backup(bundle, ROOT, restored)["mode"] == "verify_only"
    assert not restored.exists()
    result = payloads.restore_backup(bundle, ROOT, restored, apply=True)
    assert result["test_restore_passed"]
    assert result["store"]["counts"]["jobs"] == 1
    assert (restored / "metadata/private/config/config.json").read_bytes() == (config / "config.json").read_bytes()
    for source in state.rglob("*"):
        if source.is_file() and source.parts[len(state.parts)] in payloads.PAYLOAD_DIRS:
            assert (restored / "data" / source.relative_to(state)).read_bytes() == source.read_bytes()
    assert (restored / "data/artifacts/empty").is_dir()


@pytest.mark.parametrize("status", ["running", "queued", "paused", "cancel_requested", "future_unknown"])
def test_unfinished_jobs_refused_before_destination_created(tmp_path, monkeypatch, status):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch, job_status=status)
    with pytest.raises(MaintenanceError, match="terminal"):
        payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_source_same_size_change_after_stream_retains_incomplete_receipt(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    original = payloads.stream_file
    changed = False

    def mutate(source, destination=None, expected=None):
        nonlocal changed
        result = original(source, destination, expected)
        if destination is not None and source == state / "jobs/finished/optimizer.pt" and not changed:
            stamp = source.stat()
            source.write_bytes(b"x" * stamp.st_size)
            os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            changed = True
        return result

    monkeypatch.setattr(payloads, "stream_file", mutate)
    with pytest.raises(MaintenanceError, match="inventory changed"):
        payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup")
    receipt = read_json(tmp_path / "backup/receipt.json")
    assert not receipt["completed"] and receipt["stage"] == "incomplete"
    assert receipt["failed_stage"] == "final_source_validation"
    assert receipt["payload_bytes"] > 0
    assert (state / "jobs/finished/model.safetensors").exists()


def test_job_rows_change_is_refused_but_events_can_arrive(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    original = payloads.stream_file
    changed = False

    def update(source, destination=None, expected=None):
        nonlocal changed
        result = original(source, destination, expected)
        if destination is not None and source == state / "jobs/finished/optimizer.pt" and not changed:
            # Simulate an unsafe external SQLite writer. The normal Store API
            # correctly preserves terminal results and cannot create this race.
            with store.connect() as database:
                database.execute("UPDATE jobs SET result=? WHERE id=?",
                                 (json.dumps({"changed_after_terminal": True}), ident))
            changed = True
        return result

    monkeypatch.setattr(payloads, "stream_file", update)
    with pytest.raises(MaintenanceError, match="job rows changed"):
        payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup")
    assert not read_json(tmp_path / "backup/receipt.json")["completed"]


def test_new_events_do_not_invalidate_job_window(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    original = payloads.stream_file
    inserted = False

    def event(source, destination=None, expected=None):
        nonlocal inserted
        result = original(source, destination, expected)
        if destination is not None and source == state / "jobs/finished/optimizer.pt" and not inserted:
            with store.connect() as database:
                database.execute("INSERT INTO events VALUES (?,?,?,?,?)", ("during-copy", "pi", 1, "digest", "{}"))
            inserted = True
        return result

    monkeypatch.setattr(payloads, "stream_file", event)
    receipt = payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup")
    assert receipt["completed"] and inserted
    assert receipt["store"]["counts"]["events"] == 0
    with store.connect() as database:
        assert database.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_internal_links_and_hardlinks_are_portable_and_no_external_traversal(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    source = state / "jobs/finished/optimizer.pt"
    (state / "artifacts/optimizer-link.pt").symlink_to(source)
    os.link(source, state / "artifacts/optimizer-hardlink.pt")
    receipt = payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup")
    target = tmp_path / "restored"
    payloads.restore_backup(tmp_path / "backup", ROOT, target, apply=True)
    link = target / "data/artifacts/optimizer-link.pt"
    assert link.is_symlink() and not Path(os.readlink(link)).is_absolute()
    assert link.resolve() == target / "data/jobs/finished/optimizer.pt"
    hard = target / "data/artifacts/optimizer-hardlink.pt"
    assert hard.read_bytes() == source.read_bytes()
    assert hard.stat().st_ino != (target / "data/jobs/finished/optimizer.pt").stat().st_ino
    (state / "artifacts/escape").symlink_to(tmp_path)
    with pytest.raises(MaintenanceError, match="escapes"):
        payloads.create_backup(state, config, calibration, ROOT, tmp_path / "refused")
    assert not read_json(tmp_path / "refused/receipt.json")["completed"]


@pytest.mark.parametrize("suffix", ["source", "existing", "nested", "linked"])
def test_backup_destination_protection(tmp_path, monkeypatch, suffix):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    destination = {"source": state / "new", "existing": state, "nested": config / "new"}.get(suffix)
    if suffix == "linked":
        (tmp_path / "link").symlink_to(state)
        destination = tmp_path / "link/new"
    with pytest.raises(MaintenanceError):
        payloads.create_backup(state, config, calibration, ROOT, destination)


def rewrite_manifest(bundle, transform):
    manifest = read_json(bundle / "manifest.json")
    transform(manifest)
    replace_private_json(bundle / "manifest.json", manifest)
    receipt = read_json(bundle / "receipt.json")
    receipt["manifest_sha256"] = payloads.stream_file(bundle / "manifest.json")[0]
    replace_private_json(bundle / "receipt.json", receipt)


def test_corrupt_payload_and_duplicate_or_link_prefix_members_refused(tmp_path, monkeypatch):
    *_, bundle, receipt = create(tmp_path, monkeypatch)
    path = bundle / "data/jobs/finished/optimizer.pt"
    original = path.read_bytes()
    path.write_bytes(b"x" * len(original))
    with pytest.raises(MaintenanceError, match="checksum"):
        payloads.verify_backup(bundle)
    path.write_bytes(original)
    rewrite_manifest(bundle, lambda value: value["entries"].append(value["entries"][0]))
    with pytest.raises(MaintenanceError, match="Duplicate"):
        payloads.verify_backup(bundle)


def test_restore_protects_original_config_and_resolved_calibration(tmp_path, monkeypatch):
    state, config, calibration, store, ident, bundle, receipt = create(tmp_path, monkeypatch)
    for destination in (state / "new", config / "new", calibration / "new", bundle / "new"):
        with pytest.raises(MaintenanceError, match="protected"):
            payloads.restore_backup(bundle, ROOT, destination, apply=True)
        assert not destination.exists()
    with pytest.raises(MaintenanceError, match="explicit"):
        payloads.restore_backup(bundle, ROOT, apply=True)


def test_reuses_verified_metadata_and_rejects_stale_job_snapshot(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    metadata = tmp_path / "metadata-existing"
    backup_state.create_backup(state, config, calibration, ROOT, metadata)
    receipt = payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup", metadata_backup=metadata)
    assert receipt["completed"]
    assert (metadata / "data/research.sqlite3").read_bytes() == (tmp_path / "backup/metadata/data/research.sqlite3").read_bytes()
    # An out-of-band writer must still invalidate an old metadata snapshot,
    # even though Store.update_job now refuses terminal-result replacement.
    with store.connect() as database:
        database.execute("UPDATE jobs SET result=? WHERE id=?",
                         (json.dumps({"later_terminal_evidence": True}), ident))
    with pytest.raises(MaintenanceError, match="snapshot"):
        payloads.create_backup(state, config, calibration, ROOT, tmp_path / "refused", metadata_backup=metadata)
    assert not read_json(tmp_path / "refused/receipt.json")["completed"]


def test_dataset_reference_mismatch_prevents_success_even_with_complete_byte_copy(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    source = state / "datasets/recorded/episode_0000.npz"
    source.write_bytes(b"modified-before-backup-valid-inventory-but-invalid-dataset-reference")
    with pytest.raises(MaintenanceError, match="reference checksum"):
        payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup")
    receipt = read_json(tmp_path / "backup/receipt.json")
    assert receipt["failed_stage"] == "isolated_restore" and not receipt["test_restore_passed"]
    assert source.exists()


def test_stream_detects_replacement_and_truncation_before_copy(tmp_path):
    source = put(tmp_path / "file", b"abcdefgh")
    expected = payloads.identity(source.stat())
    # Keep the old inode allocated: immediate inode/timestamp reuse is legal on
    # Linux and would make a byte-identical replacement indistinguishable here.
    with source.open('rb'):
        source.unlink()
        source.write_bytes(b"abcdefgh")
        with pytest.raises(MaintenanceError, match="identity"):
            payloads.stream_file(source, tmp_path / "copy", expected)
    expected = payloads.identity(source.stat())
    source.write_bytes(b"short")
    with pytest.raises(MaintenanceError, match="identity"):
        payloads.stream_file(source, tmp_path / "copy", expected)


@pytest.mark.parametrize("operation", ["truncate", "replace"])
def test_stream_detects_change_after_bytes_read(tmp_path, monkeypatch, operation):
    source = put(tmp_path / "file", b"abcdefgh")
    original = os.fstat
    calls = 0

    def changed(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            if operation == "replace":
                source.unlink()
            source.write_bytes(b"short")
        return original(descriptor)

    monkeypatch.setattr(payloads.os, "fstat", changed)
    with pytest.raises(MaintenanceError, match="mutated/replaced/truncated"):
        payloads.stream_file(source, tmp_path / "copy")
    assert source.exists()


def test_program_library_hash_binding_does_not_invent_local_checkpoint_and_explicit_paths_stay_required(tmp_path,monkeypatch):
    state,config,calibration,store,ident=setup_state(tmp_path,monkeypatch)
    checkpoint=state/'jobs/finished/model.safetensors'
    program=state/'jobs/finished/program_trace/program.json'
    binding={'skill_id':'actual-library-id','weights_sha256':'b'*64,'checkpoint_sha256':{'model.safetensors':payloads.stream_file(checkpoint)[0]}}
    put(program,json.dumps({'schema':'explorer_declarative_trace_program_v1','statements':[{'library_binding':binding}]}))
    receipt=payloads.create_backup(state,config,calibration,ROOT,tmp_path/'backup')
    assert receipt['completed'] and receipt['test_restore_passed']
    assert receipt['references']['checkpoint_digest_groups_without_file_locator']==1
    binding['checkpoint_dir']=str(state/'jobs/finished/missing-explicit-checkpoint')
    put(program,json.dumps({'statements':[{'library_binding':binding}]}))
    with pytest.raises(MaintenanceError,match='reference is missing'):
        payloads.create_backup(state,config,calibration,ROOT,tmp_path/'broken-explicit-path')


def test_actual_checkpoint_and_artifact_reference_hashes_are_exercised(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    checkpoint = state / "jobs/finished"
    put(checkpoint / "result.json", json.dumps({"student": {"checkpoint_dir": str(checkpoint), "checkpoint_sha256": {
        "model.safetensors": payloads.stream_file(checkpoint / "model.safetensors")[0]}}, "teacher": {
            "checkpoint": str(checkpoint / "optimizer.pt"), "sha256": payloads.stream_file(checkpoint / "optimizer.pt")[0]},
        "artifact_sha256": {"optimizer.pt": payloads.stream_file(checkpoint / "optimizer.pt")[0]}}))
    receipt = payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup")
    assert receipt["references"]["local_hashed_references_checked"] == 3


def test_detached_qualification_summary_does_not_invent_its_original_job_root(tmp_path,monkeypatch):
    state,config,calibration,store,ident=setup_state(tmp_path,monkeypatch)
    summary=state/'qualification/actual-run/copied-summary.json'
    put(summary,json.dumps({'artifact_sha256':{'original-job/frame.jpg':'a'*64}}))
    receipt=payloads.create_backup(state,config,calibration,ROOT,tmp_path/'backup')
    assert receipt['completed'] and receipt['references']['detached_qualification_relative_digest_entries_without_locator']==1
    put(summary,json.dumps({'checkpoint_dir':str(state/'qualification/missing-explicit-directory'),'checkpoint_sha256':{'config.json':'a'*64}}))
    with pytest.raises(MaintenanceError,match='reference is missing'):
        payloads.create_backup(state,config,calibration,ROOT,tmp_path/'missing-checkpoint')


def test_retained_negative_qualification_fixture_is_restored_byte_identical_without_admitting_it(tmp_path,monkeypatch):
    state,config,calibration,store,ident=setup_state(tmp_path,monkeypatch)
    fixture=state/'qualification/rehearsal/test_deliberately_broken0/negative.json'
    put(fixture,json.dumps({'artifact_sha256':{'intentionally-missing':'a'*64},'scope':'SOFTWARE_NEGATIVE_FIXTURE'}))
    receipt=payloads.create_backup(state,config,calibration,ROOT,tmp_path/'backup')
    assert receipt['completed'] and receipt['test_restore_passed']
    assert receipt['references']['generated_qualification_test_documents_preserved_without_runtime_admission']==1
    assert (tmp_path/'backup/data'/fixture.relative_to(state)).read_bytes()==fixture.read_bytes()


def test_frozen_release_documents_are_copied_as_source_and_not_reinterpreted_as_new_artifacts(tmp_path,monkeypatch):
    state,config,calibration,store,ident=setup_state(tmp_path,monkeypatch)
    revision='a'*40;source=state/'qualification/recovery/releases'/revision
    put(source/'RELEASE_COMMIT',revision+'\n')
    put(source/'docs/historical-result.json',json.dumps({'artifact_sha256':{'old-run-only.json':'b'*64}}))
    receipt=payloads.create_backup(state,config,calibration,ROOT,tmp_path/'backup')
    assert receipt['completed'] and receipt['test_restore_passed']
    assert receipt['references']['preserved_source_snapshot_documents_not_reinterpreted_as_runtime_artifacts']==1
    assert (tmp_path/'backup/data/qualification/recovery/releases'/revision/'docs/historical-result.json').read_bytes()==(source/'docs/historical-result.json').read_bytes()


def test_admission_report_retains_source_digests_without_inventing_a_sidecar_payload_root(tmp_path,monkeypatch):
    state,config,calibration,store,ident=setup_state(tmp_path,monkeypatch)
    report=state/'jobs/finished/reports/canonical-admission.json'
    put(report,json.dumps({'source_integrity':{'source_files_sha256':{'depth.u16':'a'*64}}}))
    receipt=payloads.create_backup(state,config,calibration,ROOT,tmp_path/'backup')
    assert receipt['completed'] and receipt['test_restore_passed']
    assert receipt['references']['source_relative_digests_requiring_their_original_or_aggregate_locator']==1
    put(report,json.dumps({'artifact_sha256':{'explicit-missing-file':'a'*64}}))
    with pytest.raises(MaintenanceError,match='reference is missing'):
        payloads.create_backup(state,config,calibration,ROOT,tmp_path/'missing-explicit-artifact')


def test_absolute_and_nested_hash_provenance_remaps_local_and_marks_external(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    artifact = state / "jobs/finished/optimizer.pt"
    put(state / "qualification/provenance.json", json.dumps({"nested": [{"source_files_sha256": {
        str(artifact): payloads.stream_file(artifact)[0], "/remote-pi/not-retained-original.jpg": "a" * 64}}]}))
    receipt = payloads.create_backup(state, config, calibration, ROOT, tmp_path / "backup")
    assert receipt["references"]["local_hashed_references_checked"] == 2
    assert receipt["references"]["original_absolute_provenance_paths_retained"] == 1


def test_source_state_symlink_alias_is_remapped_after_copy_to_another_host(tmp_path, monkeypatch):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    alias = tmp_path / "configured-state"
    alias.symlink_to(state, target_is_directory=True)
    artifact = alias / "jobs/finished/optimizer.pt"
    put(state / "qualification/provenance.json", json.dumps({"source_files_sha256": {str(artifact): payloads.stream_file(artifact)[0]}}))
    receipt = payloads.create_backup(alias, config, calibration, ROOT, tmp_path / "backup")
    assert receipt["references"]["local_hashed_references_checked"] == 2
    # Original alias can be absent on the verification/restoration host.
    alias.unlink()
    result = payloads.restore_backup(tmp_path / "backup", ROOT, tmp_path / "rehearsal", apply=True)
    assert result["references"]["local_hashed_references_checked"] == 2
    assert result["references"]["original_absolute_provenance_paths_retained"] == 0


@pytest.mark.parametrize("name", ["research.sqlite3", "research.sqlite3-wal", "research.sqlite3-shm"])
def test_raw_central_sqlite_aliases_are_refused(tmp_path, monkeypatch, name):
    state, config, calibration, store, ident = setup_state(tmp_path, monkeypatch)
    # Keep the actual WAL connection alive while inspecting aliases; the mechanism is inode-based.
    writer = store.connect()
    writer.execute("SELECT count(*) FROM jobs").fetchone()
    try:
        original = state / name
        assert original.exists()
        os.link(original, state / "artifacts/innocent-payload-name.bin")
        with pytest.raises(MaintenanceError, match="hardlink aliases"):
            payloads.inventory(state)
    finally:
        writer.close()


@pytest.mark.parametrize("tamper", ["prefix", "outside", "symlink_root"])
def test_untrusted_manifest_paths_and_replaced_root_refused_without_restore(tmp_path, monkeypatch, tamper):
    *_, bundle, receipt = create(tmp_path, monkeypatch)
    if tamper == "symlink_root":
        (bundle / "data").rename(bundle / "retained-data")
        (bundle / "data").symlink_to(tmp_path, target_is_directory=True)
    else:
        def patch(value):
            if tamper == "prefix":
                entry = next(item for item in value["entries"] if item["path"] == "data/jobs")
                entry.update(kind="symlink", target="../metadata")
            else:
                value["entries"].append({"path": "data/../../escape", "kind": "directory"})
        rewrite_manifest(bundle, patch)
    destination = tmp_path / "never-created"
    with pytest.raises(MaintenanceError):
        payloads.restore_backup(bundle, ROOT, destination, apply=True)
    assert not destination.exists()
