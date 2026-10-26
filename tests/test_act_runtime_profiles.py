"""Process-profile validation with no model allocation or device commands."""
from types import SimpleNamespace

import pytest

from explorer_shared.learning.runtime_profiles import (ACT9_CUDA_DETERMINISTIC_PROFILE,
    ACT9_WORKER_ENVIRONMENT, act_cuda_profile, act_worker_environment, configure_act_runtime)


def settings(**updates):
    return dict(dict(device='cuda', image_size=64, batch_size=2, chunk_size=4,
        cuda_execution_profile=ACT9_CUDA_DETERMINISTIC_PROFILE), **updates)


class Torch:
    float32 = 'actual_float32'
    def __init__(self, initialized=False):
        self.initialized=initialized;self.enabled=False;self.precision='high';self.calls=[]
        self.cuda=SimpleNamespace(is_initialized=lambda:self.initialized)
        self.backends=SimpleNamespace(cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True)),
            cudnn=SimpleNamespace(deterministic=False,benchmark=True,allow_tf32=True))
    def are_deterministic_algorithms_enabled(self):return self.enabled
    def is_deterministic_algorithms_warn_only_enabled(self):return False
    def get_float32_matmul_precision(self):return self.precision
    def get_default_dtype(self):return self.float32
    def use_deterministic_algorithms(self,value):self.enabled=value;self.calls.append(('deterministic',value))
    def set_float32_matmul_precision(self,value):self.precision=value;self.calls.append(('precision',value))


def test_CPU_profile_does_not_touch_global_torch_flags_or_environment():
    torch=Torch();assert configure_act_runtime({'device':'cpu'},6,6,torch) is None
    assert torch.calls==[] and not torch.enabled and torch.backends.cudnn.benchmark
    assert act_worker_environment({'device':'cpu'})=={}
    with pytest.raises(ValueError,match='another device'):act_cuda_profile(settings(device='cpu'))


@pytest.mark.parametrize('changes',[{'cuda_execution_profile':None},{'cuda_execution_profile':'unqualified'},
    {'image_size':128},{'batch_size':4},{'chunk_size':16},{'batch_size':True}])
def test_anonymous_or_unqualified_tensor_shapes_are_not_silently_relabelled(changes):
    with pytest.raises(ValueError):act_cuda_profile(settings(**changes),9,9)


@pytest.mark.parametrize('dimensions',[(6,6),(106,9),(9,6),(True,9)])
def test_native6_and_fused106_are_not_qualified_by_native9_profile(dimensions):
    with pytest.raises(ValueError,match='state9 and action9'):act_cuda_profile(settings(),*dimensions)


def test_profile_requires_exact_server_environment_before_CUDA_startup(monkeypatch):
    monkeypatch.delenv('CUBLAS_WORKSPACE_CONFIG',raising=False);torch=Torch()
    with pytest.raises(ValueError,match='before worker/library startup'):configure_act_runtime(settings(),9,9,torch)
    assert not torch.calls
    for name,value in ACT9_WORKER_ENVIRONMENT.items():monkeypatch.setenv(name,value)
    started=Torch(initialized=True)
    with pytest.raises(ValueError,match='before CUDA startup'):configure_act_runtime(settings(),9,9,started)
    assert not started.calls
    receipt=configure_act_runtime(settings(),9,9,torch)
    assert not receipt['CUDA_initialized_before_configuration'] and receipt['environment_validated_before_configuration']
    assert torch.enabled and torch.backends.cudnn.deterministic and not torch.backends.cudnn.benchmark
    assert not torch.backends.cuda.matmul.allow_tf32 and not torch.backends.cudnn.allow_tf32
    assert torch.precision=='highest' and act_worker_environment(settings())==ACT9_WORKER_ENVIRONMENT
    torch.initialized=True
    rechecked=configure_act_runtime(settings(),9,9,torch)
    assert not rechecked['CUDA_initialized_before_configuration'] and rechecked['CUDA_initialized_when_rechecked']
    unowned=Torch(initialized=True);unowned.enabled=True;unowned.precision='highest'
    unowned.backends.cudnn.deterministic=True;unowned.backends.cudnn.benchmark=False
    unowned.backends.cuda.matmul.allow_tf32=False;unowned.backends.cudnn.allow_tf32=False
    with pytest.raises(ValueError,match='before CUDA startup'):configure_act_runtime(settings(),9,9,unowned)
