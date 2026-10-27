#!/usr/bin/env python3
"""ENGINEERING_GLUE: trusted-current-code NEW-PID portable V10 replay proof.

Preparation copies/hash-checks data only. A fresh child forbids Python opens in
the old producer tree, loads embedded MJB without recompiling recorded paths,
restores the unchanged registered profile, and compares exact native/contact
bytes. Archived Python payloads are never imported or executed.
"""
from __future__ import annotations
import argparse
import gc
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback
import uuid

from explorer_shared.contact_diagnosis import canonical, sha, _relative

REPO=Path(__file__).resolve().parents[1]
MAX_PAYLOAD=256*1024**2


class PortableProofRejected(ValueError):pass


def hash_file(path,maximum=MAX_PAYLOAD):
    path=Path(path);before=path.stat(follow_symlinks=False)
    if path.is_symlink() or path.resolve()!=path.absolute() or not stat.S_ISREG(before.st_mode) or before.st_size>maximum:
        raise PortableProofRejected('Bounded nonaliased regular portable input required')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        opened=os.fstat(stream.fileno());digest=hashlib.file_digest(stream,'sha256').hexdigest();after=os.fstat(stream.fileno())
    def identity(value):return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns
    if any(identity(value)!=identity(before) for value in (opened,after,path.stat(follow_symlinks=False))):
        raise PortableProofRejected('Portable source changed during bounded hash')
    return dict(sha256=digest,bytes=before.st_size)


def read_json(path,expected=None):
    descriptor=hash_file(path,16*1024**2)
    if expected is not None and descriptor['sha256']!=expected:raise PortableProofRejected('Portable JSON SHA differs')
    return json.loads(Path(path).read_bytes())


def write_new(path,value):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())


def copy_exact(source,target,expected,maximum=MAX_PAYLOAD):
    descriptor=hash_file(source,maximum)
    if descriptor['sha256']!=expected:raise PortableProofRejected('Copied source SHA differs')
    target=Path(target);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with Path(source).open('rb') as src,target.open('xb') as dest:
        shutil.copyfileobj(src,dest,1024**2);dest.flush();os.fsync(dest.fileno())
    if hash_file(target,maximum)!=descriptor or hash_file(source,maximum)!=descriptor:
        raise PortableProofRejected('Exact portable copy/source recheck differs')
    return descriptor


def trusted_sources(commit,expected):
    if not re.fullmatch('[0-9a-f]{40}',commit):raise PortableProofRejected('Explicit current trusted40hex Git commit required')
    if subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()!=commit:
        raise PortableProofRejected('Current trusted Git commit differs')
    if subprocess.check_output(['git','-C',str(REPO),'status','--porcelain'],text=True).strip():
        raise PortableProofRejected('Trusted tree must be clean')
    actual={}
    for relative,checksum in expected.items():
        _relative(relative);path=REPO/relative;raw=subprocess.check_output(['git','-C',str(REPO),'show',commit+':'+relative])
        if sha(raw)!=checksum or hash_file(path)['sha256']!=checksum:raise PortableProofRejected('Trusted current Git source bytes differ')
        actual[relative]=checksum
    relative=Path(__file__).resolve().relative_to(REPO).as_posix()
    if sha(subprocess.check_output(['git','-C',str(REPO),'show',commit+':'+relative]))!=hash_file(__file__)['sha256']:
        raise PortableProofRejected('Qualifier itself must be committed trusted current code')
    return actual


