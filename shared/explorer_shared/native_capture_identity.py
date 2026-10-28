"""Pure DATA identity for the existing native9 capture consumers02/03."""
from copy import deepcopy
import re
import hashlib
import json


def native_context_sha256(context):
    """Exact original writer JSON bytes, including its ASCII escaping."""
    return hashlib.sha256(json.dumps(context,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def normalize_capture_context(skill_context,*,observation_only=False):
    """Pure original02/03 writer context; also used to identify known DATA starts."""
    if type(observation_only) is not bool:raise ValueError('Explicit recording mode required')
    if not isinstance(skill_context,dict) or skill_context.get('project_id') not in ('02','03','06'):
        raise ValueError('Validated source project02/03 skill_context required')
    if skill_context['project_id']=='06':
        from .transfer_target_capture import validate_target_capture_context
        validate_target_capture_context(skill_context)
    context=deepcopy(skill_context)
    context['recording_mode']='observations_only' if observation_only else 'demonstration'
    context['force_analysis_only']=observation_only
    return context


def validate_native_capture_start(body,source_identity,packet):
    context=normalize_capture_context(body.get('skill_context'),observation_only=body.get('observation_only',False))
    capture=packet.get('capture') or {}
    identity=packet.get('active_recording_id')
    if (not re.fullmatch('[a-f0-9]{32}',str(identity)) or capture.get('recording_id')!=identity or
            capture.get('schema')!='explorer_native_capture_status_v1' or
            capture.get('state') not in ('RECORDING','PAUSED','FINISHING') or
            capture.get('source_context_sha256')!=native_context_sha256(context) or
            capture.get('source_clock_domain')!=source_identity.get('clock_domain') or
            capture.get('source_boot_id')!=source_identity.get('boot_id') or
            capture.get('project_id')!=context['project_id']):
        raise ValueError('Exact known native9 DATA context/clock/boot and active original required')
    return identity


def validate_native_capture_finish(body,source_identity,identity,manifest,expected_outcome):
    context=normalize_capture_context(body.get('skill_context'),observation_only=body.get('observation_only',False))
    episode=manifest.get('embodied_episode') or {};record=episode.get('recording') or {}
    if (not re.fullmatch('[a-f0-9]{32}',str(identity)) or
            manifest.get('schema')!='explorer_native_embodied_capture_manifest_v1' or
            manifest.get('recording_id')!=identity or manifest.get('project_id')!=context['project_id'] or
            manifest.get('state') not in ('completed','cancelled') or record.get('status')!=manifest.get('state') or
            manifest.get('skill_context')!=context or episode.get('episode_id')!=identity or
            (episode.get('clock_domains') or {}).get(source_identity['clock_domain'],{}).get('boot_id')!=source_identity['boot_id'] or
            record.get('operator_outcome')!=expected_outcome.get('operator_outcome') or
            (manifest.get('embodied_source_files_sha256') or {}).get('sources/outcome.json')!=native_context_sha256(expected_outcome) or
            manifest.get('physical_success_inferred') is not False or
            manifest.get('controller_settings_written') is not False):
        raise ValueError('Exact known sealed native9 DATA context/clock/outcome required')
    return identity
