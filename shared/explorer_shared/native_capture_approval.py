"""ENGINEERING_GLUE: verify explicit Source review of one immutable archive.

The caller owns the authenticated fixed GET. A structurally valid canonical
episode or an operator outcome is not a separate approval to transfer/train.
No source mutation, controller, model import or network call in this module.
"""
from copy import deepcopy
import hashlib
import json
import re

SCHEMA='explorer_native_capture_review_v1'


def source_bytes(value):
    # Exact NativeCaptureWriter._bytes ABI, including JSON's ASCII escaping.
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def validate_source_approval(wrapper,recording_id,archive_sha256,*,expected_review_sha256=None,actor=None,require_approved=True):
    if not isinstance(wrapper,dict) or len(source_bytes(wrapper))>32*1024:raise ValueError('Bounded actual Source review wrapper required')
    receipt=wrapper.get('source_review')
    if not isinstance(receipt,dict):raise ValueError('Explicit sealed-source review required before transfer')
    checksum=hashlib.sha256(source_bytes(receipt)).hexdigest();previous=receipt.get('previous_review_sha256')
    if (receipt.get('schema')!=SCHEMA or not re.fullmatch('[0-9a-f]{32}',str(recording_id)) or not re.fullmatch('[0-9a-f]{32}',str(receipt.get('review_id'))) or
            receipt.get('recording_id')!=recording_id or not re.fullmatch('[0-9a-f]{64}',str(archive_sha256)) or
            receipt.get('archive_sha256')!=archive_sha256 or checksum!=wrapper.get('review_sha256') or
            expected_review_sha256 is not None and checksum!=expected_review_sha256 or
            receipt.get('decision') not in ('approve','reject') or receipt.get('raw_archive_unchanged') is not True or
            type(receipt.get('reviewed_native_ns')) is not int or receipt['reviewed_native_ns']<0 or
            any(not isinstance(receipt.get(key),str) or not 1<=len(receipt[key])<=200 for key in ('actor_id','source_boot_id','source_clock_domain')) or
            actor is not None and receipt['actor_id']!=actor or
            previous is not None and not re.fullmatch('[0-9a-f]{64}',str(previous)) or
            type(wrapper.get('transfer_ready')) is not bool or wrapper['transfer_ready']!=(receipt['decision']=='approve')):
        raise ValueError('Exact Source review actor/archive/hash/identity changed')
    if require_approved and receipt['decision']!='approve':raise ValueError('Source recording is not approved for transfer/training')
    return {'verified':True,'decision':receipt['decision'],'recording_id':recording_id,'archive_sha256':archive_sha256,
        'review_sha256':checksum,'source_review':deepcopy(receipt),'transfer_ready':wrapper['transfer_ready'],
        'approval_is_not_expert_outcome_or_physical_validation':True}
