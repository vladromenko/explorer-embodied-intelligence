"""ENGINEERING_GLUE: recheck current local approval before a delayed worker fit.

The immutable aggregate retains the exact approved Source review snapshot. A
later central USER review does not rewrite that snapshot or a previous result;
it prevents a queued job from fitting a now withdrawn/revised approval.
"""
from .native_capture_approval import validate_source_approval


def validate_current_approvals(store,manifest):
    from .skill_workflow import _records
    with store.connect() as db:
        latest={row['payload']['original_episode_id']:row['payload'] for row in _records(db,'capture.reviewed')}
    checked=[]
    for descriptor in manifest.get('episodes') or []:
        if descriptor.get('source_approval_required') is True or descriptor.get('source_approval_file') or descriptor.get('source_format')=='native_capture_v1':
            original=descriptor['source_episode_id'];current=latest.get(original)
            if current:
                validate_source_approval(current['source_wrapper'],original,descriptor.get('source_archive_sha256'),
                    expected_review_sha256=descriptor.get('source_review_sha256'))
            checked.append({'original_episode_id':original,'review_sha256':descriptor.get('source_review_sha256'),
                'current_local_review_checked':True,'remote_review_rechecked':False})
    return checked
