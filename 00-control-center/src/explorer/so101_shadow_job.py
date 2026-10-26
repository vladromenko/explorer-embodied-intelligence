"""ENGINEERING_GLUE: isolated ACT6 / qualified history2 DP6 cached shadow.

Only GETs to the existing Pi source. No actuator, policy start, lease, STOP or
camera ownership. ACT model loading stays outside the central API process.
"""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import httpx
from explorer_shared.contracts import digest


def shadow_request(store,model_job_id):
    if not isinstance(model_job_id,str) or not re.fullmatch('[0-9a-f]{32}',model_job_id):raise ValueError('Select a completed source04 model')
    training=store.job(model_job_id)
    if not training or training['kind']!='so101_skill_train' or training['status']!='completed':raise ValueError('Completed source04 whole-skill model required')
    config=training.get('config') or {};result=training.get('result') or {}
    if config.get('backend','act')=='diffusion':
        from .transfer_source import _metadata
        model=_metadata(store,training)
        if model['source_domain']!='PHYSICAL':raise ValueError('A real native-source cohort is required; SIMULATION is offline only')
        return dict(schema='explorer_so101_diffusion_shadow_request_v1',model_job_id=model_job_id,
            model_checkpoint_sha256=model['checkpoint_content_sha256'],backend='diffusion',device='cpu',
            model_training_receipt_sha256=model['training_receipt_sha256'],
            aggregate_manifest_sha256=model['result']['aggregate_manifest_sha256'],spec_sha256=model['manifest']['spec_sha256'],
            history_steps=2,prediction_steps=4,execution_scope='shadow_without_dispatch',hardware_commands_dispatched=False)
    if config.get('backend','act')!='act' or result.get('backend','act')!='act':raise ValueError('Unknown or mismatched server source shadow backend')
    if result.get('source_kind')!='SO101_RECORDED_DEMONSTRATIONS' or not all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed')):
        raise ValueError('A real native-source cohort and optimizer/save/reload evidence are required; SIMULATION is offline only')
    if not isinstance(result.get('checkpoint_sha256'),dict) or not result['checkpoint_sha256']:raise ValueError('Server checkpoint SHA binding is missing')
    return dict(model_job_id=model_job_id,model_checkpoint_sha256=digest(result['checkpoint_sha256']),backend='act',
        execution_scope='shadow_without_dispatch',hardware_commands_dispatched=False)


def _get(client,path,limit,*,guard=None):
    # Streaming keeps a malformed/oversized upstream response out of RAM.
    if guard is not None:guard()
    with client.stream('GET',path) as response:
        response.raise_for_status();chunks=[];size=0
        for chunk in response.iter_bytes():
            if guard is not None:guard()
            size+=len(chunk)
            if size>limit:raise ValueError('Source observation response exceeds its bound')
            chunks.append(chunk)
        if guard is not None:guard()
        return b''.join(chunks),dict(response.headers)


