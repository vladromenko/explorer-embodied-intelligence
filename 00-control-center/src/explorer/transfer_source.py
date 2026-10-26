"""ENGINEERING_GLUE: current source04 model selection for semantic transfer06.

Control-plane import is Torch/robot free. Technical checkpoint qualification,
offline source predictions and a closed-loop object rollout are separate facts.
Missing evaluation owner is explicit software work, never hidden as missing data.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
from pathlib import Path
import re

from explorer_shared.contracts import digest
from explorer_shared.experiments import experiment_id
from explorer_shared.so101_station import NAMES, UNITS, LOW, HIGH

CONTRACT = 'so101_follower_calibrated_position6_v1'
EVALUATION_SCHEMA = 'explorer_so101_source_model_object_evaluation_v1'
ID = re.compile('[0-9a-f]{32}')
HASH = re.compile('[0-9a-f]{64}')


class SourceBlocked(ValueError):
    def __init__(self, blockers):
        self.blockers = blockers
        super().__init__(','.join(row['code'] for row in blockers))


def _block(code, category, detail):
    return dict(code=code, category=category, detail=detail)


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _file(store, value, expected=None):
    path = Path(value).resolve(strict=True)
    if not path.is_relative_to(Path(store.root).resolve()) or not path.is_file():
        raise ValueError('Source artifact escaped managed state')
    if expected is not None and (not isinstance(expected, str) or not HASH.fullmatch(expected) or _sha(path) != expected):
        raise ValueError('Source artifact SHA changed')
    return path


def _json(path):
    path=Path(path)
    if path.stat().st_size>16*1024**2:
        raise ValueError('Bounded source metadata required')
    return json.loads(path.read_bytes())


def evaluation_owner_capabilities():
    """Future actual observed source evaluator owns this pure capability hook."""
    try:
        module = importlib.import_module('.so101_source_evaluation', __package__)
    except ModuleNotFoundError as error:
        if error.name != __package__ + '.so101_source_evaluation':
            raise
        return dict(implemented=False, physical_execution_authorized=False,
            reason='A_SOURCE_EVALUATION_OWNER_NOT_IMPLEMENTED')
    return module.source_evaluation_capabilities()


def _admission(store, config):
    from .so101_dataset_admission import verify_so101_aggregate
    return verify_so101_aggregate(store, config)


def _metadata(store, job, *, admission_verifier=None):
    config, result = job.get('config') or {}, job.get('result') or {}
    if job.get('kind') != 'so101_skill_train' or job.get('status') != 'completed' or job.get('project_id') != experiment_id('so101_source_learning'):
        raise ValueError('Completed current semantic source04 whole-skill model required')
    path, outer, view_path, view = (admission_verifier or _admission)(store, config)
    if (any(result.get(key) != outer.get(key) for key in ('skill_id','skill_version','spec_sha256','action_contract_id','source_kind')) or
            result.get('aggregate_manifest_sha256') != config['aggregate_manifest_sha256'] or
            result.get('source_manifest_sha256') != config['aggregate_manifest_sha256'] or
            result.get('framework')!='LeRobot' or result.get('framework_version')!='0.6.1' or
            result.get('sealed_payload_used') is not False or
            not all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed')) or
            type(result.get('optimizer_steps')) is not int or result['optimizer_steps'] < 1):
        raise ValueError('Current model optimizer/reload/Spec/source lineage differs')
    if outer.get('source_kind') not in ('SIMULATION','SO101_RECORDED_DEMONSTRATIONS') or outer.get('action_contract_id') != CONTRACT:
        raise ValueError('Unknown source domain or native6 contract')
    receipt = _file(store, Path(store.root)/'jobs'/job['id']/'result.json')
    original = _json(receipt)
    for key in ('skill_id','skill_version','spec_sha256','aggregate_manifest_sha256','checkpoint_sha256','source_kind','trained','save_reload_passed'):
        if original.get(key) != result.get(key):
            raise ValueError('Original immutable training receipt differs')
    checkpoint = Path(result['checkpoint_dir']).resolve()
    if not checkpoint.is_relative_to((Path(store.root)/'jobs'/job['id']).resolve()) or not checkpoint.is_dir():
        raise ValueError('Checkpoint is not the selected original job')
    hashes = result.get('checkpoint_sha256') or {}
    if not isinstance(hashes, dict) or not hashes or any(Path(name).name != name for name in hashes):
        raise ValueError('Explicit complete checkpoint SHA map required')
    if {entry.name for entry in checkpoint.iterdir() if entry.is_file()} != set(hashes):
        raise ValueError('Unbound checkpoint/processor members')
    for name, checksum in hashes.items():
        _file(store, checkpoint/name, checksum)
    if result.get('checkpoint_content_sha256') != digest(hashes):
        raise ValueError('Checkpoint content SHA differs')
    saved = _json(checkpoint/'config.json')
    backend = saved.get('type')
    if backend not in ('act','diffusion') or any(value not in (None, backend) for value in (config.get('backend'), result.get('backend'))):
        raise ValueError('Explicit official saved backend differs from training owner')
    if saved.get('device') != 'cpu':
        raise ValueError('Current native6 source loaders own CPU-qualified models')
    if saved.get('input_features',{}).get('observation.state',{}).get('shape') != [6] or saved.get('output_features',{}).get('action',{}).get('shape') != [6]:
        raise ValueError('Native6 source state/action head required; no silent12D reduction')
    visual={key:value for key,value in saved.get('input_features',{}).items() if key.startswith('observation.images.')}
    shape=(visual.get('observation.images.overview') or {}).get('shape')
    if len(visual)!=1 or not isinstance(shape,list) or len(shape)!=3 or shape[0]!=3 or shape[1]!=shape[2] or not 32<=shape[1]<=224:
        raise ValueError('Exact qualified source RGB slot/shape required')
    normalization=saved.get('normalization_mapping') or {}
    if set(normalization)!={'VISUAL','STATE','ACTION'} or any(value!='IDENTITY' for value in normalization.values()):
        raise ValueError('Saved source identity normalization required; no duplicate preprocessing')
    pre = _json(checkpoint/'explorer_preprocessing.json')
    expected = dict(action_contract_id=CONTRACT, action_names=list(NAMES), action_units=list(UNITS),
        state_low=list(LOW), state_high=list(HIGH), state_basis='measured_state', official_robot_use_degrees=False)
    if any(pre.get(key) != value for key, value in expected.items()):
        raise ValueError('Actual source normalization/native coordinate contract differs')
    error=result.get('reload_max_abs_error')
    if type(error) not in (int,float) or not math.isfinite(error) or not 0<=error<=1e-6 or (result.get('official_dataset_readback') or {}).get('official_readback_passed') is not True:
        raise ValueError('Actual official dataset/save-reload qualification required')
    calibration = ((outer.get('source_calibration_metadata') or {}).get('calibration_metadata') or {}).get('follower') or {}
    calibration_sha = calibration.get('calibration_sha256')
    cameras = sorted({camera for row in view.get('source_records',[]) for camera in row['source_evidence']['camera_ids']})
    if not isinstance(calibration_sha,str) or not HASH.fullmatch(calibration_sha) or not cameras:
        raise ValueError('Source camera/follower calibration SHA unavailable')
    if backend == 'act':
        required = {'config.json','model.safetensors','explorer_preprocessing.json','policy_preprocessor.json','policy_postprocessor.json'}
        transforms=dict(state_transform='2*(state-low)/(high-low)-1',action_transform='same fixed endpoint normalization',
            rgb_layout='NCHW',rgb_scale='uint8 / 255',mean=[.485,.456,.406],std=[.229,.224,.225],
            temporal_ensembling=False,chunk_execution_steps=1)
        processor_calibration=(((pre.get('source_calibration_metadata') or {}).get('calibration_metadata') or {}).get('follower') or {}).get('calibration_sha256')
        if (not required <= hashes.keys() or result.get('official_saved_processors_executed') is not True or
                type(result.get('saved_processor_reload_max_abs_error')) not in (int,float) or not math.isfinite(result['saved_processor_reload_max_abs_error']) or not 0<=result['saved_processor_reload_max_abs_error']<=1e-6 or
                any(pre.get(key)!=value for key,value in transforms.items()) or
                saved.get('n_action_steps')!=1 or saved.get('temporal_ensemble_coeff') is not None or
                pre.get('source_camera_ids') != cameras or
                processor_calibration != calibration_sha):
            raise ValueError('Saved official ACT processor/camera/calibration qualification differs')
        history, predictions = 1, 1
    else:
        from explorer_shared.learning.diffusion_backend import PROFILE_ID,compact_profile
        required = {'config.json','model.safetensors','explorer_preprocessing.json'}
        if (not required <= hashes.keys() or pre.get('profile_id') != PROFILE_ID or
                result.get('profile_id') != PROFILE_ID or pre.get('history_steps') != 2 or pre.get('chunk_prediction_steps') != 4 or
                pre.get('image_size') != 64 or pre.get('RGB_transform') != 'uint8/255_NCHW_bilinear_resize_once'):
            raise ValueError('Distinct qualified Diffusion history2/profile/processor required')
        for key,value in compact_profile()['configuration'].items():
            expected=list(value) if isinstance(value,tuple) else value
            if saved.get(key)!=expected:raise ValueError('Saved native Diffusion compact profile differs: '+key)
        history, predictions = 2, 4
    cards = [card for card in store.skills() if card.get('id') == (result.get('skill_card') or {}).get('id')]
    expected_lineage=dict(job_id=job['id'],skill_id=outer['skill_id'],skill_version=outer['skill_version'],
        spec_sha256=outer['spec_sha256'],dataset_manifest_sha256=config['aggregate_manifest_sha256'],checkpoint_sha256=digest(hashes))
    if (not cards or any(cards[0].get('lineage',{}).get(key)!=value for key,value in expected_lineage.items()) or
            cards[0].get('source_kind')!=outer['source_kind'] or
            any(cards[0].get('action_contract',{}).get(key)!=value for key,value in
                dict(id=CONTRACT,names=list(NAMES),units=list(UNITS),state_basis='measured_state',official_robot_use_degrees=False).items())):
        raise ValueError('Registered native source SkillCard missing or changed')
    episodes = [dict(id=row.get('id'), original_episode_id=row.get('source_episode_id'),split=row['split'])
        for row in view.get('episodes',[]) if row.get('split') in ('train','development')]
    return dict(job=job, result=result, manifest=outer, manifest_path=str(path), view=view,
        view_path=str(view_path), checkpoint_path=str(checkpoint), checkpoint_sha256=hashes,
        checkpoint_content_sha256=digest(hashes), backend=backend, preprocessing=pre,
        source_camera_ids=cameras, source_calibration_sha256=calibration_sha,
        source_domain='SIMULATION' if outer['source_kind']=='SIMULATION' else 'PHYSICAL',
        history_steps=history, prediction_steps=predictions, unsealed_episode_choices=episodes,
        training_receipt_sha256=_sha(receipt), source_profile=dict(action_contract_id=CONTRACT,
            names=list(NAMES), units=list(UNITS), low=list(LOW), high=list(HIGH)))


def _qualified_trace(store, model, evaluation):
    from explorer_shared.skill_transfer import load_source_trace
    result = evaluation.get('result') or {}
    if (evaluation.get('kind') != 'so101_skill_eval' or evaluation.get('status') != 'completed' or
            evaluation.get('project_id') != experiment_id('so101_source_learning') or
            (evaluation.get('config') or {}).get('source_model_job_id') != model['job']['id'] or
            result.get('schema') != EVALUATION_SCHEMA or result.get('source_model_job_id') != model['job']['id'] or
            result.get('execution_kind') != 'fresh_observation_learned_policy_rollout' or
            result.get('independent_full_task_success') is not True or result.get('raw_episode_replay_only') is not False or
            result.get('source_domain') != model['source_domain']):
        raise ValueError('Actual typed independent closed-loop source object evaluation required')
    for key, expected in (('skill_id',model['manifest']['skill_id']),('skill_version',model['manifest']['skill_version']),
        ('spec_sha256',model['manifest']['spec_sha256']),('aggregate_manifest_sha256',model['result']['aggregate_manifest_sha256']),
        ('checkpoint_content_sha256',model['checkpoint_content_sha256']),('source_calibration_sha256',model['source_calibration_sha256'])):
        if result.get(key) != expected:
            raise ValueError('Source evaluation/model/Spec/calibration lineage differs')
    if result.get('camera_id') not in model['source_camera_ids']:
        raise ValueError('Source evaluation camera differs')
    if model['source_domain']=='PHYSICAL' and (result.get('separate_user_observed_test') is not True or result.get('existing_controller_settings_preserved') is not True):
        raise ValueError('Separate observed physical source test required; this reader authorizes no motion')
    trace_path = _file(store,result['source_trace_manifest'],result['source_trace_sha256'])
    bundle = load_source_trace(trace_path,result['source_trace_sha256'])
    source = bundle['document'];q=source.get('qualification') or {}
    if (source.get('source_kind')!='QUALIFIED_SOURCE_MODEL' or source.get('skill_id')!=model['manifest']['skill_id'] or
            source.get('skill_version')!=model['manifest']['skill_version'] or source.get('split',{}).get('partition')=='sealed_test' or
            q.get('source_model_sha256')!=model['checkpoint_sha256']['model.safetensors'] or
            evaluation['id'] not in q.get('evaluation_run_ids',[]) or
            q.get('source_checkpoint_content_sha256')!=model['checkpoint_content_sha256']):
        raise ValueError('Qualified source trace is sealed, foreign or not this actual model')
    inventory=set(source['source_files_sha256'].values())
    if not set(model['checkpoint_sha256'].values()) <= inventory:
        raise ValueError('All saved source processors/checkpoint bytes must be retained')
    if q.get('processor_sha256')!=model['checkpoint_sha256']['explorer_preprocessing.json']:
        raise ValueError('Exact source coordinate/processor receipt required')
    phases={stage['id'] for stage in model['manifest']['skill_spec']['stages']}
    if {frame['phase_id'] for frame in source['frames']}!=phases:
        raise ValueError('Whole source phases incomplete')
    names=[name for name,checksum in source['source_files_sha256'].items() if checksum==q.get('evaluation_receipt_sha256')]
    if len(names)!=1:
        raise ValueError('Unique retained independent evaluation receipt required')
    raw_evaluation=_json(trace_path.parent/names[0])
    if (raw_evaluation.get('schema')!=EVALUATION_SCHEMA or raw_evaluation.get('source_model_job_id')!=model['job']['id'] or
            raw_evaluation.get('checkpoint_content_sha256')!=model['checkpoint_content_sha256'] or
            raw_evaluation.get('independent_full_task_success') is not True or
            raw_evaluation.get('execution_kind')!='fresh_observation_learned_policy_rollout' or
            raw_evaluation.get('source_domain')!=model['source_domain']):
        raise ValueError('Retained independent evaluation differs from this model/domain')
    outcomes=raw_evaluation.get('per_phase') or {}
    if set(outcomes)!=phases or any(row.get('status')!='success' or row.get('independent_of_action_commands') is not True for row in outcomes.values()):
        raise ValueError('Actual independent whole/per-phase source outcomes required')
    steps=raw_evaluation.get('steps') or []
    if len(steps)!=len(source['frames']):
        raise ValueError('Retained source policy steps/trace count differs')
    receipts=set();last_tick=None;epoch=None
    for index,(step,frame) in enumerate(zip(steps,source['frames'])):
        values=frame['native_action6'];stamp=step.get('dispatch_stamp') or {};observed=step.get('observation_stamp') or {}
        current_epoch=(stamp.get('clock_domain'),stamp.get('boot_id'));tick=stamp.get('monotonic_ns');obs_tick=observed.get('monotonic_ns')
        frame_name=frame.get('source_rgb_file');inputs=step.get('model_input_frame_ids') or []
        if (step.get('index')!=index or step.get('phase_id')!=frame['phase_id'] or step.get('reference')!=frame['reference'] or
                step.get('actual_dispatched_native6')!=values or not isinstance(values,list) or
                any(type(value) not in (int,float) or not math.isfinite(value) for value in values) or
                step.get('prediction_kind')!='fresh_observation_learned_policy_inference' or
                step.get('checkpoint_content_sha256')!=model['checkpoint_content_sha256'] or
                step.get('camera_id')!=result['camera_id'] or not step.get('frame_id') or
                not frame_name or source['source_files_sha256'].get(frame_name)!=step.get('frame_sha256') or
                not step.get('native_dispatch_receipt_id') or step['native_dispatch_receipt_id'] in receipts or
                len(inputs)!=model['history_steps'] or len(set(inputs))!=len(inputs) or inputs[-1]!=step['frame_id'] or
                not all(isinstance(value,str) and value for value in current_epoch) or
                (observed.get('clock_domain'),observed.get('boot_id'))!=current_epoch or
                type(tick) is not int or type(obs_tick) is not int or not 0<=obs_tick<=tick or tick-obs_tick>2_000_000_000 or
                last_tick is not None and (current_epoch!=epoch or tick<=last_tick)):
            raise ValueError('Actual causal native source model/RGB/dispatch trace required')
        receipts.add(step['native_dispatch_receipt_id']);last_tick=tick;epoch=current_epoch
    return dict(evaluation_job_id=evaluation['id'],source_trace_manifest=str(trace_path),
        source_trace_sha256=result['source_trace_sha256'],source_domain=model['source_domain'],
        episode_id=source['split']['episode_id'],split=source['split']['partition'],
        skill_id=source['skill_id'],skill_version=source['skill_version'],frames=len(source['frames']),
        qualification_scope='source_object_rollout_only_target_mapping_and_target_policy_still_separate',physical_execution_authorized=False)


def inspect_source_model(store, model_job_id, *, admission_verifier=None, owner_capabilities=None):
    job=store.job(model_job_id)
    if not job:
        raise ValueError('Source model job not found')
    result=job.get('result') or {};blockers=[];model=None
    choice=dict(job_id=job['id'],created=job.get('created'),source_family='current_so101_whole_skill',
        backend=(job.get('config') or {}).get('backend') or result.get('backend'),
        skill_id=result.get('skill_id'),skill_version=result.get('skill_version'),spec_sha256=result.get('spec_sha256'),
        source_kind=result.get('source_kind'),technical_source_ready=False,eligible_for_transfer=False,
        source_object_rollout_qualified=False,qualified_trace_choices=[],unsealed_episode_choices=[],
        physical_execution_authorized=False,direct_action_head_transfer_allowed=False,text_is_ACT_input=False)
    choice['evaluation_attempts']=[]
    try:
        model=_metadata(store,job,admission_verifier=admission_verifier)
    except (OSError,ValueError,KeyError,TypeError) as error:
        blockers.append(_block('A_SOURCE_MODEL_OR_ADMISSION_NOT_VERIFIED','A',str(error)))
    if model is not None:
        spec=model['manifest']['skill_spec']
        choice.update(technical_source_ready=True,backend=model['backend'],task=spec.get('title'),task_type=spec.get('task_type'),
            history_steps=model['history_steps'],prediction_steps=model['prediction_steps'],source_domain=model['source_domain'],
            source_camera_ids=model['source_camera_ids'],source_calibration_sha256=model['source_calibration_sha256'],
            aggregate_manifest_sha256=model['result']['aggregate_manifest_sha256'],checkpoint_content_sha256=model['checkpoint_content_sha256'],
            unsealed_episode_choices=model['unsealed_episode_choices'],action_contract_id=CONTRACT,action_names=list(NAMES))
        if spec.get('task_type') in (None,'hold','trajectory','joint_trajectory'):
            blockers.append(_block('C_OBJECT_SKILL_SOURCE_REQUIRED','C','Удержание и joint replay не подтверждают предметный навык.'))
        else:
            for evaluation in store.jobs(10000):
                if evaluation.get('kind')=='so101_skill_eval' and (evaluation.get('config') or {}).get('source_model_job_id')==job['id']:
                    try:
                        choice['qualified_trace_choices'].append(_qualified_trace(store,model,evaluation))
                        choice['evaluation_attempts'].append(dict(evaluation_job_id=evaluation['id'],qualified=True,blockers=[]))
                    except (OSError,ValueError,KeyError,TypeError) as error:
                        choice['evaluation_attempts'].append(dict(evaluation_job_id=evaluation['id'],qualified=False,
                            blockers=[_block('C_SOURCE_EVALUATION_NOT_QUALIFIED','C',str(error))]))
    caps=owner_capabilities if owner_capabilities is not None else evaluation_owner_capabilities()
    if caps.get('implemented') is not True:
        blockers.append(_block('A_SOURCE_EVALUATION_OWNER_NOT_IMPLEMENTED','A','Нужен программный владелец отдельной наблюдаемой проверки модели SO101; offline loss и shadow его не заменяют.'))
    if not choice['qualified_trace_choices']:
        blockers.extend(reason for attempt in choice['evaluation_attempts'] for reason in attempt['blockers'])
        blockers.append(_block('C_NO_QUALIFIED_OBJECT_TRACE','C','Нет независимой полной попытки предметной задачи обученной source-модели с исходными кадрами, dispatch и исходом.'))
    choice['source_object_rollout_qualified']=bool(choice['qualified_trace_choices'])
    choice['eligible_for_transfer']=bool(model is not None and choice['qualified_trace_choices'] and not blockers)
    choice['blockers']=blockers
    return choice


def current_source_choices(store):
    choices=[];legacy=[]
    for job in store.jobs(10000):
        if job.get('kind')=='so101_skill_train' and job.get('status')=='completed':
            choices.append(inspect_source_model(store,job['id']))
        elif job.get('kind')=='train_act' and job.get('status')=='completed' and (job.get('result') or {}).get('feature_dimension')==6:
            legacy.append(dict(job_id=job['id'],source_family='legacy_action6_analysis_only',eligible_for_transfer=False,
                blockers=[_block('C_LEGACY_MODEL_NOT_CURRENT_OBJECT_QUALIFICATION','C','Историческая6D модель сохранена для анализа; размерность не доказывает предметный source-навык.')]))
    return dict(schema='explorer_transfer_current_sources_v1',choices=choices,legacy_analysis_sources=legacy,
        source_experiment=experiment_id('so101_source_learning'),target_experiment=experiment_id('cross_embodiment_transfer'),
        source_action_dimension=6,target_action_dimension=9,joint_head_copy_allowed=False,physical_execution_authorized=False)


def prepare_source_probe(store,model_job_id,episode_id,*,admission_verifier=None):
    model=_metadata(store,store.job(model_job_id),admission_verifier=admission_verifier)
    descriptors=[row for row in model['view']['episodes'] if row.get('id')==episode_id and row.get('split') in ('train','development')]
    if len(descriptors)!=1:
        raise ValueError('Select one actual unsealed source episode; no sealed inference/tuning')
    return dict(schema='explorer_native_source_probe_binding_v1',model_job_id=model_job_id,
        skill_id=model['manifest']['skill_id'],skill_version=model['manifest']['skill_version'],spec_sha256=model['manifest']['spec_sha256'],
        backend=model['backend'],history_steps=model['history_steps'],prediction_steps=model['prediction_steps'],
        checkpoint_path=model['checkpoint_path'],checkpoint_sha256=model['checkpoint_sha256'],
        checkpoint_content_sha256=model['checkpoint_content_sha256'],source_manifest_path=model['manifest_path'],
        source_view_manifest=model['view_path'],source_episode=descriptors[0],source_camera_ids=model['source_camera_ids'],
        source_calibration_sha256=model['source_calibration_sha256'],source_domain=model['source_domain'],
        action_contract_id=CONTRACT,physical_execution_authorized=False,qualifies_object_source=False,
        execution_kind='offline_original_observation_policy_inference_not_rollout')


def prepare_qualified_source(store,model_job_id,evaluation_job_id,*,expected_source_trace_sha256=None,
                             admission_verifier=None,owner_capabilities=None):
    """Server IDs only. Source admission authorizes zero target physical commands."""
    choice=inspect_source_model(store,model_job_id,admission_verifier=admission_verifier,owner_capabilities=owner_capabilities)
    if not choice['eligible_for_transfer']:
        raise SourceBlocked(choice['blockers'])
    selected=[row for row in choice['qualified_trace_choices'] if row['evaluation_job_id']==evaluation_job_id]
    if len(selected)!=1 or expected_source_trace_sha256 not in (None,selected[0]['source_trace_sha256']):
        raise ValueError('Select the exact current qualified source trace/version')
    trace=selected[0]
    return dict(schema='explorer_qualified_native_source_transfer_binding_v1',
        source_job_id=model_job_id,source_evaluation_job_id=evaluation_job_id,
        source_trace_manifest=trace['source_trace_manifest'],source_trace_sha256=trace['source_trace_sha256'],
        skill_id=trace['skill_id'],source_skill_version=trace['skill_version'],minimum_new_target_skill_version=trace['skill_version']+1,
        source_episode_id=trace['episode_id'],source_partition=trace['split'],source_domain=trace['source_domain'],
        source_kind='QUALIFIED_SOURCE_MODEL',source_experiment=experiment_id('so101_source_learning'),
        target_experiment=experiment_id('cross_embodiment_transfer'),source_action_contract_id=CONTRACT,
        source_checkpoint_content_sha256=choice['checkpoint_content_sha256'],
        target_action_contract_id='rosmaster_joint9_position6_velocity3_v1',
        direct_action_head_transfer_allowed=False,physical_execution_authorized=False,
        target_mapping_adaptation_training_and_evaluation_still_required=True)


class SourcePolicyProbe:
    """Explicit runtime load only; arrays returned, no source/target dispatch path."""
    def __init__(self,binding,*,camera_id,loader=None):
        if binding.get('schema')!='explorer_native_source_probe_binding_v1' or camera_id not in binding['source_camera_ids']:
            raise ValueError('Server source probe/camera binding required')
        self.binding,self.camera_id=binding,camera_id
        if loader is not None:
            self.policy=loader(binding,camera_id)
        elif binding['backend']=='act':
            from explorer_shared.learning.act_policy import NativeACTPolicy
            self.policy=NativeACTPolicy(binding['checkpoint_path'],binding['checkpoint_sha256'],expected_contract_id=CONTRACT,
                expected_names=NAMES,expected_units=UNITS,expected_low=LOW,expected_high=HIGH,
                expected_camera_id=camera_id,expected_calibration_sha256=binding['source_calibration_sha256'])
        elif binding['backend']=='diffusion':
            from explorer_shared.learning.diffusion_policy import NativeDiffusionPolicy
            self.policy=NativeDiffusionPolicy(binding['checkpoint_path'],binding['checkpoint_sha256'],expected_contract_id=CONTRACT,
                expected_names=NAMES,expected_units=UNITS,expected_low=LOW,expected_high=HIGH)
        else:raise ValueError('Typed native source backend required')

    def predict_history(self,rgb,native,*,valid_mask,observations):
        import numpy as np
        from explorer_shared.learning.act_policy import validate_observation
        count=self.binding['history_steps'];images=np.asarray(rgb);states=np.asarray(native);masks=np.asarray(valid_mask)
        if images.ndim!=4 or len(images)!=count or states.shape!=(count,6) or masks.shape!=(count,6) or len(observations)!=count:
            raise ValueError('Backend-specific actual history1 ACT /history2 Diffusion required')
        previous=None
        for image,state,mask,observation in zip(images,states,masks,observations):
            validate_observation(image,state,mask,observation,camera_id=self.camera_id,
                calibration_sha256=self.binding['source_calibration_sha256'],low=np.asarray(LOW),high=np.asarray(HIGH))
            epoch=(observation['frame_clock_domain'],observation['frame_boot_id'])
            tick=observation['source_cutoff_monotonic_ns']
            if previous and (epoch!=previous[0] or tick<=previous[1] or observation['frame_id']==previous[2]):
                raise ValueError('Distinct chronological source history in one clock/boot required')
            previous=(epoch,tick,observation['frame_id'])
        if self.binding['backend']=='act':
            predicted=self.policy.predict_history(images,states,valid_mask=masks,observations=observations)
        else:predicted=self.policy.predict_history(images,states,valid_mask=masks)
        predicted=np.asarray(predicted)
        if (predicted.dtype.kind not in 'fiu' or predicted.shape!=(self.binding['prediction_steps'],6) or
                not np.isfinite(predicted).all() or np.any(predicted<np.asarray(LOW)) or np.any(predicted>np.asarray(HIGH))):
            raise ValueError('Source prediction shape/nonfinite output differs')
        return dict(schema='explorer_source_native6_offline_probe_v1',backend=self.binding['backend'],
            predicted_native6=predicted.tolist(),history_steps=count,prediction_steps=len(predicted),
            model_job_id=self.binding['model_job_id'],checkpoint_content_sha256=self.binding['checkpoint_content_sha256'],
            execution_kind='offline_original_observation_policy_inference_not_rollout',
            physical_execution_authorized=False,source_or_target_commands_dispatched=False,
            qualifies_object_source=False,direct_action_head_transfer_allowed=False)
