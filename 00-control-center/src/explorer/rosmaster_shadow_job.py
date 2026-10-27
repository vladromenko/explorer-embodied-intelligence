"""ENGINEERING_GLUE: queued CPU ACT9/106D inference over actual native caches.

Control-center request code imports no Torch/policy. Worker loads its verified
model BEFORE obtaining a fresh packet. GET only, never input/begin/lease/STOP.
"""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import time

import httpx

from explorer_shared.contracts import ACTION_NAMES,canonical,digest
from explorer_shared.embodied_episode import ACTION_CONTRACT_ID,_Evidence
from .so101_shadow_job import _get

PROFILE='ROSMASTER_ACT9_RGB_AVAILABLE_SENSOR106_V1'


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def shadow_request(store,model_job_id):
    if not isinstance(model_job_id,str) or not re.fullmatch('[0-9a-f]{32}',model_job_id):raise ValueError('Select one actual shared ROSMASTER model job')
    job=store.job(model_job_id)
    if not job or job['kind']!='rosmaster_skill_train' or job['status']!='completed':raise ValueError('Completed shared native9 skill model required')
    result=job.get('result') or {};lineage=(job.get('config') or {}).get('skill_workflow') or {}
    if (result.get('device')!='cpu' or not all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed')) or
            result.get('optimizer_steps',0)<1):raise ValueError('Actual CPU optimizer/save/reload model evidence required')
    path=Path(result.get('model_manifest','')).resolve()
    if not path.is_relative_to(store.root.resolve()) or not path.is_file() or sha(path)!=result.get('model_manifest_sha256'):
        raise ValueError('Immutable server model manifest required')
    if path.stat().st_size>16*1024**2:raise ValueError('Bounded saved model metadata required')
    model=json.loads(path.read_bytes())
    if (model.get('schema')!='explorer_rosmaster_skill_model_v1' or model.get('action_contract_id')!=ACTION_CONTRACT_ID or
            model.get('action_names')!=list(ACTION_NAMES) or (model.get('ACT9') or {}).get('state_dimension')!=106 or
            (model.get('ACT9') or {}).get('output_dimension')!=9):
        raise ValueError('ROSMASTER_ACT9_106D_PROFILE_REQUIRED_LEGACY_STATE9_NOT_INTERCHANGEABLE')
    if model.get('source_kind')!=['physical']:raise ValueError('SIMULATION/mixed models remain offline; actual native-source cohort required for live shadow')
    if (model.get('skill_id')!=lineage.get('skill_id') or model.get('skill_version')!=lineage.get('skill_version') or
            model.get('skill_spec_sha256')!=lineage.get('spec_sha256')):raise ValueError('Pinned saved model/source skill lineage differs')
    checkpoint=result.get('checkpoint_sha256');folder=Path(result.get('checkpoint_dir','')).resolve()
    if not isinstance(checkpoint,dict) or not checkpoint or checkpoint!=model.get('weights_sha256') or not folder.is_relative_to(store.root.resolve()):
        raise ValueError('Actual native checkpoint inventory required')
    for name,expected in checkpoint.items():
        file=(folder/name).resolve()
        if Path(name).name!=name or not file.is_relative_to(folder) or sha(file)!=expected:raise ValueError('Native checkpoint/source binding changed')
    return {'model_job_id':model_job_id,'model_manifest_sha256':result['model_manifest_sha256'],
        'model_checkpoint_sha256':digest(checkpoint),'state_profile':PROFILE,'state_dimension':106,'action_dimension':9,
        'backend':'act','device':'cpu','execution_scope':'shadow_without_dispatch','hardware_commands_dispatched':False}


