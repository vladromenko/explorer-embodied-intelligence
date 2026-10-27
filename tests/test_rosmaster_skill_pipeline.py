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
from explorer_sim.rosmaster_skill_pipeline import CanonicalCorpus,FusedChunks,RosmasterSkillPolicy,train_pipeline,train_rosmaster_skill,pipeline_capabilities
from test_skill_workflow import skill,imported,assign,sha
from test_embodied_episode import package,encoded


def fixture_aggregate(tmp_path,*,missing_sensors=False,native02=False,paused=False):
    store=Store(tmp_path/'state');selected=skill(store);identity=selected['spec']['skill_id']
    sessions={partition:create_session(store,identity,1,dict(split=partition,scene='fixture-scene-'+partition)) for partition in ('train','development','sealed_test')}
    for episode_index,variant in enumerate(selected['spec']['coverage_plan']['variants']):
        project='03' if episode_index%2 and not native02 else '02';kind='retarget_import' if project=='03' else 'mobile_import'
        original=hashlib.sha256(('native-fixture-'+str(episode_index)).encode()).hexdigest()[:32] if native02 else 'packet-'+str(episode_index)
        job,path=imported(store,selected,original,project_id=project,job_kind=kind,split=variant['split'],command_estimate=missing_sensors)
        facts=json.loads((path.parent/'facts.json').read_text());assets={}
        depth=np.full((32,32),700,dtype='<u2');assets['depth.u16']=depth.tobytes()
        for index,sample in enumerate(facts['samples']):
            rgb=np.zeros((32,32,3),np.uint8);rgb[...,0]=index*20;rgb[...,1]=np.arange(32)[None,:]*7;rgb[...,2]=episode_index*23+9
            output=io.BytesIO();Image.fromarray(rgb).save(output,format='PNG');name='rgb-'+str(index)+'.png';assets[name]=output.getvalue()
            sample['channels']['camera_rgb']['value'].update(source_file=name,sha256=hashlib.sha256(assets[name]).hexdigest(),width=32,height=32,encoding='png')
            stamp=deepcopy(sample['channels']['camera_rgb']['stamp'])
            sample['channels']['preceding_command9']=dict(present=True,available=True,mask=[True]*9,source_id='literal-native-prior9',stamp=stamp,
                value=dict(values=[0.]*9,names=list(ACTION_NAMES),state_source='command_estimate',reference_id='factory-known-reference',estimator_id='original-native-no-readback',last_dispatch_receipt_id='reference-receipt'))
            if paused:
                sample.update(recording_segment_id=int(index>=5),recording_segment={'index':int(index>=5)})
                sample['action']['stamp']['monotonic_ns']=20+index*10;sample['action']['delivery']['stamp']['monotonic_ns']=20+index*10
                for record in sample['channels'].values():
                    if isinstance(record.get('stamp'),dict):record['stamp']['monotonic_ns']=10+index*10
            if not missing_sensors:
                sample['channels']['camera_depth']['value'].update(source_file='depth.u16',sha256=hashlib.sha256(assets['depth.u16']).hexdigest(),width=32,height=32,encoding='uint16_le')
                sample['channels']['camera_calibration']['value'].update(frame_id='world',intrinsics=[16,0,16,0,16,16,0,0,1])
                sample['channels']['odometry_pose']['value']['position']=[index*.02,0.,0.]
                sample['channels']['goal']['value'].update(position=[.6,.6,0.],units='m',frame_id='world',calibration_id='cal-1')
                sample['channels']['lidar']['value']['occupancy_grid']=dict(cells=np.zeros((14,14),int).tolist(),origin_xy_m=[-.2,-.2],resolution_m=.1,frame_id='world',source_id='synthetic_observed_map',robot_radius_m=.08,robot_geometry_id='software_fixture_ROSMASTER_body')
        if paused:facts['recording']['capture_intervals']={'started_monotonic_ns':1,'pause_intervals':[{'start_monotonic_ns':65,'end_monotonic_ns':68}]}
        facts['identity']={key:deepcopy(facts[key]) for key in ('episode_id','run_id','project_id','source_kind','skill','episode_scope','action_contract')}
        envelope,payloads=package(facts,assets)
        if paused:
            for index,sample in enumerate(envelope['samples']):sample['recording_segment']['provenance']={
                'source_file':'facts.json','sha256':envelope['source_files_sha256']['facts.json'],'json_pointer':'/samples/'+str(index)+'/recording_segment'}
        for name,raw in payloads.items():(path.parent/name).write_bytes(raw)
        path.write_bytes(encoded(envelope))
        if native02:
            from explorer.native_capture_approval import source_bytes
            from test_native_capture_approval import approved
            wrapper=approved();wrapper['source_review']['recording_id']=original
            wrapper['review_sha256']=hashlib.sha256(source_bytes(wrapper['source_review'])).hexdigest()
            approval=path.parent/'source-review.json';approval.write_bytes(source_bytes(wrapper))
            job['config'].update(source_format='native_capture_v1',archive_sha256='b'*64,source_review_sha256=wrapper['review_sha256'])
            job['result'].update(schema='explorer_native_capture_import_v1',source_archive_sha256='b'*64,source_approval=wrapper,
                source_review_sha256=wrapper['review_sha256'],source_approval_path=str(approval),source_approval_file_sha256=sha(approval))
            with store.connect() as db:db.execute('UPDATE jobs SET config=?,result=? WHERE id=?',(json.dumps(job['config']),json.dumps(job['result']),job['id']))
            job=store.job(job['id'])
        assign(store,selected,job,variant['variant_id'],sessions[variant['split']])
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
    assert set(dataset.resume_context['source_implementation_sha256'])=={'rosmaster_skill_pipeline.py','rosmaster_skill_features.py','rosmaster_skill_hybrid.py','rosmaster_skill_segments.py'}
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


