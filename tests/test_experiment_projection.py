"""Canonical views must preserve legacy bytes, causal lineage and hash meaning."""
import hashlib
import json

import pytest

from explorer.experiment_projection import project_document
from explorer.store import Store
from explorer_shared.contracts import canonical,digest,event


def bind_index(store,entries):
    checked=[]
    for row in entries:
        row=dict(row);row['projection_sha256']=digest(row);checked.append(row)
    body=dict(schema='explorer_experiment_projection_v1',entries=checked,entries_sha256=digest(checked))
    raw=canonical(body);(store.root/'experiment-projection.json').write_bytes(raw)
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS experiment_numbering_migrations (target_numbering_version INTEGER,index_sha256 TEXT)')
        db.execute('DELETE FROM experiment_numbering_migrations')
        db.execute('INSERT INTO experiment_numbering_migrations VALUES (2,?)',(hashlib.sha256(raw).hexdigest(),))
    return raw


def retained_job(store,ident,project,*,status='completed',config=None):
    with store.connect() as db:
        db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
            (ident,'station_import',project,status,1,1,canonical(config or {}).decode()))


def test_current_jobs_use_semantic_identity_without_double_swapping(tmp_path):
    store=Store(tmp_path)
    source=store.create_job('station_import','04',dict(project_id='04',experiment_numbering_version=2))
    target=store.create_job('policy_transfer','06',{})
    assert source['project_id']=='04' and source['experiment_slug']=='so101_source_learning'
    assert target['project_id']=='06' and target['experiment_slug']=='cross_embodiment_transfer'
    assert store.job(source['id'])==source


def test_mixed_event_epochs_preserve_exact_originals_and_idempotency(tmp_path):
    store=Store(tmp_path)
    old=event('pi','04',dict(kind='retained'))
    old.pop('experiment_slug');old.pop('experiment_numbering_version')
    new=event('pi','04',dict(kind='current'))
    for body,expected in ((old,'06'),(new,'04')):
        assert store.ingest(body) and not store.ingest(body)
        with store.connect() as db:
            raw=db.execute('SELECT body FROM events WHERE event_id=?',(body['event_id'],)).fetchone()[0]
            identity=store.experiment_projection.identity(db,'event',body['event_id'],body['project_id'],raw=raw.encode())
        assert identity['project_id']==expected and raw==canonical(body).decode()
    invalid=dict(new,event_id='bad',experiment_numbering_version=1)
    with pytest.raises(ValueError,match='conflicts'):store.ingest(invalid)


def test_display_spec_keeps_original_hash_and_never_changes_input():
    spec=dict(skill_id='stable-id',owner_experiment='06',compatible_source_projects=['06','03'])
    original=dict(spec=spec,spec_sha256=digest(spec),raw=dict(original_spec=spec),
        current=dict(project_id='06',experiment_numbering_version=2))
    frozen=canonical(original);view=project_document(original,numbering_version=1)
    assert view['spec']['owner_experiment']=='04'
    assert view['spec']['compatible_source_projects']==['04','03']
    assert view['spec_sha256']==digest(spec)
    assert view['canonical_spec_sha256']==digest(view['spec'])
    assert view['legacy_provenance']['original_spec']==spec
    assert view['spec_sha256_basis']=='original_immutable_spec'
    assert view['current']['project_id']=='06'
    assert canonical(original)==frozen
    assert project_document(view,numbering_version=1)==view


