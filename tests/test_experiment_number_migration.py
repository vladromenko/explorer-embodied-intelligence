"""Copied state only: historical source bytes, active pins and all09 actions survive."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from explorer.store import Store
from explorer_shared.contracts import canonical,digest,event
from explorer_shared.outbox import EventOutbox

MODULE=Path(__file__).resolve().parents[1]/'scripts'/'migrate_experiment_numbers.py'
spec=importlib.util.spec_from_file_location('migration_numbers',MODULE)
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)


@pytest.fixture
def copied_state(tmp_path):
    state=tmp_path/'state';store=Store(state)
    ids={}
    for name,project,status in [('transfer','04','completed'),('so101','06','completed'),('personal','personal','completed'),('keyboard','02','completed'),('active','04','running')]:
        job=store.create_job('train_act',project,dict(source='retained-source',_execution_commit='e'*40))
        store.update_job(job['id'],status=status,result=dict(original_project_id=project,nine_neutral_commands=[0]*9))
        ids[name]=job['id']
    original_spec=dict(skill_id='unchanged-uuid',version=1,project_id='06',pipeline=dict(implemented=False))
    record=event('pi','06',dict(type='workflow.spec',spec=original_spec));store.ingest(record)
    for name in ('transfer','so101','personal'):
        store.add_skill(dict(id='model-'+name,task='retain',source_type='fixture',action_contract={'dimension':6},lineage={'job_id':ids[name]},evaluation={},status='offline_candidate',limitations=['not hardware']))
    outbox=EventOutbox(tmp_path/'outbox.sqlite3');outbox.append(record['event_id'],record)
    delivered=event('pi','04',dict(type='done'));outbox.append(delivered['event_id'],delivered);outbox.acknowledge(delivered['event_id'])
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
    assert candidate.job(f['ids']['active'])['project_id']=='04' # Pinned worker owns old semantics.
    assert candidate.job(f['ids']['active'])['config']['_execution_commit']=='e'*40
    assert old.job(f['ids']['transfer'])['project_id']=='04' and old.job(f['ids']['personal'])['project_id']=='personal'
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
    assert old.job(f['ids']['transfer'])['project_id']=='04' and old.job(f['ids']['so101'])['project_id']=='06'
    assert old.job(f['ids']['personal'])['project_id']=='personal'
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
