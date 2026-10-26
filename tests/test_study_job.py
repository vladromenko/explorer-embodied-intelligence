"""Temporary typed Store/queue Study integration; no live data or hardware."""
from pathlib import Path
import json

import pytest

from test_study_evidence import study
from explorer import study_job
from explorer.store import Store
from explorer.queue_policy import GIB,default_metadata,queue_plan,validate_options
from explorer_shared.study_evidence import StudyRejected


@pytest.fixture
def prepared(study,tmp_path,monkeypatch):
    store=Store(tmp_path/'state')
    def definition(identity):
        if identity!=study.study_id:raise StudyRejected('Unknown server-registered Study ID')
        return study
    monkeypatch.setattr(study_job,'registered_definition',definition)
    return store,study


def launch(store,settings):
    job=store.create_job('study_export','06',settings);store.update_job(job['id'],status='running')
    return store.job(job['id'])


def test_CC_preparation_only_stats_originals_and_pins_estimate_before_authoritative_job_creation(prepared,monkeypatch):
    store,source=prepared
    def forbidden(*args,**kwargs):raise AssertionError('CC must not inspect payloads')
    monkeypatch.setattr(study_job,'inspect_study',forbidden)
    original=Path.open
    def open_guard(path,*args,**kwargs):
        if path.is_relative_to(source.campaign_root.parent):raise AssertionError('Source bytes read during CC preparation')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',open_guard)
    config=study_job.request_settings(store,source.study_id)
    assert type(config['estimated_payload_bytes']) is int and config['estimated_payload_bytes']>0
    assert store.jobs()==[]
    assert config==study_job.request_settings(store,source.study_id)
    request=store.root/config['request_manifest'];body=json.loads(request.read_bytes())
    assert body['inventory']['file_count']>10 and body['disk_budget_bytes']==3*config['estimated_payload_bytes']+256*1024**2
    assert 'campaign_root' not in config and 'root' not in config and 'source_path' not in config


def test_actual_worker_Store_TAR_restore_and_fixed_completed_download(prepared):
    store,source=prepared;settings=study_job.request_settings(store,source.study_id);job=launch(store,settings)
    originals={path:path.read_bytes() for path in source.campaign_root.rglob('*') if path.is_file()}
    result=study_job.run_study_export(store,job,settings)
    assert result['independent_restore_verified'] and result['independent_restore_source']=='exact_TAR_unpack_in_new_root'
    assert not result['independent_restore_process'] and result['verification']=='PARTIAL'
    assert result['actual_payload_bytes']==settings['estimated_payload_bytes']
    assert result['outcomes']=={'synthetic-success':'SUCCESS','synthetic-failure':'FAILURE'}
    assert result['target_optimizer_allowed'] is False and result['originals_deleted'] is False
    folder=store.root/'jobs'/job['id']
    assert not (folder/'.staging-bundle').exists() and (folder/'bundle/manifest.json').is_file()
    assert (folder/'restored/campaign/synthetic-failure/target/result.json').is_file()
    assert {path:path.read_bytes() for path in originals}==originals
    assert study_job.run_study_export(store,store.job(job['id']),settings)==result
    with pytest.raises(study_job.StudyJobBlocked,match='COMPLETED'):
        study_job.download_validation(store,job['id'])
    store.complete_job(job['id'],result)
    download=study_job.download_validation(store,job['id'])
    assert download['path']==folder/'study.tar' and download['sha256']==result['archive_sha256']
    assert download['size_bytes']==result['archive_bytes'] and download['verification']=='PARTIAL'


def test_unknown_browser_path_never_prepares_or_allocates_store_artifacts(prepared):
    store,source=prepared
    for identity in ['../campaign','/etc/passwd',{},None]:
        with pytest.raises(StudyRejected):study_job.request_settings(store,identity)
    assert not (store.root/'artifacts/study-export-requests').exists()