def test_unseen_valid_runtime_sensor_blocks_predict_before_network_without_masking_measurement(tmp_path):
    path,_=fixture_aggregate(tmp_path);corpus=CanonicalCorpus(path);episode=corpus.train[0];frame=episode['frames'][0]
    training_mask=frame['masks'].copy();index=FEATURE_NAMES.index('goal.0');assert training_mask[index]
    training_mask[index]=False
    normalizer=TrainNormalizer([frame['values']],[training_mask],[frame['action']])
    actor=RosmasterSkillPolicy.__new__(RosmasterSkillPolicy);actor.model={};actor.normalizer=normalizer
    class ForbiddenNetwork:
        def predict_action_chunk(self,*args,**kwargs):raise AssertionError('OOD observation must fail before network inference')
    actor.policy=ForbiddenNetwork()
    observation=deepcopy(frame['sample']);observation.pop('action');unchanged=deepcopy(observation);validity=frame['masks'].copy()
    with pytest.raises(PipelineBlocked,match='UNTRAINED_VALID_SENSOR_FEATURES: goal.0'):
        actor.predict(observation,lambda name:corpus.read(episode,name),verify_observation=lambda *args:episode['report']['channels'])
    assert observation==unchanged and observation['channels']['goal']['mask'] is True
    assert np.array_equal(frame['masks'],validity) and frame['masks'][index]


def test_runtime_normalizer_preserves_genuine_zero_and_validity_and_rejects_corrupt_counts():
    values=np.zeros(len(FEATURE_NAMES),np.float32);masks=np.zeros(len(FEATURE_NAMES),bool);masks[:9]=True
    normalizer=TrainNormalizer([values],[masks],[np.zeros(9)])
    result=normalizer.runtime_state(values,masks)
    assert np.array_equal(result[len(FEATURE_NAMES):len(FEATURE_NAMES)+9],np.ones(9))
    assert np.array_equal(result[:9],np.zeros(9)) and np.array_equal(normalizer.count[:9],np.ones(9))
    for count in (-1.,.5):
        document=normalizer.document();document['valid_training_counts'][0]=count
        with pytest.raises(PipelineBlocked,match='COUNTS_INVALID'):TrainNormalizer.from_document(document)
    document=normalizer.document();document['scales'][0]=0
    with pytest.raises(PipelineBlocked,match='SCALE_INVALID'):TrainNormalizer.from_document(document)
    with pytest.raises(PipelineBlocked,match='VALIDITY_SCHEMA_INVALID'):normalizer.runtime_state(values,masks.astype(float))


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


def test_worker_rejects_changed_or_missing_queued_source_lineage_before_optimizer(tmp_path,monkeypatch):
    from explorer_sim import rosmaster_skill_pipeline as pipeline
    path,store=fixture_aggregate(tmp_path);manifest=json.loads(path.read_text());called=[]
    monkeypatch.setattr(pipeline,'train_pipeline',lambda *args,**kwargs:called.append(True))
    job=dict(id='worker-fixture',kind='rosmaster_skill_train',project_id='03',config=dict(dataset_manifest=str(path)))
    with pytest.raises(PipelineBlocked,match='SERVER_BOUND'):train_rosmaster_skill(store,job)
    lineage=dict(skill_id=manifest['skill_id'],skill_version=manifest['skill_version'],spec_sha256=manifest['skill_spec_sha256'],aggregate_sha256='0'*64)
    job['config']['skill_workflow']=lineage
    with pytest.raises(PipelineBlocked,match='AGGREGATE_SHA_CHANGED'):train_rosmaster_skill(store,job)
    lineage['aggregate_sha256']=sha(path);lineage['skill_version']+=1
    with pytest.raises(PipelineBlocked,match='SKILL_SPEC_LINEAGE_CHANGED'):train_rosmaster_skill(store,job)
    lineage['skill_version']-=1;lineage['training_original_episode_ids']=[]
    with pytest.raises(PipelineBlocked,match='PARTITION_LINEAGE_CHANGED'):train_rosmaster_skill(store,job)
    assert not called


def test_actual_eight_official_ACT_optimizer_steps_fused_state_full9_save_reload_and_heldout_metrics(tmp_path,monkeypatch):
    pytest.importorskip('lerobot')
    import torch
    from explorer_sim.student import IMAGE_KEY
    from explorer import job_runner
    from explorer.skill_workflow import enqueue_training
    from explorer.scheduler import HEAVY_JOBS
    path,store=fixture_aggregate(tmp_path);before=CanonicalCorpus(path);source_hashes=deepcopy(before.source_files)
    job=enqueue_training(store,before.skill_id,before.version,dict(act_steps=8,batch_size=2,image_size=32,chunk_size=2),project_id='03')
    assert job['kind']=='rosmaster_skill_train' and job['kind'] in HEAVY_JOBS
    monkeypatch.setattr(job_runner,'load',lambda:dict(root=str(Path(__file__).parents[1]),data_dir=str(store.root)))
    job_runner.main(job['id'])
    completed=store.job(job['id']);assert completed['status']=='completed',completed.get('error')
    result=completed['result']
    card=next(row for row in store.skills() if row['id']==result['skill_card']['id'])
    assert card['source_type']=='simulation' and card['lineage']['job_id']==job['id']
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
