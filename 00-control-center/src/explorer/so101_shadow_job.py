"""ENGINEERING_GLUE: isolated ACT6 worker over authenticated live cached packets.

Only GETs to the existing Pi source. No actuator, policy start, lease, STOP or
camera ownership. ACT model loading stays outside the central API process.
"""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import time

import httpx
from explorer_shared.contracts import digest


def shadow_request(store,model_job_id):
    if not isinstance(model_job_id,str) or not re.fullmatch('[0-9a-f]{32}',model_job_id):raise ValueError('Select a completed source04 model')
    training=store.job(model_job_id)
    if not training or training['kind']!='so101_skill_train' or training['status']!='completed':raise ValueError('Completed source04 whole-skill model required')
    config=training.get('config') or {};result=training.get('result') or {}
    if config.get('backend','act')!='act' or result.get('backend','act')!='act':raise ValueError('Live source shadow currently uses ACT6; Diffusion history2 needs its own qualified adapter')
    if result.get('source_kind')!='SO101_RECORDED_DEMONSTRATIONS' or not all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed')):
        raise ValueError('A real native-source cohort and optimizer/save/reload evidence are required; SIMULATION is offline only')
    if not isinstance(result.get('checkpoint_sha256'),dict) or not result['checkpoint_sha256']:raise ValueError('Server checkpoint SHA binding is missing')
    return dict(model_job_id=model_job_id,model_checkpoint_sha256=digest(result['checkpoint_sha256']),backend='act',
        execution_scope='shadow_without_dispatch',hardware_commands_dispatched=False)


def _get(client,path,limit):
    # Streaming keeps a malformed/oversized upstream response out of RAM.
    with client.stream('GET',path) as response:
        response.raise_for_status();chunks=[];size=0
        for chunk in response.iter_bytes():
            size+=len(chunk)
            if size>limit:raise ValueError('Source observation response exceeds its bound')
            chunks.append(chunk)
        return b''.join(chunks),dict(response.headers)


def run_so101_shadow(store,job,config,*,client_factory=httpx.Client,loader=None,predictor=None,clock=time.monotonic_ns):
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
