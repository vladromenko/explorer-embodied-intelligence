"""One finite live-prefix correction round, with student labels always masked."""
import argparse
import json
from pathlib import Path
import shutil

from .learning import write_json,sha256
from .recovery_pilot import collect_teacher,run as collect_baseline,PROTOCOL
from .recovery_retest import load
from .recovery_micro_learning import dataset
from .recovery_tiny import run as tiny


def run(source,reference,output,replay=None):
    source,reference,output=Path(source),Path(reference),Path(output)
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'protocol.json',dict(prefix_source=str(source),reference=str(reference),
        prefix_steps=360,maximum_collection_steps=960,wall_budget_s=180,
        maximum_correction_rounds=1,maximum_updates=600,training_seed=20261020,
        objective='physical_l1',representation='relative_support',
        student_prefix_targets=False,exact_live_prefix_required=True,
        teacher_truth_only_for_correction=True,physical_ready=False))
    # Fresh baseline under the same source revision; old records stay intact.
    baseline=output/'baseline'
    gate=collect_baseline(baseline)
    if gate['state']!='teacher_gate_passed':
        write_json(output/'state.json',dict(state='blocked_teacher'));return
    actor=load(source/'bc','bc',raw=True)
    correction=output/'teacher-correction'
    result=collect_teacher(correction,PROTOCOL['training_seeds'][0],PROTOCOL['positions_m'][0],
        max_steps=960,wall_budget_s=180,prefix_policy=actor,prefix_steps=360,reference_prefix=reference)
    if not result['success']:
        write_json(output/'state.json',dict(state='correction_failed',result=result));return
    composed=output/'combined';composed.mkdir()
    for name in ('protocol.json','source.json','state.json'):
        shutil.copy2(baseline/name,composed/name)
    original=baseline/'teacher-2026102001'
    if replay is None:
        shutil.copytree(original,composed/'teacher-original')
        shutil.copytree(correction,composed/'teacher-correction')
    else:
        for previous in sorted(Path(replay).glob('teacher-*')):
            shutil.copytree(previous,composed/previous.name)
        shutil.copytree(correction,composed/'teacher-correction-new')
    data=dataset(composed)
    from .recovery_tiny import audit_observations
    audit=audit_observations(data)
    write_json(output/'combined-data-audit.json',audit)
    if audit['exact_conflict_groups']:
        write_json(output/'state.json',dict(state='blocked_exact_observation_aliasing',training_updates=0));return
    from .recovery_micro_learning import validate_training_source
    validate_training_source(composed)
    write_json(output/'dataset.json',dict(expert_frames=len(data['state']),masked_student_frames=360,
        original_sha256=sha256(original/'samples.jsonl'),
        correction_sha256=sha256(correction/'samples.jsonl'),sources=data['sha256']))
    tiny(composed,'teacher-correction','bc',output/'correction-only-audit',False)
    # Combined run uses identical architecture/optimizer/budget. Dataset is the
    # sole intended change from the previous relative-support candidate.
    from .recovery_micro_learning import train
    from .recovery_tiny import offline_actor_metrics
    import numpy as np
    trained=output/'trained';trained.mkdir()
    model=train(data,'bc',trained/'bc',updates=600,wall_budget_s=240,
        objective='physical_l1',representation='relative_support')
    metrics,predictions=offline_actor_metrics(model,data)
    np.savez_compressed(trained/'offline-predictions.npz',predictions=predictions,
        states=data['state'].numpy(),targets=data['action'].numpy())
    write_json(trained/'offline-metrics.json',metrics)
    write_json(output/'state.json',dict(state='correction_trained',expert_frames=len(data['state']),
        masked_student_frames=360,raw_guard_refusals=metrics['raw_guard_refusals'],physical_ready=False,
        replay_dataset=str(replay) if replay else None))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--replay',type=Path)
    args=parser.parse_args();run(args.source,args.reference,args.output,args.replay)
