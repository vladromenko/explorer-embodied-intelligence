"""Exact first-action reconstruction on the finite pilot's recorded inputs."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from .learning import write_json, sha256
from .recovery_micro_learning import dataset, build


def replay_refusal(source,trial,output):
    from PIL import Image
    from .recovery_retest import load
    from .room_policy import issued_command
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    trial=Path(trial);kind=trial.name.split('-')[0]
    result=json.loads((trial/'result.json').read_text())
    recorded=json.loads((trial/'last-policy-exposure.json').read_text())
    is_v2=result.get('contract_schema')=='recovery_stationary_grasp_v2'
    if is_v2:
        if not (trial/'refusal.json').is_file():
            raise ValueError('v2 replay requires a PRE-ACTION refusal, not a final post-motion image')
        refused=json.loads((trial/'refusal.json').read_text())
        recorded=refused['policy_exposure']
    actor=load(Path(source)/kind,kind).actor
    state=np.asarray(refused['state'] if is_v2 else result['physics_state']['command'],dtype=np.float32)
    image_path=trial/('refusal.png' if is_v2 else 'final-wrist.png')
    rgb=np.asarray(Image.open(image_path).convert('RGB'))
    torch.set_num_threads(1)
    raw=actor(state,rgb)
    expected=(recorded['network_prediction9'] if 'network_prediction9' in recorded
        else recorded['full_physical_chunk'][0][0])
    error=float(np.max(abs(raw-np.asarray(expected))))
    try:
        if is_v2:
            from .recovery_contracts import StationaryExecutor
            from .environment import ExplorerEnv
            executor=StationaryExecutor(ExplorerEnv.validate_command,issued_command)
            executor.residual[:]=refused['residual']
            executor.prepare(state,raw)
        else:
            issued_command(state,raw,np.zeros(6))
        guard=dict(accepted=True)
    except ValueError as refusal:
        guard=dict(accepted=False,reason=str(refusal))
    write_json(output/'result.json',dict(state=state.tolist(),raw=raw.tolist(),
        exposure=actor.last_exposure,guard=guard,max_abs_reproduction_error=error,
        checkpoint_sha256=sha256(Path(source)/kind/'checkpoint.pt'),
        image_sha256=sha256(image_path),source_trial=str(trial),
        physics_steps=0,training_updates=0,physical_ready=False,
        replay_scope='pre-action raw inference and guard only; not a full dynamics replay'))
    return dict(max_abs_reproduction_error=error,guard=guard)


def measure(teacher,source,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    data=dataset(teacher);results={};torch.set_num_threads(1)
    for kind in ('bc','act'):
        checkpoint=Path(source)/kind/'checkpoint.pt'
        manifest=json.loads((checkpoint.parent/'training.json').read_text())
        if sha256(checkpoint)!=manifest['checkpoint_sha256']:
            raise ValueError('Checkpoint changed')
        value=torch.load(checkpoint,map_location='cpu',weights_only=False)
        model=build(kind);model.load_state_dict(value['model']);model.eval()
        mean,std,amean,astd=value['stats'];chunks=[]
        with torch.no_grad():
            for index in range(0,len(data['state']),8):
                state=(data['state'][index:index+8]-mean)/std
                image=data['images'][index:index+8].float()/255.
                prediction=(model(state,image)[:,None] if kind=='bc' else model.predict_action_chunk(
                    {'observation.state':state,'observation.images.wrist':image}))
                chunks.append((prediction*astd+amean).numpy())
        chunks=np.concatenate(chunks)
        prediction=chunks[:,0];error=abs(prediction-data['action'].numpy())
        np.savez_compressed(output/(kind+'-predictions.npz'),physical_chunks=chunks,
            observation_state=data['state'].numpy(),targets=data['action'].numpy())
        results[kind]=dict(updates=value['updates'],checkpoint_sha256=sha256(checkpoint),
            samples=len(error),arm_mae_deg=error[:,:6].mean(0).tolist(),
            arm_p95_deg=np.quantile(error[:,:6],.95,axis=0).tolist(),
            arm_max_deg=error[:,:6].max(0).tolist(),
            outside_one_degree_rows=int(np.any(error[:,:6]>1.,axis=1).sum()),
            gripper_below_30=int((prediction[:,5]<30).sum()),
            gripper_above_170=int((prediction[:,5]>170).sum()),
            negative_arm_predictions=int((prediction[:,:5]<0).any(axis=1).sum()),
            base_abs_prediction_max=abs(prediction[:,6:]).max(0).tolist(),
            task_clock=False,execution_horizon=1,offline_only=True)
    result=dict(results=results,source=str(source),teacher=str(teacher),
        physical_ready=False,limitations='Offline reconstruction cannot establish closed-loop grasp or generalization')
    write_json(output/'result.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--teacher',type=Path)
    parser.add_argument('--trial',type=Path)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    value=(replay_refusal(args.source,args.trial,args.output) if args.trial else
        measure(args.teacher,args.source,args.output))
    print(json.dumps(value,ensure_ascii=False))
