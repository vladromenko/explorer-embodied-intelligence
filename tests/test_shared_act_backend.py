"""Shared policy graph characterization; all artifacts and IO are temporary."""
import ast
import builtins
import dis
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import types

import numpy as np
import pytest
import torch
from torch.utils.data import Dataset

from explorer_shared.learning import backend_catalogue,get_backend,UnqualifiedBackend
from explorer_shared.learning import act_backend as shared
from explorer_shared.learning.artifacts import canonical,sha,state_digest,write_json
from explorer_shared.learning.settings import act_parameters

REFERENCE=Path(__file__).parent/'fixtures/act_backend_before_shared.py.txt'
REFERENCE_SHA='ce4610c12b13ca8689fb96dee26aebb274fbb2bf8b8fd88b730b040a99e0a866'


def legacy_reference():
    assert sha(REFERENCE)==REFERENCE_SHA
    module=types.ModuleType('explorer_sim._pre_shared_ACT_reference');module.__file__=str(REFERENCE)
    module.__package__='explorer_sim';sys.modules[module.__name__]=module
    exec(compile(REFERENCE.read_text(),str(REFERENCE),'exec'),module.__dict__)
    return module


def same(a,b):
    if isinstance(a,torch.Tensor):assert torch.equal(a,b)
    elif isinstance(a,dict):
        assert a.keys()==b.keys()
        for key in a:same(a[key],b[key])
    elif isinstance(a,(list,tuple)):
        assert len(a)==len(b)
        for x,y in zip(a,b):same(x,y)
    else:assert a==b


def test_registry_import_is_torch_lazy_and_candidates_are_explicitly_unqualified():
    program='''import sys,json
from explorer_shared.learning import backend_catalogue,get_backend,UnqualifiedBackend
assert 'torch' not in sys.modules and 'mujoco' not in sys.modules and 'explorer_sim' not in sys.modules
assert get_backend('act').id=='act'
for candidate in ('diffusion','smolvla'):
    try:get_backend(candidate)
    except UnqualifiedBackend:pass
    else:raise AssertionError('Unqualified candidate became executable')
try:get_backend('act').create_policy({'device':'cuda'},state_dim=6,action_dim=6)
except UnqualifiedBackend:pass
else:raise AssertionError('Old graph CUDA qualification was reused')
assert 'torch' not in sys.modules
print(json.dumps(backend_catalogue()))
'''
    program='import sys;sys.path.insert(0,'+repr(str(Path(__file__).parents[1]/'shared'))+');\n'+program
    result=subprocess.run([sys.executable,'-c',program],capture_output=True,text=True,check=True,timeout=15)
    report=json.loads(result.stdout);assert report['owner']=='shared_training_platform' and report['experiments']==['01','02','03','04','05','06']
    assert all(not entry['physical_execution_authorized'] for entry in report['backends'])


def test_shared_backend_has_no_experiment_simulator_or_hardware_dependency():
    folder=Path(shared.__file__).parent
    for path in folder.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.ImportFrom):
                assert not any(value in (node.module or '') for value in ('explorer_sim','pilot','mujoco','serial','so_follower'))
            if isinstance(node,ast.Import):
                assert not any(any(value in alias.name for value in ('explorer_sim','mujoco','serial')) for alias in node.names)


def test_legacy_public_names_use_shared_implementation_and_keep_native_default_preprocessing(tmp_path):
    from explorer_sim import student
    reference=legacy_reference()
    assert student.optimize_act is shared.optimize_act and student.save_reload_act is shared.save_reload_act
    assert student.prepare_rgb is shared.prepare_rgb and student._BatchPlan is shared._BatchPlan
    assert student.preprocessing_config()==reference.preprocessing_config()
    low=np.asarray([-100.]*5+[0.],np.float32);high=np.full(6,100.,np.float32)
    assert canonical(shared.preprocessing_config(low,high,range(6)))==canonical(reference.preprocessing_config(low,high,range(6)))
    rgb=np.arange(2*8*8*3,dtype=np.uint8).reshape(2,8,8,3)
    assert torch.equal(shared.prepare_rgb(rgb),reference.prepare_rgb(rgb))
    path=tmp_path/'canonical.json';value={'русский':['данные',1,False],'exact':.125};write_json(path,value)
    assert path.read_bytes()==reference.write_json.__globals__['canonical'](value)
    assert 'teacher_steps' not in act_parameters({}) and act_parameters({})['seed']==41
    with pytest.raises(ValueError):act_parameters({'device':'automatic'})


def test_remaining_experiment_adapters_keep_their_runtime_imports():
    from explorer_sim import student
    functions=[value for value in vars(student).values() if isinstance(value,types.FunctionType) and value.__module__==student.__name__]
    functions += [value for value in vars(student.EpisodeChunks).values() if isinstance(value,types.FunctionType)]
    for function in functions:
        unresolved=[instruction.argval for instruction in dis.get_instructions(function) if instruction.opname=='LOAD_GLOBAL'
            and instruction.argval not in function.__globals__ and not hasattr(builtins,instruction.argval)]
        assert unresolved==[],(function.__qualname__,unresolved)


