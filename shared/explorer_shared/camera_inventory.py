"""ENGINEERING_GLUE: factual audited physical cameras, without device operations.

Legacy stream IDs stay stable. Identity is a dated audit, not a new runtime
USB/ROS probe; stream availability and freshness still come from its owner.
"""
import json
from pathlib import Path

AUDIT=Path(__file__).resolve().parents[2]/'_registry/camera-inventory-20261027.json'


def audited_camera_metadata(role,stream_id,*,audit_path=None):
    path=Path(audit_path) if audit_path is not None else AUDIT
    if not path.is_file():return {}
    if path.stat().st_size>64*1024:raise ValueError('Bounded camera identity audit required')
    body=json.loads(path.read_bytes())
    if body.get('schema')!='explorer_current_physical_camera_inventory_v1':raise ValueError('Known camera audit schema required')
    matches=[item for item in body.get('cameras',[]) if item.get('node_role')==role and item.get('stream_id')==stream_id]
    if len(matches)>1:raise ValueError('One physical identity per configured stream required')
    if not matches:return {}
    item=matches[0]
    return dict(physical_camera_id=item['physical_camera_id'],label=item['label'],camera_domain=item['camera_domain'],
        identity_audit=dict(audited_on=body['audited_on'],identity=item['identity'],runtime_device_probe_performed=False),
        primary_usage=item['primary_usage'],optional_depth=item.get('capabilities',{}).get('optional_depth',False),
        additional_camera_required=False,viewpoint_equivalence_claimed=False)
