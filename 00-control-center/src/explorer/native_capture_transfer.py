"""ENGINEERING_GLUE: approved, bounded, resumable common native02/03 archive transfer.

Fixed authenticated GETs only. Partial bytes survive a network/process failure;
full hash and unchanged latest Source approval are mandatory before admission.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .native_capture_approval import validate_source_approval,source_bytes

MAX_ARCHIVE_BYTES=8*1024**3
CHUNK_BYTES=128*1024


def _regular(path,flags):
    descriptor=os.open(path,flags|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):os.close(descriptor);raise ValueError('Managed regular native transfer file required')
    return descriptor


def transfer_native_capture(client,base_url,recording_id,archive_sha256,review_sha256,artifact_root,*,guard=lambda:None,
                            source_prefix='/native-capture',max_archive_bytes=MAX_ARCHIVE_BYTES,review_observer=None):
    if type(max_archive_bytes) is not int or not 0<max_archive_bytes<=MAX_ARCHIVE_BYTES:
        raise ValueError('Bounded source archive transfer budget required')
    if (not isinstance(recording_id,str) or not re.fullmatch('[0-9a-f]{32}',recording_id) or
            not re.fullmatch('[0-9a-f]{64}',str(archive_sha256)) or not re.fullmatch('[0-9a-f]{64}',str(review_sha256))):
        raise ValueError('Pinned native original/archive/Source review identity required')
    if source_prefix not in ('/native-capture','/so101-capture'):
        raise ValueError('Fixed server-selected source capture profile required')
    prefix=base_url+source_prefix+'/recordings/'+recording_id
    def review():
        guard();response=client.get(prefix+'/review');response.raise_for_status()
        if len(response.content)>32*1024:raise ValueError('Bounded Source approval required')
        wrapper=response.json()
        if review_observer is not None:
            validate_source_approval(wrapper,recording_id,archive_sha256,require_approved=False)
            review_observer(wrapper)
        validate_source_approval(wrapper,recording_id,archive_sha256,expected_review_sha256=review_sha256)
        return wrapper
    approval=review();root=Path(artifact_root)/archive_sha256[:2];root.mkdir(parents=True,exist_ok=True)
    final=root/(archive_sha256+'.tar');part=final.with_suffix('.part');lock=final.with_suffix('.lock')
    with os.fdopen(_regular(lock,os.O_RDWR|os.O_CREAT),'r+b') as held:
        fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if final.exists():
            with os.fdopen(_regular(final,os.O_RDONLY),'rb') as source:
                if not 0<os.fstat(source.fileno()).st_size<=max_archive_bytes:raise ValueError('Existing native archive exceeds transfer budget')
                if hashlib.file_digest(source,'sha256').hexdigest()!=archive_sha256:raise ValueError('Existing immutable native archive changed')
            if review()!=approval:raise ValueError('Source review changed during existing archive verification')
            return final,approval,{'resumed_bytes':0,'downloaded_bytes':0,'existing_archive_reverified':True}
        descriptor=_regular(part,os.O_RDWR|os.O_CREAT)
        with os.fdopen(descriptor,'r+b') as output:
            offset=os.fstat(output.fileno()).st_size
            if offset>max_archive_bytes:raise ValueError('Partial native archive exceeds transfer budget')
            if offset:
                output.seek(0)
                if hashlib.file_digest(output,'sha256').hexdigest()==archive_sha256:
                    if review()!=approval:raise ValueError('Source review changed during completed-part verification')
                    guard();os.replace(part,final)
                    parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(parent)
                    finally:os.close(parent)
                    return final,approval,{'resumed_bytes':offset,'downloaded_bytes':0,'completed_partial_recovered':True}
            headers={'Range':'bytes='+str(offset)+'-','If-Range':'"'+archive_sha256+'"'} if offset else {}
            with client.stream('GET',prefix+'/download',headers=headers) as response:
                response.raise_for_status()
                if response.headers.get('X-Content-SHA256')!=archive_sha256 or response.headers.get('ETag','"'+archive_sha256+'"')!='"'+archive_sha256+'"':
                    raise ValueError('Source archive/hash changed before transfer')
                if response.status_code==206:
                    match=re.fullmatch(r'bytes ([0-9]+)-([0-9]+)/([0-9]+)',response.headers.get('Content-Range',''))
                    if not match or int(match[1])!=offset or int(match[2])+1!=int(match[3]):raise ValueError('Resumed native byte range differs')
                    total=int(match[3]);resumed=offset
                elif response.status_code==200:
                    total=int(response.headers.get('Content-Length','0'));resumed=0;offset=0;output.seek(0);output.truncate()
                else:raise ValueError('Native archive GET requires full or exact partial response')
                if not 0<total<=max_archive_bytes:raise ValueError('Bounded native archive length required')
                output.seek(offset);downloaded=0
                try:
                    for block in response.iter_bytes(CHUNK_BYTES):
                        guard();downloaded+=len(block)
                        if offset+downloaded>total:raise ValueError('Native archive exceeds declared length')
                        output.write(block)
                finally:output.flush();os.fsync(output.fileno())
                if offset+downloaded!=total:raise ValueError('Native archive incomplete; partial original retained')
            output.seek(0)
            if hashlib.file_digest(output,'sha256').hexdigest()!=archive_sha256:raise ValueError('Native archive final SHA mismatch; partial retained for diagnosis')
            if review()!=approval:raise ValueError('Source approval changed while archive transferred')
        guard();os.replace(part,final)
        parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(parent)
        finally:os.close(parent)
    return final,approval,{'resumed_bytes':resumed,'downloaded_bytes':downloaded,'existing_archive_reverified':False}