def validate_packet(packet):
    if (not isinstance(packet,dict) or packet.get('schema')!='explorer_rosmaster_cached_observation_v1' or
            packet.get('state_profile')!=PROFILE or packet.get('action_dimension')!=9 or
            packet.get('hardware_commands_dispatched') is not False or packet.get('physical_execution_authorized') is not False):
        raise ValueError('Typed actual source106D packet required; no actuator/expert claims')
    if not re.fullmatch('[0-9a-f]{32}',str(packet.get('observation_id'))):raise ValueError('Original source observation ID required')
    declared=packet.get('source_files_sha256');encoded=packet.get('source_payloads_base64')
    if not isinstance(declared,dict) or not isinstance(encoded,dict) or set(declared)!=set(encoded):raise ValueError('Complete original source asset map required')
    payloads={};size=0
    for name,value in encoded.items():
        if not isinstance(name,str) or not name or Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:raise ValueError('Safe original runtime asset name required')
        raw=base64.b64decode(value,validate=True);size+=len(raw)
        if size>8*1024**2 or hashlib.sha256(raw).hexdigest()!=declared[name]:raise ValueError('Runtime original asset SHA/budget differs')
        payloads[name]=raw
    observation=deepcopy(packet.get('observation') or {});cutoff=observation.get('source_cutoff_monotonic_ns')
    domain,boot=observation.get('clock_domain'),observation.get('boot_id')
    if (type(cutoff) is not int or cutoff<0 or not domain or not boot or observation.get('source_kind')!='physical' or
            observation.get('source_boot_identity_verified') is not True or any(key in observation for key in ('action','target_action9','phase_label'))):
        raise ValueError('Native past observation cutoff/boot required; target labels are not observations')
    missing=[];evidence=_Evidence({'source_files_sha256':declared},payloads,missing);admission={}
    for name,record in (observation.get('channels') or {}).items():
        if not evidence.record(record,'channels.'+name):raise ValueError('Original source channel record SHA/pointer differs:'+name)
        stamp=record.get('stamp') or {};tick=stamp.get('monotonic_ns')
        causal=(stamp.get('clock_domain')==domain and stamp.get('boot_id')==boot and type(tick) is int and 0<=cutoff-tick<=2_000_000_000)
        available=record.get('available') is True and record.get('present') is True
        if available and not causal:raise ValueError('Source channel future/stale/boot/clock differs:'+name)
        admission[name]={'admitted':available and causal,'recorded':available,'mask':deepcopy(record.get('mask'))}
    if missing or any(not admission.get(name,{}).get('admitted') for name in ('camera_rgb','preceding_command9')):
        raise ValueError('Actual source RGB and prior complete command9 are mandatory')
    # Bind the outer cutoff/native context as well as each channel. A caller
    # cannot splice a different issued receipt onto individually valid records.
    proof=observation['channels']['preceding_command9']['provenance']
    original=json.loads(payloads[proof['source_file']]);unwrapped=deepcopy(observation)
    for item in unwrapped['channels'].values():item.pop('provenance',None)
    if canonical(original)!=canonical(unwrapped):raise ValueError('Whole original runtime context differs from its source bytes')
    origin=observation['channels']['preceding_command9'].get('source_record_origin') or {}
    source=payloads.get(origin.get('source_file'))
    if source is None or hashlib.sha256(source).hexdigest()!=origin.get('sha256') or json.loads(source)!=observation.get('native_observation'):
        raise ValueError('Actual original publisher/base source bytes differ')
    command=(observation['channels']['preceding_command9'].get('value') or {})
    values=command.get('values');native=observation.get('native_observation') or {}
    import math
    if (command.get('names')!=list(ACTION_NAMES) or observation['channels']['preceding_command9'].get('mask')!=[True]*9 or
            not isinstance(values,list) or len(values)!=9 or any(type(value) not in (int,float) or not math.isfinite(value) for value in values) or
            native.get('command_reference9_basis')!='actual_original_publisher_and_base_request_as_of_snapshot' or
            native.get('journal_pose_used_for_command9') is not False or native.get('command_reference9')!=values):
        raise ValueError('Whole original command9 required, never reconstructed from measured odometry')
    return observation,payloads,admission