def run_so101_shadow(store,job,config,*,client_factory=httpx.Client,loader=None,predictor=None,clock=time.monotonic_ns):
    if job['config'].get('backend')=='diffusion':
        if predictor is not None:raise ValueError('DP shadow uses SourcePolicyProbe/history2, never the ACT predictor adapter')
        return _run_diffusion_shadow(store,job,config,client_factory=client_factory,loader=loader,clock=clock)
    import numpy as np
    from PIL import Image
    from .so101_shadow import load_shadow_policy,infer_shadow
    loader=loader or load_shadow_policy;predictor=predictor or infer_shadow
    expected=shadow_request(store,job['config'].get('model_job_id'))
    if any(job['config'].get(key)!=value for key,value in expected.items()):raise ValueError('Queued model binding changed')
    node=next((row for row in config['nodes'] if row['id']=='pi'),None)
    if node is None:raise ValueError('Pi native source is not configured')
    output=store.root/'jobs'/job['id'];output.mkdir(parents=True,exist_ok=True)
    def cancelled():
        current=store.job(job['id'])
        if current and current['status'] in ('cancel_requested','cancelled'):raise RuntimeError('Shadow cancelled; no hardware command was sent')
    base='http://'+node['host']+':'+str(node['port'])
    with client_factory(base_url=base,headers={'Authorization':'Bearer '+node['token']},timeout=2,trust_env=False,follow_redirects=False) as client:
        cancelled();initial_raw,_=_get(client,'/station/observation',128*1024);initial=json.loads(initial_raw)
        policy=loader(store,expected['model_job_id'],expected_camera_id=initial['observation']['camera_id'],
            expected_calibration_sha256=initial['observation']['follower_calibration_sha256'])
        # Loading may take seconds; obtain a NEW source packet after loading.
        cancelled();begin=clock();packet_raw,_=_get(client,'/station/observation',128*1024);packet=json.loads(packet_raw)
        observation=packet['observation'];ident=observation['observation_id']
        if not re.fullmatch('[0-9a-f]{32}',str(ident)):raise ValueError('Invalid original source observation ID')
        jpeg,headers=_get(client,'/station/observations/'+ident+'/frame',2*1024**2)
        if (headers.get('x-observation-id')!=ident or headers.get('x-frame-id')!=observation['frame_id'] or
                headers.get('x-boot-id')!=observation['frame_boot_id'] or
                headers.get('x-jpeg-sha256')!=observation['frame_sha256'] or
                hashlib.sha256(jpeg).hexdigest()!=observation['frame_sha256']):raise ValueError('Authenticated pinned source JPEG/frame/boot/SHA differs')
        if clock()-begin>2_000_000_000:raise ValueError('Source packet transport expired; no live shadow prediction accepted')
        with Image.open(BytesIO(jpeg)) as image:
            if image.size!=(observation['width'],observation['height']) or image.width*image.height>1920*1080:raise ValueError('Pinned JPEG dimensions differ')
            rgb=np.asarray(image.convert('RGB'))
        cancelled();inference_started=clock()
        result=predictor(policy,rgb,packet['measured_native6'],valid_mask=packet['valid_mask'],observation=observation)
        ended=clock();cancelled()
        if ended-begin>2_000_000_000:raise ValueError('Source observation expired during inference; no live shadow result accepted')
        if result.get('hardware_commands_dispatched') is not False or result.get('physical_execution_authorized') is not False:raise ValueError('Shadow predictor returned an execution claim')
    artifacts={'observation.json':packet_raw,'frame.jpg':jpeg,'prediction.json':json.dumps(result,sort_keys=True,allow_nan=False).encode()}
    files={}
    for name,raw in artifacts.items():
        path=output/name
        if path.exists() and path.read_bytes()!=raw:raise ValueError('Immutable shadow artifact changed')
        with path.open('xb' if not path.exists() else 'rb') as stream:
            if stream.writable():
                import os
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
        files[name]=dict(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
    import os
    descriptor=os.open(output,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)
    return dict(result,phase='fresh_native_ACT6_shadow',model_job_id=expected['model_job_id'],artifacts=files,
        transport_freshness_verified_by_worker=True,source_observation_id=ident,request_inference_elapsed_ms=(ended-begin)/1e6,
        inference_ms=(ended-inference_started)/1e6,physical_control_requests_sent=0,independent_full_task_success=False)


def _diffusion_worker_runtime(job):
    """Reject direct wrong-interpreter calls before imports/model/network IO."""
    from .policy_runtime import qualified_diffusion_runtime
    selected=qualified_diffusion_runtime()
    executable=Path(selected['executable']).absolute()
    if (job['config'].get('_policy_runtime_binding_sha256')!=selected['binding_sha256'] or
            Path(sys.executable).absolute()!=executable or Path(sys.prefix).absolute()!=executable.parent.parent):
        raise ValueError('DP shadow requires the server-pinned qualified Diffusion interpreter/prefix/binding')
    return dict(binding_sha256=selected['binding_sha256'],executable=str(executable),prefix=sys.prefix)


def _run_diffusion_shadow(store,job,config,*,client_factory,loader,clock):
    """One finite GET-only probe: two real chronological frames -> all4 native6 outputs."""
    import numpy as np
    from PIL import Image
    from explorer_shared.source_archive import canonical,_write
    from .transfer_source import _metadata,SourcePolicyProbe
    from explorer_shared.so101_station import NAMES,UNITS
    expected=shadow_request(store,job['config'].get('model_job_id'))
    if any(job['config'].get(key)!=value for key,value in expected.items()):raise ValueError('Queued DP model binding changed')
    def cancelled():
        current=store.job(job['id'])
        if not current or current['status'] not in ('queued','running'):raise RuntimeError('DP shadow cancelled/ended; no hardware request was sent')
    cancelled();runtime=_diffusion_worker_runtime(job)
    model=_metadata(store,store.job(expected['model_job_id']))
    node=next((row for row in config['nodes'] if row['id']=='pi'),None)
    if node is None:raise ValueError('Pi native source is not configured')
    output=Path(store.root).resolve()/'jobs'/job['id']
    if output.resolve()!=output:raise ValueError('Private DP shadow output alias is forbidden')
    output.mkdir(parents=True,exist_ok=True)
    if (output/'diffusion-shadow-context.json').exists():raise ValueError('Prior/interrupted DP shadow is retained; prepare a new job')
    files={}
    def keep(name,raw):
        target=output/name
        if target.resolve()!=target or not target.is_relative_to(output):raise ValueError('Private DP artifact path changed')
        _write(target,raw)
        files[name]=dict(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
    keep('diffusion-shadow-context.json',canonical(dict(expected,policy_runtime=runtime,hardware_control_requests_sent=0)))
    history=[];polls=[]
    try:
        cancelled();base='http://'+node['host']+':'+str(node['port'])
        with client_factory(base_url=base,headers={'Authorization':'Bearer '+node['token']},timeout=2,trust_env=False,follow_redirects=False) as client:
            def fetch(path,limit):
                started=clock()
                def guard():
                    cancelled()
                    if not 0<=clock()-started<=2_000_000_000:raise ValueError('DP source streaming transport expired')
                return _get(client,path,limit,guard=guard)
            cancelled();initial_raw,_=fetch('/station/observation',128*1024);cancelled();initial=json.loads(initial_raw)
            keep('preload-observation.json',initial_raw)
            if initial.get('schema')!='explorer_so101_cached_observation_v1' or initial.get('names')!=list(NAMES) or initial.get('units')!=list(UNITS):raise ValueError('Original DP packet native6 names/units/schema differ')
            camera=initial['observation']['camera_id'];calibration=initial['observation']['follower_calibration_sha256']
            if camera not in model['source_camera_ids'] or calibration!=model['source_calibration_sha256']:raise ValueError('Current DP source camera/calibration differs from the frozen cohort')
            binding=dict(schema='explorer_native_source_probe_binding_v1',model_job_id=model['job']['id'],backend='diffusion',
                checkpoint_path=model['checkpoint_path'],checkpoint_sha256=model['checkpoint_sha256'],checkpoint_content_sha256=model['checkpoint_content_sha256'],
                source_camera_ids=model['source_camera_ids'],source_calibration_sha256=model['source_calibration_sha256'],history_steps=2,prediction_steps=4)
            cancelled();probe=SourcePolicyProbe(binding,camera_id=camera,loader=loader);cancelled()
            began=clock();attempts=0
            while len(history)<2 and attempts<20 and clock()-began<=2_000_000_000:
                cancelled();started=clock();packet_raw,_=fetch('/station/observation',128*1024);cancelled()
                packet=json.loads(packet_raw);observation=packet['observation'];ident=observation['observation_id']
                if packet.get('schema')!='explorer_so101_cached_observation_v1' or packet.get('names')!=list(NAMES) or packet.get('units')!=list(UNITS):raise ValueError('Original DP packet native6 names/units/schema differ')
                if not re.fullmatch('[0-9a-f]{32}',str(ident)):raise ValueError('Invalid original DP source observation ID')
                stem='observations/'+str(attempts).zfill(2);keep(stem+'.json',packet_raw)
                jpeg,headers=fetch('/station/observations/'+ident+'/frame',8*1024**2);cancelled();keep(stem+'.jpg',jpeg)
                if (headers.get('x-observation-id')!=ident or headers.get('x-frame-id')!=observation['frame_id'] or
                        headers.get('x-boot-id')!=observation['frame_boot_id'] or headers.get('x-jpeg-sha256')!=observation['frame_sha256'] or
                        hashlib.sha256(jpeg).hexdigest()!=observation['frame_sha256']):raise ValueError('Authenticated original DP JPEG/frame/boot/SHA differs')
                if clock()-started>2_000_000_000:raise ValueError('DP source packet transport expired')
                with Image.open(BytesIO(jpeg)) as image:
                    if image.size!=(observation['width'],observation['height']) or image.width*image.height>1920*1080:raise ValueError('Original DP JPEG dimensions differ')
                    rgb=np.asarray(image.convert('RGB'))
                # Use the same native6 validation before accepting any history image.
                from explorer_shared.learning.act_policy import validate_observation
                from explorer_shared.so101_station import LOW,HIGH
                validate_observation(rgb,packet['measured_native6'],packet['valid_mask'],observation,camera_id=camera,
                    calibration_sha256=calibration,low=np.asarray(LOW),high=np.asarray(HIGH))
                previous=history[-1]['observation'] if history else None
                if previous and (observation['frame_boot_id']!=previous['frame_boot_id'] or observation['frame_clock_domain']!=previous['frame_clock_domain']):raise ValueError('DP source history clock/boot changed')
                if previous and ident==previous['observation_id']:raise ValueError('Duplicate pinned DP source observation identity')
                if previous and observation['state_monotonic_ns']<previous['state_monotonic_ns']:raise ValueError('DP measured-state history moved backwards')
                if (previous and observation['state_monotonic_ns']==previous['state_monotonic_ns'] and
                        not np.array_equal(packet['measured_native6'],history[-1]['packet']['measured_native6'])):
                    raise ValueError('One native measurement timestamp cannot identify different DP states')
                fresh=(previous is None or observation['frame_id']!=previous['frame_id'] and
                    observation['frame_monotonic_ns']>previous['frame_monotonic_ns'] and observation['source_cutoff_monotonic_ns']>previous['source_cutoff_monotonic_ns'])
                polls.append(dict(index=attempts,observation_id=ident,frame_id=observation['frame_id'],accepted_for_history=fresh,
                    reason=None if fresh else 'REPEATED_OR_NONINCREASING_NATIVE_CAMERA_FRAME',observation_file=stem+'.json',jpeg_file=stem+'.jpg'))
                if fresh:history.append(dict(observation=observation,packet=packet,rgb=rgb,started=started,raw=packet_raw,jpeg=jpeg,index=attempts))
                attempts+=1
                if len(history)<2:time.sleep(.02)
            keep('history-selection.json',canonical(dict(history_steps=2,polls=polls,accepted_indices=[entry['index'] for entry in history])))
            if len(history)!=2:raise ValueError('Two distinct chronological fresh native DP frames were not available in the bounded window')
            def freshness():
                for entry in history:
                    o=entry['observation'];local_elapsed=clock()-entry['started']
                    if local_elapsed<0 or any(o['source_cutoff_monotonic_ns']-o[key]+local_elapsed>2_000_000_000 for key in ('frame_monotonic_ns','state_monotonic_ns')):
                        raise ValueError('DP source history expired in transport/inference; no live shadow result accepted')
            cancelled();freshness();inference_started=clock()
            predicted=probe.predict_history(np.stack([entry['rgb'] for entry in history]),
                np.asarray([entry['packet']['measured_native6'] for entry in history]),
                valid_mask=np.asarray([entry['packet']['valid_mask'] for entry in history]),observations=[entry['observation'] for entry in history])
            keep('raw-source-prediction.json',canonical(predicted))
            ended=clock();cancelled();freshness()
        if shadow_request(store,expected['model_job_id'])!=expected:raise ValueError('Frozen DP source cohort changed during prediction')
        # Explicit aliases point to the LAST original, never to a manufactured temporal view.
        keep('observation.json',history[-1]['raw']);keep('frame.jpg',history[-1]['jpeg'])
        result=dict(schema='explorer_so101_diffusion_live_shadow_v1',backend='diffusion',phase='fresh_native_Diffusion6_history2_shadow',
            model_job_id=expected['model_job_id'],checkpoint_content_sha256=model['checkpoint_content_sha256'],
            aggregate_manifest_sha256=expected['aggregate_manifest_sha256'],spec_sha256=expected['spec_sha256'],policy_runtime=runtime,
            action_names=list(NAMES),action_units=list(UNITS),predicted_native6=predicted['predicted_native6'],
            first_native6=predicted['predicted_native6'][0],history_steps=2,prediction_steps=4,source_prediction=predicted,
            source_observation_ids=[entry['observation']['observation_id'] for entry in history],
            source_frame_ids=[entry['observation']['frame_id'] for entry in history],history_indices=[entry['index'] for entry in history],
            source_observation_id=history[-1]['observation']['observation_id'],
            execution_kind='fresh_native_history2_policy_inference_without_dispatch',transport_freshness_verified_by_worker=True,
            request_inference_elapsed_ms=(ended-began)/1e6,inference_ms=(ended-inference_started)/1e6,
            physical_execution_authorized=False,hardware_commands_dispatched=False,hardware_control_requests_sent=0,
            physical_control_requests_sent=0,independent_full_task_success=False,qualifies_object_source=False,
            cooperative_warm_qualified=False,full_chunk_preserved=True)
        keep('prediction.json',canonical(result));result['artifacts']=files
        return result
    except Exception as error:
        keep('shadow-error.json',canonical(dict(error_type=type(error).__name__,detail=str(error)[:1000],
            accepted_history_steps=len(history),hardware_control_requests_sent=0,physical_success_claimed=False)))
        raise
