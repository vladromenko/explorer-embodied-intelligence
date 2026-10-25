"""Explicit, finite one-episode diagnostic. Never runs robot I/O or a rollout.

Default is data audit only. --train authorizes at most 600 updates / 240 seconds
for ONE chosen method; it cannot resume any previous run.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from .learning import write_json
from .recovery_micro_learning import dataset, train, validate_training_source
from .recovery_contracts import StationaryExecutor, evidence_value
from .environment import ExplorerEnv
from .room_policy import issued_command


def audit_observations(data):
    state = data['state'].numpy()
    action = data['action'].numpy()
    image = data['images'].numpy()
    groups = defaultdict(list)
    for i in range(len(state)):
        digest = hashlib.sha256(state[i].tobytes() + image[i].tobytes()).hexdigest()
        groups[digest].append(i)
    conflicts = []
    for indices in groups.values():
        if len(indices) > 1:
            span = np.ptp(action[indices], axis=0)
            if np.any(span > 1e-6):
                conflicts.append(dict(rows=indices, target_span=span.tolist()))
    delta = action[:, :6] - state[:, :6]
    return dict(samples=len(state), exact_observation_conflicts=conflicts,
        exact_conflict_groups=len(conflicts), action_std_deg=data['action'].std(0)[:6].tolist(),
        all_arm_hold_rows=int(np.all(np.abs(delta) < 1e-6, axis=1).sum()),
        integer_delta_targets=bool(np.allclose(delta,np.round(delta),atol=1e-6,rtol=0)),
        maximum_applied_step_deg=np.max(np.abs(delta),axis=0).tolist(),
        state_only_or_near_image_similarity_not_proof_of_aliasing=True)


def offline_actor_metrics(actor, data):
    predictions=[]; refusals=[]
    for i in range(len(data['state'])):
        state=data['state'][i].numpy()
        rgb=data['images'][i].permute(1,2,0).numpy()
        raw=actor(state,rgb)
        predictions.append(raw)
        # Independent recorded-state query; no simulated dynamics are executed.
        guard=StationaryExecutor(ExplorerEnv.validate_command,issued_command)
        try:
            guard.prepare(state,raw)
        except ValueError as error:
            try:
                ExplorerEnv.validate_command(raw)
                category='raw_step' if np.any(abs(raw[:6]-state[:6])>20.) else 'executor_contract'
            except ValueError:
                category='raw_range_or_shape'
            refusals.append(dict(row=i,reason=str(error),category=category,
                raw=raw.tolist(),state=state.tolist(),raw_delta_deg=(raw[:6]-state[:6]).tolist()))
    predictions=np.asarray(predictions)
    error=np.abs(predictions-data['action'].numpy())
    value=dict(samples=len(error),raw_guard_refusals=len(refusals),refusals=refusals,
        joint_mae_deg=error[:,:6].mean(0).tolist(),joint_max_deg=error[:,:6].max(0).tolist(),
        joint_p95_deg=np.quantile(error[:,:6],.95,axis=0).tolist(),
        first_action=dict(state=data['state'][0].tolist(),target=data['action'][0].tolist(),
            prediction=predictions[0].tolist(),absolute_error=error[0].tolist()),
        offline_only=True,closed_loop_grasp_evaluated=False,physical_ready=False)
    return value,predictions


def run(teacher,episode,kind,output,execute_training=False,updates=600,budget_s=240,objective='normalized_l1',representation='absolute_normalized'):
    if kind not in ('bc','act') or not (1 <= updates <= 600) or not (0 < budget_s <= 240):
        raise ValueError('Finite diagnostic budget exceeded')
    if not episode or Path(episode).name != episode:
        raise ValueError('Provide one teacher episode basename, not a path')
    if execute_training:
        validate_training_source(teacher)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    data=dataset(teacher,episode_name=episode)
    report=audit_observations(data)
    write_json(output/'data-audit.json',report)
    write_json(output/'protocol.json',dict(teacher=str(teacher),episode=episode,kind=kind,
        maximum_updates=updates,wall_budget_s=budget_s,training_requested=execute_training,
        source_sha256=data['sha256'],legacy_teacher_allowed_for_offline_diagnosis_only=True,
        objective=objective,
        action_representation=representation,
        robot_io=False,old_training_resume=False,physical_rollouts=0))
    if report['exact_conflict_groups']:
        write_json(output/'state.json',dict(state='blocked_exact_observation_aliasing',training_updates=0))
        return
    if execute_training:
        actor=train(data,kind,output/kind,updates=updates,wall_budget_s=budget_s,objective=objective,representation=representation)
        metrics,predictions=offline_actor_metrics(actor,data)
        np.savez_compressed(output/'offline-predictions.npz',predictions=predictions,
            states=data['state'].numpy(),targets=data['action'].numpy())
        write_json(output/'offline-metrics.json',evidence_value(metrics))
        write_json(output/'state.json',dict(state='offline_diagnostic_complete',
            candidate_raw_valid=metrics['raw_guard_refusals']==0,
            local_grasp_success=None,physical_ready=False,long_training_allowed=False))
    else:
        write_json(output/'state.json',dict(state='data_audit_only',training_updates=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--teacher',type=Path,required=True)
    parser.add_argument('--episode',required=True)
    parser.add_argument('--kind',choices=['bc','act'],default='bc')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--updates',type=int,default=600)
    parser.add_argument('--budget-s',type=float,default=240)
    parser.add_argument('--train',action='store_true')
    parser.add_argument('--objective',choices=['normalized_l1','physical_l1'],default='normalized_l1')
    parser.add_argument('--representation',choices=['absolute_normalized','relative_support'],default='absolute_normalized')
    args=parser.parse_args()
    run(args.teacher,args.episode,args.kind,args.output,args.train,args.updates,args.budget_s,args.objective,args.representation)
