"""All state and synthetic source bytes live only under pytest temporary paths."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import uuid

import numpy as np
import pytest

from explorer.store import Store
from explorer import skill_workflow as service
from explorer.mobile_dataset_evidence import inspect_mobile_import
from explorer.skill_workflow import (
    MANIPULATION_STAGES,PRESETS,UNIFIED_ACTION_CONTRACT,WorkflowBlocked,
    attach_skill_lineage,available_imports,begin_capture,bind_episode,bind_import_from_capture,
    build_aggregate,capture_finished,capture_for_episode,capture_stage,capture_started,
    confirm_skill,create_session,create_skill,enqueue_training,list_skills,proposal,
    revise_skill,training_request,validate_02_training_request,verify_capture_episode,workflow,
)
from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS
from test_embodied_episode import command_estimate_facts,encoded,package,source_facts


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def skill(store,preset='sock_to_basket'):
    chosen=create_skill(store,dict(preset=preset,request_id=str(uuid.uuid4())))
    return confirm_skill(store,chosen['spec']['skill_id'],1,chosen['spec_sha256'])


def imported(store,selected,name,*,outcome='success',quality=True,split='train',observation_only=False,
             source_session=None,source_scene=None,source_object=None,scope='whole_episode',phases=None,legacy_view=False,frame_prefix=None,rgb_content_key=None,
             project_id='02',job_kind='mobile_import',command_estimate=False):
    """Actual canonical parser receives an entirely synthetic source-bound record."""
    directory=store.root/'datasets'/name;directory.mkdir(parents=True)
    facts,images=command_estimate_facts() if command_estimate else source_facts();phases=list(phases or MANIPULATION_STAGES)
    images['rgb.bin']+=(rgb_content_key or name).encode()
    facts['samples'][0]['channels']['camera_rgb']['value']['sha256']=hashlib.sha256(images['rgb.bin']).hexdigest()
    facts.update(episode_id=name,run_id='fixture-run-'+name,episode_scope=scope,project_id=project_id)
    facts['skill']=dict(id=selected['spec']['skill_id'],version=selected['spec']['version'],variant_id='declared-source-variant',session_id=source_session or 'source-session-'+name)
    facts['recording'].update(operator_outcome=outcome,source_quality_usable=quality)
    facts['split'].update(partition=split,episode_id=name,session_id=facts['skill']['session_id'],scene_id=source_scene or 'source-scene-'+name,object_instance_id=source_object or 'source-object-'+name,group_id='group-'+name)
    initial_sample=deepcopy(facts['samples'][0]);facts['samples']=[]
    for index,phase in enumerate(phases):
        sample=deepcopy(initial_sample);sample['sample_id']='sample-'+str(index)
        sample['action']['stamp']['monotonic_ns']=20+index;sample['action']['delivery']['stamp']['monotonic_ns']=20+index
        for channel in sample['channels'].values():
            if isinstance(channel.get('stamp'),dict):channel['stamp']['monotonic_ns']=10+index
        frame_id='rgb-'+(frame_prefix or name)+'-'+str(index)
        sample['channels']['camera_rgb']['value']['frame_id']=frame_id
        if isinstance(sample['channels']['camera_depth'].get('value'),dict):sample['channels']['camera_depth']['value']['aligned_to_rgb_frame_id']=frame_id
        if isinstance(sample['channels']['detections'].get('value'),dict):sample['channels']['detections']['value']['frame_id']=frame_id
        facts['samples'].append(sample)
    facts['phases']=[dict(id=phase,start_sample=index,end_sample=index,transition_reason='synthetic completed phase',recovery_of=None) for index,phase in enumerate(phases)]
    independent=deepcopy(facts['outcomes']['end_to_end']);independent['status']=outcome
    facts['outcomes']=dict(end_to_end=deepcopy(independent),per_phase={phase:deepcopy(independent) for phase in phases})
    facts['identity']={key:deepcopy(facts[key]) for key in ('episode_id','run_id','project_id','source_kind','skill','episode_scope','action_contract')}
    envelope,payloads=package(facts,images)
    for filename,data in payloads.items():(directory/filename).write_bytes(data)
    envelope_path=directory/'original-envelope.json';envelope_path.write_bytes(b' \n'+encoded(envelope)+b'\n')
    result=dict(episode_id=name,embodied_manifest=None if observation_only else str(envelope_path),frames=1)
    if legacy_view:
        low=np.array([0,0,0,0,0,30,-.8,-.72,-1.67],np.float32);high=np.array([180,180,180,180,270,180,.8,.72,1.67],np.float32)
        count=16;native=np.tile(np.array([90,90,90,90,90,80,0,0,0],np.float32),(count,1))
        ticks=np.arange(1,count+1,dtype=np.int64)*100000000
        archive=directory/'episode.npz'
        np.savez_compressed(archive,rgb=np.random.default_rng(int(hashlib.sha256(name.encode()).hexdigest()[:8],16)).integers(0,255,(count,24,32,3),dtype=np.uint8),state=2*(native-low)/(high-low)-1,
            state_native=native,action_native=native,frame_monotonic_ns=ticks,state_monotonic_ns=ticks+1000,action_monotonic_ns=ticks+2000)
        manifest=dict(schema_version=1,format='explorer_robot_episode_v1',dataset_id='fixture-'+name,source='explicit_synthetic_component_view',
            action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),action_normalization=dict(low=low.tolist(),high=high.tolist()),
            state_basis='command_estimate',action_basis='host_reported_target_snapshot',source_camera_feature='wrist_rgb',clock_domain='fixture-clock-'+name,fps=10,
            training_admission=dict(source_quality_usable=True,operator_success_label=True),
            episodes=[dict(id=name,file=archive.name,sha256=sha(archive),source_episode_id=name,split=split,frames=count,outcome='success',frame_source_ids=[name+':'+str(i) for i in range(count)],clock_domain='fixture-clock-'+name)])
        legacy=directory/'explicit-component.json';legacy.write_bytes(encoded(manifest));result['fixture_legacy_view']=str(legacy)
    job=store.create_job(job_kind,project_id,dict(episode_id=name));store.complete_job(job['id'],result)
    return store.job(job['id']),envelope_path


def component_inspector(job,*,managed_root=None,include_manifest=False,**unused):
    """The real canonical inspector plus an explicitly qualified test-only NPZ view.

    This does not assert that the production converter accepted synthetic NPZ.
    """
    report=inspect_mobile_import(job,managed_root=managed_root,include_manifest=include_manifest)
    candidate=job['result'].get('fixture_legacy_view')
    if candidate:
        path=Path(candidate);manifest=json.loads(path.read_text())
        assert path.resolve().is_relative_to(Path(managed_root).resolve())
        for entry in manifest['episodes']:assert sha(path.parent/entry['file'])==entry['sha256']
        report.update(episodes=manifest['episodes'],legacy_RGB9D_contract_admitted=True)
        report['legacy_RGB9D_view'].update(manifest=manifest,manifest_path=str(path),manifest_sha256=sha(path),legacy_RGB9D_contract_admitted=True)
    return report


def assign(store,selected,job,variant,session,**extra):
    body=dict(import_job_id=job['id'],session_id=session['session_id'],completed_stages=list(MANIPULATION_STAGES),full_task_confirmed=True,object_and_goal_visible=True)
    body.update(extra)
    return bind_episode(store,selected['spec']['skill_id'],selected['spec']['version'],variant,body,inspector=component_inspector)


def prepared(store,*,legacy_view=False):
    selected=skill(store);identity=selected['spec']['skill_id']
    sessions={split:create_session(store,identity,1,dict(split=split,scene='declared-scene-'+split,description='explicit synthetic operator session')) for split in service.SPLITS}
    rows=[]
    for index,variant in enumerate(selected['spec']['coverage_plan']['variants']):
        job,path=imported(store,selected,'episode-'+str(index),split=variant['split'],legacy_view=legacy_view)
        binding=assign(store,selected,job,variant['variant_id'],sessions[variant['split']]);rows.append((job,path,binding))
    return selected,sessions,rows


def test_templates_custom_and_revision_never_offer_partial_robot_contract(tmp_path):
    for preset in PRESETS:
        spec=proposal(preset=preset)
        assert spec['status']=='PROPOSED' and uuid.UUID(spec['skill_id'])
        assert spec['resources']==['arm','gripper','base'] and spec['embodiment']=='rosmaster_unified_mobile_manipulator'
        assert spec['action_profile']['schema']==UNIFIED_ACTION_CONTRACT and spec['action_profile']['neutral_channels_retained']
        assert spec['action_profile']['names']==list(ACTION_NAMES) and spec['state_profile']['preceding_command9']['manufactured_from_joints_and_odometry'] is False
        assert set(spec['observation_contract']['channels'])==set(service.OBSERVATION_CHANNELS)
        assert len(spec['coverage_plan']['variants'])==5 and spec['constraints']['chassis_motion_forbidden']
        assert 'implemented' not in spec['pipeline'] and spec['pipeline']['availability_basis']=='current_runtime_pipeline_overlay_not_immutable_task_fact'
        assert not spec['pipeline']['arbitrary_Russian_instruction_conditioning']
    custom=proposal('Переложи красный носок в синюю корзину')
    assert custom['task_type']=='mobile_manipulation' and custom['object']['name']=='красный носок'
    assert custom['goal']['name']=='синюю корзину' and 'not_certain_NLU' in custom['proposal_basis']
    assert proposal('Сделай красиво')['task_type']=='unknown'
    store=Store(tmp_path);selected=skill(store)
    for patch in (dict(resources=['arm']),dict(action_profile=dict(dimension=6)),dict(task_type='manipulation'),dict(task_type='base_only')):
        with pytest.raises(ValueError):revise_skill(store,selected['spec']['skill_id'],1,patch,selected['spec_sha256'])


def test_versions_confirmation_idempotence_and_immutable_operator_constraints(tmp_path):
    store=Store(tmp_path);body=dict(preset='sock_to_basket',request_id='repeatable-request')
    first=create_skill(store,body);assert create_skill(store,body)==first
    with pytest.raises(ValueError):create_skill(store,dict(body,preset='navigate'))
    identity=first['spec']['skill_id'];confirm_skill(store,identity,1,first['spec_sha256'])
    second=revise_skill(store,identity,1,dict(title='Носок в большую корзину'),first['spec_sha256'])
    assert second['spec']['skill_id']==identity and second['spec']['version']==2 and not second['confirmed']
    assert len(list_skills(Store(tmp_path)))==2
    with pytest.raises(ValueError,match='refresh'):revise_skill(store,identity,1,dict(title='stale'),first['spec_sha256'])
    with pytest.raises(ValueError,match='controller'):revise_skill(store,identity,2,dict(constraints=dict(chassis_motion_forbidden=False)),second['spec_sha256'])


def test_source_grade_unknown_failure_and_observation_only_never_become_expert_targets(tmp_path):
    store=Store(tmp_path);selected=skill(store);identity=selected['spec']['skill_id']
    session=create_session(store,identity,1,dict(split='train',scene='fixture scene'))
    for index,options in enumerate((dict(observation_only=True),dict(outcome='unknown'),dict(outcome='failure'),dict(quality=False))):
        job,_=imported(store,selected,'rejected-'+str(index),**options);assign(store,selected,job,'variant-001',session)
    state=workflow(store,identity,1,inspector=component_inspector)
    assert state['counts']['accepted_originals']==0 and state['counts']['rejected_originals']==4
    assert state['next_action']['kind']=='repeat_rejected' and state['next_action']['repeat_number']==5
    assert state['next_action']['show_number']==1 and state['next_action']['initial_technical_target']==5
    assert not state['train_ready'] and not state['skill_mastery_claimed']


def test_review_cannot_invent_missing_source_phases_or_upgrade_targeted_to_whole(tmp_path):
    store=Store(tmp_path);selected=skill(store);identity=selected['spec']['skill_id']
    session=create_session(store,identity,1,dict(split='train',scene='fixture scene'))
    job,_=imported(store,selected,'partial-source',scope='targeted_subskill',phases=['grasp_confirm'])
    with pytest.raises(ValueError,match='every full-task target phase'):assign(store,selected,job,'variant-001',session,target_phase_ids=['grasp_confirm'])
    bound=assign(store,selected,job,'variant-001',session)
    state=workflow(store,identity,1)
    assert not state['assignments'][0]['accepted']
    assert 'source_bound_whole_task_success_not_verified' in state['assignments'][0]['reasons']
    targeted=assign(store,selected,job,'variant-001',session,episode_scope='targeted_subskill',target_phase_ids=['grasp_confirm'],completed_stages=['grasp_confirm'],phase_outcomes={'grasp_confirm':'success'},full_task_confirmed=False,expected_binding_sha256=bound['binding_sha256'])
    state=workflow(store,identity,1)
    assert state['counts']['accepted_originals']==1 and state['counts']['covered_variants']==0
    assert state['phase_variant_coverage']['grasp_confirm']['variant-001']==1
    assert state['variants'][0]['targeted_or_recovery_accepted']==1
    assert targeted['source_contract_status']=='CANONICAL_EMBODIED_VERIFIED'


def test_complete_review_visibility_and_source_skill_identity_are_independent_gates(tmp_path):
    store=Store(tmp_path);selected=skill(store);identity=selected['spec']['skill_id']
    session=create_session(store,identity,1,dict(split='train',scene='fixture'))
    job,_=imported(store,selected,'review-source')
    bound=assign(store,selected,job,'variant-001',session,completed_stages=['grasp_confirm'])
    assert not workflow(store,identity,1)['assignments'][0]['accepted']
    with pytest.raises(ValueError,match='refresh'):assign(store,selected,job,'variant-001',session)
    bound=assign(store,selected,job,'variant-001',session,expected_binding_sha256=bound['binding_sha256'])
    assert workflow(store,identity,1)['assignments'][0]['accepted']
    another=skill(store,'object_a_to_b');other=create_session(store,another['spec']['skill_id'],1,dict(split='train',scene='fixture'))
    assign(store,another,job,'variant-001',other,expected_binding_sha256=bound['binding_sha256'])
    assert workflow(store,identity,1)['counts']['assigned_originals']==0
    result=workflow(store,another['spec']['skill_id'],1)
    assert not result['assignments'][0]['accepted'] and 'source_bound_skill_identity_differs_legacy_not_silently_upgraded' in result['assignments'][0]['reasons']


def test_all_canonical_train_dev_aggregate_preserves_original_envelope_payloads_and_neutral_nine(tmp_path):
    store=Store(tmp_path);selected,_,rows=prepared(store);identity=selected['spec']['skill_id']
    state=workflow(store,identity,1)
    assert state['data_ready'] and not state['train_ready'] and state['counts']['covered_variants']==5
    assert state['next_action']['kind']=='resolve_source_inputs'
    assert state['runtime_pipeline']['implemented'] and state['runtime_pipeline']['job_kind']=='rosmaster_skill_train'
    assert all('EXPLICIT_RECORDED_PRIOR_COMMAND9_REQUIRED' in row['blockers'] for row in state['source_preflight'])
    before={str(path):sha(path) for _,path,_ in rows}
    result=build_aggregate(store,identity,1);manifest=result['manifest'];aggregate=Path(result['dataset_manifest'])
    assert manifest['schema']=='explorer_unified_embodied_skill_dataset_v1' and len(manifest['episodes'])==4
    assert manifest['action_names']==list(ACTION_NAMES) and manifest['neutral_channels_retained']
    assert manifest['cross_episode_split_checked'] and not manifest['preceding_command9_manufactured']
    assert result['internal_legacy_component_view'] is None and len(manifest['sealed_sources_excluded'])==1
    assert not any(row['source_episode_id']=='episode-4' for row in manifest['episodes'])
    assert build_aggregate(store,identity,1)['dataset_manifest']==result['dataset_manifest']
    assert all(sha(path)==expected for path,expected in before.items())
    for row in manifest['episodes']:
        source=next(path for job,path,_ in rows if job['config']['episode_id']==row['source_episode_id'])
        copied=aggregate.parent/row['source_artifact_map']['__envelope__/original-document.json']
        assert copied.read_bytes()==source.read_bytes()
        envelope=json.loads(copied.read_text());action=envelope['samples'][0]['action']
        assert action['values']==[0.]*9 and action['mask']==[True]*9
        assert set(row['recorded_admitted_model_used_channels'])>=set(service.OBSERVATION_CHANNELS)
    for filename,expected in manifest['source_files_sha256'].items():assert sha(aggregate.parent/filename)==expected
    assert not set(result['sealed_original_episode_ids']) & set(result['training_original_episode_ids']+result['development_original_episode_ids'])


def test_optional_existing_RecordedChunks_view_is_internal_full9_only_not_whole_skill_train(tmp_path):
    from explorer_sim.recorded_training import RecordedChunks
    from explorer_sim.dataset_export import validate_splits
    store=Store(tmp_path);selected,_,_=prepared(store,legacy_view=True)
    result=build_aggregate(store,selected['spec']['skill_id'],1,inspector=component_inspector)
    path=Path(result['internal_legacy_component_view']);manifest=json.loads(path.read_text());validate_splits(manifest)
    assert 'NOT_full_robot_model' in manifest['purpose'] and manifest['action_names']==list(ACTION_NAMES)
    train=[entry for entry in manifest['episodes'] if entry['split']=='train'];dev=[entry for entry in manifest['episodes'] if entry['split']=='development']
    training=RecordedChunks(path,manifest,train,chunk_size=4,image_size=32);validation=RecordedChunks(path,manifest,dev,chunk_size=4,image_size=32)
    assert len(training)==48 and len(validation)==16
    assert training[0]['action'].shape==(4,9) and training[0]['observation.state'].shape==(9,)
    assert not manifest['contains_physical_recordings']
    with pytest.raises(WorkflowBlocked):training_request(store,selected['spec']['skill_id'],1,inspector=component_inspector)


@pytest.mark.parametrize('key',['source_session','source_scene','source_object'])
def test_original_session_scene_object_never_cross_splits_even_with_new_teaching_labels(tmp_path,key):
    store=Store(tmp_path);selected=skill(store);identity=selected['spec']['skill_id']
    training=create_session(store,identity,1,dict(split='train',scene='one'));dev=create_session(store,identity,1,dict(split='development',scene='two'))
    first,_=imported(store,selected,'first',**{key:'shared-original-group'})
    second,_=imported(store,selected,'second',split='development',**{key:'shared-original-group'})
    assign(store,selected,first,'variant-001',training)
    with pytest.raises(ValueError,match='cannot cross'):assign(store,selected,second,'variant-004',dev)


def test_sealed_development_and_previously_fitted_legacy_sources_keep_their_partitions(tmp_path):
    store=Store(tmp_path);selected,sessions,rows=prepared(store);identity=selected['spec']['skill_id']
    for index,message in ((4,'sealed'),(3,'development')):
        with pytest.raises(ValueError,match=message):assign(store,selected,rows[index][0],'variant-001',sessions['train'],expected_binding_sha256=rows[index][2]['binding_sha256'])
    other=Store(tmp_path/'legacy');chosen=skill(other);job,path=imported(other,chosen,'old-source',legacy_view=True)
    other.create_job('train_act','02',dict(dataset_manifest=job['result']['fixture_legacy_view']))
    dev=create_session(other,chosen['spec']['skill_id'],1,dict(split='development',scene='heldout'))
    with pytest.raises(ValueError,match='fitted|training'):assign(other,chosen,job,'variant-004',dev)


def test_source_tamper_duplicate_original_and_frame_reuse_do_not_inflate_coverage(tmp_path):
    store=Store(tmp_path);selected,sessions,rows=prepared(store);identity=selected['spec']['skill_id']
    duplicate=assign(store,selected,rows[0][0],'variant-001',sessions['train'],expected_binding_sha256=rows[0][2]['binding_sha256'])
    assert duplicate['binding_sha256']==rows[0][2]['binding_sha256'] and workflow(store,identity,1)['counts']['accepted_originals']==5
    payload=rows[0][1].parent/'facts.json';payload.write_bytes(b'changed retained source')
    state=workflow(store,identity,1);assert not state['data_ready'] and not state['assignments'][0]['accepted']
    with pytest.raises(WorkflowBlocked):build_aggregate(store,identity,1)


@pytest.mark.parametrize('shared',['frame_identity','RGB_bytes'])
def test_original_rgb_frame_reuse_cannot_hide_behind_new_scene_session_or_episode_ids(tmp_path,shared):
    store=Store(tmp_path);selected=skill(store);identity=selected['spec']['skill_id']
    train=create_session(store,identity,1,dict(split='train',scene='one'));dev=create_session(store,identity,1,dict(split='development',scene='two'))
    common=dict(frame_prefix='reused-camera-frame') if shared=='frame_identity' else dict(rgb_content_key='reused-RGB-bytes')
    first,_=imported(store,selected,'train-frame',**common)
    second,_=imported(store,selected,'dev-frame',split='development',**common)
    assign(store,selected,first,'variant-001',train);assign(store,selected,second,'variant-004',dev)
    state=workflow(store,identity,1)
    assert state['counts']['accepted_originals']==0
    assert all('original_scene_session_object_or_RGB_payload_overlaps_splits' in row['reasons'] for row in state['assignments'])


def test_no_implicit_skill_version_pool_and_model_lineage_does_not_promote_legacy_model(tmp_path):
    store=Store(tmp_path);selected,_,_=prepared(store);identity=selected['spec']['skill_id'];aggregate=build_aggregate(store,identity,1)
    lineage={key:aggregate[key] for key in ('skill_id','skill_version','spec_sha256','aggregate_sha256','aggregate_content_id')}
    config=dict(dataset_manifest=aggregate['dataset_manifest'],skill_workflow=lineage)
    card=dict(id='legacy-model',action_contract='legacy_RGB9D',lineage={},limitations=[],status='trained_offline_only')
    attached=attach_skill_lineage(card,config,aggregate['aggregate_sha256'])
    assert attached['skill_id']==identity and attached['skill_version']==1 and not attached['unified_model_qualified']
    assert attached['action_contract']=='legacy_RGB9D' and card['lineage']=={}
    with pytest.raises(ValueError,match='aggregate'):attach_skill_lineage(card,config,'wrong')
    second=revise_skill(store,identity,1,dict(title='Version two'),selected['spec_sha256'])
    assert workflow(store,identity,second['spec']['version'])['counts']['assigned_originals']==0


def test_no_forged_partial_or_single_clip_training_and_block_does_not_queue_jobs(tmp_path):
    store=Store(tmp_path);selected,_,_=prepared(store);identity=selected['spec']['skill_id'];before=len(store.jobs(10000))
    for body in (dict(project_id='02',kind='train_act',config={}),dict(project_id='02',kind='full_hierarchy',config=dict(skill_workflow=dict(action_contract_id=UNIFIED_ACTION_CONTRACT,skill_id=identity,skill_version=1,spec_sha256=selected['spec_sha256'])))):
        with pytest.raises(WorkflowBlocked):validate_02_training_request(store,body)
        with pytest.raises(WorkflowBlocked):validate_02_training_request(store,body,server_generated=True)
    with pytest.raises(WorkflowBlocked):enqueue_training(store,identity,1)
    assert len(store.jobs(10000))==before
    assert validate_02_training_request(store,dict(project_id='01',kind='unrelated'))


def test_capture_identity_reload_invalid_post_finish_review_and_delayed_import_are_durable(tmp_path):
    store=Store(tmp_path);selected=skill(store);identity=selected['spec']['skill_id'];session=create_session(store,identity,1,dict(split='train',scene='new fixture'))
    with pytest.raises(WorkflowBlocked,match='chassis'):begin_capture(store,identity,1,'variant-001',session['session_id'])
    intent=begin_capture(store,identity,1,'variant-001',session['session_id'],stationary_chassis_confirmed=True)
    assert intent['native_payload_additions']=={} and not intent['automatic_motion']
    capture_started(store,intent['capture_id'],'captured-original')
    assert verify_capture_episode(store,intent['capture_id'],'captured-original')['skill_id']==identity
    with pytest.raises(ValueError,match='another native episode'):verify_capture_episode(store,intent['capture_id'],'wrong-original')
    assert capture_for_episode(Store(tmp_path),'captured-original')['capture_id']==intent['capture_id']
    assert capture_for_episode(store,'unbound-original') is None
    capture_stage(store,intent['capture_id'],'grasp_confirm')
    job,_=imported(store,selected,'captured-original')
    assert bind_import_from_capture(store,job) is None
    with pytest.raises(ValueError,match='identity changed'):capture_finished(store,intent['capture_id'],'wrong-original',dict(outcome='success'))
    done=capture_finished(store,intent['capture_id'],'captured-original',dict(outcome='forged',completed_stages=['unrecognized']))
    assert done['review_rejected_reasons'] and done['outcome']=='unknown'
    bound=bind_import_from_capture(store,job);assert bound['skill_id']==identity
    assert bind_import_from_capture(store,job)==bound
    assert not workflow(store,identity,1)['assignments'][0]['accepted']
    assert available_imports(store)[0]['assigned_to']['skill_id']==identity
    with pytest.raises(ValueError,match='finished capture'):capture_stage(store,intent['capture_id'],'grasp_confirm')


def test_atomic_enqueue_rollback_and_dedup_in_simulated_future_backend(tmp_path,monkeypatch):
    """Only queue transaction mechanics are mocked; no backend qualification claim."""
    store=Store(tmp_path);selected,_,_=prepared(store);identity=selected['spec']['skill_id'];aggregate=build_aggregate(store,identity,1)
    lineage={key:aggregate[key] for key in ('skill_id','skill_version','spec_sha256','aggregate_sha256','aggregate_content_id','training_original_episode_ids','development_original_episode_ids','sealed_original_episode_ids')}
    request=dict(kind='test_only_future_pipeline',project_id='02',config=dict(dataset_manifest=aggregate['dataset_manifest'],skill_workflow=lineage))
    monkeypatch.setattr(service,'training_request',lambda *a,**k:deepcopy(request));monkeypatch.setattr(service,'validate_02_training_request',lambda *a,**k:True)
    with store.connect() as db:db.execute("CREATE TRIGGER reject_training_event BEFORE INSERT ON events WHEN json_extract(NEW.body,'$.payload.type')='skill.workflow.training' BEGIN SELECT RAISE(ABORT,'test rollback'); END")
    with pytest.raises(sqlite3.IntegrityError,match='test rollback'):enqueue_training(store,identity,1)
    assert not [job for job in store.jobs(10000) if job['kind']=='test_only_future_pipeline']
    with store.connect() as db:db.execute('DROP TRIGGER reject_training_event')
    first=enqueue_training(store,identity,1);assert enqueue_training(store,identity,1)['id']==first['id']
    with store.connect() as db:events=service._records(db,'training')
    assert len(events)==1 and events[0]['payload']['job_id']==first['id']
    other=Path(aggregate['dataset_manifest']).with_name('simulated-next-aggregate.json')
    manifest=deepcopy(aggregate['manifest']);manifest['unit_fixture_revision']=2;other.write_bytes(encoded(manifest))
    request['config']['dataset_manifest']=str(other);request['config']['skill_workflow']['aggregate_sha256']=sha(other)
    with pytest.raises(WorkflowBlocked,match='one_active'):enqueue_training(store,identity,1)
    assert len([job for job in store.jobs(10000) if job['kind']=='test_only_future_pipeline'])==1


def test_js_single_train_blocked_and_recovered_capture_cannot_take_another_tabs_labels(tmp_path):
    node=shutil.which('node')
    if node is None:pytest.skip('Node is optional for standalone JS semantic regression')
    store=Store(tmp_path/'state');selected,sessions,_=prepared(store);state=workflow(store,selected['spec']['skill_id'],1)
    fixture=tmp_path/'state.json';fixture.write_text(json.dumps(state))
    script=Path(__file__).parents[1]/'00-control-center/src/explorer/static/skill-workflow.js'
    runner=tmp_path/'check.cjs';runner.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const state=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const context={console,structuredClone,crypto:require('crypto').webcrypto,document:{addEventListener(){}},model:{jobs:[]},array:x=>Array.isArray(x)?x:[],esc:x=>String(x),kv:(a,b)=>a+b,count:x=>x,render(){},toast(){},queueMicrotask(){}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
context.fixture=state;
vm.runInContext(`skillWorkflowUI.loaded=true;skillWorkflowUI.state=fixture;skillWorkflowUI.selected=fixture.spec.skill_id;skillWorkflowUI.entries=[fixture];skillWorkflowResetDraft();`,context);
const html=vm.runInContext('skillWorkflowPanel()',context);
assert.equal((html.match(/data-skill-action="train"/g)||[]).length,1);
assert(/data-skill-action="train" disabled/.test(html));assert(!/arm_only|base_only|value="manipulation"/.test(html));
assert(html.includes('Нейтральные 9D-каналы'));
vm.runInContext(`skillWorkflowUI.session=fixture.sessions.find(x=>x.split==='train').session_id;skillWorkflowUI.stationary=true;`,context);
assert.throws(()=>vm.runInContext('skillWorkflowRecordingContext()',context),/начальную фазу/);
vm.runInContext(`skillWorkflowUI.initialPhase=fixture.spec.stages[0].id;const intent=skillWorkflowRecordingContext();skillWorkflowCaptureStarted('capture-1');skillWorkflowUI.fullReview=true;`,context);
let finish=vm.runInContext(`skillWorkflowFinishContext('success')`,context);assert(finish.full_task_confirmed);
vm.runInContext(`skillWorkflowUI.variant='variant-002'`,context);
assert.throws(()=>vm.runInContext(`skillWorkflowFinishContext('success')`,context),/отличается/);
context.window={location:{pathname:'/experiments/03'}};
const adapter=vm.runInContext('skillWorkflowAdapterContext()',context);assert.equal(adapter.project_id,'03');assert.equal(adapter.input_mode,'so101_leader_retarget');
assert(vm.runInContext('skillWorkflowPanel()',context).includes('03 / Общий навык ROSMASTER'));
context.api=async url=>{assert(url.startsWith('/api/skill-workflow/capture-context?episode_id='));return {capture:{capture_id:'durable-id',skill_id:state.spec.skill_id,version:1,variant_id:'variant-001',session_id:state.sessions.find(x=>x.split==='train').session_id,episode_scope:'whole_episode',target_phase_ids:state.spec.stages.map(x=>x.id)}}};
vm.runInContext('refreshSkillWorkflow=async()=>{}',context);
vm.runInContext(`skillWorkflowRecoverCapture('original-id')`,context).then(value=>{assert.equal(value.capture_id,'durable-id');assert.equal(vm.runInContext('skillWorkflowUI.capture',context),'durable-id')}).catch(error=>{console.error(error);process.exitCode=1});
''')
    checked=subprocess.run([node,str(runner),str(fixture),str(script)],capture_output=True,text=True)
    assert checked.returncode==0,checked.stdout+checked.stderr


def test_02_and_03_share_one_skill_counter_aggregate_and_import_pool(tmp_path):
    store=Store(tmp_path)
    selected=create_skill(store,dict(preset='sock_to_basket',project_id='03',input_mode='so101_leader_retarget',request_id='shared-request'))
    selected=confirm_skill(store,selected['spec']['skill_id'],1,selected['spec_sha256']);identity=selected['spec']['skill_id']
    sessions={split:create_session(store,identity,1,dict(split=split,scene='shared-scene-'+split,project_id='03')) for split in service.SPLITS}
    rows=[]
    for index,variant in enumerate(selected['spec']['coverage_plan']['variants']):
        project='03' if index%2 else '02';kind='retarget_import' if project=='03' else 'mobile_import'
        job,path=imported(store,selected,'mixed-'+str(index),split=variant['split'],project_id=project,job_kind=kind)
        rows.append((job,path,assign(store,selected,job,variant['variant_id'],sessions[variant['split']],project_id='02')))
    first=workflow(store,identity,1,project_id='02');second=workflow(store,identity,1,project_id='03')
    assert first['spec_sha256']==second['spec_sha256'] and first['counts']==second['counts']
    assert first['data_ready'] and second['data_ready'] and first['counts']['accepted_originals']==5
    assert first['context']['input_mode']=='keyboard_gamepad' and second['context']['input_mode']=='so101_leader_retarget'
    assert list_skills(store,project_id='02')==list_skills(store,project_id='03')
    pool=available_imports(store,project_id='03')
    assert len(pool)==5 and {row['source_project_id'] for row in pool}=={'02','03'}
    assert all(row['demonstration_input']['input_mode']=='UNKNOWN' for row in pool)
    aggregate=build_aggregate(store,identity,1);manifest=aggregate['manifest']
    assert {row['source_project_id'] for row in manifest['episodes']}=={'02','03'}
    assert {row['import_kind'] for row in manifest['episodes']}=={'mobile_import','retarget_import'}
    assert manifest['input_mode_is_provenance_not_model_type'] and manifest['workflow_family']==service.WORKFLOW_FAMILY
    assert manifest['compatible_source_projects']==['02','03']
    assert len(manifest['episodes'])==4 and manifest['action_names']==list(ACTION_NAMES)
    assert build_aggregate(store,identity,1)['aggregate_sha256']==aggregate['aggregate_sha256']


def test_shared_03_command_estimate_capture_admits_without_enabling_feedback_and_rejects_raw_leader(tmp_path):
    store=Store(tmp_path);selected=skill(store);identity=selected['spec']['skill_id']
    session=create_session(store,identity,1,dict(split='train',scene='command-estimate-fixture',project_id='03'))
    intent=begin_capture(store,identity,1,'variant-001',session['session_id'],stationary_chassis_confirmed=True,project_id='03')
    assert intent['input_mode']=='so101_leader_retarget' and intent['action_contract_id']==UNIFIED_ACTION_CONTRACT
    assert not intent['native_payload_additions'] and not intent['automatic_motion']
    job,path=imported(store,selected,'retarget-command-estimate',project_id='03',job_kind='retarget_import',command_estimate=True)
    capture_started(store,intent['capture_id'],'retarget-command-estimate')
    capture_finished(store,intent['capture_id'],'retarget-command-estimate',dict(outcome='success',full_task_confirmed=True,object_and_goal_visible=True,completed_stages=list(MANIPULATION_STAGES)))
    binding=bind_import_from_capture(store,job)
    state=workflow(store,identity,1,project_id='03')
    assert binding['source_project_id']=='03' and state['assignments'][0]['accepted'],state['assignments'][0]['reasons']
    report=service._inspector(job,store.root)
    assert report['mobile_embodied']['channels']['joint_feedback']['mask'] is False
    assert report['mobile_embodied']['raw_simultaneous_actions'][0]['values']==[0.]*9
    raw=store.create_job('retarget_import','03',dict(episode_id='raw-leader-archive'))
    store.complete_job(raw['id'],dict(recording_id='raw-leader-archive',samples=12))
    assign(store,selected,store.job(raw['id']),'variant-002',session)
    rejected=next(row for row in workflow(store,identity,1)['assignments'] if row['binding']['original_episode_id']=='raw-leader-archive')
    assert not rejected['accepted'] and rejected['binding']['demonstration_input']['input_mode']=='UNKNOWN'


def test_original_split_groups_are_global_across_02_and_03_not_page_local(tmp_path):
    store=Store(tmp_path);selected=skill(store);identity=selected['spec']['skill_id']
    train=create_session(store,identity,1,dict(split='train',scene='one'));dev=create_session(store,identity,1,dict(split='development',scene='two',project_id='03'))
    first,_=imported(store,selected,'native02',source_object='same-object')
    second,_=imported(store,selected,'native03',project_id='03',job_kind='retarget_import',split='development',source_object='same-object')
    assign(store,selected,first,'variant-001',train)
    with pytest.raises(ValueError,match='cannot cross'):assign(store,selected,second,'variant-004',dev,project_id='03')


def test_both_projects_reject_forged_partial_training_and_share_active_queue_reservation(tmp_path,monkeypatch):
    store=Store(tmp_path);selected,_,_=prepared(store);identity=selected['spec']['skill_id'];aggregate=build_aggregate(store,identity,1)
    for project in ('02','03'):
        with pytest.raises(WorkflowBlocked):service.validate_rosmaster_training_request(store,dict(kind='train_act',project_id=project,config={}))
        with pytest.raises(WorkflowBlocked):training_request(store,identity,1,project_id=project)
    lineage={key:aggregate[key] for key in ('skill_id','skill_version','spec_sha256','aggregate_sha256','aggregate_content_id','training_original_episode_ids','development_original_episode_ids','sealed_original_episode_ids')}
    def fake_request(*args,project_id='02',**kwargs):return dict(kind='test_only_shared_pipeline',project_id=project_id,config=dict(dataset_manifest=aggregate['dataset_manifest'],skill_workflow=lineage))
    monkeypatch.setattr(service,'training_request',fake_request);monkeypatch.setattr(service,'validate_02_training_request',lambda *a,**k:True)
    first=enqueue_training(store,identity,1,project_id='03');second=enqueue_training(store,identity,1,project_id='02')
    assert first['project_id']=='03' and first['id']==second['id']
    with store.connect() as db:events=service._records(db,'training')
    assert len(events)==1 and events[0]['payload']['workflow_family']==service.WORKFLOW_FAMILY


def test_verified_native_adapter_and_source_project_are_preserved_independently_of_registry_page(tmp_path):
    from test_mobile_dataset_evidence import shared_command_estimate_fixture
    job,path=shared_command_estimate_fixture(tmp_path,'retarget_import','03','so101_leader')
    job['project_id']='02'
    report=service._inspector(job,tmp_path)
    assert report['admitted_for_training']
    provenance=service._input_provenance(job,report)
    assert provenance['source_project_id']=='03' and provenance['import_job_project_id']=='02'
    assert provenance['input_mode']=='so101_leader' and provenance['input_mode_verified']
    assert provenance['provenance']['source_sha256']==sha(path.parent/'facts.json')


def test_legacy_spec_immutable_false_pipeline_uses_runtime_overlay_without_changing_SHA(tmp_path):
    store=Store(tmp_path);legacy=proposal(preset='sock_to_basket');identity=legacy['skill_id']
    legacy['pipeline']=dict(pipeline_class=legacy['pipeline']['pipeline_class'],implemented=False,job_kind=None)
    legacy['missing_facts'] += [dict(id=name,required_for='training',reason='Historical code blocker') for name in sorted(service.CODE_BACKEND_FACTS)]
    legacy['missing_facts'].append(dict(id='unresolved_task_contact_definition',required_for='training',reason='A genuine task fact must remain'))
    with store.connect() as db:record=service._append(db,'spec',dict(spec=legacy,request_id='historical-v1',intent=dict(text='',preset='sock_to_basket')))
    selected=confirm_skill(store,identity,1,service.digest(legacy))
    with store.connect() as db:before=db.execute('SELECT body FROM events WHERE event_id=?',(record['event_id'],)).fetchone()['body']
    state=workflow(store,identity,1)
    assert state['spec']==legacy and state['spec_sha256']==selected['spec_sha256']
    assert state['spec']['pipeline']['implemented'] is False
    assert state['runtime_pipeline']['implemented'] and state['runtime_pipeline']['job_kind']=='rosmaster_skill_train'
    assert state['effective_pipeline']['state_dimension']==106 and state['effective_pipeline']['action_dimension']==9
    assert set(state['runtime_pipeline']['resolved_legacy_code_blockers'])==service.CODE_BACKEND_FACTS
    remaining={fact['id'] for fact in state['runtime_missing_facts']}
    assert not service.CODE_BACKEND_FACTS&remaining and 'unresolved_task_contact_definition' in remaining
    assert 'unresolved_task_contact_definition' in state['training_blockers']
    assert not state['train_ready'] and not state['runtime_pipeline']['physical_execution_ready']
    with store.connect() as db:after=db.execute('SELECT body FROM events WHERE event_id=?',(record['event_id'],)).fetchone()['body']
    assert before==after


def test_real_canonical_inputs_enable_one_common_CPU_queue_without_running_an_optimizer(tmp_path):
    from test_rosmaster_skill_pipeline import fixture_aggregate
    path,store=fixture_aggregate(tmp_path);manifest=json.loads(path.read_text());identity=manifest['skill_id']
    state=workflow(store,identity,1,project_id='03')
    assert state['data_ready'] and state['training_data_ready'] and state['train_ready']
    assert all(row['baseline_ready'] for row in state['source_preflight'])
    request=training_request(store,identity,1,dict(act_steps=8,batch_size=2,image_size=32,chunk_size=2),project_id='03')
    assert request['kind']=='rosmaster_skill_train' and request['project_id']=='03'
    assert request['config']['device']=='cpu' and request['config']['skill_workflow']['model_input_state_dimension']==106
    assert request['config']['skill_workflow']['output_action_dimension']==9
    assert service.validate_rosmaster_training_request(store,request,server_generated=True)
    with pytest.raises(WorkflowBlocked):service.validate_rosmaster_training_request(store,request)
    with pytest.raises(WorkflowBlocked,match='DEVICE_NOT_QUALIFIED'):training_request(store,identity,1,dict(device='cuda'))
    before={str(file):sha(file) for file in path.parent.rglob('*') if file.is_file()}
    first=enqueue_training(store,identity,1,dict(act_steps=8,batch_size=2,image_size=32,chunk_size=2),project_id='03')
    second=enqueue_training(store,identity,1,dict(act_steps=8,batch_size=2,image_size=32,chunk_size=2),project_id='02')
    assert first['id']==second['id'] and first['status']=='queued'
    assert len([job for job in store.jobs(10000) if job['kind']=='rosmaster_skill_train'])==1
    assert all(sha(name)==expected for name,expected in before.items())
    assert not (store.root/'jobs'/first['id']/'student_act').exists()
    assert not store.skills()
    node=shutil.which('node')
    if node:
        fixture=tmp_path/'ready-workflow.json';fixture.write_text(json.dumps(state))
        renderer=Path(__file__).parents[1]/'00-control-center/src/explorer/static/skill-workflow.js'
        program="const fs=require('fs'),vm=require('vm'),assert=require('assert');const c={structuredClone,document:{addEventListener(){}},window:{location:{pathname:'/experiments/03'}},model:{jobs:[]},array:x=>Array.isArray(x)?x:[],esc:x=>String(x),kv:(a,b)=>a+b,count:x=>x,render(){},queueMicrotask(){}};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);c.fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));vm.runInContext('skillWorkflowUI.loaded=true;skillWorkflowUI.state=fixture;skillWorkflowResetDraft()',c);const html=vm.runInContext('skillWorkflowPanel()',c);assert.equal((html.match(/data-skill-action=\"train\"/g)||[]).length,1);assert(!/data-skill-action=\"train\" disabled/.test(html));assert(html.includes('реализованы offline ACT9'));"
        result=subprocess.run([node,'-e',program,str(renderer),str(fixture)],capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr


def test_runtime_pipeline_overlay_import_is_controlplane_lightweight():
    root=Path(__file__).parents[1]
    code="import sys;sys.path[:0]="+repr([str(root/'shared'),str(root/'00-control-center/src')])+";from explorer.skill_workflow import runtime_pipeline;p=runtime_pipeline();assert p['implemented'];assert p['job_kind']=='rosmaster_skill_train';assert 'torch' not in sys.modules;assert 'mujoco' not in sys.modules"
    result=subprocess.run([__import__('sys').executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
