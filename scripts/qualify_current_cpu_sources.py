#!/usr/bin/env python3
"""ENGINEERING_GLUE: serial current-source CPU qualification, no hardware.

A stdlib-only plan binds a clean immutable Git checkout, final Station/Gate
hashes and exact existing tests. Heavy phases require a pinned own cgroup;
old R2 receipts/weights are read-only references. Publication files are emitted
as NEW candidate evidence, never activated in production by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time

GIB=1024**3
BASE_PYTHON=Path.home()/'.local/share/explorer/venv/bin/python'
DP_PYTHON=Path.home()/'.local/share/explorer/policy-runtimes/diffusion-0.38.0-cpu/bin/python'
STATION='shared/explorer_shared/so101_station.py'
GATE='shared/explorer_shared/station_publication_gate.py'
ADMISSION=('00-control-center/src/explorer/so101_dataset_admission.py','00-control-center/src/explorer/lab_skill_workflow.py',
    '00-control-center/src/explorer/skill_catalogue.py','01-autonomous-learning/src/explorer_sim/so101_dataset_qualification.py',
    '01-autonomous-learning/src/explorer_sim/station_adapter.py','01-autonomous-learning/src/explorer_sim/dataset_export.py',
    STATION,'shared/explorer_shared/contracts.py','shared/explorer_shared/experiments.py')
COMMON=('shared/explorer_shared/learning/act_backend.py','shared/explorer_shared/learning/artifacts.py',
    'shared/explorer_shared/learning/checkpoint_control.py','shared/explorer_shared/learning/runtime_profiles.py')
EXTRA_ACT=('01-autonomous-learning/src/explorer_sim/so101_skill_training.py','01-autonomous-learning/src/explorer_sim/recorded_training.py',
    '01-autonomous-learning/src/explorer_sim/pilot.py','00-control-center/src/explorer/so101_shadow.py','shared/explorer_shared/learning/act_policy.py')
EXTRA_DP=('00-control-center/src/explorer/so101_diffusion_training.py','shared/explorer_shared/learning/diffusion_backend.py',
    'shared/explorer_shared/learning/diffusion_optimizer.py','shared/explorer_shared/learning/diffusion_policy.py','shared/explorer_shared/learning/history_windows.py')
FUSED=COMMON+('shared/explorer_shared/learning/rosmaster_skill_pipeline.py','shared/explorer_shared/learning/rosmaster_skill_features.py','shared/explorer_shared/learning/rosmaster_skill_hybrid.py')
CONTROLLER=('00-control-center/src/explorer/store.py','00-control-center/src/explorer/scheduler.py','00-control-center/src/explorer/queue_policy.py',
    '00-control-center/src/explorer/worker_bootstrap.py','00-control-center/src/explorer/job_runner.py','00-control-center/src/explorer/policy_runtime.py',
    'tests/test_official_queue_worker_pause.py')
TESTS={
    'act':'tests/test_lab_skill_workflow.py::test_real_official_ACT6_eight_CPU_updates_reload_registered_SIMULATION_card_and_readiness',
    'diffusion':'tests/test_so101_diffusion_consumer.py::test_same_accepted_whole_skill_official_dataset_DP6_restart_saved_native_loader_and_SIMULATION_card',
    'fused106':'tests/test_rosmaster_skill_pipeline.py::test_actual_eight_official_ACT_optimizer_steps_fused_state_full9_save_reload_and_heldout_metrics',
    'controller':'tests/test_official_queue_worker_pause.py'}
PARAMETERS={'act':dict(device='cpu',state_dim=6,action_dim=6,act_steps=8,image_size=32,batch_size=2,chunk_size=4),
    'diffusion':dict(device='cpu',state_dim=6,action_dim=6,optimizer_steps=8,resumed_from_step=4,image_size=64,batch_size=2,history_steps=2,horizon=8,chunk_prediction_steps=4,seed=41),
    'fused106':dict(device='cpu',state_dim=106,action_dim=9,act_steps=8,image_size=32,batch_size=2,chunk_size=2),
    'controller':dict(backends=['act','diffusion'],steps=8,actual_supervisor=True,old_PID_exit_required=True,model_optimizer_RNG_sampler_loss_equality='exact')}
REFERENCE_NAMES={'act':'SO101_ACT6_20261026_PROFILE4_QUALIFICATION.json',
    'diffusion':'DIFFUSION_NATIVE6_20261026_CURRENT_PROFILE4_QUALIFICATION.json',
    'fused106':'ROSMASTER_ACT106_20261026_PROFILE4_CPU_QUALIFICATION.json'}


class QualificationRejected(ValueError):pass


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write(path,value):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())


def read(path,limit=16*1024**2):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>limit:raise QualificationRejected('Bounded regular qualification metadata required')
    return json.loads(path.read_bytes())


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


def file_hashes(root,names):
    root=Path(root).resolve();result={}
    for name in sorted(set(names)):
        path=root/name
        if path.is_symlink() or not path.is_file() or path.resolve()!=path:raise QualificationRejected('Pinned source missing/aliased: '+name)
        result[name]=sha(path)
    return result


def assert_source(root,commit,station_sha256,gate_sha256):
    root=Path(root).resolve()
    if not re.fullmatch('[0-9a-f]{40}',commit) or git(root,'rev-parse','HEAD')!=commit:
        raise QualificationRejected('Exact full frozen Git commit required')
    if git(root,'status','--porcelain'):raise QualificationRejected('Freeze a clean qualification worktree first')
    if any(not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value) for value in (station_sha256,gate_sha256)):
        raise QualificationRejected('Both final Station and Gate SHA256 required')
    if sha(root/STATION)!=station_sha256 or sha(root/GATE)!=gate_sha256:
        raise QualificationRejected('Final Station/Gate freeze differs')
    return root


def prepare_plan(root,output,*,commit,station_sha256,gate_sha256,label):
    if not isinstance(label,str) or not re.fullmatch('[0-9A-Z_]{8,64}',label):raise QualificationRejected('New immutable evidence label required')
    root=assert_source(root,commit,station_sha256,gate_sha256);output=Path(output).resolve()
    if output.exists() or output.is_symlink():raise QualificationRejected('New output root required; old attempts remain immutable')
    graphs=dict(act=ADMISSION+COMMON+EXTRA_ACT,diffusion=ADMISSION+COMMON+EXTRA_DP,fused106=FUSED)
    sources={mode:file_hashes(root,names) for mode,names in graphs.items()}
    harness=tuple(test.split('::',1)[0] for test in TESTS.values())+('conftest.py','tests/test_skill_workflow.py','tests/test_embodied_episode.py')
    if len(sources['diffusion'])!=18 or STATION not in sources['diffusion']:raise QualificationRejected('Expected full native6 18-source graph including Station')
    references={}
    for mode,name in REFERENCE_NAMES.items():
        path=root/'shared/training'/name;result=read(path)
        references[mode]=dict(path=str(path),sha256=sha(path),model_weights_sha256=result['checkpoint_sha256']['model.safetensors'],optimizer_steps=result['optimizer_steps'])
        if result['optimizer_steps']!=8:raise QualificationRejected('Prior reference is not the original eight-update result')
    prior=read(root/'shared/training/DIFFUSION_NATIVE6_20261026_CURRENT_PROFILE4_SOURCE_BINDING.json')['implementation_sha256']
    changes={name:dict(old=prior.get(name),current=value) for name,value in sources['diffusion'].items() if prior.get(name)!=value}
    stages={mode:dict(test=test,parameters=json.loads(canonical(PARAMETERS[mode])),executable=str(DP_PYTHON if mode=='diffusion' else BASE_PYTHON),
        unit='explorer-station-cpu-qual-'+label.lower().replace('_','-')+'-'+mode,
        limits=dict(memory_max_bytes=4*GIB,memory_swap_max_bytes=0,cpu_quota_cores=2),
        requires_new_qualified_DP_binding=mode=='controller') for mode,test in TESTS.items()}
    plan=dict(schema='explorer_current_station_CPU_qualification_plan_v1',source_root=str(root),source_commit=commit,label=label,
        source_sha256=sources,auxiliary_source_sha256=file_hashes(root,(GATE,)+CONTROLLER+harness),old_R2_references=references,
        formal_DP_changes_from_R2=changes,stages=stages,sequential_only=True,hardware_io=False,production_state_writes=False,
        old_proofs_immutable=True,tolerances_changed=False,controller_stage_requires_new_binding=True,
        actual_model_or_optimizer_executed=False,orchestrator_sha256=sha(__file__))
    output.mkdir(mode=0o700,parents=True);write(output/'plan.json',plan)
    return plan


def read_plan(path):
    plan=read(path)
    if plan.get('schema')!='explorer_current_station_CPU_qualification_plan_v1' or plan.get('orchestrator_sha256')!=sha(__file__):
        raise QualificationRejected('Frozen orchestrator/plan identity changed')
    for phase,test in TESTS.items():
        stage=plan['stages'][phase]
        expected=str(DP_PYTHON if phase=='diffusion' else BASE_PYTHON)
        if stage.get('test')!=test or stage.get('parameters')!=PARAMETERS[phase] or stage.get('executable')!=expected:
            raise QualificationRejected('Exact original test/parameters/interpreter recipe cannot be changed')
        if stage.get('limits')!=dict(memory_max_bytes=4*GIB,memory_swap_max_bytes=0,cpu_quota_cores=2):
            raise QualificationRejected('Qualification resource recipe changed')
    references=plan['old_R2_references'];root=Path(plan['source_root'])
    for mode,name in REFERENCE_NAMES.items():
        file=root/'shared/training'/name;body=read(file);row=references[mode]
        if str(file)!=row['path'] or sha(file)!=row['sha256'] or body['checkpoint_sha256']['model.safetensors']!=row['model_weights_sha256']:
            raise QualificationRejected('Immutable historical R2 reference changed')
    return plan


def limits():
    relative=next(line.split('::',1)[1] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    group=Path('/sys/fs/cgroup')/relative.lstrip('/');quota,period=(group/'cpu.max').read_text().split()
    memory=(group/'memory.max').read_text().strip();swap=(group/'memory.swap.max').read_text().strip()
    if memory!=str(4*GIB) or swap!='0' or quota=='max' or int(quota)!=2*int(period):
        raise QualificationRejected('Own CPU2/4GiB/swap0 cgroup required BEFORE pytest/policy imports')
    return dict(path=str(group),memory_max=4*GIB,memory_swap_max=0,cpu_quota_cores=2)


def resource_end(group):
    path=Path(group['path'])
    return dict(cgroup_peak_memory_bytes=int((path/'memory.peak').read_text()),cgroup_memory_events=(path/'memory.events').read_text(),
        cgroup_swap_peak_bytes=int((path/'memory.swap.peak').read_text()),process_peak_RSS_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)


def runtime(mode):
    names=['lerobot','torch','torchvision','numpy','mujoco','datasets','pillow','huggingface-hub','safetensors']
    if mode=='diffusion':names+=['diffusers','importlib_metadata','zipp','regex']
    packages={}
    for name in names:
        package=importlib.metadata.distribution(name);record=Path(package._path)/'RECORD'
        packages[name]=dict(version=package.version,location=str(package.locate_file('')),record_sha256=sha(record))
    files=['lerobot/policies/diffusion/configuration_diffusion.py','lerobot/policies/diffusion/modeling_diffusion.py','lerobot/optim/optimizers.py'] if mode=='diffusion' else ['lerobot/policies/act/configuration_act.py','lerobot/policies/act/modeling_act.py','lerobot/policies/act/processor_act.py','lerobot/policies/factory.py','lerobot/processor/pipeline.py','lerobot/processor/device_processor.py']
    official={name:sha(importlib.metadata.distribution('lerobot').locate_file(name)) for name in files}
    if mode=='diffusion':
        for name in ('diffusers/schedulers/scheduling_ddim.py','diffusers/schedulers/scheduling_ddpm.py'):
            official[name]=sha(importlib.metadata.distribution('diffusers').locate_file(name))
    return dict(executable=sys.executable,executable_resolved=str(Path(sys.executable).resolve()),executable_sha256=sha(Path(sys.executable).resolve()),
        prefix=sys.prefix,python_version=sys.version,packages=packages,official_sources_sha256=official)


def one_result(output,phase):
    pattern={'act':'test_real_official_ACT6_eig*/actual8-qualification.json',
        'diffusion':'test_same_accepted_whole_skill*/qualification.json',
        'fused106':'test_actual_eight_official_ACT_o*/state/jobs/*/result.json'}[phase]
    boundary=(output/'pytest').resolve();matches=set()
    for candidate in (output/'pytest').glob(pattern):
        resolved=candidate.resolve()
        if not resolved.is_relative_to(boundary):raise QualificationRejected('Pytest result alias escaped own basetemp')
        matches.add(resolved)
    if len(matches)!=1:raise QualificationRejected('Expected one exact actual '+phase+' result, found '+str(len(matches)))
    return next(iter(matches))


def validate_result(result,phase,reference=None):
    if result.get('optimizer_steps')!=8 or result.get('trained') is not True or result.get('weights_changed') is not True or result.get('save_reload_passed') is not True or result.get('physical_io') is not False:
        raise QualificationRejected('Actual eight-update optimizer/save/reload software evidence required')
    if result.get('reload_max_abs_error')!=0:raise QualificationRejected('No reload tolerance relaxation is permitted')
    if result['checkpoint_sha256']['model.safetensors']!=reference['model_weights_sha256']:
        raise QualificationRejected('New CPU math differs from retained R2 exact weights')
    if phase in ('act','diffusion'):
        if result.get('source_kind')!='SIMULATION':
            raise QualificationRejected('New native6 math differs from retained R2 exact weights')
    if phase=='diffusion':
        resume=result.get('training_resume',{})
        if result.get('backend')!='diffusion' or resume.get('resumed_from_step')!=4 or resume.get('optimizer_steps_executed_this_process')!=4:
            raise QualificationRejected('Actual native6 durable4+restart4 required')
    if phase=='fused106' and (result.get('feature_dimension')!=106 or result.get('output_action_dimension')!=9):
        raise QualificationRejected('Fused106 CPU profile must retain106 state/9 action')


def extract_diffusion(path):
    # This is inside the same bounded DP process after the real optimizer test.
    import torch
    result=read(path);directory=Path(result['checkpoint_dir']).parent/'diffusion_resume';pointer=read(directory/'latest.json')
    checkpoint=directory/pointer['checkpoint']
    if checkpoint.parent!=directory or Path(pointer['checkpoint']).name!=pointer['checkpoint'] or sha(checkpoint)!=pointer['sha256']:
        raise QualificationRejected('Actual trusted resume checkpoint identity changed')
    payload=torch.load(checkpoint,map_location='cpu',weights_only=True);metadata=payload['metadata']
    signature=hashlib.sha256(json.dumps(metadata,sort_keys=True,allow_nan=False).encode()).hexdigest()
    if signature!=payload['signature'] or signature!=pointer['signature'] or signature!=result['training_resume']['signature'] or pointer['completed_steps']!=8 or payload['completed_steps']!=8:
        raise QualificationRejected('Actual native6 resume metadata/signature/count differs')
    return dict(qualification_result=str(path),qualification_result_sha256=sha(path),resume_signature=signature,
        resume_checkpoint_sha256=pointer['sha256'],resume_pointer_sha256=sha(directory/'latest.json'),resume_metadata=metadata,
        completed_steps=8,extraction_only_no_optimizer=True)


def run_phase(plan_path,phase):
    plan=read_plan(plan_path);root=Path(plan['source_root']);stage=plan['stages'][phase]
    bound=limits()
    if Path(bound['path']).name!=stage['unit']+'.service':raise QualificationRejected('Use the declared own transient unit for this phase')
    if Path(sys.executable).absolute()!=Path(stage['executable']).absolute():raise QualificationRejected('Use the exact server-owned phase interpreter')
    if phase=='controller':raise QualificationRejected('Controller must follow a separately committed current DP binding; use the frozen existing controller qualifier then')
    assert_source(root,plan['source_commit'],plan['source_sha256']['diffusion'][STATION],plan['auxiliary_source_sha256'][GATE])
    before_code=file_hashes(root,plan['source_sha256'][phase]);aux=file_hashes(root,plan['auxiliary_source_sha256'])
    if before_code!=plan['source_sha256'][phase] or aux!=plan['auxiliary_source_sha256']:raise QualificationRejected('Frozen source changed before optimizer')
    output=Path(plan_path).resolve().parent/phase;output.mkdir(mode=0o700)
    private_config=output/'private-config.json';write(private_config,dict(data_dir=str(output/'controlcenter-private-state'),nodes=[]))
    os.environ.update(EXPLORER_CONFIG=str(private_config),HF_HUB_OFFLINE='1',HF_DATASETS_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2')
    if phase=='act':os.environ['EXPLORER_RUN_06_OPTIMIZER']='1'
    if phase=='diffusion':os.environ['EXPLORER_RUN_SO101_DIFFUSION_QUAL']='1'
    sys.path[:0]=[str(root/name) for name in ('shared','00-control-center/src','01-autonomous-learning/src','tests','01-autonomous-learning/tests')]
    before=runtime(phase);write(output/'runtime-before.json',dict(runtime=before,limits=bound,implementation_sha256=before_code,auxiliary_source_sha256=aux,source_commit=plan['source_commit'],launcher_sha256=sha(__file__)))
    import pytest
    started=time.monotonic();status=None;failure=None;result_path=None
    try:
        status=pytest.main(['-x','-q',str(root/stage['test']),'--basetemp='+str(output/'pytest')])
        if status==0:
            result_path=one_result(output,phase);result=read(result_path);validate_result(result,phase,plan['old_R2_references'].get(phase))
            if phase=='diffusion':write(output/'resume-extraction.json',extract_diffusion(result_path))
        if before!=runtime(phase) or before_code!=file_hashes(root,before_code) or aux!=file_hashes(root,aux) or sha(__file__)!=plan['orchestrator_sha256'] or git(root,'status','--porcelain'):
            raise QualificationRejected('Frozen source/runtime/orchestrator changed during qualification')
    except BaseException as error:
        failure=dict(error_type=type(error).__name__,detail=str(error));raise
    finally:
        write(output/'runtime-after.json',dict(runtime=runtime(phase),limits=bound,implementation_sha256=before_code,auxiliary_source_sha256=aux,
            pytest_exit_code=status,elapsed_s=time.monotonic()-started,failure=failure,
            qualification_result=None if result_path is None else str(result_path),
            qualification_result_sha256=None if result_path is None else sha(result_path),**resource_end(bound),production_state_writes=False,hardware_io=False))
    if status:raise SystemExit(status)
    return dict(phase=phase,pytest_exit_code=0,output=str(output),actual_result=str(result_path))


def validate_retained_act(plan_path,old_output):
    """Repair a reporting-only alias ambiguity; never rerun/relabel optimizer.

    The original actual pytest0+wrapper1 attempt stays untouched. This NEW
    receipt links its real checkpoint/runtime/source and explicitly records
    that no model was executed by the validation-only process.
    """
    plan=read_plan(plan_path);stage=plan['stages']['act'];bound=limits();old=Path(old_output).resolve()
    if Path(bound['path']).name!=stage['unit']+'.service' or Path(sys.executable).absolute()!=BASE_PYTHON.absolute():
        raise QualificationRejected('Use exact ACT validation interpreter and declared own unit')
    old_plan=read(old.parent/'plan.json');before=read(old/'runtime-before.json');after=read(old/'runtime-after.json')
    if (old_plan.get('source_commit')!=plan['source_commit'] or old_plan.get('source_root')!=plan['source_root'] or
            old_plan.get('source_sha256')!=plan['source_sha256'] or old_plan.get('auxiliary_source_sha256')!=plan['auxiliary_source_sha256'] or
            old_plan['stages']['act']['test']!=TESTS['act'] or old_plan['stages']['act']['parameters']!=PARAMETERS['act']):
        raise QualificationRejected('Retained actual ACT source/recipe is not the same frozen computation')
    old_script=old.parent/'orchestrator-source.py'
    if sha(old_script)!=old_plan['orchestrator_sha256'] or before['launcher_sha256']!=old_plan['orchestrator_sha256']:
        raise QualificationRejected('Retained wrapper source identity is unavailable')
    if (after.get('pytest_exit_code')!=0 or (after.get('failure') or {}).get('error_type')!='QualificationRejected' or
            (after.get('failure') or {}).get('detail')!='Expected one exact actual act result, found 2' or before['runtime']!=after['runtime']):
        raise QualificationRejected('Only the known reporting-only duplicate-result failure can be revalidated')
    root=assert_source(plan['source_root'],plan['source_commit'],plan['source_sha256']['diffusion'][STATION],plan['auxiliary_source_sha256'][GATE])
    code=file_hashes(root,plan['source_sha256']['act']);aux=file_hashes(root,plan['auxiliary_source_sha256'])
    if code!=before['implementation_sha256'] or aux!=before['auxiliary_source_sha256'] or runtime('act')!=before['runtime']:
        raise QualificationRejected('Actual retained source/runtime no longer matches')
    path=one_result(old,'act');result=read(path);validate_result(result,'act',plan['old_R2_references']['act'])
    checkpoint=Path(result['checkpoint_dir']).resolve()
    if not checkpoint.is_relative_to(old):raise QualificationRejected('Retained checkpoint escaped original actual attempt')
    for name,expected in result['checkpoint_sha256'].items():
        if Path(name).name!=name or sha(checkpoint/name)!=expected:raise QualificationRejected('Retained real checkpoint/processor changed')
    output=Path(plan_path).resolve().parent/'act';output.mkdir(mode=0o700)
    receipt=dict(schema='explorer_retained_actual_ACT_artifact_validation_v1',source_commit=plan['source_commit'],
        implementation_sha256=code,auxiliary_source_sha256=aux,actual_runtime=runtime('act'),limits=bound,**resource_end(bound),
        original_actual_result=str(path),original_actual_result_sha256=sha(path),original_actual_optimizer_steps=8,
        original_actual_pytest_exit_code=0,original_reporting_wrapper_exit_code=1,
        original_runtime_before_sha256=sha(old/'runtime-before.json'),original_runtime_after_sha256=sha(old/'runtime-after.json'),
        original_wrapper_source_sha256=sha(old_script),original_attempt_unchanged=True,
        actual_model_or_optimizer_executed_this_process=False,artifact_alias_resolved_within_original_basetemp=True,
        model_weights_equal_R2=True,reload_max_abs_error=0,physical_io=False,production_state_writes=False)
    write(output/'retained-actual-validation.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--commit');parser.add_argument('--station-sha256');parser.add_argument('--gate-sha256');parser.add_argument('--label')
    parser.add_argument('--plan',type=Path);parser.add_argument('--phase',choices=tuple(TESTS));parser.add_argument('--validate-retained-act',type=Path)
    args=parser.parse_args()
    if args.phase:
        if args.plan is None:parser.error('--phase requires existing immutable --plan')
        if args.validate_retained_act is not None:
            if args.phase!='act':parser.error('Retained artifact repair is ACT-only')
            result=validate_retained_act(args.plan,args.validate_retained_act)
        else:result=run_phase(args.plan,args.phase)
    else:
        if any(value is None for value in (args.source_root,args.output,args.commit,args.station_sha256,args.gate_sha256,args.label)):
            parser.error('plan requires source-root/output/fullcommit/finalStationSHA/finalGateSHA/newlabel')
        result=prepare_plan(args.source_root,args.output,commit=args.commit,station_sha256=args.station_sha256,gate_sha256=args.gate_sha256,label=args.label)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
