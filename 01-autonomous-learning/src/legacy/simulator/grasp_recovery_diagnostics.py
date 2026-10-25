"""Finite, isolated phase diagnosis. No production queue, robot or registration."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import time
import zipfile

import mujoco
import numpy as np
from PIL import Image
import torch
from torch import nn

from .learning import write_json, sha256
from .environment import ExplorerEnv
from .recovery_contracts import StationaryExecutor, evidence_value
from .room_policy import issued_command
from .recovery_micro_learning import dataset, build, MicroActor, decode_relative_bc
from .recovery_retest import load
from .recovery_tiny import offline_actor_metrics, audit_observations
from .recovery_pilot import collect_teacher, PROTOCOL, current_grasp_probe

DIAG=Path('/Volumes/ExplorerSim/ExplorerSim/diagnostics')
OLD='20261020-v2-01'
ROOT=DIAG/'grasp-recovery-20261020-01'
COMBINED=DIAG/f'recovery-next-correction2-{OLD}/combined'
TEACHER=COMBINED/'teacher-original'
MODEL=DIAG/f'recovery-next-correction2-{OLD}/trained/bc'
SEED=20261020
SCALES=torch.tensor([1.]*6+[.02,.02,.06])


def rows(path):
    return [json.loads(line) for line in (Path(path)/'samples.jsonl').read_text().splitlines()]


def phases():
    return [row['phase'] for ep in sorted(COMBINED.glob('teacher-*'))
            for row in rows(ep) if row['training_target_valid']]


def selected(data,indices):
    return {key:value[indices] if isinstance(value,torch.Tensor) else value for key,value in data.items()}


def mse_physical_loss(prediction,target,scales=SCALES):
    return (((prediction-target)/scales.to(prediction.device))**2).mean()


def train_case(name,data,*,objective='physical_l1',sampling='uniform',phase_labels=None,
               updates=600,initial=None):
    if not 1<=updates<=600 or objective not in ('physical_l1','physical_mse'):
        raise ValueError('Finite, declared training budget required')
    folder=ROOT/name;folder.mkdir(exist_ok=False)
    output=folder/'bc';output.mkdir()
    torch.manual_seed(SEED);np.random.seed(SEED);random.seed(SEED);torch.set_num_threads(1)
    device=torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
    model=build('bc').to(device)
    stats=[data['state'].mean(0),data['state'].std(0).clamp_min(1.),
           data['action'].mean(0),data['action'].std(0).clamp_min(1.)]
    if initial is not None:
        saved=torch.load(initial/'checkpoint.pt',map_location='cpu',weights_only=False)
        if saved['protocol']['action_representation']!='relative_support':
            raise ValueError('This ablation preserves the relative-support action contract')
        model.load_state_dict(saved['model'],strict=True);stats=saved['stats']
    if not all(torch.isfinite(v).all() for v in stats):
        raise ValueError('Nonfinite statistics')
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.0001)
    protocol=dict(kind='bc',seed=SEED,maximum_updates=updates,wall_budget_s=240,
        batch_size=16,device=str(device),action_representation='relative_support',
        objective=objective,sampling=sampling,normalizers=[v.tolist() for v in stats],
        source_sha256=data['sha256'],initial_checkpoint_sha256=sha256(initial/'checkpoint.pt') if initial else None,
        student_inputs=['wrist_rgb','command_estimate9'],teacher_phase_input=False,
        execution_horizon=1,robot_io=False,old_learning_resume=False,
        maximum_closed_loop_trials=0,stop_on_nonfinite=True)
    write_json(output/'protocol.json',protocol)
    initial_weights={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
    torch.save(initial_weights,output/'initial-weights.pt')
    weights=None
    if sampling=='phase_balanced':
        if phase_labels is None or len(phase_labels)!=len(data['state']):
            raise ValueError('Sampler labels must match rows; they never enter model')
        counts=Counter(phase_labels)
        weights=torch.tensor([1/counts[label] for label in phase_labels])
    elif sampling!='uniform':
        raise ValueError('Unknown sampler')
    mean,std,amean,astd=[v.to(device) for v in stats]
    log=[];started=time.monotonic();model.train()
    for step in range(updates):
        if time.monotonic()-started>240:
            break
        index=(torch.randint(len(data['state']),(16,)) if weights is None else
               torch.multinomial(weights,16,replacement=True))
        anchor=data['state'][index].to(device)
        image=data['images'][index].to(device).float()/255
        target=data['action'][index].to(device)
        prediction=decode_relative_bc(model((anchor-mean)/std,image),anchor)
        loss=(((prediction-target).abs()/SCALES.to(device)).mean()
              if objective=='physical_l1' else mse_physical_loss(prediction,target))
        optimizer.zero_grad(set_to_none=True)
        if not torch.isfinite(loss):
            raise ValueError('Nonfinite loss')
        loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
        log.append(dict(update=step+1,loss=float(loss.detach().cpu()),
            batch_index_sha256=hashlib.sha256(index.numpy().tobytes()).hexdigest(),
            phase_counts=dict(Counter(phase_labels[i] for i in index.tolist())) if phase_labels else None))
    if device.type=='mps':
        torch.mps.synchronize()
    final={k:v.detach().cpu() for k,v in model.state_dict().items()}
    torch.save(dict(model=final,optimizer=optimizer.state_dict(),stats=stats,protocol=protocol,
        updates=len(log),torch_rng=torch.get_rng_state(),numpy_rng=np.random.get_state(),
        python_rng=random.getstate(),mps_rng=torch.mps.get_rng_state() if device.type=='mps' else None),output/'checkpoint.pt')
    write_json(output/'training.json',dict(updates=len(log),wall_time_s=time.monotonic()-started,
        changed_tensors=sum(not torch.equal(v,final[k]) for k,v in initial_weights.items()),
        checkpoint_sha256=sha256(output/'checkpoint.pt'),log=log))
    actor=MicroActor(model,'bc',stats,device,'relative_support')
    metrics,predictions=offline_actor_metrics(actor,data)
    np.savez_compressed(folder/'offline-predictions.npz',predictions=predictions,
        states=data['state'].numpy(),targets=data['action'].numpy())
    write_json(folder/'offline-metrics.json',metrics)
    return actor


def audit():
    ROOT.mkdir(exist_ok=True)
    archive=Path('/Users/vladromenko/Downloads/ROSMASTER_RECOVERY_NEXT_MAC_20261020_RESULTS.zip')
    with zipfile.ZipFile(archive) as source:
        manifest=json.loads(source.read('MAC_RESULT_MANIFEST.json'))
        export=json.loads(source.read('EXPORT_MANIFEST.json'))
        for entry in export['files']+manifest['extra_files']:
            h=hashlib.sha256()
            with source.open(entry['path']) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    h.update(block)
            if h.hexdigest()!=entry['sha256']:
                raise ValueError('Previous package SHA mismatch: '+entry['path'])
    write_json(ROOT/'previous-package-verified.json',dict(sha256=sha256(archive),
        commit=manifest['commit'],entries_verified=len(export['files'])+len(manifest['extra_files'])))
    data=dataset(COMBINED);all_phases=phases();counts=Counter(all_phases)
    report=audit_observations(data);report['phases']=dict(counts);report['rows_by_phase']={}
    for phase in counts:
        indices=[i for i,p in enumerate(all_phases) if p==phase]
        delta=(data['action']-data['state'])[indices,:6].numpy()
        report['rows_by_phase'][phase]=dict(samples=len(indices),zero_delta_fraction=(delta==0).mean(0).tolist(),
            mean_delta_deg=delta.mean(0).tolist(),median_delta_deg=np.median(delta,axis=0).tolist())
    write_json(ROOT/'dataset-audit.json',report)
    models={name:load(DIAG/f'recovery-next-{folder}-{OLD}'/suffix,'bc',raw=True)
        for name,folder,suffix in [('relative','tiny-relative','bc'),('corrected','correction','trained/bc'),
                                  ('corrected2','correction2','trained/bc')]}
    corrections={}
    for episode_name in ('teacher-correction','teacher-correction-new'):
        ep=COMBINED/episode_name
        row=next(row for row in rows(ep) if row['training_target_valid'])
        image=np.asarray(Image.open(ep/row['image']).convert('RGB'))
        state=np.asarray(row['observation_state'])
        corrections[episode_name]=dict(state=state.tolist(),target=row['applied_action'],
            models={name:dict(prediction=actor(state,image).tolist()) for name,actor in models.items()})
    write_json(ROOT/'correction-benefit.json',corrections)
    teacher_rows=rows(TEACHER);divergences={}
    for label,actor in models.items():
        name={'relative':'candidate-relative','corrected':'candidate-corrected','corrected2':'candidate-corrected2'}[label]
        ep=DIAG/f'recovery-next-{name}-{OLD}/bc-2026102001'
        recorded=rows(ep);snapshots=np.load(ep/'evaluator_states.npz')['integration_state']
        target=ROOT/('reconstructed-'+label);target.mkdir(exist_ok=False)
        env=ExplorerEnv(render=False);env.reset(seed=2026102001,options=dict(sock_position=PROTOCOL['positions_m'][0],bin_position=[.6,.3,0]))
        trace=[];max_prediction_error=0.;first=None
        try:
            for i,row in enumerate(recorded):
                image_path=ep/row['image']
                if sha256(image_path)!=row['image_sha256']:
                    raise ValueError('Stored RGB changed')
                rgb=np.asarray(Image.open(image_path).convert('RGB'));state=np.asarray(row['observation_state'])
                prediction=actor(state,rgb)
                max_prediction_error=max(max_prediction_error,float(np.max(abs(prediction-row['raw_action']))))
                mujoco.mj_setState(env.model,env.data,snapshots[i],mujoco.mjtState.mjSTATE_INTEGRATION)
                mujoco.mj_forward(env.model,env.data)
                probe=current_grasp_probe(env)
                closest=min(range(len(teacher_rows)),key=lambda j:np.linalg.norm(np.asarray(teacher_rows[j]['observation_state'])[:6]-state[:6]))
                same_time=teacher_rows[min(i,len(teacher_rows)-1)]
                error=float(np.max(abs(np.asarray(same_time['observation_state'])[:6]-state[:6])))
                if first is None and error>5:
                    first=dict(step=i,simulation_time=row['simulation_time'],max_command_error_deg=error,
                        reference_phase=same_time['phase'],note='First >5deg time-aligned divergence; not a phase oracle')
                guard=StationaryExecutor(env.validate_command,issued_command);guard.residual[:]=row['residual_before']
                plan=guard.prepare(state,prediction)
                if not np.allclose(plan.issued,row['applied_action'],atol=1e-6,rtol=0):
                    raise ValueError('Executor replay differs')
                physical=env.state()
                trace.append(dict(step=i,rgb_source=str(image_path),rgb_sha256=row['image_sha256'],
                    command_state=state.tolist(),normalized_state=actor.last_exposure['normalized_state'],
                    raw_action=prediction.tolist(),applied_action=row['applied_action'],
                    network_logits=actor.last_exposure['full_network_chunk'],
                    measured_joints_deg=physical['measured_sim'],jaw_center_m=physical['jaw_center_m'],
                    sock_center_m=physical['sock_center_m'],finger_pad_positions=env.data.geom_xpos[env.pad_ids].tolist(),
                    recomputed_contact_count=env.data.ncon,posthoc_probe=probe,
                    nearest_teacher_step=closest,nearest_teacher_phase=teacher_rows[closest]['phase'],
                    guard_passed=True,offline_evaluator_reconstruction=True,
                    contact_forces_recomputed=True,not_a_live_restore=True))
        finally:
            env.close()
        with (target/'trace.jsonl').open('w') as stream:
            for row in trace:
                stream.write(json.dumps(row)+'\n')
        divergences[label]=dict(first_time_aligned_divergence=first,
            original_predictions_max_reproduction_error=max_prediction_error,
            last=trace[-1],result=json.loads((ep/'result.json').read_text()))
    write_json(ROOT/'divergence.json',evidence_value(divergences))
    print(json.dumps(dict(phases=counts,reproduced={k:v['original_predictions_max_reproduction_error'] for k,v in divergences.items()})))


def critical():
    """A: one true pre-close input; B: real lower+close sequence, no rollout."""
    data=dataset(COMBINED,episode_name='teacher-original')
    records=rows(TEACHER);index=next(i for i,r in enumerate(records) if r['phase']=='close')
    one=selected(data,torch.tensor([index]*16))
    actor=train_case('A-one-close',one,updates=300,initial=MODEL)
    prediction=actor(one['state'][0].numpy(),one['images'][0].permute(1,2,0).numpy())
    plan=StationaryExecutor(ExplorerEnv.validate_command,issued_command).prepare(one['state'][0].numpy(),prediction)
    write_json(ROOT/'A-one-close/decision.json',dict(teacher_row=index,prediction=prediction.tolist(),
        target=one['action'][0].tolist(),issued=plan.issued.tolist(),
        positive_close_issued=bool(plan.issued[5]>one['state'][0,5]),teacher_assistance=False,
        single_input_not_a_grasp=True))
    if plan.issued[5]<=one['state'][0,5]:
        raise ValueError('Single real close input did not reproduce closure')
    indices=[i for i,r in enumerate(records) if r['phase'] in ('lower','close')]
    train_case('B-lower-close-l1',selected(data,torch.tensor(indices)),phase_labels=[records[i]['phase'] for i in indices])


def fit(name,objective,sampling):
    data=dataset(COMBINED)
    train_case(name,data,objective=objective,sampling=sampling,phase_labels=phases())


def evaluate(name,seed_index=0,independent=False):
    folder=ROOT/name
    if json.loads((folder/'offline-metrics.json').read_text())['raw_guard_refusals']:
        raise ValueError('Offline raw guard gate failed')
    if seed_index not in range(3 if independent else 4):
        raise ValueError('Bounded declared scenes only')
    reserved=json.loads((ROOT/'BUDGET_AND_PROTOCOL.json').read_text())['success_followup']
    seed=(reserved['seeds'][seed_index] if independent else
          (PROTOCOL['training_seeds']+PROTOCOL['development_seeds'])[seed_index])
    position=(reserved['positions'][seed_index] if independent else PROTOCOL['positions_m'][seed_index])
    if independent:
        first=json.loads((folder/'rollout-2026102001/result.json').read_text())
        if first['success'] is not True:
            raise ValueError('Independent followup only after successful training scene')
    output=folder/f'rollout-{seed}'
    write_json(folder/f'rollout-protocol-{seed}.json',dict(seed=seed,position=position,
        maximum_decisions=360,wall_budget_s=180,maximum_trials_this_invocation=1,
        source_checkpoint_sha256=sha256(folder/'bc/checkpoint.pt'),
        teacher_fallback=False,unchanged_evaluator=True,collision_checks=True,
        training_scene=not independent and seed_index<2,independent_scene=independent,
        note='Reserved before fits; no selection on these results' if independent else
             'Existing development starts were previously viewed, not independent holdout'))
    actor=load(folder/'bc','bc',raw=True)
    result=collect_teacher(output,seed,position,
        max_steps=400,wall_budget_s=180,policy=actor)
    print(json.dumps(dict(name=name,success=result['success'],reason=result.get('reason'),steps=result['steps'])))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['audit','critical','fit','evaluate'])
    parser.add_argument('--name');parser.add_argument('--objective',default='physical_l1')
    parser.add_argument('--sampling',default='uniform');parser.add_argument('--seed-index',type=int,default=0)
    parser.add_argument('--independent',action='store_true')
    args=parser.parse_args()
    {'audit':audit,'critical':critical,'fit':lambda:fit(args.name,args.objective,args.sampling),
     'evaluate':lambda:evaluate(args.name,args.seed_index,args.independent)}[args.command]()
