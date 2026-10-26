"""ENGINEERING_GLUE: explicit process-local ACT CUDA constraints, no Torch import.

Only the named native9 profile can request these settings. A worker must receive
the cuBLAS environment before library startup; other profiles remain unchanged.
"""
import os

ACT9_CUDA_DETERMINISTIC_PROFILE = 'act9_cuda64_b2_c4_fp32_deterministic_v1'
ACT9_TENSOR_PROFILE = dict(device='cuda', precision='float32', state_dimension=9,
    action_dimension=9, image_size=64, batch_size=2, chunk_size=4)
ACT9_RUNTIME_CONSTRAINTS = dict(deterministic_algorithms=True, cudnn_deterministic=True,
    cudnn_benchmark=False, cuda_matmul_tf32=False, cudnn_tf32=False,
    matmul_precision='highest', CUBLAS_WORKSPACE_CONFIG=':4096:8',
    NVIDIA_TF32_OVERRIDE='0', TORCH_ALLOW_TF32_CUBLAS_OVERRIDE='0')
ACT9_WORKER_ENVIRONMENT = {name:ACT9_RUNTIME_CONSTRAINTS[name] for name in
    ('CUBLAS_WORKSPACE_CONFIG','NVIDIA_TF32_OVERRIDE','TORCH_ALLOW_TF32_CUBLAS_OVERRIDE')}


def act_cuda_profile(settings, state_dimension=None, action_dimension=None):
    selected = settings.get('cuda_execution_profile')
    if settings.get('device') != 'cuda':
        if selected is not None:
            raise ValueError('A CUDA execution profile cannot configure another device')
        return None
    if selected != ACT9_CUDA_DETERMINISTIC_PROFILE:
        raise ValueError('CUDA requires explicit qualified profile ' + ACT9_CUDA_DETERMINISTIC_PROFILE + '; anonymous and other profiles are unqualified in this source')
    for name in ('image_size', 'batch_size', 'chunk_size'):
        if type(settings.get(name)) is not int or settings[name] != ACT9_TENSOR_PROFILE[name]:
            raise ValueError('Named CUDA profile requires image64/batch2/chunk4 FP32 native9')
    if any(value is not None and (type(value) is not int or value != 9) for value in (state_dimension, action_dimension)):
        raise ValueError('Named CUDA profile requires state9 and action9; other actor contracts are unqualified')
    return dict(cuda_execution_profile=selected, tensor_profile=dict(ACT9_TENSOR_PROFILE),
        runtime_constraints=dict(ACT9_RUNTIME_CONSTRAINTS))


def act_worker_environment(settings):
    profile = act_cuda_profile(settings)
    return dict(ACT9_WORKER_ENVIRONMENT) if profile else {}


def configure_act_runtime(settings, state_dimension, action_dimension, torch):
    profile = act_cuda_profile(settings, state_dimension, action_dimension)
    if profile is None:
        return None
    if any(os.environ.get(name)!=value for name,value in ACT9_WORKER_ENVIRONMENT.items()):
        raise ValueError('Named CUDA profile requires server-provided cuBLAS/FP32 environment before worker/library startup')
    initialized = torch.cuda.is_initialized()
    already_configured = (torch.are_deterministic_algorithms_enabled() and torch.backends.cudnn.deterministic
        and not torch.backends.cudnn.benchmark and not torch.backends.cuda.matmul.allow_tf32
        and not torch.backends.cudnn.allow_tf32 and torch.get_float32_matmul_precision() == 'highest')
    if initialized and not already_configured:
        raise ValueError('Named CUDA constraints must be configured before CUDA startup')
    if torch.get_default_dtype() != torch.float32:
        raise ValueError('Named CUDA profile requires the actual FP32 model default')
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.set_float32_matmul_precision('highest')
    return dict(profile, CUDA_initialized_before_configuration=initialized,
        environment_validated_before_configuration=True, scope='explicit_process_local_profile_only')
