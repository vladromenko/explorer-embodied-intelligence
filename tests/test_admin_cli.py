"""ENGINEERING_GLUE: administrative rehearsals use temporary files and mocked units only."""
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / '00-control-center/src'), str(ROOT / 'shared')]
import backup_state
from explorer import cli
import rollback_release as rollback


def release(base, commit, node='homebrain'):
    path = base / 'releases' / commit
    path.mkdir(parents=True)
    (path / 'RELEASE_COMMIT').write_text(commit + '\n')
    entry = path / ('00-control-center/src/explorer/cli.py' if node == 'homebrain' else 'shared/explorer_shared/node_agent.py')
    entry.parent.mkdir(parents=True)
    entry.write_text('# Source fixture; never executed.\n')
    return path


def setup(home, node='homebrain'):
    base = home / '.local/share/explorer'
    previous = release(base, 'a' * 40, node)
    current = release(base, 'b' * 40, node)
    (base / 'current').symlink_to(current, target_is_directory=True)
    (base / 'previous-release').write_text(str(previous))
    state = base / 'state'
    state.mkdir()
    with sqlite3.connect(state / 'research.sqlite3') as database:
        database.execute('CREATE TABLE jobs (id TEXT,kind TEXT,status TEXT,created REAL)')
    # Rehearse the deployer's explicit ownership/mode contract regardless of host umask.
    for path in (base,*base.rglob('*')):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)
    return base, previous, current, state


def test_rollback_dry_run_has_no_mutations_or_service_calls(tmp_path):
    home = tmp_path / 'home'
    base, previous, current, state = setup(home)
    calls = []
    result = rollback.rollback(home, 'homebrain', state=state, restart=lambda units: calls.append(units))
    assert result['dry_run'] and not result['applied']
    assert result['requested']['commit'] == previous.name
    assert result['current']['commit'] == current.name
    assert (base / 'current').resolve() == current
    assert not calls and not list(base.glob('.current-rollback-*'))


def test_missing_authoritative_registry_and_forbidden_units_refuse_apply(tmp_path):
    home = tmp_path / 'home'
    base, previous, current, state = setup(home)
    (state / 'research.sqlite3').unlink()
    planned = rollback.rollback(home, 'homebrain', state=state)
    assert not planned['jobs']['authoritative']
    with pytest.raises(rollback.RollbackError, match='authoritative'):
        rollback.rollback(home, 'homebrain', state=state, apply=True,
                          restart=lambda units: pytest.fail('Unexpected restart'))
    assert (base / 'current').resolve() == current
    with pytest.raises(rollback.RollbackError, match='restricted'):
        rollback.restart_units(('camera.service',))


def test_actual_isolated_symlink_transition_holds_job_guard_and_uses_exact_units(tmp_path):
    home = tmp_path / 'home'
    base, previous, current, state = setup(home)
    hint = (base / 'previous-release').read_bytes()
    calls = []

    def restart(units):
        calls.append(units)
        assert (base / 'current').resolve() == previous
        database = sqlite3.connect(state / 'research.sqlite3', timeout=.01)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                database.execute('BEGIN IMMEDIATE')
        finally:
            database.close()

    result = rollback.rollback(home, 'homebrain', state=state, apply=True, restart=restart)
    assert result['applied'] and result['restart_completed']
    assert calls == [('explorer-control-center.service', 'explorer-worker.service')]
    assert result['previous']['path'] == str(current)
    assert result['current']['path'] == str(previous)
    assert (base / 'previous-release').read_bytes() == hint
    database = sqlite3.connect(state / 'research.sqlite3', timeout=.01)
    try:
        database.execute('BEGIN IMMEDIATE')
        assert database.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
    finally:
        database.rollback()
        database.close()


@pytest.mark.parametrize('status', ['queued', 'running', 'paused', 'pause_requested', 'resume_requested', 'cancel_requested'])
def test_unfinished_jobs_refuse_apply_before_any_transition(tmp_path, status):
    home = tmp_path / 'home'
    base, previous, current, state = setup(home)
    with sqlite3.connect(state / 'research.sqlite3') as database:
        database.execute('INSERT INTO jobs VALUES (?,?,?,?)', ('active', 'train_act', status, 1))
    calls = []
    with pytest.raises(rollback.RollbackError, match='unfinished') as failure:
        rollback.rollback(home, 'homebrain', state=state, apply=True, restart=lambda units: calls.append(units))
    assert failure.value.receipt['jobs']['active_jobs'][0]['status'] == status
    assert (base / 'current').resolve() == current and not calls


@pytest.mark.parametrize('node', ['pi', 'nano'])
def test_pi_nano_only_plan_and_refuse_unknown_central_jobs(tmp_path, node):
    home = tmp_path / 'home'
    base, previous, current, state = setup(home, node)
    result = rollback.rollback(home, node, state=state)
    assert not result['jobs']['authoritative']
    assert result['restart_units'] == ['explorer-observer.service']
    with pytest.raises(rollback.RollbackError, match='Pi/Nano'):
        rollback.rollback(home, node, state=state, apply=True, restart=lambda units: pytest.fail('Unexpected service call'))
    assert (base / 'current').resolve() == current


