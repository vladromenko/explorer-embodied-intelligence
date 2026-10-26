"""ENGINEERING_GLUE: cooperative whole-task spec with separate native policies.

Giver ROSMASTER remains9D; receiver SO101 remains its actual native6D. Neither
actions nor clocks/datasets are concatenated into an invented common head.
"""
from copy import deepcopy
import hashlib
import json
import re
import uuid

from .contracts import ACTION_NAMES,ACTION_UNITS
from .embodied_episode import ACTION_CONTRACT_ID
from .experiments import experiment_identity,experiment_id
from .handover import STATES
from .so101_station import NAMES,UNITS

SCHEMA='explorer_cooperative_skill_spec_v1'
SO_CONTRACT_ID='so101_follower_calibrated_position6_v1'
MODES=('both_manual','giver_policy_receiver_manual','giver_manual_receiver_policy','both_policy')
PHASES=tuple(state for state in STATES if state not in ('FAULT','SAFE_STOP'))
GIVER_SOURCES=('rosmaster_keyboard_learning','rosmaster_leader_learning','cross_embodiment_transfer')
ROLE_CONTRACTS={
    'giver':{'id':ACTION_CONTRACT_ID,'dimension':9,'names':list(ACTION_NAMES),'units':list(ACTION_UNITS)},
    'receiver':{'id':SO_CONTRACT_ID,'dimension':6,'names':list(NAMES),'units':list(UNITS)},
}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def text(value,field,limit=240):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError('Explicit bounded '+field+' required')
    return value


def sha256(value,field):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
        raise ValueError('Exact SHA256 '+field+' required')
    return value


def native_role_reference(role,reference):
    if role not in ROLE_CONTRACTS or not isinstance(reference,dict):
        raise ValueError('Named cooperative native role reference required')
    ref=deepcopy(reference);slug=ref.get('experiment_slug')
    if (role=='giver' and slug not in GIVER_SOURCES) or (role=='receiver' and slug!='so101_source_learning'):
        raise ValueError('Subskill source embodiment does not match cooperative role')
    experiment_id(slug)
    ref['skill_id']=str(uuid.UUID(str(ref.get('skill_id'))))
    if type(ref.get('skill_version')) is not int or ref['skill_version']<1:
        raise ValueError('Pinned subskill version required')
    sha256(ref.get('spec_sha256'),'subskill spec')
    if ref.get('action_contract')!=ROLE_CONTRACTS[role]:
        raise ValueError('Native action contract must remain exact for '+role)
    phases=ref.get('phase_map',{})
    if not isinstance(phases,dict) or any(key not in PHASES for key in phases):
        raise ValueError('Cooperative-to-source phase map required')
    for value in phases.values():text(value,'original native phase ID',100)
    if ref.get('model_id') is not None:
        text(ref['model_id'],'model ID',160)
        sha256(ref.get('checkpoint_sha256'),'checkpoint')
    ref.setdefault('execution_scope','targeted_subskill')
    if ref['execution_scope'] not in ('whole_skill','targeted_subskill'):
        raise ValueError('Explicit native subskill execution scope required')
    return ref


