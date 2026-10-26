"""ENGINEERING_GLUE: versioned unified skills and immutable multi-demo evidence sets.

Russian text becomes an editable PROPOSED specification, never certain NLU.
Operator-declared conditions and full-task reviews stay distinct from measured
robot evidence. This module has no hardware, capture, controller or policy IO.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

import numpy as np
from explorer_shared.contracts import ACTION_NAMES, ACTION_UNITS, canonical, digest, event

SPLITS = ('train','development','sealed_test')
MANIPULATION_STAGES = ('find_object','localize_object','approach_align','coordinated_reach','grasp_confirm','find_goal','retained_transport','align_release','verify_placement','retreat')
NAVIGATION_STAGES = ('reference','navigate','stop','verify_goal')
MOBILE_STAGES = MANIPULATION_STAGES
UNIFIED_ACTION_CONTRACT='rosmaster_joint9_position6_velocity3_v1'
OBSERVATION_CHANNELS=('camera_rgb','camera_depth','camera_calibration','lidar','odometry_pose','base_velocity','imu','joint_command','joint_feedback','gripper','clocks','detections','goal','power_context')
STAGE_TITLES = dict(approach='Подвести руку к предмету',grasp='Захватить предмет',lift='Поднять над опорой',
    transport='Перенести к цели',place='Поместить в цель',release='Отпустить',settle='Убрать руку и проверить устойчивый итог',
    reference='Зафиксировать начало и цель',navigate='Доехать до цели',stop='Остановиться',verify_goal='Проверить положение',
    approach_base='Подъехать к предмету',transport_base='Перевезти предмет к цели',
    find_object='Найти предмет',localize_object='Локализовать предмет',approach_align='Подойти и выровнять весь робот',
    coordinated_reach='Согласованно подвести захват',grasp_confirm='Захватить и подтвердить удержание',find_goal='Найти и локализовать корзину / цель',
    retained_transport='Перенести / перевезти с сохранением удержания',align_release='Выровнять робот и захват, затем отпустить',
    verify_placement='Проверить полное размещение',retreat='Убрать захват и отойти без потери результата')
PRESETS = {
    'sock_to_basket': dict(title='Носок → корзина',task_type='mobile_manipulation',object='Носок',goal='Корзина'),
    'object_a_to_b': dict(title='Предмет A → B',task_type='mobile_manipulation',object='Предмет в точке A',goal='Точка B'),
    'navigate': dict(title='Навигация к точке',task_type='navigation',object='Робот',goal='Целевая точка'),
    'approach_grasp_transport_place': dict(title='Подъехать → захватить → перевезти → поставить',task_type='mobile_manipulation',object='Предмет',goal='Место размещения'),
}
EVENT_PREFIX = 'skill.workflow.'


class WorkflowBlocked(ValueError):
    def __init__(self,reasons):
        self.reasons = list(reasons)
        super().__init__('; '.join(self.reasons))


def _uuid(value):
    try:return str(uuid.UUID(str(value)))
    except (ValueError,TypeError,AttributeError) as error:raise ValueError('Explicit skill/session UUID required') from error


def _text(value,label,maximum=240,*,empty=False):
    if not isinstance(value,str) or len(value.strip()) > maximum or (not empty and not value.strip()):
        raise ValueError('Explicit short text required: '+label)
    return value.strip()


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _records(db,kind=None):
    query = "SELECT event_id,body FROM events WHERE node_id='nx' AND json_extract(body,'$.payload.type') LIKE 'skill.workflow.%' ORDER BY received,event_id"
    rows = []
    for row in db.execute(query):
        envelope = json.loads(row['body'])
        payload = envelope['payload']
        if digest(payload) != envelope['sha256']:
            raise ValueError('Skill workflow event checksum mismatch')
        if kind is None or payload['type'] == EVENT_PREFIX+kind:
            rows.append(dict(event_id=row['event_id'],sha256=envelope['sha256'],payload=payload))
    return rows


def _append(db,kind,body,*,identity=None):
    payload = dict(body,type=EVENT_PREFIX+kind)
    envelope = event('nx','02',payload)
    envelope['event_id'] = identity or EVENT_PREFIX+kind+':'+digest(payload)
    serialized = canonical(envelope).decode()
    previous = db.execute('SELECT body FROM events WHERE event_id=?',(envelope['event_id'],)).fetchone()
    if previous:
        saved = json.loads(previous['body'])
        if saved['payload'] != payload:raise ValueError('Conflicting immutable workflow event identity')
        return dict(event_id=saved['event_id'],sha256=saved['sha256'],payload=saved['payload'])
    db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(envelope['event_id'],'nx',time.time(),envelope['sha256'],serialized))
    return dict(event_id=envelope['event_id'],sha256=envelope['sha256'],payload=payload)


def _variants(task_type):
    # Five technical smoke variants, not a promise of mastery after five demos.
    descriptions = [
        ('train','Исходное положение','Текущая проверенная поза руки','Предмет по центру','Цель по центру'),
        ('train','Другой подход','Другая проверенная начальная поза','Предмет слева','Та же видимая цель'),
        ('train','Другое расположение','Третья проверенная начальная поза','Предмет справа / другие складки','Цель немного смещена в проверенной области'),
        ('development','Отдельная проверочная сессия','Новая начальная поза','Новое положение предмета','Новая видимая позиция цели'),
        ('sealed_test','Закрытая сессия — не обучать','Другая сессия и сцена','Отложенное положение предмета','Отложенное положение цели'),
    ]
    result = []
    for index,(split,title,robot,obj,goal) in enumerate(descriptions):
        result.append(dict(variant_id='variant-'+str(index+1).zfill(3),split=split,title=title,
            initial_robot=robot,initial_object=obj,goal_position=goal,scene='Новая явно указанная сцена' if split!='train' else 'Учебная сцена',
            target_accepted=1,condition_basis='operator_declared_not_automatically_measured'))
    return result


def _derive(spec):
    spec = json.loads(json.dumps(spec))
    task = spec['task_type']
    if task not in ('navigation','mobile_manipulation','unknown'):
        raise ValueError('Unknown proposed task type')
    if spec.get('resources') != ['arm','gripper','base']:
        raise ValueError('Every SkillSpec is one unified ROSMASTER mobile manipulator; separate resource modes are unsupported')
    spec['embodiment']='rosmaster_unified_mobile_manipulator'
    stages = NAVIGATION_STAGES if task=='navigation' else MANIPULATION_STAGES if task=='mobile_manipulation' else ()
    spec['stages'] = [dict(id=name,title=STAGE_TITLES[name]) for name in stages]
    if not isinstance(spec['cameras'],list) or not spec['cameras'] or any(not isinstance(name,str) or not name or len(name)>80 for name in spec['cameras']) or len(set(spec['cameras'])) != len(spec['cameras']):
        raise ValueError('Explicit distinct recording cameras required')
    if spec['training_camera'] not in spec['cameras']:
        raise ValueError('The selected RGB source must be one declared recording camera')
    spec['constraints'] = dict(chassis_motion_forbidden=True,original_controller_settings='PRESERVE',physical_skill_status='NOT_VALIDATED',
        recording_authority='existing_native_gateway_and_current_owner_constraints_only')
    spec['action_profile'] = dict(schema=UNIFIED_ACTION_CONTRACT,simultaneous_9d=True,neutral_channels_retained=True,names=list(ACTION_NAMES),units=list(ACTION_UNITS),dimension=9,
        label_semantics='preserve_source_contract; host target intent is not MCU delivery')
    spec['state_profile'] = dict(schema='rosmaster_embodied_multisensor_state_v1',observation_channels=list(OBSERVATION_CHANNELS),
        preceding_command9=dict(optional=True,basis='explicit_original_source_only',manufactured_from_joints_and_odometry=False),
        availability='source_recorded_per_measurement_masks; unknowns_remain_unknown',neutral_command_channels='retained_not_masked')
    missing = []
    for name in ('object','goal'):
        if spec[name]['name'] in ('UNKNOWN','Не определено',''):
            missing.append(dict(id=name+'_definition',required_for='training',reason='Уточните '+('предмет' if name=='object' else 'цель')))
    if task=='unknown':missing.append(dict(id='task_type',required_for='training',reason='Выберите тип задачи и исполнитель'))
    missing += [dict(id='object_geometry',required_for='physical_validation',reason='Размеры, материал и захват предмета не измерены'),
        dict(id='workspace_and_camera_registration',required_for='physical_validation',reason='Рабочая область и геометрия камер требуют фактической проверки')]
    spec['observation_contract']=dict(schema='explorer_embodied_episode_v1',channels=list(OBSERVATION_CHANNELS),
        masks='only_actual_unavailable_or_invalid_measurements_never_phase_nonparticipation',
        sensor_identity_calibration_required=True,power_role='recorded_context_not_a_new_motor_or_charging_setting')
    try:
        from explorer_shared.embodied_episode import hierarchy_plan,sensor_contract
        hierarchy=hierarchy_plan(spec)
        spec['observation_contract']['canonical_sensor_contract']=sensor_contract()
    except ImportError:
        hierarchy=dict(schema='explorer_embodied_hierarchy_plan_v1',action_contract_id=UNIFIED_ACTION_CONTRACT,
            simultaneous_9d=True,full_hierarchy_status='BLOCKED',missing_channels=list(OBSERVATION_CHANNELS),
            stages=[],scope='internal_components_of_one_mobile_manipulator_not_separate_tasks')
    spec['pipeline']=dict(pipeline_class='unified_rosmaster_perception_goal_navigation_contact_ACT_BC_coordinator_recovery',
        implemented=False,job_kind=None,hierarchy_plan=hierarchy,full_task_required=True,
        observations='canonical_RGB_depth_calibration_lidar_odom_pose_velocity_IMU_joints_masks_gripper_clocks_detection_goal_power_context',
        action_contract_id=UNIFIED_ACTION_CONTRACT,arbitrary_Russian_instruction_conditioning=False,
        internal_ACT_BC_component='legacy_RGB_state9_reader_exists_but_is_not_a_full_embodied_skill_pipeline')
    missing += [dict(id='canonical_multisensor_episode_admission',required_for='full_pipeline',reason='Нужны фактические RGB/depth и калибровки, LiDAR, одометрия/поза/скорость, IMU, суставы/маски, захват, часы, обнаружения и цель'),
        dict(id='perception_goal_tracking_backend',required_for='training',reason='Нужен проверенный perception/goal tracking в общей иерархии'),
        dict(id='navigation_training_backend',required_for='training',reason='Нужен настоящий odometry/trajectory navigation baseline; RGB+9D ACT не заменяет его'),
        dict(id='hierarchy_coordinator_recovery_backend',required_for='training',reason='Полный hierarchy/coordinator/recovery ещё не реализован как обучающий backend'),
        dict(id='owner_chassis_prohibition',required_for='recording',reason='Шасси перемещать запрещено; только неподвижная сцена / допустимые targeted показы с тем же полным 9D-контрактом')]
    spec['missing_facts'] = missing
    spec['success']['verification_basis'] = 'operator_review_of_complete_task_not_automatic_contact_measurement'
    spec['coverage_plan'] = dict(variants=spec.get('coverage_plan',{}).get('variants') or _variants(task),
        minimum_unique_train_variants=3,minimum_unique_development_variants=1,minimum_unique_sealed_variants=1,
        initial_smoke_scope='technical_dataset_and_pipeline_check_not_mastery',physical_independence_claimed=False)
    ids = set()
    counts = {split:0 for split in SPLITS}
    for variant in spec['coverage_plan']['variants']:
        if not isinstance(variant,dict) or variant.get('split') not in SPLITS or not re.fullmatch('[A-Za-z0-9_.-]{1,80}',str(variant.get('variant_id',''))) or variant['variant_id'] in ids:
            raise ValueError('Distinct named coverage variants and explicit splits required')
        ids.add(variant['variant_id']);counts[variant['split']] += 1
        for name in ('title','initial_robot','initial_object','goal_position','scene'):
            _text(variant.get(name),name)
        if variant.get('target_accepted',1) != 1:raise ValueError('Initial variant targets count original full episodes, not frames')
    if counts['train']<3 or counts['development']<1 or counts['sealed_test']<1:
        raise ValueError('Keep at least three train, one development and one sealed planned variants')
    return spec


def proposal(text='',preset=None):
    """Deterministic suggestion only; the human edits and confirms this spec."""
    source_text = _text(text,'описание',600,empty=bool(preset))
    if preset is not None and preset not in PRESETS:raise ValueError('Unknown skill preset')
    base = dict(PRESETS[preset]) if preset else dict(title=source_text[:100],task_type='unknown',object='UNKNOWN',goal='UNKNOWN')
    basis = 'deterministic_preset' if preset else 'PROPOSED_custom_text_not_certain_NLU'
    if not preset:
        match = re.fullmatch(r'\s*(?:положить|положи|переложить|переложи|перенести|перенеси)\s+(.+?)\s+(?:в|на)\s+(.+?)\s*[.!]?\s*',source_text,re.I)
        if match:
            base.update(task_type='mobile_manipulation',object=match.group(1).strip(),goal=match.group(2).strip(' .!'))
            basis = 'PROPOSED_literal_Russian_pattern_extraction_not_certain_NLU'
    resources = ['arm','gripper','base']
    return _derive(dict(schema='explorer_skill_spec_v1',skill_id=str(uuid.uuid4()),version=1,status='PROPOSED',
        title=_text(base['title'],'название',100),source_text=source_text,proposal_basis=basis,preset_id=preset,
        task_type=base['task_type'],object=dict(name=base['object'],geometry='UNKNOWN'),goal=dict(name=base['goal'],frame='UNKNOWN',coordinates='UNKNOWN'),
        resources=resources,cameras=['nano-rgbd'],training_camera='nano-rgbd',
        success=dict(description='Вся выбранная задача завершена, предмет/робот устойчиво находится в цели',full_task=True)))


def _get_spec(db,skill_id,version=None):
    skill_id = _uuid(skill_id)
    if version is not None and (type(version) is not int or version < 1):raise ValueError('Positive integer skill version required')
    found = [record for record in _records(db,'spec') if record['payload']['spec']['skill_id']==skill_id]
    if version is None:version = max((record['payload']['spec']['version'] for record in found),default=0)
    selected = next((record for record in found if record['payload']['spec']['version']==version),None)
    if selected is None:raise ValueError('Unknown skill version')
    spec = selected['payload']['spec']
    confirmed = any(record['payload']['skill_id']==skill_id and record['payload']['version']==version and record['payload']['spec_sha256']==digest(spec) for record in _records(db,'confirmed'))
    return dict(spec=spec,spec_sha256=digest(spec),confirmed=confirmed)


def create_skill(store,body):
    if not isinstance(body,dict):raise ValueError('Skill proposal must be an object')
    request_id = _text(body.get('request_id') or str(uuid.uuid4()),'request_id',100)
    intent = dict(text=body.get('text',''),preset=body.get('preset'))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = next((row for row in _records(db,'spec') if row['payload'].get('request_id')==request_id),None)
        if previous:
            if previous['payload']['intent'] != intent:raise ValueError('Proposal request identity changed')
            return _get_spec(db,previous['payload']['spec']['skill_id'],1)
        spec = proposal(intent['text'],intent['preset'])
        _append(db,'spec',dict(spec=spec,request_id=request_id,intent=intent))
        return _get_spec(db,spec['skill_id'],1)


def revise_skill(store,skill_id,version,patch,expected_sha256):
    permitted = {'title','object','goal','task_type','cameras','training_camera','success','coverage_plan'}
    if not isinstance(patch,dict) or set(patch)-permitted:raise ValueError('Edit only task fields; action/controller/owner constraints stay fixed')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = _get_spec(db,skill_id)
        if previous['spec']['version'] != version or previous['spec_sha256'] != expected_sha256:raise ValueError('Skill changed; refresh before creating a new version')
        spec = json.loads(json.dumps(previous['spec']));spec.update(patch);spec['version'] += 1;spec['status']='PROPOSED'
        for key in ('title',):spec[key]=_text(spec[key],key,100)
        for key in ('object','goal'):
            if not isinstance(spec[key],dict):raise ValueError('Named object/goal fields required')
            spec[key]['name']=_text(spec[key].get('name'),key,100)
        if not isinstance(spec.get('success'),dict) or spec['success'].get('full_task') is not True:raise ValueError('A complete task outcome is required')
        spec=_derive(spec)
        _append(db,'spec',dict(spec=spec,parent_spec_sha256=previous['spec_sha256']))
        return _get_spec(db,skill_id,spec['version'])


def confirm_skill(store,skill_id,version,spec_sha256):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        selected=_get_spec(db,skill_id,version)
        if selected['spec_sha256']!=spec_sha256:raise ValueError('Skill definition changed')
        if selected['spec']['task_type']=='unknown' or selected['spec']['object']['name']=='UNKNOWN' or selected['spec']['goal']['name']=='UNKNOWN':
            raise ValueError('Resolve the proposed task, object and goal before confirming')
        _append(db,'confirmed',dict(skill_id=_uuid(skill_id),version=version,spec_sha256=spec_sha256))
        return _get_spec(db,skill_id,version)


def list_skills(store):
    with store.connect() as db:
        keys=sorted({(row['payload']['spec']['skill_id'],row['payload']['spec']['version']) for row in _records(db,'spec')})
        return [_get_spec(db,identity,version) for identity,version in keys]


def create_session(store,skill_id,version,body):
    if not isinstance(body,dict) or body.get('split') not in SPLITS:raise ValueError('Choose one explicit teaching-session split')
    session_id = _uuid(body.get('session_id') or str(uuid.uuid4()))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');selected=_get_spec(db,skill_id,version)
        if not selected['confirmed']:raise ValueError('Confirm the proposed skill version first')
        scene=_text(body.get('scene'),'сцена')
        if any(row['payload']['scene']==scene and row['payload']['split']!=body['split'] for row in _records(db,'session')):raise ValueError('A declared physical scene cannot cross dataset splits')
        payload=dict(session_id=session_id,skill_id=_uuid(skill_id),version=version,split=body['split'],
            scene=scene,description=_text(body.get('description','Отдельная сессия показов'),'сессия'),
            historical=body.get('historical') is True,basis='operator_declared_teaching_session_not_measured_physical_independence')
        record=_append(db,'session',payload,identity=EVENT_PREFIX+'session:'+session_id)
        return dict(payload,session_sha256=record['sha256'])


def _session(db,session_id):
    session_id=_uuid(session_id)
    row=next((item for item in _records(db,'session') if item['payload']['session_id']==session_id),None)
    if row is None:raise ValueError('Unknown teaching session; create an explicit session before binding')
    return row['payload']


def _variant(spec,variant_id):
    item=next((item for item in spec['coverage_plan']['variants'] if item['variant_id']==variant_id),None)
    if item is None:raise ValueError('Unknown skill coverage variant')
    return item


def _inspector(job,root,inspector=None):
    if inspector is None:
        from .mobile_dataset_evidence import inspect_mobile_import
        inspector=inspect_mobile_import
    return inspector(job,managed_root=root,include_manifest=True)


def _binding_map(db):
    latest={}
    for row in _records(db,'binding'):
        latest[row['payload']['original_episode_id']]=dict({key:value for key,value in row['payload'].items() if key!='type'},binding_sha256=row['sha256'])
    return latest


def _split_groups(report):
    source=(report.get('mobile_embodied') or {}).get('split') or {}
    return {name:source[name] for name in ('scene_id','session_id','object_instance_id','group_id') if source.get(name)}


def _binding_groups(binding):
    groups=[('teaching_session',binding['session_id']),('teaching_scene',binding['scene'])]
    groups += [('source_'+name,value) for name,value in binding.get('source_split_groups',{}).items()]
    if binding.get('source_session_id'):groups.append(('source_session_id',binding['source_session_id']))
    if binding.get('source_scene_id'):groups.append(('source_scene_id',binding['source_scene_id']))
    return groups


def bind_episode(store,skill_id,version,variant_id,body,*,inspector=None):
    if not isinstance(body,dict):raise ValueError('Episode assignment must be an object')
    job=store.job(body.get('import_job_id',''))
    if not job or job['kind']!='mobile_import' or job['project_id']!='02' or job['status']!='completed':raise ValueError('Choose a completed original Exp02 mobile import')
    report=_inspector(job,store.root,inspector)
    original=report.get('original_episode_id') or (job.get('result') or {}).get('episode_id') or job['config'].get('episode_id')
    original=_text(original,'original episode',160)
    fingerprint=_source_fingerprint(job,report,original)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');selected=_get_spec(db,skill_id,version);spec=selected['spec'];variant=_variant(spec,variant_id)
        session=_session(db,body.get('session_id'))
        if session['skill_id']!=spec['skill_id'] or session['version']!=version or session['split']!=variant['split']:raise ValueError('A teaching session belongs to one skill/version/split')
        episode_scope=body.get('episode_scope','whole_episode')
        if episode_scope not in ('whole_episode','targeted_subskill','recovery'):raise ValueError('Unknown episode scope; robot contract always remains unified 9D')
        phases=body.get('completed_stages',[])
        expected=[item['id'] for item in spec['stages']]
        if not isinstance(phases,list) or any(not isinstance(item,str) for item in phases) or len(set(phases))!=len(phases) or set(phases)-set(expected):raise ValueError('Review only distinct declared full-task stages')
        targets=body.get('target_phase_ids',expected)
        if not isinstance(targets,list) or not targets or any(not isinstance(item,str) for item in targets) or len(set(targets))!=len(targets) or set(targets)-set(expected):raise ValueError('Targeted/recovery phases must belong to the common full SkillSpec')
        if episode_scope=='whole_episode' and set(targets)!=set(expected):raise ValueError('Whole episodes must retain every full-task target phase')
        outcomes=body.get('phase_outcomes',{name:'success' if name in phases else 'unknown' for name in expected})
        if not isinstance(outcomes,dict) or set(outcomes)-set(expected) or any(value not in ('success','failure','unknown') for value in outcomes.values()):raise ValueError('Explicit per-phase success/failure/unknown outcomes required')
        payload=dict(original_episode_id=original,import_job_id=job['id'],skill_id=spec['skill_id'],version=version,spec_sha256=selected['spec_sha256'],
            variant_id=variant_id,split=variant['split'],session_id=session['session_id'],source_session_id=report.get('operator_session_id') or report.get('original_session_id'),source_scene_id=report.get('original_scene_id') or report.get('scene_id'),source_split_groups=_split_groups(report),
            source_fingerprint=fingerprint,manifest_sha256=report.get('manifest_sha256'),
            completed_stages=[name for name in expected if name in phases],full_task_confirmed=body.get('full_task_confirmed') is True,
            object_and_goal_visible=body.get('object_and_goal_visible') is True,training_camera=_text(body.get('training_camera',spec['training_camera']),'camera',80),
            initial_robot=_text(body.get('initial_robot',variant['initial_robot']),'начальная поза'),initial_object=_text(body.get('initial_object',variant['initial_object']),'предмет'),
            goal_position=_text(body.get('goal_position',variant['goal_position']),'цель'),scene=session['scene'],
            review_basis='operator_review_not_sensor_success_verification',source_quality_override_allowed=False,
            source_contract_status='CANONICAL_EMBODIED_VERIFIED' if (report.get('mobile_embodied') or {}).get('admitted_for_training') is True else 'ANALYSIS_ONLY_LEGACY_OR_INCOMPLETE',
            episode_scope=episode_scope,target_phase_ids=targets,phase_outcomes=outcomes,
            phase_outcome_basis='operator_attestation_not_automatic_sensor_success',recovery_context=body.get('recovery_context'))
        previous=_binding_map(db).get(original)
        comparable={key:value for key,value in (previous or {}).items() if key not in ('binding_sha256','supersedes_sha256','type')}
        if comparable==payload:return previous
        if body.get('expected_binding_sha256') != (previous or {}).get('binding_sha256'):raise ValueError('Episode assignment changed; refresh before moving or correcting it')
        history=[row['payload'] for row in _records(db,'binding') if row['payload']['original_episode_id']==original]
        source_partition=((report.get('mobile_embodied') or {}).get('split') or {}).get('partition')
        if variant['split']!='sealed_test' and (source_partition=='sealed_test' or any(row['split']=='sealed_test' for row in history) or any(item.get('split') in ('sealed_test','test') for item in report.get('episodes',[]))):
            raise ValueError('An original sealed episode cannot be reclassified into training/development')
        reserved={episode for row in _records(db,'training') for episode in row['payload'].get('training_original_episode_ids',[])}
        if original in reserved and variant['split']!='train':raise ValueError('A source already reserved for fitting cannot become an independent heldout episode')
        if payload['training_camera']!=spec['training_camera']:raise ValueError('Assignment camera differs from the selected skill input')
        source_splits={item.get('split','train') for item in report.get('episodes',[])}
        if (source_partition=='development' or source_splits & {'development','validation','dev'}) and variant['split']!='development':raise ValueError('An original development source stays development; it cannot become BC or a pristine sealed test')
        for other in _binding_map(db).values():
            if payload['source_session_id'] and other['original_episode_id']!=original and other['source_session_id']==payload['source_session_id'] and other['split']!=payload['split']:
                raise ValueError('An original recording session cannot cross dataset splits')
        reserved |= _previous_fitting_ids(store)
        if original in reserved and variant['split']!='train':raise ValueError('A source already fitted/reserved for training cannot become heldout')
        for other in _binding_map(db).values():
            if payload['source_scene_id'] and other['original_episode_id']!=original and other.get('source_scene_id')==payload['source_scene_id'] and other['split']!=payload['split']:raise ValueError('An original source scene cannot cross dataset splits')
            if other['original_episode_id']!=original and other['split']!=payload['split'] and set(_binding_groups(other)) & set(_binding_groups(payload)):
                raise ValueError('An original scene/session/object split group cannot cross dataset splits')
        payload['supersedes_sha256']=(previous or {}).get('binding_sha256')
        row=_append(db,'binding',payload)
        return dict(payload,binding_sha256=row['sha256'])


def _source_fingerprint(job,report,original):
    return digest(dict(job_id=job['id'],original_episode_id=original,manifest_sha256=report.get('manifest_sha256'),
        source_archive_sha256=(job.get('result') or {}).get('source_archive_sha256'),episode_sha256=[item.get('sha256') for item in report.get('episodes',[])],
        embodied_report_sha256=digest(report.get('mobile_embodied') or {}),embodied_source_files=report.get('embodied_source_files') or {item.get('relative_name'):item.get('sha256') for item in report.get('source_artifacts',[])},
        legacy_RGB9D_view_sha256=(report.get('legacy_RGB9D_view') or {}).get('manifest_sha256')))


def _local(root,value):
    if not isinstance(value,str) or not value:raise ValueError('Missing managed source artifact')
    path=Path(value).expanduser().resolve()
    if not path.is_file() or not path.is_relative_to(Path(root).resolve()):raise ValueError('Source artifact is unavailable in managed storage')
    return path


def _admission(store,spec,binding,inspector=None):
    job=store.job(binding['import_job_id'])
    if not job or job['kind']!='mobile_import' or job['status']!='completed':return dict(binding=binding,accepted=False,reasons=['import_not_complete'])
    try:report=_inspector(job,store.root,inspector)
    except (OSError,ValueError,KeyError,TypeError) as error:return dict(binding=binding,accepted=False,reasons=['source_evidence_error:'+str(error)])
    if not isinstance(report,dict):return dict(binding=binding,accepted=False,reasons=['invalid_diagnostic_report'])
    embodied=report.get('mobile_embodied') or {}
    reasons=[]
    for name in ('completed_recording','operator_success','source_quality_usable'):
        if report.get(name) is not True:reasons.append(name+'_not_verified')
    if embodied.get('admitted_for_training') is not True:reasons.append('canonical_embodied_episode_not_admitted_legacy_kept_for_analysis')
    if embodied.get('action_contract_id')!=UNIFIED_ACTION_CONTRACT or embodied.get('simultaneous_9d') is not True or embodied.get('unified_action_names')!=list(ACTION_NAMES):
        reasons.append('unified_simultaneous_action9_contract_not_verified')
    if (embodied.get('split') or {}).get('partition')!=binding['split']:reasons.append('canonical_source_partition_differs_from_teaching_assignment')
    if binding['split']=='train' and embodied.get('optimizer_partition_allowed') is not True:reasons.append('canonical_source_not_optimizer_eligible')
    if _source_fingerprint(job,report,binding['original_episode_id'])!=binding['source_fingerprint']:reasons.append('bound_source_evidence_changed')
    envelope=embodied.get('envelope') or {}
    source_skill=envelope.get('skill') or {}
    if source_skill.get('id')!=spec['skill_id'] or source_skill.get('version')!=spec['version']:
        reasons.append('source_bound_skill_identity_differs_legacy_not_silently_upgraded')
    targets=binding.get('target_phase_ids') or [item['id'] for item in spec['stages']]
    phases=binding.get('phase_outcomes') or {}
    if not set(targets).issubset(binding['completed_stages']) or any(phases.get(name)!='success' for name in targets):reasons.append('target_phase_outcomes_not_successfully_reviewed')
    if binding.get('episode_scope','whole_episode')=='whole_episode' and (not binding['full_task_confirmed'] or set(binding['completed_stages'])!={item['id'] for item in spec['stages']}):
        reasons.append('full_task_phase_review_incomplete')
    source_phase_ids={phase.get('id') for phase in envelope.get('phases',[]) if isinstance(phase,dict)}
    source_outcomes=(envelope.get('outcomes') or {}).get('per_phase') or {}
    if not set(targets).issubset(source_phase_ids) or any(source_outcomes.get(name,{}).get('status')!='success' or source_outcomes.get(name,{}).get('independent_of_action_commands') is not True for name in targets):
        reasons.append('source_bound_target_phase_success_not_verified')
    if binding.get('episode_scope','whole_episode')=='whole_episode':
        end=(envelope.get('outcomes') or {}).get('end_to_end') or {}
        if embodied.get('episode_scope')!='whole_episode' or end.get('status')!='success' or end.get('independent_of_action_commands') is not True:
            reasons.append('source_bound_whole_task_success_not_verified')
        if binding['split']=='train' and embodied.get('eligible_as_successful_bc_expert') is not True:reasons.append('canonical_source_not_a_successful_whole_expert')
    if not binding['object_and_goal_visible']:reasons.append('object_or_goal_not_confirmed_visible')
    if binding['training_camera']!=spec['training_camera']:reasons.append('training_camera_differs')
    # The canonical source may have no preceding-command9 view at all. Never
    # manufacture one by concatenating joint6 and measured odometry velocity.
    legacy=report.get('legacy_RGB9D_view') or {}
    manifest=legacy.get('manifest') if isinstance(legacy.get('manifest'),dict) else None
    legacy_path=legacy.get('manifest_path');legacy_sha=legacy.get('manifest_sha256')
    # Older diagnostics can expose the converted view at top level, but a
    # canonical envelope is never a converted NPZ manifest.
    if manifest is None and isinstance(report.get('manifest'),dict) and report['manifest'].get('format')=='explorer_robot_episode_v1':
        manifest=report['manifest'];legacy_path=report.get('manifest_path');legacy_sha=report.get('manifest_sha256')
    if manifest is not None and manifest.get('format')!='explorer_robot_episode_v1':manifest=None
    artifacts=list(embodied.get('source_artifacts') or report.get('source_artifacts') or [])
    envelope_binding=embodied.get('envelope_binding') or {}
    if envelope_binding.get('path'):
        artifacts.append(dict(relative_name='__envelope__/original-document.json',path=envelope_binding['path'],sha256=envelope_binding.get('sha256')))
    if not artifacts and report.get('embodied_source_files'):
        artifacts=[dict(relative_name=Path(path).name,path=path,sha256=expected) for path,expected in report['embodied_source_files'].items()]
    retained=[];names=set()
    try:
        for artifact in artifacts:
            path=_local(store.root,artifact['path']);name=artifact['relative_name']
            if not isinstance(name,str) or not name or name in names or Path(name).is_absolute() or '..' in Path(name).parts or _sha(path)!=artifact['sha256']:raise ValueError('canonical source artifact path/hash mismatch')
            names.add(name)
            retained.append(dict(relative_name=name,path=str(path),sha256=artifact['sha256']))
        if not retained:raise ValueError('no retained canonical envelope/payload artifacts')
    except (OSError,ValueError,KeyError,TypeError) as error:reasons.append('embodied_source_artifact_invalid:'+str(error))
    return dict(binding=binding,accepted=not reasons,reasons=list(dict.fromkeys(reasons)),evidence=report,manifest=manifest,
        original_episode_id=binding['original_episode_id'],embodied_source_artifacts=retained,
        legacy_RGB9D_view_available=report.get('legacy_RGB9D_contract_admitted') is True and bool(manifest and legacy_path and legacy_sha),legacy_manifest_path=legacy_path,legacy_manifest_sha256=legacy_sha,
        semantic_task_verified_by='operator_only',physical_success_inferred=False)


def workflow(store,skill_id,version=None,*,inspector=None):
    with store.connect() as db:
        selected=_get_spec(db,skill_id,version);spec=selected['spec']
        bindings=[row for row in _binding_map(db).values() if row['skill_id']==spec['skill_id'] and row['version']==spec['version']]
        sessions=[row['payload'] for row in _records(db,'session') if row['payload']['skill_id']==spec['skill_id'] and row['payload']['version']==spec['version']]
        pending_captures=[dict(row['payload'],native_active_unverified=True) for row in _records(db,'capture.begin') if row['payload']['skill_id']==spec['skill_id'] and row['payload']['version']==spec['version'] and not any(done['payload']['capture_id']==row['payload']['capture_id'] for done in _records(db,'capture.finished'))]
        training=[row['payload'] for row in _records(db,'training') if row['payload']['skill_id']==spec['skill_id'] and row['payload']['version']==spec['version']]
    decisions=[_admission(store,spec,row,inspector) for row in bindings]
    # Known original session IDs are additional leakage groups; operator teaching
    # sessions remain explicit declarations, not proof of changed physical scene.
    groups={};duplicates=set();source_hashes={}
    for row in decisions:
        if row['accepted']:
            binding=row['binding']
            for key in _binding_groups(binding):
                if key[1]:groups.setdefault(key,set()).add(binding['split'])
            for sample in ((row['evidence'].get('mobile_embodied') or {}).get('envelope') or {}).get('samples',[]):
                image=((sample.get('channels') or {}).get('camera_rgb') or {}).get('value') or {}
                if image.get('camera_id') and image.get('frame_id'):
                    groups.setdefault(('original_camera_frame',(image['camera_id'],image['frame_id'])),set()).add(binding['split'])
                if image.get('sha256'):groups.setdefault(('original_RGB_payload',image['sha256']),set()).add(binding['split'])
            for descriptor in (row.get('manifest') or {}).get('episodes',[]):
                previous=source_hashes.get(descriptor['sha256'])
                identity=(binding['original_episode_id'],binding['split'])
                if previous and previous!=identity:duplicates.update((previous[0],identity[0]))
                source_hashes[descriptor['sha256']]=identity
    for row in decisions:
        binding=row['binding']
        leakage=any(len(groups.get(key,set()))>1 for key in _binding_groups(binding) if key[1])
        for sample in ((row.get('evidence',{}).get('mobile_embodied') or {}).get('envelope') or {}).get('samples',[]):
            image=((sample.get('channels') or {}).get('camera_rgb') or {}).get('value') or {}
            if len(groups.get(('original_camera_frame',(image.get('camera_id'),image.get('frame_id'))),set()))>1:leakage=True
            if len(groups.get(('original_RGB_payload',image.get('sha256')),set()))>1:leakage=True
        if leakage:row['accepted']=False;row['reasons'].append('original_scene_session_object_or_RGB_payload_overlaps_splits')
        if binding['original_episode_id'] in duplicates:row['accepted']=False;row['reasons'].append('same_source_payload_reused_as_another_original')
    variants=[]
    for variant in spec['coverage_plan']['variants']:
        assigned=[row for row in decisions if row['binding']['variant_id']==variant['variant_id']]
        approved=[row for row in assigned if row['accepted'] and row['binding'].get('episode_scope','whole_episode')=='whole_episode']
        variants.append(dict(variant,accepted_originals=len(approved),rejected_originals=sum(not row['accepted'] for row in assigned),
            attempts=len(assigned),targeted_or_recovery_accepted=sum(row['accepted'] and row['binding'].get('episode_scope','whole_episode')!='whole_episode' for row in assigned),covered=bool(approved),next_repeat_number=len(assigned)+1))
    covered={split:sum(row['covered'] and row['split']==split for row in variants) for split in SPLITS}
    accepted=[row for row in decisions if row['accepted']]
    counts=dict(accepted_originals=len(accepted),rejected_originals=len(decisions)-len(accepted),assigned_originals=len(decisions),
        covered_variants=sum(row['covered'] for row in variants),planned_variants=len(variants),
        accepted_train_originals=sum(row['binding']['split']=='train' for row in accepted),
        accepted_development_originals=sum(row['binding']['split']=='development' for row in accepted),
        sealed_originals=sum(row['binding']['split']=='sealed_test' for row in accepted))
    reasons=[]
    if not selected['confirmed']:reasons.append('skill_spec_not_confirmed')
    if not spec['pipeline']['implemented']:reasons.append('pipeline_not_implemented_for_this_task_type')
    reasons += [item['id'] for item in spec['missing_facts'] if item['required_for']=='training']
    for split,minimum in (('train',3),('development',1),('sealed_test',1)):
        if covered[split]<minimum:reasons.append('need_'+str(minimum)+'_distinct_'+split+'_variants')
    missing=next((row for row in variants if not row['covered']),None)
    if missing:
        failures=[row for row in decisions if row['binding']['variant_id']==missing['variant_id'] and not row['accepted']]
        next_action=dict(kind='repeat_rejected' if failures else 'record_variant',variant_id=missing['variant_id'],split=missing['split'],
            title=missing['title'],show_number=counts['covered_variants']+1,initial_technical_target=counts['planned_variants'],
            repeat_number=missing['next_repeat_number'],initial_robot=missing['initial_robot'],initial_object=missing['initial_object'],goal_position=missing['goal_position'],
            reason=('Повторите полный показ после исправления: '+', '.join(failures[-1]['reasons'])) if failures else 'Эти начальные условия ещё не покрыты одобренным полным показом',
            suggested_next_batch=1)
    elif reasons:next_action=dict(kind='resolve_pipeline',reason='Показы сохранены, но выбранный полный pipeline пока заблокирован',suggested_next_batch=0)
    elif not training:next_action=dict(kind='train_all_accepted',reason='Начальный технический охват собран. Обучите одну модель на всех принятых train-показах с отдельной development-сессией.',suggested_next_batch=0)
    else:next_action=dict(kind='independent_full_task_check',reason='Нужны отдельные полные попытки обученной модели. Offline chunk error и пять показов не подтверждают владение навыком; sealed записи не дообучаются.',suggested_next_batch=0)
    data_blockers=[reason for reason in reasons if reason.startswith('need_')]
    data_ready=selected['confirmed'] and not data_blockers
    phase_coverage={phase['id']:{variant['variant_id']:sum(row['accepted'] and row['binding']['variant_id']==variant['variant_id'] and row['binding'].get('phase_outcomes',{}).get(phase['id'])=='success' for row in decisions) for variant in variants} for phase in spec['stages']}
    return dict(schema='explorer_skill_workflow_v1',**selected,variants=variants,sessions=sessions,counts=counts,covered_by_split=covered,
        train_ready=not reasons,data_ready=data_ready,data_blockers=data_blockers,training_blockers=list(dict.fromkeys(reasons)),next_action=next_action,phase_variant_coverage=phase_coverage,
        recording_allowed=selected['confirmed'],recording_conditions=['chassis_must_remain_stationary_under_current_owner_prohibition'],
        recording_blockers=[],
        assignments=[dict(binding=row['binding'],accepted=row['accepted'],reasons=row['reasons'],
            evidence={key:row.get('evidence',{}).get(key) for key in ('original_episode_id','operator_success','source_quality_usable','rgb_verified','action9_verified','causal_verified','raw_frames_recorded','missing_facts')}) for row in decisions],
        training_jobs=training,pending_captures=pending_captures,skill_mastery_claimed=False,physical_validation_inferred=False,
        split_independence_basis='distinct_original_episodes_payloads_and_declared_teaching_sessions; physical_scene_independence_not_automatically_verified')


def _schema(manifest,row):
    return dict(action_names=manifest['action_names'],action_units=manifest['action_units'],action_normalization=manifest['action_normalization'],
        state_basis=manifest['state_basis'],action_basis=manifest['action_basis'],fps=manifest['fps'],
        resampling_method=manifest.get('resampling',{}).get('method'),clock_kind=manifest.get('native_timestamp_basis',{}).get('kind','monotonic'),
        camera_feature=manifest.get('source_camera_feature','UNKNOWN'),rgb_shape=row['rgb_shape'])


def _write_file(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())


def _sync_directory(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def build_aggregate(store,skill_id,version,*,inspector=None):
    state=workflow(store,skill_id,version,inspector=inspector)
    if not state['data_ready']:raise WorkflowBlocked(state['data_blockers'])
    spec=state['spec']
    with store.connect() as db:
        bindings=[row for row in _binding_map(db).values() if row['skill_id']==spec['skill_id'] and row['version']==version]
    decisions=[_admission(store,spec,row,inspector) for row in bindings]
    accepted_ids={row['binding']['original_episode_id'] for row in state['assignments'] if row['accepted']}
    included=[row for row in decisions if row['accepted'] and row['original_episode_id'] in accepted_ids and row['binding']['split'] in ('train','development')]
    if any(not row['accepted'] for row in decisions if row['original_episode_id'] in accepted_ids):raise ValueError('Source admission changed while preparing aggregate')
    if not included:raise WorkflowBlocked(['no_accepted_train_and_development_sources'])
    canonical_episodes,component_descriptors,source_records,files=[],[],[],{}
    frame_splits={};group_splits={};originals=set();component_schema=None;component_possible=True
    for row in sorted(included,key=lambda item:(item['binding']['split'],item['original_episode_id'])):
        binding=row['binding'];report=row['evidence'];manifest=row.get('manifest');original=binding['original_episode_id']
        if original in originals:raise ValueError('Duplicate original cannot inflate aggregate')
        originals.add(original)
        for key in _binding_groups(binding):
            if key in group_splits and group_splits[key]!=binding['split']:raise ValueError('Original scene/session/object crosses aggregate split')
            group_splits[key]=binding['split']
        namespace='episodes/'+digest(dict(original=original,source=binding['source_fingerprint']))[:32]
        mapping={}
        for artifact in row['embodied_source_artifacts']:
            relative=namespace+'/embodied/'+artifact['relative_name']
            if relative in files and files[relative][1]!=artifact['sha256']:raise ValueError('Canonical artifact namespace collision')
            files[relative]=(Path(artifact['path']),artifact['sha256']);mapping[artifact['relative_name']]=relative
        inspection_file=namespace+'/canonical-admission.json';inspection_bytes=canonical(report['mobile_embodied'])
        files[inspection_file]=(inspection_bytes,hashlib.sha256(inspection_bytes).hexdigest())
        canonical_episodes.append(dict(id='episode-'+digest(dict(original=original,source=binding['source_fingerprint']))[:32],
            source_episode_id=original,split=binding['split'],skill_id=spec['skill_id'],skill_version=version,variant_id=binding['variant_id'],
            teaching_session_id=binding['session_id'],original_session_id=binding['source_session_id'],scene=binding['scene'],
            source_kind=report['mobile_embodied'].get('source_kind'),source_split=report['mobile_embodied'].get('split'),
            episode_scope=binding.get('episode_scope','whole_episode'),target_phase_ids=binding.get('target_phase_ids'),phase_outcomes=binding.get('phase_outcomes'),
            canonical_admission_file=inspection_file,canonical_admission_sha256=files[inspection_file][1],source_artifact_map=mapping,
            action_contract_id=UNIFIED_ACTION_CONTRACT,simultaneous_9d=True,neutral_channels_retained=True,
            recorded_admitted_model_used_channels=report['mobile_embodied'].get('channels'),model_input_plan=report['mobile_embodied'].get('model_input_plan')))
        source_record=dict(original_episode_id=original,import_job_id=binding['import_job_id'],source_fingerprint=binding['source_fingerprint'],
            binding_sha256=binding['binding_sha256'],variant_id=binding['variant_id'],split=binding['split'],teaching_session_id=binding['session_id'],
            original_session_id=binding['source_session_id'],source_split_groups=binding.get('source_split_groups'),canonical_source_artifact_map=mapping)
        # An unchanged optional legacy RGB/prior-command9 view may be retained
        # for an internal ACT component; it never becomes the full dataset.
        if manifest and row.get('legacy_manifest_path'):
            source_path=_local(store.root,row['legacy_manifest_path']);source_bytes=source_path.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest()!=row.get('legacy_manifest_sha256'):raise ValueError('Original converted manifest changed')
            snapshot=namespace+'/original-converted-manifest.json';files[snapshot]=(source_path,row['legacy_manifest_sha256'])
            source_record.update(manifest_sha256=row['legacy_manifest_sha256'],saved_manifest=snapshot)
        can_component=bool(manifest and row['legacy_RGB9D_view_available'])
        if can_component:
            signature={key:manifest.get(key) for key in ('action_names','action_units','action_normalization','state_basis','action_basis','fps','source_camera_feature')}
            signature['resampling_method']=manifest.get('resampling',{}).get('method');signature['clock_kind']=manifest.get('native_timestamp_basis',{}).get('kind','monotonic')
            if signature['action_names']!=list(ACTION_NAMES) or signature['action_units']!=list(ACTION_UNITS):raise ValueError('Optional component must retain the full original action9')
            if component_schema is None:component_schema=signature
            if signature!=component_schema:component_possible=False
            for source in manifest['episodes']:
                if source.get('source_episode_id',source.get('id'))!=original:raise ValueError('Converted view contains another original')
                if source.get('split') in ('sealed_test','test') or (source.get('split') in ('development','validation','dev') and binding['split']=='train'):
                    raise ValueError('Heldout original cannot become a fitted component')
                for frame_id in source.get('frame_source_ids',[]):
                    key=(source.get('clock_domain',manifest.get('clock_domain')),frame_id)
                    if key in frame_splits and frame_splits[key]!=binding['split']:raise ValueError('Camera frame crosses aggregate split')
                    frame_splits[key]=binding['split']
                path=_local(store.root,str(source_path.parent/source['file']))
                if not path.is_relative_to(source_path.parent) or _sha(path)!=source['sha256']:raise ValueError('Converted source payload changed')
                relative=namespace+'/legacy_component/'+digest(dict(original=original,sha256=source['sha256']))[:24]+'.npz';files[relative]=(path,source['sha256'])
                component_descriptors.append(dict(source,id='component-'+digest(dict(original=original,sha256=source['sha256']))[:32],file=relative,
                    source_episode_id=original,split=binding['split'],outcome='success',clock_domain=source.get('clock_domain',manifest.get('clock_domain'))))
        else:component_possible=False
        source_records.append(source_record)
    sealed=[dict(original_episode_id=row['binding']['original_episode_id'],split='sealed_test',source_fingerprint=row['binding']['source_fingerprint']) for row in decisions if row['binding']['split']=='sealed_test']
    component_name=None
    if component_possible and component_descriptors:
        first=included[0]['manifest'];component_name='internal_legacy_RGB9D_component_view.json'
        component=dict(schema_version=1,format='explorer_robot_episode_v1',dataset_id='internal-view-'+spec['skill_id']+'-v'+str(version),
            purpose='internal_optional_RGB_preceding_command9_view_NOT_full_robot_model_or_alternative_task',
            source='unified_skill_internal_legacy_view',task=spec['title'],skill_id=spec['skill_id'],skill_version=version,
            action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),action_normalization=first['action_normalization'],state_basis=first['state_basis'],
            action_basis=first['action_basis'],fps=first['fps'],clock_domain='PER_EPISODE_SOURCE_CLOCKS_NOT_MERGED',native_timestamp_basis=first.get('native_timestamp_basis',{}),
            resampling=dict(method=first.get('resampling',{}).get('method')) if first.get('resampling',{}).get('method') else {},episodes=component_descriptors,
            contains_physical_recordings=any(row['evidence']['mobile_embodied'].get('source_kind')=='physical' for row in included),training_admission=dict(source_quality_usable=True,operator_success_label=True,physical_validation_inferred=False),
            physical_success_inferred=False,action_contract_id=UNIFIED_ACTION_CONTRACT,neutral_channels_retained=True)
        component.update(cross_view_sample_correspondence_verified=False,eligible_as_full_hierarchy_training_dataset=False,
            correspondence_limitation='Separate valid views and an original episode ID do not prove shared per-sample RGB/action/clock linkage; no fusion or full-sensor ACT claim')
        data=canonical(component);files[component_name]=(data,hashlib.sha256(data).hexdigest())
    core=dict(schema='explorer_unified_embodied_skill_dataset_v1',format='explorer_unified_embodied_skill_dataset_v1',
        embodiment='rosmaster_unified_mobile_manipulator',skill_id=spec['skill_id'],skill_version=version,skill_spec_sha256=state['spec_sha256'],skill_spec=spec,
        action_contract_id=UNIFIED_ACTION_CONTRACT,action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),simultaneous_9d=True,neutral_channels_retained=True,
        observation_contract=spec['observation_contract'],episodes=canonical_episodes,source_records=source_records,sealed_sources_excluded=sealed,
        source_files_sha256={name:expected for name,(_,expected) in files.items()},
        optional_internal_legacy_RGB9D_view=component_name,preceding_command9_manufactured=False,
        source_kinds=sorted({row['evidence']['mobile_embodied']['source_kind'] for row in included}),cross_episode_split_checked=True,
        full_hierarchy_training_implemented=False,physical_success_inferred=False,method_ids=['ENGINEERING_GLUE'])
    content=digest(core);manifest=dict(core,aggregate_content_id=content,dataset_id='unified-skill-'+spec['skill_id']+'-v'+str(version)+'-'+content[:16]);data=canonical(manifest)
    destination=Path(store.root)/'datasets'/'skill-workflows'/spec['skill_id']/('v'+str(version).zfill(4))/content
    if destination.exists():
        if (destination/'manifest.json').read_bytes()!=data:raise ValueError('Immutable unified aggregate manifest changed')
        for name,(_,expected) in files.items():
            if _sha(destination/name)!=expected:raise ValueError('Immutable unified aggregate payload changed')
    else:
        destination.parent.mkdir(parents=True,exist_ok=True);stage=destination.with_name('.'+content+'.'+uuid.uuid4().hex+'.staging');stage.mkdir()
        try:
            for name,(source,expected) in files.items():
                payload=source.read_bytes() if isinstance(source,Path) else source
                _write_file(stage/name,payload)
                if _sha(stage/name)!=expected or (isinstance(source,Path) and _sha(source)!=expected):raise ValueError('Source changed while copying immutable canonical aggregate')
            _write_file(stage/'manifest.json',data)
            for directory,_,_ in os.walk(stage,topdown=False):_sync_directory(directory)
            stage.rename(destination);_sync_directory(destination.parent)
        except Exception:
            if stage.exists():shutil.rmtree(stage)
            raise
    return dict(dataset_manifest=str(destination/'manifest.json'),aggregate_sha256=hashlib.sha256(data).hexdigest(),aggregate_content_id=content,dataset_id=manifest['dataset_id'],
        skill_id=spec['skill_id'],skill_version=version,spec_sha256=state['spec_sha256'],
        training_original_episode_ids=[row['binding']['original_episode_id'] for row in included if row['binding']['split']=='train'],
        development_original_episode_ids=[row['binding']['original_episode_id'] for row in included if row['binding']['split']=='development'],sealed_original_episode_ids=[row['original_episode_id'] for row in sealed],
        internal_legacy_component_view=str(destination/component_name) if component_name else None,manifest=manifest)


def training_request(store,skill_id,version,settings=None,*,inspector=None):
    settings=settings or {}
    allowed={'seed','act_steps','batch_size','image_size','chunk_size','device'}
    if not isinstance(settings,dict) or set(settings)-allowed:raise ValueError('Choose supported learner settings only')
    values=dict(seed=41,act_steps=120,batch_size=8,image_size=64,chunk_size=8,device='cpu');values.update(settings)
    for name,low,high in (('seed',0,1000000),('act_steps',1,100000),('batch_size',1,64),('image_size',32,224),('chunk_size',1,100)):
        if type(values[name]) is not int or not low<=values[name]<=high:raise ValueError('Invalid offline training '+name)
    if values['device'] not in ('cpu','cuda'):raise ValueError('Explicit CPU/CUDA learner required')
    state=workflow(store,skill_id,version,inspector=inspector)
    if not state['train_ready']:raise WorkflowBlocked(state['training_blockers'])
    aggregate=build_aggregate(store,skill_id,version,inspector=inspector)
    lineage={key:aggregate[key] for key in ('skill_id','skill_version','spec_sha256','aggregate_sha256','aggregate_content_id','training_original_episode_ids','development_original_episode_ids','sealed_original_episode_ids')}
    lineage.update(pipeline_class=aggregate['manifest']['skill_spec']['pipeline']['pipeline_class'],training_scope='whole_unified_rosmaster_skill',action_contract_id=UNIFIED_ACTION_CONTRACT)
    return dict(kind=state['spec']['pipeline']['job_kind'],project_id='02',config=dict(values,dataset_manifest=aggregate['dataset_manifest'],skill_workflow=lineage))


def register_training_job(store,job):
    raise WorkflowBlocked(['use_enqueue_training_for_atomic_job_and_lineage_reservation'])


def attach_skill_lineage(card,job_config,source_manifest_sha256):
    lineage=job_config.get('skill_workflow') if isinstance(job_config,dict) else None
    if not lineage:return card
    if lineage.get('aggregate_sha256')!=source_manifest_sha256:raise ValueError('Model source does not match its skill aggregate')
    path=Path(job_config.get('dataset_manifest',''))
    if not path.is_file() or _sha(path)!=source_manifest_sha256:raise ValueError('Model aggregate source is unavailable or changed')
    manifest=json.loads(path.read_text())
    if manifest.get('skill_id')!=lineage.get('skill_id') or manifest.get('skill_version')!=lineage.get('skill_version') or manifest.get('skill_spec_sha256')!=lineage.get('spec_sha256'):
        raise ValueError('Model aggregate belongs to another skill/version/specification')
    result=json.loads(json.dumps(card));result.setdefault('lineage',{}).update(skill_id=lineage['skill_id'],skill_version=lineage['skill_version'],
        skill_spec_sha256=lineage['spec_sha256'],aggregate_sha256=lineage['aggregate_sha256'],aggregate_content_id=lineage['aggregate_content_id'])
    result['skill_id']=lineage['skill_id'];result['skill_version']=lineage['skill_version']
    result['unified_model_qualified']=False
    result['analysis_reason']='Skill lineage alone does not qualify a legacy RGB9D/other model as the full unified multisensor hierarchy; actual model-used channel and hierarchy execution receipts required'
    result['recorded_admitted_model_used_provenance']=manifest.get('observation_contract')
    result.setdefault('limitations',[]).append('Skill definition and aggregate lineage do not establish physical full-task success')
    return result


def _previous_fitting_ids(store):
    """Conservative legacy guard: a queued fitting reservation is never a test."""
    used=set()
    for job in store.jobs(10000):
        if job['kind']=='train_act':
            config=job.get('config') or {};lineage=config.get('skill_workflow') or {}
            used.update(lineage.get('training_original_episode_ids',[]))
            value=config.get('dataset_manifest') or (job.get('result') or {}).get('source_manifest_path')
            if value:
                try:
                    manifest=json.loads(_local(store.root,value).read_text())
                    used.update(item.get('source_episode_id',item.get('id')) for item in manifest.get('episodes',[]) if item.get('split','train') in ('train','training','imitation_train'))
                except (OSError,ValueError,TypeError):pass
    return used


def begin_capture(store,skill_id,version,variant_id,session_id,*,request_id=None,episode_scope='whole_episode',target_phase_ids=None,stationary_chassis_confirmed=False):
    """Create a local intent; this does not start or modify native recording."""
    capture_id=_uuid(request_id or str(uuid.uuid4()))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');selected=_get_spec(db,skill_id,version);spec=selected['spec'];variant=_variant(spec,variant_id);session=_session(db,session_id)
        if not selected['confirmed']:raise WorkflowBlocked(['skill_spec_not_confirmed'])
        if stationary_chassis_confirmed is not True:raise WorkflowBlocked(['owner_chassis_motion_forbidden'])
        if episode_scope not in ('whole_episode','targeted_subskill','recovery'):raise ValueError('Unknown episode coverage scope; action contract always remains full 9D')
        targets=target_phase_ids if target_phase_ids is not None else [item['id'] for item in spec['stages']]
        if not isinstance(targets,list) or not targets or any(not isinstance(item,str) for item in targets) or len(set(targets))!=len(targets) or set(targets)-{item['id'] for item in spec['stages']}:raise ValueError('Explicit valid target phases required')
        if episode_scope=='whole_episode' and set(targets)!={item['id'] for item in spec['stages']}:raise ValueError('Whole episodes must retain every full-task target phase')
        if session['skill_id']!=spec['skill_id'] or session['version']!=version or session['split']!=variant['split']:raise ValueError('Recording context belongs to a different skill/version/split')
        payload=dict(capture_id=capture_id,skill_id=spec['skill_id'],version=version,spec_sha256=selected['spec_sha256'],variant_id=variant_id,session_id=session['session_id'],split=variant['split'],
            native_payload_additions={},automatic_motion=False,episode_scope=episode_scope,target_phase_ids=targets,stationary_chassis_confirmed=True,
            action_contract_id=UNIFIED_ACTION_CONTRACT,neutral_channels_retained=True)
        _append(db,'capture.begin',payload,identity=EVENT_PREFIX+'capture.begin:'+capture_id)
        return payload


def _capture(db,capture_id):
    capture_id=_uuid(capture_id)
    record=next((row['payload'] for row in _records(db,'capture.begin') if row['payload']['capture_id']==capture_id),None)
    if record is None:raise ValueError('Unknown local capture intent')
    return record


def capture_started(store,capture_id,native_episode_id):
    """Root calls only after a successful unchanged native start response."""
    original=_text(native_episode_id,'native episode',160)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');capture=_capture(db,capture_id)
        existing=next((row['payload'] for row in _records(db,'capture.started') if row['payload']['original_episode_id']==original),None)
        if existing and existing['capture_id']!=capture_id:raise ValueError('Native episode already belongs to another capture intent')
        return _append(db,'capture.started',dict(capture_id=capture['capture_id'],original_episode_id=original,
            source='root_verified_native_start_response',native_payload_modified=False),identity=EVENT_PREFIX+'capture.started:'+capture['capture_id'])['payload']


def capture_stage(store,capture_id,stage_id,request_id=None):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');capture=_capture(db,capture_id);spec=_get_spec(db,capture['skill_id'],capture['version'])['spec']
        if any(row['payload']['capture_id']==capture['capture_id'] for row in _records(db,'capture.finished')):raise ValueError('Cannot label a finished capture')
        if stage_id not in {item['id'] for item in spec['stages']}:raise ValueError('Stage differs from the selected complete skill task')
        return _append(db,'capture.stage',dict(capture_id=capture['capture_id'],stage_id=stage_id,mark_id=_uuid(request_id or str(uuid.uuid4())),
            timing_basis='central_receipt_of_operator_stage_mark_not_camera_exposure',automatic_contact_verified=False))['payload']


def capture_finished(store,capture_id,native_episode_id,body):
    """Save explicit review sidecar after native finish; no original bytes change."""
    body=body if isinstance(body,dict) else {}
    rejected=[]
    outcome=body.get('outcome')
    if outcome not in ('success','failure','unknown'):rejected.append('invalid_or_missing_review_outcome');outcome='unknown'
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');capture=_capture(db,capture_id);spec=_get_spec(db,capture['skill_id'],capture['version'])['spec']
        started=next((row['payload'] for row in _records(db,'capture.started') if row['payload']['capture_id']==capture['capture_id']),None)
        if not started or started['original_episode_id']!=native_episode_id:raise ValueError('Native recording identity changed; no skill labels applied')
        stages=body.get('completed_stages',[])
        if not isinstance(stages,list) or any(not isinstance(stage,str) for stage in stages) or len(set(stages))!=len(stages) or set(stages)-{item['id'] for item in spec['stages']}:rejected.append('unknown_or_repeated_completed_skill_stage');stages=[]
        payload=dict(capture_id=capture['capture_id'],original_episode_id=native_episode_id,outcome=outcome,
            completed_stages=stages,full_task_confirmed=not rejected and body.get('full_task_confirmed') is True and outcome=='success' and capture['episode_scope']=='whole_episode',
            object_and_goal_visible=body.get('object_and_goal_visible') is True,review_basis='explicit_operator_finished_recording_review',native_payload_modified=False,review_rejected_reasons=rejected,
            phase_outcomes={name:'success' if outcome=='success' and name in stages and not rejected else 'failure' if outcome=='failure' and name in stages else 'unknown' for name in capture['target_phase_ids']})
        return _append(db,'capture.finished',payload,identity=EVENT_PREFIX+'capture.finished:'+capture['capture_id'])['payload']


def bind_import_from_capture(store,job,*,inspector=None):
    """Root import-completion hook: idempotent, even when upload finishes later."""
    if job.get('kind')!='mobile_import' or job.get('status')!='completed':return None
    original=(job.get('result') or {}).get('episode_id') or job['config'].get('episode_id')
    with store.connect() as db:
        started=next((row['payload'] for row in _records(db,'capture.started') if row['payload']['original_episode_id']==original),None)
        if not started:return None
        capture=_capture(db,started['capture_id'])
        finished=next((row['payload'] for row in _records(db,'capture.finished') if row['payload']['capture_id']==capture['capture_id']),None)
        if not finished:return None
        previous=_binding_map(db).get(original)
    # An already reviewed/moved original is never silently reassigned by a late callback.
    if previous:return previous
    return bind_episode(store,capture['skill_id'],capture['version'],capture['variant_id'],dict(import_job_id=job['id'],session_id=capture['session_id'],
        completed_stages=finished['completed_stages'],full_task_confirmed=finished['full_task_confirmed'],object_and_goal_visible=finished['object_and_goal_visible'],episode_scope=capture['episode_scope'],target_phase_ids=capture['target_phase_ids'],phase_outcomes=finished.get('phase_outcomes',{})),inspector=inspector)


def available_imports(store,*,inspector=None):
    with store.connect() as db:assigned=_binding_map(db)
    result=[]
    for job in store.jobs(10000):
        if job['kind']=='mobile_import' and job['project_id']=='02' and job['status']=='completed':
            report=_inspector(job,store.root,inspector)
            identity=report.get('original_episode_id') or (job.get('result') or {}).get('episode_id') or job['config'].get('episode_id')
            result.append(dict(import_job_id=job['id'],original_episode_id=identity,created=job['created'],
                assigned_to={key:assigned[identity][key] for key in ('skill_id','version','variant_id','split','binding_sha256')} if identity in assigned else None,
                **{key:report.get(key) for key in ('completed_recording','operator_success','source_quality_usable','rgb_verified','action9_verified','causal_verified','admitted_for_training','reasons','missing_facts','raw_frames_recorded')}))
    return result


def validate_02_training_request(store,body,*,server_generated=False):
    """Root calls before generic /api/jobs: no forged single-clip/partial bypass."""
    if body.get('project_id')!='02':return True
    if body.get('kind') in ('mobile_import','camera_recording'):return True
    if not server_generated:raise WorkflowBlocked(['only_server_built_verified_unified_skill_requests_allowed'])
    lineage=(body.get('config') or {}).get('skill_workflow')
    if not isinstance(lineage,dict) or lineage.get('action_contract_id')!=UNIFIED_ACTION_CONTRACT:raise WorkflowBlocked(['verified_unified_skill_lineage_required'])
    with store.connect() as db:selected=_get_spec(db,lineage.get('skill_id'),lineage.get('skill_version'))
    if not selected['confirmed'] or selected['spec_sha256']!=lineage.get('spec_sha256'):raise WorkflowBlocked(['confirmed_current_skill_definition_required'])
    pipeline=selected['spec']['pipeline']
    if pipeline.get('implemented') is not True or body.get('kind')!=pipeline.get('job_kind'):raise WorkflowBlocked(['real_full_perception_navigation_contact_coordinator_recovery_backend_not_implemented'])
    return True


def enqueue_training(store,skill_id,version,settings=None,*,inspector=None):
    """One transaction for a future qualified job and its immutable reservation.

    The current complete hierarchy is deliberately blocked before any job row.
    Preparing immutable artifacts precedes the short SQLite write transaction.
    """
    prepared=training_request(store,skill_id,version,settings,inspector=inspector)
    validate_02_training_request(store,prepared,server_generated=True)
    lineage=prepared['config']['skill_workflow'];path=_local(store.root,prepared['config']['dataset_manifest'])
    if _sha(path)!=lineage['aggregate_sha256']:raise ValueError('Prepared unified aggregate changed')
    manifest=json.loads(path.read_text())
    if manifest.get('schema')!='explorer_unified_embodied_skill_dataset_v1' or manifest.get('action_contract_id')!=UNIFIED_ACTION_CONTRACT:
        raise WorkflowBlocked(['canonical_unified_dataset_required'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');selected=_get_spec(db,skill_id,version)
        if selected['spec_sha256']!=lineage['spec_sha256'] or not selected['confirmed']:raise ValueError('Skill changed while preparing training')
        current=_binding_map(db)
        for record in manifest.get('source_records',[]):
            if (current.get(record['original_episode_id']) or {}).get('binding_sha256')!=record['binding_sha256']:
                raise ValueError('Episode assignment changed while preparing training')
        previous=db.execute("SELECT * FROM jobs WHERE project_id='02' AND status NOT IN ('completed','failed','cancelled') AND json_extract(config,'$.skill_workflow.skill_id')=? AND json_extract(config,'$.skill_workflow.skill_version')=? ORDER BY created LIMIT 1",(skill_id,version)).fetchone()
        if previous:
            if json.loads(previous['config']).get('skill_workflow',{}).get('aggregate_sha256')==lineage['aggregate_sha256']:return store.decode_job(previous)
            raise WorkflowBlocked(['one_active_training_job_per_skill_version'])
        job_id=uuid.uuid4().hex;now=time.time()
        db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
            (job_id,prepared['kind'],'02','queued',now,now,json.dumps(prepared['config'],allow_nan=False)))
        _append(db,'training',dict(skill_id=skill_id,version=version,job_id=job_id,aggregate_sha256=lineage['aggregate_sha256'],aggregate_content_id=lineage['aggregate_content_id'],
            training_original_episode_ids=lineage['training_original_episode_ids'],development_original_episode_ids=lineage['development_original_episode_ids'],sealed_original_episode_ids=lineage['sealed_original_episode_ids']),identity=EVENT_PREFIX+'training:'+job_id)
    return store.job(job_id)


def verify_capture_episode(store,capture_id,native_episode_id):
    with store.connect() as db:
        capture=_capture(db,capture_id)
        started=next((row['payload'] for row in _records(db,'capture.started') if row['payload']['capture_id']==capture['capture_id']),None)
        if not started or started['original_episode_id']!=native_episode_id:raise ValueError('Capture belongs to another native episode; no stage/outcome labels applied')
        return dict(capture,original_episode_id=started['original_episode_id'])


def capture_for_episode(store,native_episode_id):
    """Durable lookup for UI reload; native active ID remains the authority."""
    with store.connect() as db:
        started=next((row['payload'] for row in _records(db,'capture.started') if row['payload']['original_episode_id']==native_episode_id),None)
        if not started:return None
        capture=_capture(db,started['capture_id'])
        finished=next((row['payload'] for row in _records(db,'capture.finished') if row['payload']['capture_id']==capture['capture_id']),None)
        spec=_get_spec(db,capture['skill_id'],capture['version'])
        return dict(capture,original_episode_id=native_episode_id,finished=finished,spec=spec['spec'],spec_sha256=spec['spec_sha256'],
            native_still_active_must_be_verified_separately=True)
