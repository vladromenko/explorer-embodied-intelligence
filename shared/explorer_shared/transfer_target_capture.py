"""Pure06 metadata for the SAME native9 source/camera/file writer.

This binds a target demonstration to an admitted source wizard. It contains no
controller action, permission/grant, device access or successful-outcome claim.
"""
from copy import deepcopy
import re
import uuid

from .contracts import ACTION_NAMES,ACTION_UNITS,digest

SCHEMA='explorer_transfer_target_capture_context_v1'
SPEC_SCHEMA='explorer_transfer_target_skill_spec_v1'
CONTRACT='rosmaster_joint9_position6_velocity3_v1'


def _uuid(value):
    try:return str(uuid.UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def validate_target_capture_context(context):
    if not isinstance(context,dict):raise ValueError('Explicit target06 DATA context required')
    binding=context.get('source_model_binding') or {};spec=context.get('skill_spec') or {}
    if not isinstance(binding,dict) or not isinstance(spec,dict):raise ValueError('Structured source binding and target Spec required')
    hashes=('spec_sha256','aggregate_manifest_sha256','training_receipt_sha256',
            'checkpoint_content_sha256','source_calibration_sha256','source_urdf_sha256')
    if (context.get('schema')!=SCHEMA or context.get('project_id')!='06' or
            context.get('target_domain')!='physical' or
            not all(_uuid(context.get(key)) for key in ('capture_id','wizard_id','skill_id','session_id')) or
            not context.get('created_by_actor') or
            binding.get('skill_id')!=context['skill_id'] or not re.fullmatch('[a-f0-9]{32}',str(binding.get('model_job_id'))) or
            any(not re.fullmatch('[a-f0-9]{64}',str(binding.get(name))) for name in hashes) or
            binding.get('source_domain') not in ('SIMULATION','PHYSICAL') or
            type(binding.get('skill_version')) is not int or binding['skill_version']<1 or
            type(context.get('skill_version')) is not int or context['skill_version']<=binding['skill_version'] or
            context.get('version')!=context['skill_version']):
        raise ValueError('Exact source-model/SAME UUID/advancing physical target06 metadata required')
    if (spec.get('schema')!=SPEC_SCHEMA or spec.get('skill_id')!=context['skill_id'] or
            spec.get('version')!=context['skill_version'] or spec.get('source_spec_sha256')!=binding['spec_sha256'] or
            spec.get('source_model_job_id')!=binding['model_job_id'] or
            spec.get('action_contract_id')!=CONTRACT or spec.get('action_names')!=list(ACTION_NAMES) or
            spec.get('action_units')!=list(ACTION_UNITS) or digest(spec)!=context.get('skill_spec_sha256') or
            context.get('spec_sha256')!=context['skill_spec_sha256']):
        raise ValueError('Exact target9 SkillSpec and retained source Spec lineage required')
    source_spec=spec.get('source_spec_snapshot')
    if not isinstance(source_spec,dict) or digest(source_spec)!=binding['spec_sha256']:
        raise ValueError('Exact retained source SkillSpec snapshot required')
    if (source_spec.get('skill_id')!=context['skill_id'] or source_spec.get('version')!=binding['skill_version'] or
            spec.get('stages')!=source_spec.get('stages')):
        raise ValueError('Same source task and complete phase contract required')
    phases=[row.get('id') for row in spec.get('stages',[]) if isinstance(row,dict)]
    if (not phases or len(phases)!=len(spec.get('stages',[])) or any(not isinstance(name,str) or not name for name in phases) or
            len(phases)!=len(set(phases)) or context.get('phase_id') not in phases or
            context.get('target_phase_ids')!=phases or context.get('episode_scope')!='whole_episode'):
        raise ValueError('Complete ordered target-task phases required')
    split=context.get('split') or {}
    if not isinstance(split,dict):raise ValueError('Structured original target split required')
    if (split.get('partition') not in ('train','development','sealed_test') or split.get('session_id')!=context['session_id'] or
            any(not isinstance(split.get(key),str) or not 0<len(split[key])<=160 for key in
                ('scene_id','object_instance_id','group_id')) or
            not isinstance(context.get('variant_id'),str) or not 0<len(context['variant_id'])<=160):
        raise ValueError('Operator-declared target original split/scene/object/group required')
    if (context.get('automatic_motion') is not False or context.get('native_payload_additions')!={} or
            context.get('stationary_chassis_confirmed') is not True or
            context.get('action_contract_id')!=CONTRACT or
            context.get('source_action_head_reused') is not False):
        raise ValueError('DATA-only stationary target9 context required; no action/grant additions')
    return deepcopy(context)


def build_target_capture_context(source_model_binding,target_spec,*,wizard_id,capture_id,actor_id,
                                 session_id,partition,scene_id,object_instance_id,group_id,variant_id,phase_id):
    context=dict(schema=SCHEMA,project_id='06',target_domain='physical',wizard_id=wizard_id,capture_id=capture_id,
        created_by_actor=actor_id,source_model_binding=deepcopy(source_model_binding),
        skill_id=target_spec['skill_id'],skill_version=target_spec['version'],version=target_spec['version'],
        skill_spec=deepcopy(target_spec),skill_spec_sha256=digest(target_spec),spec_sha256=digest(target_spec),
        session_id=session_id,variant_id=variant_id,episode_scope='whole_episode',phase_id=phase_id,
        target_phase_ids=[row['id'] for row in target_spec['stages']],input_mode='manual_native9_target',
        split=dict(partition=partition,scene_id=scene_id,session_id=session_id,object_instance_id=object_instance_id,
            group_id=group_id,group_by=['scene_id','session_id','episode_id','object_instance_id']),
        action_contract_id=CONTRACT,neutral_channels_retained=True,stationary_chassis_confirmed=True,
        automatic_motion=False,native_payload_additions={},source_action_head_reused=False)
    return validate_target_capture_context(context)
