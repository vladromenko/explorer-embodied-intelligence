"""ENGINEERING_GLUE: correlate known source DATA starts for04 and05.

Historical capture provenance never grants control, borrows a current owner or
replays a request. Persistence and explicit recovery remain with each consumer.
"""
from copy import deepcopy
import re

from .contracts import canonical
from .so101_station import _record_context
from .station_observed_activation import binding


def validate_capture_start(actor_id,record_start_body,permission,source_identity,packet,*,native=None,source_stamp=None):
    return _validate_known_capture(actor_id,record_start_body,permission,source_identity,packet,
        native=native,source_stamp=source_stamp,states=('RECORDING','PAUSED'))


def _validate_known_capture(actor_id,record_start_body,permission,source_identity,packet,*,native=None,source_stamp=None,states):
    if not isinstance(record_start_body,dict) or not isinstance(packet,dict):
        raise ValueError('Known start body and actual source capture packet required')
    if (not isinstance(actor_id,str) or not 1<=len(actor_id)<=200 or not isinstance(permission,dict) or
            set(permission)!={'grant_id','binding','control_epoch'} or
            not all(isinstance(permission.get(key),str) and permission[key] for key in ('grant_id','control_epoch'))):
        raise ValueError('Exact known capture actor/permission provenance required')
    selected=binding(permission['binding'])
    if selected['purpose'] not in ('source04_demo','cooperative05'):
        raise ValueError('Known demonstration/cooperative DATA capture purpose required')
    identity=source_identity if isinstance(source_identity,dict) else {}
    calibrations=identity.get('calibration_sha256') or {}
    if (not isinstance(calibrations,dict) or set(calibrations)!={'leader','follower'} or
            any(not re.fullmatch('[0-9a-f]{64}',str(value)) for value in calibrations.values()) or
            any(not isinstance(identity.get(key),str) or not identity[key] for key in ('boot_id','clock_domain'))):
        raise ValueError('Original DATA source boot/clock/calibration identity required')
    capture=packet.get('capture') or {}
    if not isinstance(capture,dict):raise ValueError('Actual source capture metadata required')
    receipt=capture.get('original_start_receipt') or {}
    if not isinstance(receipt,dict):raise ValueError('Actual original DATA start receipt required')
    ident=capture.get('recording_id')
    if (not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident) or
            packet.get('schema')!='explorer_so101_source_capture_v1' or packet.get('recording_id')!=ident or
            packet.get('recovery_required') is not False or packet.get('closed') is not False or
            capture.get('state') not in states or capture.get('actor_id')!=actor_id or
            capture.get('native_control_epoch')!=permission['control_epoch'] or
            capture.get('source_boot_id')!=identity['boot_id'] or capture.get('source_clock_domain')!=identity['clock_domain'] or
            capture.get('calibration_sha256')!=calibrations or
            receipt.get('episode_id')!=ident):
        raise ValueError('Source DATA start identity is foreign, uncertain or changed; no adoption')
    body=deepcopy(record_start_body);context=dict(body.get('context') or {})
    key='source04_demo_permission' if selected['purpose']=='source04_demo' else 'cooperative05_capture_permission'
    if any(name in context for name in ('source04_demo_permission','cooperative05_capture_permission')):
        raise ValueError('Known pre-start body cannot supply injected permission provenance')
    context[key]=deepcopy(permission);body['context']=context;expected=_record_context(body)
    if canonical(capture.get('source_context'))!=canonical(expected):
        raise ValueError('Original capture context differs from the exact known DATA start intent')
    if native is not None:
        if not isinstance(native,dict):raise ValueError('Actual original writer metadata required')
        original=native.get('recording') or {};roles=native.get('roles') or {}
        if not isinstance(original,dict) or not isinstance(roles,dict):raise ValueError('Actual original writer/calibration objects required')
        original_context=deepcopy(original.get('source_context') or {})
        if not isinstance(original_context,dict):raise ValueError('Original DATA context object required')
        original_context.pop('native_phase_annotation',None)
        if (original.get('episode_id')!=ident or original.get('boot_id')!=identity['boot_id'] or
                original.get('clock_domain')!=identity['clock_domain'] or original.get('demonstration_actor')!=actor_id[:80] or
                canonical(original_context)!=canonical(expected) or
                any((roles.get(role) or {}).get('calibration_sha256')!=calibrations[role] for role in ('leader','follower')) or
                not isinstance(source_stamp,dict) or source_stamp.get('boot_id')!=identity['boot_id'] or
                source_stamp.get('clock_domain')!=identity['clock_domain']):
            raise ValueError('Original writer/boot/calibration no longer matches the known DATA intent')
    return ident


def validate_capture_finish(actor_id,record_start_body,permission,source_identity,recording_id,manifest,expected_outcome):
    """Acknowledge exact sealed DATA; no original finish/control request is replayed."""
    if (not isinstance(manifest,dict) or manifest.get('schema')!='explorer_so101_source_capture_v1' or
            manifest.get('recording_id')!=recording_id or manifest.get('project_id')!='04' or
            manifest.get('action_contract_id')!='so101_follower_calibrated_position6_v1' or
            manifest.get('state') not in ('completed','cancelled','interrupted') or
            manifest.get('physical_success_inferred') is not False or manifest.get('automatic_import_or_training') is not False or
            expected_outcome not in ('success','failure','unknown') or manifest.get('operator_outcome')!=expected_outcome):
        raise ValueError('Exact sealed DATA identity and declared per-role outcome required')
    lifecycle=manifest.get('lifecycle') or {}
    if not isinstance(lifecycle,dict) or lifecycle.get('state')!=manifest['state']:
        raise ValueError('Sealed DATA lifecycle is inconsistent')
    packet={'schema':manifest['schema'],'recording_id':recording_id,'recovery_required':False,'closed':False,'capture':lifecycle}
    ident=_validate_known_capture(actor_id,record_start_body,permission,source_identity,packet,
        states=('completed','cancelled','interrupted'))
    original=manifest.get('original_manifest') or {};archive=manifest.get('original_archive') or {}
    receipt=lifecycle.get('original_finish_receipt') or {}
    if (not all(isinstance(value,dict) for value in (original,archive,receipt)) or
            any(not re.fullmatch('[0-9a-f]{64}',str(value.get('sha256'))) for value in (original,archive)) or
            receipt.get('episode_id')!=ident or receipt.get('sha256')!=original.get('sha256') or
            manifest.get('source_context')!=lifecycle.get('source_context') or
            manifest.get('source_boot_id')!=source_identity['boot_id'] or
            manifest.get('source_clock_domain')!=source_identity['clock_domain']):
        raise ValueError('Sealed original DATA context/manifest/archive identity changed')
    return ident
