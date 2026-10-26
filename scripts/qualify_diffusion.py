#!/usr/bin/env python3
"""Real official compact Diffusion CPU updates in an OWN bounded user cgroup.

No camera/device/Store/robot/class/serial/control call. Source is explicitly
generated software data, never a qualified physical teaching episode or skill.
"""
import argparse
import dataclasses
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import sys
import time

from explorer_shared.learning.artifacts import sha,state_digest,write_json
from explorer_shared.learning.diffusion_backend import PROFILE_ID,IMAGE_KEY,compact_profile,make_compact_diffusion_policy


def cgroup_limits():
    relative=next(line.split('::',1)[1] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    root=Path('/sys/fs/cgroup');group=(root/relative.lstrip('/')).resolve()
    if not group.is_relative_to(root) or not group.name.startswith('explorer-diffusion-qual-'):raise ValueError('Run only in an own explorer-diffusion-qual-* transient user service')
    maximum=(group/'memory.max').read_text().strip();swap=(group/'memory.swap.max').read_text().strip();quota,period=(group/'cpu.max').read_text().split()
    if not maximum.isdigit() or not 128*1024**2<=int(maximum)<=4*1024**3 or swap!='0' or quota=='max' or int(quota)/int(period)>2:raise ValueError('Own MemoryMax<=4GiB, MemorySwapMax=0 and CPUQuota<=200% required')
    return group,dict(path=str(group),memory_max=int(maximum),memory_swap_max=0,cpu_quota_cores=int(quota)/int(period))


def runtime_identity():
    names=('lerobot','diffusers','torch','torchvision','numpy','huggingface-hub','safetensors','importlib_metadata','zipp','regex')
    packages={}
    for name in names:
        package=importlib.metadata.distribution(name);record=next((package.locate_file(path) for path in package.files or [] if str(path).endswith('.dist-info/RECORD')),None)
        packages[name]=dict(version=package.version,location=str(package.locate_file('')),record_sha256=sha(record) if record else None)
    sources={}
    for name in ('lerobot/policies/diffusion/configuration_diffusion.py','lerobot/policies/diffusion/modeling_diffusion.py','lerobot/optim/optimizers.py'):
        file=importlib.metadata.distribution('lerobot').locate_file(name);sources[name]=sha(file)
    package=importlib.metadata.distribution('diffusers')
    for name in ('diffusers/schedulers/scheduling_ddim.py','diffusers/schedulers/scheduling_ddpm.py'):
        sources[name]=sha(package.locate_file(name))
    return dict(executable=sys.executable,executable_resolved=str(Path(sys.executable).resolve()),executable_sha256=sha(Path(sys.executable).resolve()),
        prefix=sys.prefix,python_version=sys.version,packages=packages,official_sources_sha256=sources,
        backend_source_sha256=sha(Path(__file__).parents[1]/'shared/explorer_shared/learning/diffusion_backend.py'),qualification_script_sha256=sha(__file__))


def create_source(folder):
    import numpy as np
    from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS
    folder.mkdir(parents=True,exist_ok=False);episodes=[]
    low=np.asarray([0,0,0,0,0,30,-.2,-.2,-.6],np.float32);high=np.asarray([180,180,180,180,270,170,.2,.2,.6],np.float32)
    for index,split in enumerate(('train','development')):
        rng=np.random.default_rng(310+index);rgb=rng.integers(0,256,(16,64,64,3),dtype=np.uint8)
        state=rng.uniform(-.8,.8,(16,9)).astype(np.float32);action=rng.uniform(-.8,.8,(16,9)).astype(np.float32)
        state[:,6:]=0;action[::2,6:]=0 # Genuine retained neutral base channels in a software fixture.
        clocks=np.arange(16,dtype=np.int64)*100_000_000+1_000_000_000
        path=folder/(split+'.npz');np.savez_compressed(path,rgb=rgb,state=state,action=action,state_native=(state+1)*.5*(high-low)+low,
            action_native=(action+1)*.5*(high-low)+low,frame_monotonic_ns=clocks,state_monotonic_ns=clocks+1,action_monotonic_ns=clocks+2,valid_mask=np.ones((16,9),bool))
        episodes.append(dict(original_episode_id='software-'+split,split=split,file=path.name,sha256=sha(path),frames=16,scene='software-scene-'+split,session='software-session-'+split))
    document=dict(schema='explorer_policy_qualification_fixture_v1',source_kind='SIMULATION',source_quality='software_only_not_admitted_physical_demonstration',
        action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),normalization=dict(low=low.tolist(),high=high.tolist()),episodes=episodes)
    write_json(folder/'manifest.json',document);return document


