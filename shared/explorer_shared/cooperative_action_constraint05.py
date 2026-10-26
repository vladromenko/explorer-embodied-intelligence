"""ENGINEERING_GLUE: explicit stationary coordinator projection, raw9 retained.

This is a source-bound cooperative execution constraint, not an arm-only model,
unknown-state mask, controller limit or a claim that the base is stationary.
"""
from copy import deepcopy
import math

from .cooperative_contract05 import SCHEMA,ROLE_CONTRACTS,canonical,digest

SCHEMA_CONSTRAINT='explorer_cooperative_stationary_base_constraint_v1'
SCHEMA_PROJECTION='explorer_cooperative_stationary_base_projection_v1'
SESSION_FIELDS=('session_id','epoch','protocol_object_id','spec_sha256','stationary_chassis_confirmed','giver_input_mode')


def _nine(value):
    if (not isinstance(value,list) or len(value)!=9 or any(type(item) not in (int,float) or not math.isfinite(item) for item in value)):
        raise ValueError('Exact finite original native action9 required')
    return deepcopy(value)


def stationary_constraint(spec,session,model):
    if (not isinstance(spec,dict) or len(canonical(spec))>128*1024 or spec.get('schema')!=SCHEMA or
            spec.get('spec_sha256')!=digest({key:value for key,value in spec.items() if key!='spec_sha256'}) or
            spec.get('initial_base_mode')!='stationary' or spec.get('native_action_contracts',{}).get('giver')!=ROLE_CONTRACTS['giver']):
        raise ValueError('Original source-bound cooperative stationary SkillSpec required')
    if not isinstance(session,dict):raise ValueError('Original cooperative session context required')
    source={key:deepcopy(session.get(key)) for key in SESSION_FIELDS}
    if (source['stationary_chassis_confirmed'] is not True or source['giver_input_mode']!='policy' or
            source['spec_sha256']!=spec['spec_sha256'] or any(not isinstance(source[key],str) or not source[key] for key in SESSION_FIELDS[:4])):
        raise ValueError('Explicit source-session stationary policy constraint required')
    reference=spec['subskills']['giver'];expected={key:reference.get(key) for key in ('model_id','checkpoint_sha256','spec_sha256')}
    if model!=expected or not all(expected.values()):raise ValueError('Exact pinned original native9 policy model required')
    value={'schema':SCHEMA_CONSTRAINT,'source_cooperative_spec':deepcopy(spec),'source_session':source,
        'model':deepcopy(model),'action_contract':deepcopy(ROLE_CONTRACTS['giver']),
        'rule':'coordinator_stationary_base_projects_only_vx_vy_wz_to_requested_zero',
        'projected_indices':[6,7,8],'raw_policy9_must_be_retained':True,'physical_stationarity_claimed':False,
        'unavailable_values_replaced':False,'controller_settings_written':False}
    value['constraint_sha256']=digest(value)
    return value


def verify_constraint(value,*,binding=None,model=None):
    if not isinstance(value,dict):raise ValueError('Registered explicit coordinator constraint required')
    expected=stationary_constraint(value.get('source_cooperative_spec'),value.get('source_session') or {},value.get('model'))
    if value!=expected:raise ValueError('Original stationary constraint source/digest differs')
    source=value['source_session']
    if binding is not None and any(source[field]!=binding.get(key) for field,key in
            (('session_id','run_id'),('epoch','coordinator_epoch'),('protocol_object_id','object_id'))):
        raise ValueError('Stationary constraint belongs to another cooperative run/object/epoch')
    if model is not None and value['model']!=model:raise ValueError('Stationary constraint model changed')
    return deepcopy(value)


def project_stationary_action(raw9,constraint):
    selected=verify_constraint(constraint);raw=_nine(raw9);issued=[float(value) for value in raw[:6]]+[0.,0.,0.]
    receipt={'schema':SCHEMA_PROJECTION,'constraint_sha256':selected['constraint_sha256'],
        'raw_policy_action9':raw,'raw_policy_action9_sha256':digest(raw),'requested_action9':issued,
        'requested_action9_sha256':digest(issued),'projected_indices':[6,7,8],'valid_mask':[True]*9,
        'rule':selected['rule'],'raw_base_was_neutral':all(value==0 for value in raw[6:]),
        'base_request_is_not_attained_stationarity':True,'unavailable_values_replaced':False,'action_contract':deepcopy(ROLE_CONTRACTS['giver'])}
    return issued,receipt
