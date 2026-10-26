"""ENGINEERING_GLUE regression tests use only temporary state and synthetic status."""
import json
from pathlib import Path
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "00-control-center/src"), str(ROOT / "shared")]
import backup_state
import doctor
import environment_inventory
from maintenance_common import MaintenanceError, digest, verify_bundle
import restore_state
from explorer.store import Store


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def make_backup(tmp_path, monkeypatch):
    state = tmp_path / "live-state"
    store = Store(state)
    writer = store.connect()
    writer.execute("INSERT INTO events VALUES (?,?,?,?,?)", ("event-1", "pi", 123, "hash", "{}"))
    writer.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", ("job-1", "simulation", "p", "completed", 1, 2, "{}", "{}", 1, None))
    writer.commit()
    assert (state / "research.sqlite3-wal").stat().st_size > 0
    config = tmp_path / "private-config"
    secret = "private-example-token-never-in-receipt"
    put(config / "config.json", json.dumps({"ingest_token": secret}))
    put(config / "arms.json", '{"calibration": "preserved"}')
    calibration = tmp_path / "calibration"
    put(calibration / "robots/original.json", '{"original": true}')
    monkeypatch.setattr(backup_state, "collect_environment", lambda: {"python": "test-only"})
    monkeypatch.setattr(backup_state, "source_inventory", lambda root: {"files": [], "scope": "test fixture"})
    destination = tmp_path / "backup"
    receipt = backup_state.create_backup(state, config, calibration, ROOT, destination)
    return state, writer, destination, receipt, secret


def test_consistent_wal_backup_private_files_and_actual_store_restore(tmp_path, monkeypatch):
    state, writer, bundle, receipt, secret = make_backup(tmp_path, monkeypatch)
    try:
        assert receipt["test_restore_passed"] is True
        assert receipt["store"]["counts"]["events"] == 1
        assert secret not in json.dumps(receipt)
        assert secret in (bundle / "private/config/config.json").read_text()
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in bundle.rglob("*") if path.is_file())
        assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in bundle.rglob("*") if path.is_dir())
        target = tmp_path / "rehearsal"
        plan = restore_state.restore(bundle, ROOT, target, home=tmp_path / "home")
        assert plan["mode"] == "dry_run" and not target.exists()
        result = restore_state.restore(bundle, ROOT, target, apply=True, home=tmp_path / "home")
        assert result["test_restore_passed"]
        assert result["store"]["counts"]["jobs"] == 1
        assert writer.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert (target / "private/calibration/robots/original.json").read_text() == '{"original": true}'
    finally:
        writer.close()


def test_corrupt_backup_refused_before_destination_creation(tmp_path, monkeypatch):
    state, writer, bundle, receipt, secret = make_backup(tmp_path, monkeypatch)
    writer.close()
    with (bundle / "data/research.sqlite3").open("ab") as output:
        output.write(b"corruption")
    target = tmp_path / "rehearsal"
    with pytest.raises(MaintenanceError, match="checksum"):
        restore_state.restore(bundle, ROOT, target, apply=True, home=tmp_path / "home")
    assert not target.exists()


def test_restore_refuses_existing_live_and_implicit_destinations(tmp_path, monkeypatch):
    state, writer, bundle, receipt, secret = make_backup(tmp_path, monkeypatch)
    writer.close()
    with pytest.raises(MaintenanceError, match="explicit"):
        restore_state.restore(bundle, ROOT, apply=True)
    with pytest.raises(MaintenanceError, match="must be new"):
        restore_state.restore(bundle, ROOT, state, apply=True)
    with pytest.raises(MaintenanceError, match="protected"):
        restore_state.restore(bundle, ROOT, state / "new-rehearsal", apply=True)
    with pytest.raises(MaintenanceError, match="protected"):
        restore_state.restore(bundle, ROOT, tmp_path / "calibration/new-rehearsal", apply=True)
    home = tmp_path / "home"
    live_cache = tmp_path / "ssd-live-calibration"
    live_cache.mkdir()
    link = home / ".cache/huggingface/lerobot"
    link.parent.mkdir(parents=True)
    link.symlink_to(live_cache, target_is_directory=True)
    with pytest.raises(MaintenanceError, match="protected"):
        restore_state.restore(bundle, ROOT, live_cache / "new-rehearsal", apply=True, home=home)
    linked = tmp_path / "linked-backup-member"
    linked.symlink_to(bundle / "environment.json")
    (bundle / "environment.json").unlink()
    (bundle / "environment.json").symlink_to(linked)
    with pytest.raises(MaintenanceError, match="linked"):
        verify_bundle(bundle)


