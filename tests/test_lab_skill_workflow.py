"""Temporary native-format teaching episodes; no camera, serial or actuator IO."""
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

import numpy as np
from PIL import Image
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from explorer.store import Store
from explorer import lab_skill_workflow as flow
from explorer.lab_skill_api import install_router
from explorer_shared.contracts import digest
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH
from explorer_shared.experiments import experiment_id,experiment_identity
SOURCE_ID=experiment_id('so101_source_learning')


def proposal(store):
    chosen=flow.propose(store,{'template_id':'pick_object_to_container'})
    return flow.confirm(store,chosen['spec']['skill_id'],1,{'spec_sha256':chosen['spec_sha256']})


def native_source(store,chosen,variant,index,*,legacy=False,hold=False,outcome='success',image_offset=None,evidence_fault=None):
    skill=chosen['spec'];session=flow.create_session(store,skill['skill_id'],1,{'scene_id':'scene-'+str(index),'split':variant['split']})
    context=flow.capture_context(store,skill['skill_id'],1,{'variant_id':variant['variant_id'],'session_id':session['session_id'],'object_instance_id':'object-'+str(index)})['record_start_body']
    ident=uuid.uuid4().hex;folder=store.root/'episodes/station'/ident;folder.mkdir(parents=True);rows=[]
    for step in range(8):
        image=folder/(str(step)+'.jpg');Image.fromarray(np.full((24,32,3),(index*35 if image_offset is None else image_offset)+step*2,np.uint8)).save(image)
        stamp=1_000_000_000+step*100_000_000;state=[float(0 if hold else step+j) for j in range(6)];issued=[float(step+j+3) for j in range(6)]
        row={'frame':{'file':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'frame_id':ident+':'+str(step),
            'camera_id':'software-camera-not-physical','monotonic_ns':stamp,'clock_domain':'software-boot-'+ident},
            'state':state,'action':issued,'requested_action':[float(step+j+4) for j in range(6)],'accepted_action':issued,'dispatched_action':issued,
            'leader_state':[float(step+j+1) for j in range(6)],'leader_state_monotonic_ns':stamp+1,'state_monotonic_ns':stamp+1,'action_monotonic_ns':stamp+2,
            'clock_domain':'software-boot-'+ident,'dispatch_receipt':{'available':True,'stage':'host_bus_write_completed','monotonic_ns':stamp+2,'clock_domain':'software-boot-'+ident},
            'source_valid_masks':{key:[True]*6 for key in ('follower_measured','leader_state','requested_action','accepted_action','dispatched_action')}}
        rows.append(row)
    if evidence_fault=='false_mask':rows[0]['source_valid_masks']['leader_state'][0]=False
    if evidence_fault=='leader_future':rows[0]['leader_state_monotonic_ns']=rows[0]['state_monotonic_ns']+1
    source={'schema_version':1,'episode_id':ident,'source_type':'station_demonstration','source_kind':'software_fixture','project_id':SOURCE_ID,**experiment_identity('so101_source_learning'),
        'recording_provenance_version':1 if legacy else 2,'state':'complete','outcome':outcome,'label_source':'software_fixture_operator_label',
        'action_names':list(NAMES),'action_units':UNITS,'normalization':{'low':LOW,'high':HIGH},'fps':10,
        'state_basis':'measured_state','action_basis':'dispatched_command','clock_domain':'software-boot-'+ident,'boot_id':'software-boot-'+ident,
        'calibration_metadata':{role:{'calibration_sha256':hashlib.sha256(role.encode()).hexdigest(),'verified_on_connected_hardware':False} for role in ('leader','follower')},
        'samples':rows,'source_context':{key:context[key] for key in ('skill_id','skill_version','skill_spec_sha256','scene_id','session_id','split','phase_id','task_type','context','project_id','experiment_slug','experiment_numbering_version')},
        **{key:context[key] for key in ('skill_id','skill_version','skill_spec_sha256','scene_id','session_id','split')}}
    if legacy:source.pop('source_context');source.pop('skill_id');source.pop('skill_version');source.pop('skill_spec_sha256')
    path=folder/'manifest.json';path.write_text(json.dumps(source));checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    job=store.create_job('station_import',SOURCE_ID,{'episode_id':ident,**experiment_identity('so101_source_learning')});store.complete_job(job['id'],{'episode_id':ident,'observation_manifest':str(path),'source_manifest_sha256':checksum})
    return job,path,source,session


def assigned(store,chosen,variant,index,**options):
    job,path,source,session=native_source(store,chosen,variant,index,**options)
    binding=flow.bind_episode(store,chosen['spec']['skill_id'],1,{'import_job_id':job['id'],'variant_id':variant['variant_id'],'session_id':session['session_id']})
    phases=chosen['spec']['phase_ids'];intervals={phase:[number,number if number<len(phases)-1 else 7] for number,phase in enumerate(phases)}
    review=flow.review_episode(store,chosen['spec']['skill_id'],1,{'original_episode_id':source['episode_id'],'binding_sha256':binding['receipt_sha256'],
        'outcome':'success','reviewed_by':'software-fixture-not-physical-review','observed_recording':True,'object_and_goal_visible':True,'full_task_confirmed':True,
        'phase_outcomes':{phase:'success' for phase in phases},'phase_intervals':intervals})
    return job,path,source,binding,review