def prepare(producer,report_sha,output,commit,case_id,all_cases=False):
    producer=Path(producer).absolute();output=Path(output).absolute()
    if output.is_relative_to(producer) or producer.is_relative_to(output):
        raise PortableProofRejected('New portable root must not overlap original producer artifacts')
    report=read_json(producer/'independent-report.json',report_sha)
    protocol=read_json(producer/'frozen-protocol.json',report['protocol_sha256'])
    old_repo=Path(next(path for path in protocol['sources'] if path.endswith('/scripts/run_contact_probes.py'))).parents[1]
    expected_sources={Path(path).relative_to(old_repo).as_posix():checksum for path,checksum in protocol['sources'].items()}
    trusted_sources(commit,expected_sources)
    selected=report['attempts'] if all_cases else [entry for entry in report['attempts'] if entry['attempt_id']==case_id]
    if len(selected)!=(16 if all_cases else 1):raise PortableProofRejected('One exact retained attempt or all16 required')
    output.mkdir(mode=0o700,parents=True,exist_ok=False);cases=[]
    for entry in selected:
        identity=_relative(entry['attempt_id']);original=producer/'attempts'/identity;folder=output/'copies'/identity
        result=read_json(original/'result.json',entry['result_sha256']);snapshot=result['initial_state']
        if snapshot.get('status')!='RECORDED':raise PortableProofRejected('Actual registered snapshot required')
        doc=read_json(original/'initial-state/manifest.json',entry['initial_snapshot']['manifest_sha256'])
        if doc['payloads']!=snapshot['payloads']:raise PortableProofRejected('Original registered payload inventories disagree')
        total=0
        for name,payload in doc['payloads'].items():
            total+=payload['bytes']
            if total>MAX_PAYLOAD:raise PortableProofRejected('Registered payload total bound differs')
            copy_exact(original/'initial-state'/_relative(name),folder/'initial-state'/_relative(name),payload['sha256'])
        copy_exact(original/'initial-state/manifest.json',folder/'initial-state/manifest.json',snapshot['manifest_sha256'],16*1024**2)
        assets={}
        for name,asset in doc['assets'].items():
            if Path(name).name!=name or not name.endswith('.STL'):raise PortableProofRejected('Canonical recorded STL basename required')
            path=folder/'relocated-robot-geometry'/name
            copy_exact(folder/'initial-state'/_relative(asset['file']),path,asset['sha256'],64*1024**2);assets[str(path)]=asset['sha256']
        references={}
        for name in ('native-states.npy','substeps.jsonl'):
            checksum=entry['raw_artifacts'][name]['sha256'];destination=folder/'reference'/name
            references[name]=dict(path=str(destination),**copy_exact(original/name,destination,checksum,64*1024**2))
        copy_exact(original/'result.json',folder/'reference/result.json',entry['result_sha256'],16*1024**2)
        condition=next(item for item in protocol['conditions'] if item['id']==result['condition'])
        cases.append(dict(attempt_id=identity,folder=str(folder),manifest_sha256=snapshot['manifest_sha256'],assets=assets,
            references=references,original_result_sha256=entry['result_sha256'],condition=condition,
            original_clock_domain=doc['original_sim_clock_domain']))
    plan=dict(schema='explorer_portable_contact_numeric_plan_v1',trusted_commit=commit,trusted_current_sources=expected_sources,
        qualifier_sha256=hash_file(__file__)['sha256'],producer_report_sha256=report_sha,producer_protocol_sha256=report['protocol_sha256'],
        output_root=str(output),forbidden_original_roots=[str(producer)],cases=cases,physical_io=False,learned_policy=False,
        source_code_policy='execute_only_current_trusted_Git_sources_never_archived_payload_python',
        exact_numeric_and_contact_comparison=True,tolerance_relaxation_allowed=False,
        portable_C_open_trace='strace_if_installed_else_UNMEASURED',original_OS_closure='PARTIAL',
        resources=dict(cpu=1,memory_max=2*1024**3,swap_max=0,EGL=False,GPU=False,max_wall_s=360))
    write_new(output/'portable-plan.json',plan)
    return dict(plan_path=str(output/'portable-plan.json'),plan_sha256=sha(canonical(plan)),cases=len(cases),CAD_loaded=False,dynamics_steps=0)


def resources():
    group=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];root=Path('/sys/fs/cgroup')/group.lstrip('/')
    return dict(pid=os.getpid(),cgroup=group,values={name:(root/name).read_text().strip() for name in ('cpu.max','memory.max','memory.current','memory.peak','memory.swap.max','memory.events')})


def guard(plan):
    info=resources();values=info['values'];quota,period=values['cpu.max'].split()
    if quota=='max' or int(quota)>int(period) or values['memory.max']=='max' or int(values['memory.max'])>plan['resources']['memory_max'] or values['memory.swap.max']!='0':
        raise PortableProofRejected('Own CPU1/2GiB/swap0 cgroup required before CAD')
    return info


