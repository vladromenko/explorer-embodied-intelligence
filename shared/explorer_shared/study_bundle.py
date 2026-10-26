"""ENGINEERING_GLUE: portable recorded-study CAS export and independent restore.

Only immutable bytes and the explicitly allowlisted pure statistical evaluator
are used. XML, archived drivers, models and native runtime objects are data.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile

from .study_evidence import (ContextFile, MAX_BYTES, MAX_FILES, MAX_FILE_BYTES,
    StudyDefinition, StudyRejected, canonical, inspect_study, read_json,
    regular, relative_name, sha_file)

SCHEMA='explorer_study_evidence_bundle_v1'
_HEX=re.compile(r'[0-9a-f]{64}\Z')
MAX_MANIFEST_BYTES=16*1024**2


def checksum(value):
    if not isinstance(value,str) or not _HEX.fullmatch(value):
        raise StudyRejected('Expected immutable SHA256 required')
    return value


def _new_directory(path):
    path=Path(path)
    if path.exists() or path.is_symlink():raise StudyRejected('New private destination required')
    path.mkdir(mode=0o700,parents=True)
    return path


def _sync_directory(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def _write(path,data):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def _copy(source,destination,expected,size):
    """Hash bytes during the copy, rejecting an input replaced since inspection."""
    regular(source)
    descriptor=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    initial=os.fstat(descriptor)
    digest=hashlib.sha256();count=0
    with os.fdopen(descriptor,'rb') as incoming, destination.open('xb') as outgoing:
        os.chmod(destination,0o600)
        block=incoming.read(1024*1024)
        while block:
            count+=len(block)
            if count>MAX_FILE_BYTES:raise StudyRejected('Member grew beyond recorded byte limit')
            digest.update(block);outgoing.write(block);block=incoming.read(1024*1024)
        final=os.fstat(incoming.fileno());outgoing.flush();os.fsync(outgoing.fileno())
    if count!=size or digest.hexdigest()!=expected or (initial.st_size,initial.st_mtime_ns,initial.st_ctime_ns)!=(final.st_size,final.st_mtime_ns,final.st_ctime_ns):
        raise StudyRejected('Study input changed during immutable export')


def _definition_record(definition):
    def entry(name,context):
        return dict(logical_path='context/'+relative_name(name),sha256=context.sha256,role=context.role)
    return dict(study_id=definition.study_id,protocol_sha256=definition.protocol_sha256,
        audit=entry('public-audit.json',definition.audit),benchmark=entry('frozen-benchmark.py',definition.benchmark),
        context={name:entry(name,source) for name,source in definition.context.items()})


def export_study_bundle(inspection,destination):
    """Export a complete recorded-byte graph, retaining explicit closure gaps.

    Destination must be new. Failed exports remain as partial evidence and are
    never advertised with a manifest receipt. No original is moved or changed.
    """
    destination=_new_directory(destination);objects=destination/'objects';objects.mkdir(mode=0o700)
    document=inspection.document
    entries=[];copied={}
    for row in document['file_manifest']:
        logical=relative_name(row['logical_path']);digest=checksum(row['sha256']);size=row['bytes']
        source=inspection.sources[logical];object_name='objects/'+digest+'.blob'
        if digest not in copied:
            _copy(source,destination/object_name,digest,size);copied[digest]=size
        elif copied[digest]!=size:raise StudyRejected('Conflicting content-addressed member size')
        else:
            # Even duplicate originals are independently checked against their
            # recorded identities; deduplication cannot hide changed evidence.
            if regular(source).stat().st_size!=size or sha_file(source)!=digest:
                raise StudyRejected('Duplicate original changed during immutable export')
        entries.append(dict(row,object=object_name))
    manifest=dict(schema=SCHEMA,definition=_definition_record(inspection.definition),
        inspection=document,files=entries,file_count=len(entries),object_count=len(copied),
        total_logical_bytes=sum(row['bytes'] for row in entries),total_object_bytes=sum(copied.values()),
        scope='Recorded byte graph and benchmark recomputation; original-runtime gaps remain explicit')
    rendered=canonical(manifest);_write(destination/'manifest.json',rendered)
    _sync_directory(objects);_sync_directory(destination)
    manifest_sha256=hashlib.sha256(rendered).hexdigest()
    verified=verify_study_bundle(destination,expected_manifest_sha256=manifest_sha256)
    return dict(schema='explorer_study_bundle_export_receipt_v1',study_id=document['study_id'],
        manifest_sha256=manifest_sha256,file_count=len(entries),object_count=len(copied),
        total_logical_bytes=manifest['total_logical_bytes'],total_object_bytes=manifest['total_object_bytes'],
        completeness=verified['inspection']['completeness'],target_optimizer_allowed=False,physical_io=False)


def verify_study_bundle(bundle,*,expected_manifest_sha256):
    """Validate bound manifest + every regular object; never trust a loose tree."""
    expected_manifest_sha256=checksum(expected_manifest_sha256);root=Path(bundle)
    manifest_path=regular(root/'manifest.json',root)
    if manifest_path.stat().st_size>MAX_MANIFEST_BYTES or sha_file(manifest_path)!=expected_manifest_sha256:
        raise StudyRejected('Portable study manifest differs from its trusted receipt')
    manifest=read_json(manifest_path)
    if manifest.get('schema')!=SCHEMA:raise StudyRejected('Unsupported portable study bundle')
    inspection=manifest['inspection'];inspection_body=dict(inspection);recorded_digest=inspection_body.pop('inspection_sha256',None)
    if recorded_digest!=hashlib.sha256(canonical(inspection_body)).hexdigest():raise StudyRejected('Inspection content hash changed')
    if inspection.get('target_optimizer_allowed') is not False or inspection.get('target_partition')!='development' or inspection.get('physical_io') is not False:
        raise StudyRejected('Recorded development study must remain nonacting and outside optimizer partitions')
    if inspection.get('learned_policy_executed') is not False or inspection.get('master_prompt_completed') is not False:
        raise StudyRejected('Recorded study cannot confer learned mastery or master completion')
    if inspection.get('completeness')!='PARTIAL':raise StudyRejected('This recorded study has unclosed original-runtime dependencies')
    files=manifest['files']
    if not isinstance(files,list) or not 0<len(files)<=MAX_FILES:raise StudyRejected('Bounded nonempty study inventory required')
    logicals=set();objects={};total=0;plain=[]
    for row in files:
        name=relative_name(row['logical_path']);digest=checksum(row['sha256']);size=row['bytes']
        if not name.startswith(('campaign/','context/')) or name in logicals or type(size) is not int or not 0<=size<=MAX_FILE_BYTES:
            raise StudyRejected('Duplicate, unscoped or oversized portable member')
        object_name='objects/'+digest+'.blob'
        if row['object']!=object_name:raise StudyRejected('CAS object path differs from its content identity')
        path=regular(root/object_name,root)
        if path.stat().st_size!=size or sha_file(path)!=digest:raise StudyRejected('Portable object bytes changed')
        if object_name in objects and objects[object_name]!=size:raise StudyRejected('CAS identity size conflict')
        objects[object_name]=size;logicals.add(name);total+=size
        plain.append({key:value for key,value in row.items() if key!='object'})
    if total>MAX_BYTES or plain!=inspection['file_manifest'] or manifest['file_count']!=len(files) or manifest['total_logical_bytes']!=total or manifest['object_count']!=len(objects) or manifest['total_object_bytes']!=sum(objects.values()):
        raise StudyRejected('Portable study byte accounting changed')
    actual=set()
    for path in root.rglob('*'):
        if path.is_symlink():raise StudyRejected('Portable evidence cannot contain links')
        if path.is_file():actual.add(path.relative_to(root).as_posix())
    if actual!=set(objects)|{'manifest.json'}:raise StudyRejected('Unaccounted or missing portable bundle files')
    definition=manifest['definition']
    if definition['study_id']!=inspection['study_id'] or definition['protocol_sha256']!=inspection['frozen_protocol_sha256']:
        raise StudyRejected('Study identity differs between source graph and definition')
    return manifest


def restore_study_bundle(bundle,destination,*,expected_manifest_sha256):
    """Independent new-root restore and raw→frozen benchmark recomputation.

    Original absolute locators remain provenance data. No original path is
    opened, even when it is still present on this machine.
    """
    manifest=verify_study_bundle(bundle,expected_manifest_sha256=expected_manifest_sha256)
    destination=_new_directory(destination)
    for row in manifest['files']:
        path=destination/row['logical_path'];path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        _copy(Path(bundle)/row['object'],path,row['sha256'],row['bytes'])
    definition=manifest['definition']
    def context(row):
        return ContextFile(destination/relative_name(row['logical_path']),row['sha256'],row['role'])
    restored=StudyDefinition(definition['study_id'],destination/'campaign',definition['protocol_sha256'],
        context(definition['audit']),context(definition['benchmark']),{name:context(row) for name,row in definition['context'].items()})
    actual=inspect_study(restored)
    if canonical(actual.document)!=canonical(manifest['inspection']):
        raise StudyRejected('Independent restored raw→frozen study graph differs')
    receipt=dict(schema='explorer_study_independent_restore_receipt_v1',study_id=actual.document['study_id'],
        manifest_sha256=expected_manifest_sha256,inspection_sha256=actual.document['inspection_sha256'],
        original_paths_read=False,original_programs_executed=False,physics_executed=False,
        benchmark_recomputed=actual.document['benchmark_recomputation']['verified'],
        completeness=actual.document['completeness'],file_count=actual.document['file_count'],
        target_optimizer_allowed=False,physical_io=False)
    _write(destination/'restore-receipt.json',canonical(receipt));_sync_directory(destination)
    return receipt


def archive_study_bundle(bundle,archive,*,expected_manifest_sha256):
    """Create deterministic portable TAR from the verified CAS, not raw paths."""
    manifest=verify_study_bundle(bundle,expected_manifest_sha256=expected_manifest_sha256)
    archive=Path(archive)
    if archive.exists() or archive.is_symlink():raise StudyRejected('Archive destination already exists')
    names=['manifest.json']+sorted({row['object'] for row in manifest['files']})
    with archive.open('xb') as output:
        os.chmod(archive,0o600)
        with tarfile.open(fileobj=output,mode='w',format=tarfile.PAX_FORMAT) as stream:
            for name in names:
                path=regular(Path(bundle)/name,Path(bundle));info=tarfile.TarInfo(name)
                info.size=path.stat().st_size;info.mode=0o600;info.uid=info.gid=0;info.mtime=0
                with path.open('rb') as incoming:stream.addfile(info,incoming)
        output.flush();os.fsync(output.fileno())
    return dict(schema='explorer_study_archive_receipt_v1',manifest_sha256=expected_manifest_sha256,
        archive_sha256=sha_file(archive),archive_bytes=archive.stat().st_size,compression='none')


def unpack_study_archive(archive,destination,*,expected_archive_sha256,expected_manifest_sha256):
    """Strict regular-file TAR extraction with both caller-pinned identities."""
    archive=Path(archive)
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size>MAX_BYTES+32*1024**2 or sha_file(archive)!=checksum(expected_archive_sha256):
        raise StudyRejected('Archive bytes differ from their trusted receipt or bound')
    destination=_new_directory(destination);seen=set();total=0
    with tarfile.open(archive,mode='r:') as stream:
        for member in stream:
            name=relative_name(member.name)
            if not member.isfile() or name in seen or not (name=='manifest.json' or re.fullmatch(r'objects/[0-9a-f]{64}\.blob',name)):
                raise StudyRejected('Only unique regular CAS members may be unpacked')
            bound=MAX_MANIFEST_BYTES if name=='manifest.json' else MAX_FILE_BYTES
            total+=member.size
            if not 0<=member.size<=bound or len(seen)>=MAX_FILES+1 or total>MAX_BYTES+MAX_MANIFEST_BYTES:
                raise StudyRejected('Portable archive exceeds bounded inventory')
            seen.add(name);path=destination/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            incoming=stream.extractfile(member)
            if incoming is None:raise StudyRejected('Missing regular TAR payload')
            with incoming, path.open('xb') as output:
                os.chmod(path,0o600);shutil.copyfileobj(incoming,output,1024*1024);output.flush();os.fsync(output.fileno())
    verified=verify_study_bundle(destination,expected_manifest_sha256=expected_manifest_sha256)
    return dict(study_id=verified['inspection']['study_id'],manifest_sha256=expected_manifest_sha256,file_count=verified['file_count'])
