"""ENGINEERING_GLUE: editable SO101 source-role tasks and Experiment05 composition.

Only planning, immutable intent and separately verified readiness. This module
never opens a robot/camera, trains a policy or authorizes physical execution.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import time
import uuid

from explorer_shared.contracts import ACTION_NAMES as ROSMASTER_NAMES,canonical,digest,event
from explorer_shared.so101_station import NAMES as SO101_NAMES,UNITS as SO101_UNITS
from explorer_shared.experiments import canonical_experiment,experiment_id,experiment_identity,experiment_slug

SO101_ROLE='so101_source_learning'
SO101_PROJECT=experiment_id(SO101_ROLE)
SCHEMA='explorer_skill_catalogue_spec_v1'
SO101_CONTRACT='so101_follower_calibrated_position6_v1'
ROSMASTER_CONTRACT='rosmaster_joint9_position6_velocity3_v1'
EVENT_PREFIX='skill.catalogue.'
PICK_PHASES=('observe_select','approach','grasp','lift_verify','transport','place_release','retreat')
PHASE_TITLES=dict(observe_select='Наблюдать и выбрать предмет / цель',approach='Подвести захват',grasp='Захватить предмет',
    lift_verify='Поднять и проверить удержание',transport='Перенести с удержанием',place_release='Поставить и проверить отпускание',retreat='Убрать руку и проверить результат',
    select_held_object='Подтвердить удерживаемый предмет',observe_goal='Наблюдать цель',push_contact='Подвести контактную поверхность',push='Подтолкнуть в выбранную ячейку',
    verify_goal='Проверить конечное положение',classify_select='Выбрать класс / цвет',validate_stack_geometry='Проверить геометрию устойчивой стопки',
    request_delivery='Зафиксировать запрос и получателя',deliver_to_handoff='Доставить ROSMASTER к месту передачи',
    coordinate_handoff='Согласовать владение и принять предмет рукой SO-101',present_to_person='Подать предмет человеку',
    verify_person_grasp='Независимо подтвердить захват человеком',release_after_grasp='Отпустить только после подтверждения захвата',verify_handoff='Подтвердить завершение передачи')
TEMPLATES={
    'pick_object_to_container':dict(title='Взять предмет и положить в контейнер',example='взять конфетку и положить в коробку',task_type='pick_object_to_container',object='Конфетка',source='Стол',goal='Коробка',phases=PICK_PHASES),
    'pick_from_location':dict(title='Взять предмет в заданном месте',example='взять предмет со стола',task_type='pick',object='Предмет',source='Выбранное место',goal='Удержание над опорой',phases=PICK_PHASES[:4]+('retreat',)),
    'place_to_container_or_grid':dict(title='Положить удерживаемый предмет в контейнер / ячейку',example='положить предмет в ячейку B2',task_type='place',object='Удерживаемый предмет',source='Подтверждённый захват',goal='Контейнер / ячейка',phases=('select_held_object','observe_goal','transport','place_release','retreat')),
    'move_a_to_b':dict(title='Переложить из A в B',example='переложить кубик из A в B',task_type='move_a_to_b',object='Предмет',source='Точка A',goal='Точка B',phases=PICK_PHASES),
    'push_to_cell':dict(title='Подтолкнуть в ячейку',example='подтолкнуть кубик в ячейку C3',task_type='push',object='Предмет',source='Выбранное место',goal='Целевая ячейка',phases=('observe_select','approach','push_contact','push','verify_goal','retreat')),
    'sort_by_class_or_colour':dict(title='Рассортировать по классу / цвету',example='разложить красные кубики по коробкам',task_type='sort',object='Объекты выбранного класса / цвета',source='Рабочая поверхность',goal='Соответствующие контейнеры',phases=('classify_select',)+PICK_PHASES),
    'stack_if_geometry_validated':dict(title='Поставить один предмет на другой',example='поставить кубик на кубик',task_type='stack',object='Предмет',source='Рабочая поверхность',goal='Устойчивая опора другого предмета',phases=('validate_stack_geometry',)+PICK_PHASES),
}
SOURCE_SENSORS=('top_rgb','camera_identity_calibration','leader_measured','follower_requested','follower_accepted','follower_dispatched','follower_measured','measurement_masks','gripper','clock_boot_domains','camera_control_clock_mapping','selection_goal','phase_outcomes')
SOURCE_PHASES={phase for template in TEMPLATES.values() for phase in template['phases']}
# Historical import names refer to the same SO101 semantics, never new transfer06.
SENSORS06=SOURCE_SENSORS
PHASES06=SOURCE_PHASES


def owner_identity(document,*,fallback_version=2):
    """Numeric legacy06 requires an explicit v1; personal is an unambiguous alias."""
    owner=document.get('owner_experiment',document.get('project_id'))
    version=document.get('experiment_numbering_version',fallback_version)
    return canonical_experiment(owner,numbering_version=version)


def is_source_spec(spec):
    try:
        contract=spec.get('action_contract') or {}
        return owner_identity(spec)==SO101_PROJECT and contract.get('id')==SO101_CONTRACT and contract.get('names')==list(SO101_NAMES) and contract.get('units')==list(SO101_UNITS)
    except (ValueError,TypeError):return False


def _text(value,label,maximum=600,empty=False):
    if not isinstance(value,str) or len(value.strip())>maximum or (not empty and not value.strip()):raise ValueError('Explicit short text required: '+label)
    return value.strip()


def _uuid(value):
    try:return str(uuid.UUID(str(value)))
    except (ValueError,TypeError,AttributeError) as error:raise ValueError('Explicit skill UUID required') from error


def _hash(value):return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None


def catalogue():
    return dict(schema='explorer_skill_template_catalogue_v1',owner_experiment=SO101_PROJECT,route='/experiments/'+SO101_PROJECT,legacy_route='/learning/so101',
        title='Две руки LeRobot · показ, обучение и повторение',templates=[dict(template_id=key,**deepcopy(value)) for key,value in TEMPLATES.items()],
        robot_contract=dict(id=SO101_CONTRACT,names=list(SO101_NAMES),units=list(SO101_UNITS),dimension=6),physical_execution_authorized=False,
        **experiment_identity(SO101_ROLE))


def _route(text):
    mobile=bool(re.search(r'rosmaster|росмастер',text,re.I))
    human=bool(re.search(r'\b(?:мне|человеку|владельцу|получателю)\b',text,re.I))
    give=bool(re.search(r'\b(?:дай|дать|передай|передать|подай|подать|принеси|принести)\b',text,re.I))
    return '05' if mobile and human and give else '02' if mobile else SO101_PROJECT


def _custom(text):
    """A few literal patterns; uncertain facts remain explicit and editable."""
    match=re.fullmatch(r'\s*(?:взять|возьми)\s+(.+?)\s+и\s+(?:положить|положи)\s+(?:в|на)\s+(.+?)[.!]?\s*',text,re.I)
    if match:return 'pick_object_to_container',match.group(1).strip(),match.group(2).strip(' .!')
    match=re.fullmatch(r'\s*(?:положить|положи|переложить|переложи|перенести|перенеси)\s+(.+?)\s+(?:в|на)\s+(.+?)[.!]?\s*',text,re.I)
    if match:return 'pick_object_to_container',match.group(1).strip(),match.group(2).strip(' .!')
    return None,'UNKNOWN','UNKNOWN'


def _derive(spec):
    spec=deepcopy(spec);owner=owner_identity(spec)
    if owner not in ('02','05',SO101_PROJECT):raise ValueError('Use the ROSMASTER, SO101 source or coordinator catalogue owner')
    identity=experiment_identity(experiment_slug(owner,numbering_version=2))
    spec.update(owner_experiment=owner,experiment_slug=identity['experiment_slug'],experiment_numbering_version=2)
    spec['schema']=SCHEMA;spec['status']='PROPOSED'
    spec['constraints']=dict(physical_motion_authorized=False,separate_user_observed_arm_test_required=True,chassis_motion_forbidden=True,
        original_controller_settings='PRESERVE',no_automatic_deployment=True)
    spec['language_semantics']=dict(text_role='editable_SkillSpec_metadata_not_ACT_language_input',ACT_arbitrary_instruction_conditioning=False,
        per_skill_version_dataset_and_model=True,proposal_is_not_NLU_certainty=True)
    spec['demo_plan']=dict(episode_scopes=['whole_episode','targeted_subskill','recovery'],same_skill_and_action_contract_for_all_scopes=True,
        variants=[dict(variant_id='variant-'+str(index+1).zfill(3),split=split,title=title,target_accepted_whole_episodes=1,
            vary=['initial_robot','object_pose_geometry','goal_pose','scene','session'],independence_basis='operator_declared_plus_source_bound_split_groups')
            for index,(split,title) in enumerate((('train','Исходные условия'),('train','Другой подход'),('train','Другое положение предмета и цели'),('development','Новая проверочная сцена / сессия'),('sealed_test','Отложенная сцена / сессия — не дообучать')))],
        count_is_technical_smoke_not_mastery=True,minimum_before_training=dict(train_variants=3,development_variants=1,sealed_variants=1),
        sealed_data_never_optimizer_or_adaptive_correction=True,split_group_by=['original_episode_id','scene_id','session_id','object_instance_id','RGB_frame_identity'])
    if owner==SO101_PROJECT:
        spec['robots']=[dict(role='leader',embodiment='SO101',purpose='measured_human_teaching'),dict(role='follower',embodiment='SO101',purpose='target_robot')]
        spec['action_contract']=dict(id=SO101_CONTRACT,names=list(SO101_NAMES),units=list(SO101_UNITS),dimension=6,target='SO101_follower',
            label_basis='actual_follower_dispatched_command_distinct_from_leader_requested_accepted_and_measured')
        spec['sensors']=dict(required_schema_channels=list(SOURCE_SENSORS),primary_camera=dict(profile='pi-overview',role='single_fixed_table_camera',hardware_identity='UNKNOWN'),
            unknowns='preserve_null_and_actual_availability_masks_never_zero_fill',desired_model_inputs=['RGB','measured_follower6'],
            recorded_admitted_model_used_are_distinct=True)
        spec['execution_method']=dict(candidate='official_LeRobot_0.6_ACT_per_skill_baseline',implemented_by_catalogue=False,
            data_adapter='explorer_sim.so101_dataset_qualification',raw_episode_replay_is_not_learned_rollout=True,
            learned_rollout_requires_fresh_observations=True,first_policy_execution='shadow',physical_rollout_requires_separate_user_observed_test=True)
        spec['execution_method'].update(coordinates='existing_station_calibrated_normalized6_not_official_default_degrees',
            official_default_robot_connect_allowed=False,hardware_connect_qualified=False,
            hardware_adapter_requirement='preserve_existing_native_executor_and_controller_settings; official_dataset_readback_does_not_qualify_configure_or_replay')
        spec['stages']=[dict(id=phase,title=PHASE_TITLES[phase]) for phase in spec['phase_ids']]
        spec['composite']=None
    elif owner=='05':
        phases=('request_delivery','deliver_to_handoff','coordinate_handoff','present_to_person','verify_person_grasp','release_after_grasp','verify_handoff')
        spec['robots']=[dict(role='deliverer',embodiment='ROSMASTER_unified_mobile_manipulator'),dict(role='receiver',embodiment='SO101_follower'),dict(role='recipient',embodiment='human')]
        spec['action_contract']=None
        spec['sensors']=dict(required_schema_channels=['robot_relative_registration','shared_scene_visibility','object_identity','native_dispatch_receipts','retained_hold','person_grasp_evidence','clock_boot_domains'],
            unknowns='preserve_UNKNOWN_no_VLM_contact_or_timed_release')
        references=spec.get('subskill_references') or [dict(role='deliver_to_handoff',owner_experiment='02',experiment_numbering_version=2,skill_id=None,version=None,spec_sha256=None),dict(role='receive_and_offer',owner_experiment=SO101_PROJECT,experiment_numbering_version=2,skill_id=None,version=None,spec_sha256=None)]
        spec['subskill_references']=references
        spec['composite']=dict(parent_owner='05',versioned_subskill_references=references,datasets_may_merge=False,concatenate_action_spaces=False,
            child_contracts={'02':ROSMASTER_CONTRACT,SO101_PROJECT:SO101_CONTRACT},release_guard='source_bound_independent_person_grasp_evidence',
            timed_release_allowed=False,VLM_is_contact_measurement=False)
        spec['execution_method']=dict(candidate='coordinator_handshake_FSM_with_versioned_02_source_subskills',implemented_by_catalogue=False,
            physical_rollout_requires_separate_user_observed_test=True)
        spec['demo_plan'].update(training_scope='coordinator_scenarios_not_merged_parent_policy_dataset',datasets_may_merge=False)
        spec['stages']=[dict(id=phase,title=PHASE_TITLES[phase]) for phase in phases]
    else:
        from .skill_workflow import proposal
        unified=proposal(text=spec['source_text'])
        spec['robots']=[dict(role='target',embodiment='ROSMASTER_unified_mobile_manipulator')]
        spec['action_contract']=dict(id=ROSMASTER_CONTRACT,names=list(ROSMASTER_NAMES),dimension=9,simultaneous_9d=True,neutral_channels_retained=True)
        spec['sensors']=deepcopy(unified['observation_contract']);spec['stages']=deepcopy(unified['stages'])
        spec['execution_method']=dict(candidate='unified_perception_navigation_contact_coordinator',implemented_by_catalogue=False,workflow_owner='explorer.skill_workflow')
        spec['composite']=None
    missing=[]
    for key in ('object','goal'):
        if spec[key]['query']=='UNKNOWN':missing.append(dict(id=key,required_for='confirmation',reason='Уточните '+('предмет' if key=='object' else 'цель')))
    if owner==SO101_PROJECT and not spec.get('template_id'):missing.append(dict(id='task_type',required_for='confirmation',reason='Выберите подходящий шаблон и фазы'))
    if owner=='05':missing.append(dict(id='versioned_subskill_references',required_for='confirmation',reason='Выберите конкретные версии навыков ROSMASTER и SO101'))
    if owner=='02':missing.append(dict(id='open_unified_02_workflow',required_for='confirmation',reason='Определение и показы ROSMASTER подтверждаются в общем workflow опыта02'))
    if owner==SO101_PROJECT and spec.get('template_id')=='stack_if_geometry_validated':missing.append(dict(id='stable_stack_geometry',required_for='physical_validation',reason='Размеры, плоскости опоры и устойчивость стопки должны быть фактически проверены'))
    missing.extend((dict(id='source_bound_geometry_camera',required_for='physical_validation',reason='Identity/calibration камеры, рабочая область и метрическая геометрия требуют фактической проверки'),
        dict(id='native_full_teaching_provenance',required_for='training',reason='Нужны реальные source-bound leader/requested/accepted/dispatched/measured, clocks, calibration, отбор и независимый outcome'),
        dict(id='separate_user_observed_physical_test',required_for='physical_execution',reason='Ни одна рука не движется по одному описанию или наличию модели')))
    spec['missing_facts']=missing
    spec['success']=dict(spec.get('success') or {},full_task=True,verification='independent_full_task_outcome_separate_from_operator_label_and_command_dispatch')
    return spec


def propose_skill(text='',template_id=None):
    text=_text(text,'описание',empty=template_id is not None)
    if template_id is not None and template_id not in TEMPLATES:raise ValueError('Unknown SO101 source template')
    owner=SO101_PROJECT if template_id else _route(text)
    suggested=template_id
    if owner==SO101_PROJECT and suggested is None:suggested,obj,goal=_custom(text)
    else:obj=goal='UNKNOWN'
    base=TEMPLATES.get(suggested,{})
    if template_id:obj=base['object'];goal=base['goal']
    if owner=='05':
        match=re.search(r'(?:дай|дать|передай|передать|подай|подать|принеси|принести)\s+(?:мне|человеку|владельцу|получателю)\s+(.+?)(?:,|\s+котор|$)',text,re.I)
        obj=match.group(1).strip() if match else 'UNKNOWN';goal='Подтверждённый захват получателем'
    return _derive(dict(skill_id=str(uuid.uuid4()),version=1,owner_experiment=owner,title=base.get('title') or text[:100],source_text=text,
        proposal_basis='deterministic_template' if template_id else 'PROPOSED_literal_patterns_not_certain_NLU',template_id=suggested,
        recognized_type=base.get('task_type') if owner==SO101_PROJECT else 'composite_delivery_and_handover' if owner=='05' else 'unified_mobile_manipulation',
        object=dict(query=obj),source=dict(query='ROSMASTER' if owner=='05' else base.get('source','UNKNOWN')),
        goal=dict(query=goal),recipient=dict(query='Человек: '+(re.search(r'\b(мне|человеку|владельцу|получателю)\b',text,re.I).group(1) if re.search(r'\b(мне|человеку|владельцу|получателю)\b',text,re.I) else 'UNKNOWN') if owner=='05' else 'Целевое место'),
        phase_ids=list(base.get('phases',PICK_PHASES)),success=dict(description='Вся выбранная задача завершена и результат независимо проверен')))


def validate_subskill_references(spec,known_specs=()):
    if owner_identity(spec)!='05':return []
    errors=[];refs=spec.get('subskill_references') or []
    if not isinstance(refs,list) or len(refs)!=2 or any(not isinstance(row,dict) for row in refs) or {row.get('role') for row in refs}!={'deliver_to_handoff','receive_and_offer'}:return ['exactly_two_versioned_02_source_roles_required']
    for ref in refs:
        expected='02' if ref.get('role')=='deliver_to_handoff' else SO101_PROJECT
        selected=next((row for row in known_specs if row.get('spec',row).get('skill_id')==ref.get('skill_id') and row.get('spec',row).get('version')==ref.get('version')),None)
        child=(selected or {}).get('spec',selected or {})
        owner=child.get('owner_experiment') or ('02' if child.get('embodiment')=='rosmaster_unified_mobile_manipulator' else None)
        try:
            owner=owner_identity(dict(child,owner_experiment=owner))
            ref_owner=owner_identity(ref,fallback_version=spec.get('experiment_numbering_version',2))
        except (ValueError,TypeError):owner=ref_owner=None
        contract=(child.get('action_contract') or {}).get('id') or (child.get('action_profile') or {}).get('schema')
        if ref_owner!=expected or owner!=expected or contract!=({'02':ROSMASTER_CONTRACT,SO101_PROJECT:SO101_CONTRACT}[expected]) or type(ref.get('version')) is not int or not _hash(ref.get('spec_sha256')) or digest(child)!=ref.get('spec_sha256') or (selected or {}).get('confirmed') is not True:
            errors.append('unresolved_or_mismatched_versioned_subskill:'+str(ref.get('role')))
    return errors


def _records(db):
    rows=[]
    for row in db.execute("SELECT body FROM events WHERE json_extract(body,'$.payload.type') LIKE 'skill.catalogue.%' ORDER BY rowid"):
        envelope=json.loads(row['body'])
        if digest(envelope['payload'])!=envelope['sha256']:raise ValueError('Catalogue event checksum mismatch')
        rows.append(envelope['payload'])
    return rows


def _append(db,owner,kind,payload,identity):
    payload=dict(payload,type=EVENT_PREFIX+kind);envelope=event('nx',owner,payload);envelope['event_id']=identity
    envelope.update(experiment_identity(experiment_slug(owner,numbering_version=2)))
    previous=db.execute('SELECT body FROM events WHERE event_id=?',(identity,)).fetchone()
    if previous:
        if json.loads(previous['body'])['payload']!=payload:raise ValueError('Conflicting immutable catalogue event')
    else:db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(identity,'nx',time.time(),envelope['sha256'],canonical(envelope).decode()))


def _selected(db,skill_id,version=None):
    if version is not None and (type(version) is not int or version<1):raise ValueError('Positive integer skill version required')
    skill_id=_uuid(skill_id);records=_records(db);found=[row for row in records if row['type']==EVENT_PREFIX+'spec' and row['spec']['skill_id']==skill_id]
    if version is None:version=max((row['spec']['version'] for row in found),default=0)
    chosen=next((row for row in found if row['spec']['version']==version),None)
    if chosen is None:raise ValueError('Unknown catalogue skill version')
    spec=chosen['spec'];checksum=digest(spec)
    try:identity=experiment_identity(experiment_slug(owner_identity(spec),numbering_version=2))
    except (ValueError,TypeError):identity=None
    return dict(spec=spec,spec_sha256=checksum,canonical_identity=identity,original_owner_experiment=spec['owner_experiment'],
        confirmed=any(row['type']==EVENT_PREFIX+'confirmed' and row['skill_id']==skill_id and row['version']==version and row['spec_sha256']==checksum for row in records))


def create_proposal(store,body):
    if not isinstance(body,dict):raise ValueError('Skill proposal must be an object')
    request=_text(body.get('request_id') or str(uuid.uuid4()),'request id',100);intent=dict(text=body.get('text',''),template_id=body.get('template_id'))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');previous=next((row for row in _records(db) if row['type']==EVENT_PREFIX+'spec' and row.get('request_id')==request),None)
        if previous:
            if previous['intent']!=intent:raise ValueError('Proposal request identity changed')
            return _selected(db,previous['spec']['skill_id'],1)
        spec=propose_skill(**intent);_append(db,spec['owner_experiment'],'spec',dict(spec=spec,intent=intent,request_id=request),EVENT_PREFIX+'spec:'+spec['skill_id']+':1')
        return _selected(db,spec['skill_id'],1)


def list_proposals(store):
    with store.connect() as db:
        keys=sorted({(row['spec']['skill_id'],row['spec']['version']) for row in _records(db) if row['type']==EVENT_PREFIX+'spec'})
        return [_selected(db,*key) for key in keys]


def revise_proposal(store,skill_id,version,patch,spec_sha256):
    allowed={'title','template_id','object','source','goal','recipient','phase_ids','success','subskill_references'}
    if not isinstance(patch,dict) or set(patch)-allowed:raise ValueError('Edit task fields, never ownership, action contract or physical authorization')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');chosen=_selected(db,skill_id)
        if chosen['spec']['version']!=version or chosen['spec_sha256']!=spec_sha256:raise ValueError('Skill changed; refresh before revision')
        spec=deepcopy(chosen['spec']);spec.update(deepcopy(patch));spec['version']+=1
        spec['title']=_text(spec['title'],'title',100)
        for key in ('object','source','goal','recipient'):
            if not isinstance(spec[key],dict):raise ValueError('Named query field required: '+key)
            spec[key]['query']=_text(spec[key].get('query'),key,240)
        if owner_identity(spec)==SO101_PROJECT:
            if spec.get('template_id') not in TEMPLATES:raise ValueError('Select a known SO101 source task template')
            spec['recognized_type']=TEMPLATES[spec['template_id']]['task_type']
            if 'template_id' in patch and 'phase_ids' not in patch:spec['phase_ids']=list(TEMPLATES[spec['template_id']]['phases'])
            if not isinstance(spec.get('phase_ids'),list) or not spec['phase_ids'] or any(not isinstance(phase,str) for phase in spec['phase_ids']) or len(set(spec['phase_ids']))!=len(spec['phase_ids']) or set(spec['phase_ids'])-SOURCE_PHASES:raise ValueError('Explicit distinct SO101 source phase IDs required')
            if not set(TEMPLATES[spec['template_id']]['phases']).issubset(spec['phase_ids']):raise ValueError('Keep the full-task template phases; partial demonstrations use episode scopes')
        if not isinstance(spec.get('success'),dict) or not isinstance(spec['success'].get('description'),str):raise ValueError('Explicit full-task success required')
        spec=_derive(spec);_append(db,spec['owner_experiment'],'spec',dict(spec=spec,parent_spec_sha256=chosen['spec_sha256']),EVENT_PREFIX+'spec:'+skill_id+':'+str(spec['version']))
        return _selected(db,skill_id,spec['version'])


def confirm_proposal(store,skill_id,version,spec_sha256,*,known_specs=()):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');chosen=_selected(db,skill_id,version);spec=chosen['spec']
        if chosen['spec_sha256']!=spec_sha256:raise ValueError('Skill definition changed')
        if owner_identity(spec) not in ('02','05',SO101_PROJECT) or owner_identity(spec)==SO101_PROJECT and not is_source_spec(spec):raise ValueError('Source role/contract is not verified for this catalogue')
        blockers=[row['id'] for row in spec['missing_facts'] if row['required_for']=='confirmation' and row['id']!='versioned_subskill_references']
        blockers.extend(validate_subskill_references(spec,known_specs))
        if blockers:raise ValueError('Resolve editable proposal before confirmation: '+', '.join(blockers))
        _append(db,owner_identity(spec),'confirmed',dict(skill_id=skill_id,version=version,spec_sha256=spec_sha256),EVENT_PREFIX+'confirmed:'+skill_id+':'+str(version))
        return _selected(db,skill_id,version)


def readiness(spec,*,confirmed=False,receipts=None,receipt_verifier=None):
    """Only server-trusted adapters may verify receipts; input booleans are no proof."""
    receipts=receipts or {};stages={};previous=True
    for kind in ('demos','trained','shadow','physical'):
        receipt=receipts.get(kind);reasons=[]
        if not isinstance(receipt,dict):reasons.append('receipt_missing');receipt={}
        owner=owner_identity(spec);contract=(spec.get('action_contract') or {}).get('id')
        try:receipt_owner=owner_identity(receipt)
        except (ValueError,TypeError):receipt_owner=None
        bound=receipt.get('skill_id')==spec['skill_id'] and receipt.get('skill_version')==spec['version'] and receipt_owner==owner and receipt.get('action_contract_id')==contract
        verified=bound and callable(receipt_verifier) and receipt_verifier(kind,deepcopy(receipt),deepcopy(spec)) is True
        if not verified:reasons.append('source_bound_receipt_not_verified_by_server_adapter')
        if owner!=SO101_PROJECT:reasons.append('use_owner_specific_02_workflow_or_05_coordinator_not_SO101_training')
        if kind=='demos':
            if not _hash(receipt.get('aggregate_manifest_sha256')) or receipt.get('full_teaching_provenance_ready') is not True or receipt.get('object_expert_eligible') is not True or receipt.get('global_split_groups_verified') is not True:reasons.append('complete_source_bound_aggregate_and_expert_split_evidence_required')
        if kind=='trained':
            if receipt.get('trained') is not True or receipt.get('framework')!='LeRobot' or not str(receipt.get('framework_version','')).startswith('0.6.') or not _hash(receipt.get('checkpoint_sha256')):reasons.append('actual_official_LeRobot_0_6_checkpoint_receipt_required')
            if receipt.get('aggregate_manifest_sha256')!=(receipts.get('demos') or {}).get('aggregate_manifest_sha256'):reasons.append('model_dataset_lineage_mismatch')
        if kind in ('shadow','physical'):
            if receipt.get('checkpoint_sha256')!=(receipts.get('trained') or {}).get('checkpoint_sha256') or not _hash(receipt.get('checkpoint_sha256')):reasons.append('checkpoint_lineage_mismatch')
            if receipt.get('execution_kind')!='fresh_observation_learned_policy_rollout':reasons.append('raw_episode_replay_or_open_loop_is_not_policy_rollout')
        if kind=='shadow' and (receipt.get('shadow') is not True or receipt.get('hardware_commands_dispatched') is not False):reasons.append('actual_nonmoving_shadow_receipt_required')
        if kind=='physical':
            if receipt.get('source_kind')!='physical' or receipt.get('separate_user_observed_test') is not True or receipt.get('independent_full_task_success') is not True:reasons.append('separate_observed_physical_full_task_test_required')
            if receipt.get('coordinate_contract_verified') is not True or receipt.get('native_executor_qualified') is not True or receipt.get('existing_controller_settings_preserved') is not True:reasons.append('qualified_native_coordinates_and_unchanged_controller_evidence_required')
        if not confirmed:reasons.append('spec_not_confirmed')
        if not previous:reasons.append('preceding_readiness_stage_not_verified')
        ready=not reasons;stages[kind]=dict(ready=ready,evidence_status='VERIFIED_RECEIPT' if verified else 'DECLARED_UNVERIFIED' if receipt else 'MISSING',reasons=reasons,
            source_kind=receipt.get('source_kind'),receipt_id=receipt.get('receipt_id'))
        previous=ready
    return dict(schema='explorer_skill_readiness_v1',skill_id=spec['skill_id'],skill_version=spec['version'],owner_experiment=owner_identity(spec),
        original_owner_experiment=spec['owner_experiment'],experiment_numbering_version=2,
        template=dict(ready=confirmed,status='CONFIRMED' if confirmed else 'PROPOSED'),**stages,
        physical_execution_authorized=False,sim_evidence_is_not_physical_evidence=True,text_is_ACT_input=False)


def demonstration_plan(spec,episode_reports=(),*,receipt_verifier=None):
    """Adaptive suggestions use verified outcomes, never convert failures to labels."""
    if not is_source_spec(spec):raise ValueError('Use owner02 workflow or owner05 coordinator; never merge child demonstration contracts')
    variants=spec['demo_plan']['variants'];phases=[row['id'] for row in spec['stages']];counts={row['variant_id']:0 for row in variants};matrix={phase:{row['variant_id']:0 for row in variants} for phase in phases}
    attempts={key:0 for key in counts};rejected=[];identities=set();sealed_seen=[]
    for report in episode_reports:
        identity=report.get('original_episode_id');variant=report.get('variant_id')
        if not identity or identity in identities:rejected.append(dict(original_episode_id=identity,reason='missing_or_duplicate_original_episode'));identities.add(identity)
        else:
            identities.add(identity)
            if variant not in counts:rejected.append(dict(original_episode_id=identity,reason='unknown_variant'))
            else:
                attempts[variant]+=1;planned=next(row for row in variants if row['variant_id']==variant)
                if planned['split']=='sealed_test':sealed_seen.append(identity)
                verified=callable(receipt_verifier) and receipt_verifier('episode',deepcopy(report),deepcopy(spec)) is True
                eligible=verified and report.get('skill_id')==spec['skill_id'] and report.get('skill_version')==spec['version'] and report.get('source_quality_usable') is True and report.get('operator_success') is True and report.get('split')==planned['split']
                outcomes=report.get('phase_outcomes') or {};targets=report.get('target_phase_ids') or phases
                good=eligible and bool(targets) and set(targets)<=set(phases) and all(outcomes.get(phase)=='success' for phase in targets)
                if not good:rejected.append(dict(original_episode_id=identity,reason='source_grade_or_successful_phase_evidence_not_verified',phase_outcomes=outcomes))
                else:
                    for phase in targets:matrix[phase][variant]+=1
                    if report.get('episode_scope')=='whole_episode' and set(targets)==set(phases) and report.get('independent_full_task_success') is True:counts[variant]+=1
    missing=next((row for row in variants if not counts[row['variant_id']]),None)
    if missing:next_action=dict(kind='repeat_variant' if attempts[missing['variant_id']] else 'record_variant',variant_id=missing['variant_id'],split=missing['split'],episode_scope='whole_episode',target_phase_ids=phases,
        show_number=sum(bool(value) for value in counts.values())+1,initial_technical_target=len(variants),repeat_number=attempts[missing['variant_id']]+1,
        reason='Нет принятого полного исходного эпизода для этих условий; failed/unknown остаются разбором, targeted фазы не заменяют whole episode')
    else:next_action=dict(kind='independent_model_evaluation',reason='Технический начальный охват собран. Нужны независимые learned-policy full-task попытки; пять показов не подтверждают владение навыком')
    return dict(schema='explorer_skill_demo_plan_v1',skill_id=spec['skill_id'],skill_version=spec['version'],covered_variants=sum(bool(value) for value in counts.values()),planned_variants=len(variants),
        whole_episode_counts=counts,phase_variant_coverage=matrix,rejected=rejected,next_action=next_action,sealed_episode_ids=sealed_seen,
        expert_targets_generated_from_failures=False,skill_mastery_claimed=False,physical_execution_authorized=False)