def test_request_and_source_mutation_rejected_before_payload_inspection(prepared,monkeypatch):
    store,source=prepared;settings=study_job.request_settings(store,source.study_id);job=launch(store,settings)
    called=[];monkeypatch.setattr(study_job,'inspect_study',lambda *a:called.append(a))
    changed=dict(settings,estimated_payload_bytes=0)
    with pytest.raises(study_job.StudyJobBlocked,match='JOB_STUDY_SOURCE_REQUEST_MISMATCH'):
        study_job.run_study_export(store,job,changed)
    changed=dict(job,config=changed)
    with pytest.raises(study_job.StudyJobBlocked,match='JOB_STUDY_SOURCE_REQUEST_MISMATCH'):
        study_job.run_study_export(store,changed,changed['config'])
    request=store.root/settings['request_manifest'];raw=request.read_bytes();request.write_bytes(raw+b' ')
    with pytest.raises(study_job.StudyJobBlocked,match='REQUEST_SHA_MISMATCH'):
        study_job.run_study_export(store,job,settings)
    request.write_bytes(raw)
    (source.campaign_root/'new-source-file.json').write_text('{}')
    with pytest.raises(study_job.StudyJobBlocked,match='METADATA_CHANGED_AFTER_ENQUEUE'):
        study_job.run_study_export(store,job,settings)
    assert not called and not (store.root/'jobs'/job['id']/'study.tar').exists()


def test_partial_failure_is_retained_and_cannot_be_silently_reused(prepared,monkeypatch):
    store,source=prepared;settings=study_job.request_settings(store,source.study_id);job=launch(store,settings)
    def fail(*args,**kwargs):raise RuntimeError('explicit restore failure fixture')
    monkeypatch.setattr(study_job,'restore_study_bundle',fail)
    with pytest.raises(RuntimeError,match='restore failure'):study_job.run_study_export(store,job,settings)
    folder=store.root/'jobs'/job['id']
    assert (folder/'study.tar').is_file() and (folder/'bundle/manifest.json').is_file()
    assert not (folder/'export-result.json').exists()
    with pytest.raises(study_job.StudyJobBlocked,match='INTERRUPTED_STUDY_EXPORT_RETAINED'):
        study_job.run_study_export(store,job,settings)
    assert (source.campaign_root/'synthetic-failure/target/result.json').is_file()


def test_cancel_during_inspection_keeps_artifacts_and_never_publishes_receipt(prepared,monkeypatch):
    store,source=prepared;settings=study_job.request_settings(store,source.study_id);job=launch(store,settings)
    inspect=study_job.inspect_study
    def inspect_then_cancel(definition):
        result=inspect(definition);store.request_job_operation(job['id'],'cancel');return result
    monkeypatch.setattr(study_job,'inspect_study',inspect_then_cancel)
    with pytest.raises(study_job.StudyJobBlocked,match='NOT_RUNNING_OR_CANCELLED'):
        study_job.run_study_export(store,job,settings)
    assert store.job(job['id'])['status']=='cancel_requested'
    folder=store.root/'jobs'/job['id'];assert not (folder/'export-result.json').exists() and not (folder/'study.tar').exists()


def test_cached_scope_and_fixed_download_tamper_cannot_be_promoted(prepared):
    store,source=prepared;settings=study_job.request_settings(store,source.study_id);job=launch(store,settings)
    result=study_job.run_study_export(store,job,settings);folder=store.root/'jobs'/job['id']
    cached=folder/'export-result.json';body=json.loads(cached.read_bytes());body['completeness']='COMPLETE';cached.write_text(json.dumps(body))
    with pytest.raises(study_job.StudyJobBlocked,match='CACHED_STUDY_CLAIM_CHANGED'):
        study_job.run_study_export(store,job,settings)
    store.complete_job(job['id'],result)
    archive=folder/'study.tar';archive.write_bytes(archive.read_bytes()+b'tamper')
    with pytest.raises(study_job.StudyJobBlocked,match='SHA_OR_SIZE_MISMATCH'):
        study_job.download_validation(store,job['id'])
    archive.unlink();archive.symlink_to(source.audit.path)
    with pytest.raises(study_job.StudyJobBlocked):study_job.download_validation(store,job['id'])


def test_managed_request_and_job_aliases_never_write_outside_state(prepared,tmp_path):
    store,source=prepared;outside=tmp_path/'outside';outside.mkdir()
    (store.root/'artifacts').symlink_to(outside,target_is_directory=True)
    with pytest.raises(study_job.StudyJobBlocked,match='ALIAS'):study_job.request_settings(store,source.study_id)
    assert not list(outside.iterdir())


