"""Finite physical check only after the chosen model's complete offline gate."""
import argparse
import json
from pathlib import Path

from .learning import write_json,sha256
from .recovery_pilot import collect_teacher,PROTOCOL
from .recovery_retest import load


def run(source,output):
    source=Path(source);output=Path(output)
    metrics=json.loads((source/'offline-metrics.json').read_text())
    if metrics['raw_guard_refusals']!=0:
        raise ValueError('All nine raw channels must pass before physical candidate test')
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'protocol.json',dict(source=str(source),
        offline_metrics_sha256=sha256(source/'offline-metrics.json'),
        checkpoint_sha256=sha256(source/'bc/checkpoint.pt'),
        hypothesis='relative one-step support with unchanged raw guards; closed-loop behavior unproven',
        maximum_trials=4,stop_after_first_failure=True,maximum_decisions=360,
        physical_ready=False,holdout_qualification=False,old_learning_resume=False))
    actor=load(source/'bc','bc',raw=True)
    trials=[]
    for index,seed in enumerate(PROTOCOL['training_seeds']+PROTOCOL['development_seeds']):
        value=collect_teacher(output/f'bc-{seed}',seed,PROTOCOL['positions_m'][index],
            max_steps=400,wall_budget_s=180,policy=actor)
        trials.append(dict(seed=seed,success=value['success'],reason=value.get('reason'),
            split='training' if index<2 else 'development',steps=value['steps']))
        write_json(output/'state.json',dict(state='evaluating',trials=trials))
        if not value['success']:
            break
    result=dict(state='local_candidate' if len(trials)==4 and all(r['success'] for r in trials)
        else 'needs_targeted_repair',trials=trials,training_updates=0,teacher_fallback=False,
        physical_ready=False,room_ready=False)
    write_json(output/'state.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(run(args.source,args.output),ensure_ascii=False))
