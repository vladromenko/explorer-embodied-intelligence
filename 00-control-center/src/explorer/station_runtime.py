"""ENGINEERING_GLUE: typed six-axis ACT inference through the existing Pi station.

Checkpoint, normalization, source calibration and current bus roles must agree.
No serial ports, controller configuration or robot-learning success inference.
"""
import hashlib
import io
import json
from pathlib import Path
import sys
import time

import httpx


def validate_source(store,skill_id,status):
    from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH
    card=next((item for item in store.skills() if item['id']==skill_id),None)
    if not card:
        raise ValueError('Selected skill does not exist')
    contract=card.get('action_contract',{})
    if contract.get('names')!=list(NAMES) or contract.get('units')!=UNITS or contract.get('state_basis')!='measured_state':
        raise ValueError('This checkpoint is not a measured six-axis SO-101 policy')
    training=store.job(card.get('lineage',{}).get('job_id'))
    if not training or training['status']!='completed' or not (training.get('result') or {}).get('trained'):
        raise ValueError('Training has not completed successfully')
    result=training['result']
    source=Path(result['source_manifest_path'])
    if hashlib.sha256(source.read_bytes()).hexdigest()!=result['source_manifest_sha256']:
        raise ValueError('Training source manifest changed')
    manifest=json.loads(source.read_text())
    profiles=manifest.get('source_calibration_metadata',{}).get('calibration_metadata',{})
    for role in ('leader','follower'):
        expected=profiles.get(role,{}).get('calibration_sha256')
        if not expected or expected!=status.get('roles',{}).get(role,{}).get('calibration_sha256'):
            raise ValueError('Recorded and connected '+role+' calibration do not match')
    checkpoint=store.root/'jobs'/training['id']/'student_act'
    for name,digest in result['checkpoint_sha256'].items():
        if Path(name).name!=name or hashlib.sha256((checkpoint/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Checkpoint checksum changed')
    preprocessing=json.loads((checkpoint/'explorer_preprocessing.json').read_text())
    if preprocessing.get('state_low')!=LOW or preprocessing.get('state_high')!=HIGH:
        raise ValueError('Checkpoint normalization does not match original station coordinates')
    return card,checkpoint,preprocessing


def run_station_policy(store,job,config):
    import numpy as np
    from PIL import Image
    import torch
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.act.configuration_act import ACTConfig
    from explorer_shared.so101_station import NAMES,UNITS
    sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
    from explorer_sim.student import IMAGE_KEY,prepare_rgb,framework_info
    requested=job['config']
    def cancellation_requested():
        current=store.job(job['id'])
        return current and current['status'] in ('cancel_requested','cancelled')
    shadow=bool(requested.get('shadow',False))
    duration=float(requested.get('duration_s',10))
    if not 0<duration<=60:
        raise ValueError('Choose a finite station attempt of at most 60 seconds')
    if not shadow and time.time()-job['created']>10:
        raise ValueError('Interactive execution request expired in the queue; start a new attempt')
    node=next(item for item in config['nodes'] if item['id']=='pi')
    base='http://'+node['host']+':'+str(node['port'])
    folder=store.root/'jobs'/job['id']
    folder.mkdir(parents=True,exist_ok=True)
    rows,receipts=[],{}
    (folder/'frames').mkdir(exist_ok=True)
    started=time.monotonic()
    epoch=None
    with httpx.Client(base_url=base,headers={'Authorization':'Bearer '+node['token']},timeout=4,trust_env=False) as client:
        def get(path):
            response=client.get(path);response.raise_for_status();return response
        def post(path,body):
            response=client.post('/station/'+path,json=body);response.raise_for_status();return response.json()
        status=get('/station/status').json()
        card,checkpoint,preprocessing=validate_source(store,requested.get('skill_id'),status)
        policy_config=ACTConfig.from_pretrained(checkpoint,local_files_only=True)
        policy_config.device='cpu'
        if tuple(policy_config.input_features['observation.state'].shape)!=(6,) or tuple(policy_config.output_features['action'].shape)!=(6,):
            raise ValueError('Actual ACT checkpoint does not have six state and action coordinates')
        policy=ACTPolicy.from_pretrained(checkpoint,config=policy_config,local_files_only=True,strict=True).cpu().eval()
        torch.set_num_threads(4)
        low=np.asarray(preprocessing['state_low'],dtype=np.float32)
        high=np.asarray(preprocessing['state_high'],dtype=np.float32)
        try:
            if not shadow:
                if cancellation_requested():
                    raise RuntimeError('Policy request was cancelled before control started')
                if time.time()-job['created']>10:
                    raise ValueError('Interactive execution request expired while loading its model; start a new attempt')
                status=post('policy/start',dict(observing=requested.get('observing'),operator_present=requested.get('operator_present'),
                    expected_control_epoch=requested.get('expected_control_epoch')))
                epoch=status['control_epoch']
                store.update_job(job['id'],result={'phase':'observed_policy_attempt','control_epoch':epoch,'frames':0})
            sequence=0
            started=time.monotonic()
            while time.monotonic()-started<duration:
                frame=get('/cameras/pi-overview/snapshot')
                frame_ns=int(frame.headers['X-Monotonic-NS'])
                for attempt in range(10):
                    status=get('/station/status').json()
                    state=status['measurements']
                    if frame_ns<=state['monotonic_ns']:
                        break
                    time.sleep(.01)
                receipt=status.get('last_command')
                if epoch and receipt and receipt.get('control_epoch')==epoch and receipt.get('sequence',-1)>=0:
                    receipts[str(receipt['sequence'])]=receipt
                if frame_ns>state['monotonic_ns']:
                    raise ValueError('Camera/state timestamps do not form a causal observation')
                if epoch and (status['control_epoch']!=epoch or status['mode']!='policy'):
                    raise ValueError('Station control was stopped or taken over')
                with Image.open(io.BytesIO(frame.content)) as image:
                    rgb=np.asarray(image.convert('RGB').resize((policy.config.input_features[IMAGE_KEY].shape[-1],)*2))
                native=np.asarray(state['follower'],dtype=np.float32)
                if native.shape!=(6,) or not np.isfinite(native).all():
                    raise ValueError('Six finite measured follower coordinates are required')
                inputs={IMAGE_KEY:prepare_rgb(rgb[None]),'observation.state':torch.from_numpy((2*(native-low)/(high-low)-1)[None])}
                inference_start=time.monotonic()
                with torch.inference_mode():
                    predicted=policy.predict_action_chunk(inputs)[0,0].numpy()
                inference_ms=(time.monotonic()-inference_start)*1000
                if predicted.shape!=(6,) or not np.isfinite(predicted).all():
                    raise ValueError('ACT must predict exactly six finite action coordinates')
                action=(low+(predicted+1)*(high-low)/2).tolist()
                if epoch:
                    if cancellation_requested():
                        raise RuntimeError('Policy attempt was cancelled')
                    post('action',dict(observing=True,operator_present=bool(requested.get('operator_present')),control_epoch=epoch,
                        sequence=sequence,action_names=list(NAMES),action_units=UNITS,action=action,duration_s=1,
                        source_frame_monotonic_ns=frame_ns,source_clock_domain=state['clock_domain'],source_frame_id=frame.headers['X-Frame-ID']))
                frame_file=f'frames/{sequence:06d}.jpg'
                (folder/frame_file).write_bytes(frame.content)
                rows.append(dict(frame_id=frame.headers['X-Frame-ID'],frame_monotonic_ns=frame_ns,
                    frame_file=frame_file,frame_sha256=hashlib.sha256(frame.content).hexdigest(),
                    state_monotonic_ns=state['monotonic_ns'],clock_domain=state['clock_domain'],state=native.tolist(),
                    predicted_action=action,inference_ms=inference_ms,
                    accepted_by_station=bool(epoch),sequence=sequence))
                if sequence==0:
                    (folder/'frame.jpg').write_bytes(frame.content)
                (folder/'policy-observations.json').write_text(json.dumps(rows,indent=2))
                (folder/'dispatch-receipts.json').write_text(json.dumps(receipts,indent=2))
                store.update_job(job['id'],progress=min(.98,(time.monotonic()-started)/duration),result={'phase':'shadow_inference' if shadow else 'observed_policy_attempt','frames':len(rows),'control_epoch':epoch})
                sequence+=1
                time.sleep(max(0,.1-(time.monotonic()-inference_start)))
        finally:
            if epoch:
                response=client.post('/station/stop',json={'expected_control_epoch':epoch},timeout=1.5)
                superseded=response.status_code==409 and 'control session has already ended' in response.text
                (folder/'stop-receipt.json').write_text(json.dumps({'confirmed':response.is_success,'superseded':superseded,'status':response.status_code}))
                if not superseded:
                    response.raise_for_status()
                    receipt=response.json().get('preceding_command')
                    if receipt and receipt.get('control_epoch')==epoch and receipt.get('sequence',-1)>=0:
                        receipts[str(receipt['sequence'])]=receipt
                        (folder/'dispatch-receipts.json').write_text(json.dumps(receipts,indent=2))
    return dict(skill_id=card['id'],mode='shadow' if shadow else 'physical_attempt',physical_io=not shadow,
        physical_success_inferred=False,outcome='unknown',observations=len(rows),elapsed_s=time.monotonic()-started,
        max_predicted_displacement_from_measured=max((abs(a-b) for row in rows for a,b in zip(row['predicted_action'],row['state'])),default=None),
        observation_log=str(folder/'policy-observations.json'),checkpoint=str(checkpoint),**framework_info())
