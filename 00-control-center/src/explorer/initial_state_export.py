"""ENGINEERING_GLUE: byte-only admission of recorded stationary SIM bundles.

No MuJoCo/NumPy/model/archive imports, state restoration or archived execution.
A source RECORDED claim authorizes only bounded exact-byte scientific copying.
Numerical/RGB replay and original OS completeness remain independent claims.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from explorer_shared.research_export import ExportRejected, _relative, _check_text, assert_secret_free

SCHEMA='explorer_stationary_sim_initial_state_v1'
PROFILE='explorer_manipulation_stationary_initial_v1'
MAX_TOTAL=256*1024**2
MAX_MANIFEST=8*1024**2
MAX_MEMBERS=512
REQUIRED={'compiled-model.mjb','model.xml','profile-state.json','native-integration.npy'}
HEX=re.compile('[0-9a-f]{64}')


def recorded_claims(value,depth=0):
    if depth>64:raise ExportRejected('Initial-state claim nesting bound')
    result=[]
    if isinstance(value,dict):
        claim=value.get('initial_state')
        if isinstance(claim,dict) and claim.get('status')=='RECORDED':result.append(claim)
        for child in value.values():
            if isinstance(child,(dict,list)):result.extend(recorded_claims(child,depth+1))
    elif isinstance(value,list):
        for child in value:
            if isinstance(child,(dict,list)):result.extend(recorded_claims(child,depth+1))
    return result


def _identity(value):return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns


def _raw(path,limit,expected=None):
    """One bounded no-follow read, SHA/identity checked on those same bytes."""
    before=path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size>limit:raise ExportRejected('Initial-state regular byte bound')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        if _identity(os.fstat(stream.fileno()))!=_identity(before):raise ExportRejected('Initial-state file changed before read')
        raw=stream.read(limit+1)
        if len(raw)!=before.st_size or _identity(os.fstat(stream.fileno()))!=_identity(before) or _identity(path.stat(follow_symlinks=False))!=_identity(before):
            raise ExportRejected('Initial-state file changed during read')
    if expected is not None and hashlib.sha256(raw).hexdigest()!=expected:raise ExportRejected('Initial-state SHA changed')
    return raw


def inspect_recorded_bundle(record,*,base,managed_file,hash_file,limits):
    """Validate all same-folder members before returning any binary permission.

    managed_file/hash_file are the caller's existing managed-root and streamed
    SHA guards. No general extension registry is changed by this helper.
    """
    if record.get('status')!='RECORDED' or record.get('helper_id')!=PROFILE or record.get('physical_io') is not False:
        raise ExportRejected('Initial-state RECORDED stationary profile required')
    assert_secret_free(record)
    expected=record.get('manifest_sha256');helper=record.get('helper_sha256');locator=record.get('manifest_path')
    if not isinstance(locator,str) or not isinstance(expected,str) or not HEX.fullmatch(expected) or not isinstance(helper,str) or not HEX.fullmatch(helper):
        raise ExportRejected('Initial-state manifest/helper SHA required')
    locator=Path(locator);locator=locator if locator.is_absolute() else Path(base)/locator
    _,manifest,_,_=managed_file(locator)
    if locator.absolute()!=manifest or manifest.name!='manifest.json':raise ExportRejected('Initial-state manifest alias/name forbidden')
    raw=_raw(manifest,min(MAX_MANIFEST,limits['max_json_bytes']),expected);_check_text(raw,'dataset_manifest',manifest.name,limits)
    document=json.loads(raw)
    if (document.get('schema')!=SCHEMA or document.get('profile')!=PROFILE or document.get('boundary')!='after_reset_before_first_observe_or_dispatch' or
            document.get('physical_io') is not False or document.get('retroactive_capture') is not False or document.get('original_runtime_OS_closure')!='PARTIAL'):
        raise ExportRejected('Initial-state manifest schema/profile/source claims differ')
    payloads=document.get('payloads')
    if not isinstance(payloads,dict) or not REQUIRED.issubset(payloads) or not 1<=len(payloads)<=MAX_MEMBERS or payloads!=record.get('payloads'):
        raise ExportRejected('Initial-state exact declared payload inventory required')
    references={};bindings=document.get('source_bindings');assets=document.get('assets')
    if 'source_bindings' in record and record['source_bindings']!=bindings:raise ExportRejected('Initial-state recorded source bindings differ')
    if not isinstance(bindings,dict) or not 1<=len(bindings)<=128 or not isinstance(assets,dict) or len(assets)>MAX_MEMBERS:
        raise ExportRejected('Initial-state source/CAD binding inventory required')
    for category,entries in (('source',bindings),('assets',assets)):
        for identity,entry in entries.items():
            if not isinstance(identity,str) or not isinstance(entry,dict):raise ExportRejected('Initial-state source/CAD descriptor malformed')
            checksum=entry.get('sha256');name=entry.get('file')
            if not isinstance(checksum,str) or not HEX.fullmatch(checksum) or name!=category+'/'+checksum+'.bin' or payloads.get(name)!=dict(sha256=checksum,bytes=entry.get('bytes')):
                raise ExportRejected('Initial-state source/CAD bytes binding differs')
            references[name]=entry
    if set(payloads)!=REQUIRED|set(references):raise ExportRejected('Initial-state untyped or unreferenced payload forbidden')
    capture=bindings.get('initial_capture_helper')
    if (not isinstance(capture,dict) or capture.get('sha256')!=helper or not isinstance(document.get('runtime'),dict) or document['runtime'].get('helper_sha256')!=helper):
        raise ExportRejected('Initial-state recorded helper source binding differs')
    declared=[];total=0;folder=manifest.parent
    for name,entry in payloads.items():
        _relative(name)
        if not isinstance(entry,dict) or set(entry)!={'sha256','bytes'} or not isinstance(entry['sha256'],str) or not HEX.fullmatch(entry['sha256']) or type(entry['bytes']) is not int or entry['bytes']<0:
            raise ExportRejected('Initial-state payload size/SHA descriptor malformed')
        cap=MAX_TOTAL if name=='compiled-model.mjb' else (MAX_MANIFEST if name=='profile-state.json' else 64*1024**2)
        # SHA-named source/CAD blobs can still contain text. Never authorize
        # an unchecked suffix beyond the existing full inspection budget.
        if name.endswith('.bin'):
            cap=min(cap,limits['max_json_bytes'])
        if entry['bytes']>min(cap,limits['max_file_bytes']):raise ExportRejected('Initial-state payload per-file byte bound')
        total+=entry['bytes']
        if total>min(MAX_TOTAL,limits['max_total_bytes']):raise ExportRejected('Initial-state total payload byte bound')
        locator=folder/name;_,path,_,_=managed_file(locator)
        if path!=locator.absolute() or not path.is_relative_to(folder):raise ExportRejected('Initial-state payload alias/escape forbidden')
        declared.append((path,entry,cap))
    actual=set();entries=0
    for path in folder.rglob('*'):
        entries+=1
        if entries>MAX_MEMBERS+4:raise ExportRejected('Initial-state folder entry count bound')
        if path.is_symlink() or not (path.is_file() or path.is_dir()):raise ExportRejected('Initial-state unaccounted alias/nonregular member')
        if path.is_dir() and path.relative_to(folder).as_posix() not in ('source','assets'):raise ExportRejected('Initial-state unaccounted directory')
        if path.is_file():actual.add(path.relative_to(folder).as_posix())
        if len(actual)>MAX_MEMBERS+1:raise ExportRejected('Initial-state folder member count bound')
    if actual!=set(payloads)|{'manifest.json'} or type(record.get('files')) is not int or record['files']!=len(payloads)+1 or type(record.get('total_payload_bytes')) is not int or record['total_payload_bytes']!=total:
        raise ExportRejected('Initial-state folder inventory/recorded size differs')
    descriptors=[]
    for path,entry,cap in declared:
        checksum,size=hash_file(path,max_bytes=cap)
        if (checksum,size)!=(entry['sha256'],entry['bytes']):raise ExportRejected('Initial-state payload bytes differ')
        if size<=limits['max_json_bytes']:
            blob=_raw(path,limits['max_json_bytes'],checksum)
            role='review' if path.suffix=='.json' else 'dataset_payload'
            _check_text(blob,role,path.name,limits)
        else:
            with path.open('rb') as stream:first=stream.read(16)
            _check_text(first,'dataset_payload',path.name,limits)
        descriptors.append(dict(path=path,sha256=checksum,size_bytes=size))
    if hash_file(manifest,max_bytes=MAX_MANIFEST)!=(expected,len(raw)):
        raise ExportRejected('Initial-state manifest changed during payload inspection')
    return dict(manifest_path=manifest,manifest_sha256=expected,profile=PROFILE,payloads=descriptors,
        admission=dict(schema='explorer_recorded_initial_state_export_admission_v1',profile=PROFILE,manifest_sha256=expected,
            admitted_scope='exact_recorded_bytes_only_no_state_execution',payload_files=len(payloads),total_payload_bytes=total,
            source_recorded=True,numerical_reexecution_verified_by_export=False,initial_RGB_reexecution='UNQUALIFIED',
            original_runtime_OS_closure='PARTIAL',physical_io=False))
