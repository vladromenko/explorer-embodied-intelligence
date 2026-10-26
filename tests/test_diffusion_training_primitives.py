"""History causality and real optimizer state continuation, no hardware IO."""
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import random
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from torch import nn
from torch.utils.data import Dataset

from explorer_shared.learning.artifacts import sha,state_digest
from explorer_shared.learning.history_windows import HistoryWindows
from explorer_shared.learning.diffusion_optimizer import optimize_diffusion
from explorer_shared.learning.diffusion_policy import NativeDiffusionPolicy


def episode_loader(path,descriptor,manifest):
    with np.load(Path(path).parent/descriptor['file'],allow_pickle=False) as source:
        return {key:source[key].copy() for key in ('rgb','state','action')}


def windows_fixture(tmp_path):
    episodes=[]
    for number,split in enumerate(('train','development')):
        source=tmp_path/(split+'.npz');rng=np.random.default_rng(20+number)
        state=np.tile(np.arange(6,dtype=np.float32),(10,1))+np.arange(10,dtype=np.float32)[:,None]
        np.savez_compressed(source,rgb=rng.integers(0,256,(10,64,64,3),dtype=np.uint8),state=state,action=state+1)
        episodes.append(dict(id=split,source_episode_id=split,file=source.name,sha256=sha(source),split=split,frame_source_ids=[split+str(i) for i in range(10)]))
    manifest=dict(episodes=episodes,action_normalization=dict(low=[-100.]*5+[0.],high=[100.]*6),clock_domain='software-only')
    path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest));return path,manifest


def test_history_at_original_episode_start_repeats_only_actual_boundary_values_and_marks_masks(tmp_path):
    path,manifest=windows_fixture(tmp_path);data=HistoryWindows(path,manifest,manifest['episodes'][:1],episode_loader=episode_loader,partition='train')
    evidence=data.window_evidence(0);sample=data[0]
    assert evidence['observation_source_indices']==[0,0] and evidence['observation_is_pad']==[True,False]
    assert evidence['action_source_indices']==[0,0,1,2,3,4,5,6] and evidence['action_is_pad']==[True,False,False,False,False,False,False,False]
    assert torch.equal(sample['observation.state'][0],sample['observation.state'][1])
    assert sample['observation.images.overview'].shape==(2,3,64,64) and 0<=sample['observation.images.overview'].min()<=sample['observation.images.overview'].max()<=1
    assert sample['observation.state'].shape==(2,6) and sample['action'].shape==(8,6)


def test_future_targets_never_become_actor_observations_and_windows_do_not_cross_originals(tmp_path):
    path,manifest=windows_fixture(tmp_path);data=HistoryWindows(path,manifest,manifest['episodes'][1:],episode_loader=episode_loader,partition='development')
    evidence=data.window_evidence(4)
    assert evidence['current_source_index']==4 and evidence['observation_source_indices']==[3,4]
    assert evidence['action_source_indices']==[3,4,5,6,7,8,9,9] and evidence['action_is_pad'][-1]
    assert evidence['original_episode_id']=='development' and not evidence['future_observations_in_actor'] and not evidence['phase_labels_in_actor']
    assert all(row[0]==0 for row in data.indices)


def test_sealed_payload_is_never_opened_and_duplicated_original_frame_or_content_cannot_cross_splits(tmp_path):
    path,manifest=windows_fixture(tmp_path);calls=[]
    def spy(*args):calls.append(args);return episode_loader(*args)
    with pytest.raises(ValueError,match='never sealed'):HistoryWindows(path,manifest,manifest['episodes'][:1],episode_loader=spy,partition='sealed_test')
    assert not calls
    manifest['episodes'][1]['frame_source_ids'][0]=manifest['episodes'][0]['frame_source_ids'][0]
    with pytest.raises(ValueError,match='overlaps'):HistoryWindows(path,manifest,manifest['episodes'][:1],episode_loader=spy,partition='train')
    assert not calls


