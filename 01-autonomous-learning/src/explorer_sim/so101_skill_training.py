"""ENGINEERING_GLUE: Official ACT6 offline learner for one immutable SO101 source skill cohort.

No robot/replay CLI/camera/serial/network or simulator execution. Source simulation
fixtures remain SIMULATION-only cards, regardless of task labels or loss values.
"""
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
import torch

from .dataset_export import verify_lerobot_v3,load_episode
from explorer_shared.learning.settings import act_parameters as parameters
from explorer_shared.learning.artifacts import sha,state_digest,write_json
from .recorded_training import RecordedChunks,offline_evaluation
from explorer_shared.learning.act_backend import IMAGE_KEY,make_act_policy,optimize_act,save_reload_act,preprocessing_config,framework_info
from explorer_shared.contracts import digest
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH,so101_source_identity
from explorer_shared.experiments import experiment_id,experiment_identity


def _verify(store,path,expected):
    path=Path(path).resolve()
    if not path.is_relative_to(Path(store.root).resolve()) or not path.is_file() or sha(path)!=expected:raise ValueError('Pinned SO101 source dataset artifact changed or escaped managed state')
    return path


def _source_job_identity(job):
    config=job.get('config') or {};proof=job.get('numbering_provenance') or {}
    if (proof.get('source')=='verified_migration_index' and proof.get('original_numbering_version')==1
            and all(isinstance(proof.get(key),str) and len(proof[key])==64 and all(char in '0123456789abcdef' for char in proof[key]) for key in ('projection_sha256','index_sha256'))):
        return so101_source_identity(proof.get('original_project_id'),numbering_version=1)
    version=job.get('experiment_numbering_version',config.get('experiment_numbering_version'))
    if version is None and job.get('project_id')==experiment_id('so101_source_learning'):version=2
    if version is None and job.get('project_id')=='06':version=config.get('source_numbering_version')
    return so101_source_identity(job.get('project_id'),numbering_version=version)


