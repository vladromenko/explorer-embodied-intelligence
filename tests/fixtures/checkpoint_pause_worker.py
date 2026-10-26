"""ENGINEERING_GLUE: isolated stochastic software optimizer process, no hardware."""
import argparse
import json
from pathlib import Path
import sys
import time

import torch

ROOT=Path(__file__).parents[2]
sys.path.insert(0,str(ROOT/'tests'));sys.path.insert(0,str(ROOT/'01-autonomous-learning/tests'))
from explorer_shared.learning.checkpoint_control import atomic_json


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--backend',choices=['act','diffusion'],required=True)
    parser.add_argument('--folder',required=True);parser.add_argument('--source',required=True);parser.add_argument('--wait-step',type=int,default=0);parser.add_argument('--initial-seed',type=int,default=71)
    args=parser.parse_args();folder=Path(args.folder);folder.mkdir(parents=True,exist_ok=True)
    if args.backend=='act':
        from test_act_resume import StochasticPolicy as Policy,StochasticRows as Rows,seed,logger
        from explorer_shared.learning.act_backend import optimize_act as optimize
        settings=dict(device='cpu',seed=23,batch_size=3,act_steps=8)
    else:
        from test_diffusion_training_primitives import Policy,Rows,seed,logger
        from explorer_shared.learning.diffusion_optimizer import optimize_diffusion as optimize
        settings=dict(device='cpu',seed=23,batch_size=3,optimizer_steps=8)
    seed(args.initial_seed);torch.set_num_threads(2);policy=Policy();rows=Rows(Path(args.source))
    def update(*unused):
        logger();name='act_resume' if args.backend=='act' else 'diffusion_resume'
        step=json.loads((folder/name/'latest.json').read_text())['completed_steps']
        if step==args.wait_step:
            atomic_json(folder/'at-step.json',dict(step=step));began=time.monotonic()
            while not (folder/'release-step').is_file():
                if time.monotonic()-began>20:raise TimeoutError('Test coordinator did not release software fixture')
                time.sleep(.01)
    optimizer,losses,_=optimize(policy,rows,settings,folder,update,checkpoint_interval=4)
    torch.save(dict(model=policy.state_dict(),optimizer=optimizer.state_dict(),losses=losses,resume=optimizer.explorer_resume_info),folder/'final.pt')


if __name__=='__main__':main()