def test_changed_original_rejected_before_first_decode_and_invalid_measurement_mask_is_not_zero_filled(tmp_path):
    path,manifest=windows_fixture(tmp_path);data=HistoryWindows(path,manifest,manifest['episodes'][:1],episode_loader=episode_loader,partition='train')
    (tmp_path/'train.npz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='SHA changed'):data[0]
    view=object.__new__(NativeDiffusionPolicy);view.low=np.asarray([-100.]*5+[0.]);view.high=np.full(6,100.)
    with pytest.raises(ValueError,match='Unavailable'):view.predict_history(np.zeros((2,64,64,3),np.uint8),np.zeros((2,6),np.float32),valid_mask=np.zeros((2,6),bool))


@dataclass
class Preset:
    lr:float=.001
    grad_clip_norm:float=10.
    def build(self,params):return torch.optim.Adam(params,lr=self.lr,betas=(.95,.999),weight_decay=1e-6)


@dataclass
class Config:
    optimizer_lr:float=.001
    def get_optimizer_preset(self):return Preset()


class Rows(Dataset):
    def __init__(self,path):
        self.values=np.load(path);self.resume_context=dict(source_signature=sha(path),source_files=[dict(path=str(path),sha256=sha(path))])
    def __len__(self):return len(self.values)
    def __getitem__(self,index):
        x=torch.from_numpy(self.values[index].copy())+.01*(random.random()+np.random.normal()+torch.rand(()))
        return dict(x=x,target=x[:1]*2-x[1:])


class Policy(nn.Module):
    def __init__(self):
        super().__init__();self.config=Config();self.network=nn.Sequential(nn.Linear(2,12),nn.ReLU(),nn.Dropout(.3),nn.Linear(12,1))
    def get_optim_params(self):return self.parameters()
    def forward(self,batch):return (self.network(batch['x'])-batch['target']).square().mean(),None


def same(a,b):
    if isinstance(a,torch.Tensor):assert torch.equal(a,b)
    elif isinstance(a,dict):
        assert a.keys()==b.keys()
        for key in a:same(a[key],b[key])
    elif isinstance(a,(list,tuple)):
        assert len(a)==len(b)
        for x,y in zip(a,b):same(x,y)
    else:assert a==b


def seed(value):random.seed(value);np.random.seed(value);torch.manual_seed(value);torch.set_num_threads(2)
def logger(*args):random.random();np.random.normal();torch.rand(())
HAS_DIFFUSERS=importlib.util.find_spec('diffusers') is not None


@pytest.mark.skipif(not HAS_DIFFUSERS,reason='Diffusion optional runtime is isolated; primitive optimizer binds its exact installed version')
def test_None_terms_official_Adam_preset_and_stochastic_resume_match_uninterrupted_exactly(tmp_path):
    source=tmp_path/'rows.npy';np.save(source,np.arange(14,dtype=np.float32).reshape(7,2)/20);rows=Rows(source)
    config=dict(device='cpu',seed=23,batch_size=3,optimizer_steps=8);seed(71);reference=Policy()
    reference_optimizer,reference_losses,_=optimize_diffusion(reference,rows,config,tmp_path/'full',logger,checkpoint_interval=4)
    seed(71);interrupted=Policy();folder=tmp_path/'interrupted'
    def interrupt(*args):
        logger()
        if json.loads((folder/'diffusion_resume/latest.json').read_text())['completed_steps']==4:raise InterruptedError('test process interruption')
    with pytest.raises(InterruptedError):optimize_diffusion(interrupted,rows,config,folder,interrupt,checkpoint_interval=4)
    seed(999);restored=Policy();optimizer,losses,_=optimize_diffusion(restored,Rows(source),config,folder,logger,checkpoint_interval=4)
    same(reference.state_dict(),restored.state_dict());same(reference_optimizer.state_dict(),optimizer.state_dict());same(reference_losses,losses)
    assert optimizer.explorer_resume_info['resumed_from_step']==4 and optimizer.explorer_resume_info['optimizer_steps_executed_this_process']==4
    assert type(optimizer).__name__=='Adam' and len(losses)==8
    pointer=(folder/'diffusion_resume/latest.json').read_bytes()
    with pytest.raises(ValueError,match='signature changed'):optimize_diffusion(Policy(),rows,dict(config,optimizer_steps=9),folder,logger,checkpoint_interval=4)
    assert (folder/'diffusion_resume/latest.json').read_bytes()==pointer
