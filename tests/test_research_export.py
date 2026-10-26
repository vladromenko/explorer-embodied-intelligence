"""Selected scientific temp fixtures; no production state, hardware or optimizers."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from explorer.research_export import registry_from_store
from explorer.experiment_projection import ExperimentProjection
from explorer_shared.research_export import (ExportRejected, REGISTRY_SCHEMA,
    export_evidence, verify_export, restore_export, inline_node)
from explorer_shared.source_archive import canonical, sha

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'research/protocols/collection-export-v1.json'
EVAL = ROOT / 'research/benchmark/evaluate.py'
_spec = importlib.util.spec_from_file_location('retained_evaluation_for_export_test', EVAL)
evaluation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluation)


def frozen_protocol():
    value = dict(protocol_id='export-temp-protocol', schema_version='1.0', task_ids=['candy'],
        conditions=['SCRIPTED_SIM'], success_predicates={'candy': 'independent all-phase terminal contact'},
        exclusion_reasons=['camera_corrupt_before_start'], frozen_at='2026-10-26T00:00:00Z',
        split_manifest={'disjoint_by':['episode_ids','session_ids'],
            'train':dict(episode_ids=['train1'], session_ids=['train-session']),
            'development':dict(episode_ids=['dev1'], session_ids=['dev-session']),
            'sealed_test':dict(episode_ids=['sealed1'], session_ids=['sealed-session'])})
    value['sha256'] = evaluation.protocol_hash(value)
    return value


def export_fixture(tmp_path):
    managed = tmp_path / 'managed'
    managed.mkdir()
    protocol = frozen_protocol()
    trials = [dict(trial_id='trial-'+str(i), run_id='run-1', task_id='candy',
        condition='SCRIPTED_SIM', source_domain=domain, protocol_sha256=protocol['sha256'],
        outcome=outcome, validity='VALID', checkpoint_sha256='b'*64,
        evidence_artifact_ids=['raw1'] if outcome=='SUCCESS' else [], intervention=i==2)
        for i, (domain, outcome) in enumerate((('SIMULATION','SUCCESS'),('SIMULATION','FAILURE'),
            ('SIMULATION','UNKNOWN'),('PHYSICAL','UNKNOWN')))]
    data = {'frozen.json':canonical(protocol), 'trials.jsonl':b'\n'.join(canonical(row) for row in trials),
        'report.json':canonical(evaluation.summarize_trials(protocol,trials)),
        'evaluate.py':EVAL.read_bytes(), 'original.bin':b'EXPLICIT_RETAINED_SIMULATION_RGB_FIXTURE',
        'sealed.bin':b'SEALED_SOURCE_BYTES_NEVER_OPTIMIZED'}
    roles = {'frozen.json':'protocol','trials.jsonl':'trial_records','report.json':'derived_metrics',
        'evaluate.py':'implementation','original.bin':'dataset_payload','sealed.bin':'dataset_payload'}
    nodes=[]
    for name, raw in data.items():
        (managed/name).write_bytes(raw)
        nodes.append(dict(id=name, role=roles[name], source_id='job-1', root='state',
            relative_path=name, sha256=sha(raw), dependencies=[],
            split='sealed_test' if name=='sealed.bin' else 'development', usage='evaluation'))
    ids=[row['id'] for row in nodes]
    registry=dict(schema=REGISTRY_SCHEMA, root_ids=ids, nodes=nodes,
        run_ids=['run-1'], episode_ids=[], job_ids=['job-1'], source_commits=['a'*40],
        episodes=[], missing_dependencies=[], dependency_inventory_complete=True,
        metric_checks=[dict(protocol='frozen.json',trials='trials.jsonl',report='report.json',implementation='evaluate.py')])
    return managed, registry


def make_export(tmp_path, registry=None, managed=None, **kwargs):
    if registry is None:
        managed, registry=export_fixture(tmp_path)
    return export_evidence(tmp_path/'bundle', registry, {'state':managed},
        protocol_path=CONTRACT, protocol_sha256=sha(CONTRACT.read_bytes()), **kwargs)


def metric_verifier(blobs, hashes):
    assert hashes['implementation']==sha(EVAL.read_bytes())
    return evaluation.summarize_trials(json.loads(blobs['protocol']),
        [json.loads(line) for line in blobs['trials'].splitlines() if line.strip()])


def test_independent_restore_without_originals_recomputes_trials_preserves_failures_unknown_domains(tmp_path):
    managed, registry=export_fixture(tmp_path)
    result=make_export(tmp_path,registry,managed)
    assert result['verification']=='COMPLETE_UNVERIFIED'
    manifest=Path(result['manifest_path']); original_manifest=manifest.read_bytes()
    shutil.rmtree(managed)
    verified=verify_export(manifest,result['manifest_sha256'])
    assert verified['metric_checks']==[dict(report='report.json',status='NOT_RECOMPUTED')]
    receipt=restore_export(manifest,result['manifest_sha256'],tmp_path/'independent',metric_verifier=metric_verifier)
    assert receipt['verification']=='VERIFIED_COMPLETE' and receipt['originals_required'] is False
    assert receipt['scientific_success_claimed'] is False
    assert manifest.read_bytes()==original_manifest==(tmp_path/'independent/manifest.json').read_bytes()
    body=json.loads(manifest.read_bytes())
    sealed=next(row for row in body['file_manifest'] if row['id']=='sealed.bin')
    assert sealed['usage']=='evaluation' and sealed['split']=='sealed_test'
    report=json.loads((manifest.parent/next(row['relative_path'] for row in body['file_manifest'] if row['id']=='report.json')).read_bytes())
    sim=next(row for row in report['groups'] if row['source_domain']=='SIMULATION')
    assert sim['attempts']==3 and sim['counts']['SUCCESS']==1 and sim['counts']['UNKNOWN']==1
    assert report['total_recorded_attempts']==4 and len(report['groups'])==2
    assert sim['interventions']==1


def test_restore_without_metric_recomputation_cannot_claim_verified_complete(tmp_path):
    result=make_export(tmp_path)
    receipt=restore_export(result['manifest_path'],result['manifest_sha256'],tmp_path/'restore')
    assert receipt['verification']=='COMPLETE_UNVERIFIED'
    with pytest.raises(ExportRejected,match='must be new'):
        restore_export(result['manifest_path'],result['manifest_sha256'],tmp_path/'restore')


def test_missing_dependencies_unknown_inventory_and_episode_facts_stay_partial(tmp_path):
    managed, registry=export_fixture(tmp_path)
    registry['nodes'][0]['dependencies']=['not-retained']
    registry['dependency_inventory_complete']=False
    registry['episode_ids']=['unreviewed-episode']
    result=make_export(tmp_path,registry,managed)
    body=json.loads(Path(result['manifest_path']).read_bytes())
    assert result['verification']=='PARTIAL'
    assert {row['reason'] for row in body['missing_dependencies']}=={
        'DEPENDENCY_NOT_REGISTERED','DEPENDENCY_INVENTORY_NOT_QUALIFIED','EPISODE_METADATA_INCOMPLETE'}
    assert restore_export(result['manifest_path'],result['manifest_sha256'],tmp_path/'restore',metric_verifier=metric_verifier)['verification']=='PARTIAL'


@pytest.mark.parametrize('usage',['fitting','normalization','checkpoint_selection'])
def test_sealed_payload_cannot_be_given_optimizer_or_selection_role(tmp_path,usage):
    managed, registry=export_fixture(tmp_path)
    registry['nodes'][-1]['usage']=usage
    with pytest.raises(ExportRejected,match='Sealed'):
        make_export(tmp_path,registry,managed)
    assert not (tmp_path/'bundle').exists()


@pytest.mark.parametrize('value',[
    {'nested':[{'nodeToken':'do-not-export'}]}, {'wifi_settings':{'ssid':'private'}},
    {'authorization':'Bearer abcdefghijklmn'}, {'url':'https://host/x?token=abcdef'},
    {'key':'-----BEGIN OPENSSH PRIVATE KEY-----private'}])
def test_secret_bearing_original_is_rejected_not_sanitized_and_no_secret_copy_remains(tmp_path,value):
    managed, registry=export_fixture(tmp_path)
    raw=canonical(value)
    source=managed/'config.json';source.write_bytes(raw)
    registry['nodes'].append(dict(id='secret',role='raw_job_config',source_id='job-1',root='state',relative_path='config.json',sha256=sha(raw)))
    registry['root_ids'].append('secret')
    with pytest.raises(ExportRejected,match='(forbidden|credential)'):
        make_export(tmp_path,registry,managed)
    assert source.read_bytes()==raw
    retained=[path for path in (tmp_path/'bundle').rglob('*') if path.is_file()]
    assert [path.name for path in retained]==['export-rejected.json']
    assert b'do-not-export' not in retained[0].read_bytes()


def test_source_hash_change_during_selected_window_cannot_leave_complete_manifest(tmp_path):
    managed, registry=export_fixture(tmp_path)
    (managed/'original.bin').write_bytes(b'changed-since-selected')
    with pytest.raises(ExportRejected,match='SHA differs'):
        make_export(tmp_path,registry,managed)
    assert not (tmp_path/'bundle/manifest.json').exists()
    assert (tmp_path/'bundle/export-rejected.json').exists()


def test_source_symlink_outside_root_and_database_copies_are_refused(tmp_path):
    managed, registry=export_fixture(tmp_path)
    outside=tmp_path/'outside.bin';outside.write_bytes((managed/'original.bin').read_bytes())
    (managed/'original.bin').unlink();(managed/'original.bin').symlink_to(outside)
    with pytest.raises(ExportRejected,match='escapes managed'):
        make_export(tmp_path,registry,managed)
    registry['nodes'][0].update(relative_path='research.sqlite3')
    (managed/'research.sqlite3').write_bytes(b'db')
    with pytest.raises(ExportRejected,match='database path'):
        export_evidence(tmp_path/'other',registry,{'state':managed},protocol_path=CONTRACT,protocol_sha256=sha(CONTRACT.read_bytes()))


def test_internal_managed_symlink_alias_retained_but_export_is_regular_exact_copy(tmp_path):
    managed, registry=export_fixture(tmp_path)
    original=managed/'original.bin';alias=managed/'historical-raw.bin';alias.symlink_to(original)
    registry['nodes'][-2]['relative_path']='historical-raw.bin'
    result=make_export(tmp_path,registry,managed)
    body=json.loads(Path(result['manifest_path']).read_bytes())
    row=next(row for row in body['file_manifest'] if row['id']=='original.bin')
    assert row['source_locator']['original_was_symlink'] is True
    assert row['source_locator']['relative_path']=='historical-raw.bin'
    assert row['source_locator']['resolved_relative_path']=='original.bin'
    assert not (Path(result['manifest_path']).parent/row['relative_path']).is_symlink()


def test_tamper_unregistered_files_traversal_graph_cycle_and_duplicate_ids_rejected(tmp_path):
    managed, registry=export_fixture(tmp_path)
    bad=copy.deepcopy(registry);bad['nodes'][0]['dependencies']=['report.json'];bad['nodes'][2]['dependencies']=['frozen.json']
    with pytest.raises(ExportRejected,match='cycle'):
        make_export(tmp_path,bad,managed)
    bad=copy.deepcopy(registry);bad['nodes'].append(bad['nodes'][0])
    with pytest.raises(ExportRejected,match='Duplicate'):
        make_export(tmp_path,bad,managed)
    result=make_export(tmp_path,registry,managed)
    manifest=Path(result['manifest_path']);body=json.loads(manifest.read_bytes())
    extra=manifest.parent/'unregistered.json';extra.write_text('{}')
    with pytest.raises(ExportRejected,match='extra files'):
        verify_export(manifest,result['manifest_sha256'])
    extra.unlink()
    row=body['file_manifest'][0];member=manifest.parent/row['relative_path'];member.write_bytes(b'tampered')
    with pytest.raises(ExportRejected,match='size/SHA'):
        verify_export(manifest,result['manifest_sha256'])
    row['relative_path']='../outside.json';raw=canonical(body);manifest.write_bytes(raw)
    with pytest.raises(ExportRejected,match='relative'):
        verify_export(manifest,sha(raw))


def test_metric_report_changed_with_valid_bytes_still_fails_independent_recomputation(tmp_path):
    managed, registry=export_fixture(tmp_path)
    wrong=json.loads((managed/'report.json').read_bytes());wrong['total_recorded_attempts']=99
    raw=canonical(wrong);(managed/'report.json').write_bytes(raw)
    registry['nodes'][2]['sha256']=sha(raw)
    result=make_export(tmp_path,registry,managed)
    assert verify_export(result['manifest_path'],result['manifest_sha256'])['verification']=='COMPLETE_UNVERIFIED'
    with pytest.raises(ExportRejected,match='recomputation differs'):
        restore_export(result['manifest_path'],result['manifest_sha256'],tmp_path/'restore',metric_verifier=metric_verifier)
    assert not (tmp_path/'restore/restore-verification.json').exists()


def test_byte_bounds_protocol_binding_and_existing_destination_fail_closed(tmp_path):
    managed, registry=export_fixture(tmp_path)
    with pytest.raises(ExportRejected,match='protocol SHA'):
        export_evidence(tmp_path/'bundle',registry,{'state':managed},protocol_path=CONTRACT,protocol_sha256='0'*64)
    with pytest.raises(ExportRejected,match='byte bound'):
        make_export(tmp_path,registry,managed,limits={'max_file_bytes':3})
    with pytest.raises(ExportRejected,match='New export'):
        make_export(tmp_path,registry,managed)
    assert all(path.read_bytes() for path in managed.iterdir())


def temporary_store(tmp_path):
    root=tmp_path/'state';root.mkdir();path=root/'research.sqlite3'
    db=sqlite3.connect(path)
    db.executescript('''CREATE TABLE jobs(id TEXT PRIMARY KEY,kind,project_id,status,created,updated,config,result,progress,error);
    CREATE TABLE events(event_id TEXT PRIMARY KEY,body TEXT); CREATE TABLE skills(id TEXT PRIMARY KEY,body TEXT);
    CREATE TABLE experiment_runtime_identity(entity_kind,entity_id,numbering_version,experiment_slug);
    CREATE TABLE sessions(password); CREATE TABLE tickets(node_token);''')
    db.execute('INSERT INTO sessions VALUES (?)',('SESSION_PRIVATE_MUST_NEVER_EXPORT',))
    db.execute('INSERT INTO tickets VALUES (?)',('TICKET_PRIVATE_MUST_NEVER_EXPORT',))
    old=' { "project_id": "04", "_execution_commit": "'+('a'*40)+'" } '
    current=' {"project_id":"04", "experiment_numbering_version":2, "source_job_id":"old", "_execution_commit":"'+('a'*40)+'"} '
    for ident, config, status in (('old',old,'failed'),('current',current,'completed')):
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)',(ident,'qualification','04',status,1,2,config,'{"outcome":"UNKNOWN"}',1,None))
    db.execute('INSERT INTO experiment_runtime_identity VALUES (?,?,?,?)',('job','current',2,'so101_source_learning'))
    event=' { "project_id":"04", "payload": {"job_id":"old", "outcome":"FAILURE"} } '
    card=' {"id":"model", "lineage":{"job_id":"current"}, "physical_success":false} '
    db.execute('INSERT INTO events VALUES (?,?)',('negative-event',event))
    db.execute('INSERT INTO skills VALUES (?,?)',('model',card))
    db.commit();db.close()
    return SimpleNamespace(root=root,path=path,experiment_projection=ExperimentProjection(root)), old,current,event,card


def test_readonly_selected_registry_retains_exact_raw_mixed_epoch_parents_negative_events_and_cards(tmp_path):
    store,old,current,event,card=temporary_store(tmp_path)
    original=store.path.read_bytes()
    registry=registry_from_store(store,['current'],dependency_inventory_complete=True)
    assert store.path.read_bytes()==original
    assert set(registry['job_ids'])=={'current','old'}
    nodes={row['id']:row for row in registry['nodes']}
    assert nodes['job:old:config']['inline_utf8']==old
    assert nodes['job:current:config']['inline_utf8']==current
    assert nodes['event:negative-event']['inline_utf8']==event
    assert nodes['skill:model']['inline_utf8']==card
    oldview=json.loads(nodes['job:old:projection']['inline_utf8'])
    newview=json.loads(nodes['job:current:projection']['inline_utf8'])
    cardview=json.loads(nodes['skill:model:projection']['inline_utf8'])
    assert oldview['identity']['project_id']=='06' and newview['identity']['project_id']=='04'
    assert cardview['identity']['project_id']=='04' and cardview['identity']['numbering_provenance']['source']=='immutable_card_lineage_job_id'
    assert oldview['raw_sha256']==sha(old.encode())
    result=export_evidence(tmp_path/'bundle',registry,{'state':store.root},protocol_path=CONTRACT,protocol_sha256=sha(CONTRACT.read_bytes()))
    receipt=restore_export(result['manifest_path'],result['manifest_sha256'],tmp_path/'independent')
    assert receipt['verification']=='VERIFIED_COMPLETE'
    payload=b''.join(path.read_bytes() for path in (tmp_path/'bundle').rglob('*') if path.is_file())
    assert b'SESSION_PRIVATE' not in payload and b'TICKET_PRIVATE' not in payload
    assert b'"outcome":"UNKNOWN"' in payload and b'"outcome":"FAILURE"' in payload
    assert store.path.read_bytes()==original


def test_secret_config_is_hash_referenced_and_partial_without_leaking_original(tmp_path):
    store,*_=temporary_store(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE jobs SET config=? WHERE id=?',('{"node_token":"SUPER_PRIVATE_NODE_VALUE","_execution_commit":"'+('a'*40)+'"}','current'))
    registry=registry_from_store(store,['current'],dependency_inventory_complete=True)
    assert any(row['reason']=='SECRET_BEARING_ORIGINAL_WITHHELD' for row in registry['missing_dependencies'])
    assert 'SUPER_PRIVATE_NODE_VALUE' not in canonical(registry).decode()
    result=export_evidence(tmp_path/'bundle',registry,{'state':store.root},protocol_path=CONTRACT,protocol_sha256=sha(CONTRACT.read_bytes()))
    assert result['verification']=='PARTIAL'
    assert b'SUPER_PRIVATE_NODE_VALUE' not in b''.join(path.read_bytes() for path in (tmp_path/'bundle').rglob('*') if path.is_file())


def test_canonical_projection_raw_binding_cannot_silently_point_to_other_bytes(tmp_path):
    managed,registry=export_fixture(tmp_path)
    registry['nodes'].append(inline_node('view','canonical_projection','job-1',canonical({'project_id':'06'}),
        dependencies=['frozen.json'],projection_of={'id':'frozen.json','sha256':'0'*64}))
    registry['root_ids'].append('view')
    result=make_export(tmp_path,registry,managed)
    with pytest.raises(ExportRejected,match='projection lost'):
        verify_export(result['manifest_path'],result['manifest_sha256'])


def test_even_renamed_json_binary_payload_cannot_hide_node_credentials(tmp_path):
    managed,registry=export_fixture(tmp_path)
    raw=b'{"node_token":"SECRET_VALUE_RENAMED_BINARY"}'
    (managed/'original.bin').write_bytes(raw)
    registry['nodes'][-2]['sha256']=sha(raw)
    with pytest.raises(ExportRejected,match='forbidden'):
        make_export(tmp_path,registry,managed)
    assert not (tmp_path/'bundle/manifest.json').exists()


def test_actual_mutation_after_copy_fsync_is_detected_before_complete_manifest(tmp_path,monkeypatch):
    from explorer_shared import research_export as module
    managed,registry=export_fixture(tmp_path)
    original_copy=module._copy_source
    original_fsync=module.os.fsync
    mutated=[]
    def copy_with_mutation(source,target,expected,**options):
        if source.name=='original.bin':
            def fsync_then_change(descriptor):
                original_fsync(descriptor)
                if not mutated:
                    source.write_bytes(b'ACTUAL_CHANGE_AFTER_COPY_BEFORE_FINAL_IDENTITY_CHECK')
                    mutated.append(True)
            with monkeypatch.context() as context:
                context.setattr(module.os,'fsync',fsync_then_change)
                return original_copy(source,target,expected,**options)
        return original_copy(source,target,expected,**options)
    monkeypatch.setattr(module,'_copy_source',copy_with_mutation)
    with pytest.raises(ExportRejected,match='changed during copying'):
        make_export(tmp_path,registry,managed)
    assert mutated and not (tmp_path/'bundle/manifest.json').exists()


def test_restore_in_fresh_python_process_uses_only_copied_originals(tmp_path):
    import os
    import subprocess
    import sys
    managed,registry=export_fixture(tmp_path)
    result=make_export(tmp_path,registry,managed)
    shutil.rmtree(managed)
    driver='''import importlib.util,json,sys
from pathlib import Path
from explorer_shared.research_export import restore_export
from explorer_shared.source_archive import sha
spec=importlib.util.spec_from_file_location('server_pinned_metric',sys.argv[4])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def recompute(blobs,hashes):
    assert hashes['implementation']==sha(Path(sys.argv[4]).read_bytes())
    return module.summarize_trials(json.loads(blobs['protocol']),[json.loads(line) for line in blobs['trials'].splitlines() if line.strip()])
print(json.dumps(restore_export(sys.argv[1],sys.argv[2],sys.argv[3],metric_verifier=recompute)))
'''
    process=subprocess.run([sys.executable,'-c',driver,result['manifest_path'],result['manifest_sha256'],str(tmp_path/'fresh-process-restore'),str(EVAL)],
        env=dict(os.environ,PYTHONPATH=str(ROOT/'shared')),capture_output=True,text=True,timeout=15)
    assert process.returncode==0,process.stderr
    receipt=json.loads(process.stdout)
    assert receipt['verification']=='VERIFIED_COMPLETE'
    assert receipt['metric_checks']==[{'report':'report.json','status':'RECOMPUTED_MATCH'}]


@pytest.mark.parametrize('name',['credentials/.env','credentials/private.key','.ssh/id_ed25519'])
def test_managed_symlink_cannot_alias_forbidden_secret_path_even_with_safe_name(tmp_path,name):
    managed,registry=export_fixture(tmp_path)
    secret=managed/name;secret.parent.mkdir(parents=True);secret.write_bytes(b'PASSWORD=private-value')
    original=managed/'original.bin';original.unlink();original.symlink_to(secret)
    registry['nodes'][-2]['sha256']=sha(secret.read_bytes())
    with pytest.raises(ExportRejected,match='Credential/database path'):
        make_export(tmp_path,registry,managed)
    assert secret.read_bytes()==b'PASSWORD=private-value'
    assert not (tmp_path/'bundle/manifest.json').exists()


def test_plain_environment_credential_in_safe_binary_name_is_rejected(tmp_path):
    managed,registry=export_fixture(tmp_path)
    raw=b'PASSWORD=private-value\n'
    (managed/'original.bin').write_bytes(raw);registry['nodes'][-2]['sha256']=sha(raw)
    with pytest.raises(ExportRejected,match='Embedded credential'):
        make_export(tmp_path,registry,managed)


def test_secret_withheld_export_can_restore_only_as_partial(tmp_path):
    store,*_=temporary_store(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE jobs SET config=? WHERE id=?',('{"node_token":"PRIVATE","_execution_commit":"'+('a'*40)+'"}','current'))
    registry=registry_from_store(store,['current'],dependency_inventory_complete=True)
    result=export_evidence(tmp_path/'bundle',registry,{'state':store.root},protocol_path=CONTRACT,protocol_sha256=sha(CONTRACT.read_bytes()))
    receipt=restore_export(result['manifest_path'],result['manifest_sha256'],tmp_path/'restore')
    assert receipt['verification']=='PARTIAL'


def test_safe_failure_error_retained_exact_and_secret_error_stays_explicitly_partial(tmp_path):
    store,*_=temporary_store(tmp_path)
    failure='CAD_COLLISION: all recorded unsuccessful requests retained\n'
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE jobs SET error=? WHERE id=?',(failure,'old'))
        db.execute('UPDATE jobs SET error=? WHERE id=?',('Bearer PRIVATE_NODE_TOKEN_VALUE','current'))
    registry=registry_from_store(store,['current'],dependency_inventory_complete=True)
    nodes={row['id']:row for row in registry['nodes']}
    assert nodes['job:old:error']['inline_utf8']==failure
    assert 'job:current:error' not in nodes
    assert any(row.get('artifact_id')=='job:current:error' and row['reason']=='SECRET_BEARING_ORIGINAL_WITHHELD' for row in registry['missing_dependencies'])
    result=export_evidence(tmp_path/'bundle',registry,{'state':store.root},protocol_path=CONTRACT,protocol_sha256=sha(CONTRACT.read_bytes()))
    assert restore_export(result['manifest_path'],result['manifest_sha256'],tmp_path/'restore')['verification']=='PARTIAL'
    assert b'PRIVATE_NODE_TOKEN_VALUE' not in b''.join(path.read_bytes() for path in (tmp_path/'bundle').rglob('*') if path.is_file())


def test_independent_verifier_rechecks_graph_and_parent_table_after_manifest_rebinding(tmp_path):
    managed,registry=export_fixture(tmp_path)
    result=make_export(tmp_path,registry,managed)
    manifest=Path(result['manifest_path']);body=json.loads(manifest.read_bytes())
    body['parents']=[dict(child='report.json',parent='unregistered')]
    raw=canonical(body);manifest.write_bytes(raw)
    with pytest.raises(ExportRejected,match='parent edge table'):
        verify_export(manifest,sha(raw))