def load_plan(output,checksum):
    plan=read_json(Path(output)/'portable-plan.json',checksum)
    if plan.get('schema')!='explorer_portable_contact_numeric_plan_v1' or plan['output_root']!=str(Path(output).absolute()):
        raise PortableProofRejected('Exact new portable plan root/schema required')
    trusted_sources(plan['trusted_commit'],plan['trusted_current_sources'])
    if plan['qualifier_sha256']!=hash_file(__file__)['sha256']:raise PortableProofRejected('Frozen qualifier SHA differs')
    return plan


def audit_originals(roots):
    roots=[Path(root).resolve() for root in roots];blocked=[]
    def audit(event,args):
        if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
            path=Path(os.fsdecode(args[0])).absolute().resolve()
            if any(path.is_relative_to(root) for root in roots):
                blocked.append(str(path));raise PermissionError('Portable verifier forbids Python open in original producer artifacts')
    sys.addaudithook(audit)
    return blocked


def current_driver(plan):
    path=REPO/'scripts/run_contact_probes.py'
    if hash_file(path)['sha256']!=plan['trusted_current_sources']['scripts/run_contact_probes.py']:
        raise PortableProofRejected('Original trusted-current SIM loop source differs')
    spec=importlib.util.spec_from_file_location('trusted_current_contact_loop',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def build_portable(case,driver):
    import mujoco
    import numpy as np
    from legacy.simulator.explorer_model import ARM_JOINTS, ROOT as LEGACY
    from explorer_sim.manipulation_env import ManipulationEnv,FullTaskEvaluator
    from explorer_shared.skill_transfer_mujoco import MuJoCoTargetProvider
    folder=Path(case['folder']);doc=read_json(folder/'initial-state/manifest.json',case['manifest_sha256'])
    xml=(folder/'initial-state/model.xml').read_bytes()
    if sha(xml)!=doc['payloads']['model.xml']['sha256']:raise PortableProofRejected('Exact recorded XML bytes required')
    mesh=Path(next(iter(case['assets']))).parent
    env=ManipulationEnv(mesh,'candy_to_tray',horizon=32)
    model=driver.release_then_compile(env,lambda:mujoco.MjModel.from_binary_path(str(folder/'initial-state/compiled-model.mjb')))
    env.model=model;env.data=mujoco.MjData(model);env.xml=xml.decode()
    env.qids=np.array([model.joint(name).qposadr[0] for name in ARM_JOINTS]);env.dids=np.array([model.joint(name).dofadr[0] for name in ARM_JOINTS]);env.joint_q=env.qids
    env.servo_ids=np.array([model.actuator('servo'+str(i)).id for i in range(1,7)])
    env.pad_ids=[model.geom(name).id for name in ('finger_left','finger_right')]
    env.object_id=model.body('candy').id;env.object_geom_ids={model.geom(name).id for name in ('candy_contact','candy_wrapper_-1','candy_wrapper_1')}
    env.support_ids={model.geom('bin_bottom').id};env.goal=model.body('bin').pos.copy();env.half=model.geom('bin_bottom').size[:2].copy();env.rim=.044
    mujoco.mj_forward(model,env.data)
    env.evaluator=FullTaskEvaluator(env.points(),env.goal,env.half,env.rim,model.opt.timestep)
    env.steps=0;env.substep_trace=[];env.record_substeps=False;env.last_distance=float(np.linalg.norm(env.jaw_center()-env.points().mean(0)))
    source_role=doc['source_bindings']['explorer_urdf3']
    if hash_file(LEGACY/'config/explorer.urdf')['sha256']!=source_role['sha256']:raise PortableProofRejected('Current trusted CAD URDF differs')
    target=MuJoCoTargetProvider(env,target_urdf_sha256=source_role['sha256'],mesh_files=case['assets'],phase_evidence_map={'new_contact_probe':['settled_in_goal']})
    target.boot='portable-contact-'+uuid.uuid4().hex
    # Known official NumPy save API loads the same current IO implementation used
    # before original capture. No archived module names are auto-imported.
    np.save(io.BytesIO(),np.array([0.],dtype=np.float64),allow_pickle=False)
    return target,doc


def relocated_bindings(case,doc):
    folder=Path(case['folder'])/'initial-state';result={}
    for role,entry in doc['source_bindings'].items():
        path=folder/_relative(entry['file'])
        if hash_file(path)['sha256']!=entry['sha256']:raise PortableProofRejected('Relocated source payload SHA differs')
        result[role]=dict(path=str(path),sha256=entry['sha256'])
    return result


def compare_native(expected,actual):
    import numpy as np
    left=np.load(expected,mmap_mode='r',allow_pickle=False);right=np.load(actual,mmap_mode='r',allow_pickle=False)
    if left.dtype!=np.dtype('float64') or left.dtype!=right.dtype or left.shape!=right.shape:
        return dict(bitwise_equal=False,reason='NATIVE_ABI_DIFFERS',expected_shape=list(left.shape),actual_shape=list(right.shape))
    different=left.view(np.uint64)!=right.view(np.uint64)
    return dict(bitwise_equal=not bool(different.any()),different_float64_words=int(different.sum()),shape=list(left.shape),dtype=left.dtype.str,
        maximum_absolute_numeric_difference=float(np.max(np.abs(left-right))))


def verify_case(plan,index):
    guard(plan);case=plan['cases'][index];folder=Path(case['folder']);result_root=Path(plan['output_root'])/'replay'/case['attempt_id']
    result_root.mkdir(mode=0o700,parents=True,exist_ok=False);started=time.monotonic();setters=[];target=None
    report=dict(schema='explorer_portable_contact_numeric_case_v1',attempt_id=case['attempt_id'],status='STARTED',physical_io=False,
        learned_policy_executed=False,full_task_success_claimed=False,resources_before=resources())
    blocked=audit_originals(plan['forbidden_original_roots'])
    try:
        positive=Path(plan['forbidden_original_roots'][0])/'frozen-protocol.json'
        try:
            with positive.open('rb'):raise AssertionError('Old producer positive-control opened')
        except PermissionError:pass
        if len(blocked)!=1:raise PortableProofRejected('Original source-open guard positive control failed')
        blocked.clear();report['Python_open_positive_control_blocked']=True
        driver=current_driver(plan);target,doc=build_portable(case,driver)
        from explorer_shared import sim_initial_state as initial
        import mujoco
        expected_runtime=doc['runtime'];actual_runtime=initial._runtime()
        report['runtime_preflight']=dict(equal=actual_runtime==expected_runtime,
            missing_modules=sorted(set(expected_runtime['modules_sha256'])-set(actual_runtime['modules_sha256'])),
            extra_modules=sorted(set(actual_runtime['modules_sha256'])-set(expected_runtime['modules_sha256'])))
        bundle=initial.load_initial_bundle(folder/'initial-state',expected_sha256=case['manifest_sha256'])
        original_set=mujoco.mj_setState
        def counted_set(*args,**kwargs):setters.append(True);return original_set(*args,**kwargs)
        mujoco.mj_setState=counted_set
        try:receipt=initial.restore_initial_trial(target,bundle,expected_sha256=case['manifest_sha256'],expected_source_bindings=relocated_bindings(case,doc))
        finally:mujoco.mj_setState=original_set
        report['restore_receipt']=receipt;del bundle;gc.collect()
        report['discarded_current_constructor_native_steps']=100
        progress=dict(native_steps=0,native_states_recorded=0)
        outcome,steps=driver.simulate(target,case['condition'],result_root,progress)
        original_result=read_json(folder/'reference/result.json',case['original_result_sha256'])
        native=compare_native(case['references']['native-states.npy']['path'],result_root/'native-states.npy')
        actual_contact=hash_file(result_root/'substeps.jsonl',64*1024**2)
        contact_equal=actual_contact=={key:case['references']['substeps.jsonl'][key] for key in ('sha256','bytes')}
        score_equal=canonical(outcome)==canonical(original_result['outcome'])
        report.update(native=native,contacts_solver_JSONL_bitwise_equal=contact_equal,contact_reference=case['references']['substeps.jsonl'],
            contact_actual=actual_contact,contact_score_bitwise_equal=score_equal,outcome=outcome,native_steps=steps,
            actual_caller_replay_clock=target.boot,source_clock_distinct=target.boot!=case['original_clock_domain'],
            initial_XML_exact_recorded_bytes=True,recorded_XML_recompiled=False,embedded_MJB_loaded=True)
        report['status']='PASS' if native['bitwise_equal'] and contact_equal and score_equal and not blocked else 'NUMERICAL_OR_ISOLATION_DIFFERENCE'
    except Exception as error:
        report.update(status='FAILED',error_type=type(error).__name__,reason=str(error))
        with (result_root/'failure-traceback.txt').open('xb') as stream:stream.write(traceback.format_exc().encode())
    finally:
        report.update(native_state_setter_calls=len(setters),old_producer_Python_open_attempts=list(blocked),
            elapsed_s=time.monotonic()-started,resources_after=resources(),original_OS_closure='PARTIAL',
            C_open_isolation='UNMEASURED_UNLESS_EXTERNAL_STRACE_LOG_VERIFIED',RGB_reexecution=False)
        write_new(result_root/'qualification.json',report)
    return report


def coordinate(plan,plan_sha):
    guard(plan);output=Path(plan['output_root']);children=[];started=time.monotonic();tracer=shutil.which('strace')
    for index,case in enumerate(plan['cases']):
        command=[sys.executable,str(Path(__file__).resolve()),'--mode','verify','--output',str(output),'--plan-sha256',plan_sha,'--case-index',str(index)]
        trace=output/('C-file-opens-%02d.log'%index)
        if tracer:command=[tracer,'-f','-e','trace=%file','-o',str(trace)]+command
        env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1');env.pop('MUJOCO_GL',None)
        with (output/('verify-%02d.stdout'%index)).open('xb') as stdout,(output/('verify-%02d.stderr'%index)).open('xb') as stderr:
            child=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env)
            try:code=child.wait(timeout=max(1.,360.-(time.monotonic()-started)))
            except subprocess.TimeoutExpired:child.kill();child.wait();code=-9
        item=dict(index=index,pid=child.pid,exit_code=code,reaped=True)
        result=output/'replay'/case['attempt_id']/'qualification.json'
        if result.is_file():item['qualification']=read_json(result);item['qualification_sha256']=hash_file(result)['sha256']
        if tracer:
            lines=[line for line in trace.read_text().splitlines() if re.search(r'\bopen(?:at2|at)?\(',line) and any(root in line for root in plan['forbidden_original_roots'])]
            item['original_C_open_attempts']=lines;item['C_file_trace_sha256']=hash_file(trace)['sha256']
        children.append(item)
        write_new(output/('children-after-%02d.json'%index),children)
        if code or item.get('qualification',{}).get('status')!='PASS' or item.get('original_C_open_attempts'):
            break  # stop on concrete first failure; never spend full16 on a portability gap
    result=dict(schema='explorer_portable_contact_numeric_campaign_v1',plan_sha256=plan_sha,children=children,
        all_declared_cases_PASS=len(children)==len(plan['cases']) and all(item['exit_code']==0 and item.get('qualification',{}).get('status')=='PASS' and not item.get('original_C_open_attempts') for item in children),
        strace_available=bool(tracer),C_open_scope='ACTUAL_STRACE_FILE_OPENS' if tracer else 'UNMEASURED_STRACE_NOT_INSTALLED',
        current_trusted_commit=plan['trusted_commit'],elapsed_s=time.monotonic()-started,resources=resources(),physical_io=False,
        original_OS_closure='PARTIAL',learned_policy_executed=False,full_task_success_claimed=False)
    write_new(output/'qualification.json',result);print(canonical(result).decode())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=('prepare','coordinate','verify'),required=True)
    parser.add_argument('--producer',type=Path);parser.add_argument('--independent-report-sha256');parser.add_argument('--trusted-commit')
    parser.add_argument('--case-id',default='v10-v9-near-left-7332-captured-physics');parser.add_argument('--all-cases',action='store_true')
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--plan-sha256');parser.add_argument('--case-index',type=int,default=0);args=parser.parse_args()
    if args.mode=='prepare':print(canonical(prepare(args.producer,args.independent_report_sha256,args.output,args.trusted_commit,args.case_id,args.all_cases)).decode())
    else:
        plan=load_plan(args.output,args.plan_sha256)
        if args.mode=='verify':print(canonical(verify_case(plan,args.case_index)).decode())
        else:coordinate(plan,args.plan_sha256)


if __name__=='__main__':main()
