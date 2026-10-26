"""Causal observation-history/action-horizon views of immutable native episodes.

The source adapter supplies its authoritative episode loader; this module never
imports an experiment, reconstructs missing measurements, changes source splits
or opens sealed payload. At most one decoded source episode is cached.
"""
import inspect
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torch.utils.data import Dataset

from .artifacts import sha
from .diffusion_backend import IMAGE_KEY


def validate_split_groups(manifest):
    groups={}
    for descriptor in manifest['episodes']:
        split=descriptor.get('split')
        if split not in ('train','development','sealed_test'):raise ValueError('Literal original episode split required')
        identity=descriptor.get('source_episode_id',descriptor.get('id'))
        if not identity:raise ValueError('Original episode identity required')
        keys=[('episode',identity),('payload',descriptor['sha256'])]
        for field in ('session_id','scene_id','object_instance_id'):
            if descriptor.get(field):keys.append((field,descriptor[field]))
        keys += [('frame',descriptor.get('clock_domain',manifest.get('clock_domain')),value) for value in descriptor.get('frame_source_ids',[])]
        for key in keys:
            if key in groups and groups[key]!=split:raise ValueError('Original episode/content/session/scene/frame overlaps dataset partitions')
            groups[key]=split


class HistoryWindows(Dataset):
    def __init__(self,manifest_path,manifest,descriptors,*,episode_loader,partition,image_size=64,n_obs_steps=2,horizon=8):
        if partition not in ('train','development'):raise ValueError('History dataset may open train/development only, never sealed payload')
        if type(n_obs_steps) is not int or type(horizon) is not int or not 1<=n_obs_steps<=horizon:raise ValueError('Explicit bounded observation/history horizon required')
        if type(image_size) is not int or not 64<=image_size<=96:raise ValueError('Compact Diffusion requires image64..96 for nondegenerate spatial features')
        validate_split_groups(manifest)
        if not descriptors or any(row.get('split')!=partition or row not in manifest['episodes'] for row in descriptors):raise ValueError('Select only original descriptors from the requested partition')
        self.path=Path(manifest_path).resolve();self.manifest=manifest;self.descriptors=descriptors;self.loader=episode_loader
        self.image_size=image_size;self.n_obs_steps=n_obs_steps;self.horizon=horizon;self.partition=partition
        normalization=manifest.get('action_normalization')
        if not isinstance(normalization,dict):raise ValueError('Explicit source native action normalization required; no other robot defaults')
        self.low=np.asarray(normalization['low'],np.float32);self.high=np.asarray(normalization['high'],np.float32)
        if self.low.ndim!=1 or self.low.shape!=self.high.shape or not np.isfinite(self.low).all() or not np.isfinite(self.high).all() or np.any(self.high<=self.low):raise ValueError('Finite explicit source native normalization required')
        self.cache=None;self.cached_episode=None;self.indices=[]
        for episode_index,descriptor in enumerate(descriptors):
            source=self._load(descriptor)
            self.indices.extend((episode_index,index) for index in range(len(source['rgb'])))
        loader_file=inspect.getsourcefile(episode_loader)
        source_files=[dict(path=str(self.path),sha256=sha(self.path))]+[dict(path=str((self.path.parent/item['file']).resolve()),sha256=item['sha256']) for item in descriptors]
        if loader_file:source_files.append(dict(path=loader_file,sha256=sha(loader_file)))
        self.resume_context=dict(source_signature=sha(self.path),source_files=source_files,training_episode_ids=[item.get('source_episode_id',item.get('id')) for item in descriptors],
            partition=partition,observation_history=n_obs_steps,action_horizon=horizon,image_size=image_size,RGB_transform='uint8/255_NCHW_bilinear_resize_once',
            action_horizon_origin='current_index-n_obs_steps+1',padding='repeat_original_boundary_values_with_explicit_masks_never_zero_fill',
            episode_loader_implementation_sha256=sha(loader_file) if loader_file else None)

    def _load(self,descriptor):
        member=(self.path.parent/descriptor['file']).resolve()
        if not member.is_relative_to(self.path.parent) or not member.is_file() or sha(member)!=descriptor['sha256']:raise ValueError('Immutable episode path/SHA changed')
        data=self.loader(self.path,descriptor,self.manifest)
        if data['rgb'].dtype!=np.uint8 or data['rgb'].ndim!=4 or data['rgb'].shape[-1]!=3 or not len(data['rgb']):raise ValueError('Actual nonempty uint8 RGB source required')
        if any(data[key].shape!=(len(data['rgb']),len(self.low)) or not np.isfinite(data[key]).all() for key in ('state','action')):raise ValueError('Actual finite source-native state/action dimensions required')
        return data

    def __len__(self):return len(self.indices)

    def _window(self,index):
        episode_index,frame_index=self.indices[index]
        if self.cached_episode!=episode_index:self.cache=self._load(self.descriptors[episode_index]);self.cached_episode=episode_index
        start=frame_index-self.n_obs_steps+1;observation_indices=np.arange(start,frame_index+1);action_indices=np.arange(start,start+self.horizon)
        observation_pad=observation_indices<0;action_pad=(action_indices<0)|(action_indices>=len(self.cache['rgb']))
        return frame_index,np.clip(observation_indices,0,len(self.cache['rgb'])-1),np.clip(action_indices,0,len(self.cache['rgb'])-1),observation_pad,action_pad

    def __getitem__(self,index):
        _,observations,actions,observation_pad,action_pad=self._window(index)
        images=np.stack([np.asarray(Image.fromarray(self.cache['rgb'][position]).resize((self.image_size,self.image_size),Image.Resampling.BILINEAR)) for position in observations])
        state=2*(self.cache['state'][observations]-self.low)/(self.high-self.low)-1
        action=2*(self.cache['action'][actions]-self.low)/(self.high-self.low)-1
        return {IMAGE_KEY:torch.from_numpy(images.copy()).float().permute(0,3,1,2)/255,
            'observation.state':torch.from_numpy(state),'action':torch.from_numpy(action),
            'observation_is_pad':torch.from_numpy(observation_pad),'action_is_pad':torch.from_numpy(action_pad)}

    def window_evidence(self,index):
        frame_index,observations,actions,observation_pad,action_pad=self._window(index);episode_index,_=self.indices[index]
        return dict(original_episode_id=self.descriptors[episode_index].get('source_episode_id',self.descriptors[episode_index].get('id')),
            current_source_index=frame_index,observation_source_indices=observations.tolist(),action_source_indices=actions.tolist(),
            observation_is_pad=observation_pad.tolist(),action_is_pad=action_pad.tolist(),partition=self.partition,
            future_observations_in_actor=False,phase_labels_in_actor=False,source_values_repeated_at_boundaries=True)