def row(data,index):
    import numpy as np
    import torch
    observations=np.asarray([index-1,index]);actions=np.arange(index-1,index+7)
    if observations.min()<0 or actions.max()>=len(data['rgb']):raise ValueError('Use only actual unpadded two-observation/source-action qualification windows')
    return {IMAGE_KEY:torch.from_numpy(data['rgb'][observations].copy()).float().permute(0,3,1,2)/255,
        'observation.state':torch.from_numpy(data['state'][observations].copy()),'action':torch.from_numpy(data['action'][actions].copy()),
        'action_is_pad':torch.zeros(8,dtype=torch.bool)}


def qualify(folder):
    group,limits=cgroup_limits();folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=False)
    write_json(folder/'preflight.json',dict(limits=limits,runtime=runtime_identity(),hardware_io=False))
    import numpy as np
    import torch
    from torch.utils.data import default_collate
    torch.set_num_threads(2)
    manifest=create_source(folder/'originals');sources={item['split']:folder/'originals'/item['file'] for item in manifest['episodes']}
    before={str(path):sha(path) for path in (folder/'originals').iterdir() if path.is_file()}
    train=dict(np.load(sources['train'],allow_pickle=False));development=dict(np.load(sources['development'],allow_pickle=False))
    train_hashes={__import__('hashlib').sha256(frame.tobytes()).hexdigest() for frame in train['rgb']}
    assert not train_hashes&{__import__('hashlib').sha256(frame.tobytes()).hexdigest() for frame in development['rgb']}
    policy=make_compact_diffusion_policy(dict(seed=41,image_size=64,device='cpu'),state_dim=9,action_dim=9);initial=state_digest(policy)
    optimizer_config=policy.config.get_optimizer_preset();optimizer=optimizer_config.build(policy.get_optim_params())
    losses=[];started=time.monotonic()
    for step in range(8):
        batch=default_collate([row(train,1+step%8),row(train,1+(step+3)%8)])
        policy.train();optimizer.zero_grad(set_to_none=True);loss,terms=policy(batch)
        if not torch.isfinite(loss):raise RuntimeError('Nonfinite real official Diffusion loss')
        loss.backward();norm=torch.nn.utils.clip_grad_norm_(policy.parameters(),optimizer_config.grad_clip_norm,error_if_nonfinite=True);optimizer.step()
        losses.append(dict(step=step+1,loss=float(loss.detach()),gradient_norm=float(norm.detach()),official_output_terms=terms))
    training_elapsed=time.monotonic()-started
    if initial==state_digest(policy):raise RuntimeError('No actual weight change')
    vision_gradient_norm=float(policy.diffusion.rgb_encoder[0].backbone[0].weight.grad.norm())
    if not vision_gradient_norm>0:raise RuntimeError('RGB encoder has no actual gradient')
    policy.eval();policy.reset();sample=default_collate([row(development,1)]);inputs={key:sample[key] for key in (IMAGE_KEY,'observation.state')}
    noise=torch.randn((1,8,9),generator=torch.Generator().manual_seed(98))
    with torch.inference_mode():
        expected=policy.predict_action_chunk(inputs,noise=noise.clone())
        different_RGB=policy.predict_action_chunk(dict(inputs,**{IMAGE_KEY:torch.zeros_like(inputs[IMAGE_KEY])}),noise=noise.clone())
    RGB_effect=float((expected-different_RGB).abs().max())
    if not RGB_effect>1e-8:raise RuntimeError('RGB does not affect the actual generated action')
    checkpoint=folder/'policy';policy.save_pretrained(checkpoint,push_to_hub=False);torch.save(optimizer.state_dict(),folder/'optimizer.pt')
    loaded=type(policy).from_pretrained(checkpoint,local_files_only=True,strict=True).to('cpu').eval();loaded.reset()
    with torch.inference_mode():actual=loaded.predict_action_chunk(inputs,noise=noise.clone())
    reload_error=float((expected-actual).abs().max())
    if reload_error>1e-6:raise RuntimeError('Official Diffusion save/reload mismatch with fixed DDIM noise')
    latencies=[];predictions=[]
    for index in range(1,9):
        sample=default_collate([row(development,index)]);inputs={key:sample[key] for key in (IMAGE_KEY,'observation.state')}
        loaded.reset();started=time.monotonic()
        with torch.inference_mode():prediction=loaded.predict_action_chunk(inputs,noise=noise.clone())
        latencies.append(time.monotonic()-started);predictions.append(float((prediction-sample['action'][:,1:5]).abs().mean()))
    assert all(sha(path)==expected for path,expected in before.items())
    checkpoint_hashes={path.name:sha(path) for path in checkpoint.iterdir() if path.is_file()}
    result=dict(schema='explorer_compact_diffusion_qualification_v1',profile=compact_profile(),runtime=runtime_identity(),limits=limits,
        source_kind='SIMULATION',source_manifest_sha256=sha(folder/'originals/manifest.json'),source_bytes_preserved=True,train_original_episode_ids=['software-train'],development_original_episode_ids=['software-development'],
        actual_model_class=type(policy).__module__+'.'+type(policy).__qualname__,parameter_count=sum(value.numel() for value in policy.parameters()),
        optimizer_class=type(optimizer).__module__+'.'+type(optimizer).__qualname__,optimizer_preset=dataclasses.asdict(optimizer_config),
        actual_optimizer_updates=8,weights_changed=True,losses=losses,training_elapsed_s=training_elapsed,checkpoint_sha256=checkpoint_hashes,
        RGB_encoder_first_conv_gradient_norm=vision_gradient_norm,changed_RGB_fixed_state_and_noise_action_max_difference=RGB_effect,
        save_reload_passed=True,reload_max_abs_error=reload_error,heldout_original_windows=8,heldout_fixed_noise_action_chunk_MAE_normalized=float(np.mean(predictions)),
        independent_episode_split=True,history_observation_steps=2,action_horizon_origin='preceding_observation_index; currently_executable_actions_at_offsets1..4',
        inference_s=dict(median=float(np.median(latencies)),p95=float(np.percentile(latencies,95)),maximum=max(latencies),samples=latencies),
        process_peak_RSS_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,cgroup_peak_memory_bytes=int((group/'memory.peak').read_text()),
        cgroup_memory_events=(group/'memory.events').read_text(),hardware_io=False,simulator_executed=False,rollout_executed=False,physical_success=False,
        production_training_admission=False,optimizer_resume_qualified=False,registry_general_backend_still_UNQUALIFIED=True,
        limitations=['Small64px/random-weight compact model and8updates are software/resource qualification only','No real object-task success benchmark','DDIM4 fixed-noise inference; not default100step DDPM latency','No source-matched ACT/Diffusion task superiority claim','No production dataset adapter/resume/device/hardware qualification'])
    write_json(folder/'result.json',result);print(json.dumps({key:result[key] for key in ('actual_optimizer_updates','parameter_count','optimizer_class','reload_max_abs_error','process_peak_RSS_bytes','cgroup_peak_memory_bytes','inference_s')}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);arguments=parser.parse_args();qualify(arguments.output)