class OriginalRGB6Rows(Dataset):
    def __init__(self,path):
        with np.load(path,allow_pickle=False) as source:self.rows={key:source[key].copy() for key in ('rgb','state','action')}
        self.resume_context={'source_signature':sha(path),'source_files':[{'path':str(path),'sha256':sha(path)}],
            'source_kind':'SIMULATION','qualification_only':True,'state_basis':'software_fixture_measured_state6','action_basis':'software_fixture_issued_command6'}
    def __len__(self):return len(self.rows['rgb'])
    def __getitem__(self,index):
        positions=np.arange(index,index+2);padded=positions>=len(self);positions=np.minimum(positions,len(self)-1)
        return {shared.IMAGE_KEY:shared.prepare_rgb(self.rows['rgb'][index:index+1])[0],
            'observation.state':torch.from_numpy(self.rows['state'][index]),'action':torch.from_numpy(self.rows['action'][positions]),
            'action_is_pad':torch.from_numpy(padded)}


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_SHARED_ACT_QUALIFICATION')!='1',reason='Explicit matched8-update official ACT qualification; no repeated broad-suite optimizer jobs')
def test_actual_official_matched_eight_updates_pre_extract_vs_shared_resume_and_reload(tmp_path):
    reference=legacy_reference();torch.set_num_threads(2)
    source=tmp_path/'immutable_original.npz';rng=np.random.default_rng(44)
    np.savez_compressed(source,rgb=rng.integers(0,255,(8,32,32,3),dtype=np.uint8),state=rng.uniform(-.8,.8,(8,6)).astype(np.float32),action=rng.uniform(-.8,.8,(8,6)).astype(np.float32))
    original=source.read_bytes();settings=dict(seed=41,act_steps=8,batch_size=2,image_size=32,chunk_size=2,device='cpu');rows=OriginalRGB6Rows(source)
    def seed(value):random.seed(value);np.random.seed(value);torch.manual_seed(value)
    def logging(*args):random.random();np.random.normal();torch.rand(())
    seed(41);old=reference.make_act_policy(settings,6,6);initial=state_digest(old)
    old_folder=tmp_path/'pre_extract';old_folder.mkdir()
    old_optimizer,old_losses,_=reference.optimize_act(old,rows,settings,old_folder,logging,checkpoint_interval=4)
    seed(41);new=get_backend('act').create_policy(settings,state_dim=6,action_dim=6)
    assert state_digest(new)==initial
    folder=tmp_path/'shared';folder.mkdir()
    def interrupt(*args):
        logging()
        if json.loads((folder/'act_resume/latest.json').read_text())['completed_steps']==4:raise InterruptedError('qualified temporary worker restart')
    with pytest.raises(InterruptedError):shared.optimize_act(new,rows,settings,folder,interrupt,checkpoint_interval=4)
    pointer=(folder/'act_resume/latest.json').read_bytes()
    seed(999);restored=shared.make_act_policy(dict(settings,seed=999),6,6)
    optimizer,losses,_=shared.optimize_act(restored,OriginalRGB6Rows(source),settings,folder,logging,checkpoint_interval=4)
    same(old.state_dict(),restored.state_dict());same(old_optimizer.state_dict(),optimizer.state_dict());same(old_losses,losses)
    assert optimizer.explorer_resume_info['resumed_from_step']==4 and optimizer.explorer_resume_info['optimizer_steps_executed_this_process']==4
    assert source.read_bytes()==original
    metadata=shared.preprocessing_config(np.zeros(6,np.float32),np.full(6,100.,np.float32),range(6));batch=rows[0]
    inputs={key:batch[key].unsqueeze(0) for key in (shared.IMAGE_KEY,'observation.state')}
    _,old_checkpoint,old_error=reference.save_reload_act(old,old_optimizer,old_folder,metadata,inputs,'cpu')
    reloaded,checkpoint,error=shared.save_reload_act(restored,optimizer,folder,metadata,inputs,'cpu')
    old_files={path.name:sha(path) for path in old_checkpoint.iterdir() if path.is_file()}
    new_files={path.name:sha(path) for path in checkpoint.iterdir() if path.is_file()}
    assert old_files==new_files and old_error==error==0.
    old_pointer=(old_folder/'act_resume/latest.json').read_bytes()
    with pytest.raises(ValueError,match='signature changed'):shared.optimize_act(shared.make_act_policy(settings,6,6),rows,settings,old_folder,logging,checkpoint_interval=4)
    assert (old_folder/'act_resume/latest.json').read_bytes()==old_pointer and all(sha(old_checkpoint/name)==expected for name,expected in old_files.items())
    receipt=dict(schema='explorer_shared_ACT_extraction_qualification_v1',reference_sha256=REFERENCE_SHA,source_kind='SIMULATION',
        physical_io=False,simulator_executed=False,matched_optimizer_budget_per_run=8,source_rgb_frames=8,state_dimension=6,action_dimension=6,
        loss_first=losses[0]['loss'],loss_last=losses[-1]['loss'],bit_exact_weights=True,bit_exact_optimizer_state=True,bit_exact_loss_history=True,
        bit_exact_checkpoint_files=True,checkpoint_sha256=new_files,reload_max_abs_error=error,source_bytes_preserved=True,
        legacy_checkpoint_incompatible_graph_rejected_without_overwrite=True,training_resume=optimizer.explorer_resume_info,
        backend_implementation_sha256=sha(shared.__file__),artifacts_implementation_sha256=sha(Path(shared.__file__).parent/'artifacts.py'),
        learned_policy_rollout_executed=False,physical_task_success=False)
    write_json(tmp_path/'qualification.json',receipt)
