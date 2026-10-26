"""Actual temporary Store and filesystem only, no live state or devices."""
import json
import os
from pathlib import Path
import sqlite3
import tarfile

import pytest

from explorer.store import Store
from explorer.research_export_job import (ResearchExportBlocked, request_settings,
    run_research_export, download_validation, DISK_OVERHEAD_BYTES)
from explorer_shared.source_archive import canonical, sha


def sources(tmp_path):
    store=Store(tmp_path/'state')
    job=store.create_job('software_qualification','06',dict(_execution_commit='a'*40))
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    (folder/'trace.json').write_bytes(b' { "outcome":"FAILURE", "physical_io":false } ')
    (folder/'rgb.png').write_bytes(b'EXPLICIT_SOFTWARE_FIXTURE_NOT_A_REAL_CAMERA_IMAGE')
    store.update_job(job['id'],status='failed',result=dict(outcome='FAILURE'),error='all unsuccessful frames retained')
    return store,store.job(job['id']),folder


def launch(store,settings):
    job=store.create_job('research_export','06',settings)
    store.update_job(job['id'],status='running')
    return store.job(job['id'])


def adversarial_result_sql(store, job_id, value):
    """TEMP fixture corruption, bypassing the correct immutable terminal API."""
    with store.connect() as db:
        db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(value), job_id))


def test_worker_real_Store_exports_registered_TAR_after_independent_restore_and_safe_download(tmp_path):
    store,source,folder=sources(tmp_path)
    with store.connect() as db:
        db.execute('INSERT INTO sessions VALUES (?,?,?)',('PRIVATE_SESSION',9999,'PRIVATE_CSRF'))
    original={p.name:p.read_bytes() for p in folder.iterdir()}
    config=request_settings(store,[source['id']])
    assert config['estimated_payload_bytes']>sum(map(len,original.values()))
    job=launch(store,config)
    result=run_research_export(store,job,config)
    assert result['verification']=='PARTIAL' and result['missing_dependency_count']>0
    assert result['disk_budget_bytes']==3*result['estimated_payload_bytes']+DISK_OVERHEAD_BYTES
    assert result['actual_payload_bytes']<=result['estimated_payload_bytes']
    assert Path(result['restore_verification_path']).is_file()
    assert result['files']>=4 and result['scientific_success_claimed'] is False
    tar=Path(result['evidence_tar_path'])
    with tarfile.open(tar) as archive:
        members=archive.getmembers()
        assert all(member.isfile() and not member.issym() and not member.name.startswith('/') and '..' not in Path(member.name).parts for member in members)
        assert any(member.name=='restore-verification.json' for member in members)
        payload=b''.join(archive.extractfile(member).read() for member in members)
        assert b'PRIVATE_SESSION' not in payload and b'PRIVATE_CSRF' not in payload
        assert original['trace.json'] in payload and original['rgb.png'] in payload
    with pytest.raises(ResearchExportBlocked,match='COMPLETED'):
        download_validation(store,job['id'])
    store.update_job(job['id'],status='completed',result=result)
    valid=download_validation(store,job['id'])
    assert valid['path']==tar and valid['sha256']==sha(tar.read_bytes())
    assert valid['size_bytes']==tar.stat().st_size and valid['media_type']=='application/x-tar'
    assert run_research_export(store,store.job(job['id']),config)==result
    assert {p.name:p.read_bytes() for p in folder.iterdir()}==original


@pytest.mark.parametrize('values',[[],['nothex'],['A'*32],['0'*32]*33,{'job_ids':['0'*32]}])
def test_browser_ids_only_validation_precedes_artifact_writes(tmp_path,values):
    store,source,folder=sources(tmp_path)
    with pytest.raises(ResearchExportBlocked,match='TERMINAL_JOB_IDS_REQUIRED'):
        request_settings(store,values)
    assert not (store.root/'artifacts/research-export-requests').exists()


def test_active_unknown_or_recursive_export_jobs_are_rejected(tmp_path):
    store,source,folder=sources(tmp_path)
    with pytest.raises(ResearchExportBlocked,match='NOT_FOUND'):
        request_settings(store,['0'*32])
    active=store.create_job('train_act','04',{})
    with pytest.raises(ResearchExportBlocked,match='NOT_TERMINAL'):
        request_settings(store,[active['id']])
    recursive=store.create_job('research_export','06',{})
    store.update_job(recursive['id'],status='completed')
    with pytest.raises(ResearchExportBlocked,match='RECURSIVE_EXPORT'):
        request_settings(store,[recursive['id']])


def test_queue_payload_estimate_and_request_source_cannot_be_forged(tmp_path):
    store,source,folder=sources(tmp_path)
    settings=request_settings(store,[source['id']])
    job=launch(store,settings)
    forged=dict(settings,estimated_payload_bytes=0)
    with pytest.raises(ResearchExportBlocked,match='QUEUED_SOURCE_SELECTION_CHANGED'):
        run_research_export(store,job,forged)
    forged=dict(settings,request_manifest='../research.sqlite3')
    with pytest.raises(ResearchExportBlocked,match='SERVER_PREPARED_REQUEST'):
        run_research_export(store,job,forged)
    forged=dict(settings,job_ids=['0'*32])
    with pytest.raises(ResearchExportBlocked,match='QUEUED_SOURCE_SELECTION_CHANGED'):
        run_research_export(store,job,forged)
    path=store.root/settings['request_manifest'];path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ResearchExportBlocked,match='REQUEST_SHA_MISMATCH'):
        run_research_export(store,job,settings)


