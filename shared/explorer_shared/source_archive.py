"""ENGINEERING_GLUE: immutable source/CAD snapshots for reproducible local trials.

Reads explicitly selected originals and copies their exact bytes with SHA/fsync.
No model, environment, device, actuator, runtime importer or deployment is called.
The archive proves bytes, not scientific performance or physical qualification.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SCHEMA='explorer_source_archive_v1'


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()


class ArchiveRejected(ValueError):pass


def _write(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists():
        if path.read_bytes()!=raw:raise ArchiveRejected('Immutable archive member changed')
    else:
        with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        path.chmod(0o600)
    descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def loaded_python_sources(prefixes=('explorer_shared','explorer_sim','legacy')):
    """Explicit limited Python graph, not a claim to retain all OS/runtime bytes."""
    result={}
    for name,module in sorted(sys.modules.items()):
        if any(name==prefix or name.startswith(prefix+'.') for prefix in prefixes):
            locator=getattr(module,'__file__',None)
            if locator and str(locator).endswith('.py'):
                path=Path(locator).resolve();result[str(path)]=sha(path.read_bytes())
    return result


def archive_sources(folder,files,*,context=None,max_files=512,max_file_bytes=64*1024**2,max_total_bytes=256*1024**2):
    folder=Path(folder)
    if folder.exists():raise ArchiveRejected('New immutable source archive directory required')
    if not isinstance(files,dict) or not files or len(files)>max_files:raise ArchiveRejected('Bounded explicit source inventory required')
    if not isinstance(context or {},dict) or len(canonical(context or {}))>65536:raise ArchiveRejected('Bounded source context object required')
    folder.mkdir(parents=True,mode=0o700);records=[];total=0
    try:
        for locator,expected in sorted(files.items(),key=lambda item:str(item[0])):
            original=Path(locator).absolute();resolved=original.resolve()
            if not stat.S_ISREG(resolved.stat().st_mode) or not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise ArchiveRejected('Regular original and explicit expected SHA required')
            size=resolved.stat().st_size
            if size>max_file_bytes or total+size>max_total_bytes:raise ArchiveRejected('Source snapshot byte bound exceeded')
            raw=resolved.read_bytes();checksum=sha(raw)
            if checksum!=expected:raise ArchiveRejected('Original source SHA differs before snapshot: '+str(original))
            suffix=resolved.suffix.lower();suffix=suffix if re.fullmatch(r'\.[a-z0-9]{1,12}',suffix) else '.bin'
            member='objects/'+checksum+suffix;_write(folder/member,raw);total+=len(raw)
            records.append(dict(original_locator=str(original),resolved_path=str(resolved),original_was_symlink=original.is_symlink(),file=member,sha256=checksum,bytes=len(raw)))
        body=dict(schema=SCHEMA,context=context or {},files=records,total_source_bytes=total,
            scope='explicit_source_inventory_bytes_not_complete_OS_or_scientific_success',original_sources_modified=False)
        raw=canonical(body);_write(folder/'manifest.json',raw)
        return dict(manifest_path=str(folder/'manifest.json'),manifest_sha256=sha(raw),files=len(records),total_source_bytes=total)
    except Exception as error:
        _write(folder/'archive-rejected.json',canonical(dict(schema='explorer_source_archive_rejection_v1',error_type=type(error).__name__,reason=str(error),retained_files=records,original_sources_modified=False)))
        raise


def verify_source_archive(manifest_path,expected_sha256,*,recheck_originals=False):
    path=Path(manifest_path).resolve()
    if not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256) or path.stat().st_size>2*1024**2:raise ArchiveRejected('Explicit bound archive manifest SHA required')
    raw=path.read_bytes()
    if sha(raw)!=expected_sha256:raise ArchiveRejected('Archive manifest SHA changed')
    body=json.loads(raw)
    if body.get('schema')!=SCHEMA or not isinstance(body.get('files'),list) or not 1<=len(body['files'])<=512:raise ArchiveRejected('Source archive schema/inventory differs')
    total=0
    for row in body['files']:
        member=(path.parent/row['file']).resolve()
        if not member.is_relative_to(path.parent) or not member.is_file() or member.stat().st_size>64*1024**2:raise ArchiveRejected('Archive source member path/size differs')
        data=member.read_bytes();total+=len(data)
        if sha(data)!=row['sha256'] or len(data)!=row['bytes']:raise ArchiveRejected('Retained source/CAD bytes changed')
        if recheck_originals:
            original=Path(row['original_locator'])
            if str(original.resolve())!=row['resolved_path'] or sha(original.read_bytes())!=row['sha256']:raise ArchiveRejected('Original source/CAD changed across trial')
    if total!=body['total_source_bytes'] or total>256*1024**2:raise ArchiveRejected('Archive total byte bound differs')
    return dict(manifest_sha256=expected_sha256,files=len(body['files']),originals_rechecked=recheck_originals,scope=body['scope'])