def full_cohort(tmp_path):
    store=Store(tmp_path/'state');chosen=proposal(store)
    sources=[assigned(store,chosen,variant,index) for index,variant in enumerate(chosen['spec']['demo_plan']['variants'])]
    return store,chosen,sources


def test_confirmed_context_and_durable_all_original_coverage_survive_store_reload(tmp_path):
    store,chosen,sources=full_cohort(tmp_path);identity=chosen['spec']['skill_id']
    state=flow.state_for_skill(Store(store.root),identity,1)
    assert state['train_ready'] and state['data_ready'] and state['plan']['covered_variants']==5
    assert state['counts']=={'assigned_originals':5,'accepted_originals':5,'rejected_originals':0}
    assert not state['physical_execution_authorized']
    assert all(row['review']['basis'].startswith('operator_video_review') for row in state['assignments'])
    assert not flow.model_readiness(store,identity,1)['trained']['ready']
    with pytest.raises(ValueError,match='source split'):flow.bind_episode(store,identity,1,{'import_job_id':sources[0][0]['id'],
        'variant_id':chosen['spec']['demo_plan']['variants'][-1]['variant_id'],'session_id':state['sessions'][-1]['session_id']})


@pytest.mark.parametrize('fault',['legacy','hold','failure','unknown'])
def test_legacy_hold_and_failed_or_unknown_sources_remain_analysis_only_despite_successful_review(tmp_path,fault):
    store=Store(tmp_path/'state');chosen=proposal(store);variant=chosen['spec']['demo_plan']['variants'][0]
    options={'legacy':True} if fault=='legacy' else {'hold':True} if fault=='hold' else {'outcome':fault}
    assigned(store,chosen,variant,0,**options);state=flow.state_for_skill(store,chosen['spec']['skill_id'],1)
    assert not state['train_ready'] and not state['assignments'][0]['accepted']
    assert state['plan']['covered_variants']==0 and state['assignments'][0]['reasons']
    with pytest.raises(flow.WorkflowBlocked):flow.build_aggregate(store,chosen['spec']['skill_id'],1)


def test_original_RGB_content_leakage_and_stale_review_or_source_changes_block_admission(tmp_path):
    store,chosen,sources=full_cohort(tmp_path);identity=chosen['spec']['skill_id'];path=sources[3][1]
    source=json.loads(path.read_text());original=sources[0][2]['samples'][0]['frame']
    image=path.parent/source['samples'][0]['frame']['file'];image.write_bytes((sources[0][1].parent/original['file']).read_bytes())
    source['samples'][0]['frame']['sha256']=hashlib.sha256(image.read_bytes()).hexdigest();path.write_text(json.dumps(source))
    state=flow.state_for_skill(store,identity,1)
    assert not state['train_ready'] and any(not row['accepted'] for row in state['assignments'])
    with pytest.raises(ValueError,match='SHA changed'):flow.review_frame(store,sources[3][0]['id'],0)
    with pytest.raises(ValueError,match='exact current'):flow.review_episode(store,identity,1,{'original_episode_id':sources[0][2]['episode_id'],'binding_sha256':'0'*64})


def test_read_only_original_video_joint_timeline_and_isolated_API_router(tmp_path):
    store=Store(tmp_path/'state');chosen=proposal(store);job,path,source,_,_=assigned(store,chosen,chosen['spec']['demo_plan']['variants'][0],0)
    data=flow.review_timeline(store,job['id'],limit=2)
    assert data['total_samples']==8 and data['next_after']==2 and len(data['action_names'])==6
    assert data['rows'][0]['state']!=data['rows'][0]['dispatched_action']
    app=FastAPI();install_router(app,lambda:store);client=TestClient(app)
    assert client.get('/api/lab/skills/catalogue').status_code==200
    assert client.get('/api/lab/skills/'+chosen['spec']['skill_id']+'?version=1').status_code==200
    response=client.get('/api/lab/skills/imports/'+job['id']+'/frame/0')
    assert response.status_code==200 and response.content==(path.parent/source['samples'][0]['frame']['file']).read_bytes()
    assert response.headers['x-content-sha256']==source['samples'][0]['frame']['sha256']
    assert client.post('/api/lab/skills/'+chosen['spec']['skill_id']+'/aggregate',json={}).status_code==400


