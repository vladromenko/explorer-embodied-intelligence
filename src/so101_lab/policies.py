"""Explicit policy factory; ACT never tries to download a vision backbone."""
def components(kind):
    if kind=='smolvla':
        from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
        from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
        from lerobot.policies.smolvla.processor_smolvla import make_smolvla_pre_post_processors
        return SmolVLAConfig,SmolVLAPolicy,make_smolvla_pre_post_processors
    if kind=='act':
        from lerobot.policies.act.configuration_act import ACTConfig
        from lerobot.policies.act.modeling_act import ACTPolicy
        from lerobot.policies.act.processor_act import make_act_pre_post_processors
        return ACTConfig,ACTPolicy,make_act_pre_post_processors
    raise ValueError('Неизвестная политика: '+str(kind))

def act_config(contract,options,tiny=False):
    from lerobot.configs import FeatureType,PolicyFeature,NormalizationMode
    cls,_,_=components('act');size=options['image_size']
    backbone_weights='ResNet18_Weights.IMAGENET1K_V1' if options.get('act_pretrained_backbone',True) and not tiny else None
    cfg=cls(device=options['device'],chunk_size=options['chunk_size'],n_action_steps=options['n_action_steps'],
            temporal_ensemble_coeff=options.get('temporal_ensemble_coeff'),pretrained_backbone_weights=backbone_weights,
            kl_weight=options['kl_weight'],optimizer_lr_backbone=options['act_lr_backbone'],push_to_hub=False)
    cfg.input_features={'observation.state':PolicyFeature(type=FeatureType.STATE,shape=(contract.state_dim,))}
    cfg.input_features.update({f'observation.images.{c}':PolicyFeature(type=FeatureType.VISUAL,shape=(3,size,size)) for c in contract.cameras})
    cfg.output_features={'action':PolicyFeature(type=FeatureType.ACTION,shape=(6,))}
    cfg.normalization_mapping['VISUAL']=NormalizationMode.MEAN_STD if backbone_weights else NormalizationMode.IDENTITY
    if tiny:
        cfg.dim_model=32;cfg.n_heads=4;cfg.dim_feedforward=64;cfg.n_encoder_layers=1;cfg.n_decoder_layers=1;cfg.n_vae_encoder_layers=1;cfg.latent_dim=8;cfg.dropout=0
    return cfg

def state_projection(policy):
    return policy.model.encoder_robot_state_input_proj if policy.name=='act' else policy.model.state_proj

def image_tensor(image,*,size=None):
    """Versioned ACT letterbox RGB: shared offline/live, raw images stay untouched."""
    import numpy as np
    import torch
    from PIL import Image,ImageOps
    if size:
        image=ImageOps.pad(Image.fromarray(np.asarray(image,dtype=np.uint8)),(size,size),method=Image.Resampling.BILINEAR,color=(0,0,0),centering=(.5,.5))
    return torch.from_numpy(np.array(image,copy=True)).permute(2,0,1).float()/255


def augment_batch_images(x,*,max_rotation_deg=5.,max_scale=.08,max_shift=.08):
    """Per-sample small rotation/zoom/shift plus brightness/contrast jitter, (B,3,H,W) in 0..1.

    With ~40 demonstrations a closed-loop ACT drifts to views it never saw (the cube a few
    degrees off-centre); training on slightly displaced views makes that drift recoverable.
    """
    import math
    import torch
    import torch.nn.functional as F
    b=x.shape[0];dev=x.device
    u=lambda: (torch.rand(b,device=dev)*2-1)
    angle=u()*math.radians(max_rotation_deg);scale=1+u()*max_scale
    cos,sin=torch.cos(angle)/scale,torch.sin(angle)/scale
    theta=torch.stack([torch.stack([cos,-sin,u()*max_shift*2],1),torch.stack([sin,cos,u()*max_shift*2],1)],1)
    grid=F.affine_grid(theta,list(x.shape),align_corners=False)
    y=F.grid_sample(x,grid,mode="bilinear",padding_mode="zeros",align_corners=False)
    mean=y.mean(dim=(1,2,3),keepdim=True)
    y=(y-mean)*(1+.15*u()).view(b,1,1,1)+mean
    y=y*(1+.2*u()).view(b,1,1,1)
    return y.clamp(0.,1.)


# Imported by the local so101_lab.lerobot_plugin package before LeRobot builds
# the processor pipeline. This keeps the official rollout/control loop while restoring the exact image
# geometry used by ACT training: centered aspect-preserving resize plus black
# padding to a square input.
from dataclasses import dataclass

from lerobot.configs import PipelineFeatureType, PolicyFeature
from lerobot.policies.common.vla_utils import resize_with_pad_torch
from lerobot.processor.pipeline import ObservationProcessorStep, ProcessorStepRegistry


@ProcessorStepRegistry.register("so101_centered_letterbox_processor")
@dataclass
class CenteredLetterboxProcessorStep(ObservationProcessorStep):
    height: int = 256
    width: int = 256

    def observation(self, observation):
        result=dict(observation)
        for key,value in observation.items():
            if "image" in key and hasattr(value,"ndim"):
                original_ndim=value.ndim
                resized=resize_with_pad_torch(value,self.height,self.width)
                result[key]=resized.squeeze(0) if original_ndim==3 and resized.ndim==4 else resized
        return result

    def get_config(self):
        return {"height":self.height,"width":self.width}

    def transform_features(self,features):
        observations=features.get(PipelineFeatureType.OBSERVATION,{})
        for key,feature in list(observations.items()):
            if "image" in key:
                observations[key]=PolicyFeature(type=feature.type,shape=(feature.shape[0],self.height,self.width))
        return features
