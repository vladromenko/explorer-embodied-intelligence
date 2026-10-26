"""ENGINEERING_GLUE: hash-bound native6 ACT shadow inference; no executor or device IO.

One actual RGB/measured-state observation conditions each returned prediction.
Native source clocks prove causality only; caller owns transport freshness and
server authentication. A prediction is never a requested/dispatched command.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path

import numpy as np
from PIL import Image


def _digest_bytes(value):return hashlib.sha256(value).hexdigest()


def validate_observation(rgb,native,valid_mask,observation,*,camera_id,calibration_sha256,low,high,max_age_ns=2_000_000_000):
    image=np.asarray(rgb);original_state=np.asarray(native);mask=np.asarray(valid_mask)
    if image.dtype!=np.uint8 or image.ndim!=3 or image.shape[-1]!=3 or not image.shape[0] or not image.shape[1]:raise ValueError('Actual nonempty uint8 RGB required')
    if original_state.dtype.kind not in 'fiu':raise ValueError('Measured state must be actual numeric coordinates, never strings/bools/null')
    state=original_state.astype(np.float32)
    if state.shape!=(6,) or not np.isfinite(state).all() or np.any(state<low) or np.any(state>high):raise ValueError('Finite measured native6 within original calibrated ranges required')
    if mask.shape!=(6,) or mask.dtype!=np.bool_ or not mask.all():raise ValueError('Unavailable measured channels cannot be replaced with zeros')
    if not isinstance(observation,dict):raise ValueError('Source-bound observation metadata required')
    if observation.get('camera_id')!=camera_id or observation.get('follower_calibration_sha256')!=calibration_sha256:raise ValueError('Current camera/follower calibration identity differs')
    if observation.get('state_basis')!='measured_state' or not observation.get('frame_id'):raise ValueError('Named actual frame and measured-state provenance required')
    hashes=(observation.get('frame_sha256'),observation.get('decoded_rgb_sha256'))
    if any(not isinstance(value,str) or len(value)!=64 or any(char not in '0123456789abcdef' for char in value) for value in hashes) or hashes[1]!=_digest_bytes(image.tobytes()):raise ValueError('Actual RGB/frame checksum metadata differs')
    domains=[observation.get(key) for key in ('frame_clock_domain','state_clock_domain','source_cutoff_clock_domain')]
    boots=[observation.get(key) for key in ('frame_boot_id','state_boot_id','source_cutoff_boot_id')]
    if any(not isinstance(value,str) or not value for value in domains+boots) or len(set(domains))!=1 or len(set(boots))!=1:raise ValueError('Source clocks/boot differ; no cross-domain timestamp arithmetic allowed')
    stamps=[observation.get(key) for key in ('frame_monotonic_ns','state_monotonic_ns','source_cutoff_monotonic_ns')]
    if any(type(value) is not int or value<0 for value in stamps) or not stamps[0]<=stamps[1]<=stamps[2] or stamps[2]-stamps[0]>max_age_ns:raise ValueError('Source observation is future, stale or noncausal')
    if observation.get('source_cutoff_basis') not in ('native_observation_packet_created','original_native_dispatch_time_for_offline_evaluation'):raise ValueError('Explicit native packet/offline cutoff basis required; NX time is a separate clock')
    return image,state


class NativeACTPolicy:
    """CPU source04 native6 policy; saved official processors, no hardware path."""
    history_steps=1
    prediction_steps=1
    physical_io=False

    def __init__(self,checkpoint,expected_sha256,*,expected_contract_id,expected_names,expected_units,expected_low,expected_high,
                 expected_camera_id,expected_calibration_sha256):
        checkpoint=Path(checkpoint).resolve()
        required={'config.json','model.safetensors','explorer_preprocessing.json','policy_preprocessor.json','policy_postprocessor.json'}
        if not isinstance(expected_sha256,dict) or not required<=set(expected_sha256):raise ValueError('Complete server-bound checkpoint and saved official processor hashes required')
        for name,digest in expected_sha256.items():
            path=(checkpoint/name).resolve()
            if Path(name).name!=name or not path.is_relative_to(checkpoint) or not path.is_file() or _digest_bytes(path.read_bytes())!=digest:raise ValueError('ACT checkpoint/processor checksum or path changed')
        if {path.name for path in checkpoint.iterdir() if path.is_file()}!=set(expected_sha256):raise ValueError('Unbound extra checkpoint/processor file')
        preprocessing=json.loads((checkpoint/'explorer_preprocessing.json').read_text())
        expected=dict(action_contract_id=expected_contract_id,action_names=list(expected_names),action_units=list(expected_units),
            state_low=list(expected_low),state_high=list(expected_high),state_basis='measured_state',
            state_transform='2*(state-low)/(high-low)-1',action_transform='same fixed endpoint normalization',
            rgb_layout='NCHW',rgb_scale='uint8 / 255',mean=[.485,.456,.406],std=[.229,.224,.225],
            temporal_ensembling=False,chunk_execution_steps=1,official_robot_use_degrees=False)
        if len(expected_names)!=6 or any(preprocessing.get(key)!=value for key,value in expected.items()):raise ValueError('ACT original native6 coordinate/preprocessing contract differs')
        calib=((preprocessing.get('source_calibration_metadata') or {}).get('calibration_metadata') or {}).get('follower') or {}
        if calib.get('calibration_sha256')!=expected_calibration_sha256 or expected_camera_id not in preprocessing.get('source_camera_ids',[]):raise ValueError('ACT source camera/calibration binding unavailable or changed')
        if importlib.metadata.version('lerobot')!='0.6.1':raise ValueError('Official LeRobot0.6.1 required')
        from lerobot.policies.act.configuration_act import ACTConfig
        from lerobot.policies.act.modeling_act import ACTPolicy
        from lerobot.policies.factory import make_pre_post_processors
        from .act_backend import IMAGE_KEY,prepare_rgb
        config=ACTConfig.from_pretrained(checkpoint,local_files_only=True)
        if (config.device!='cpu' or config.input_features['observation.state'].shape!=(6,)
                or config.output_features['action'].shape!=(6,) or config.n_action_steps!=1 or config.temporal_ensemble_coeff is not None
                or any(str(getattr(value,'value',value)).upper()!='IDENTITY' for value in config.normalization_mapping.values())):
            raise ValueError('ACT saved profile/state/action/identity normalization differs')
        visual={key:value for key,value in config.input_features.items() if key.startswith('observation.images.')}
        shape=visual.get(IMAGE_KEY).shape if IMAGE_KEY in visual else None
        if len(visual)!=1 or shape is None or len(shape)!=3 or shape[0]!=3 or shape[1]!=shape[2] or not 32<=shape[1]<=224:raise ValueError('Exactly one saved square source RGB slot required')
        self.policy=ACTPolicy.from_pretrained(checkpoint,config=config,local_files_only=True,strict=True).cpu().eval()
        self.pre,self.post=make_pre_post_processors(config,pretrained_path=str(checkpoint),preprocessor_overrides={'device_processor':{'device':'cpu'}})
        self.image_key=IMAGE_KEY;self.prepare_rgb=prepare_rgb;self.image_size=shape[1]
        self.low=np.asarray(expected_low,np.float32);self.high=np.asarray(expected_high,np.float32)
        self.camera_id=expected_camera_id;self.calibration_sha256=expected_calibration_sha256
        self.checkpoint_sha256=dict(expected_sha256);self.preprocessing=preprocessing;self.last_receipt=None

    def predict(self,rgb,native_measured,*,valid_mask,observation):
        import torch
        image,state=validate_observation(rgb,native_measured,valid_mask,observation,camera_id=self.camera_id,
            calibration_sha256=self.calibration_sha256,low=self.low,high=self.high)
        # Exact training adapter: Pillow's same resize and shared ImageNet transform.
        resized=np.asarray(Image.fromarray(image).resize((self.image_size,self.image_size)))
        inputs=self.pre({self.image_key:self.prepare_rgb(resized[None])[0],
            'observation.state':torch.from_numpy(2*(state-self.low)/(self.high-self.low)-1)})
        self.policy.reset()
        with torch.inference_mode():normalized=self.post(self.policy.select_action(inputs)).detach().cpu().numpy()[0]
        if normalized.shape!=(6,) or not np.isfinite(normalized).all():raise ValueError('Nonfinite or incorrect official ACT6 output')
        native=((np.clip(normalized,-1,1)+1)*.5*(self.high-self.low)+self.low).astype(np.float32)
        self.last_receipt=dict(schema='explorer_native6_shadow_prediction_v1',frame_id=observation['frame_id'],frame_sha256=observation['frame_sha256'],
            decoded_rgb_sha256=observation['decoded_rgb_sha256'],source_cutoff_basis=observation['source_cutoff_basis'],
            state_basis='measured_state',follower_calibration_sha256=self.calibration_sha256,camera_id=self.camera_id,
            normalized_output=normalized.tolist(),native_output_range_projection=bool(np.any(np.abs(normalized)>1)),
            official_saved_processors_executed=True,history_steps=1,hardware_commands_dispatched=False,physical_execution_authorized=False,
            transport_freshness_verified_by_this_pure_adapter=False,encoded_JPEG_sha256_verified_by_this_pure_adapter=False,
            decoded_RGB_argument_sha256_verified=True,contact_or_attainment_inferred=False)
        return native

    def predict_history(self,rgb_history,native_measured_history,*,valid_mask,observations):
        images=np.asarray(rgb_history);states=np.asarray(native_measured_history);masks=np.asarray(valid_mask)
        if images.ndim!=4 or len(images)!=1 or states.shape!=(1,6) or masks.shape!=(1,6) or not isinstance(observations,list) or len(observations)!=1:raise ValueError('ACT history is exactly one current observation; Diffusion history semantics differ')
        return self.predict(images[0],states[0],valid_mask=masks[0],observation=observations[0])[None]
