"""One controlled retest: same micro weights with the room's locked-base port."""
import argparse
import json
from pathlib import Path

import torch
import numpy as np

from .learning import sha256, write_json
from .recovery_micro_learning import MicroActor, build
from .recovery_pilot import PROTOCOL, collect_teacher
from .room_policy import issued_command


class LockedBaseActor:
    """Explicit sequential arm/base mode, with RAW prediction validated first."""
    trained=True
    returns_issued_commands=True

    def __init__(self, actor):
        self.actor=actor;self.last_exposure={}
        self.residual=np.zeros(6)

    def reset(self):
        self.actor.reset()
        self.residual[:]=0.

    def __call__(self,state,image):
        prediction=self.actor(state,image)
        self.last_exposure=dict(self.actor.last_exposure,network_prediction9=prediction.tolist(),
            controller='room_policy.issued_command',base_mode='locked_during_local_manipulation')
        try:
            command=issued_command(state,prediction,self.residual)
        except ValueError as error:
            self.last_exposure['raw_guard_refusal']=str(error)
            raise
        self.last_exposure['issued_command9']=command.tolist()
        return command


def load(folder,kind,*,raw=False):
    folder=Path(folder)
    manifest=json.loads((folder/'training.json').read_text())
    if sha256(folder/'checkpoint.pt')!=manifest['checkpoint_sha256']:
        raise ValueError('Micro checkpoint changed')
    checkpoint=torch.load(folder/'checkpoint.pt',map_location='cpu',weights_only=False)
    model=build(kind)
    model.load_state_dict(checkpoint['model'],strict=True)
    actor=MicroActor(model,kind,checkpoint['stats'],torch.device('cpu'),
        checkpoint['protocol'].get('action_representation','absolute_normalized'))
    return actor if raw else LockedBaseActor(actor)


def run(source,output):
    source,output=Path(source),Path(output)
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'protocol.json',dict(hypothesis='shared v2 executor and independent hold; same historical weights, zero training',
        weights_changed=False,normalizers_changed=False,physics_changed=False,evaluator_changed=True,contract_schema=PROTOCOL['schema'],
        source=str(source),controller='same room_policy.issued_command for both arms',
        persistent_rounding_residual=True,
        raw_guard_before_mode_mask=True,teacher_and_hold_base_commands_already_zero=True,
        source_contract_sha256=sha256(Path(__file__).with_name('room_policy.py'))))
    methods={}
    torch.set_num_threads(1)
    for kind in ('teacher','hold','bc','act'):
        actor=(None if kind=='teacher' else (lambda state,rgb:state.copy())
               if kind=='hold' else load(source/kind,kind,raw=True))
        rows=[]
        for index,seed in enumerate(PROTOCOL['training_seeds']+PROTOCOL['development_seeds']):
            result=collect_teacher(output/f'{kind}-{seed}',seed,PROTOCOL['positions_m'][index],
                max_steps=400,wall_budget_s=180,policy=actor)
            rows.append(dict(seed=seed,success=result['success'],reason=result.get('reason'),
                path=str(output/f'{kind}-{seed}'),split='training' if index<2 else 'development'))
        methods[kind]=rows
        write_json(output/'state.json',dict(state='retesting',methods=methods))
    value=dict(state='local_candidate' if (all(r['success'] for r in methods['teacher']) and
        any(all(r['success'] for r in methods[kind]) for kind in ('bc','act')))
        else 'needs_algorithm_repair',methods=methods,training_updates=0,
        repeat_same_round=False,physical_ready=False,room_ready=False,
        next_action='inspect first refusal and quantify closed-loop error on saved expert inputs before new collection')
    write_json(output/'state.json',value)
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.source,args.output),ensure_ascii=False))
