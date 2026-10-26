"""method_id=DIFFUSION_LEROBOT_0_6_1_COMPACT; P002 https://arxiv.org/abs/2303.04137.

Pinned official Diffusion compact CPU software qualification candidate.

This is a factory for an explicitly small test profile, not an admitted skill
trainer, task benchmark or hardware backend. Admission needs a separate source-bound receipt.
Optional model dependencies load only when this factory is explicitly invoked.
"""
from copy import deepcopy
import importlib.metadata

PROFILE_ID='diffusion_resnet18_unet32_64_ddim4_cpu_v1'
_PROFILE=dict(n_obs_steps=2,horizon=8,n_action_steps=4,down_dims=(32,64),kernel_size=3,n_groups=8,
    diffusion_step_embed_dim=32,spatial_softmax_num_keypoints=8,use_group_norm=True,
    vision_backbone='resnet18',pretrained_backbone_weights=None,resize_shape=None,crop_shape=None,crop_ratio=1.,
    use_separate_rgb_encoder_per_camera=True,noise_scheduler_type='DDIM',num_train_timesteps=100,num_inference_steps=4,
    do_mask_loss_for_padding=True,drop_n_last_frames=0,compile_model=False,push_to_hub=False)
IMAGE_KEY='observation.images.overview'


def compact_profile():
    return dict(profile_id=PROFILE_ID,configuration=deepcopy(_PROFILE),status='QUALIFICATION_CANDIDATE',
        device='cpu',optimizer='official0.6.1 DiffusionConfig.get_optimizer_preset() Adam',
        RGB='uint8/255 NCHW; identity external processor; no duplicate ImageNet normalization',
        production_dataset_admission=False,optimizer_resume_qualified=False,physical_execution_authorized=False)


def make_compact_diffusion_policy(settings,*,state_dim,action_dim):
    if settings.get('device','cpu')!='cpu':raise ValueError('This profile has CPU qualification scope only; CUDA requires a separate runtime/memory qualification')
    if importlib.metadata.version('lerobot')!='0.6.1' or importlib.metadata.version('diffusers')!='0.38.0':raise ImportError('Qualification pins official lerobot0.6.1 + diffusers0.38.0')
    if any(type(value) is not int or not 1<=value<=512 for value in (state_dim,action_dim)):raise ValueError('Explicit finite observation/action dimensions required')
    # ResNet18 at32px has a1x1 spatial map: SpatialSoftmax would discard image
    # information. At least64px keeps a2x2 map and must show real RGB influence.
    size=settings.get('image_size',64)
    if type(size) is not int or not 64<=size<=96:raise ValueError('Compact profile image_size must be64..96 to preserve spatial RGB information')
    import torch
    from lerobot.configs import FeatureType,NormalizationMode,PolicyFeature
    from lerobot.policies.diffusion.configuration_diffusion import DiffusionConfig
    from lerobot.policies.diffusion.modeling_diffusion import DiffusionPolicy
    torch.manual_seed(settings.get('seed',41))
    config=DiffusionConfig(device='cpu',input_features={IMAGE_KEY:PolicyFeature(type=FeatureType.VISUAL,shape=(3,size,size)),
        'observation.state':PolicyFeature(type=FeatureType.STATE,shape=(state_dim,))},
        output_features={'action':PolicyFeature(type=FeatureType.ACTION,shape=(action_dim,))},
        normalization_mapping={name:NormalizationMode.IDENTITY for name in ('VISUAL','STATE','ACTION')},**deepcopy(_PROFILE))
    return DiffusionPolicy(config).to('cpu')
