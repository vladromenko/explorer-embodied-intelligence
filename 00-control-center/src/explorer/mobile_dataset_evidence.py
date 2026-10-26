"""ENGINEERING_GLUE: read-only imported ROSMASTER RGB/9D evidence diagnostics.

Reuse existing conversion/episode validators. Raw camera observations, declared
operator success, BC admission, actual ACT input use and physical validation are
separate facts. This module never converts, trains, records or controls a robot.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


SCHEMA='explorer_mobile_dataset_evidence_v1'
IMAGE_FEATURE='observation.images.overview'
NAMES=['base','shoulder','elbow','wrist_pitch','wrist_roll','gripper','vx','vy','wz']
TRAIN_SPLITS={'train','training','imitation_train'}
DEV_SPLITS={'development','validation','dev'}
SEALED_SPLITS={'test','sealed_test','sealed_final_test'}


def file_sha256(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _json(path):
    if path.stat().st_size>16*1024**2:raise ValueError('Evidence JSON exceeds the 16MiB metadata bound')
    value=json.loads(path.read_text())
    if not isinstance(value,dict):raise ValueError('Evidence metadata must be a JSON object')
    return value


def _managed(path,root=None):
    value=Path(path).expanduser().resolve()
    if root is not None and not value.is_relative_to(Path(root).expanduser().resolve()):
        raise ValueError('Evidence path is outside the managed state root')
    return value


def _member(folder,name):
    if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('Invalid retained evidence member path')
    path=(folder/name).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():raise ValueError('Retained evidence member is missing or escaped its recording')
    return path


def _dataset_validators():
    folder=str(Path(__file__).resolve().parents[3]/'01-autonomous-learning/src')
    if folder not in sys.path:sys.path.insert(0,folder)
    from explorer_sim.dataset_export import load_episode,validate_splits,_validate_retained_originals
    return load_episode,validate_splits,_validate_retained_originals


def _native_metadata(path):
    """Inspect immutable capture identities/hashes without reconstructing unknown q."""
    metadata=_json(path);folder=path.parent
    journal=folder/'samples.jsonl'
    summary={'metadata':metadata,'metadata_sha256':file_sha256(path),'raw_rgb_verified_count':0,
        'raw_rows':0,'camera_ids':[],'camera_profiles':[],'optical_frames':[],
        'host_boot_ids':[],'controller_boot_ids':[],'arm_history_phases':[],'command_sources':[]}
    sets={name:set() for name in ('camera_ids','camera_profiles','optical_frames','host_boot_ids','controller_boot_ids','arm_history_phases','command_sources')}
    if not journal.is_file():return summary
    if journal.stat().st_size>32*1024**2:raise ValueError('Native journal exceeds the 32MiB inspection bound')
    with journal.open() as stream:
        for line in stream:
            if line.strip():
                row=json.loads(line);summary['raw_rows']+=1
                if summary['raw_rows']>10000:raise ValueError('Native observation count exceeds inspection bound')
                camera=row.get('camera_metadata_raw') or {}
                status=row.get('telemetry') or {}
                files=row.get('source_files') or {}
                for original,target in (('camera-frame.json','camera'),('status.json','status')):
                    item=files.get(original)
                    if item:
                        source=_member(folder,item.get('file'))
                        if file_sha256(source)!=item.get('sha256') or source.stat().st_size!=item.get('bytes'):
                            raise ValueError('Retained native camera/controller source SHA mismatch')
                        parsed=_json(source)
                        if target=='camera':
                            if camera and camera!=parsed:raise ValueError('Parsed camera metadata differs from original bytes')
                            camera=parsed
                        else:
                            if status and status!=parsed:raise ValueError('Parsed telemetry differs from original bytes')
                            status=parsed
                image=files.get('frame-raw.jpg')
                if image:
                    source=_member(folder,image.get('file'))
                    expected=image.get('sha256')
                    if file_sha256(source)!=expected or source.stat().st_size!=image.get('bytes'):
                        raise ValueError('Retained RGB source SHA mismatch')
                    if row.get('image_sha256')!=expected or camera.get('jpeg_sha256')!=expected:
                        raise ValueError('RGB and source camera identity are not SHA-bound')
                    from PIL import Image
                    with Image.open(source) as decoded:decoded.verify()
                    summary['raw_rgb_verified_count']+=1
                elif isinstance(row.get('image'),str):
                    source=_member(folder,row['image'])
                    if row.get('image_sha256') not in (None,file_sha256(source)):
                        raise ValueError('Native JPEG source SHA mismatch')
                    from PIL import Image
                    with Image.open(source) as decoded:decoded.verify()
                    summary['raw_rgb_verified_count']+=1
                arm=status.get('arm_command_state') or {}
                pairs=[('camera_ids',camera.get('camera_id',row.get('camera_id',metadata.get('camera_id')))),
                    ('camera_profiles',camera.get('source',row.get('image_stamp_source'))),
                    ('optical_frames',(camera.get('ros_header') or {}).get('frame_id')),
                    ('host_boot_ids',status.get('boot_id',row.get('source_boot_id'))),
                    ('controller_boot_ids',arm.get('controller_boot_id')),
                    ('arm_history_phases',arm.get('phase')),
                    ('command_sources',arm.get('command_source',row.get('executed_action_source')))]
                for key,value in pairs:
                    if type(value) in (str,int) and str(value):sets[key].add(str(value))
    for key,value in sets.items():summary[key]=sorted(value)
    return summary


def _original_path(manifest_path,manifest,managed_root):
    expected=manifest.get('source_manifest_sha256')
    candidates=[]
    if manifest.get('source_manifest_path'):
        try:candidates.append(_managed(manifest['source_manifest_path'],managed_root))
        except ValueError:pass
    for name,sha in (manifest.get('source_files_sha256') or {}).items():
        if Path(name).name=='episode.json' and sha==expected:candidates.append(_member(manifest_path.parent,name))
    for path in candidates:
        if path.is_file() and expected and file_sha256(path)==expected:return path
    raise ValueError('Original recording metadata SHA is not backed by retained bytes')


def _native_binding(original_path,manifest):
    """Reuse the exact pure adapter reader, then bind chosen NPZ labels/RGB to it."""
    if manifest['source']=='explorer_mobile_episode_v3':
        from explorer_sim.mobile_adapter import _read_native,_pair_native
        source,native,inputs,fps=_read_native(original_path,manifest['fps'],manifest['episodes'][0].get('split','train'))
        pairs,_=_pair_native(native)
        allowed={(item['observation_index'],item['action_index']) for item in pairs}
        return {'kind':'native_v3','native':native,'allowed':allowed,'source':source}
    from explorer_sim.mobile_observation_adapter import CONTRACT,AUDITED_SOURCES,_derive
    if manifest.get('derived_contract')!=CONTRACT or (manifest.get('provenance') or {}).get('audited_source_sha256')!=AUDITED_SOURCES:
        raise ValueError('Derived mobile adapter/controller provenance differs from audited contract')
    rows=[json.loads(line) for line in (original_path.parent/'samples.jsonl').read_text().splitlines() if line.strip()]
    return {'kind':'derived_observation','rows':rows,'derive':_derive,'cache':{},'folder':original_path.parent}


def _verify_binding(binding,artifact,loaded):
    import numpy as np
    from PIL import Image
    with np.load(artifact,allow_pickle=False) as arrays:
        if 'source_observation_index' not in arrays or 'source_action_index' not in arrays:
            raise ValueError('Converted mobile samples lack immutable native observation/action indices')
        observations=arrays['source_observation_index'].copy();labels=arrays['source_action_index'].copy()
    if observations.shape!=(len(loaded['rgb']),) or labels.shape!=observations.shape or not np.issubdtype(observations.dtype,np.integer) or not np.issubdtype(labels.dtype,np.integer):
        raise ValueError('Native source indices do not align with converted RGB/action rows')
    last_image_index,last_image=None,None
    for index,(observation,label) in enumerate(zip(observations,labels)):
        observation,label=int(observation),int(label)
        if observation<0 or label<0:raise ValueError('Negative native sample index')
        if binding['kind']=='native_v3':
            if (observation,label) not in binding['allowed']:raise ValueError('Converted row is not an allowed causal native-v3 pair')
            current,future=binding['native'][observation],binding['native'][label]
            state,action,image=current['state'],future['action'],current['path']
            stamps=(current['stamp']['image_stamp'],current['stamp']['state_stamp'],future['stamp']['state_stamp'])
            sent=future['stamp']['arm_command_sent_at']
        else:
            if label!=observation+1 or label>=len(binding['rows']):raise ValueError('Converted labels are not adjacent original command snapshots')
            for chosen in (observation,label):
                if chosen not in binding['cache']:
                    binding['cache'][chosen]=binding['derive'](binding['rows'][chosen],chosen,binding['folder'])
            current,future=binding['cache'][observation],binding['cache'][label]
            if future['state_time']<=max(current['frame_time'],current['state_time'],current['collected']):
                raise ValueError('Converted target snapshot is not strictly later than the native observation')
            state,action,image=current['state'],future['action'],current['image']
            stamps=(current['frame_time'],current['state_time'],future['state_time'])
            sent=future['sent']
        if not np.allclose(loaded['state'][index],state,atol=1e-5,rtol=0) or not np.allclose(loaded['action'][index],action,atol=1e-5,rtol=0):
            raise ValueError('Converted native state/action differs from retained original command history')
        for key,stamp in zip(('frame_wall_unix_ns','state_wall_unix_ns','action_wall_unix_ns'),stamps):
            if key not in loaded['timestamps'] or int(loaded['timestamps'][key][index])!=stamp:
                raise ValueError('Converted timestamp differs from the retained original clock event')
        auxiliary=loaded.get('auxiliary_timestamps') or {}
        if 'arm_command_sent_wall_unix_ns' in auxiliary and int(auxiliary['arm_command_sent_wall_unix_ns'][index])!=sent:
            raise ValueError('Host prepublication timestamp differs from its retained source (no dispatch proof inferred)')
        if observation!=last_image_index:
            with Image.open(image) as decoded:last_image=np.asarray(decoded.convert('RGB'))
            last_image_index=observation
        if not np.array_equal(loaded['rgb'][index],last_image):
            raise ValueError('Converted RGB differs from the exact retained camera JPEG decode')


def _unit_contract(units):
    return isinstance(units,list) and len(units)==9 and all(value in ('deg','degrees') for value in units[:6]) and units[6:]==['m/s','m/s','rad/s']


def _reported_rejections(result):
    qualification=result.get('qualification') or {}
    quality=qualification.get('quality') or {}
    values=[*qualification.get('fatal_reasons',[]),*quality.get('reasons',[])]
    values.extend((qualification.get('rejection_counts') or {}).keys())
    if result.get('reason'):values.append(str(result['reason']))
    return sorted({str(value) for value in values if value})[:100]


def _matching_training(jobs,manifest_sha,managed_root):
    matches=[]
    for job in jobs:
        result=job.get('result') or {}
        if job.get('kind')=='train_act' and job.get('status')=='completed' and result.get('trained') is True and result.get('source_manifest_sha256')==manifest_sha:
            record={'job_id':job.get('id'),'feature_dimension':result.get('feature_dimension'),
                'optimizer_steps':result.get('optimizer_steps'),'save_reload_passed':result.get('save_reload_passed') is True,
                'RGB_input_in_saved_policy':False,'checkpoint_config_sha_verified':False,'physical_success_validated':False}
            try:
                candidate=result.get('checkpoint_dir')
                if not candidate and managed_root is not None:candidate=Path(managed_root)/'jobs'/job['id']/'student_act'
                if not candidate and job.get('result_path'):candidate=Path(job['result_path']).parent/'student_act'
                if not candidate:raise ValueError('Saved checkpoint location unavailable')
                folder=_managed(candidate,managed_root)
                expected=result.get('checkpoint_sha256') or {}
                config=_json(folder/'config.json')
                record['checkpoint_config_sha_verified']=file_sha256(folder/'config.json')==expected.get('config.json')
                record['RGB_input_in_saved_policy']=record['checkpoint_config_sha_verified'] and IMAGE_FEATURE in (config.get('input_features') or {}) and config.get('input_features',{}).get('observation.state',{}).get('shape')==[9] and config.get('output_features',{}).get('action',{}).get('shape')==[9]
            except (OSError,ValueError,KeyError,TypeError):pass
            matches.append(record)
    return matches


def inspect_mobile_import(job,*,managed_root=None,include_manifest=False,max_episode_bytes=268435456,training_jobs=()):
    """Return JSON evidence only; missing/analysis-only imports are normal results."""
    result=job.get('result') or {}
    report={'schema':SCHEMA,'import_job_id':job.get('id'),'original_episode_id':(job.get('config') or {}).get('episode_id',result.get('episode_id')),
        'original_session_id':None,'manifest_path':None,'manifest_sha256':None,'completed_recording':False,
        'operator_success':False,'source_quality_usable':False,'source_training_admission_usable':False,
        'rgb_verified':False,'action9_verified':False,'causal_verified':False,'retained_originals_verified':False,
        'admitted_for_training':False,'raw_frames_recorded':result.get('frames',0),'raw_rgb_verified_count':0,
        'state_basis':result.get('state_basis'),'action_basis':result.get('action_basis'),'action_names':result.get('action_names') or [],'action_units':[],
        'camera_id':None,'camera_profile':None,'clock_domain':None,'episodes':[],'reasons':[],'missing_facts':[],
        'import_rejection_details':_reported_rejections(result),'physical_success_validated':False,
        'joint_measurements_claimed':False,'dispatch_confirmed':False,
        'visual_ACT':{'image_feature':IMAGE_FEATURE,'expected_input_contract':[IMAGE_FEATURE,'observation.state'],
            'pipeline_reader':'explorer_sim.recorded_training:RecordedChunks.__getitem__',
            'actual_matching_training_jobs':[],'trained_RGB_model_verified':False}}
    reasons=report['reasons'];missing=report['missing_facts'];metadata={};native={}
    try:
        if job.get('kind')!='mobile_import':reasons.append('not_a_mobile_import')
        if job.get('status')!='completed':reasons.append('mobile_import_not_completed')
        manifest_path=result.get('dataset_manifest')
        if manifest_path:
            path=_managed(manifest_path,managed_root);manifest=_json(path)
            report.update(manifest_path=str(path),manifest_sha256=file_sha256(path),clock_domain=manifest.get('clock_domain'),
                state_basis=manifest.get('state_basis'),source_action_basis=manifest.get('action_basis'),camera_feature=manifest.get('source_camera_feature'),
                action_basis='host_reported_target_snapshot',action_names=manifest.get('action_names') or [],action_units=manifest.get('action_units') or [])
            if include_manifest:report['manifest']=manifest
            if manifest.get('format')!='explorer_robot_episode_v1' or manifest.get('source') not in ('explorer_mobile_episode_v3','explorer_mobile_observation_episode_v1'):
                reasons.append('unsupported_mobile_dataset_contract')
            load_episode,validate_splits,validate_originals=_dataset_validators()
            validate_splits(manifest)
            if not manifest.get('source_files_sha256'):raise ValueError('Retained original source SHA inventory missing')
            validate_originals(path,manifest);report['retained_originals_verified']=True
            original_path=_original_path(path,manifest,managed_root)
            native=_native_metadata(original_path);metadata=native['metadata']
            if metadata!=manifest.get('source_episode_metadata'):raise ValueError('Original source metadata differs from converted provenance')
            admission=manifest.get('training_admission') or {}
            import_admission=result.get('training_admission') or {}
            report['source_training_admission_usable']=admission.get('source_quality_usable') is True and import_admission.get('source_quality_usable') is not False
            quality=((result.get('qualification') or {}).get('quality') or {})
            report['source_quality_usable']=quality.get('usable') is True if 'usable' in quality else report['source_training_admission_usable']
            named=report['action_names']==NAMES and _unit_contract(report['action_units'])
            if not named:reasons.append('mobile_named_units_or_order_not_verified')
            if report['state_basis']!='command_estimate':reasons.append('mobile_state_basis_not_command_estimate')
            if manifest.get('joint_measurements_claimed') is True:reasons.append('unbacked_measured_arm_claim_in_command_estimate_dataset')
            binding=_native_binding(original_path,manifest)
            observations=[]
            for descriptor in manifest.get('episodes') or []:
                entry={key:descriptor.get(key) for key in ('id','source_episode_id','file','sha256','split','frames')}
                entry['clock_domain']=descriptor.get('clock_domain',manifest.get('clock_domain'))
                artifact=_member(path.parent,descriptor.get('file'))
                if type(max_episode_bytes) is not int or max_episode_bytes<1 or artifact.stat().st_size>max_episode_bytes:
                    raise ValueError('Episode exceeds explicit inspection byte budget')
                loaded=load_episode(path,descriptor,manifest)
                _verify_binding(binding,artifact,loaded)
                if entry['clock_domain']!=manifest.get('clock_domain'):raise ValueError('Episode and manifest clock domains differ')
                original_split=metadata.get('split','train')
                if original_split in SEALED_SPLITS and descriptor.get('split')!=original_split:raise ValueError('Sealed source split was reassigned')
                if original_split in DEV_SPLITS and descriptor.get('split') in TRAIN_SPLITS:raise ValueError('Original development source was reassigned to training')
                count=len(loaded['rgb'])
                shape=tuple(loaded['rgb'].shape)
                rgb_ok=shape[0]>0 and len(shape)==4 and shape[-1]==3 and str(loaded['rgb'].dtype)=='uint8'
                nine_ok=loaded['state'].shape==(count,9) and loaded['action'].shape==(count,9) and named
                causal=bool(loaded['timestamps']) and bool(entry['clock_domain'])
                entry.update(frames=count,RGB_shape=list(shape),state_shape=list(loaded['state'].shape),action_shape=list(loaded['action'].shape),
                    rgb_verified=rgb_ok,action9_verified=nine_ok,causal_verified=causal,
                    unique_source_frame_ids=len(set(descriptor.get('frame_source_ids') or [])))
                report['episodes'].append(entry);observations.append((rgb_ok,nine_ok,causal))
            report['rgb_verified']=bool(observations) and all(value[0] for value in observations)
            report['action9_verified']=bool(observations) and all(value[1] for value in observations)
            report['causal_verified']=bool(observations) and all(value[2] for value in observations)
            report['timestamp_basis']=manifest.get('native_timestamp_basis') or {}
            report['clock_alignment_verified']=report['timestamp_basis'].get('clock_alignment_verified') is True
            report['timestamp_uncertainty']=manifest.get('timestamp_uncertainty')
            report['controller_provenance']={'label_basis':'host_reported_target_snapshot','source_contract_claims':manifest.get('source_contract_claims'),
                'audited_source_sha256':(manifest.get('provenance') or {}).get('audited_source_sha256'),
                'producer_runtime_binary_identity_verified':(manifest.get('provenance') or {}).get('producer_runtime_binary_identity_verified') is True,
                'dispatch_receipts_verified':False,'measured_arm_feedback_verified':False}
            if file_sha256(path)!=report['manifest_sha256']:raise ValueError('Manifest changed during read-only inspection')
            if result.get('observation_only') is True:reasons.append('import_declared_observation_only')
            report['visual_ACT']['actual_matching_training_jobs']=_matching_training(training_jobs,report['manifest_sha256'],managed_root)
            report['visual_ACT']['trained_RGB_model_verified']=any(item['RGB_input_in_saved_policy'] and item['save_reload_passed'] and (item['optimizer_steps'] or 0)>0 for item in report['visual_ACT']['actual_matching_training_jobs'])
        else:
            reasons.append('observation_only_no_admitted_dataset')
            if result.get('observation_manifest'):
                native=_native_metadata(_managed(result['observation_manifest'],managed_root));metadata=native['metadata']
            reasons.extend(report['import_rejection_details'])
        if metadata:
            if report['original_episode_id'] not in (None,metadata.get('id')):raise ValueError('Original recording identity differs from the import job')
            report['original_episode_id']=metadata.get('id')
            report['original_session_id']=metadata.get('session_id',metadata.get('session'))
            report['completed_recording']=metadata.get('state')=='complete'
            report['operator_outcome']=metadata.get('outcome','unknown')
            report['operator_success']=metadata.get('outcome')=='success' and metadata.get('label_source')=='operator'
            report['operator_outcome_basis']='operator_label_only_not_independent_task_validation'
            report['raw_frames_recorded']=metadata.get('frames',native.get('raw_rows') or report['raw_frames_recorded'])
            report['raw_rgb_verified_count']=native.get('raw_rgb_verified_count',0)
            report['original_recording_sha256']=native.get('metadata_sha256')
            report['camera_id']=native.get('camera_ids',[None])[0] if len(native.get('camera_ids',[]))==1 else None
            report['camera_profile']=(native.get('camera_profiles') or [None])[0]
            report['camera_ids']=native.get('camera_ids',[]);report['camera_optical_frames']=native.get('optical_frames',[])
            report.setdefault('controller_provenance',{}).update({key:native.get(key,[]) for key in ('host_boot_ids','controller_boot_ids','command_sources','arm_history_phases')})
            if len(native.get('camera_ids',[]))>1:reasons.append('camera_identity_changed_inside_recording')
        if not report['completed_recording']:reasons.append('recording_not_complete')
        if not report['operator_success']:reasons.append('operator_outcome_not_explicit_success')
        if not report['source_quality_usable']:reasons.append('source_quality_not_usable_for_BC')
        if not report['rgb_verified']:reasons.append('converted_RGB_episode_not_verified')
        if not report['action9_verified']:reasons.append('converted_native_action_state9_not_verified')
        if not report['causal_verified']:reasons.append('frame_state_future_label_causality_not_verified')
        for key,split in (('training_episode_ids',TRAIN_SPLITS),('development_episode_ids',DEV_SPLITS),('sealed_episode_ids',SEALED_SPLITS)):
            report[key]=[entry['id'] for entry in report['episodes'] if entry['split'] in split]
        if not report['training_episode_ids']:reasons.append('no_original_training_split_episodes')
        if not report['source_training_admission_usable']:reasons.append('source_training_admission_denied')
        if report['original_session_id'] is None:missing.append('original_operator_session_identity_not_recorded')
        if report['camera_id'] is None:missing.append('camera_hardware_identity_not_recorded')
        if not (report.get('controller_provenance') or {}).get('controller_boot_ids'):missing.append('controller_boot_identity_not_recorded')
        missing.extend(['physical_motor_dispatch_receipts_not_verified','independent_physical_task_success_not_verified'])
        report['admitted_for_training']=not reasons
    except (OSError,ValueError,KeyError,TypeError) as error:
        reasons.append('evidence_inspection_failed:'+type(error).__name__+':'+str(error))
        report['admitted_for_training']=False
    report['reasons']=sorted(set(reasons));report['missing_facts']=sorted(set(missing))
    report.setdefault('training_episode_ids',[]);report.setdefault('development_episode_ids',[]);report.setdefault('sealed_episode_ids',[])
    return report


def mobile_evidence_summary(jobs,*,managed_root=None,training_jobs=()):
    imports=[inspect_mobile_import(job,managed_root=managed_root,training_jobs=training_jobs) for job in jobs if job.get('kind')=='mobile_import']
    return {'schema':'explorer_mobile_evidence_summary_v1','imports':imports,
        'counts':{'imported':len(imports),'raw_frames_recorded':sum(item.get('raw_frames_recorded') or 0 for item in imports),
            'admitted_for_training':sum(item['admitted_for_training'] for item in imports),
            'observation_or_rejected':sum(not item['admitted_for_training'] for item in imports),
            'operator_labelled_success':sum(item.get('operator_success') is True for item in imports),
            'trained_RGB9D_models':len({job['job_id'] for item in imports for job in item['visual_ACT']['actual_matching_training_jobs'] if job['RGB_input_in_saved_policy']})},
        'physical_success_validated':False,'new_models_status':'offline_or_shadow_only_pending_separate_qualification'}