def test_immutable_all_source_aggregate_actual_official_dataset_and_idempotent_training_enqueue(tmp_path,monkeypatch):
    store,chosen,sources=full_cohort(tmp_path);identity=chosen['spec']['skill_id'];before={str(p):p.read_bytes() for _,path,*_ in sources for p in path.parent.iterdir() if p.is_file()}
    aggregate=flow.build_aggregate(store,identity,1);assert aggregate['official_dataset_readback_verified']
    assert len(aggregate['accepted_train_original_episode_ids'])==3 and len(aggregate['accepted_development_original_episode_ids'])==1 and len(aggregate['sealed_original_episode_ids'])==1
    assert aggregate['source_kind']=='SIMULATION' and not aggregate['physical_success_inferred']
    assert flow.build_aggregate(store,identity,1)==aggregate
    model_view=json.loads((Path(aggregate['aggregate_manifest']).parent/'native-data/manifest.json').read_text())
    assert len(model_view['episodes'])==4 and all(row['split']!='sealed_test' for row in model_view['episodes'])
    body={'request_id':'one-optimizer','act_steps':8,'image_size':32,'chunk_size':4,'batch_size':2}
    job=flow.enqueue_training(store,identity,1,body);assert flow.enqueue_training(store,identity,1,body)['id']==job['id']
    assert len([row for row in store.jobs() if row['kind']=='so101_skill_train'])==1
    assert all(Path(path).read_bytes()==raw for path,raw in before.items())
    with pytest.raises(ValueError,match='identity changed'):flow.enqueue_training(store,identity,1,dict(body,act_steps=9))
    assert flow.model_readiness(store,identity,1)['demos']['ready']
    review=sources[0][4];binding=sources[0][3]
    # An NTP/wall-clock correction cannot make an older successful review win.
    monkeypatch.setattr(flow.time,'time',lambda:1.)
    flow.review_episode(store,identity,1,dict(original_episode_id=sources[0][2]['episode_id'],binding_sha256=binding['receipt_sha256'],
        expected_review_sha256=review['receipt_sha256'],outcome='failure',reviewed_by='software-fixture-revision'))
    assert not flow.model_readiness(Store(store.root),identity,1)['demos']['ready']


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_06_OPTIMIZER')!='1',reason='Explicit final8-update CPU qualification; avoid repeated model jobs in broad unit runs')
def test_real_official_ACT6_eight_CPU_updates_reload_registered_SIMULATION_card_and_readiness(tmp_path):
    from explorer_sim.so101_skill_training import run_so101_skill_train
    store,chosen,_=full_cohort(tmp_path);identity=chosen['spec']['skill_id']
    job=flow.enqueue_training(store,identity,1,{'request_id':'actual8','act_steps':8,'image_size':32,'chunk_size':4,'batch_size':2})
    store.update_job(job['id'],status='running');result=run_so101_skill_train(store,store.job(job['id']));store.complete_job(job['id'],result)
    assert result['optimizer_steps']==8 and result['weights_changed'] and result['save_reload_passed']
    assert result['reload_max_abs_error']<=1e-6 and len(result['training_original_episode_ids'])==3 and len(result['development_original_episode_ids'])==1
    assert result['source_kind']=='SIMULATION' and not result['physical_io'] and not result['simulator_executed']
    card=next(row for row in store.skills() if row['id']==result['skill_card']['id'])
    assert card['source_type']=='SIMULATION' and card['deployment_scope']=='SIMULATION_ONLY' and len(card['action_contract']['names'])==6
    readiness=flow.model_readiness(store,identity,1)
    assert readiness['demos']['ready'] and readiness['trained']['ready'] and not readiness['shadow']['ready'] and not readiness['physical']['ready']
    assert not readiness['physical_execution_authorized'] and not result['evaluation']['rollout_success_claimed']
    (tmp_path/'actual8-qualification.json').write_text(json.dumps(result,indent=2))
    model=Path(result['checkpoint_dir'])/'config.json';original=model.read_bytes();model.write_bytes(original+b' ')
    assert not flow.model_readiness(store,identity,1)['trained']['ready']
    model.write_bytes(original)


def test_byte_identical_RGB_from_different_originals_cannot_cross_train_and_development(tmp_path):
    store=Store(tmp_path/'state');chosen=proposal(store)
    for index,variant in enumerate(chosen['spec']['demo_plan']['variants']):
        assigned(store,chosen,variant,index,image_offset=0 if index in (0,3) else None)
    state=flow.state_for_skill(store,chosen['spec']['skill_id'],1)
    assert not state['data_ready']
    rejected=[row for row in state['assignments'] if not row['accepted']]
    assert len(rejected)==2 and all('original_frame_content_scene_session_or_object_crosses_splits' in row['reasons'] for row in rejected)


@pytest.mark.parametrize('fault',['false_mask','leader_future'])
def test_values_without_valid_measurement_masks_or_leader_clock_cannot_be_expert(tmp_path,fault):
    store=Store(tmp_path/'state');chosen=proposal(store);variant=chosen['spec']['demo_plan']['variants'][0]
    job,path,raw,session=native_source(store,chosen,variant,0,evidence_fault=fault)
    binding=flow.bind_episode(store,chosen['spec']['skill_id'],1,dict(import_job_id=job['id'],variant_id=variant['variant_id'],session_id=session['session_id']))
    state=flow.state_for_skill(store,chosen['spec']['skill_id'],1)
    assert not state['assignments'][0]['accepted']
    assert ('source_measurement_or_command_mask_invalid' if fault=='false_mask' else 'leader_measurement_clock_not_verified') in state['assignments'][0]['reasons']