def test_source_mutations_after_enqueue_rejected_and_raw_preserved(tmp_path):
    store,source,folder=sources(tmp_path)
    settings=request_settings(store,[source['id']]);job=launch(store,settings)
    (folder/'trace.json').write_bytes(b'{"outcome":"CHANGED"}')
    with pytest.raises(Exception,match='SHA differs'):
        run_research_export(store,job,settings)
    assert (folder/'trace.json').read_bytes()==b'{"outcome":"CHANGED"}'
    assert not (store.root/'jobs'/job['id']/'evidence.tar').exists()


def test_new_file_or_changed_job_row_after_enqueue_cannot_be_silently_ignored(tmp_path):
    store,source,folder=sources(tmp_path)
    settings=request_settings(store,[source['id']]);job=launch(store,settings)
    (folder/'late.json').write_text('{}')
    with pytest.raises(ResearchExportBlocked,match='SOURCE_FOLDER_CHANGED'):
        run_research_export(store,job,settings)
    (folder/'late.json').unlink()
    adversarial_result_sql(store,source['id'],{'new':'revision'})
    with pytest.raises(ResearchExportBlocked,match='SOURCE_JOBS_CHANGED'):
        run_research_export(store,job,settings)


def test_unknown_outside_secret_and_raw_SQLite_dependencies_are_partial_never_copied(tmp_path):
    store,source,folder=sources(tmp_path)
    (folder/'runtime.env').write_text('NODE_TOKEN=PRIVATE_ENV_VALUE')
    (folder/'config.json').write_text('{"node_token":"PRIVATE_JSON_VALUE"}')
    os.link(store.path,folder/'weights.pt')
    outside=tmp_path/'outside-secret.json';outside.write_text('{"node_token":"PRIVATE_OUTSIDE_VALUE"}')
    (folder/'pointer.json').write_text(json.dumps({'source_files_sha256':{str(outside):sha(outside.read_bytes())}}))
    config=request_settings(store,[source['id']]);job=launch(store,config)
    result=run_research_export(store,job,config)
    assert result['verification']=='PARTIAL'
    with tarfile.open(result['evidence_tar_path']) as archive:
        payload=b''.join(archive.extractfile(member).read() for member in archive if member.isfile())
        assert all(secret not in payload for secret in (b'PRIVATE_ENV_VALUE',b'PRIVATE_JSON_VALUE',b'PRIVATE_OUTSIDE_VALUE',b'SQLite format 3'))
    assert outside.read_text()=='{"node_token":"PRIVATE_OUTSIDE_VALUE"}'


def test_scientific_parent_jobs_and_hash_bound_external_episode_payloads_are_included(tmp_path):
    store,parent,parent_folder=sources(tmp_path)
    data=store.root/'datasets'/'bound';data.mkdir(parents=True)
    raw=data/'episode.npz';raw.write_bytes(b'SOFTWARE_FIXTURE_EPISODE_BYTES')
    manifest=data/'manifest.json';manifest.write_bytes(canonical({'episodes':[dict(file='episode.npz',sha256=sha(raw.read_bytes()))]}))
    child=store.create_job('train_act','06',dict(source_job_id=parent['id'],dataset_manifest=str(manifest),dataset_manifest_sha256=sha(manifest.read_bytes()),_execution_commit='a'*40))
    own=store.root/'jobs'/child['id'];own.mkdir()
    (own/'result.json').write_text('{"physical_success":false}')
    store.update_job(child['id'],status='completed')
    config=request_settings(store,[child['id']])
    assert set(config['source_job_ids'])=={parent['id'],child['id']}
    result=run_research_export(store,launch(store,config),config)
    with tarfile.open(result['evidence_tar_path']) as archive:
        payload=b''.join(archive.extractfile(member).read() for member in archive if member.isfile())
    assert raw.read_bytes() in payload and (parent_folder/'trace.json').read_bytes() in payload


def test_archive_member_auth_symlinks_and_SQLite_are_inspected_without_extracting(tmp_path):
    import io
    store,source,folder=sources(tmp_path)
    bad=folder/'raw-source.tar'
    with tarfile.open(bad,'w') as archive:
        info=tarfile.TarInfo('../auth/token.json');payload=b'{"node_token":"PRIVATE_ARCHIVE_VALUE"}'
        info.size=len(payload);archive.addfile(info,io.BytesIO(payload))
    config=request_settings(store,[source['id']]);job=launch(store,config)
    result=run_research_export(store,job,config)
    assert result['verification']=='PARTIAL'
    with tarfile.open(result['evidence_tar_path']) as archive:
        payload=b''.join(archive.extractfile(member).read() for member in archive if member.isfile())
    assert b'PRIVATE_ARCHIVE_VALUE' not in payload
    assert not (store.root/'auth').exists()


