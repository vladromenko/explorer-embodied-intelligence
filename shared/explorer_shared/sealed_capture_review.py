"""ENGINEERING_GLUE: immutable Source review shared by native9 and SO101 native6.

Preserves the existing explorer_native_capture_review_v1 JSON bytes/receipt ABI.
Approval commits one sealed transport archive, never expert outcome or motion.
The producer serializes mutations and supplies its own sealed identity/state.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


def source_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def atomic_bytes(path,content):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('wb') as stream:
        stream.write(content);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    descriptor=os.open(path.parent,os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def recording_identity(recording_id):
    if not isinstance(recording_id,str) or re.fullmatch('[0-9a-f]{32}',recording_id) is None:
        raise ValueError('Original server recording identity required')
    return recording_id


def read_source_review(folder,recording_id):
    recording_identity(recording_id)
    path=Path(folder)/'review.json'
    if not path.is_file():return {'source_review':None,'review_sha256':None,'transfer_ready':False}
    result=json.loads(path.read_bytes());receipt=result.get('source_review') or {}
    if receipt.get('recording_id')!=recording_id or hashlib.sha256(source_bytes(receipt)).hexdigest()!=result.get('review_sha256'):
        raise ValueError('Original source review hash changed')
    return result


def review_sealed_capture(folder,recording_id,decision,actor_id,expected_archive_sha256,*,
                         state,original_recording_id,recorded_archive_sha256,
                         boot_id,clock_domain,clock=time.monotonic_ns,write=atomic_bytes):
    """Persist history before the latest pointer; failed publication stays false.

The native producer retains its existing state/manifest checks around this call.
No append mutates the original manifest, JPEGs, lifecycle envelope or archive.
"""
    recording_identity(recording_id)
    if decision not in ('approve','reject') or not isinstance(actor_id,str) or not 1<=len(actor_id)<=200:
        raise ValueError('Explicit authenticated operator review required')
    if state not in ('completed','cancelled','interrupted') or original_recording_id!=recording_id:
        raise ValueError('Review only a sealed original recording')
    if decision=='approve' and state!='completed':raise ValueError('Cancelled or interrupted recordings cannot be approved')
    folder=Path(folder)
    with (folder/'recording.tar').open('rb') as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
    if actual!=expected_archive_sha256 or recorded_archive_sha256!=actual:
        raise ValueError('Review the exact unchanged recording archive')
    previous=read_source_review(folder,recording_id)
    receipt={'schema':'explorer_native_capture_review_v1','review_id':uuid.uuid4().hex,
        'recording_id':recording_id,'archive_sha256':actual,'decision':decision,'actor_id':actor_id,
        'reviewed_native_ns':clock(),'source_boot_id':boot_id,'source_clock_domain':clock_domain,
        'raw_archive_unchanged':True,'previous_review_sha256':previous.get('review_sha256')}
    result={'source_review':receipt,'review_sha256':hashlib.sha256(source_bytes(receipt)).hexdigest(),
            'transfer_ready':decision=='approve'}
    reviews=folder/'reviews';reviews.mkdir(exist_ok=True)
    write(reviews/(receipt['review_id']+'.json'),source_bytes(result))
    write(folder/'review.json',source_bytes(result))
    return deepcopy(result)