def test_study_queue_has_measured_scope_reservation_nonpreemption_and_single_IO(prepared):
    store,source=prepared;settings=study_job.request_settings(store,source.study_id)
    selected=store.create_job('study_export','06',dict(settings,device='cuda'))
    metadata=default_metadata(selected)
    assert metadata['resources']==dict(cpu_cores=1,memory_bytes=2*GIB,disk_bytes=3*settings['estimated_payload_bytes']+GIB//4,gpu_slots=0)
    assert metadata['preemptible'] is False
    with pytest.raises(ValueError,match='preemptible'):validate_options(selected,metadata,dict(preemptible=True))
    ingestion=store.create_job('mobile_import','02',{});store.update_job(ingestion['id'],status='running');ingestion=store.job(ingestion['id'])
    jobs=[selected,ingestion];schedules={row['id']:default_metadata(row) for row in jobs}
    active=[dict(job_id=ingestion['id'],resources=schedules[ingestion['id']]['resources'],resident_memory_bytes=GIB//4)]
    host=dict(available_memory_bytes=12*GIB,free_disk_bytes=20*GIB,cpu_count=8,load_1m=0,thermal=[])
    result=queue_plan(jobs,schedules,host,active)
    assert 'single_ingest_disk_network_budget_occupied' in result['waiting'][selected['id']]['reasons']
    selected['config']['estimated_payload_bytes']=None
    result=queue_plan([selected],{selected['id']:default_metadata(selected)},host)
    assert 'study_export_payload_estimate_missing_or_invalid' in result['waiting'][selected['id']]['reasons']


def test_new_kind_final_entry_cancel_never_allocates_job_folder_or_calls_payload_worker(prepared,monkeypatch):
    from explorer import job_runner
    store,source=prepared;job=store.create_job('study_export','06',{});store.request_job_operation(job['id'],'cancel')
    monkeypatch.setattr(job_runner,'load',lambda:dict(data_dir=str(store.root),nodes=[]))
    monkeypatch.delenv('EXPLORER_WORKER_LEASE_NONCE',raising=False)
    called=[];monkeypatch.setattr(study_job,'run_study_export',lambda *a:called.append(a))
    job_runner.main(job['id'])
    assert store.job(job['id'])['status']=='cancelled' and not called
    assert not (store.root/'jobs'/job['id']).exists()


def test_lazy_job_runner_branch_passes_immutable_job_config_and_commits_Store_result(prepared,monkeypatch):
    from explorer import job_runner,worker_bootstrap
    store,source=prepared;settings=study_job.request_settings(store,source.study_id);job=launch(store,settings)
    monkeypatch.setattr(job_runner,'load',lambda:dict(data_dir=str(store.root),nodes=[]))
    monkeypatch.setattr(worker_bootstrap,'prework_guard',lambda *a:True)
    job_runner.main(job['id'])
    completed=store.job(job['id'])
    assert completed['status']=='completed' and completed['result']['study_id']==source.study_id
    assert completed['result']['independent_restore_verified'] and completed['result']['physical_io'] is False


def test_same_hash_registered_context_survives_immutable_release_copy_relocation(prepared,tmp_path,monkeypatch):
    from dataclasses import replace
    from explorer_shared.study_evidence import ContextFile
    store,source=prepared;settings=study_job.request_settings(store,source.study_id);job=launch(store,settings)
    other=tmp_path/'other-immutable-release';other.mkdir()
    audit=other/'audit.json';audit.write_bytes(source.audit.path.read_bytes())
    benchmark=other/'benchmark.py';benchmark.write_bytes(source.benchmark.path.read_bytes())
    relocated=replace(source,audit=ContextFile(audit,source.audit.sha256,source.audit.role),
        benchmark=ContextFile(benchmark,source.benchmark.sha256,source.benchmark.role))
    monkeypatch.setattr(study_job,'registered_definition',lambda identity:relocated)
    result=study_job.run_study_export(store,job,settings)
    assert result['independent_restore_verified'] and result['study_id']==source.study_id
