"""ENGINEERING_GLUE: Lightweight policy backend registry; importing this module never loads Torch.

Only offline software capability is described. Experiment adapters bind actual
observation/action contracts and source evidence; no entry authorizes hardware.
"""
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol


class UnqualifiedBackend(ValueError):
    pass


class PolicyBackend(Protocol):
    id: str
    def create_policy(self,settings:dict,*,state_dim:int,action_dim:int)->Any: ...
    def optimize(self,policy,samples,settings,folder,update,**kwargs): ...
    def save_reload(self,policy,optimizer,folder,preprocessing,check_batch,device): ...


_ENTRIES={
    'act':dict(id='act',framework='LeRobot',framework_version='0.6.1',implementation='explorer_shared.learning.act_backend',
        status='IMPLEMENTED',qualification_scope='offline CPU optimizer/save/reload/resume; separate source contract required',
        devices={'cpu':'QUALIFIED_OFFLINE_SOFTWARE','cuda':'REQUIRES_NEW_SHARED_GRAPH_QUALIFICATION'},
        observation_contract='explicit source-bound RGB and state feature shape',action_contract='explicit source-bound native dimension/units',
        language_conditioned=False,hardware_executor_included=False,physical_execution_authorized=False),
    'diffusion':dict(id='diffusion',framework='LeRobot',framework_version='0.6.1',status='UNQUALIFIED',implementation=None,
        reason='General dataset trainer/resume/native6/GPU qualification pending; compact software receipt does not admit a skill',physical_execution_authorized=False,
        qualified_profiles=[dict(id='diffusion_resnet18_unet32_64_ddim4_cpu_v1',qualification='COMPACT_CPU_SOFTWARE_ONLY',device='cpu',
            state_dimension=9,action_dimension=9,image_size=64,history_steps=2,horizon=8,inference_steps=4,
            optimizer='official Adam',actual_updates=8,production_queue_ready=False,physical_execution_authorized=False,
            receipt='shared/training/DIFFUSION_CPU_QUALIFICATION.json',receipt_sha256='a5f96f466c6adf4e9f42bb7247c2c7c4c97e00ce5d92a33d74c7437dae53bb83')]),
    'smolvla':dict(id='smolvla',framework='LeRobot',framework_version='0.6.1',status='UNQUALIFIED',implementation=None,
        reason='Candidate only: no Explorer model/runtime/resource/conditioning qualification',physical_execution_authorized=False),
}


def backend_catalogue():
    return dict(schema='explorer_shared_policy_backends_v1',owner='shared_training_platform',experiments=['01','02','03','04','05','06'],
        backends=deepcopy(list(_ENTRIES.values())),hardware_commands_dispatched=False,automatic_deployment=False)


@dataclass(frozen=True)
class ACTBackend:
    id:str='act'

    def create_policy(self,settings,*,state_dim,action_dim):
        if settings.get('device','cpu')!='cpu':raise UnqualifiedBackend('Shared graph CUDA needs a new explicit software qualification; older GPU receipts do not transfer')
        from .act_backend import make_act_policy
        return make_act_policy(settings,state_dim,action_dim)

    def optimize(self,policy,samples,settings,folder,update,**kwargs):
        from .act_backend import optimize_act
        return optimize_act(policy,samples,settings,folder,update,**kwargs)

    def save_reload(self,policy,optimizer,folder,preprocessing,check_batch,device):
        from .act_backend import save_reload_act
        return save_reload_act(policy,optimizer,folder,preprocessing,check_batch,device)


def get_backend(name='act') -> PolicyBackend:
    if name not in _ENTRIES:raise ValueError('Unknown shared policy backend')
    if _ENTRIES[name]['status']!='IMPLEMENTED':raise UnqualifiedBackend(_ENTRIES[name]['reason'])
    return ACTBackend()
