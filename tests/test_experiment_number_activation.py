"""Independent filesystem activation; no real service or fleet operation."""
import importlib.util
import json
from pathlib import Path

import pytest

from explorer.store import Store
from explorer_shared.contracts import canonical,event

PATH=Path(__file__).resolve().parents[1]/'scripts/activate_experiment_numbers.py'
spec=importlib.util.spec_from_file_location('activate_numbering',PATH);activation=importlib.util.module_from_spec(spec);spec.loader.exec_module(activation)


def source(tmp_path,*,active=False):
    store=Store(tmp_path/'state');record=event('pi','04',dict(source='original'));record['event_id']='retained'
    record.pop('experiment_numbering_version');record.pop('experiment_slug');body=canonical(record).decode()
    with store.connect() as db:
        db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
            ('legacy','policy_transfer','04','running' if active else 'completed',1,1,'{"_execution_commit":"'+('e'*40)+'"}'))
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',('retained','pi',1,record['sha256'],body))
    receipt=tmp_path/'backup.json';receipt.write_text(json.dumps(dict(completed=True,test_restore_passed=True,all_restored_file_sha256_verified=True)))
    preparation=tmp_path/'prepared';activation.migration.prepare(store.root,preparation)
    return store,preparation,receipt,body


def test_canonical_copy_activation_preserves_raw_evidence_pins_and_verified_index(tmp_path):
    store,prepared,backup,body=source(tmp_path)
    receipt=activation.install_quiesced_copy(prepared,store.root,backup,services_stopped=True)
    assert receipt['ok'] and not receipt['raw_scientific_payloads_rewritten'] and receipt['physical_commands']==0
    assert Store(store.root).job('legacy')['project_id']=='06'
    with store.connect() as db:
        assert db.execute('SELECT body FROM events').fetchone()[0]==body
        assert json.loads(db.execute('SELECT config FROM jobs').fetchone()[0])['_execution_commit']=='e'*40
    assert (prepared/'original/research.sqlite3').is_file()
    assert (store.root/'experiment-projection.json').read_bytes()==(prepared/'candidate/experiment-projection.json').read_bytes()


def test_running_services_active_jobs_or_unverified_backup_block_before_any_swap(tmp_path):
    store,prepared,backup,body=source(tmp_path,active=True);before=store.path.read_bytes()
    with pytest.raises(ValueError,match='services'):activation.install_quiesced_copy(prepared,store.root,backup,services_stopped=False)
    with pytest.raises(ValueError,match='Active jobs'):activation.install_quiesced_copy(prepared,store.root,backup,services_stopped=True)
    backup.write_text('{}')
    with pytest.raises(ValueError,match='verified payload backup'):activation.install_quiesced_copy(prepared,store.root,backup,services_stopped=True)
    assert not (store.root/'experiment-projection.json').exists() and store.path.read_bytes()==before


def test_new_acknowledged_evidence_after_prepare_cannot_be_lost_to_an_old_copy(tmp_path):
    store,prepared,backup,body=source(tmp_path)
    with store.connect() as db:db.execute('INSERT INTO nodes VALUES (?,?,?)',('new',2,'{"new_evidence":true}'))
    with pytest.raises(ValueError,match='changed after preparation'):activation.install_quiesced_copy(prepared,store.root,backup,services_stopped=True)
    with store.connect() as db:assert db.execute('SELECT count(*) FROM nodes').fetchone()[0]==1
    assert not (store.root/'experiment-projection.json').exists()
