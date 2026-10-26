"""ENGINEERING_GLUE: source-pinned ACT9 CUDA/CAD qualification, with no hardware IO.

Run only in an own bounded user cgroup. The existing real integration test
creates contact-scored scripted data, executes eight official optimizer updates,
reloads saved processors and independently evaluates RGB+preceding command9.
This deliberately qualifies neither another actor shape nor a physical skill.
"""
import argparse
import importlib.metadata
import json
from pathlib import Path
import resource
import sys


def own_limits():
    relative=next(line.split('::',1)[1] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    base=Path('/sys/fs/cgroup');group=(base/relative.lstrip('/')).resolve()
    if not group.is_relative_to(base) or not group.name.startswith('explorer-act-cuda-qual-'):
        raise ValueError('Own explorer-act-cuda-qual-* transient user service required')
    maximum=(group/'memory.max').read_text().strip();swap=(group/'memory.swap.max').read_text().strip()
    quota,period=(group/'cpu.max').read_text().split()
    if not maximum.isdigit() or not 128*1024**2<=int(maximum)<=4*1024**3 or swap!='0' or quota=='max' or int(quota)/int(period)>2:
        raise ValueError('MemoryMax<=4GiB, MemorySwapMax=0 and CPUQuota<=200% required')
    return group,dict(path=str(group),memory_max=int(maximum),memory_swap_max=0,cpu_quota_cores=int(quota)/int(period))


def qualify(root, output):
    group,limits=own_limits();root=Path(root).resolve();output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    for name in ('shared','00-control-center/src','01-autonomous-learning/src'):
        sys.path.insert(0,str(root/name))
    from explorer.qualification_bindings import (runtime_identity,pipeline_hashes,
        pipeline_graph_version,publish_gpu_binding,qualified_gpu_binding)
    from explorer_shared.learning.artifacts import sha,write_json
    from explorer_shared.learning.runtime_profiles import (ACT9_CUDA_DETERMINISTIC_PROFILE,ACT9_TENSOR_PROFILE,ACT9_RUNTIME_CONSTRAINTS,ACT9_WORKER_ENVIRONMENT)
    code=pipeline_hashes(root,'manipulation_student')
    if pipeline_graph_version(root,'manipulation_student')!=4:raise ValueError('Current explicitly profiled shared ACT graph required')
    import os
    if 'torch' in sys.modules or any(os.environ.get(name)!=value for name,value in ACT9_WORKER_ENVIRONMENT.items()):
        raise ValueError('Named CUDA environment must be validated before importing optional Torch libraries')
    runtime_before=runtime_identity(sys.executable)
    package=importlib.metadata.distribution('lerobot')
    names=('lerobot/policies/act/configuration_act.py','lerobot/policies/act/modeling_act.py',
           'lerobot/policies/act/processor_act.py','lerobot/policies/factory.py',
           'lerobot/processor/pipeline.py','lerobot/processor/device_processor.py')
    official={str(package.locate_file(name)):sha(package.locate_file(name)) for name in names}
    write_json(output/'preflight.json',dict(source_commit=root.name,pipeline_source_sha256=code,
        runtime=runtime_before,official_implementation_sha256=official,cgroup_limits=limits,
        qualification_script_sha256=sha(__file__),named_profile_environment_validated_before_Torch_import=True,
        cuda_execution_profile=ACT9_CUDA_DETERMINISTIC_PROFILE,physical_io=False))
    import pytest
    os.environ['EXPLORER_RUN_MANIPULATION_STUDENT_INTEGRATION']='1'
    os.environ['EXPLORER_MANIPULATION_STUDENT_TEST_DEVICE']='cuda'
    os.environ['EXPLORER_MANIPULATION_STUDENT_TEST_CUDA_PROFILE']=ACT9_CUDA_DETERMINISTIC_PROFILE
    status=pytest.main(['-x','-q',str(root/'01-autonomous-learning/tests/test_manipulation_student.py'),
                        '--basetemp='+str(output/'pytest')])
    import torch
    runtime_after=runtime_identity(sys.executable)
    if status!=0:raise RuntimeError('Actual ACT9 CUDA integration test failed; preserve logs/output')
    if runtime_before!=runtime_after or pipeline_hashes(root,'manipulation_student')!=code or any(sha(name)!=digest for name,digest in official.items()):
        raise ValueError('Source/runtime changed during actual qualification')
    results=list((output/'pytest').glob('test_actual_official_act_full_*/jobs/visual-cycle/visual-manipulation-result.json'))
    results=[path for path in results if not any(part.endswith('current') for part in path.parts)]
    if len(results)!=1:raise ValueError('One immutable actual integration result required')
    result_path=results[0];result=json.loads(result_path.read_text());student=result['student']
    if not (student['optimizer_steps']==8 and student['weights_changed'] and student['save_reload_passed']
            and student['official_saved_processors_executed'] and student['parameter_device'].startswith('cuda')
            and student['parameter_dtype']=='torch.float32'
            and student['TF32_enabled'] is False and result['scripted_baseline']['successes']==1
            and result['evaluation']['n']==1 and result['evaluation']['episodes'][0]['native_replay']['within_1e_minus_9']):
        raise ValueError('Actual result does not establish narrow ACT9 CUDA path')
    measured=student.get('runtime_profile_receipt') or {}
    if (measured.get('CUDA_initialized_before_configuration') is not False
            or measured.get('runtime_constraints')!=ACT9_RUNTIME_CONSTRAINTS
            or measured.get('environment_validated_before_configuration') is not True):
        raise ValueError('Actual worker did not establish named deterministic constraints before CUDA startup')
    receipt=dict(schema='explorer_shared_act9_cuda_qualification_v3',passed=True,
        scope='Current shared compact nine-axis official ACT CUDA; real CAD contact teacher,8updates,saved processors,independent32step RGB+command9 evaluation. Software only.',
        qualified_job_kinds=['manipulation_student'],pipeline_graph_version=4,
        state_dimension=9,action_dimension=9,optimizer_steps=8,weights_changed=True,
        qualified_tensor_profile=dict(ACT9_TENSOR_PROFILE,cuda_execution_profile=ACT9_CUDA_DETERMINISTIC_PROFILE,runtime_constraints=dict(ACT9_RUNTIME_CONSTRAINTS)),
        runtime_constraints_verified_before_CUDA_startup=True,
        official_saved_processors_executed=True,pipeline_source_sha256=code,
        framework_versions=result['provenance']['context']['versions'],lerobot=importlib.metadata.version('lerobot'),
        runtime_identity=runtime_after,official_implementation_sha256=official,
        result_path=str(result_path),result_sha256=sha(result_path),
        device=torch.cuda.get_device_name(),compute_capability=list(torch.cuda.get_device_capability()),
        compiled_architectures=torch.cuda.get_arch_list(),tf32_enabled=False,
        process_peak_RSS_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(),cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
        cgroup_limits=limits,cgroup_peak_memory_bytes=int((group/'memory.peak').read_text()),
        cgroup_memory_events=(group/'memory.events').read_text(),
        independent_development_successes=result['evaluation']['successes'],
        reload_max_abs_error=student['reload_max_abs_error'],losses_first_last=student['losses_first_last'],
        physical_io=False,physical_skill_qualified=False,other_actor_shapes_qualified=False,
        limitations=['Wheel lists architectures excluding OrinCC8.7; narrow actual execution does not imply broad compatibility',
                     'Short development rollout is not full-task mastery or physical success',
                     'Own CPU/RSS cgroup does not isolate every Jetson unified GPU allocation'])
    write_json(output/'qualification.json',receipt)
    binding=publish_gpu_binding(output/'qualification.json',root,'manipulation_student',sys.executable,output/'immutable-source-bindings')
    verified=qualified_gpu_binding({'kind':'manipulation_student','config':result['provenance']['context']['settings']},root,sys.executable,output/'immutable-source-bindings',required_binding_sha256=binding['binding_sha256'])
    write_json(output/'binding-verification.json',dict(binding=binding,selected=verified,hardware_io=False))
    print(json.dumps({key:receipt[key] for key in ('passed','optimizer_steps','reload_max_abs_error',
        'process_peak_RSS_bytes','cuda_peak_allocated_bytes','cgroup_peak_memory_bytes','independent_development_successes')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();qualify(args.root,args.output)
