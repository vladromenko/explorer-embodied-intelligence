"""Thin06 geometry over admitted04 source and existing shared coordinate core."""
import base64
from copy import deepcopy
from pathlib import Path

from explorer_shared.contracts import digest
from explorer_shared.source_geometry_context import SO101GeometryContext
from explorer_shared.transfer_mapping import TargetCoordinates,audit_known_mapping
from . import transfer_source


def admitted_model(store,model_job_id):
    if not isinstance(model_job_id,str) or not transfer_source.ID.fullmatch(model_job_id):
        raise ValueError('Choose an existing source model job ID')
    return transfer_source._metadata(store,store.job(model_job_id))


def saved_simulation_calibration(model):
    """Only source-bound original metadata, never a guessed encoder range."""
    if model['source_domain']!='SIMULATION':raise ValueError('Explicit simulation source required')
    follower=(model['manifest'].get('source_calibration_metadata') or {}).get('calibration_metadata',{}).get('follower',{})
    envelope=follower.get('calibration_artifact')
    if not isinstance(envelope,dict):
        raise transfer_source.SourceBlocked([dict(code='A_ORIGINAL_SIM_CALIBRATION_ARTIFACT_UNAVAILABLE',category='A',
            detail='В исходном manifest есть SHA калибровки, но нет исходных байтов. Геометрия не восстанавливается по предположениям о диапазоне.')])
    return deepcopy(envelope)


def build_geometry(model,envelope,*,root=None,source_qualification=None):
    if (not isinstance(envelope,dict) or envelope.get('schema')!='explorer_so101_original_calibration_artifact_v1' or
            envelope.get('role')!='follower' or envelope.get('sha256')!=model['source_calibration_sha256'] or
            not isinstance(envelope.get('raw_base64'),str) or len(envelope['raw_base64'])>180*1024):
        raise ValueError('Exact admitted source follower calibration artifact required')
    try:raw=base64.b64decode(envelope['raw_base64'],validate=True)
    except (ValueError,TypeError) as error:raise ValueError('Original bounded calibration encoding required') from error
    if type(envelope.get('bytes')) is not int or envelope['bytes']!=len(raw):
        raise ValueError('Original calibration byte count differs')
    root=Path(root or Path(__file__).resolve().parents[3])
    context=SO101GeometryContext(root,raw,expected_sha256=model['source_calibration_sha256'],source_role='follower')
    audit=audit_known_mapping(source_chain=context.source,target_chain=context.target,source_coordinates=context.coordinates,
        source_zero_convention_bytes=(root/'shared/robotics/so101/README.md').read_bytes(),
        target_coordinates=TargetCoordinates(context.native_source),gripper_reference_bytes=context.gripper_raw,
        handeye_reference_bytes=(root/'01-autonomous-learning/src/legacy/config/handeye-accepted.json').read_bytes(),source_qualification=source_qualification)
    # Technical model/encoder readiness does not establish object mastery.
    binding=dict(model_job_id=model['job']['id'],skill_id=model['manifest']['skill_id'],
        skill_version=model['manifest']['skill_version'],spec_sha256=model['manifest']['spec_sha256'],
        aggregate_manifest_sha256=model['result']['aggregate_manifest_sha256'],
        training_receipt_sha256=model['training_receipt_sha256'],checkpoint_content_sha256=model['checkpoint_content_sha256'],
        source_kind=model['manifest']['source_kind'],source_domain=model['source_domain'],
        source_calibration_sha256=context.coordinates.sha256,source_urdf_sha256=context.source.source_sha256)
    result=dict(schema='explorer_transfer_source_geometry_v1',source_model_binding=binding,source_role='follower',
        audit=audit,audit_sha256=digest(audit),coordinate_status=context.status(),
        geometry_preview_ready=True,source_object_skill_inferred=False,source_qualification=deepcopy(source_qualification or {}),target_policy_trained=False,
        target_registration_measured=False,physical_execution_authorized=False,native_commands_sent=0,
        source_and_target_camera_domains_equal=False,source_action_head_copied_to_target=False)
    return result,context
