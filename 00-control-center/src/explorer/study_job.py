"""ENGINEERING_GLUE: registered Study export through the shared durable queue.

The CC prepares bounded stat metadata only. All payload inspection, hashing,
TAR and independent restore run in the isolated worker. No archived program,
physics, policy, database export, camera or motor is executed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

from explorer_shared.study_evidence import (MAX_BYTES,MAX_FILES,MAX_FILE_BYTES,
    StudyRejected,canonical,inspect_study,relative_name,sha_file)
from explorer_shared.study_bundle import (archive_study_bundle,export_study_bundle,
    restore_study_bundle,unpack_study_archive,verify_study_bundle)
from .study_registry import registered_definition

SCHEMA='explorer_study_export_request_v1'
KIND='study_export'
REQUEST_DIRECTORY='artifacts/study-export-requests'
MAX_METADATA_BYTES=8*1024**2
MAX_ARCHIVE_BYTES=MAX_BYTES+32*1024**2
DISK_OVERHEAD_BYTES=256*1024**2
ID=re.compile(r'[0-9a-f]{32}\Z')
SHA=re.compile(r'[0-9a-f]{64}\Z')


class StudyJobBlocked(StudyRejected):
    def __init__(self,code,detail=''):
        self.code,self.detail=code,detail
        super().__init__(code+(': '+detail if detail else ''))


def _hash(value):return hashlib.sha256(canonical(value)).hexdigest()


def _metadata(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_METADATA_BYTES:
        raise StudyJobBlocked('STUDY_METADATA_PATH_OR_BOUND_INVALID')
    def invalid(value):raise StudyJobBlocked('STUDY_NONFINITE_METADATA')
    return json.loads(path.read_bytes(),parse_constant=invalid)


def _fixed_path(store,relative):
    root=Path(store.root).resolve();path=root/relative_name(relative)
    if path.resolve()!=path:raise StudyJobBlocked('STUDY_MANAGED_PATH_ALIAS_FORBIDDEN')
    return path


def _job_folder(store,identity):
    if not isinstance(identity,str) or not ID.fullmatch(identity):raise StudyJobBlocked('STUDY_JOB_ID_INVALID')
    return _fixed_path(store,'jobs/'+identity)


def _write(path,data):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.resolve()!=path:raise StudyJobBlocked('STUDY_MANAGED_PATH_ALIAS_FORBIDDEN')
    try:
        descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=len(data) or path.read_bytes()!=data:
            raise StudyJobBlocked('STUDY_IMMUTABLE_METADATA_CONFLICT')
    else:
        with os.fdopen(descriptor,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)


def _inventory(definition):
    """Cheap bounded lstat only: no source byte is read or hashed here."""
    root=Path(definition.campaign_root).resolve()
    if not root.is_dir():raise StudyJobBlocked('REGISTERED_STUDY_SOURCE_UNAVAILABLE')
    entries=[];total=0;names=set()
    def add(name,path,content_sha256=None):
        nonlocal total
        name=relative_name(name);path=Path(path);info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size>MAX_FILE_BYTES:
            raise StudyJobBlocked('STUDY_SOURCE_NOT_BOUNDED_REGULAR_FILE')
        if name in names:raise StudyJobBlocked('STUDY_SOURCE_LOGICAL_ID_CONFLICT')
        names.add(name);total+=info.st_size
        if len(names)>MAX_FILES or total>MAX_BYTES:raise StudyJobBlocked('STUDY_SOURCE_INVENTORY_BOUND_EXCEEDED')
        entry=dict(logical_path=name,bytes=info.st_size)
        if content_sha256 is None:
            entry.update(device=info.st_dev,inode=info.st_ino,mtime_ns=info.st_mtime_ns,ctime_ns=info.st_ctime_ns)
        else:
            # Source-code/public context can live in a different immutable
            # release directory at dispatch. Its registered bytes, not that
            # release copy's inode/mtime, are the portable source identity.
            entry['registered_sha256']=content_sha256
        entries.append(entry)
    for directory,current_dirs,current_files in os.walk(root,followlinks=False):
        base=Path(directory)
        for name in current_dirs:
            if (base/name).is_symlink():raise StudyJobBlocked('STUDY_SOURCE_DIRECTORY_ALIAS_FORBIDDEN')
        for name in current_files:
            path=base/name;add('campaign/'+path.relative_to(root).as_posix(),path)
    claims=dict(definition.context)
    if {'public-audit.json','frozen-benchmark.py'} & set(claims):raise StudyJobBlocked('STUDY_CONTEXT_RESERVED_ID_CONFLICT')
    claims.update({'public-audit.json':definition.audit,'frozen-benchmark.py':definition.benchmark})
    for name,context in claims.items():add('context/'+relative_name(name),context.path,context.sha256)
    if not entries:raise StudyJobBlocked('EMPTY_STUDY_NOT_EXPORTABLE')
    identity=dict(study_id=definition.study_id,protocol_sha256=definition.protocol_sha256,
        context_sha256={name:context.sha256 for name,context in sorted(claims.items())})
    return dict(definition=identity,files=sorted(entries,key=lambda row:row['logical_path']),
        file_count=len(entries),estimated_payload_bytes=total)


def request_settings(store,study_id):
    """CC-safe server preparation; browser passes one allowlisted ID only."""
    definition=registered_definition(study_id)
    inventory=_inventory(definition)
    body=dict(schema=SCHEMA,study_id=study_id,inventory=inventory,
        estimated_payload_bytes=inventory['estimated_payload_bytes'],
        disk_budget_bytes=3*inventory['estimated_payload_bytes']+DISK_OVERHEAD_BYTES,
        target_partition='development',target_optimizer_allowed=False,physical_io=False)
    payload=canonical(body)
    if len(payload)>MAX_METADATA_BYTES:raise StudyJobBlocked('STUDY_REQUEST_METADATA_BOUND_EXCEEDED')
    digest=hashlib.sha256(payload).hexdigest();relative=REQUEST_DIRECTORY+'/'+digest+'/request.json'
    _write(_fixed_path(store,relative),payload)
    return dict(schema=SCHEMA,study_id=study_id,request_manifest=relative,request_sha256=digest,
        estimated_payload_bytes=body['estimated_payload_bytes'])


def _load_request(store,config):
    digest=config.get('request_sha256')
    if not isinstance(digest,str) or not SHA.fullmatch(digest):raise StudyJobBlocked('SERVER_PREPARED_STUDY_REQUEST_REQUIRED')
    relative=REQUEST_DIRECTORY+'/'+digest+'/request.json'
    if config.get('schema')!=SCHEMA or config.get('request_manifest')!=relative:
        raise StudyJobBlocked('SERVER_PREPARED_STUDY_REQUEST_REQUIRED')
    path=_fixed_path(store,relative);body=_metadata(path)
    if sha_file(path)!=digest:raise StudyJobBlocked('STUDY_REQUEST_SHA_MISMATCH')
    if body.get('schema')!=SCHEMA or config.get('study_id')!=body.get('study_id') or type(config.get('estimated_payload_bytes')) is not int or config['estimated_payload_bytes']!=body['estimated_payload_bytes']:
        raise StudyJobBlocked('QUEUED_STUDY_SELECTION_CHANGED')
    registered_definition(body['study_id'])
    if body['disk_budget_bytes']!=3*body['estimated_payload_bytes']+DISK_OVERHEAD_BYTES:
        raise StudyJobBlocked('STUDY_DISK_RESERVATION_CHANGED')
    return body


def _running(store,identity):
    current=store.job(identity)
    if not current or current.get('kind')!=KIND or current.get('status')!='running':
        raise StudyJobBlocked('STUDY_EXPORT_NOT_RUNNING_OR_CANCELLED')
    return current


def _progress(store,identity,value):
    _running(store,identity);store.update_job(identity,progress=value)


def _cached_result(folder,digest):
    result=_metadata(folder/'export-result.json')
    if result.get('source_request_sha256')!=digest:raise StudyJobBlocked('CACHED_STUDY_SOURCE_CHANGED')
    manifest=folder/'bundle/manifest.json';archive=folder/'study.tar';restore=folder/'restored/restore-receipt.json'
    if (result.get('export_manifest_path')!=str(manifest) or result.get('evidence_tar_path')!=str(archive) or
            result.get('restore_verification_path')!=str(restore)):
        raise StudyJobBlocked('CACHED_STUDY_MANAGED_PATH_CHANGED')
    if any(path.resolve()!=path for path in (manifest,archive,restore)):
        raise StudyJobBlocked('CACHED_STUDY_ARTIFACT_ALIAS_FORBIDDEN')
    verified=verify_study_bundle(folder/'bundle',expected_manifest_sha256=result['manifest_sha256'])
    document=verified['inspection']
    expected=dict(study_id=document['study_id'],completeness=document['completeness'],verification=document['completeness'],
        dependency_closure=document['dependency_closure'],recorded_attempt_ids=document['recorded_attempt_ids'],
        outcomes={row['attempt_id']:row['outcome'] for row in document['attempts']},
        target_optimizer_allowed=False,physical_io=False,physical_success_claimed=False,learned_policy_executed=False,master_prompt_completed=False)
    if any(result.get(key)!=value for key,value in expected.items()):raise StudyJobBlocked('CACHED_STUDY_CLAIM_CHANGED')
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size!=result.get('archive_bytes') or sha_file(archive)!=result.get('archive_sha256') or sha_file(restore)!=result.get('restore_verification_sha256'):
        raise StudyJobBlocked('CACHED_STUDY_ARTIFACT_CHANGED')
    return result


def run_study_export(store,job,config):
    """Worker-only actual hash/inspect/export/TAR/independent root restore."""
    if job.get('kind')!=KIND:raise StudyJobBlocked('STUDY_EXPORT_JOB_REQUIRED')
    durable=_running(store,job.get('id'))
    fields=('schema','study_id','request_manifest','request_sha256','estimated_payload_bytes')
    if any((job.get('config') or {}).get(key)!=config.get(key) or (durable.get('config') or {}).get(key)!=config.get(key) for key in fields):
        raise StudyJobBlocked('JOB_STUDY_SOURCE_REQUEST_MISMATCH')
    request=_load_request(store,config);definition=registered_definition(request['study_id'])
    folder=_job_folder(store,job['id']);folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if (folder/'export-result.json').is_file():return _cached_result(folder,config['request_sha256'])
    if _inventory(definition)!=request['inventory']:raise StudyJobBlocked('STUDY_SOURCE_METADATA_CHANGED_AFTER_ENQUEUE')
    _write(folder/'request-context.json',canonical(dict(study_id=request['study_id'],request_sha256=config['request_sha256'],physical_io=False)))
    if any((folder/name).exists() or (folder/name).is_symlink() for name in ('.staging-bundle','bundle','restored','study.tar')):
        raise StudyJobBlocked('INTERRUPTED_STUDY_EXPORT_RETAINED_USE_NEW_JOB')
    _progress(store,job['id'],.05);inspection=inspect_study(definition)
    if inspection.document['total_logical_bytes']!=request['estimated_payload_bytes']:
        raise StudyJobBlocked('STUDY_SOURCE_PAYLOAD_ESTIMATE_CHANGED')
    _progress(store,job['id'],.2)
    exported=export_study_bundle(inspection,folder/'.staging-bundle')
    _progress(store,job['id'],.4)
    archive=archive_study_bundle(folder/'.staging-bundle',folder/'study.tar',expected_manifest_sha256=exported['manifest_sha256'])
    _progress(store,job['id'],.55)
    unpack_study_archive(folder/'study.tar',folder/'bundle',expected_archive_sha256=archive['archive_sha256'],expected_manifest_sha256=exported['manifest_sha256'])
    _running(store,job['id'])
    # Only this worker's redundant CAS copy is removed AFTER exact TAR unpack.
    # Three full payload copies plus bounded metadata fit the reserved3×+256MiB.
    # Originals and all retained final bundle/negative records are untouched.
    staging=_job_folder(store,job['id'])/'.staging-bundle'
    verify_study_bundle(staging,expected_manifest_sha256=exported['manifest_sha256'])
    shutil.rmtree(staging)
    _progress(store,job['id'],.7)
    restored=restore_study_bundle(folder/'bundle',folder/'restored',expected_manifest_sha256=exported['manifest_sha256'])
    _progress(store,job['id'],.9)
    if _inventory(definition)!=request['inventory'] or any(sha_file(inspection.sources[row['logical_path']])!=row['sha256'] for row in inspection.document['file_manifest']):
        raise StudyJobBlocked('ORIGINAL_STUDY_CHANGED_DURING_EXPORT')
    _running(store,job['id'])
    document=inspection.document;verification=folder/'restored/restore-receipt.json'
    result=dict(schema='explorer_study_export_job_result_v1',study_id=request['study_id'],source_request_sha256=config['request_sha256'],
        export_manifest_path=str(folder/'bundle/manifest.json'),export_manifest_sha256=exported['manifest_sha256'],manifest_sha256=exported['manifest_sha256'],
        evidence_tar_path=str(folder/'study.tar'),evidence_tar_sha256=archive['archive_sha256'],evidence_tar_bytes=archive['archive_bytes'],
        archive_sha256=archive['archive_sha256'],archive_bytes=archive['archive_bytes'],
        restore_verification_path=str(verification),restore_verification_sha256=sha_file(verification),
        verification=document['completeness'],completeness=document['completeness'],dependency_closure=document['dependency_closure'],
        missing_dependencies=document['dependency_gaps'],missing_dependencies_count=len(document['dependency_gaps']),
        recorded_attempt_ids=document['recorded_attempt_ids'],outcomes={row['attempt_id']:row['outcome'] for row in document['attempts']},
        benchmark_recomputation=document['benchmark_recomputation'],files=document['file_count'],
        estimated_payload_bytes=request['estimated_payload_bytes'],actual_payload_bytes=document['total_logical_bytes'],
        disk_budget_bytes=request['disk_budget_bytes'],independent_restore_verified=restored['benchmark_recomputed'],
        independent_restore_process=False,independent_restore_source='exact_TAR_unpack_in_new_root',
        originals_deleted=False,original_sha256_unchanged=True,only_redundant_own_staging_CAS_removed=True,
        target_partition='development',target_optimizer_allowed=False,source_method='SCRIPTED_SIM',
        learned_policy_executed=False,physical_io=False,physical_success_claimed=False,master_prompt_completed=False)
    result=json.loads(canonical(result))
    _write(folder/'export-result.json',canonical(result));_running(store,job['id'])
    return result


def download_validation(store,job_id):
    """Completed typed job, fixed managed TAR, pinned SHA+size; no user paths."""
    folder=_job_folder(store,job_id);job=store.job(job_id)
    if not job or job.get('kind')!=KIND or job.get('status')!='completed':
        raise StudyJobBlocked('COMPLETED_STUDY_EXPORT_REQUIRED')
    result=job.get('result') or {};path=folder/'study.tar'
    if (result.get('schema')!='explorer_study_export_job_result_v1' or result.get('evidence_tar_path')!=str(path) or
            result.get('evidence_tar_sha256')!=result.get('archive_sha256') or result.get('evidence_tar_bytes')!=result.get('archive_bytes')):
        raise StudyJobBlocked('STUDY_DOWNLOAD_PATH_OR_RESULT_INVALID')
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_ARCHIVE_BYTES or info.st_size!=result.get('archive_bytes') or sha_file(path)!=result.get('archive_sha256'):
        raise StudyJobBlocked('STUDY_DOWNLOAD_SHA_OR_SIZE_MISMATCH')
    return dict(path=path,size_bytes=info.st_size,sha256=result['archive_sha256'],media_type='application/x-tar',
        filename='explorer-study-'+job_id+'.tar',verification=result.get('verification'),study_id=result.get('study_id'))