def run_rosmaster_shadow(store,job,config,*,client_factory=httpx.Client,loader=None,clock=time.monotonic_ns):
    expected=shadow_request(store,job['config'].get('model_job_id'))
    if any(job['config'].get(key)!=value for key,value in expected.items()):raise ValueError('Queued native model binding changed')
    if 'skill_reference' in job['config']:
        from .rosmaster_learning import model_reference
        reference=job['config']['skill_reference']
        if reference!=model_reference(store,reference.get('skill_id'),reference.get('skill_version'),expected['model_job_id']):
            raise ValueError('Queued shadow skill version/aggregate binding changed')
    if loader is None:
        from explorer_shared.learning.rosmaster_skill_pipeline import RosmasterSkillPolicy
        loader=lambda path:RosmasterSkillPolicy(path,candidate='ACT9')
    training=store.job(expected['model_job_id']);model_path=training['result']['model_manifest']
    node=next((item for item in config['nodes'] if item['id']=='nano'),None)
    if node is None:raise ValueError('Configured native Nano observer required')
    def cancelled():
        current=store.job(job['id'])
        if current and current['status'] in ('cancel_requested','cancelled'):raise RuntimeError('Shadow cancelled before model/source/inference; no control request sent')
    cancelled();policy=loader(model_path);cancelled()  # load OUTSIDE all source/hold freshness windows
    with client_factory(base_url='http://'+node['host']+':'+str(node['port']),headers={'Authorization':'Bearer '+node['token']},
            timeout=2,trust_env=False,follow_redirects=False) as client:
        begin=clock();raw,_=_get(client,'/native-policy/observation',16*1024**2);packet=json.loads(raw)
        observation,payloads,admission=validate_packet(packet);identity=packet['observation_id']
        image,headers=_get(client,'/native-policy/observations/'+identity+'/frame',2*1024**2)
        rgb=observation['channels']['camera_rgb']['value']
        if (headers.get('x-observation-id')!=identity or headers.get('x-frame-id')!=rgb['frame_id'] or
                headers.get('x-boot-id')!=observation['boot_id'] or headers.get('x-jpeg-sha256')!=rgb['sha256'] or
                headers.get('x-clock-domain')!=observation['clock_domain'] or
                image!=payloads.get(rgb['source_file'])):raise ValueError('Exact pinned native observation JPEG/boot/SHA differs')
        cancelled()
        if clock()-begin>2_000_000_000:raise ValueError('Native observation transport expired')
        inference_start=clock()
        result=policy.predict(observation,payloads.__getitem__,verify_observation=lambda _observation,_model:deepcopy(admission))
        ended=clock();cancelled()
        if ended-begin>2_000_000_000:raise ValueError('Native observation expired during inference; prediction cannot qualify live shadow')
        action=result.get('action9')
        import math
        if (result.get('hardware_io') is not False or not isinstance(action,list) or len(action)!=9 or
                any(type(value) not in (int,float) or not math.isfinite(value) for value in action)):
            raise ValueError('Finite native9 prediction without hardware I/O required')
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    from explorer_shared.cooperative_recording05 import write_original
    artifacts={'observation.json':raw,'frame.jpg':image,'prediction.json':canonical(result)}
    inventory={}
    for name,value in artifacts.items():
        path=folder/name
        if path.exists():
            if path.read_bytes()!=value:raise ValueError('Immutable actual shadow artifact changed')
        else:write_original(path,value)
        inventory[name]={'sha256':hashlib.sha256(value).hexdigest(),'bytes':len(value)}
    return {'schema':'explorer_rosmaster_fresh_shadow_v1','state_profile':PROFILE,'state_dimension':106,'action_dimension':9,
        'action9':action,'prediction':result,'model_job_id':expected['model_job_id'],'checkpoint_sha256':expected['model_checkpoint_sha256'],
        'artifacts':inventory,'source_observation_id':identity,'source_evidence':{'boot_id':observation['boot_id'],'clock_domain':observation['clock_domain'],
            'channel_admission':admission,'raw_packet_sha256':hashlib.sha256(raw).hexdigest()},
        'model_loaded_before_fresh_source':True,'request_inference_elapsed_ms':(ended-begin)/1e6,
        'inference_ms':(ended-inference_start)/1e6,'runtime_implementation_sha256':{
            'rosmaster_shadow_job.py':sha(__file__),
            'rosmaster_skill_pipeline.py':sha(Path(__file__).parents[3]/'shared/explorer_shared/learning/rosmaster_skill_pipeline.py')
        },'hardware_commands_dispatched':False,'physical_execution_authorized':False,
        'physical_control_requests_sent':0,'independent_full_task_success':False,'dispatch_performed':False}