def test_fixed_download_rejects_tamper_wrong_kind_wrong_path_and_symlink(tmp_path):
    store,source,folder=sources(tmp_path)
    config=request_settings(store,[source['id']]);job=launch(store,config)
    result=run_research_export(store,job,config)
    store.update_job(job['id'],status='completed',result=result)
    with pytest.raises(ResearchExportBlocked,match='COMPLETED_RESEARCH_EXPORT'):
        download_validation(store,source['id'])
    changed=dict(result,evidence_tar_path=str(folder/'trace.json'))
    adversarial_result_sql(store,job['id'],changed)
    with pytest.raises(ResearchExportBlocked,match='PATH_INVALID'):
        download_validation(store,job['id'])
    adversarial_result_sql(store,job['id'],result)
    path=Path(result['evidence_tar_path']);raw=path.read_bytes();path.write_bytes(raw+b'CHANGED')
    with pytest.raises(ResearchExportBlocked,match='SHA_OR_SIZE'):
        download_validation(store,job['id'])
    path.unlink();path.symlink_to(folder/'trace.json')
    with pytest.raises(ResearchExportBlocked,match='PATH_INVALID'):
        download_validation(store,job['id'])


def test_selected_ids_are_server_sorted_and_deduplicated_before_enqueue(tmp_path):
    store,source,folder=sources(tmp_path)
    second=store.create_job('software_qualification','04',{'_execution_commit':'b'*40})
    store.update_job(second['id'],status='completed')
    config=request_settings(store,[source['id'],second['id'],source['id']])
    assert config['job_ids']==sorted({source['id'],second['id']})
    assert len(config['job_ids'])==2


def test_logical_state_symlink_keeps_historical_absolute_payload_references_usable(tmp_path):
    store,source,folder=sources(tmp_path)
    actual=store.root
    logical=tmp_path/'historical-state-locator';logical.symlink_to(actual,target_is_directory=True)
    alias_store=Store(logical)
    data=actual/'datasets'/'legacy';data.mkdir(parents=True)
    episode=data/'episode.npz';episode.write_bytes(b'IMMUTABLE_OLD_ABSOLUTE_REFERENCE')
    manifest=data/'manifest.json';manifest.write_bytes(canonical({'source_files_sha256':{str(logical/'datasets/legacy/episode.npz'):sha(episode.read_bytes())}}))
    adversarial_result_sql(store,source['id'],dict(dataset_manifest=str(logical/'datasets/legacy/manifest.json'),dataset_manifest_sha256=sha(manifest.read_bytes())))
    config=request_settings(alias_store,[source['id']]);job=launch(alias_store,config)
    result=run_research_export(alias_store,job,config)
    with tarfile.open(result['evidence_tar_path']) as archive:
        payload=b''.join(archive.extractfile(member).read() for member in archive if member.isfile())
    assert episode.read_bytes() in payload
    assert str(logical/'datasets/legacy/episode.npz').encode() in payload


def test_request_preparation_refuses_aliased_artifacts_before_writing_outside_state(tmp_path):
    store,source,folder=sources(tmp_path)
    outside=tmp_path/'outside';outside.mkdir()
    (store.root/'artifacts').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ResearchExportBlocked,match='DIRECTORY_ALIAS'):
        request_settings(store,[source['id']])
    assert list(outside.iterdir())==[]


def test_missing_parent_metadata_is_partial_instead_of_dropping_available_terminal_child(tmp_path):
    store,source,folder=sources(tmp_path)
    with store.connect() as db:
        config=json.loads(db.execute('SELECT config FROM jobs WHERE id=?',(source['id'],)).fetchone()[0])
        config['source_job_id']='0'*32
        db.execute('UPDATE jobs SET config=? WHERE id=?',(json.dumps(config),source['id']))
    settings=request_settings(store,[source['id']])
    assert settings['source_job_ids']==[source['id']]
    result=run_research_export(store,launch(store,settings),settings)
    assert result['verification']=='PARTIAL'
    assert any(row['reason']=='SELECTED_JOB_NOT_FOUND' for row in result['missing_dependencies'])


def test_result_contract_and_job_source_request_binding(tmp_path):
    store,source,folder=sources(tmp_path)
    config=request_settings(store,[source['id']]);job=launch(store,config)
    forged=dict(job,config={'request_sha256':'0'*64})
    with pytest.raises(ResearchExportBlocked,match='JOB_SOURCE_REQUEST_MISMATCH'):
        run_research_export(store,forged,config)
    result=run_research_export(store,job,config)
    assert result['archive_sha256']==result['evidence_tar_sha256']
    assert result['manifest_sha256']==result['export_manifest_sha256']
    assert result['total_payload_bytes']==result['actual_payload_bytes']
    assert result['missing_dependencies_count']==result['missing_dependency_count']
    assert result['independent_restore_verified'] is True and result['verification']=='PARTIAL'