def test_outside_root_symlink_forged_marker_and_wrong_owner_refused(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    base, previous, current, state = setup(home)
    outside = release(tmp_path / 'outside', 'c' * 40)
    with pytest.raises(rollback.RollbackError, match='outside'):
        rollback.rollback(home, 'homebrain', release=outside, state=state)
    alias = base / 'releases' / ('d' * 40)
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(rollback.RollbackError, match='symlink'):
        rollback.rollback(home, 'homebrain', release=alias, state=state)
    (previous / 'RELEASE_COMMIT').write_text('wrong')
    with pytest.raises(rollback.RollbackError, match='marker'):
        rollback.rollback(home, 'homebrain', state=state)
    monkeypatch.setattr(rollback.os, 'geteuid', lambda: current.stat().st_uid + 1)
    with pytest.raises(rollback.RollbackError, match='owned'):
        rollback.rollback(home, 'homebrain', state=state)


def test_failed_service_restart_restores_previous_current_and_releases_job_lock(tmp_path):
    home = tmp_path / 'home'
    base, previous, current, state = setup(home)
    calls = []

    def restart(units):
        calls.append(units)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, ['systemctl'])

    with pytest.raises(rollback.RollbackError) as failure:
        rollback.rollback(home, 'homebrain', state=state, apply=True, restart=restart)
    assert failure.value.receipt['recovered_symlink']
    assert failure.value.receipt['recovery_restart_completed']
    assert (base / 'current').resolve() == current
    assert calls == [rollback.UNITS['homebrain'], rollback.UNITS['homebrain']]


def test_cli_backup_and_restore_check_keep_password_and_use_staged_calibration(tmp_path, monkeypatch, capsys):
    home = tmp_path / 'home'
    config = home / '.config/explorer/config.json'
    config.parent.mkdir(parents=True)
    state = tmp_path / 'ssd-state'
    from explorer.store import Store
    Store(state)
    raw = json.dumps({'data_dir': str(state), 'password_hash': 'unchanged-test-hash', 'nodes': []})
    config.write_text(raw)
    config.chmod(0o600)
    access = config.parent / 'access-code.txt'
    access.write_text('unchanged-test-access-code\n')
    access.chmod(0o600)
    staged = home / '.local/state/explorer/backup-staging/pi-original-calibration'
    staged.mkdir(parents=True)
    (staged / 'original.json').write_text('{"calibration":"original"}')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('EXPLORER_CONFIG', str(config))
    monkeypatch.setattr(backup_state, 'collect_environment', lambda: {'python': 'fixture'})
    monkeypatch.setattr(backup_state, 'source_inventory', lambda root: {'files': []})
    monkeypatch.setattr(sys, 'argv', ['explorerctl', 'backup'])
    cli.main()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['metadata_only'] and receipt['test_restore_passed'] and receipt['calibration_files'] == 1
    assert config.read_text() == raw and 'unchanged-test-hash' not in json.dumps(receipt)
    assert access.read_text() == 'unchanged-test-access-code\n' and 'unchanged-test-access-code' not in json.dumps(receipt)
    backup = Path(receipt['backup_directory'])
    assert stat.S_IMODE(backup.parent.stat().st_mode) == 0o700
    assert json.loads((backup / 'manifest.json').read_text())['source_calibration'] == str(staged)
    monkeypatch.setattr(sys, 'argv', ['explorerctl', 'restore-check', '--backup', str(backup)])
    cli.main()
    checked = json.loads(capsys.readouterr().out)
    assert checked['test_restore_passed'] and not checked['live_data_changed']
    assert config.read_text() == raw
    explicit = tmp_path / 'manual-private-parent/copied-metadata'
    selected = tmp_path / 'selected-calibration'
    selected.mkdir()
    (selected / 'selected.json').write_text('{"selected":true}')
    monkeypatch.setattr(sys, 'argv', ['explorerctl', 'backup', '--output', str(explicit), '--calibration', str(selected)])
    cli.main()
    copied = json.loads(capsys.readouterr().out)
    assert copied['backup_directory'] == str(explicit) and copied['calibration_files'] == 1
    assert json.loads((explicit / 'manifest.json').read_text())['source_calibration'] == str(selected)
    assert stat.S_IMODE(explicit.parent.stat().st_mode) == 0o700


def test_cli_doctor_and_rollback_plan_do_not_construct_live_store(tmp_path, monkeypatch, capsys):
    home = tmp_path / 'home'
    base, previous, current, state = setup(home)
    config = home / '.config/explorer/config.json'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'data_dir': str(state), 'nodes': []}))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('EXPLORER_CONFIG', str(config))
    monkeypatch.setattr(cli, 'Store', lambda *args: pytest.fail('Unexpected live Store construction'))
    import doctor
    monkeypatch.setattr(doctor, 'run', lambda args: 'ActiveState=active\nSubState=running')
    monkeypatch.setattr(sys, 'argv', ['explorerctl', 'doctor', '--no-http'])
    cli.main()
    assert json.loads(capsys.readouterr().out)['read_only']
    monkeypatch.setattr(sys, 'argv', ['explorerctl', 'rollback'])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result['dry_run'] and not result['applied']
    assert (base / 'current').resolve() == current
