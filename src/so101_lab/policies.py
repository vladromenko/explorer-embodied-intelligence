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
    cfg=cls(device=options['device'],chunk_size=options['chunk_size'],n_action_steps=1,
            pretrained_backbone_weights=None,kl_weight=options['kl_weight'],optimizer_lr_backbone=options['act_lr_backbone'],push_to_hub=False)
    cfg.input_features={'observation.state':PolicyFeature(type=FeatureType.STATE,shape=(contract.state_dim,))}
    cfg.input_features.update({f'observation.images.{c}':PolicyFeature(type=FeatureType.VISUAL,shape=(3,size,size)) for c in contract.cameras})
    cfg.output_features={'action':PolicyFeature(type=FeatureType.ACTION,shape=(6,))}
    cfg.normalization_mapping['VISUAL']=NormalizationMode.IDENTITY
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
