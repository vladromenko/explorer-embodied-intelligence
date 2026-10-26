"""Copied state only: historical source bytes, active pins and all09 actions survive."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from explorer.store import Store
from explorer_shared.contracts import canonical,digest,event
from explorer_shared.outbox import EventOutbox
from explorer_shared.experiments import experiment_slug

MODULE=Path(__file__).resolve().parents[1]/'scripts'/'migrate_experiment_numbers.py'
spec=importlib.util.spec_from_file_location('migration_numbers',MODULE)
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)


def legacy_event(project,payload):
    record=event('pi',None,payload)
    record.update(project_id=project,experiment_numbering_version=1,experiment_slug=experiment_slug(project,numbering_version=1))
    return record


def retained_job(store,project,status,*,epoch=1,ident=None,config=None):
    ident=ident or uuid.uuid4().hex;settings=dict(config or {},experiment_numbering_version=epoch,experiment_slug=experiment_slug(project,numbering_version=epoch))
    with store.connect() as db:
        db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config,result) VALUES (?,?,?,?,?,?,?,?)',
            (ident,'train_act',project,status,1,1,canonical(settings).decode(),canonical(dict(original_project_id=project,nine_neutral_commands=[0]*9)).decode()))
        db.execute('INSERT INTO experiment_runtime_identity VALUES (?,?,?,?)',('job',ident,epoch,experiment_slug(project,numbering_version=epoch)))
    return ident


def raw_job(store,identity):
    with store.connect() as db:return dict(db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone())


@pytest.fixture
def copied_state(tmp_path):
    state=tmp_path/'state';store=Store(state)
    ids={}
    for name,project,status in [('transfer','04','completed'),('so101','06','completed'),('personal','personal','completed'),('keyboard','02','completed'),('active','04','running')]:
        ids[name]=retained_job(store,project,status,config=dict(source='retained-source',_execution_commit='e'*40))
    original_spec=dict(skill_id='unchanged-uuid',version=1,project_id='06',pipeline=dict(implemented=False))
    record=legacy_event('06',dict(type='workflow.spec',spec=original_spec));store.ingest(record)
    for name in ('transfer','so101','personal'):
        card=dict(id='model-'+name,task='retain',source_type='fixture',action_contract={'dimension':6},lineage={'job_id':ids[name]},evaluation={},status='offline_candidate',limitations=['not hardware'])
        project=raw_job(store,ids[name])['project_id']
        with store.connect() as db:
            db.execute('INSERT INTO skills VALUES (?,?,?)',(card['id'],1,canonical(card).decode()))
            db.execute('INSERT INTO experiment_runtime_identity VALUES (?,?,?,?)',('skill',card['id'],1,experiment_slug(project,numbering_version=1)))
    outbox=EventOutbox(tmp_path/'outbox.sqlite3');outbox.append(record['event_id'],record)
    delivered=legacy_event('04',dict(type='done'));outbox.append(delivered['event_id'],delivered);outbox.acknowledge(delivered['event_id'])
    artifact=state/'models'/'source.json';artifact.parent.mkdir();raw=b'{"project_id":"06", "url":"/experiments/06", "action":[0,0,0,0,0,0,0,0,0]}'
    artifact.write_bytes(raw)
    audit=dict(managed_root=str(state),files=[dict(path=str(artifact),sha256=migration.sha(raw),references=[dict(pointer='/project_id',value='06'),dict(pointer='/url',value='/experiments/06')])],summary=dict(affected_json_files=1),scan=dict(json_files=1))
    inventory=tmp_path/'audit.json';inventory.write_bytes(canonical(audit))
    return dict(store=store,state=state,ids=ids,outbox=outbox,record=record,artifact=artifact,raw=raw,inventory=inventory)


def test_transactional_swap_keeps_immutable_bytes_original_specs_and_all_fields(copied_state,tmp_path):
    f=copied_state;output=tmp_path/'prepared';report=migration.prepare(f['state'],output,outboxes=[f['outbox'].path],lineage_inventory=f['inventory'])
    assert report['ok'] and report['immutable_tables_preserved'] and report['counts']['terminal_job_updates']==3
    assert report['counts']['deferred_active_jobs']==1 and not report['live_state_modified']
    candidate=Store(output/'candidate');old=f['store']
    assert candidate.job(f['ids']['transfer'])['project_id']=='06'
    assert candidate.job(f['ids']['so101'])['project_id']=='04'
    assert candidate.job(f['ids']['personal'])['project_id']=='04'
    assert candidate.job(f['ids']['keyboard'])==dict(old.job(f['ids']['keyboard']))
    assert candidate.job(f['ids']['active'])['project_id']=='06' # Public view is canonical; pinned SQL stays legacy.
    assert raw_job(candidate,f['ids']['active'])['project_id']=='04'
    assert candidate.job(f['ids']['active'])['config']['_execution_commit']=='e'*40
    assert raw_job(old,f['ids']['transfer'])['project_id']=='04' and raw_job(old,f['ids']['personal'])['project_id']=='personal'
    assert f['artifact'].read_bytes()==f['raw']
    index=json.loads((output/'experiment-projection.json').read_bytes())
    entry=next(row for row in index['entries'] if row['entity_kind']=='event')
    assert entry['original_project_id']=='06' and entry['canonical_project_id']=='04'
    assert entry['original_specs'][0]['original_spec']==f['record']['payload']['spec']
    assert entry['original_specs'][0]['original_spec_sha256']==digest(f['record']['payload']['spec'])
    assert next(row for row in index['entries'] if row['entity_kind']=='skill' and row['entity_id']=='model-personal')['basis']=='immutable_card_lineage_job_id'
    assert index['artifact_inventory']['unresolved'][0]['reason']=='NON_PROJECT_REFERENCE_RETAINED_NO_TEXT_REPLACEMENT'
    assert migration.verify(output)['ok']


def test_outbox_delivery_and_event_identity_are_not_rewritten_or_duplicated(copied_state,tmp_path):
    f=copied_state;output=tmp_path/'prepared';migration.prepare(f['state'],output,outboxes=[f['outbox'].path])
    outbox=EventOutbox(output/'candidate'/'outboxes'/'outbox-0.sqlite3')
    assert outbox.pending()==f['outbox'].pending()
    assert outbox.append(f['record']['event_id'],f['record']) is False
    consumer=Store(output/'candidate')
    assert consumer.ingest(outbox.pending()[0]['payload']) is False
    outbox.acknowledge(f['record']['event_id'])
    assert not outbox.pending() and len(f['outbox'].pending())==1


def test_repeated_prepare_and_version_marker_cannot_double_swap(copied_state,tmp_path):
    f=copied_state;output=tmp_path/'prepared'
    first=migration.prepare(f['state'],output)
    assert migration.prepare(f['state'],output)['no_op']
    marker=migration.prepare(output/'candidate',tmp_path/'unused')
    assert marker['no_op'] and marker['existing_migration']['target_numbering_version']==2
    assert not (tmp_path/'unused').exists()
    assert migration.dry_run(output/'candidate')['no_op']
    assert migration.verify(output)['index_sha256']==first['index_sha256']


def test_rollback_restores_original_ids_tables_and_pins_into_new_copy(copied_state,tmp_path):
    f=copied_state;output=tmp_path/'prepared';migration.prepare(f['state'],output,outboxes=[f['outbox'].path])
    restored=tmp_path/'restored';receipt=migration.rollback_copy(output,restored)
    assert receipt['ok'] and not receipt['live_state_modified']
    old=Store(restored)
    assert raw_job(old,f['ids']['transfer'])['project_id']=='04' and raw_job(old,f['ids']['so101'])['project_id']=='06'
    assert raw_job(old,f['ids']['personal'])['project_id']=='personal'
    with old.connect() as db:assert migration.MIGRATION_TABLE not in migration._tables(db)
    assert EventOutbox(restored/'outboxes'/'outbox-0.sqlite3').pending()==f['outbox'].pending()
    with pytest.raises(migration.MigrationError,match='NEW external'):
        migration.rollback_copy(output,restored)


def test_tampered_copy_or_index_never_passes_verification(copied_state,tmp_path):
    f=copied_state;output=tmp_path/'prepared';migration.prepare(f['state'],output)
    with sqlite3.connect(output/'candidate'/'research.sqlite3') as db:db.execute("UPDATE jobs SET config='{}' WHERE project_id='06'")
    with pytest.raises(migration.MigrationError,match='SHA mismatch|Unexpected candidate'):
        migration.verify(output)
    with pytest.raises(migration.MigrationError):migration.rollback_copy(output,tmp_path/'restored')


def test_changed_artifact_is_explicitly_unprojected_not_rewritten(copied_state,tmp_path):
    f=copied_state;f['artifact'].write_bytes(b'{"project_id":"04"}')
    plan=migration.dry_run(f['state'],lineage_inventory=f['inventory'])
    assert plan['artifact_inventory']['unresolved'][0]['reason']=='INVENTORY_SOURCE_CHANGED_OR_MISSING'
    assert not any(row['entity_kind']=='artifact_reference' for row in plan['entries'])
    assert f['artifact'].read_bytes()==b'{"project_id":"04"}'


def test_snapshot_prepare_rejects_live_state_and_symlink_destination(copied_state,tmp_path):
    f=copied_state
    with pytest.raises(migration.MigrationError,match='outside live'):
        migration.prepare(f['state'],f['state']/'nested')
    link=tmp_path/'linked';link.symlink_to(f['state'],target_is_directory=True)
    with pytest.raises(migration.MigrationError,match='outside live'):
        migration.prepare(f['state'],link/'nested')
    assert not (f['state']/'nested').exists()


def test_live_changes_after_copy_block_activation_preflight_without_losing_updates(copied_state,tmp_path):
    f=copied_state;output=tmp_path/'prepared';migration.prepare(f['state'],output,outboxes=[f['outbox'].path],lineage_inventory=f['inventory'])
    assert migration.check_source(output)['ok']
    record=event('pi','06',dict(type='new.source.evidence'));f['store'].ingest(record)
    f['outbox'].append(record['event_id'],record)
    receipt=migration.check_source(output)
    assert not receipt['ok'] and not receipt['live_state_modified']
    assert {row['code'] for row in receipt['failures']}=={'LIVE_DATABASE_CHANGED_REPREPARE_AFTER_QUIESCENCE','LIVE_OUTBOX_CHANGED_REPREPARE'}
    assert any(row['payload']['event_id']==record['event_id'] for row in f['outbox'].pending())
    assert migration.verify(output)['ok'] # Preparation remains the same historical snapshot.


def test_mixed_old_and_current_jobs_events_cards_and_undelivered_outbox_keep_epochs(tmp_path):
    store=Store(tmp_path/'state');outbox=EventOutbox(tmp_path/'mixed-outbox.sqlite3');jobs={};events=[];cards=[]
    cases=[('old-target','04',1,'06'),('new-source','04',2,'04'),('old-source','06',1,'04'),('new-target','06',2,'06')]
    for name,original,epoch,current in cases:
        jobs[name]=retained_job(store,original,'completed',epoch=epoch)
        record=legacy_event(original,dict(kind=name)) if epoch==1 else event('pi',original,dict(kind=name))
        store.ingest(record);outbox.append(record['event_id'],record);events.append((record,current))
        card=dict(id='card-'+name,task='fixture',source_type='fixture',action_contract={'dimension':9},lineage={},evaluation={},status='offline_candidate',limitations=[],project_id=original,experiment_numbering_version=epoch,experiment_slug=experiment_slug(original,numbering_version=epoch))
        store.add_skill(card);cards.append((card,current))
    # Trusted runtime marker2 alone protects a current source job with no config marker.
    runtime_only=store.create_job('station_import','04',{});store.update_job(runtime_only['id'],status='completed')
    with store.connect() as db:
        # Historical unmarked rows deliberately have no runtime sidecar.
        db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',('unmarked','policy_transfer','04','completed',1,1,'{}'))
    frozen={record['event_id']:canonical(record).decode() for record,current in events}
    output=tmp_path/'prepared';migration.prepare(store.root,output,outboxes=[outbox.path])
    current_store=Store(output/'candidate')
    for name,original,epoch,current in cases:
        assert raw_job(current_store,jobs[name])['project_id']==current
        assert current_store.job(jobs[name])['project_id']==current
        assert raw_job(store,jobs[name])['project_id']==original
    assert raw_job(current_store,runtime_only['id'])['project_id']=='04'
    assert raw_job(current_store,'unmarked')['project_id']=='06'
    index=json.loads((output/'experiment-projection.json').read_bytes())
    assert (output/'candidate'/'experiment-projection.json').read_bytes()==(output/'experiment-projection.json').read_bytes()
    event_entries={row['entity_id']:row for row in index['entries'] if row['entity_kind']=='event'}
    for record,current in events:
        assert (record['event_id'] in event_entries)==(record['project_id']!=current)
        with current_store.connect() as db:assert db.execute('SELECT body FROM events WHERE event_id=?',(record['event_id'],)).fetchone()[0]==frozen[record['event_id']]
    view_cards={card['id']:card for card in current_store.skills()}
    for card,current in cards:
        assert view_cards[card['id']]['project_id']==current
        with current_store.connect() as db:assert db.execute('SELECT body FROM skills WHERE id=?',(card['id'],)).fetchone()[0]==canonical(card).decode()
    copied_outbox=EventOutbox(output/'candidate'/'outboxes'/'outbox-0.sqlite3')
    assert copied_outbox.pending()==outbox.pending()
    for record,current in events:
        assert copied_outbox.append(record['event_id'],record) is False
        assert current_store.ingest(record) is False
    assert migration.verify(output)['ok']


def test_explicit_config_epoch_without_runtime_marker_overrides_global_legacy_epoch(tmp_path):
    store=Store(tmp_path/'state');ident=retained_job(store,'04','completed',epoch=2)
    with store.connect() as db:db.execute("DELETE FROM experiment_runtime_identity WHERE entity_kind='job'")
    output=tmp_path/'prepared';migration.prepare(store.root,output,source_numbering_version=1)
    assert raw_job(Store(output/'candidate'),ident)['project_id']=='04'
    assert migration.verify(output)['counts']['terminal_job_updates']==0


def test_artifact_references_use_nearest_explicit_ancestor_epoch(tmp_path):
    store=Store(tmp_path/'state')
    document=dict(experiment_numbering_version=2,experiment_slug='so101_source_learning',project_id='04',items=[
        dict(experiment_numbering_version=1,experiment_slug='cross_embodiment_transfer',project_id='04'),
        dict(experiment_slug='cross_embodiment_transfer',project_id='06'),
        dict(experiment_numbering_version=1,experiment_slug='so101_source_learning',project_id='06'),
        dict(experiment_slug='so101_source_learning',project_id='04')],source_project_id='06')
    path=store.root/'mixed.json';raw=canonical(document);path.write_bytes(raw)
    pointers=['/project_id','/items/0/project_id','/items/1/project_id','/items/2/project_id','/items/3/project_id','/source_project_id']
    audit=dict(managed_root=str(store.root),files=[dict(path=str(path),sha256=migration.sha(raw),references=[dict(pointer=pointer,value=migration._json_pointer(document,pointer)) for pointer in pointers])])
    audit_path=tmp_path/'audit.json';audit_path.write_bytes(canonical(audit))
    plan=migration.dry_run(store.root,lineage_inventory=audit_path)
    mapped={row['pointer']:row for row in plan['entries'] if row['entity_kind']=='artifact_reference'}
    assert [mapped[pointer]['canonical_project_id'] for pointer in pointers]==['04','06','06','04','04','06']
    assert [mapped[pointer]['original_numbering_version'] for pointer in pointers]==[2,1,2,1,2,2]
    assert path.read_bytes()==raw and not plan['artifact_inventory']['unresolved']


def test_old_database_copy_boots_with_only_declared_empty_runtime_table_added(tmp_path):
    store=Store(tmp_path/'state');ident=retained_job(store,'04','running',epoch=1,config={'_execution_commit':'a'*40})
    with store.connect() as db:db.execute('DROP TABLE experiment_runtime_identity')
    output=tmp_path/'prepared';migration.prepare(store.root,output)
    index=json.loads((output/'experiment-projection.json').read_bytes())
    assert index['candidate_added_tables']==['experiment_runtime_identity']
    restored=Store(output/'candidate')
    assert restored.job(ident)['project_id']=='06' and raw_job(restored,ident)['project_id']=='04'
    with restored.connect() as db:assert db.execute('SELECT count(*) FROM experiment_runtime_identity').fetchone()[0]==0
    assert migration.verify(output)['ok']
    migration.rollback_copy(output,tmp_path/'rollback')
    with sqlite3.connect(tmp_path/'rollback'/'research.sqlite3') as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='experiment_runtime_identity'").fetchone()[0]==0


def test_conflicting_runtime_and_explicit_epochs_fail_before_any_live_change(tmp_path):
    store=Store(tmp_path/'state');ident=retained_job(store,'04','completed',epoch=1)
    with store.connect() as db:db.execute("UPDATE experiment_runtime_identity SET numbering_version=2,experiment_slug='so101_source_learning'")
    with pytest.raises(migration.MigrationError,match='epochs conflict'):
        migration.dry_run(store.root)
    assert raw_job(store,ident)['project_id']=='04'