def run_so101_skill_train(store,job):
    config=job.get('config') or {}
    identity=experiment_identity('so101_source_learning')
    if job.get('kind')!='so101_skill_train' or not _source_job_identity(job)['verified'] or config.get('device','cpu')!='cpu':raise ValueError('This learner owns the CPU SO101 source role ACT6 only; legacy06 requires explicit v1')
    from explorer.so101_dataset_admission import verify_so101_aggregate,verify_current_source_reviews,admission_sources
    source,outer,view_path,view=verify_so101_aggregate(store,config)
    official=source.parent/outer['official_dataset'];provenance=json.loads((official/'explorer_provenance.json').read_text())
    official_readback=verify_lerobot_v3(official,provenance['repo_id'],view_path)
    train=[row for row in view['episodes'] if row['split']=='train'];development=[row for row in view['episodes'] if row['split']=='development']
    if not train or not development or any(row['split'] not in ('train','development') for row in view['episodes']):raise ValueError('Whole source train/development groups required; sealed payload must be absent')
    settings=parameters({key:config[key] for key in ('act_steps','batch_size','image_size','chunk_size','seed','device') if key in config})
    if settings['act_steps']<1:raise ValueError('Actual positive optimizer budget required')
    torch.set_num_threads(2)
    folder=Path(store.root)/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True);result_path=folder/'result.json'
    if result_path.is_file():
        saved=json.loads(result_path.read_text())
        if saved['aggregate_manifest_sha256']!=sha(source):raise ValueError('Completed learner belongs to another cohort')
        for name,expected in saved['checkpoint_sha256'].items():_verify(store,folder/'student_act'/name,expected)
        if not any(card['id']==saved['skill_card']['id'] for card in store.skills()):store.add_skill(saved['skill_card'])
        return saved
    def update(progress,phase):
        current=store.job(job['id'])
        if current and current['status'] in ('cancelled','cancel_requested'):raise InterruptedError('SO101 source training cancelled')
        verify_current_source_reviews(store,outer)
        store.update_job(job['id'],progress=progress,result={'phase':phase,'physical_io':False,'skill_id':outer['skill_id']})
    samples=RecordedChunks(view_path,view,train,settings['chunk_size'],settings['image_size'])
    samples.resume_context.update(source_signature=sha(source),skill_id=outer['skill_id'],skill_version=outer['skill_version'],spec_sha256=outer['spec_sha256'])
    samples.resume_context['learner_implementation_sha256']=sha(__file__)
    samples.resume_context['source_files']+=[{'path':str(source),'sha256':sha(source)}]
    samples.resume_context['source_files'] += [dict(path=str(rule),sha256=sha(rule)) for rule in admission_sources()]
    preview=samples[0];Image.fromarray(samples.cache['rgb'][0]).save(folder/'frame.jpg')
    write_json(folder/'frame.json',{'source':'actual_selected_native_dataset_RGB','source_episode_id':train[0]['source_episode_id'],'live_camera':False})
    update(.05,'verified_official_dataset_and_original_native_provenance')
    policy=make_act_policy(settings,state_dim=6,action_dim=6);initial=state_digest(policy);started=time.monotonic()
    optimizer,losses,_=optimize_act(policy,samples,settings,folder,update,.1,.8,checkpoint_interval=8)
    inputs={key:preview[key].unsqueeze(0) for key in (IMAGE_KEY,'observation.state')}
    preprocessing=preprocessing_config(np.asarray(LOW,np.float32),np.asarray(HIGH,np.float32),range(6))
    preprocessing.update(state_basis='measured_state',action_names=list(NAMES),action_units=UNITS,action_contract_id=outer['action_contract_id'],
        official_robot_use_degrees=False,hardware_executor='existing_native_Pi_station_only',ACT_language_input=False,
        source_calibration_metadata=outer['source_calibration_metadata'],
        source_camera_ids=sorted({camera for row in view['source_records'] for camera in row['source_evidence']['camera_ids']}))
    reloaded,checkpoint,error=save_reload_act(policy,optimizer,folder,preprocessing,inputs,'cpu')
    from lerobot.policies.factory import make_pre_post_processors
    pre,post=make_pre_post_processors(policy.config)
    pre.save_pretrained(checkpoint,push_to_hub=False,config_filename='policy_preprocessor.json')
    post.save_pretrained(checkpoint,push_to_hub=False,config_filename='policy_postprocessor.json')
    loaded_pre,loaded_post=make_pre_post_processors(reloaded.config,pretrained_path=str(checkpoint),
        preprocessor_overrides={'device_processor':{'device':'cpu'}})
    reloaded.reset()
    with torch.inference_mode():
        expected_first=reloaded.predict_action_chunk(inputs)[:,0]
        processed=loaded_post(reloaded.select_action(loaded_pre({key:preview[key] for key in (IMAGE_KEY,'observation.state')})))
    processor_error=float((expected_first-processed).abs().max())
    if processor_error>1e-6:raise ValueError('Saved official ACT processor pair changed source normalization')
    validation=RecordedChunks(view_path,view,development,settings['chunk_size'],settings['image_size'])
    evaluation=offline_evaluation(reloaded,validation,'cpu',list(NAMES),UNITS,np.asarray(LOW),np.asarray(HIGH))
    for name,expected in outer['source_files_sha256'].items():_verify(store,source.parent/name,expected)
    if sha(source)!=config['aggregate_manifest_sha256']:raise ValueError('Whole-skill source changed during optimizer run')
    verify_so101_aggregate(store,config)  # A late Source rejection cannot publish a newly trained model card.
    weights_changed=state_digest(policy)!=initial
    if not weights_changed:raise RuntimeError('No optimizer weight change; no trained card registered')
    hashes={path.name:sha(path) for path in checkpoint.iterdir() if path.is_file()};kind=outer['source_kind'];simulation=kind=='SIMULATION'
    result=dict(trained=True,framework='LeRobot',framework_version='0.6.1',algorithm='official ACT6 RGB+measured_follower6→issued_native_follower6',
        skill_id=outer['skill_id'],skill_version=outer['skill_version'],spec_sha256=outer['spec_sha256'],action_contract_id=outer['action_contract_id'],
        source_kind=kind,source_type='SIMULATION' if simulation else 'SO101_RECORDED_DEMONSTRATIONS',status='trained_offline_only',**identity,
        aggregate_manifest_sha256=sha(source),source_manifest_path=str(source),source_manifest_sha256=sha(source),
        internal_view_manifest_sha256=sha(view_path),official_dataset_readback=official_readback,
        optimizer_steps=len(losses),loss_first=float(losses[0]['loss']),loss_last=float(losses[-1]['loss']),weights_changed=True,
        training_resume=optimizer.explorer_resume_info,save_reload_passed=True,reload_max_abs_error=error,
        official_saved_processors_executed=True,saved_processor_reload_max_abs_error=processor_error,
        checkpoint_dir=str(checkpoint),checkpoint_sha256=hashes,checkpoint_content_sha256=digest(hashes),evaluation=evaluation,
        training_original_episode_ids=outer['receipt']['accepted_train_original_episode_ids'],development_original_episode_ids=outer['receipt']['accepted_development_original_episode_ids'],
        sealed_original_episode_ids=outer['receipt']['sealed_original_episode_ids'],sealed_payload_used=False,
        physical_io=False,simulator_executed=False,physical_success_inferred=False,rollout_success_claimed=False,elapsed_s=time.monotonic()-started,**framework_info())
    card=dict(id='so101-act-'+job['id'],task=outer['skill_spec']['title'],source_type=result['source_type'],source_kind=kind,status='trained_offline_only',
        action_contract={'id':outer['action_contract_id'],'names':list(NAMES),'units':UNITS,'state_basis':'measured_state','official_robot_use_degrees':False},
        lineage={'job_id':job['id'],'skill_id':outer['skill_id'],'skill_version':outer['skill_version'],'spec_sha256':outer['spec_sha256'],**identity,
            'dataset_manifest_sha256':sha(source),'checkpoint_sha256':digest(hashes),'training_original_episode_ids':result['training_original_episode_ids'],
            'development_original_episode_ids':result['development_original_episode_ids'],'sealed_original_episode_ids':result['sealed_original_episode_ids'],
            'methods':['ACT_LEROBOT_0_6_1','ENGINEERING_GLUE']},evaluation=evaluation,
        deployment_scope='SIMULATION_ONLY' if simulation else 'OFFLINE_SHADOW_PENDING_SEPARATE_OBSERVED_TEST',
        limitations=['Offline development chunk MAE is not full-task rollout success','Source operator labels are BC targets, not learned-policy physical validation',
            'No physical commands, robot connection or controller changes','Sealed source payload excluded','Task text is metadata; ACT has no language input'])
    result['skill_card']=card;write_json(folder/'provenance.json',{'aggregate_sha256':sha(source),'settings':settings,'source_kind':kind,'physical_io':False,**identity})
    write_json(result_path,result)
    if not any(value['id']==card['id'] for value in store.skills()):store.add_skill(card)
    update(.99,'official_ACT6_optimizer_and_save_reload_verified_offline_only')
    return result
