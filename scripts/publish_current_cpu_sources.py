#!/usr/bin/env python3
"""ENGINEERING_GLUE: bind retained actual CPU proofs, no model/hardware execution.

Preserve failed reporting wrappers as failures. Only two exact known locator
errors may accompany successful original pytest/model results; every original
source/runtime/checkpoint is revalidated before emitting NEW public candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import qualify_current_cpu_sources as q

KNOWN_LOCATOR_FAILURES={'act':'Expected one exact actual act result, found 2',
    'fused106':'Expected one exact actual fused106 result, found 0'}


def collect(plan_path,phase,output):
    plan=q.read(plan_path);root=Path(plan['source_root']);output=Path(output).resolve()
    original=q.read(output.parent/'plan.json');before=q.read(output/'runtime-before.json');after=q.read(output/'runtime-after.json')
    for key in ('source_root','source_commit','source_sha256','auxiliary_source_sha256'):
        if original[key]!=plan[key]:raise q.QualificationRejected('Original computation differs from current frozen plan')
    if before['launcher_sha256']!=original['orchestrator_sha256'] or q.sha(output.parent/'orchestrator-source.py')!=original['orchestrator_sha256']:
        raise q.QualificationRejected('Exact original reporting source is unavailable')
    if before['runtime']!=after['runtime'] or before['implementation_sha256']!=after['implementation_sha256'] or before['auxiliary_source_sha256']!=after['auxiliary_source_sha256']:
        raise q.QualificationRejected('Actual runtime/source differs across original computation')
    if before['implementation_sha256']!=plan['source_sha256'][phase] or before['auxiliary_source_sha256']!=plan['auxiliary_source_sha256']:
        raise q.QualificationRejected('Actual computation was not the frozen source graph')
    if q.file_hashes(root,before['implementation_sha256'])!=before['implementation_sha256'] or q.file_hashes(root,before['auxiliary_source_sha256'])!=before['auxiliary_source_sha256']:
        raise q.QualificationRejected('Frozen implementation changed since actual computation')
    if after['pytest_exit_code']!=0:raise q.QualificationRejected('Original actual optimizer test failed')
    failure=after.get('failure');wrapper_status=0
    if failure is not None:
        if failure!={'error_type':'QualificationRejected','detail':KNOWN_LOCATOR_FAILURES.get(phase)}:
            raise q.QualificationRejected('Unrecognized actual/reporting failure cannot be adopted')
        wrapper_status=1
    path=q.one_result(output,phase);result=q.read(path);q.validate_result(result,phase,plan['old_R2_references'][phase])
    checkpoint=Path(result['checkpoint_dir']).resolve()
    if not checkpoint.is_relative_to(output):raise q.QualificationRejected('Checkpoint outside original attempt')
    for name,expected in result['checkpoint_sha256'].items():
        if Path(name).name!=name or q.sha(checkpoint/name)!=expected:raise q.QualificationRejected('Actual checkpoint/processor bytes changed')
    measured=before['runtime']
    if q.sha(Path(measured['executable']).resolve())!=measured['executable_sha256']:
        raise q.QualificationRejected('Actual interpreter bytes changed')
    for name,expected in measured['official_sources_sha256'].items():
        library=Path(measured['packages'][name.split('/',1)[0]]['location'])/name
        if q.sha(library)!=expected:raise q.QualificationRejected('Measured official library bytes changed')
    receipt=dict(schema='explorer_actual_CPU_artifact_revalidation_v1',phase=phase,source_commit=plan['source_commit'],
        implementation_sha256=before['implementation_sha256'],auxiliary_source_sha256=before['auxiliary_source_sha256'],
        original_result=str(path),original_result_sha256=q.sha(path),original_actual_pytest_exit_code=0,
        original_reporting_wrapper_exit_code=wrapper_status,original_reporting_failure=failure,
        original_runtime_before_sha256=q.sha(output/'runtime-before.json'),original_runtime_after_sha256=q.sha(output/'runtime-after.json'),
        original_reporting_source_sha256=q.sha(output.parent/'orchestrator-source.py'),original_unit=original['stages'][phase]['unit'],
        original_attempt_unchanged=True,actual_model_or_optimizer_executed_by_revalidator=False,
        actual_optimizer_steps=8,model_weights_equal_R2=True,reload_max_abs_error=0,physical_io=False,
        production_state_writes=False,task_success_claimed=False,
        original_limits=before['limits'],original_resource_metrics={key:after[key] for key in ('elapsed_s','process_peak_RSS_bytes','cgroup_peak_memory_bytes','cgroup_swap_peak_bytes','cgroup_memory_events')})
    return path,result,dict(before=before,after=after),receipt


def diffusion_implementation(extraction,result_path,observation,sources):
    if extraction['qualification_result_sha256']!=q.sha(result_path) or extraction['completed_steps']!=8:
        raise q.QualificationRejected('Trusted actual resume extraction/result binding changed')
    metadata=extraction['resume_metadata']
    signature=hashlib.sha256(json.dumps(metadata,sort_keys=True,allow_nan=False).encode()).hexdigest()
    result=q.read(result_path)
    directory=Path(result['checkpoint_dir']).parent/'diffusion_resume';pointer=q.read(directory/'latest.json')
    if (q.sha(directory/'latest.json')!=extraction['resume_pointer_sha256'] or pointer['completed_steps']!=8 or
            pointer['signature']!=signature or Path(pointer['checkpoint']).name!=pointer['checkpoint'] or
            pointer['sha256']!=extraction['resume_checkpoint_sha256'] or q.sha(directory/pointer['checkpoint'])!=pointer['sha256']):
        raise q.QualificationRejected('Original durable resume pointer/checkpoint changed after trusted extraction')
    if signature!=extraction['resume_signature'] or signature!=result['training_resume']['signature']:
        raise q.QualificationRejected('Trusted actual resume metadata signature changed')
    official={}
    measured=observation['before']['runtime']
    for name,value in measured['official_sources_sha256'].items():
        path=Path(measured['packages'][name.split('/',1)[0]]['location'])/name
        if q.sha(path)!=value:raise q.QualificationRejected('Measured official implementation changed')
        official[str(path)]=value
    for name,value in metadata['source_context']['diffusion_optimizer']['implementation_sha256'].items():
        if q.sha(name)!=value:raise q.QualificationRejected('Resume-bound diffusion dependency changed')
    policy=[value for name,value in official.items() if name.endswith('/lerobot/policies/diffusion/modeling_diffusion.py')]
    if policy!=[metadata['implementation_sha256']['policy']]:raise q.QualificationRejected('Resume policy does not match measured official policy')
    return dict(schema='explorer_native6_diffusion_implementation_binding_v1',qualification_result_sha256=q.sha(result_path),
        resume_signature=signature,resume_checkpoint_sha256=extraction['resume_checkpoint_sha256'],resume_pointer_sha256=extraction['resume_pointer_sha256'],
        completed_steps=8,official_implementation_sha256=official,shared_implementation_sha256=sources,
        measured_versions={name:row['version'] for name,row in measured['packages'].items()},resume_metadata=metadata)


def publish(plan_path,act_output,destination):
    plan_path=Path(plan_path);plan=q.read(plan_path);root=q.assert_source(plan['source_root'],plan['source_commit'],plan['source_sha256']['diffusion'][q.STATION],plan['auxiliary_source_sha256'][q.GATE])
    label=plan['label'];destination=Path(destination)
    if destination.exists():raise q.QualificationRejected('New publication candidate directory required')
    collected={mode:collect(plan_path,mode,Path(act_output) if mode=='act' else plan_path.parent/mode) for mode in ('act','diffusion','fused106')}
    dp_path,dp_result,dp_observation,_=collected['diffusion']
    implementation=diffusion_implementation(q.read(plan_path.parent/'diffusion/resume-extraction.json'),dp_path,dp_observation,plan['source_sha256']['diffusion'])
    destination.mkdir(mode=0o700,parents=True)
    names={}
    for mode,(path,result,observation,receipt) in collected.items():
        prefix={'act':'SO101_ACT6','diffusion':'DIFFUSION_NATIVE6','fused106':'ROSMASTER_ACT106'}[mode]+'_'+label
        names[mode]=dict(qualification=prefix+'_QUALIFICATION.json',observation=prefix+'_RUNTIME_OBSERVATION.json',validation=prefix+'_ARTIFACT_VALIDATION.json')
        with (destination/names[mode]['qualification']).open('xb') as stream:stream.write(path.read_bytes())
        q.write(destination/names[mode]['observation'],observation);q.write(destination/names[mode]['validation'],receipt)
    prefix='DIFFUSION_NATIVE6_'+label
    implementation_name=prefix+'_IMPLEMENTATION_BINDING.json';q.write(destination/implementation_name,implementation)
    before=dp_observation['before'];after=dp_observation['after'];runtime_file=q.DP_PYTHON.parent.parent/'explorer-runtime-binding.json'
    binding=dict(schema='explorer_native6_diffusion_source_qualification_binding_v1',qualification_result=str(dp_path),
        qualification_result_sha256=q.sha(dp_path),actual_runtime_executable=str(q.DP_PYTHON),runtime_binding_sha256=q.sha(runtime_file),
        implementation_sha256=plan['source_sha256']['diffusion'],actual_source_commit=plan['source_commit'],
        actual_scope='Actual strict native6 CPU officialDataset + Diffusion4/durable restart4 after final Station publication gate freeze; SIMULATION only; process restart qualified separately',
        runtime_observation=names['diffusion']['observation'],runtime_observation_sha256=q.sha(destination/names['diffusion']['observation']),
        cgroup_limits_verified=before['limits'],process_peak_RSS_bytes=after['process_peak_RSS_bytes'],cgroup_peak_memory_bytes=after['cgroup_peak_memory_bytes'],
        cgroup_memory_events=after['cgroup_memory_events'],public_queue_integration_qualified=False,physical_io=False,physical_rollout_qualified=False,production_state_writes=False)
    binding_name=prefix+'_SOURCE_BINDING.json';q.write(destination/binding_name,binding)
    q.write(destination/'DIFFUSION_NATIVE6_ACTIVE_BINDING.json',dict(schema='explorer_native6_diffusion_active_binding_v1',source_binding=binding_name,
        qualification_result=names['diffusion']['qualification'],implementation_binding=implementation_name))
    summary=dict(schema='explorer_current_station_CPU_qualification_summary_v1',source_commit=plan['source_commit'],label=label,
        original_plan=str(plan_path),original_plan_sha256=q.sha(plan_path),publisher_sha256=q.sha(__file__),
        formal_DP_changes_from_R2=plan['formal_DP_changes_from_R2'],auxiliary_source_sha256=plan['auxiliary_source_sha256'],
        profiles={mode:row[3] for mode,row in collected.items()},publication_files_sha256={p.name:q.sha(p) for p in destination.iterdir() if p.is_file()},
        reporting_wrapper_failures_retained=True,actual_new_PID_controller_qualification_in_this_receipt=False,
        physical_io=False,production_state_writes=False,task_success_claimed=False)
    q.write(destination/('STATION_CPU_'+label+'_SUMMARY.json'),summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True,type=Path);parser.add_argument('--act-output',required=True,type=Path);parser.add_argument('--destination',required=True,type=Path)
    args=parser.parse_args();summary=publish(args.plan,args.act_output,args.destination)
    print(json.dumps(dict(source_commit=summary['source_commit'],profiles={key:dict(model_weights_equal_R2=value['model_weights_equal_R2'],original_reporting_wrapper_exit_code=value['original_reporting_wrapper_exit_code']) for key,value in summary['profiles'].items()}),sort_keys=True))
