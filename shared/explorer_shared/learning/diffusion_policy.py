"""method_id=DIFFUSION_LEROBOT_0_6_1_COMPACT; P002 https://arxiv.org/abs/2303.04137.

Hash-bound Diffusion saved-policy loader and native-coordinate offline view.

No device/camera/network/robot executor. Caller admits actual source observations;
predictions are arrays, never dispatched commands or physical-success evidence.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch

from .artifacts import sha,write_json
from .diffusion_backend import IMAGE_KEY,PROFILE_ID,compact_profile


def save_reload_diffusion(policy,optimizer,folder,preprocessing,check_batch,*,noise_seed=41):
    folder=Path(folder);checkpoint=folder/'student_diffusion';policy.eval();policy.reset()
    noise=torch.randn((len(check_batch['observation.state']),policy.config.horizon,policy.config.action_feature.shape[0]),generator=torch.Generator().manual_seed(noise_seed))
    with torch.inference_mode():expected=policy.predict_action_chunk(check_batch,noise=noise.clone())
    policy.save_pretrained(checkpoint,push_to_hub=False);torch.save(optimizer.state_dict(),folder/'diffusion_optimizer.pt')
    write_json(checkpoint/'explorer_preprocessing.json',preprocessing)
    reloaded=type(policy).from_pretrained(checkpoint,local_files_only=True,strict=True).to('cpu').eval();reloaded.reset()
    with torch.inference_mode():actual=reloaded.predict_action_chunk(check_batch,noise=noise.clone())
    error=float((actual-expected).abs().max())
    if error>1e-6:raise RuntimeError('Official Diffusion save/reload prediction changed at fixed DDIM noise')
    return reloaded,checkpoint,error


class NativeDiffusionPolicy:
    """Pure feature→native-command chunk adapter; freshness/admission is external."""
    def __init__(self,checkpoint,expected_sha256,*,expected_contract_id,expected_names,expected_units,expected_low,expected_high):
        checkpoint=Path(checkpoint).resolve()
        if not isinstance(expected_sha256,dict) or not {'config.json','model.safetensors','explorer_preprocessing.json'}<=set(expected_sha256):raise ValueError('Server-bound complete checkpoint hashes required')
        for name,digest in expected_sha256.items():
            path=(checkpoint/name).resolve()
            if Path(name).name!=name or not path.is_relative_to(checkpoint) or not path.is_file() or sha(path)!=digest:raise ValueError('Diffusion checkpoint checksum/path changed')
        preprocessing=json.loads((checkpoint/'explorer_preprocessing.json').read_text())
        required=dict(schema='explorer_native_diffusion_preprocessing_v1',profile_id=PROFILE_ID,action_contract_id=expected_contract_id,
            action_names=list(expected_names),action_units=list(expected_units),state_low=list(expected_low),state_high=list(expected_high),
            RGB_transform='uint8/255_NCHW_bilinear_resize_once',state_basis='measured_state',history_steps=2,horizon=8,
            action_chunk_start_offset=1,chunk_prediction_steps=4,image_size=64,physical_execution_authorized=False)
        if any(preprocessing.get(key)!=value for key,value in required.items()):raise ValueError('Native Diffusion source coordinate/preprocessing contract differs')
        from lerobot.policies.diffusion.configuration_diffusion import DiffusionConfig
        from lerobot.policies.diffusion.modeling_diffusion import DiffusionPolicy
        config=DiffusionConfig.from_pretrained(checkpoint,local_files_only=True)
        for key,value in compact_profile()['configuration'].items():
            actual=getattr(config,key)
            if isinstance(value,tuple):actual=tuple(actual)
            if actual!=value:raise ValueError('Unqualified Diffusion model profile: '+key)
        if config.input_features['observation.state'].shape!=(len(expected_names),) or config.action_feature.shape!=(len(expected_names),) or config.input_features[IMAGE_KEY].shape!=(3,64,64):raise ValueError('Native Diffusion feature dimensions differ')
        if config.device!='cpu':raise ValueError('Saved Diffusion runtime profile is outside this CPU qualification')
        config.device='cpu'
        self.policy=DiffusionPolicy.from_pretrained(checkpoint,config=config,local_files_only=True,strict=True).to('cpu').eval();self.policy.reset()
        self.low=np.asarray(expected_low,np.float32);self.high=np.asarray(expected_high,np.float32)
        self.preprocessing=preprocessing;self.checkpoint_sha256=expected_sha256;self.physical_io=False

    def predict_history(self,rgb_history,native_measured_history,*,noise_seed=41,valid_mask=None):
        rgb=np.asarray(rgb_history);state=np.asarray(native_measured_history,np.float32)
        if rgb.dtype!=np.uint8 or rgb.ndim!=4 or rgb.shape[0]!=2 or rgb.shape[-1]!=3 or state.shape!=(2,len(self.low)) or not np.isfinite(state).all():raise ValueError('Two actual uint8 RGB frames and finite measured native states required')
        if valid_mask is not None and (np.asarray(valid_mask).dtype!=np.bool_ or np.asarray(valid_mask).shape!=state.shape or not np.asarray(valid_mask).all()):raise ValueError('Unavailable measured channels cannot be replaced with zeros')
        if np.any(state<self.low) or np.any(state>self.high):raise ValueError('Measured state outside immutable source native coordinate ranges')
        images=np.stack([np.asarray(Image.fromarray(frame).resize((64,64),Image.Resampling.BILINEAR)) for frame in rgb])
        inputs={IMAGE_KEY:torch.from_numpy(images.copy()).float().permute(0,3,1,2).unsqueeze(0)/255,
            'observation.state':torch.from_numpy(2*(state-self.low)/(self.high-self.low)-1).unsqueeze(0)}
        noise=torch.randn((1,8,len(self.low)),generator=torch.Generator().manual_seed(noise_seed));self.policy.reset()
        with torch.inference_mode():normalized=self.policy.predict_action_chunk(inputs,noise=noise).cpu().numpy()[0]
        if normalized.shape!=(4,len(self.low)) or not np.isfinite(normalized).all():raise ValueError('Nonfinite/incorrect Diffusion prediction')
        return ((np.clip(normalized,-1,1)+1)*.5*(self.high-self.low)+self.low).astype(np.float32)


def offline_evaluation(policy,windows,names,units,*,seed=41):
    from torch.utils.data import DataLoader
    total=np.zeros(len(names),np.float64);valid_actions=0;batches=0
    policy.eval();policy.reset();start=policy.config.n_obs_steps-1;steps=policy.config.n_action_steps
    generator=torch.Generator().manual_seed(seed)
    for batch in DataLoader(windows,batch_size=4,shuffle=False,num_workers=0):
        inputs={key:batch[key] for key in (IMAGE_KEY,'observation.state')}
        noise=torch.randn((len(batch['action']),policy.config.horizon,len(names)),generator=generator)
        policy.reset()
        with torch.inference_mode():predicted=policy.predict_action_chunk(inputs,noise=noise).cpu().numpy()
        target=batch['action'][:,start:start+steps].numpy();mask=~batch['action_is_pad'][:,start:start+steps].numpy()
        total+=(np.abs(predicted-target)*mask[...,None]).sum(axis=(0,1));valid_actions+=int(mask.sum());batches+=1
    mae=total/max(valid_actions,1)
    return dict(domain='independent_development_offline_diffusion_chunks',available=bool(valid_actions),independent_episode_split=True,
        valid_chunk_actions=valid_actions,batches=batches,history_steps=policy.config.n_obs_steps,action_start_offset=start,
        mean_absolute_error_normalized=float(mae.mean()),action_mae={name:dict(value=float(mae[index]*(windows.high[index]-windows.low[index])/2),unit=units[index]) for index,name in enumerate(names)},
        fixed_noise_seed=seed,sealed_test_used=False,rollout_success_claimed=False,physical_run_ids=[],physical_io=False)
