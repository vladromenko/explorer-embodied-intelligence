import hashlib
import json
import sqlite3
import pytest
from explorer.store import Store
from explorer_shared.contracts import check_sample, event
from explorer_shared.outbox import EventOutbox
from explorer_shared.legacy_bridge import LegacyBridge


def test_outbox_survives_restart_and_rejects_conflicting_identity(tmp_path):
    outbox = EventOutbox(tmp_path / 'outbox.sqlite3')
    assert outbox.append('event1', {'value':1})
    assert not outbox.append('event1', {'value':1})
    with pytest.raises(ValueError):
        outbox.append('event1', {'value':2})
    again = EventOutbox(tmp_path / 'outbox.sqlite3')
    assert again.pending() == [{'event_id':'event1','payload':{'value':1}}]
    again.acknowledge('event1')
    assert not EventOutbox(tmp_path / 'outbox.sqlite3').pending()


def test_event_integrity_and_sqlite_restore(tmp_path):
    store = Store(tmp_path / 'main')
    body = event('pi', '02', {'observation':'actual'})
    assert store.ingest(body)
    assert not store.ingest(body)
    body['payload']['observation'] = 'modified'
    with pytest.raises(ValueError):
        store.ingest(body)
    backup = store.backup(tmp_path / 'restore.sqlite3')
    with sqlite3.connect(backup) as restored:
        assert restored.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_cross_clock_and_future_observation_rejected():
    row = dict(clock_domain='pi:boot1', observation_monotonic_ns=10, dispatch_monotonic_ns=20, state_source='command_estimate')
    assert check_sample(row)
    with pytest.raises(ValueError):
        check_sample({**row, 'dispatch_clock_domain':'nano:boot2'})
    with pytest.raises(ValueError):
        check_sample({**row, 'observation_monotonic_ns':21})


def test_controller_configuration_is_never_proxied(tmp_path):
    bridge = LegacyBridge(tmp_path)
    for operation in ('arm/reference', 'arm/prepare', '../settings', 'firmware/flash', 'config'):
        with pytest.raises(ValueError):
            bridge.request(operation, {})
    with pytest.raises(ValueError):
        bridge.request('control', {'op':'drive', 'velocity':[1,1,1]})


def test_no_physical_validation_without_evidence(tmp_path):
    store = Store(tmp_path)
    card = dict(id='example',task='reach',source_type='simulation',action_contract='v1',lineage=[],evaluation={},status='physically_validated',limitations=[])
    with pytest.raises(ValueError):
        store.add_skill(card)
