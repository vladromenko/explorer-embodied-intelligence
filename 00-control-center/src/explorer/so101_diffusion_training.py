"""ENGINEERING_GLUE: SO101 source-role adapter to official Diffusion; offline arrays only.

The semantic experiment registry owns page numbering. Historical source fields
and SHA are preserved; this adapter never starts a station/camera/control client.
"""
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image

from explorer_shared.contracts import digest
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH
from explorer_shared.learning.artifacts import sha,state_digest,write_json
from explorer_shared.learning.diffusion_backend import PROFILE_ID,IMAGE_KEY,make_compact_diffusion_policy
from explorer_shared.learning.history_windows import HistoryWindows
from explorer_shared.learning.diffusion_optimizer import optimize_diffusion
from explorer_shared.learning.diffusion_policy import save_reload_diffusion,offline_evaluation


def _file(store,path,expected):
    path=Path(path).resolve()
    if not path.is_relative_to(Path(store.root).resolve()) or not path.is_file() or sha(path)!=expected:raise ValueError('Immutable SO101 source/checkpoint escaped managed state or changed SHA')
    return path


def _data_tools():
    # Existing source format/officialDataset adapter remains at its compatibility
    # path. Shared optimizer/windows/policy modules have no experiment dependency.
    root=Path(__file__).resolve().parents[3]/'01-autonomous-learning/src'
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    from explorer_sim.dataset_export import load_episode,verify_lerobot_v3
    return load_episode,verify_lerobot_v3


from .so101_dataset_admission import verify_so101_aggregate as _aggregate,admission_sources


