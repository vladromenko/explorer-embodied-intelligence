"""ENGINEERING_GLUE: offline SO-101 provenance, aggregate and official data check.

No robot classes, network clients, replay CLI, policy weights or motor operations.
Recorded command replay and learned closed-loop rollout are separate operations;
this module performs neither. Original personal-lab bytes are never rewritten.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image
from explorer_shared.so101_station import NAMES, UNITS, LOW, HIGH

ACTION_CONTRACT_ID='so101_follower_calibrated_position6_v1'
SPLITS={'train','development','sealed_test'}


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _json(path):
    path=Path(path).resolve()
    if path.stat().st_size>16*1024**2:raise ValueError('Station source metadata exceeds inspection limit')
    data=json.loads(path.read_text())
    if not isinstance(data,dict):raise ValueError('Station source must be an object')
    return data


def _member(folder,name):
    if not isinstance(name,str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Station source member must be a relative retained path')
    path=(folder/name).resolve()
    if not path.is_relative_to(folder) or not path.is_file():raise ValueError('Station source member escaped or is missing')
    return path


def _six(value):
    return isinstance(value,list) and len(value)==6 and all(type(item) in (int,float) and np.isfinite(item) for item in value)


def inspect_station_episode(path,*,task_type='joint_trajectory'):
    """Return source-bound facts, including honest omissions in legacy captures."""
    path=Path(path).resolve();source=_json(path);samples=source.get('samples')
    report={'schema':'explorer_so101_episode_evidence_v1','action_contract_id':ACTION_CONTRACT_ID,
        'episode_id':source.get('episode_id'),'source_manifest_path':str(path),'source_manifest_sha256':_sha(path),
        'project_id_original':source.get('project_id'),'source_kind':source.get('source_kind','unspecified'),
        'task_type':task_type,'source_session_id':source.get('session_id'),'source_boot_id':source.get('boot_id'),
        'clock_domain':source.get('clock_domain'),'state_basis':source.get('state_basis'),'action_basis':source.get('action_basis'),
        'split':source.get('split'),'operator_outcome':source.get('outcome'),'outcome_label_source':source.get('label_source',source.get('outcome_label_source')),
        'completed_recording':source.get('state')=='complete','operator_success':source.get('outcome')=='success',
        'technical_dataset_ready':False,'full_teaching_provenance_ready':False,'object_expert_eligible':False,
        'physical_task_success_verified':False,'motor_application_time_verified':False,'source_files':[],
        'recorded_channels':{},'reasons':[],'missing_facts':[],'legacy_hold_only':None}
    reasons=report['reasons'];missing=report['missing_facts']
    if not isinstance(source.get('episode_id'),str) or not source['episode_id'] or len(source['episode_id'])>160:reasons.append('original_episode_identity_missing')
    if type(source.get('fps')) not in (int,float) or not np.isfinite(source['fps']) or not 0<source['fps']<=1000:reasons.append('finite_recording_FPS_missing')
    if source.get('source_type')!='station_demonstration':reasons.append('not_a_native_station_demonstration')
    if source.get('action_names')!=list(NAMES) or source.get('action_units')!=UNITS:reasons.append('canonical_SO101_names_units_not_verified')
    if source.get('normalization')!={'low':LOW,'high':HIGH}:reasons.append('original_SO101_coordinate_ranges_not_verified')
    if source.get('state_basis')!='measured_state':reasons.append('measured_follower_state_missing')
    if source.get('action_basis')!='dispatched_command':reasons.append('host_dispatched_command_labels_missing')
    if not report['completed_recording']:reasons.append('recording_not_complete')
    if not isinstance(samples,list) or not samples or len(samples)>10000:raise ValueError('A bounded nonempty station sample list is required')
    fields={'operator_requested':'requested_action','follower_accepted':'accepted_action',
        'follower_dispatched_separate':'dispatched_action','leader_measured':'leader_state'}
    counts={key:0 for key in fields};counts.update(follower_measured=0,command_label=0,camera_rgb=0)
    clocks=set();camera_ids=set();frame_ids=[];rgb_hashes=[];states=[];previous=-1
    for index,sample in enumerate(samples):
        if not isinstance(sample,dict):raise ValueError('Station sample must be an object')
        frame=sample.get('frame') or {};image=_member(path.parent,frame.get('file'))
        if _sha(image)!=frame.get('sha256'):raise ValueError('Original station RGB SHA mismatch')
        with Image.open(image) as decoded:decoded.verify()
        counts['camera_rgb']+=1;report['source_files'].append({'path':str(image),'sha256':frame['sha256']})
        if not _six(sample.get('state')) or not _six(sample.get('action')):raise ValueError('Station measured state and command label must each have six finite coordinates')
        if any(value<LOW[axis] or value>HIGH[axis] for field in ('state','action') for axis,value in enumerate(sample[field])):raise ValueError('SO101 source values outside recorded native coordinate ranges')
        counts['follower_measured']+=1;counts['command_label']+=1;states.append(sample['state'])
        for channel,field in fields.items():
            if field in sample:
                if not _six(sample[field]):raise ValueError('Invalid recorded '+field)
                counts[channel]+=1
        if 'dispatched_action' in sample and sample['dispatched_action']!=sample['action']:
            raise ValueError('Separate dispatched source differs from converted command label')
        stamps=(frame.get('monotonic_ns'),sample.get('state_monotonic_ns'),sample.get('action_monotonic_ns'))
        if any(type(value) is not int or value<0 for value in stamps) or not stamps[0]<=stamps[1]<=stamps[2] or stamps[2]<=previous:
            raise ValueError('Native frame/state/dispatch clock causality failed')
        previous=stamps[2];domain=sample.get('clock_domain')
        if not isinstance(domain,str) or not domain or frame.get('clock_domain')!=domain:raise ValueError('Native station clocks differ')
        clocks.add(domain)
        if not isinstance(frame.get('frame_id'),str) or not frame['frame_id']:raise ValueError('Native camera frame identity missing')
        frame_ids.append((domain,frame['frame_id']));rgb_hashes.append(frame['sha256'])
        if frame.get('camera_id'):camera_ids.add(frame['camera_id'])
        if _six(sample.get('leader_state')) and (type(sample.get('leader_state_monotonic_ns')) is not int or sample['leader_state_monotonic_ns']>stamps[1]):
            missing.append('leader_measurement_clock_not_verified')
    if len(clocks)!=1 or next(iter(clocks))!=source.get('clock_domain'):reasons.append('one_original_boot_clock_not_verified')
    report['frame_ids']=frame_ids;report['RGB_sha256']=rgb_hashes;report['frames']=len(samples);report['camera_ids']=sorted(camera_ids)
    for channel,count in counts.items():
        report['recorded_channels'][channel]={'recorded':count>0,'complete':count==len(samples),'samples':count,
            'source_field':fields.get(channel,{'follower_measured':'state','command_label':'action','camera_rgb':'frame'}.get(channel)),
            'basis':'original_native_fields_not_reconstructed'}
        if count!=len(samples):missing.append(channel+'_not_recorded_for_all_samples')
    profiles=source.get('calibration_metadata') or {};calibration={}
    for role in ('leader','follower'):
        digest=(profiles.get(role) or {}).get('calibration_sha256')
        valid=isinstance(digest,str) and len(digest)==64 and all(char in '0123456789abcdef' for char in digest)
        calibration[role]={'sha256':digest,'recorded':valid,'original_register_verification_claim':(profiles.get(role) or {}).get('verified_on_connected_hardware') is True}
        if not valid:missing.append(role+'_calibration_identity_not_recorded')
    report['calibration']=calibration
    if not report['source_session_id']:missing.append('original_teaching_session_not_recorded')
    if not report['source_boot_id']:missing.append('explicit_source_boot_identity_not_recorded')
    if len(camera_ids)!=1:missing.append('one_camera_hardware_identity_not_recorded')
    if not source.get('camera_calibration'):missing.append('camera_calibration_not_recorded')
    if not all(isinstance(row.get('dispatch_receipt'),dict) and row['dispatch_receipt'].get('stage')=='host_bus_write_completed' for row in samples):
        missing.append('per_sample_host_dispatch_receipts_not_recorded')
    report['legacy_hold_only']=bool(np.max(np.ptp(np.asarray(states),axis=0))<=1e-6)
    report['technical_dataset_ready']=not reasons
    report['full_teaching_provenance_ready']=not reasons and not missing
    missing.append('independent_object_task_success_not_qualified_by_this_adapter')
    report['missing_facts']=sorted(set(missing));report['replay_vs_rollout']={
        'recorded_replay':'Recorded host-dispatched targets; no policy and no new visual feedback conditioning',
        'learned_rollout':'Requires checkpoint, fresh current RGB and measured follower state, validated current calibration and separately observed permission',
        'replay_executed':False,'learned_rollout_executed':False,'physical_io':False}
    if _sha(path)!=report['source_manifest_sha256']:raise ValueError('Station source changed during inspection')
    return report



def official_api_evidence():
    """Pin inspected installed primary source; never import hardware robot classes."""
    distribution=importlib.metadata.distribution('lerobot')
    if distribution.version!='0.6.1':raise RuntimeError('Inspected official APIs are pinned to LeRobot0.6.1')
    names=('lerobot/robots/so_follower/so_follower.py','lerobot/robots/so_follower/config_so_follower.py',
        'lerobot/scripts/lerobot_replay.py','lerobot/datasets/lerobot_dataset.py')
    return {'lerobot_version':distribution.version,'installed_primary_sources_sha256':{
        name:_sha(distribution.locate_file(name)) for name in names},
        'official_action_keys':[name+'.pos' for name in NAMES],
        'official_default_robot_use_degrees':True,'official_default_robot_coordinate_compatible':False,
        'recorded_native_coordinate_units':UNITS,'required_normalized_software_mapping':{'use_degrees':False},
        'official_robot_connect_qualified':False,'official_robot_classes_imported':False,
        'connect_calls_configure_and_writes_controller_registers':True,
        'hardware_entrypoint_executed':False,'existing_executor_or_controller_settings_changed':False}


def replay_schedule(path):
    """Pure official-compatible action dictionaries; never calls send_action."""
    report=inspect_station_episode(path)
    if not report['technical_dataset_ready']:raise ValueError('Replay data contract unavailable: '+','.join(report['reasons']))
    source=_json(path)
    return {'schema':'explorer_so101_recorded_replay_plan_v1','action_contract_id':ACTION_CONTRACT_ID,
        'source_manifest_sha256':report['source_manifest_sha256'],'fps':source['fps'],'robot_connect_called':False,
        'physical_io':False,'learned_policy_used':False,'recorded_action_basis':report['action_basis'],
        'official_api_compatibility':official_api_evidence(),
        'actions':[{'source_sample_index':index,'source_dispatch_monotonic_ns':row['action_monotonic_ns'],
                    'action':{name+'.pos':float(value) for name,value in zip(NAMES,row['action'])}}
                   for index,row in enumerate(source['samples'])]}


def qualify_station_dataset(bindings,target,*,skill_id,skill_version,task_type='joint_trajectory'):
    """Build all accepted train/dev originals; sealed payloads never enter export.

    Bindings are immutable operator/server decisions tied to exact original SHA.
    The caller supplies an isolated destination. No fitting, replay or rollout.
    """
    from .station_adapter import convert_station_episode
    from .dataset_export import export_lerobot_v3,validate_splits
    if importlib.metadata.version('lerobot')!='0.6.1':raise RuntimeError('Official LeRobot0.6.1 required')
    if not isinstance(skill_id,str) or not skill_id or type(skill_version) is not int or skill_version<1:raise ValueError('Explicit selected skill/version required')
    if not isinstance(bindings,list) or not bindings:raise ValueError('Accepted original episode bindings required')
    target=Path(target).resolve()
    if target.exists():raise ValueError('Immutable qualification destination already exists')
    selected=[];seen={};groups={};sealed=[];calibration_signature=None
    for binding in bindings:
        if binding.get('accepted') is not True:raise ValueError('Only explicitly accepted bindings belong in this aggregate')
        if binding.get('skill_id')!=skill_id or binding.get('skill_version')!=skill_version:raise ValueError('Source bound to a different skill/version')
        source=Path(binding['manifest_path']).resolve()
        if source.is_relative_to(target):raise ValueError('Output cannot contain an original source')
        evidence=inspect_station_episode(source,task_type=task_type);split=binding.get('split')
        if split not in SPLITS or split!=evidence['split']:raise ValueError('Original source split cannot be reassigned')
        if evidence['source_manifest_sha256']!=binding.get('manifest_sha256'):raise ValueError('Bound station source SHA changed')
        if not evidence['technical_dataset_ready'] or not evidence['operator_success']:raise ValueError('Only completed successful source-labelled technical demonstrations may be aggregated')
        if task_type!='joint_trajectory':raise ValueError('Object/custom expert qualification is unavailable; legacy holds are not object experts')
        identity=evidence['episode_id']
        if not isinstance(identity,str) or not identity or identity in seen:raise ValueError('Duplicate original cannot count as another demonstration')
        seen[identity]=split
        for key in [('source_manifest',evidence['source_manifest_sha256']),*[('source_frame',tuple(item)) for item in evidence['frame_ids']],*[('RGB_content',item) for item in evidence['RGB_sha256']]]:
            if key in groups and groups[key]!=split:raise ValueError('Original source/frame/RGB content crosses dataset splits')
            groups[key]=split
        if evidence['source_session_id']:
            key=('session',evidence['source_session_id'])
            if key in groups and groups[key]!=split:raise ValueError('Original teaching session crosses dataset splits')
            groups[key]=split
        current_calibration=tuple(evidence['calibration'][role]['sha256'] for role in ('leader','follower'))
        if calibration_signature is not None and current_calibration!=calibration_signature:raise ValueError('Original leader/follower calibration identities differ')
        calibration_signature=current_calibration
        selected.append((binding,evidence))
        if split=='sealed_test':sealed.append({'episode_id':identity,'source_manifest_sha256':evidence['source_manifest_sha256'],'payload_excluded':True})
    if not any(e['split']=='train' for _,e in selected) or not any(e['split']=='development' for _,e in selected):raise ValueError('Distinct accepted train and development originals required')
    target.parent.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='.'+target.name+'.',dir=target.parent))
    try:
        episodes=[];files={};records=[];signature=None
        for index,(binding,evidence) in enumerate(selected):
            if evidence['split']!='sealed_test':
                namespace='originals/'+hashlib.sha256(evidence['episode_id'].encode()).hexdigest()[:24]
                converted=convert_station_episode(binding['manifest_path'],stage/namespace)
                current={key:converted[key] for key in ('action_names','action_units','action_normalization','state_basis','action_basis','fps')}
                if signature is not None and current!=signature:raise ValueError('Source station data contracts differ')
                signature=current
                for item in converted['episodes']:episodes.append(dict(item,file=namespace+'/'+item['file']))
                for name,digest in converted['source_files_sha256'].items():files[namespace+'/'+name]=digest
                files[namespace+'/manifest.json']=_sha(stage/namespace/'manifest.json')
                records.append({'original_episode_id':evidence['episode_id'],'split':evidence['split'],'source_manifest_sha256':evidence['source_manifest_sha256'],
                    'saved_original_manifest':namespace+'/source_originals/manifest.json','source_evidence':evidence})
        manifest=dict(signature,schema_version=1,format='explorer_robot_episode_v1',source='so101_skill_qualification',
            dataset_id='so101-'+hashlib.sha256((skill_id+str(skill_version)).encode()).hexdigest()[:24],task='SO101 recorded joint trajectory software qualification',
            skill_id=skill_id,skill_version=skill_version,action_contract_id=ACTION_CONTRACT_ID,episodes=episodes,
            clock_domain='PER_EPISODE_SOURCE_CLOCKS_NOT_MERGED',contains_physical_recordings=True,
            source_files_sha256=files,source_records=records,sealed_sources_excluded=sealed,
            resampling={'method':'causal_previous_sample_hold'},source_calibration_metadata={'calibration_metadata':_json(selected[0][0]['manifest_path']).get('calibration_metadata',{}),'per_original':[r['source_evidence']['calibration'] for r in records]},
            physical_success_inferred=False,object_expert_eligible=False)
        validate_splits(manifest)
        (stage/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False))
        # Pin immutable final paths before official provenance records their source.
        stage.rename(target)
    finally:
        if stage.exists():shutil.rmtree(stage)
    exported=export_lerobot_v3(target/'manifest.json',target/'lerobot_v3')
    receipt={'schema':'explorer_so101_dataset_qualification_v1','skill_id':skill_id,'skill_version':skill_version,
        'action_contract_id':ACTION_CONTRACT_ID,'source_kind':selected[0][1]['source_kind'] if len({e['source_kind'] for _,e in selected})==1 else 'mixed',
        'source_kinds':sorted({e['source_kind'] for _,e in selected}),
        'source_manifests_sha256':{e['episode_id']:e['source_manifest_sha256'] for _,e in selected},
        'aggregate_manifest_path':str(target/'manifest.json'),'aggregate_manifest_sha256':_sha(target/'manifest.json'),
        'accepted_train_original_episode_ids':[e['episode_id'] for _,e in selected if e['split']=='train'],
        'accepted_development_original_episode_ids':[e['episode_id'] for _,e in selected if e['split']=='development'],
        'sealed_original_episode_ids':[e['episode_id'] for _,e in selected if e['split']=='sealed_test'],
        'official_dataset_readback_verified':exported['official_readback_passed'],'official_dataset':exported,
        'official_api_compatibility':official_api_evidence(),
        'full_teaching_provenance_ready':all(e['full_teaching_provenance_ready'] for _,e in selected),
        'technical_dataset_ready':True,'object_expert_eligible':False,'physical_task_success_verified':False,
        'physical_io':False,'replay_executed':False,'learned_rollout_executed':False,'uploaded_to_hub':False,
        'originals_unchanged':all(_sha(b['manifest_path'])==e['source_manifest_sha256'] and all(_sha(item['path'])==item['sha256'] for item in e['source_files']) for b,e in selected)}
    if not receipt['originals_unchanged']:raise ValueError('Original source changed during official export')
    (target/'qualification.json').write_text(json.dumps(receipt,indent=2,allow_nan=False))
    return receipt