def skill_spec(body):
    if not isinstance(body,dict):raise ValueError('Cooperative skill object required')
    spec=deepcopy(body);spec['schema']=SCHEMA
    spec['skill_id']=str(uuid.UUID(str(spec.get('skill_id') or uuid.uuid4())))
    spec.setdefault('version',1)
    if type(spec['version']) is not int or spec['version']<1:raise ValueError('Positive cooperative skill version required')
    text(spec.get('name'),'task name',160);text(spec.get('object_description'),'actual object description')
    text(spec.get('receiver_goal_description'),'receiver finishing task')
    if spec.get('mode','both_manual') not in MODES:raise ValueError('Supported cooperative development mode required')
    spec.setdefault('mode','both_manual')
    if spec.get('initial_base_mode','stationary')!='stationary':
        raise ValueError('Qualify the initial stationary shared-zone scenario before adding mobile transport')
    references=spec.get('subskills',{})
    if not isinstance(references,dict) or set(references)!={'giver','receiver'}:
        raise ValueError('Both pinned native subskills are required')
    spec['subskills']={role:native_role_reference(role,references[role]) for role in ROLE_CONTRACTS}
    criteria=spec.get('outcome_criteria')
    if not isinstance(criteria,list) or not criteria or len(criteria)>20:
        raise ValueError('Explicit independent whole-task outcome criteria required')
    for criterion in criteria:text(criterion,'task outcome criterion')
    spec.update(experiment_identity('cooperative_handover'))
    spec.update(phases=list(PHASES),native_action_contracts=deepcopy(ROLE_CONTRACTS),
        initial_base_mode='stationary',datasets_and_action_heads_separate=True,
        success_requires_independent_outcome=True,closed_servo_is_not_hold_evidence=True,
        hold_evidence_fallback='explicit_authenticated_operator_confirmation',
        novel_method_claimed=False,physical_execution_authorized=False)
    spec['spec_sha256']=digest({key:value for key,value in spec.items() if key!='spec_sha256'})
    return spec


def source_contexts(spec,session):
    """Exact underlying source context, with a separate cooperative sidecar link."""
    contexts={}
    for role,ref in spec['subskills'].items():
        context={'project_id':experiment_id(ref['experiment_slug']),'skill_id':ref['skill_id'],
            'skill_version':ref['skill_version'],'skill_spec_sha256':ref['spec_sha256'],
            'session_id':str(uuid.UUID(session['session_id'])),'scene_id':session['scene_id'],'scene':session['scene_id'],
            'split':session['split'],'object_instance_id':session['object_instance_id'],
            'variant_id':session.get('native_variants',{}).get(role),
            'cooperative_parent_session_id':session['session_id'],
            'cooperative_parent':{'session_id':session['session_id'],'skill_id':spec['skill_id'],
                'skill_version':spec['version'],'spec_sha256':spec['spec_sha256'],'role':role},
            'episode_scope':'whole_episode' if ref['execution_scope']=='whole_skill' else 'targeted_subskill'}
        if context['episode_scope']=='targeted_subskill':
            context['target_phase_ids']=list(dict.fromkeys(ref.get('phase_map',{}).values()))
        if ref.get('phase_map',{}).get('PREPARE') is not None:context['phase_id']=ref['phase_map']['PREPARE']
        if role=='giver':
            # Native target producer retains its supported source adapter;05 is
            # a parent link, not an ambiguous new target data-plane identity.
            mode=session['giver_input_mode']
            if mode not in ('keyboard','browser_gamepad','so101_leader','policy'):
                raise ValueError('Actual declared giver input adapter required; never infer from source-model project')
            context['project_id']=experiment_id('rosmaster_leader_learning' if mode=='so101_leader' else 'rosmaster_keyboard_learning')
            context['input_mode']=mode
            context['source_skill_experiment_slug']=ref['experiment_slug']
            context['spec_sha256']=ref['spec_sha256']
            context['stationary_chassis_confirmed']=session.get('stationary_chassis_confirmed') is True
        else:
            context['context']={'cooperative_parent':deepcopy(context['cooperative_parent']),
                                'cooperative_parent_session_id':session['session_id'],
                                'input_mode':session['receiver_input_mode']}
        contexts[role]=context
    return contexts


def model_input_plan():
    return {'schema':'explorer_cooperative_model_input_plan_v1',
        'role_models':{'giver':{'native_dimension':9,'provenance':'existing unified ROSMASTER model and saved input plan'},
                       'receiver':{'native_dimension':6,'provenance':'existing native SO101 model and saved input plan'}},
        'coordinator':{'baseline':'evidence-gated FSM','learned':False,
            'inputs':['native source receipts','independent/operator hold evidence','two cached camera identities','shared-zone registration'],
            'cross_robot_synchronous_tensor_qualified':False},
        'missing_sensors':'preserved unavailable with masks; no fabricated force/contact/measured ROSMASTER q',
        'vlm_role':'semantic diagnostic only; never holding-force or motor-coordinate authority',
        'camera_capture_owners_added':0,'action_spaces_merged':False,'clock_domains_equated':False}