def run_so101_diffusion_train(store,job):
    import torch
    from explorer_shared import experiments as experiment_registry
    from explorer_shared.experiments import experiment_id,experiment_identity
    from explorer_shared.learning.act_backend import framework_info
    config=job.get('config') or {};identity=experiment_identity('so101_source_learning')
    if job.get('kind')!='so101_skill_train' or job.get('project_id')!=experiment_id('so101_source_learning') or config.get('backend')!='diffusion':raise ValueError('Typed SO101 semantic source-role Diffusion job required')
    if config.get('device','cpu')!='cpu' or config.get('profile_id')!=PROFILE_ID:raise ValueError('Only explicitly qualified compact CPU profile accepted')
    steps=config.get('optimizer_steps',config.get('act_steps',8));batch=config.get('batch_size',2);seed=config.get('seed',41)
    if 'optimizer_steps' in config and 'act_steps' in config and config['optimizer_steps']!=config['act_steps']:raise ValueError('Optimizer budget aliases disagree')
    if any(type(value) is not int for value in (steps,batch,seed)) or not 1<=steps<=20000 or not 1<=batch<=2 or not 0<=seed<=1000000 or config.get('image_size',64)!=64:raise ValueError('Bounded qualified CPU Diffusion budget/batch2/image64 required')
    settings=dict(optimizer_steps=steps,batch_size=batch,seed=seed,image_size=64,device='cpu');torch.set_num_threads(2)
    path,outer,view_path,view=_aggregate(store,config);load_episode,verify_official=_data_tools()
    official=path.parent/outer['official_dataset'];provenance=json.loads((official/'explorer_provenance.json').read_text())
    official_readback=verify_official(official,provenance['repo_id'],view_path)
    training=[row for row in view['episodes'] if row['split']=='train'];development=[row for row in view['episodes'] if row['split']=='development']
    if not training or not development:raise ValueError('Independent original train/development groups required')
    folder=Path(store.root)/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True);result_path=folder/'result.json'
    if result_path.exists():
        saved=json.loads(result_path.read_text())
        if saved.get('backend')!='diffusion' or saved['aggregate_manifest_sha256']!=sha(path):raise ValueError('Stored model belongs to another source/backend')
        for name,expected in saved['checkpoint_sha256'].items():_file(store,folder/'student_diffusion'/name,expected)
        if not any(card['id']==saved['skill_card']['id'] for card in store.skills()):store.add_skill(saved['skill_card'])
        return saved
    def progress(value,phase):
        current=store.job(job['id'])
        if current and current['status'] in ('cancelled','cancel_requested'):raise InterruptedError('SO101 Diffusion training cancelled')
        store.update_job(job['id'],progress=value,result=dict(phase=phase,backend='diffusion',physical_io=False,**identity))
    samples=HistoryWindows(view_path,view,training,episode_loader=load_episode,partition='train')
    samples.resume_context.update(source_signature=sha(path),skill_id=outer['skill_id'],skill_version=outer['skill_version'],spec_sha256=outer['spec_sha256'],
        native_consumer_implementation_sha256=sha(__file__))
    samples.resume_context['source_files'].append(dict(path=str(path),sha256=sha(path)))
    samples.resume_context['source_files'] += [dict(path=str(source),sha256=sha(source)) for source in admission_sources()]
    samples.resume_context['source_files'].append(dict(path=experiment_registry.__file__,sha256=sha(experiment_registry.__file__)))
    preview=samples[0];Image.fromarray(samples.cache['rgb'][0]).save(folder/'frame.jpg')
    write_json(folder/'frame.json',dict(source='actual_accepted_source_dataset_RGB',source_episode_id=training[0]['source_episode_id'],live_camera=False,backend='diffusion'))
    policy=make_compact_diffusion_policy(settings,state_dim=6,action_dim=6);initial=state_digest(policy);started=time.monotonic()
    optimizer,losses,_=optimize_diffusion(policy,samples,settings,folder,progress,.1,.8,checkpoint_interval=4)
    RGB_gradient=float(policy.diffusion.rgb_encoder[0].backbone[0].weight.grad.norm())
    if not RGB_gradient>0:raise ValueError('No actual RGB encoder gradient in this source/profile run')
    preprocessing=dict(schema='explorer_native_diffusion_preprocessing_v1',profile_id=PROFILE_ID,action_contract_id=outer['action_contract_id'],action_names=list(NAMES),action_units=UNITS,
        state_low=LOW,state_high=HIGH,state_basis='measured_state',state_transform='2*(state-low)/(high-low)-1',action_transform='same_fixed_native_endpoint_normalization',
        RGB_transform='uint8/255_NCHW_bilinear_resize_once',image_size=64,history_steps=2,horizon=8,action_chunk_start_offset=1,chunk_prediction_steps=4,
        official_robot_use_degrees=False,language_input=False,phase_labels_in_actor=False,physical_execution_authorized=False)
    inputs={key:preview[key].unsqueeze(0) for key in (IMAGE_KEY,'observation.state')}
    reloaded,checkpoint,error=save_reload_diffusion(policy,optimizer,folder,preprocessing,inputs)
    heldout=HistoryWindows(view_path,view,development,episode_loader=load_episode,partition='development')
    evaluation=offline_evaluation(reloaded,heldout,NAMES,UNITS,seed=seed)
    for name,expected in outer['source_files_sha256'].items():_file(store,path.parent/name,expected)
    if sha(path)!=config['aggregate_manifest_sha256'] or state_digest(policy)==initial:raise ValueError('Source changed or optimizer did not change weights')
    hashes={file.name:sha(file) for file in checkpoint.iterdir() if file.is_file()};simulation=outer['source_kind']=='SIMULATION'
    result=dict(trained=True,backend='diffusion',profile_id=PROFILE_ID,framework='LeRobot',framework_version='0.6.1',algorithm='official Diffusion RGB_history2+measured_follower6→issued_native_follower6',
        source_kind=outer['source_kind'],source_type='SIMULATION' if simulation else 'SO101_RECORDED_DEMONSTRATIONS',status='trained_offline_only',
        skill_id=outer['skill_id'],skill_version=outer['skill_version'],spec_sha256=outer['spec_sha256'],action_contract_id=outer['action_contract_id'],**identity,
        original_spec_owner_experiment=outer['skill_spec'].get('owner_experiment'),source_manifest_path=str(path),source_manifest_sha256=sha(path),aggregate_manifest_sha256=sha(path),
        official_dataset_readback=official_readback,optimizer_steps=len(losses),optimizer_class=type(optimizer).__module__+'.'+type(optimizer).__qualname__,
        loss_first=losses[0]['loss'],loss_last=losses[-1]['loss'],weights_changed=True,training_resume=optimizer.explorer_resume_info,
        RGB_encoder_first_conv_gradient_norm=RGB_gradient,
        save_reload_passed=True,reload_max_abs_error=error,checkpoint_dir=str(checkpoint),checkpoint_sha256=hashes,checkpoint_content_sha256=digest(hashes),evaluation=evaluation,
        training_original_episode_ids=outer['receipt']['accepted_train_original_episode_ids'],development_original_episode_ids=outer['receipt']['accepted_development_original_episode_ids'],sealed_original_episode_ids=outer['receipt']['sealed_original_episode_ids'],
        sealed_payload_used=False,physical_io=False,simulator_executed=False,rollout_success_claimed=False,physical_success_inferred=False,elapsed_s=time.monotonic()-started,**framework_info())
    card=dict(id='so101-diffusion-'+job['id'],task=outer['skill_spec']['title'],source_type=result['source_type'],source_kind=outer['source_kind'],status='trained_offline_only',
        action_contract=dict(id=outer['action_contract_id'],names=list(NAMES),units=UNITS,state_basis='measured_state',official_robot_use_degrees=False),
        lineage=dict(job_id=job['id'],skill_id=outer['skill_id'],skill_version=outer['skill_version'],spec_sha256=outer['spec_sha256'],dataset_manifest_sha256=sha(path),
            checkpoint_sha256=digest(hashes),backend='diffusion',profile_id=PROFILE_ID,methods=['DIFFUSION_LEROBOT_0_6_1','ENGINEERING_GLUE'],**identity,
            training_original_episode_ids=result['training_original_episode_ids'],development_original_episode_ids=result['development_original_episode_ids'],sealed_original_episode_ids=result['sealed_original_episode_ids']),
        evaluation=evaluation,deployment_scope='SIMULATION_ONLY' if simulation else 'OFFLINE_SHADOW_PENDING_SEPARATE_OBSERVED_TEST',
        limitations=['Source video outcomes are BC targets, not model physical success','Offline Diffusion chunk error is not whole-task rollout','No hardware or replay CLI invoked','Oldnumeric source identity preserved; semantic role selects current experiment','Sealed payload excluded','Text and source phase labels are not model inputs'])
    result['skill_card']=card;write_json(folder/'provenance.json',dict(settings=settings,aggregate_manifest_sha256=sha(path),backend='diffusion',physical_io=False,**identity));write_json(result_path,result)
    if not any(existing['id']==card['id'] for existing in store.skills()):store.add_skill(card)
    progress(.99,'official_Diffusion_native6_optimizer_reload_verified_offline_only');return result
