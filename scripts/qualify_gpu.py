"""ENGINEERING_GLUE: narrowly qualify the actual compact ACT path on Orin."""
import argparse
import copy
import importlib.metadata
import json
from pathlib import Path
import sys
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(Path(args.root)/'01-autonomous-learning/src'))
    import torch
    import numpy as np
    from explorer_sim.pilot import parameters,state_digest
    from explorer_sim.student import IMAGE_KEY,make_act_policy,prepare_rgb,save_reload_act,preprocessing_config,framework_info
    output=Path(args.output)
    output.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(4)
    torch.set_float32_matmul_precision('highest')
    torch.backends.cuda.matmul.allow_tf32=False
    torch.backends.cudnn.allow_tf32=False
    settings=parameters(dict(seed=27,device='cuda',image_size=64,chunk_size=4,act_steps=2,batch_size=2))
    policy=make_act_policy(settings,6,6)
    before=state_digest(policy)
    rng=np.random.default_rng(27)
    inputs={IMAGE_KEY:prepare_rgb(rng.integers(0,256,(2,64,64,3),dtype=np.uint8)).cuda(),
            'observation.state':torch.zeros((2,6),device='cuda')}
    batch=dict(inputs,action=torch.zeros((2,4,6),device='cuda'),action_is_pad=torch.zeros((2,4),device='cuda',dtype=torch.bool))
    optimizer=torch.optim.AdamW(policy.get_optim_params(),lr=3e-4)
    losses=[]
    started=time.monotonic()
    for step in range(2):
        policy.train()
        optimizer.zero_grad(set_to_none=True)
        loss,_=policy(batch)
        if not bool(torch.isfinite(loss)):
            raise RuntimeError('Nonfinite ACT GPU loss')
        loss.backward()
        torch.nn.utils.clip_grad_norm_(policy.parameters(),1,error_if_nonfinite=True)
        optimizer.step()
        losses.append(float(loss.detach().cpu()))
    policy.eval()
    low=np.asarray([-100]*5+[0],dtype=np.float32)
    high=np.asarray([100]*6,dtype=np.float32)
    reloaded,checkpoint,reload_error=save_reload_act(policy,optimizer,output,preprocessing_config(low,high,range(6)),inputs,'cuda')
    cpu=copy.deepcopy(reloaded).cpu().eval()
    with torch.inference_mode():
        gpu_prediction=reloaded.predict_action_chunk(inputs)
        cpu_prediction=cpu.predict_action_chunk({key:value.cpu() for key,value in inputs.items()})
    parity=float((gpu_prediction.cpu()-cpu_prediction).abs().max())
    finite=bool(torch.isfinite(gpu_prediction).all()) and bool(torch.isfinite(cpu_prediction).all())
    passed=finite and parity<1e-4 and before!=state_digest(policy)
    result=dict(passed=passed,scope='Compact six-axis official ACT only; synthetic tensors, no physical skill validation',
        device=torch.cuda.get_device_name(),compute_capability=list(torch.cuda.get_device_capability()),
        compiled_architectures=torch.cuda.get_arch_list(),tf32_enabled=False,lerobot=importlib.metadata.version('lerobot'),
        optimizer_steps=2,losses=losses,weights_changed=before!=state_digest(policy),finite=finite,
        save_reload_max_abs_error=reload_error,cpu_gpu_max_abs_error=parity,elapsed_s=time.monotonic()-started,
        checkpoint=str(checkpoint),**framework_info())
    (output/'qualification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if not passed:
        raise SystemExit(1)


if __name__=='__main__':
    main()