def test_doctor_requires_real_finite_recent_same_clock_feedback():
    hashes = {"leader": "a" * 64, "follower": "b" * 64}
    inventory = {"monotonic_ns": 2_000_000_000, "clock_domain": "pi:boot",
                 "capabilities": {"joint_measurements": True},
                 "cameras": [{"id": "pi-overview", "available": True, "frame": {"frame_id": "pi:1", "monotonic_ns": 1_900_000_000}}],
                 "station": {"available": True, "measurement_age_ms": 100,
                             "roles": {"leader": {"calibration_sha256": "a" * 64}, "follower": {"calibration_sha256": "b" * 64}},
                             "measurements": {"leader": [1.] * 6, "follower": [2.] * 6, "clock_domain": "pi:boot"}}}
    facts = doctor.inventory_facts(inventory, hashes)
    assert facts["station"]["fresh_measured_six_axis_feedback"]
    assert facts["station"]["both_original_calibration_hashes_match"]
    inventory["station"]["roles"]["leader"]["calibration_sha256"] = "b" * 64
    inventory["station"]["roles"]["follower"]["calibration_sha256"] = "a" * 64
    assert not doctor.inventory_facts(inventory, hashes)["station"]["both_original_calibration_hashes_match"]
    inventory["station"]["measurement_age_ms"] = 5000
    inventory["station"]["measurements"]["follower"][0] = float("nan")
    inventory["cameras"][0]["frame"]["monotonic_ns"] = -2_000_000_000
    facts = doctor.inventory_facts(inventory, hashes)
    assert not facts["station"]["fresh_measured_six_axis_feedback"]
    assert not facts["cameras"][0]["fresh"]
    inventory["station"]["measurements"] = {}
    assert not doctor.inventory_facts(inventory, hashes)["station"]["fresh_measured_six_axis_feedback"]


def test_full_doctor_reads_actual_list_node_config_without_live_store_creation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    custom_state = tmp_path / "custom-state"
    custom_state.mkdir()
    token = "PRIVATE_TOKEN_MUST_NOT_APPEAR"
    put(home / ".config/explorer/config.json", json.dumps({"data_dir": str(custom_state), "nodes": [
        {"id": "pi", "host": "pi.local", "port": 8091, "token": token},
        {"id": "nano", "host": "nano.local", "port": 8092, "token": token}]}))
    calls = []

    def inventory(address):
        calls.append(address["host"])
        return {"cameras": [], "station": {}}

    monkeypatch.setattr(doctor, "fetch_inventory", inventory)
    monkeypatch.setattr(doctor, "run", lambda args: "ActiveState=active\nSubState=running\nUnitFileState=enabled\nExecMainStatus=0")
    result = doctor.doctor(home, ROOT, "homebrain")
    assert calls == ["pi.local"]
    assert result["nodes"]["pi"]["reachable"]
    assert not result["nodes"]["nano"]["contacted"]
    assert not (custom_state / "research.sqlite3").exists()
    assert token not in json.dumps(result)


def test_environment_freeze_redacts_direct_urls_and_keeps_actual_version(monkeypatch):
    class Dist:
        metadata = {"Name": "torch"}
        version = "2.11.0+jetson"

        def read_text(self, name):
            return json.dumps({"url": "https://user:SECRET@example.invalid/torch.whl?token=SECRET", "archive_info": {"hashes": {"sha256": "a" * 64}}}) if name == "direct_url.json" else "record"

        def locate_file(self, name):
            return Path("/nonexistent-test-version-file")

    monkeypatch.setattr(environment_inventory.metadata, "distributions", lambda: [Dist()])
    monkeypatch.setattr(environment_inventory.metadata, "distribution", lambda name: Dist())
    monkeypatch.setattr(environment_inventory, "run", lambda args, timeout=10: "torch @ https://user:SECRET@example.invalid/torch.whl?token=SECRET\nnumpy==2.2.6" if "freeze" in args else None)
    result = environment_inventory.collect_environment()
    assert "SECRET" not in json.dumps(result)
    assert "torch==2.11.0+jetson" in result["pip_freeze_sanitized"]
    assert result["packages"]["torch"]["origin"]["archive_sha256"] == "a" * 64
    assert result["freeze_url_or_editable_lines_redacted"] == 1


def test_source_inventory_hashes_source_and_skips_private_payload(tmp_path):
    source = put(tmp_path / "src/agent.py", "print('source')")
    put(tmp_path / "_registry/private/secret.json", '{"token":"not copied"}')
    put(tmp_path / "models/model.json", "not source")
    inventory = environment_inventory.source_inventory(tmp_path)
    assert inventory["files"] == [{"path": "src/agent.py", "bytes": source.stat().st_size, "sha256": digest(source)}]


def test_backup_defaults_to_selected_config_data_dir_not_old_default(tmp_path):
    config = tmp_path / "config"
    actual = tmp_path / "ssd/actual-state"
    override = tmp_path / "explicit-state"
    put(config / "config.json", json.dumps({"data_dir": str(actual)}))
    assert backup_state.selected_state(config, None) == actual
    assert backup_state.selected_state(config, override) == override
