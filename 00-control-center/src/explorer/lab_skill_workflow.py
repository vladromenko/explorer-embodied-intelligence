"""ENGINEERING_GLUE: Durable SO101 source teaching→all-source dataset→official ACT workflow.

Metadata/immutable data only. No robot/network/control operation is imported.
Operator video/phase labels are BC evidence, never learned-policy physical proof.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

from explorer_shared.contracts import canonical,digest,event
from explorer_shared.so101_station import NAMES,UNITS,so101_source_identity
from explorer_shared.experiments import experiment_id,experiment_identity
from . import skill_catalogue as catalogue_module

PREFIX='lab.skill.'
CONTRACT=catalogue_module.SO101_CONTRACT
SPLITS=('train','development','sealed_test')
SOURCE_ROLE='so101_source_learning'
SOURCE_PROJECT=experiment_id(SOURCE_ROLE)
CAPTURE_PINS=('capture_manifest_sha256','source_review_sha256','source_archive_sha256','import_provenance_sha256')


def _capture_pins(evidence):
    capture=evidence.get('source_capture')
    return {key:capture[key] for key in CAPTURE_PINS} if capture else {}


def _source_snapshot(decision):
    return dict(binding=decision['binding'],review=decision['review'],
        **({'source_capture':decision['evidence']['source_capture']} if decision['evidence'].get('source_capture') else {}))


class WorkflowBlocked(ValueError):
    def __init__(self,reasons):self.reasons=list(dict.fromkeys(reasons));super().__init__(';'.join(self.reasons))


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _local(store,path):
    path=Path(path).expanduser().resolve()
    if not path.is_file() or not path.is_relative_to(Path(store.root).resolve()):raise ValueError('Source artifact outside managed state or missing')
    return path


def _sim():
    root=Path(__file__).resolve().parents[3]/'01-autonomous-learning/src'
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    from explorer_sim.so101_dataset_qualification import inspect_station_episode,qualify_station_dataset
    return inspect_station_episode,qualify_station_dataset


def _records(db):
    return [json.loads(row['body'])['payload'] for row in db.execute("SELECT body FROM events WHERE event_id LIKE ? ORDER BY rowid",(PREFIX+'%',))]


def _append(db,kind,payload,request_id=None):
    identity=PREFIX+kind+':'+str(request_id or uuid.uuid4());payload=dict(payload,**experiment_identity(SOURCE_ROLE),type=PREFIX+kind)
    old=db.execute('SELECT body FROM events WHERE event_id=?',(identity,)).fetchone()
    if old:
        body=json.loads(old['body'])['payload']
        if body!=payload:raise ValueError('Immutable workflow request identity changed')
    else:
        envelope=event('nx',SOURCE_PROJECT,payload);envelope['event_id']=identity
        envelope.update(experiment_identity(SOURCE_ROLE))
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(identity,'nx',time.time(),envelope['sha256'],canonical(envelope).decode()))
    return dict(payload,receipt_id=identity,receipt_sha256=digest(payload))


def _latest(rows,kind,key):
    result={}
    for row in rows:
        if row.get('type')==PREFIX+kind:result[row[key]]=dict(row,receipt_sha256=digest(row))
    return result


def _chosen(store,skill_id,version=None):
    with store.connect() as db:chosen=catalogue_module._selected(db,skill_id,version)
    if not catalogue_module.is_source_spec(chosen['spec']):raise ValueError('This workflow owns only the verified native SO101 source role; legacy06 requires explicit v1')
    return chosen


def catalogue():return catalogue_module.catalogue()
def list_skills(store):return [row for row in catalogue_module.list_proposals(store) if catalogue_module.is_source_spec(row['spec'])]
def propose(store,body):return catalogue_module.create_proposal(store,body)
def revise(store,skill_id,version,body):return catalogue_module.revise_proposal(store,skill_id,version,body['patch'],body['spec_sha256'])
def confirm(store,skill_id,version,body):return catalogue_module.confirm_proposal(store,skill_id,version,body['spec_sha256'])


def imports(store):
    return [{'import_job_id':row['id'],'status':row['status'],'original_episode_id':row.get('config',{}).get('episode_id'),
        'original_project_id':row.get('legacy_experiment_id',row.get('project_id')),'canonical_identity':_job_identity(row),'result':row.get('result')} for row in store.jobs(10000)
        if row['kind']=='station_import']


def _migration_epoch(job):
    proof=job.get('numbering_provenance') or {}
    if (proof.get('source')=='verified_migration_index' and proof.get('original_numbering_version')==1
            and all(catalogue_module._hash(proof.get(key)) for key in ('projection_sha256','index_sha256'))):return 1
    return None


def _job_identity(job):
    config=job.get('config') or {};proof=job.get('numbering_provenance') or {}
    if _migration_epoch(job)==1:
        identity=so101_source_identity(proof.get('original_project_id'),numbering_version=1)
        return dict(identity,provenance=proof)
    version=job.get('experiment_numbering_version',config.get('experiment_numbering_version'))
    if version is None and job.get('project_id')=='06':version=config.get('source_numbering_version')
    return so101_source_identity(job.get('project_id'),numbering_version=version)


def _source_identity(job,raw,source_sha256):
    context=raw.get('source_context') or {};version=raw.get('experiment_numbering_version',context.get('experiment_numbering_version'))
    basis='source_manifest_marker' if version is not None else None
    if 'experiment_numbering_version' in raw and 'experiment_numbering_version' in context and raw['experiment_numbering_version']!=context['experiment_numbering_version']:
        return dict(verified=False,reasons=['source_numbering_marker_conflict'])
    if version is None and _migration_epoch(job)==1:version=1;basis='verified_migration_index_bound_import'
    config=job.get('config') or {}
    if version is None and config.get('source_numbering_version') in (1,2):version=config['source_numbering_version'];basis='server_stored_source_numbering_declaration'
    identity=so101_source_identity(raw.get('project_id'),numbering_version=version)
    if raw.get('experiment_slug',SOURCE_ROLE)!=SOURCE_ROLE or context.get('experiment_slug',SOURCE_ROLE)!=SOURCE_ROLE:identity=dict(identity,verified=False,reasons=['source_semantic_role_conflict'])
    return dict(identity,source_manifest_sha256=source_sha256,numbering_basis=basis or identity.get('basis'))


def _source(store,job_id):
    job=store.job(job_id)
    if not job or job['kind']!='station_import' or job['status']!='completed':raise ValueError('Completed native SO101 import required')
    result=job.get('result') or {};candidates=[]
    for key in ('observation_manifest','source_manifest_path'):
        if result.get(key):candidates.append(result[key])
    if result.get('dataset_manifest'):
        converted=_local(store,result['dataset_manifest']);manifest=json.loads(converted.read_text())
        if manifest.get('source_manifest_path'):candidates.append(manifest['source_manifest_path'])
    selected=None
    for candidate in candidates:
        try:path=_local(store,candidate)
        except ValueError:path=None
        if path is not None:
            expected=result.get('source_manifest_sha256')
            if expected is not None and _sha(path)!=expected:raise ValueError('Imported original source manifest SHA changed')
            selected=path;break
    if selected is None:raise ValueError('Retained native source manifest unavailable')
    raw=json.loads(selected.read_text());capture=None
    if job.get('config',{}).get('source_format')=='so101_source_capture_v1':
        from .source_demo_workflow import source_capture_evidence
        capture=source_capture_evidence(store,job)
    elif ((raw.get('source_context') or {}).get('context') or {}).get('source04_demo_permission') is not None:
        raise ValueError('Source04 demonstration requires approved capture provenance; legacy admission is closed')
    inspect,_=_sim();evidence=inspect(selected)
    if capture:evidence['source_capture']=capture
    identity=job.get('config',{}).get('episode_id') or result.get('episode_id')
    if raw.get('episode_id')!=identity:raise ValueError('Imported original SO101 episode identity differs')
    evidence['source_experiment_identity']=_source_identity(job,raw,evidence['source_manifest_sha256'])
    evidence['import_job_identity']=_job_identity(job)
    if (not evidence['import_job_identity']['verified'] and evidence['source_experiment_identity'].get('verified')
            and job.get('project_id')==raw.get('project_id') and raw.get('experiment_numbering_version')==1
            and job.get('experiment_numbering_version') is None and (job.get('config') or {}).get('experiment_numbering_version') is None):
        evidence['import_job_identity']=dict(so101_source_identity(job['project_id'],numbering_version=1),basis='verified_source_manifest_v1_bound_import')
    return job,selected,raw,evidence


def create_session(store,skill_id,version,body):
    chosen=_chosen(store,skill_id,version)
    if not chosen['confirmed']:raise WorkflowBlocked(['confirm_skill_first'])
    split=body.get('split');scene=body.get('scene_id');identity=str(uuid.UUID(str(body.get('session_id') or uuid.uuid4())))
    if split not in SPLITS or not isinstance(scene,str) or not scene.strip() or len(scene)>160:raise ValueError('Explicit scene and train/development/sealed_test session required')
    payload=dict(session_id=identity,skill_id=skill_id,skill_version=version,spec_sha256=chosen['spec_sha256'],split=split,scene_id=scene,
        independence_basis='operator_declared_source_bound_group_not_physical_independence_proof')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');rows=_records(db)
        existing=next((row for row in rows if row.get('type')==PREFIX+'session' and row['session_id']==identity),None)
        if existing and existing!=dict(payload,**experiment_identity(SOURCE_ROLE),type=PREFIX+'session'):raise ValueError('Session identity already bound to another skill/version/split')
        if any(row.get('type')==PREFIX+'session' and row['scene_id']==scene and row['split']!=split for row in rows):raise ValueError('Declared scene cannot cross dataset splits')
        return _append(db,'session',payload,identity)


def capture_context(store,skill_id,version,body):
    chosen=_chosen(store,skill_id,version);spec=chosen['spec']
    if not chosen['confirmed']:raise WorkflowBlocked(['confirm_skill_first'])
    variant=next((row for row in spec['demo_plan']['variants'] if row['variant_id']==body.get('variant_id')),None)
    with store.connect() as db:session=next((row for row in _records(db) if row.get('type')==PREFIX+'session' and row['session_id']==body.get('session_id')),None)
    if not variant or not session or session['skill_id']!=skill_id or session['skill_version']!=version or session['split']!=variant['split']:raise ValueError('Select an existing skill/version/variant/session with its original split')
    object_id=body.get('object_instance_id')
    if not isinstance(object_id,str) or not object_id or len(object_id)>160:raise ValueError('Explicit object instance identity required for split groups')
    return dict(schema='explorer_so101_capture_context_v1',**experiment_identity(SOURCE_ROLE),record_start_body=dict(**experiment_identity(SOURCE_ROLE),name=body.get('name',spec['title']),task=spec['title'],
        skill_id=skill_id,skill_version=version,skill_spec_sha256=chosen['spec_sha256'],scene_id=session['scene_id'],session_id=session['session_id'],split=variant['split'],
        phase_id=spec['phase_ids'][0],task_type=spec['recognized_type'],context=dict(variant_id=variant['variant_id'],object_instance_id=object_id,
            object_query=spec['object']['query'],goal_query=spec['goal']['query'],episode_scope=body.get('episode_scope','whole_episode'))),
        action_contract_id=CONTRACT,control_session_started=False,physical_commands_dispatched=False,
        required_existing_data_plane='separately_observed_native_station_record_start_no_automatic_teleoperation')


def bind_episode(store,skill_id,version,body):
    chosen=_chosen(store,skill_id,version);spec=chosen['spec']
    if not chosen['confirmed']:raise WorkflowBlocked(['confirm_skill_first'])
    job,path,raw,evidence=_source(store,body.get('import_job_id'))
    variant=next((row for row in spec['demo_plan']['variants'] if row['variant_id']==body.get('variant_id')),None)
    if variant is None:raise ValueError('Unknown whole-skill variant')
    if evidence['split']!=variant['split']:raise ValueError('Original source split cannot be reassigned')
    source_context=raw.get('source_context') or {};context=source_context.get('context') or {};session_id=evidence.get('source_session_id')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');rows=_records(db);old=_latest(rows,'binding','original_episode_id').get(evidence['episode_id'])
        if old and old['source_manifest_sha256']!=evidence['source_manifest_sha256']:raise ValueError('An immutable original episode was rewritten')
        if old and body.get('expected_binding_sha256')!=old['receipt_sha256']:raise ValueError('Assignment changed; refresh before revision')
        session=next((row for row in rows if row.get('type')==PREFIX+'session' and row['session_id']==body.get('session_id')),None)
        if not session or session['skill_id']!=skill_id or session['skill_version']!=version or session['split']!=variant['split']:raise ValueError('Assignment needs the original teaching session/split')
        if session_id is not None and session_id!=session['session_id']:raise ValueError('Original source session cannot be reassigned')
        payload=dict(skill_id=skill_id,skill_version=version,spec_sha256=chosen['spec_sha256'],import_job_id=job['id'],
            original_episode_id=evidence['episode_id'],source_manifest_sha256=evidence['source_manifest_sha256'],variant_id=variant['variant_id'],split=variant['split'],
            session_id=session['session_id'],scene_id=session['scene_id'],object_instance_id=context.get('object_instance_id'),episode_scope=context.get('episode_scope','whole_episode'),
            source_context_present=bool(source_context),supersedes_sha256=old['receipt_sha256'] if old else None)
        if evidence.get('source_capture'):payload['source_capture_pins']=_capture_pins(evidence)
        return _append(db,'binding',payload,body.get('request_id'))


def review_episode(store,skill_id,version,body):
    chosen=_chosen(store,skill_id,version);spec=chosen['spec'];identity=body.get('original_episode_id')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');rows=_records(db);binding=_latest(rows,'binding','original_episode_id').get(identity)
        if not binding or binding['skill_id']!=skill_id or binding['skill_version']!=version or binding['receipt_sha256']!=body.get('binding_sha256'):raise ValueError('Review must bind exact current source assignment')
        old=_latest(rows,'review','original_episode_id').get(identity)
        if old and body.get('expected_review_sha256')!=old['receipt_sha256']:raise ValueError('Review changed; refresh before revision')
        if body.get('outcome') not in ('success','failure','unknown') or not isinstance(body.get('reviewed_by'),str) or not body['reviewed_by']:raise ValueError('Explicit operator and success/failure/unknown review required')
        intervals=body.get('phase_intervals') or {};outcomes=body.get('phase_outcomes') or {}
        if not isinstance(intervals,dict) or not isinstance(outcomes,dict) or set(intervals)-set(spec['phase_ids']) or set(outcomes)-set(spec['phase_ids']):raise ValueError('Review only declared whole-skill phases')
        if any(value not in ('success','failure','unknown') for value in outcomes.values()):raise ValueError('Explicit phase outcomes required')
        if any(not isinstance(value,list) or len(value)!=2 or any(type(x) is not int or x<0 for x in value) or value[0]>value[1] for value in intervals.values()):raise ValueError('Phase intervals use original sample indices')
        payload=dict(skill_id=skill_id,skill_version=version,original_episode_id=identity,binding_sha256=binding['receipt_sha256'],
            source_manifest_sha256=binding['source_manifest_sha256'],outcome=body['outcome'],phase_outcomes=outcomes,phase_intervals=intervals,
            reviewed_by=body['reviewed_by'],observed_recording=body.get('observed_recording') is True,object_and_goal_visible=body.get('object_and_goal_visible') is True,
            full_task_confirmed=body.get('full_task_confirmed') is True,basis='operator_video_review_independent_of_command_targets_not_learned_policy_physical_validation',
            supersedes_sha256=old['receipt_sha256'] if old else None)
        return _append(db,'review',payload,body.get('request_id'))


def _decision(store,spec,binding,review):
    reasons=[]
    try:job,path,raw,evidence=_source(store,binding['import_job_id'])
    except (ValueError,OSError,KeyError,TypeError) as error:return dict(binding=binding,accepted=False,reasons=['source_error:'+str(error)])
    if evidence['source_manifest_sha256']!=binding['source_manifest_sha256']:reasons.append('bound_source_changed')
    if _capture_pins(evidence)!=binding.get('source_capture_pins',{}):reasons.append('bound_source_capture_or_approval_changed')
    if not evidence['source_experiment_identity'].get('verified') or not evidence['import_job_identity'].get('verified'):reasons.append('source_experiment_identity_not_verified')
    if not evidence['technical_dataset_ready'] or raw.get('recording_provenance_version',1)<2:reasons.append('source_grade_not_training_ready')
    for channel in ('operator_requested','follower_accepted','follower_dispatched_separate','leader_measured','follower_measured','camera_rgb'):
        if not evidence['recorded_channels'][channel]['complete']:reasons.append('missing_source_channel:'+channel)
    masks=('leader_state','follower_measured','requested_action','accepted_action','dispatched_action')
    if any(any((sample.get('source_valid_masks') or {}).get(channel)!=[True]*6 for channel in masks) for sample in raw['samples']):reasons.append('source_measurement_or_command_mask_invalid')
    if 'leader_measurement_clock_not_verified' in evidence['missing_facts']:reasons.append('leader_measurement_clock_not_verified')
    if not evidence['source_boot_id'] or len(evidence['camera_ids'])!=1 or any(not value['recorded'] for value in evidence['calibration'].values()):reasons.append('source_camera_boot_motor_calibration_not_verified')
    if evidence['legacy_hold_only']:reasons.append('hold_recording_is_not_object_expert')
    if (evidence.get('source_skill_id'),evidence.get('source_skill_version'),evidence.get('source_skill_spec_sha256'))!=(spec['skill_id'],spec['version'],digest(spec)):
        reasons.append('original_skill_version_spec_context_missing_or_mismatched')
    if evidence.get('source_session_id')!=binding['session_id'] or evidence.get('source_scene_id')!=binding['scene_id'] or evidence['split']!=binding['split'] or not binding.get('object_instance_id'):
        reasons.append('original_scene_session_object_split_not_verified')
    if not evidence['operator_success']:reasons.append('source_operator_outcome_not_success')
    if not review or review.get('binding_sha256')!=binding['receipt_sha256']:reasons.append('bound_operator_video_review_missing')
    else:
        if not all(review.get(key) is True for key in ('observed_recording','object_and_goal_visible','full_task_confirmed')) or review['outcome']!='success':reasons.append('whole_task_video_success_not_reviewed')
        ordered=[review['phase_intervals'].get(phase) for phase in spec['phase_ids']]
        if any(review['phase_outcomes'].get(phase)!='success' for phase in spec['phase_ids']) or any(interval is None for interval in ordered):reasons.append('whole_task_successful_phase_labels_missing')
        elif ordered[0][0]!=0 or ordered[-1][1]!=evidence['frames']-1 or any(a[1]+1!=b[0] for a,b in zip(ordered,ordered[1:])) or any(interval[1]>=evidence['frames'] for interval in ordered):reasons.append('phase_labels_do_not_cover_exact_original_timeline')
    return dict(binding=binding,review=review,accepted=not reasons,reasons=reasons,evidence=evidence,source_path=str(path),source=raw,
        source_kind='SIMULATION' if evidence['source_kind'] in ('software_fixture','simulation','SIMULATION') else 'SO101_RECORDED_DEMONSTRATIONS')


def _decisions(store,spec):
    with store.connect() as db:rows=_records(db)
    bindings=[row for row in _latest(rows,'binding','original_episode_id').values() if row['skill_id']==spec['skill_id'] and row['skill_version']==spec['version']]
    reviews=_latest(rows,'review','original_episode_id');decisions=[_decision(store,spec,row,reviews.get(row['original_episode_id'])) for row in bindings]
    groups={}
    for row in decisions:
        if row['accepted']:
            binding=row['binding'];evidence=row['evidence']
            keys=[('episode',binding['original_episode_id']),('session',binding['session_id']),('scene',binding['scene_id']),('object',binding['object_instance_id'])]
            keys += [('frame',tuple(value)) for value in evidence['frame_ids']]+[('RGB',value) for value in evidence['RGB_sha256']]
            row['_groups']=keys
            for key in keys:groups.setdefault(key,set()).add(binding['split'])
    for row in decisions:
        if any(len(groups[key])>1 for key in row.pop('_groups',[])):row['accepted']=False;row['reasons'].append('original_frame_content_scene_session_or_object_crosses_splits')
    return decisions,rows


def _cohort_fingerprint(spec_sha256,decisions):
    return digest(dict(spec=spec_sha256,sources=[dict(binding=row['binding'],review=row['review'],
        **({'source_capture_pins':_capture_pins(row['evidence'])} if row.get('evidence',{}).get('source_capture') else {}))
        for row in sorted(decisions,key=lambda row:row['binding']['original_episode_id']) if row['accepted']]))


def state_for_skill(store,skill_id,version=None):
    chosen=_chosen(store,skill_id,version);spec=chosen['spec'];decisions,rows=_decisions(store,spec)
    reports=[dict(original_episode_id=row['binding']['original_episode_id'],variant_id=row['binding']['variant_id'],skill_id=skill_id,skill_version=spec['version'],
        split=row['binding']['split'],source_quality_usable=row['accepted'],operator_success=row['accepted'],
        phase_outcomes=(row.get('review') or {}).get('phase_outcomes',{}),target_phase_ids=spec['phase_ids'],episode_scope=row['binding']['episode_scope'],
        independent_full_task_success=row['accepted'],outcome_basis='source_bound_operator_video_review_not_model_physical_validation') for row in decisions]
    plan=catalogue_module.demonstration_plan(spec,reports,receipt_verifier=lambda kind,report,selected:report in reports and report['source_quality_usable'])
    ready=chosen['confirmed'] and plan['covered_variants']==plan['planned_variants']
    training=[row for row in store.jobs(10000) if row['kind']=='so101_skill_train' and row.get('config',{}).get('skill_id')==skill_id and row['config'].get('skill_version')==spec['version']]
    return dict(schema='explorer_so101_skill_workflow_v1',**chosen,cohort_fingerprint=_cohort_fingerprint(chosen['spec_sha256'],decisions),plan=plan,demonstration_plan=plan,data_ready=ready,train_ready=ready,
        assignments=[dict(binding=row['binding'],review=row.get('review'),accepted=row['accepted'],reasons=row['reasons'],
            evidence=dict({key:row.get('evidence',{}).get(key) for key in ('source_kind','frames','missing_facts','recorded_channels','operator_outcome','legacy_hold_only','source_experiment_identity','import_job_identity')},
                **({'source_capture':row['evidence']['source_capture']} if row.get('evidence',{}).get('source_capture') else {}))) for row in decisions],
        sessions=[row for row in rows if row.get('type')==PREFIX+'session' and row['skill_id']==skill_id and row['skill_version']==spec['version']],
        counts=dict(assigned_originals=len(decisions),accepted_originals=sum(row['accepted'] for row in decisions),rejected_originals=sum(not row['accepted'] for row in decisions)),
        training_jobs=training,action_contract_id=CONTRACT,physical_execution_authorized=False,physical_calibration_required_for_grasp_geometry=True,
        training_blockers=[] if ready else ['confirmed_spec_and_all_planned_whole_episode_variants_required'],next_action=plan['next_action'],**experiment_identity(SOURCE_ROLE))


status=state_for_skill


def _build_aggregate_locked(store,skill_id,version):
    state=state_for_skill(store,skill_id,version)
    if not state['data_ready']:raise WorkflowBlocked(state['training_blockers'])
    spec=state['spec'];decisions,_=_decisions(store,spec);accepted=[row for row in decisions if row['accepted']]
    fingerprint=_cohort_fingerprint(state['spec_sha256'],accepted)
    root=Path(store.root)/'datasets/lab-skills'/skill_id/('v'+str(version).zfill(4))/fingerprint;outer=root/'skill-manifest.json'
    if outer.exists():
        saved=json.loads(outer.read_text())
        for name,expected in saved['source_files_sha256'].items():
            if _sha(_local(store,root/name))!=expected:raise ValueError('Immutable aggregate artifact changed')
        result=dict(saved['receipt'],aggregate_manifest=str(outer),aggregate_manifest_sha256=_sha(outer))
        with store.connect() as db:_append(db,'aggregate',result,fingerprint)
        return result
    _,qualify=_sim()
    bindings=[dict(manifest_path=row['source_path'],manifest_sha256=row['binding']['source_manifest_sha256'],accepted=True,skill_id=skill_id,skill_version=version,split=row['binding']['split'],
        **(dict(capture_manifest_path=row['evidence']['source_capture']['capture_manifest_path'],**_capture_pins(row['evidence'])) if row['evidence'].get('source_capture') else {})) for row in accepted]
    capture_bindings={row['binding']['original_episode_id']:_capture_pins(row['evidence']) for row in accepted if row['evidence'].get('source_capture')}
    qualification=root/'native-data/qualification.json'
    if qualification.is_file():
        qualified=json.loads(qualification.read_text())
        if qualified['skill_id']!=skill_id or qualified['skill_version']!=version or qualified['source_manifests_sha256']!={row['binding']['original_episode_id']:row['binding']['source_manifest_sha256'] for row in accepted}:raise ValueError('Interrupted aggregate belongs to another source cohort')
        if qualified.get('source_capture_bindings',{})!=capture_bindings:raise ValueError('Interrupted aggregate capture/review pins changed')
        if _sha(root/'native-data/manifest.json')!=qualified['aggregate_manifest_sha256']:raise ValueError('Interrupted aggregate manifest changed')
    else:qualified=qualify(bindings,root/'native-data',skill_id=skill_id,skill_version=version,task_type='joint_trajectory')
    current,_=_decisions(store,spec)
    if _cohort_fingerprint(state['spec_sha256'],current)!=fingerprint:raise ValueError('Accepted source/review cohort changed during aggregate build')
    sidecars=root/'source-reviews.json';sidecars.write_bytes(canonical([_source_snapshot(row) for row in accepted]))
    sources={str(path.relative_to(root)):_sha(path) for path in root.rglob('*') if path.is_file()}
    kinds={row['source_kind'] for row in accepted};source_kind='SIMULATION' if 'SIMULATION' in kinds else 'SO101_RECORDED_DEMONSTRATIONS'
    receipt=dict(schema='explorer_so101_accepted_dataset_v1',owner_experiment=SOURCE_PROJECT,action_contract_id=CONTRACT,skill_id=skill_id,skill_version=version,**experiment_identity(SOURCE_ROLE),
        spec_sha256=state['spec_sha256'],cohort_fingerprint=fingerprint,source_kind=source_kind,accepted_train_original_episode_ids=qualified['accepted_train_original_episode_ids'],
        accepted_development_original_episode_ids=qualified['accepted_development_original_episode_ids'],sealed_original_episode_ids=qualified['sealed_original_episode_ids'],
        full_teaching_provenance_ready=True,object_expert_eligible=True,global_split_groups_verified=True,
        expert_basis='source_native_provenance_and_operator_video_phase_review_for_BC_not_model_physical_validation',
        teaching_provenance_scope='ACT6_RGB_measured_state_issued_command_and_operator_phase_outcome_labels',
        metric_table_camera_calibration_is_separate_physical_execution_blocker=True,
        official_dataset_readback_verified=qualified['official_dataset_readback_verified'],physical_success_inferred=False)
    if capture_bindings:receipt['source_capture_bindings']=capture_bindings
    manifest=dict(schema='explorer_so101_skill_dataset_v1',skill_id=skill_id,skill_version=version,skill_spec=spec,spec_sha256=state['spec_sha256'],
        action_contract_id=CONTRACT,action_names=list(NAMES),action_units=UNITS,source_kind=source_kind,source_files_sha256=sources,**experiment_identity(SOURCE_ROLE),
        internal_view='native-data/manifest.json',official_dataset='native-data/lerobot_v3',receipt=receipt,
        source_calibration_metadata=json.loads((root/'native-data/manifest.json').read_text())['source_calibration_metadata'],physical_execution_allowed=False)
    outer.write_bytes(canonical(manifest));result=dict(receipt,aggregate_manifest=str(outer),aggregate_manifest_sha256=_sha(outer))
    with store.connect() as db:_append(db,'aggregate',result,fingerprint)
    return result


def build_aggregate(store,skill_id,version):
    chosen=_chosen(store,skill_id,version)
    parent=Path(store.root)/'datasets/lab-skills'/chosen['spec']['skill_id']/('v'+str(chosen['spec']['version']).zfill(4))
    parent.mkdir(parents=True,exist_ok=True)
    with (parent/'aggregate.lock').open('a') as owner:
        fcntl.flock(owner.fileno(),fcntl.LOCK_EX)
        return _build_aggregate_locked(store,skill_id,version)


def enqueue_training(store,skill_id,version,body=None):
    from .training_options import so101_training_settings
    body=body or {};settings=so101_training_settings(body)
    if settings['backend']=='diffusion':
        from .policy_runtime import qualified_diffusion_runtime
        qualified_diffusion_runtime()
    aggregate=build_aggregate(store,skill_id,version)
    config=dict(settings,skill_id=skill_id,skill_version=version,spec_sha256=aggregate['spec_sha256'],**experiment_identity(SOURCE_ROLE),
        aggregate_manifest=aggregate['aggregate_manifest'],aggregate_manifest_sha256=aggregate['aggregate_manifest_sha256'],source_kind=aggregate['source_kind'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');request=body.get('request_id')
        previous=next((row for row in _records(db) if row.get('type')==PREFIX+'training' and request and row.get('request_id')==request),None)
        if previous:
            if previous['config']!=config:raise ValueError('Training request identity changed')
            job_id=previous['job_id']
        else:
            job_id=uuid.uuid4().hex;now=time.time()
            db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
                (job_id,'so101_skill_train',SOURCE_PROJECT,'queued',now,now,json.dumps(config,allow_nan=False)))
            _append(db,'training',dict(skill_id=skill_id,skill_version=version,job_id=job_id,request_id=request,config=config),request)
    return store.job(job_id)


def model_readiness(store,skill_id,version=None):
    state=state_for_skill(store,skill_id,version);spec=state['spec'];receipts={}
    with store.connect() as db:rows=_records(db)
    aggregates=[row for row in rows if row.get('type')==PREFIX+'aggregate' and row['skill_id']==skill_id and row['skill_version']==spec['version']]
    if aggregates:
        candidate={key:value for key,value in aggregates[-1].items() if key!='type'}
        try:
            path=_local(store,candidate['aggregate_manifest']);document=json.loads(path.read_text())
            intact=_sha(path)==candidate['aggregate_manifest_sha256'] and all(_sha(_local(store,path.parent/name))==expected for name,expected in document['source_files_sha256'].items())
            if state['data_ready'] and candidate.get('cohort_fingerprint')==state['cohort_fingerprint'] and intact:receipts['demos']=candidate
        except (OSError,ValueError,KeyError):pass
    for job in state['training_jobs']:
        result=job.get('result') or {}
        if job['status']=='completed' and result.get('trained') and result.get('save_reload_passed') and result.get('weights_changed') and result.get('optimizer_steps',0)>0:
            try:
                checkpoint=_local(store,result['checkpoint_dir']+'/config.json').parent
                if any(_sha(checkpoint/name)!=expected for name,expected in result['checkpoint_sha256'].items()):raise ValueError('Checkpoint changed')
                receipts['trained']=dict(skill_id=skill_id,skill_version=spec['version'],owner_experiment=SOURCE_PROJECT,action_contract_id=CONTRACT,**experiment_identity(SOURCE_ROLE),
                    trained=True,framework='LeRobot',framework_version='0.6.1',checkpoint_sha256=digest(result['checkpoint_sha256']),
                    aggregate_manifest_sha256=result['aggregate_manifest_sha256'],source_kind=result['source_kind'],job_id=job['id'])
                break
            except (OSError,ValueError,KeyError):pass
    readiness=catalogue_module.readiness(spec,confirmed=state['confirmed'],receipts=receipts,receipt_verifier=lambda kind,receipt,selected:receipt==receipts.get(kind))
    return dict(readiness,receipts=receipts,physical_execution_authorized=False)


def review_timeline(store,import_job_id,*,after=0,limit=100):
    job,path,raw,evidence=_source(store,import_job_id)
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=200:raise ValueError('Bounded original timeline cursor required')
    rows=[]
    for index,sample in enumerate(raw['samples'][after:after+limit],after):
        rows.append({key:sample.get(key) for key in ('state','requested_action','accepted_action','dispatched_action','leader_state','leader_state_monotonic_ns','state_monotonic_ns','action_monotonic_ns','clock_domain','boot_id','source_valid_masks','dispatch_receipt','source_context')})
        rows[-1].update(source_sample_index=index,frame_id=sample['frame']['frame_id'],frame_sha256=sample['frame']['sha256'],
            frame_metadata=sample['frame'],frame_url='/api/lab/skills/imports/'+import_job_id+'/frame/'+str(index))
    return dict(schema='explorer_so101_source_review_timeline_v1',original_episode_id=evidence['episode_id'],source_manifest_sha256=evidence['source_manifest_sha256'],
        total_samples=len(raw['samples']),rows=rows,next_after=after+len(rows) if after+len(rows)<len(raw['samples']) else None,
        fps=raw['fps'],action_names=list(NAMES),action_units=UNITS,source_boot_id=evidence['source_boot_id'],camera_ids=evidence['camera_ids'],
        motor_calibration=evidence['calibration'],camera_calibration=raw.get('camera_calibration'),
        source_video_is_original_JPEG_sequence=True,physical_success_inferred=False)


def review_frame(store,import_job_id,index):
    _,path,raw,_=_source(store,import_job_id)
    if type(index) is not int or not 0<=index<len(raw['samples']):raise ValueError('Original source frame index outside recording')
    descriptor=raw['samples'][index]['frame'];member=(path.parent/descriptor['file']).resolve();member=_local(store,member)
    if not member.is_relative_to(path.parent) or _sha(member)!=descriptor['sha256']:raise ValueError('Original reviewed JPEG changed')
    return member,descriptor['sha256']
