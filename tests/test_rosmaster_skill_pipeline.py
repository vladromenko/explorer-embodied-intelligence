"""Source-only software qualification; no camera, robot, simulator or GPU execution."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from explorer.store import Store
from explorer.skill_workflow import build_aggregate,create_session
from explorer_shared.contracts import ACTION_NAMES
from explorer_sim.rosmaster_skill_features import FEATURE_NAMES,PipelineBlocked,TrainNormalizer,channel,sensor_features
from explorer_sim.rosmaster_skill_hybrid import PhaseCoordinator,PhaseModel,astar,calibrated_goal,navigation_action
from explorer_sim.rosmaster_skill_pipeline import CanonicalCorpus,FusedChunks,RosmasterSkillPolicy,train_pipeline,pipeline_capabilities
from test_skill_workflow import skill,imported,assign,sha
from test_embodied_episode import package,encoded


def fixture_aggregate(tmp_path,*,missing_sensors=False):
    store=Store(tmp_path/'state');selected=skill(store);identity=selected['spec']['skill_id']
    sessions={partition:create_session(store,identity,1,dict(split=partition,scene='fixture-scene-'+partition)) for partition in ('train','development','sealed_test')}
    for episode_index,variant in enumerate(selected['spec']['coverage_plan']['variants']):
        project='03' if episode_index%2 else '02';kind='retarget_import' if project=='03' else 'mobile_import'
        job,path=imported(store,selected,'packet-'+str(episode_index),project_id=project,job_kind=kind,split=variant['split'],command_estimate=missing_sensors)
        facts=json.loads((path.parent/'facts.json').read_text());assets={}
        depth=np.full((32,32),700,dtype='<u2');assets['depth.u16']=depth.tobytes()
        for index,sample in enumerate(facts['samples']):
            rgb=np.zeros((32,32,3),np.uint8);rgb[...,0]=index*20;rgb[...,1]=np.arange(32)[None,:]*7;rgb[...,2]=episode_index*23+9
            output=io.BytesIO();Image.fromarray(rgb).save(output,format='PNG');name='rgb-'+str(index)+'.png';assets[name]=output.getvalue()
            sample['channels']['camera_rgb']['value'].update(source_file=name,sha256=hashlib.sha256(assets[name]).hexdigest(),width=32,height=32,encoding='png')
            stamp=deepcopy(sample['channels']['camera_rgb']['stamp'])
            sample['channels']['preceding_command9']=dict(present=True,available=True,mask=[True]*9,source_id='literal-native-prior9',stamp=stamp,
                value=dict(values=[0.]*9,names=list(ACTION_NAMES),state_source='command_estimate',reference_id='factory-known-reference',estimator_id='original-native-no-readback',last_dispatch_receipt_id='reference-receipt'))
            if not missing_sensors:
                sample['channels']['camera_depth']['value'].update(source_file='depth.u16',sha256=hashlib.sha256(assets['depth.u16']).hexdigest(),width=32,height=32,encoding='uint16_le')
                sample['channels']['camera_calibration']['value'].update(frame_id='world',intrinsics=[16,0,16,0,16,16,0,0,1])
                sample['channels']['odometry_pose']['value']['position']=[index*.02,0.,0.]
                sample['channels']['goal']['value'].update(position=[.6,.6,0.],units='m',frame_id='world',calibration_id='cal-1')
                sample['channels']['lidar']['value']['occupancy_grid']=dict(cells=np.zeros((14,14),int).tolist(),origin_xy_m=[-.2,-.2],resolution_m=.1,frame_id='world',source_id='synthetic_observed_map',robot_radius_m=.08,robot_geometry_id='software_fixture_ROSMASTER_body')
        facts['identity']={key:deepcopy(facts[key]) for key in ('episode_id','run_id','project_id','source_kind','skill','episode_scope','action_contract')}
        envelope,payloads=package(facts,assets)
        for name,raw in payloads.items():(path.parent/name).write_bytes(raw)
        path.write_bytes(encoded(envelope));assign(store,selected,job,variant['variant_id'],sessions[variant['split']])
    result=build_aggregate(store,identity,1)
    return Path(result['dataset_manifest']),store


def test_canonical_reader_real_RGB_full9D_sensor_masks_and_train_only_normalization(tmp_path):
    path,store=fixture_aggregate(tmp_path);corpus=CanonicalCorpus(path)
    assert len(corpus.train)==3 and len(corpus.development)==1 and len(corpus.sealed_ids)==1
    assert {episode['descriptor']['source_project_id'] for episode in corpus.episodes}=={'02','03'}
    dataset=FusedChunks(corpus,corpus.train,2,32);item=dataset[0]
    assert item['observation.state'].shape==(len(FEATURE_NAMES)*2,) and item['action'].shape==(2,9)
    assert item['observation.images.overview'].shape==(3,32,32)
    assert np.isfinite(item['observation.state'].numpy()).all() and len(corpus.normalizer.mean)>9
    assert corpus.normalizer.document()['fit_partition']=='train'
    assert not corpus.normalizer.document()['masks_normalized']
    # Development mutation is diagnostic data, never a normalization fit input.
    training=np.asarray([frame['values'] for episode in corpus.train for frame in episode['frames']])
    masks=np.asarray([frame['masks'] for episode in corpus.train for frame in episode['frames']])
    assert np.allclose(corpus.normalizer.mean,(training*masks).sum(0)/np.maximum(masks.sum(0),1))
    originals={name:sha(name) for name in corpus.source_files};corpus.development[0]['frames'][0]['values'][:]=1e6
    assert np.allclose(corpus.normalizer.mean,(training*masks).sum(0)/np.maximum(masks.sum(0),1))
    assert all(sha(name)==expected for name,expected in originals.items())


def test_feedback_stays_null_while_literal_nine_neutral_coordinates_are_valid(tmp_path):
    path,_=fixture_aggregate(tmp_path,missing_sensors=True);corpus=CanonicalCorpus(path)
    frame=corpus.train[0]['frames'][0];record=frame['sample']['channels']['joint_feedback']
    assert record['value'] is None and record['mask'] is False
    assert frame['masks'][:9].all() and np.array_equal(frame['values'][:9],[0.]*9)
    assert not frame['masks'][9:15].any()
    state=corpus.normalizer.state(frame['values'],frame['masks'])
    assert np.array_equal(state[len(FEATURE_NAMES):len(FEATURE_NAMES)+9],np.ones(9))
    assert np.array_equal(frame['action'],np.zeros(9))
    with pytest.raises(PipelineBlocked,match='CALIBRATION_MISSING'):calibrated_goal(frame['sample'],lambda name:corpus.read(corpus.train[0],name))


def test_actor_feature_allowlist_never_reads_privileged_simulator_object_truth(tmp_path):
    path,_=fixture_aggregate(tmp_path);corpus=CanonicalCorpus(path);episode=corpus.train[0];sample=deepcopy(episode['frames'][0]['sample'])
    original=sensor_features(sample,lambda name:corpus.read(episode,name))
    sample['privileged_object_xyz']=[1e30,-1e30,8e30];sample['phase_label']='fake';sample['evaluator_success']=True
    changed=sensor_features(sample,lambda name:corpus.read(episode,name))
    assert np.array_equal(original[0],changed[0]) and np.array_equal(original[1],changed[1])
    sample['channels'].pop('preceding_command9')
    with pytest.raises(PipelineBlocked,match='PRIOR_COMMAND9_REQUIRED'):sensor_features(sample,lambda name:corpus.read(episode,name))


def test_source_payload_tamper_and_sealed_or_duplicate_descriptors_rejected(tmp_path):
    path,_=fixture_aggregate(tmp_path);manifest=json.loads(path.read_text());corpus=CanonicalCorpus(path)
    first=next(iter(corpus.train[0]['paths'].values()));first.write_bytes(b'changed')
    with pytest.raises(PipelineBlocked,match='SHA_MISMATCH'):CanonicalCorpus(path)
    # Restore-independent manifest-only validation cases with a fresh fixture.
    path,_=fixture_aggregate(tmp_path/'fresh');manifest=json.loads(path.read_text())
    manifest['episodes'][0]['split']='sealed_test';path.write_bytes(encoded(manifest))
    with pytest.raises(PipelineBlocked,match='PARTITION_FORBIDDEN'):CanonicalCorpus(path)


def test_global_scene_leakage_is_rechecked_in_trainer_not_only_upstream_workflow(tmp_path):
    path,_=fixture_aggregate(tmp_path);manifest=json.loads(path.read_text());training=next(row for row in manifest['episodes'] if row['split']=='train')
    development=next(row for row in manifest['episodes'] if row['split']=='development');development['scene']=training['scene'];path.write_bytes(encoded(manifest))
    with pytest.raises(PipelineBlocked,match='SPLIT_GROUP_LEAKAGE'):CanonicalCorpus(path)


def test_Astar_observed_map_trajectory_is_full9_and_never_treats_unknown_as_free(tmp_path):
    grid=np.zeros((5,5),int);grid[2,:4]=1
    path=astar(grid,(0,0),(4,0));assert path[0]==(0,0) and path[-1]==(4,0) and (2,4) in path
    grid[2,4]=-1
    with pytest.raises(PipelineBlocked,match='PATH_UNAVAILABLE'):astar(grid,(0,0),(4,0))
    source,_=fixture_aggregate(tmp_path);corpus=CanonicalCorpus(source);episode=corpus.train[0];frame=episode['frames'][0]
    goal,grounding=calibrated_goal(frame['sample'],lambda name:corpus.read(episode,name));assert goal.tolist()==[.6,.6,0.]
    candidate,trace=navigation_action(frame['sample'],goal,np.ones(9))
    assert candidate.shape==(9,) and np.array_equal(candidate[:6],np.zeros(6))
    assert np.linalg.norm(candidate[6:8])<=.150001 and not trace['hardware_io']
    sample=deepcopy(frame['sample']);sample['channels']['lidar']['value'].pop('occupancy_grid')
    with pytest.raises(PipelineBlocked,match='OCCUPANCY_GRID_MISSING'):navigation_action(sample,goal,np.ones(9))


def test_RGB_locked_detection_backprojects_only_real_aligned_depth_with_calibration(tmp_path):
    source,_=fixture_aggregate(tmp_path);corpus=CanonicalCorpus(source);episode=corpus.train[0];sample=deepcopy(episode['frames'][0]['sample'])
    sample['channels']['goal']['value'].pop('position');sample['channels']['goal']['value']['detection_id']='locked-box'
    sample['channels']['detections']['value']['detections']=[dict(id='locked-box',bbox_xyxy=[8,8,24,24])]
    point,receipt=calibrated_goal(sample,lambda name:corpus.read(episode,name))
    assert np.allclose(point,[0,0,.7]) and receipt['depth_samples']==256
    sample['channels']['camera_depth']['available']=False
    with pytest.raises(PipelineBlocked,match='DEPTH_GEOMETRY_MISSING'):calibrated_goal(sample,lambda name:corpus.read(episode,name))


def test_phase_model_uses_observations_and_guard_never_promotes_incomplete_or_timed_release():
    model=PhaseModel(['a','b']).fit([[0,0],[1,1]],['a','b'])
    assert model.predict([1,1])=='b'
    with pytest.raises(PipelineBlocked,match='AMBIGUOUS'):model.predict([.5,.5])
    guard=PhaseCoordinator(['grasp_confirm','verify_placement'],max_unconfirmed_steps=2)
    assert guard.observe('grasp_confirm')['state']=='ACTIVE'
    assert guard.observe('grasp_confirm',dict(phase_id='grasp_confirm',status='success',independent_of_action_commands=True))['state']=='RECOVERY_REQUIRED'
    with pytest.raises(PipelineBlocked,match='RELOCALIZATION'):guard.recover(dict(kind='timer'))
    guard.recover(dict(kind='measured_relocalization'),verify_evidence=lambda receipt:True)
    complete=guard.observe('grasp_confirm',dict(phase_id='grasp_confirm',status='success',independent_of_action_commands=True),verify_evidence=lambda receipt:True)
    assert not complete['full_task_success'] and complete['next_phase']=='verify_placement'
    final=guard.observe('verify_placement',dict(phase_id='verify_placement',status='success',independent_of_action_commands=True),verify_evidence=lambda receipt:True)
    assert final['full_task_success'] and not final['automatic_release']


def test_controlplane_capabilities_do_not_import_torch_mujoco_or_initialize_GPU():
    root=Path(__file__).parents[1]
    code="import sys;sys.path[:0]="+repr([str(root/'shared'),str(root/'01-autonomous-learning/src')])+";from explorer_sim.rosmaster_skill_pipeline import pipeline_capabilities;c=pipeline_capabilities();assert c['fused_state_dimension']==106;assert 'torch' not in sys.modules;assert 'mujoco' not in sys.modules"
    result=subprocess.run([sys.executable,'-c',code],text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr


def test_actual_eight_official_ACT_optimizer_steps_fused_state_full9_save_reload_and_heldout_metrics(tmp_path):
    pytest.importorskip('lerobot')
    import torch
    from explorer_sim.student import IMAGE_KEY
    path,_=fixture_aggregate(tmp_path);before=CanonicalCorpus(path);source_hashes=deepcopy(before.source_files)
    result=train_pipeline(path,tmp_path/'model',settings=dict(act_steps=8,batch_size=2,image_size=32,chunk_size=2,threads=1))
    assert result['trained'] and result['optimizer_steps']==8 and result['weights_changed']
    assert result['save_reload_passed'] and result['reload_max_abs_error']<=1e-6
    assert result['output_action_dimension']==9 and result['feature_dimension']==len(FEATURE_NAMES)*2
    model=json.loads(Path(result['model_manifest']).read_text());config=json.loads((Path(result['checkpoint_dir'])/'config.json').read_text())
    assert config['input_features']['observation.state']['shape']==[len(FEATURE_NAMES)*2] and config['output_features']['action']['shape']==[9]
    assert IMAGE_KEY in config['input_features'] and 'camera_rgb' in model['model_used_channels']
    assert model['ACT9']['implemented'] and model['hybrid']['implemented']
    assert set(model['train_original_episode_ids']).isdisjoint(model['development_original_episode_ids']+model['sealed_original_episode_ids'])
    assert result['evaluation']['ACT9']['evaluated_samples']==10
    assert result['evaluation']['whole_task_success']=='NOT_EVALUATED' and not result['evaluation']['learned_policy_rollout_executed']
    assert not result['physical_io'] and not result['simulator_executed'] and not model['whole_skill_mastered']
    assert all(sha(name)==expected for name,expected in source_hashes.items())
    assert pipeline_capabilities()['job_kind']=='rosmaster_skill_train'
    actor=RosmasterSkillPolicy(result['model_manifest'])
    observation=deepcopy(before.train[0]['frames'][0]['sample']);observation.pop('action')
    with pytest.raises(PipelineBlocked,match='SENSOR_ADMISSION_REQUIRED'):actor.predict(observation,lambda name:before.read(before.train[0],name))
    predicted=actor.predict(observation,lambda name:before.read(before.train[0],name),verify_observation=lambda *args:before.train[0]['report']['channels'])
    assert len(predicted['action9'])==9 and not predicted['hardware_io'] and not predicted['phase_progress']['full_task_success']
    # Actual saved network responds to BOTH image and fused-state inputs.
    sample=FusedChunks(before,before.train,2,32)[0]
    inputs={key:sample[key].unsqueeze(0) for key in (IMAGE_KEY,'observation.state')}
    with torch.inference_mode():
        original=actor.policy.predict_action_chunk(inputs)
        image_changed=actor.policy.predict_action_chunk({**inputs,IMAGE_KEY:inputs[IMAGE_KEY].flip(-1)})
        state=inputs['observation.state'].clone();state[:,0]+=.25
        state_changed=actor.policy.predict_action_chunk({**inputs,'observation.state':state})
    assert float((original-image_changed).abs().max())>1e-8
    assert float((original-state_changed).abs().max())>1e-8