def test_pinned_historical_job_and_late_card_have_canonical_views_only(tmp_path):
    store=Store(tmp_path);raw_config=dict(project_id='04',_execution_commit='e'*40)
    retained_job(store,'pinned','04',status='running',config=raw_config)
    # The active old worker keeps its original 04, but all new views mean target06.
    bind_index(store,[dict(entity_kind='job',entity_id='pinned',original_project_id='04',original_numbering_version=1,
        canonical_project_id='06',experiment_slug='cross_embodiment_transfer',raw_sha256=digest(raw_config),deferred_active_pin=True)])
    job=store.job('pinned')
    assert job['project_id']=='06' and job['config']==raw_config
    assert job['numbering_provenance']['deferred_active_pin']
    # An old pinned process can finish after the migration and insert an unmarked
    # card: parent semantics, rather than an already-projected numeric ID, win.
    card=dict(id='late',lineage=dict(job_id='pinned'),spec=dict(owner_experiment='04'))
    raw=canonical(card).decode()
    with store.connect() as db:db.execute('INSERT INTO skills VALUES (?,?,?)',('late',2,raw))
    view=store.skills()[0]
    assert view['project_id']=='06' and view['spec']['owner_experiment']=='06'
    assert view['numbering_provenance']['source']=='immutable_card_lineage_job_id'
    with store.connect() as db:
        assert db.execute('SELECT project_id FROM jobs').fetchone()[0]=='04'
        assert db.execute('SELECT body FROM skills').fetchone()[0]==raw


def test_late_source_card_does_not_reinterpret_migrated_parent04_as_old_target(tmp_path):
    store=Store(tmp_path);retained_job(store,'source','04')
    bind_index(store,[dict(entity_kind='job',entity_id='source',original_project_id='06',original_numbering_version=1,
        canonical_project_id='04',experiment_slug='so101_source_learning',raw_sha256=digest({}))])
    card=dict(id='late-source',lineage=dict(job_id='source'),spec=dict(owner_experiment='06'))
    with store.connect() as db:db.execute('INSERT INTO skills VALUES (?,?,?)',(card['id'],2,canonical(card).decode()))
    view=store.skills()[0]
    assert view['project_id']=='04' and view['spec']['owner_experiment']=='04'
    assert view['experiment_slug']=='so101_source_learning'


def test_bound_index_tampering_and_raw_card_tampering_fail_closed(tmp_path):
    store=Store(tmp_path);card=dict(id='bound',owner_experiment='06')
    raw=canonical(card)
    with store.connect() as db:db.execute('INSERT INTO skills VALUES (?,?,?)',(card['id'],1,raw.decode()))
    index=bind_index(store,[dict(entity_kind='skill',entity_id='bound',original_project_id='06',original_numbering_version=1,
        canonical_project_id='04',experiment_slug='so101_source_learning',raw_sha256=hashlib.sha256(raw).hexdigest())])
    assert store.skills()[0]['project_id']=='04'
    (store.root/'experiment-projection.json').write_bytes(index+b' ')
    with pytest.raises(ValueError,match='SQLite binding'):store.skills()
    (store.root/'experiment-projection.json').write_bytes(index)
    with store.connect() as db:db.execute('UPDATE skills SET body=?',(json.dumps(dict(card,unexpected=True)),))
    with pytest.raises(ValueError,match='source body changed'):store.skills()


def test_same_index_can_be_loaded_after_rollback_then_reapplication(tmp_path):
    store=Store(tmp_path);retained_job(store,'old','04')
    entries=[dict(entity_kind='job',entity_id='old',original_project_id='04',original_numbering_version=1,
        canonical_project_id='06',experiment_slug='cross_embodiment_transfer',raw_sha256=digest({}))]
    bind_index(store,entries);assert store.job('old')['project_id']=='06'
    with store.connect() as db:db.execute('DROP TABLE experiment_numbering_migrations')
    assert store.job('old')['project_id']=='06'
    bind_index(store,entries)
    assert store.job('old')['numbering_provenance']['source']=='verified_migration_index'


def test_retry_derives_original_epoch_on_server_and_keeps_raw_parent(tmp_path):
    store=Store(tmp_path);config=dict(episode_id='kept',project_id='06',_execution_commit='e'*40)
    retained_job(store,'failed','04',status='failed',config=config)
    bind_index(store,[dict(entity_kind='job',entity_id='failed',original_project_id='personal',original_numbering_version=1,
        canonical_project_id='04',experiment_slug='so101_source_learning',raw_sha256=digest(config))])
    retry=store.retry_import_job('failed')
    assert retry['project_id']=='04' and retry['original_numbering_version']==2
    assert retry['config']['source_numbering_version']==1
    assert retry['config']['experiment_numbering_version']==2
    assert '_execution_commit' not in retry['config']
    assert store.job('failed')['config']==config
    assert store.retry_import_job('failed')['id']==retry['id']
