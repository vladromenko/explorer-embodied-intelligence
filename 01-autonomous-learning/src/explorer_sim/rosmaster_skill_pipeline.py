"""ENGINEERING_GLUE: actual shared ROSMASTER training with ACT_LEROBOT_0_6_1.

No hardware/simulator execution, source production, deployment or external IO.
Offline held-out action/phase errors never establish whole-task learned success.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS,canonical,digest
from explorer_shared.embodied_episode import ACTION_CONTRACT_ID,inspect_embodied_episode
from .rosmaster_skill_features import FEATURE_NAMES,FEATURE_GROUPS,PipelineBlocked,TrainNormalizer,channel,rgb_image,sensor_features
from .rosmaster_skill_hybrid import NAV_PHASES,PhaseCoordinator,PhaseModel,calibrated_goal,navigation_action

JOB_KIND='rosmaster_skill_train'
MODEL_SCHEMA='explorer_rosmaster_skill_model_v1'
AGGREGATE_SCHEMA='explorer_unified_embodied_skill_dataset_v1'


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _json(path):
    if Path(path).stat().st_size>32*1024**2:raise PipelineBlocked('METADATA_SIZE_LIMIT')
    value=json.loads(Path(path).read_text())
    if not isinstance(value,dict):raise PipelineBlocked('INVALID_JSON_DOCUMENT')
    return value


def _member(root,name):
    if not isinstance(name,str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:raise PipelineBlocked('SOURCE_PATH_INVALID',str(name))
    path=(Path(root)/name).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():raise PipelineBlocked('SOURCE_PATH_UNAVAILABLE',name)
    return path


def _write(path,value):
    from .pilot import write_json
    write_json(Path(path),value)


def pipeline_capabilities():
    return dict(schema='rosmaster_skill_pipeline_capabilities_v1',job_kind=JOB_KIND,compatible_source_projects=['02','03'],
        action_contract_id=ACTION_CONTRACT_ID,action_names=list(ACTION_NAMES),action_dimension=9,
        fused_state_dimension=len(FEATURE_NAMES)*2,RGB_real_input=True,qualified_devices=['cpu'],controlplane_imports_torch=False,
        end_to_end_implemented=True,hybrid_implemented=True,whole_autonomous_execution_qualified=False,
        implemented_candidates=['official_ACT9_RGB_available_sensor_fusion','calibrated_goal_Astar_navigation_ACT9_contact_observed_phase_coordinator_recovery'],
        user_robot_modes=['whole_unified_ROSMASTER'],physical_execution=False,automatic_deployment=False,
        baseline_requires=['source_bound_RGB','explicit_recorded_prior_command9','causal_native_or_simulation_dispatch','expert_outcomes','global_episode_scene_session_object_splits'],
        hybrid_additionally_requires=['calibrated_metric_goal','observed_occupancy_map','odometry_common_frame','learned_observation_phase_model','independent_transition_evidence'],
        offline_metrics_establish_mastery=False)


class RosmasterSkillPolicy:
    """Load verified weights and infer9D; caller owns actual sensor/native IO.

    Runtime observation and transition validators are trusted server adapters,
    never booleans supplied by a browser. No target actions or phase labels are
    read as observations, and this class cannot send a motor command.
    """
    def __init__(self,model_manifest,*,candidate='ACT9'):
        if candidate not in ('ACT9','hybrid'):raise PipelineBlocked('UNKNOWN_SHARED_POLICY_CANDIDATE')
        self.path=Path(model_manifest).resolve();self.model=_json(self.path);self.candidate=candidate
        if self.model.get('schema')!=MODEL_SCHEMA or self.model.get('action_contract_id')!=ACTION_CONTRACT_ID or self.model.get('action_names')!=list(ACTION_NAMES):raise PipelineBlocked('MODEL_CONTRACT_MISMATCH')
        for name in ('rosmaster_skill_features.py',)+(('rosmaster_skill_hybrid.py',) if candidate=='hybrid' else ()):
            if (self.model.get('implementation_sha256') or {}).get(name)!=_sha(Path(__file__).with_name(name)):raise PipelineBlocked('MODEL_FEATURE_OR_HYBRID_IMPLEMENTATION_NOT_QUALIFIED',name)
        from lerobot.policies.act.modeling_act import ACTPolicy
        checkpoint=self.path.parent/'student_act'
        for name,expected in self.model.get('weights_sha256',{}).items():
            if _sha(_member(checkpoint,name))!=expected:raise PipelineBlocked('CHECKPOINT_SHA_MISMATCH',name)
        if not self.model.get('weights_sha256'):raise PipelineBlocked('CHECKPOINT_INVENTORY_MISSING')
        self.policy=ACTPolicy.from_pretrained(checkpoint,local_files_only=True,strict=True).cpu().eval()
        if tuple(self.policy.config.input_features['observation.state'].shape)!=(len(FEATURE_NAMES)*2,) or tuple(self.policy.config.output_features['action'].shape)!=(9,):raise PipelineBlocked('SAVED_ACT_FEATURE_DIMENSION_MISMATCH')
        self.normalizer=TrainNormalizer.from_document(self.model['preprocessing']['normalizer']);self.image_size=self.model['preprocessing']['image_size']
        document=self.model['hybrid']['phase_model'];self.phases=document['phases'];self.phase_model=PhaseModel(self.phases)
        self.phase_model.centroids={name:np.asarray(value,float) for name,value in document['centroids'].items()}
        if digest(document)!=self.model.get('hybrid_weights_sha256'):raise PipelineBlocked('HYBRID_PHASE_WEIGHTS_SHA_MISMATCH')
        self.reset()
    def reset(self):
        self.policy.reset();self.coordinator=PhaseCoordinator(self.phases)
    def predict(self,observation,read_payload,*,verify_observation=None,phase_evidence=None,verify_phase_evidence=None):
        import torch
        from .student import IMAGE_KEY,prepare_rgb
        if not callable(verify_observation):raise PipelineBlocked('RUNTIME_SENSOR_ADMISSION_REQUIRED')
        admitted=verify_observation(deepcopy(observation),deepcopy(self.model))
        if not isinstance(admitted,dict):raise PipelineBlocked('RUNTIME_SENSOR_ADMISSION_REQUIRED')
        sample=deepcopy(observation)
        for name,record in (sample.get('channels') or {}).items():record['_model_admitted']=(admitted.get(name) or {}).get('admitted') is True
        rgb=rgb_image(channel(sample,'camera_rgb'),read_payload);values,masks,_=sensor_features(sample,read_payload)
        resized=np.asarray(Image.fromarray(rgb).resize((self.image_size,self.image_size),Image.Resampling.BILINEAR))
        inputs={IMAGE_KEY:prepare_rgb(resized[None]),'observation.state':torch.from_numpy(self.normalizer.state(values,masks)[None])}
        with torch.inference_mode():action=self.normalizer.native_action(self.policy.predict_action_chunk(inputs)[0,0].cpu().numpy())
        feature=np.concatenate([self.normalizer.state(values,masks),rgb.mean(axis=(0,1))/255.,rgb.std(axis=(0,1))/255.])
        try:phase=self.phase_model.predict(feature);phase_diagnostic=None
        except PipelineBlocked as error:
            if self.candidate=='hybrid':raise
            phase='UNKNOWN';phase_diagnostic=error.document()
        metadata=dict(candidate=self.candidate,predicted_phase=phase,phase_diagnostic=phase_diagnostic,hardware_io=False)
        if self.candidate=='hybrid':
            if self.coordinator.index<len(self.phases) and phase!=self.phases[self.coordinator.index]:raise PipelineBlocked('PHASE_PROGRESS_NOT_INDEPENDENTLY_VERIFIED',phase)
            if phase in NAV_PHASES:
                goal,grounding=calibrated_goal(sample,read_payload);action,navigation=navigation_action(sample,goal,action);metadata.update(perception=grounding,navigation=navigation)
        progress=self.coordinator.observe(phase,phase_evidence,verify_evidence=verify_phase_evidence)
        if progress['state']=='RECOVERY_REQUIRED':raise PipelineBlocked('MEASURED_RECOVERY_REQUIRED','No open-loop timed release or automatic movement fallback')
        if action.shape!=(9,) or not np.isfinite(action).all():raise PipelineBlocked('MODEL_PREDICTION_NOT_FINITE_ACTION9')
        if phase_evidence and progress['completed'] and progress['completed'][-1]==phase:self.policy.reset()
        return dict(action9=action.tolist(),action_names=list(ACTION_NAMES),simultaneous_9d=True,phase_progress=progress,**metadata)
    def recover(self,receipt,*,verify_evidence=None):
        result=self.coordinator.recover(receipt,verify_evidence=verify_evidence);self.policy.reset();return result


class CanonicalCorpus:
    def __init__(self,manifest_path):
        self.path=Path(manifest_path).resolve();self.root=self.path.parent;self.sha256=_sha(self.path);self.manifest=_json(self.path)
        manifest=self.manifest
        if manifest.get('schema')!=AGGREGATE_SCHEMA or manifest.get('action_contract_id')!=ACTION_CONTRACT_ID or manifest.get('action_names')!=list(ACTION_NAMES) or manifest.get('simultaneous_9d') is not True:raise PipelineBlocked('CANONICAL_WHOLE_ROSMASTER9_DATASET_REQUIRED')
        self.spec=manifest.get('skill_spec') or {};self.skill_id=manifest.get('skill_id');self.version=manifest.get('skill_version')
        if self.spec.get('skill_id')!=self.skill_id or self.spec.get('version')!=self.version or digest(self.spec)!=manifest.get('skill_spec_sha256'):raise PipelineBlocked('SKILL_SPEC_LINEAGE_MISMATCH')
        self.phases=[item['id'] for item in self.spec.get('stages',[])]
        if not self.phases or len(self.phases)!=len(set(self.phases)):raise PipelineBlocked('WHOLE_SKILL_PHASE_CONTRACT_REQUIRED')
        self.source_files={str(self.path):self.sha256};self.episodes=[];groups={};ids=set();rgb_groups={};variant_splits={}
        inventory=manifest.get('source_files_sha256') or {}
        if not inventory:raise PipelineBlocked('SOURCE_INVENTORY_REQUIRED')
        for name,expected in inventory.items():
            path=_member(self.root,name)
            if _sha(path)!=expected:raise PipelineBlocked('SOURCE_SHA_MISMATCH',name)
            self.source_files[str(path)]=expected
        sealed={row['original_episode_id'] for row in manifest.get('sealed_sources_excluded',[])}
        for descriptor in manifest.get('episodes',[]):
            original=descriptor.get('source_episode_id');partition=descriptor.get('split')
            if not original or original in ids or original in sealed:raise PipelineBlocked('DUPLICATE_OR_SEALED_ORIGINAL',str(original))
            if partition not in ('train','development'):raise PipelineBlocked('SEALED_OR_UNKNOWN_PARTITION_FORBIDDEN',str(partition))
            ids.add(original)
            if descriptor.get('skill_id')!=self.skill_id or descriptor.get('skill_version')!=self.version:raise PipelineBlocked('CROSS_SKILL_OR_VERSION_MIXING')
            inspection=_json(_member(self.root,descriptor['canonical_admission_file']))
            if descriptor['canonical_admission_file'] not in inventory or _sha(_member(self.root,descriptor['canonical_admission_file']))!=descriptor.get('canonical_admission_sha256'):raise PipelineBlocked('ADMISSION_SNAPSHOT_SHA_MISMATCH')
            mapping=descriptor.get('source_artifact_map') or {};document=_json(_member(self.root,mapping.get('__envelope__/original-document.json')))
            envelope=document if document.get('schema')=='explorer_embodied_episode_v1' else document.get('embodied_episode')
            if not isinstance(envelope,dict) or envelope!=inspection.get('envelope'):raise PipelineBlocked('ORIGINAL_ENVELOPE_BINDING_MISMATCH')
            payloads={};paths={};total=0
            for name,expected in (envelope.get('source_files_sha256') or {}).items():
                path=_member(self.root,mapping.get(name));total+=path.stat().st_size
                if total>256*1024**2:raise PipelineBlocked('EPISODE_BYTE_BUDGET_EXCEEDED')
                if _sha(path)!=expected or inventory.get(mapping.get(name))!=expected:raise PipelineBlocked('CANONICAL_PAYLOAD_SHA_MISMATCH',name)
                payloads[name]=path.read_bytes();paths[name]=path
            report=inspect_embodied_episode(envelope,payloads)
            if not report.get('admitted_for_dataset') or not report.get('completed_recording') or not report.get('operator_success'):raise PipelineBlocked('CANONICAL_SOURCE_NOT_EXPERT',','.join(report.get('missing_facts') or []),episode_id=original)
            if envelope.get('episode_id')!=original or (envelope.get('skill') or {}).get('id')!=self.skill_id or (envelope.get('skill') or {}).get('version')!=self.version:raise PipelineBlocked('SOURCE_SKILL_OR_EPISODE_IDENTITY_MISMATCH')
            if (report.get('split') or {}).get('partition')!=partition:raise PipelineBlocked('ORIGINAL_PARTITION_CHANGED')
            if partition=='train' and report.get('optimizer_partition_allowed') is not True:raise PipelineBlocked('OPTIMIZER_PARTITION_NOT_ALLOWED')
            split=report['split'];group_items=[('source_'+key,split[key]) for key in ('scene_id','session_id','object_instance_id','group_id')]
            group_items.extend((('teaching_session',descriptor.get('teaching_session_id')),('teaching_scene',descriptor.get('scene'))))
            for key,value in group_items:
                if value:
                    if (key,value) in groups and groups[key,value]!=partition:raise PipelineBlocked('GLOBAL_SPLIT_GROUP_LEAKAGE',key)
                    groups[key,value]=partition
            variant=descriptor.get('variant_id')
            if variant in variant_splits and variant_splits[variant]!=partition:raise PipelineBlocked('VARIANT_SPLIT_CHANGED')
            variant_splits[variant]=partition
            samples=envelope.get('samples') or [];labels=[None]*len(samples)
            for phase in envelope.get('phases',[]):
                if phase['id'] not in self.phases:raise PipelineBlocked('SOURCE_PHASE_OUTSIDE_SKILL')
                for index in range(phase['start_sample'],phase['end_sample']+1):labels[index]=phase['id']
            if any(label is None for label in labels):raise PipelineBlocked('SOURCE_PHASE_COVERAGE_INCOMPLETE')
            source_outcomes=(envelope.get('outcomes') or {}).get('per_phase') or {}
            targets=descriptor.get('target_phase_ids') or self.phases
            if not set(targets).issubset(labels):raise PipelineBlocked('TARGET_PHASE_NOT_RECORDED')
            if not targets or set(targets)-set(self.phases) or any((source_outcomes.get(phase) or {}).get('status')!='success' or (source_outcomes.get(phase) or {}).get('independent_of_action_commands') is not True for phase in targets):raise PipelineBlocked('FAILED_PHASE_CANNOT_BE_EXPERT_TARGET')
            if descriptor.get('episode_scope')=='whole_episode':
                if set(targets)!=set(self.phases) or envelope.get('episode_scope')!='whole_episode' or set(labels)!=set(self.phases) or ((envelope.get('outcomes') or {}).get('end_to_end') or {}).get('status')!='success':raise PipelineBlocked('WHOLE_SOURCE_PHASE_OR_OUTCOME_INCOMPLETE')
            frames=[]
            def read_payload(name):return _member(self.root,mapping.get(name)).read_bytes()
            for index,sample in [(position,value) for position,value in enumerate(samples) if labels[position] in targets]:
                sample=deepcopy(sample)
                for name,record in (sample.get('channels') or {}).items():record['_model_admitted']=(report.get('channels',{}).get(name) or {}).get('admitted') is True
                rgb=channel(sample,'camera_rgb') or {};rgb_sha=rgb.get('sha256');identity=(rgb.get('camera_id'),rgb.get('frame_id'))
                for key in (('RGBbytes',rgb_sha),('RGBidentity',identity)):
                    if key in rgb_groups and rgb_groups[key]!=partition:raise PipelineBlocked('RGB_SOURCE_OVERLAPS_SPLITS')
                    rgb_groups[key]=partition
                image=rgb_image(rgb,read_payload);values,masks,used=sensor_features(sample,read_payload)
                action=np.asarray(sample['action']['values'],np.float32)
                if action.shape!=(9,) or not np.isfinite(action).all():raise PipelineBlocked('SIMULTANEOUS_ACTION9_REQUIRED')
                frames.append(dict(values=values,masks=masks,used=used,action=action,phase=labels[index],original_sample_index=index,rgb_record=rgb,
                    rgb_summary=np.concatenate([image.mean(axis=(0,1))/255.,image.std(axis=(0,1))/255.]).astype(np.float32),sample=sample))
            self.episodes.append(dict(descriptor=descriptor,envelope=envelope,report=report,frames=frames,paths=paths))
        self.train=[episode for episode in self.episodes if episode['descriptor']['split']=='train'];self.development=[episode for episode in self.episodes if episode['descriptor']['split']=='development']
        if not self.train or not self.development:raise PipelineBlocked('INDEPENDENT_TRAIN_AND_DEVELOPMENT_EPISODES_REQUIRED')
        for partition,episodes in (('train',self.train),('development',self.development)):
            if not any(episode['descriptor'].get('episode_scope')=='whole_episode' for episode in episodes):raise PipelineBlocked('INDEPENDENT_WHOLE_EPISODE_REQUIRED',partition)
            covered={frame['phase'] for episode in episodes for frame in episode['frames']}
            if set(self.phases)-covered:raise PipelineBlocked('PARTITION_WHOLE_PHASE_COVERAGE_MISSING',partition)
        training=[frame for episode in self.train for frame in episode['frames']]
        self.normalizer=TrainNormalizer([row['values'] for row in training],[row['masks'] for row in training],[row['action'] for row in training])
        self.sealed_ids=sorted(sealed)
    def recheck(self):
        for name,expected in self.source_files.items():
            if _sha(name)!=expected:raise PipelineBlocked('SOURCE_CHANGED_DURING_TRAINING',name)
    def read(self,episode,name):
        if name not in episode['paths']:raise PipelineBlocked('UNDECLARED_SENSOR_PAYLOAD',str(name))
        return episode['paths'][name].read_bytes()


class FusedChunks:
    def __init__(self,corpus,episodes,chunk_size,image_size):
        self.corpus,self.episodes,self.chunk_size,self.image_size=corpus,episodes,chunk_size,image_size
        self.indices=[(episode_index,index) for episode_index,episode in enumerate(episodes) for index in range(len(episode['frames']))]
        self.resume_context=dict(source_signature=corpus.sha256,source_files=[dict(path=path,sha256=expected) for path,expected in sorted(corpus.source_files.items())],
            source_implementation_sha256={name:_sha(Path(__file__).with_name(name)) for name in ('rosmaster_skill_features.py','rosmaster_skill_hybrid.py')},
            skill_id=corpus.skill_id,skill_version=corpus.version,training_episode_ids=[row['descriptor']['source_episode_id'] for row in episodes],
            feature_schema=corpus.normalizer.document(),chunk_size=chunk_size,image_size=image_size,actor_inputs='real_RGB_and_source_sensor_values_with_availability_only')
    def __len__(self):return len(self.indices)
    def __getitem__(self,index):
        import torch
        from .student import IMAGE_KEY,prepare_rgb
        episode_index,position=self.indices[index];episode=self.episodes[episode_index];frame=episode['frames'][position]
        image=rgb_image(frame['rgb_record'],lambda name:self.corpus.read(episode,name))
        image=np.asarray(Image.fromarray(image).resize((self.image_size,self.image_size),Image.Resampling.BILINEAR))
        slots=position+np.arange(self.chunk_size);padded=slots>=len(episode['frames']);slots=np.minimum(slots,len(episode['frames'])-1)
        discontinuity=np.array([episode['frames'][slot]['original_sample_index']!=frame['original_sample_index']+offset for offset,slot in enumerate(slots)])
        padded|=np.maximum.accumulate(discontinuity)
        last_valid=int(np.flatnonzero(~padded)[-1]);slots=np.where(padded,slots[last_valid],slots)
        actions=np.asarray([episode['frames'][slot]['action'] for slot in slots])
        return {IMAGE_KEY:prepare_rgb(image[None])[0],'observation.state':torch.from_numpy(self.corpus.normalizer.state(frame['values'],frame['masks'])),
            'action':torch.from_numpy(self.corpus.normalizer.action(actions)),'action_is_pad':torch.from_numpy(padded)}


def phase_observation(normalizer,frame):return np.concatenate([normalizer.state(frame['values'],frame['masks']),frame['rgb_summary']])


def hybrid_prediction(phase_model,corpus,episode,frame,act_action):
    phase=phase_model.predict(phase_observation(corpus.normalizer,frame))
    if phase in NAV_PHASES:
        goal,grounding=calibrated_goal(frame['sample'],lambda name:corpus.read(episode,name))
        action,navigation=navigation_action(frame['sample'],goal,act_action)
        return action,dict(predicted_phase=phase,component='navigation_with_recorded_arm_command_hold',perception=grounding,navigation=navigation)
    return np.asarray(act_action),dict(predicted_phase=phase,component='ACT9_contact_or_coordinated_motion',simultaneous_9d=True)


def offline_metrics(policy,corpus,samples,phase_model,device):
    import torch
    from .student import IMAGE_KEY
    baseline=np.zeros(9);hybrid=np.zeros(9);count=0;hybrid_count=0;blocked=Counter();phase_correct=0;predictions=[]
    policy.eval()
    for index,(episode_index,position) in enumerate(samples.indices):
        item=samples[index];episode=samples.episodes[episode_index];frame=episode['frames'][position]
        inputs={key:item[key].unsqueeze(0).to(device) for key in (IMAGE_KEY,'observation.state')}
        with torch.inference_mode():predicted=corpus.normalizer.native_action(policy.predict_action_chunk(inputs)[0,0].cpu().numpy())
        baseline+=np.abs(predicted-frame['action']);count+=1
        try:
            candidate,metadata=hybrid_prediction(phase_model,corpus,episode,frame,predicted)
            hybrid+=np.abs(candidate-frame['action']);hybrid_count+=1;phase_correct+=metadata['predicted_phase']==frame['phase']
            predictions.append(dict(episode_id=episode['descriptor']['source_episode_id'],sample_index=position,actual_target9=frame['action'].tolist(),ACT9=predicted.tolist(),hybrid9=candidate.tolist(),**metadata))
        except PipelineBlocked as error:
            blocked[error.code]+=1;predictions.append(dict(episode_id=episode['descriptor']['source_episode_id'],sample_index=position,actual_target9=frame['action'].tolist(),ACT9=predicted.tolist(),hybrid_blocked=error.document()))
    def errors(total,n):return {name:dict(mae_native=float(total[index]/max(n,1)),unit=ACTION_UNITS[index]) for index,name in enumerate(ACTION_NAMES)}
    return dict(domain='independent_development_offline_next_action_and_observation_phase_diagnostics',same_matched_development_samples=True,
        ACT9=dict(evaluated_samples=count,action_error=errors(baseline,count)),hybrid=dict(evaluated_samples=hybrid_count,blocked_samples=count-hybrid_count,blocked_reasons=dict(blocked),action_error=errors(hybrid,hybrid_count) if hybrid_count else None,phase_accuracy_on_executed_predictions=phase_correct/max(hybrid_count,1)),
        raw_predictions=predictions,sealed_test_used=False,learned_policy_rollout_executed=False,whole_task_success='NOT_EVALUATED',physical_success=False)


def train_pipeline(dataset_manifest,output_dir,*,settings=None,progress=None):
    import torch
    from .student import IMAGE_KEY,framework_info,make_act_policy,optimize_act,save_reload_act
    from .pilot import state_digest
    options=dict(seed=41,act_steps=8,batch_size=2,image_size=32,chunk_size=2,device='cpu',threads=1)
    if settings and set(settings)-set(options):raise ValueError('Unsupported fused ACT setting')
    options.update(settings or {})
    for name,low,high in (('seed',0,1000000),('act_steps',1,100000),('batch_size',1,32),('image_size',32,224),('chunk_size',1,100),('threads',1,4)):
        if type(options[name]) is not int or not low<=options[name]<=high:raise ValueError('Invalid fused ACT setting: '+name)
    if options['device']!='cpu':raise PipelineBlocked('FUSED_ACT_DEVICE_NOT_QUALIFIED','This new feature shape has CPU qualification only; old6D/CUDA receipts do not apply')
    torch.set_num_threads(options['threads']);started=time.monotonic();corpus=CanonicalCorpus(dataset_manifest);folder=Path(output_dir);folder.mkdir(parents=True,exist_ok=True)
    training=FusedChunks(corpus,corpus.train,options['chunk_size'],options['image_size']);development=FusedChunks(corpus,corpus.development,options['chunk_size'],options['image_size'])
    settings_act={key:value for key,value in options.items() if key!='threads'}
    policy=make_act_policy(settings_act,len(FEATURE_NAMES)*2,9);initial=state_digest(policy)
    progress=progress or (lambda *args:None)
    optimizer,losses,_=optimize_act(policy,training,settings_act,folder,progress,.05,.75,checkpoint_interval=min(25,options['act_steps']))
    phase_rows=[row for episode in corpus.train for row in episode['frames']]
    phase_model=PhaseModel(corpus.phases).fit([phase_observation(corpus.normalizer,row) for row in phase_rows],[row['phase'] for row in phase_rows])
    preview=training[0];inputs={key:preview[key].unsqueeze(0).to('cpu') for key in (IMAGE_KEY,'observation.state')}
    preprocessing=dict(schema='rosmaster_fused_ACT_preprocessing_v1',normalizer=corpus.normalizer.document(),image_key=IMAGE_KEY,
        RGB='actual_source_RGB_UINT8_resize_bilinear_to_declared_size_once_ImageNet_normalization',image_size=options['image_size'],actor_state_dimension=len(FEATURE_NAMES)*2,
        action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),action_contract_id=ACTION_CONTRACT_ID,
        source_phase_labels_or_privileged_object_truth_in_actor=False,phase_is_not_actor_input=True,neutral_channels_retained=True)
    reloaded,checkpoint,error=save_reload_act(policy,optimizer,folder,preprocessing,inputs,'cpu')
    evaluation=offline_metrics(reloaded,corpus,development,phase_model,'cpu');corpus.recheck()
    used=Counter();recorded=set();admitted=set()
    for episode in corpus.train:
        for name,channel_report in episode['report']['channels'].items():
            if channel_report.get('recorded'):recorded.add(name)
            if channel_report.get('admitted'):admitted.add(name)
        for frame in episode['frames']:used.update(frame['used'])
    used_channels=['camera_rgb']+[name for name,count in sorted(used.items()) if count]
    navigation_used=any(row.get('navigation') for row in evaluation['raw_predictions'])
    hybrid_used=sorted(set(used_channels)|({'camera_calibration','odometry_pose','goal','lidar','preceding_command9'} if navigation_used else set()))
    model=dict(schema=MODEL_SCHEMA,job_kind=JOB_KIND,status='TRAINED_OFFLINE_CANDIDATE_NOT_ROLLOUT_QUALIFIED',skill_id=corpus.skill_id,skill_version=corpus.version,
        skill_spec_sha256=corpus.manifest['skill_spec_sha256'],aggregate_sha256=corpus.sha256,source_files_sha256=corpus.source_files,
        action_contract_id=ACTION_CONTRACT_ID,action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),simultaneous_9d=True,
        ACT9=dict(implemented=True,official_module=type(policy).__module__,state_dimension=len(FEATURE_NAMES)*2,output_dimension=9,RGB_input=True,model_used_channels=used_channels),
        hybrid=dict(implemented=True,phase_model=phase_model.document(),navigation='A_star_observed_occupancy_grid_body_trajectory_tracking',perception='recorded_calibrated_goal_or_locked_RGB_detection_and_depth',
            transitions='independent_phase_outcome_receipt_required',recovery='measured_relocalization_then_policy_reset; no fabricated_contact_or_timed_release',runtime_capability_missing=evaluation['hybrid']['blocked_reasons'],model_used_channels=hybrid_used),
        preprocessing=preprocessing,recorded_channels=sorted(recorded),admitted_channels=sorted(admitted),model_used_channels=used_channels,valid_training_feature_counts=dict(used),
        model_channel_use={name:dict(value_features_used=bool(used[name]),availability_feature_present=True,valid_training_value_count=used[name]) for name,_ in FEATURE_GROUPS},
        train_original_episode_ids=[row['descriptor']['source_episode_id'] for row in corpus.train],development_original_episode_ids=[row['descriptor']['source_episode_id'] for row in corpus.development],sealed_original_episode_ids=corpus.sealed_ids,
        input_adapters_are_provenance_only=True,compatible_source_projects=['02','03'],source_kind=sorted({row['report']['source_kind'] for row in corpus.episodes}),
        weights_sha256={path.name:_sha(path) for path in sorted(checkpoint.iterdir()) if path.is_file()},hybrid_weights_sha256=digest(phase_model.document()),
        implementation_sha256={Path(__file__).name:_sha(__file__), 'rosmaster_skill_features.py':_sha(Path(__file__).with_name('rosmaster_skill_features.py')),'rosmaster_skill_hybrid.py':_sha(Path(__file__).with_name('rosmaster_skill_hybrid.py'))},
        offline_evaluation=evaluation,physical_execution_qualified=False,whole_skill_mastered=False,method_ids=['ACT_LEROBOT_0_6_1','ENGINEERING_GLUE'])
    _write(folder/'model_manifest.json',model)
    card=dict(id='rosmaster-skill-'+_sha(folder/'model_manifest.json')[:24],task=corpus.spec.get('title'),status='trained_offline_candidate_only',
        action_contract=dict(id=ACTION_CONTRACT_ID,names=list(ACTION_NAMES),units=list(ACTION_UNITS),dimension=9,neutral_channels_retained=True),
        lineage=dict(skill_id=corpus.skill_id,skill_version=corpus.version,aggregate_sha256=corpus.sha256,model_manifest_sha256=_sha(folder/'model_manifest.json'),workflow_family='rosmaster_shared_demonstration_pipeline_v1',compatible_source_projects=['02','03']),
        model_input_schema=preprocessing,recorded_channels=model['recorded_channels'],admitted_channels=model['admitted_channels'],model_used_channels=used_channels,checkpoint_sha256=model['weights_sha256'],
        evaluation=evaluation,limitations=['No learned-policy rollout or physical trial from this offline job','Hybrid blocks absent essential navigation/geometry/transition evidence','Phase labels are supervised targets, never actor observations','Sealed payloads excluded from optimization and development metrics'])
    result=dict(trained=True,optimizer_steps=len(losses),weights_changed=state_digest(policy)!=initial,save_reload_passed=True,reload_max_abs_error=error,
        algorithm='official LeRobot compact ACT9 available-sensor fusion plus separately composed hybrid candidate',job_kind=JOB_KIND,feature_dimension=len(FEATURE_NAMES)*2,
        output_action_dimension=9,device='cpu',checkpoint_dir=str(checkpoint),checkpoint_sha256=model['weights_sha256'],model_manifest=str(folder/'model_manifest.json'),
        model_manifest_sha256=_sha(folder/'model_manifest.json'),source_manifest_path=str(corpus.path),source_manifest_sha256=corpus.sha256,
        training_resume=optimizer.explorer_resume_info,evaluation=evaluation,skill_card=card,physical_io=False,simulator_executed=False,learned_rollout_executed=False,elapsed_s=time.monotonic()-started,**framework_info())
    _write(folder/'result.json',result);progress(1.,'shared_rosmaster_ACT9_and_hybrid_offline_receipts_saved')
    return result


def train_rosmaster_skill(store,job):
    config=job.get('config') or {};source=config.get('dataset_manifest')
    if not source:raise PipelineBlocked('CANONICAL_AGGREGATE_REQUIRED')
    source=Path(source).expanduser().resolve()
    if not source.is_relative_to(Path(store.root).resolve()):raise PipelineBlocked('MANAGED_WORKER_AGGREGATE_REQUIRED')
    if job.get('kind')!=JOB_KIND or job.get('project_id') not in ('02','03'):raise PipelineBlocked('SHARED_ROSMASTER_JOB_KIND_AND_PROJECT_REQUIRED')
    lineage=config.get('skill_workflow')
    if not isinstance(lineage,dict):raise PipelineBlocked('SERVER_BOUND_SKILL_AGGREGATE_LINEAGE_REQUIRED')
    if _sha(source)!=lineage.get('aggregate_sha256'):raise PipelineBlocked('QUEUED_AGGREGATE_SHA_CHANGED')
    manifest=_json(source)
    if manifest.get('skill_id')!=lineage.get('skill_id') or manifest.get('skill_version')!=lineage.get('skill_version') or manifest.get('skill_spec_sha256')!=lineage.get('spec_sha256'):raise PipelineBlocked('QUEUED_SKILL_SPEC_LINEAGE_CHANGED')
    partitions={'training_original_episode_ids':'train','development_original_episode_ids':'development'}
    for key,partition in partitions.items():
        actual={row.get('source_episode_id') for row in manifest.get('episodes',[]) if row.get('split')==partition}
        declared=lineage.get(key)
        if not isinstance(declared,list) or len(declared)!=len(set(declared)) or set(declared)!=actual:raise PipelineBlocked('QUEUED_SOURCE_PARTITION_LINEAGE_CHANGED',key)
    if set(lineage.get('sealed_original_episode_ids') or [])!={row['original_episode_id'] for row in manifest.get('sealed_sources_excluded',[])}:raise PipelineBlocked('QUEUED_SEALED_LINEAGE_CHANGED')
    options={key:config[key] for key in ('seed','act_steps','batch_size','image_size','chunk_size','device','threads') if key in config}
    result=train_pipeline(source,store.root/'jobs'/job['id'],settings=options,progress=lambda value,phase:store.update_job(job['id'],progress=value,result=dict(phase=phase)))
    result['skill_card']['lineage']['job_id']=job['id']
    if not any(item['id']==result['skill_card']['id'] for item in store.skills()):store.add_skill(result['skill_card'])
    _write(store.root/'jobs'/job['id']/'result.json',result)
    return result
