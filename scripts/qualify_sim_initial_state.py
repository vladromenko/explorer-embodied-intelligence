#!/usr/bin/env python3
"""ENGINEERING_GLUE: sequential producer→exit→new-PID CPU CAD replay.

The small coordinator never constructs a CAD model. No renderer, policy,
physical device or archived Python program is used. These new software fixtures
never amend historical V9 records or confer their missing initial state.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

import numpy as np
from explorer_shared.sim_initial_state import (InitialStateRejected,canonical,capture_initial_trial,
    restore_initial_trial,save_initial_bundle,load_initial_bundle,digest,_runtime,InitialStateBundle,encode,decode)


def write(path,value):
    with path.open('xb') as stream:stream.write(canonical(value))


def resource_snapshot():
    group=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
    root=Path('/sys/fs/cgroup')/group.lstrip('/')
    return dict(pid=os.getpid(),cgroup=group,
        limits={name:(root/name).read_text().strip() if (root/name).is_file() else None for name in ('memory.max','memory.current','memory.peak','memory.swap.max','memory.swap.peak','memory.events','cpu.max')},
        process_status=[line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith(('VmHWM:','VmRSS:'))])


def provider():
    from explorer_sim.manipulation_env import ManipulationEnv
    from explorer_shared.skill_transfer_mujoco import MuJoCoTargetProvider
    from legacy.simulator.explorer_model import resolve_mesh_dir,ROOT
    mesh=resolve_mesh_dir();env=ManipulationEnv(mesh,'candy_to_tray',horizon=32)
    expected=json.loads((ROOT/'config/arm_model.json').read_bytes())['asset_sha256']
    assets={mesh/Path(name).name:sha for name,sha in expected.items() if name.endswith('.STL')}
    return MuJoCoTargetProvider(env,target_urdf_sha256=digest((ROOT/'config/explorer.urdf').read_bytes()),mesh_files=assets,phase_evidence_map={'approach':['approach_proximity']})


def source_bindings():
    return {'qualification_driver':dict(path=str(Path(__file__).resolve()),sha256=digest(Path(__file__).read_bytes()))}


def rng_values(target):
    return dict(env=target.env.__dict__['_np_random'].standard_normal(8).tolist(),numpy=np.random.standard_normal(8).tolist(),python=[random.random() for _ in range(8)])


def execute(target,commands):
    states=[target.env.integration_state().copy()];reports=[]
    for command in commands:
        target.advance(command,phase_id='approach');states.append(target.env.integration_state().copy());reports.append(target.report())
    return np.asarray(states),reports


def produce(target,output):
    before=target.env.integration_state().tobytes();rng_before=canonical(target.env.__dict__['_np_random'].bit_generator.state)
    target.env.unregistered_profile_field={'must':'reject'}
    try:capture_initial_trial(target,scenario=dict(seed=417),source_bindings=source_bindings())
    except InitialStateRejected:unknown_rejected=True
    else:raise AssertionError('Unknown mutable profile accepted')
    del target.env.unregistered_profile_field
    snapshot=capture_initial_trial(target,scenario=dict(seed=417,kind='NEW_SOFTWARE_FIXTURE'),source_bindings=source_bindings())
    assert before==target.env.integration_state().tobytes() and rng_before==canonical(target.env.__dict__['_np_random'].bit_generator.state)
    save_initial_bundle(snapshot,output/'initial');write(output/'producer-rng.json',rng_values(target))
    current=target.env.command.copy();commands=[]
    for index in range(12):
        command=current.copy();command[0]+=(-1 if index%2 else 1)*2;command[1]-=min(index,4);commands.append(command.tolist()+[0.,0.,0.])
    write(output/'commands.json',commands);states,reports=execute(target,commands)
    np.save(output/'producer-states.npy',states,allow_pickle=False);write(output/'producer-reports.json',reports)
    write(output/'producer-summary.json',dict(snapshot_sha256=snapshot.sha256,pid=os.getpid(),unknown_profile_rejected_before_mutation=unknown_rejected,
        capture_did_not_advance_native_or_env_RNG=True,renderer_created=target.env.renderer is not None,
        compiled_MJB_bytes=len(snapshot.payloads['compiled-model.mjb']),total_snapshot_payload_bytes=sum(map(len,snapshot.payloads.values()))))


def verify(target,output):
    summary=json.loads((output/'producer-summary.json').read_bytes())
    snapshot=load_initial_bundle(output/'initial',expected_sha256=summary['snapshot_sha256'])
    write(output/'verifier-runtime-before-restore.json',_runtime())
    import mujoco
    original_set_state=mujoco.mj_setState;native_calls=[];cases=[]
    def observed_set_state(*args,**kwargs):
        native_calls.append(True);return original_set_state(*args,**kwargs)
    mujoco.mj_setState=observed_set_state
    try:
        for name in ('warning_overflow','command_shape','seed_shape','provider_field'):
            profile=json.loads(snapshot.payloads['profile-state.json'])
            if name=='warning_overflow':
                values=decode(profile['warnings']);values[0]['lastinfo']=2**40;profile['warnings']=encode(values)
            elif name=='command_shape':
                values=decode(profile['environment']);values['command']=np.zeros(7);profile['environment']=encode(values)
            elif name=='seed_shape':profile['env_seed']=encode(np.array([417]))
            else:
                values=decode(profile['provider']);values['unknown']='rejected';profile['provider']=encode(values)
            raw=canonical(profile);payloads=dict(snapshot.payloads);payloads['profile-state.json']=raw
            document=deepcopy(snapshot.document);document['payloads']['profile-state.json']=dict(bytes=len(raw),sha256=digest(raw))
            malformed=InitialStateBundle(document,payloads)
            before_native=target.env.integration_state().tobytes()
            before_python=canonical(encode(dict(evaluator=vars(target.env.evaluator),env_rng=target.env.__dict__['_np_random'].bit_generator.state,
                numpy_rng=np.random.get_state(),python_rng=random.getstate())))
            try:restore_initial_trial(target,malformed,expected_sha256=malformed.sha256,expected_source_bindings=source_bindings())
            except InitialStateRejected:pass
            else:raise AssertionError('Resealed malformed initial profile accepted: '+name)
            assert not native_calls and before_native==target.env.integration_state().tobytes()
            assert before_python==canonical(encode(dict(evaluator=vars(target.env.evaluator),env_rng=target.env.__dict__['_np_random'].bit_generator.state,
                numpy_rng=np.random.get_state(),python_rng=random.getstate())))
            write(output/('rejected-'+name+'-profile.json'),profile)
            cases.append(dict(case=name,valid_resealed_manifest_sha256=malformed.sha256,rejected_before_native_state_set=True,native_and_RNG_unchanged=True))
        write(output/'malformed-initial-profile-rejections.json',cases)
    finally:mujoco.mj_setState=original_set_state
    receipt=restore_initial_trial(target,snapshot,expected_sha256=summary['snapshot_sha256'],expected_source_bindings=source_bindings())
    write(output/'verifier-initial-restore.json',receipt);write(output/'verifier-rng.json',rng_values(target))
    commands=json.loads((output/'commands.json').read_bytes());states,reports=execute(target,commands)
    np.save(output/'verifier-states.npy',states,allow_pickle=False);write(output/'verifier-reports.json',reports)


def run_child(output,mode):
    command=[sys.executable,str(Path(__file__).resolve()),'--output',str(output),'--mode',mode]
    with (output/(mode+'.stdout')).open('xb') as stdout,(output/(mode+'.stderr')).open('xb') as stderr:
        child=subprocess.Popen(command,stdout=stdout,stderr=stderr)
        try:code=child.wait(timeout=40)
        except subprocess.TimeoutExpired:
            child.kill();child.wait();raise
    if code:raise RuntimeError(mode+' process failed; exact source/failure retained')
    return dict(pid=child.pid,exit_code=code,process_reaped=True)


def coordinate(output,started):
    producer=run_child(output,'produce')
    # waitpid has reaped the producer before a new native allocator/model exists.
    verifier=run_child(output,'verify')
    assert producer['pid']!=verifier['pid']
    summary=json.loads((output/'producer-summary.json').read_bytes())
    expected=np.load(output/'producer-states.npy',allow_pickle=False);actual=np.load(output/'verifier-states.npy',allow_pickle=False)
    exact=expected.dtype==actual.dtype and expected.shape==actual.shape and expected.tobytes()==actual.tobytes()
    error=float(np.max(np.abs(expected-actual)))
    rng_equal=(output/'producer-rng.json').read_bytes()==(output/'verifier-rng.json').read_bytes()
    reports_equal=(output/'producer-reports.json').read_bytes()==(output/'verifier-reports.json').read_bytes()
    report=dict(schema='explorer_stationary_sim_initial_new_pid_qualification_v1',snapshot_sha256=summary['snapshot_sha256'],
        source_helper_sha256=digest((Path(__file__).resolve().parents[1]/'shared/explorer_shared/sim_initial_state.py').read_bytes()),
        source_driver_sha256=digest(Path(__file__).read_bytes()),coordinator_pid=os.getpid(),producer=producer,verifier=verifier,
        producer_exited_before_verifier_started=True,actual_CAD_model=True,commands_reexecuted=12,native_shape=list(expected.shape),native_dtype=expected.dtype.str,
        native_states_bitwise_equal=exact,maximum_native_error=error,independent_reports_equal=reports_equal,RNG_draws_equal=rng_equal,
        unknown_profile_rejected_before_mutation=summary['unknown_profile_rejected_before_mutation'],
        capture_did_not_advance_native_or_env_RNG=summary['capture_did_not_advance_native_or_env_RNG'],
        resealed_malformed_mutable_profiles=json.loads((output/'malformed-initial-profile-rejections.json').read_bytes()),
        compiled_MJB_bytes=summary['compiled_MJB_bytes'],total_snapshot_payload_bytes=summary['total_snapshot_payload_bytes'],
        renderer_created=summary['renderer_created'],initial_RGB_render_reexecution='UNQUALIFIED',original_runtime_OS_closure='PARTIAL',
        physical_io=False,learned_policy_executed=False,no_retrospective_V9_amendment=True,
        elapsed_s=time.monotonic()-started,passed=exact and rng_equal and reports_equal)
    write(output/'qualification.json',report);print(json.dumps(report,sort_keys=True))
    if not report['passed']:raise RuntimeError('Actual new-PID finite-command parity did not pass')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--mode',choices=('coordinate','produce','verify','model-size'),default='coordinate')
    args=parser.parse_args();output=args.output.resolve();owns_output=False;target=None;started=time.monotonic()
    try:
        if args.mode in ('coordinate','model-size'):output.mkdir(mode=0o700,parents=True,exist_ok=False)
        elif not output.is_dir():raise RuntimeError('Coordinator-owned new output required')
        owns_output=True
        resources=resource_snapshot();write(output/(args.mode+'-resources-start.json'),resources)
        if resources['limits']['memory.max']!='2147483648' or resources['limits']['memory.swap.max']!='0' or resources['limits']['cpu.max']!='100000 100000':
            raise RuntimeError('Actual own cgroup CPU1/Memory2GiB/Swap0 limits required before CAD allocation')
        if args.mode=='coordinate':coordinate(output,started)
        else:
            if args.mode!='verify':random.seed(713);np.random.seed(619)
            target=provider();target.reset(seed=919 if args.mode=='verify' else 417)
            if args.mode=='produce':produce(target,output)
            elif args.mode=='verify':verify(target,output)
            else:
                import mujoco
                embedded=mujoco.mj_embedAssets(target.env.model)
                write(output/'model-size.json',dict(compiled_MJB_bytes=int(mujoco.mj_sizeModel(embedded)),CAD_asset_bytes=sum(Path(name).stat().st_size for name in target.mesh_files),physical_io=False))
    except Exception as error:
        if owns_output:
            path=output/(args.mode+'-failure.json')
            if not path.exists():write(path,dict(error_type=type(error).__name__,detail=str(error),physical_io=False))
        raise
    finally:
        try:
            if owns_output:write(output/(args.mode+'-resources-final.json'),resource_snapshot())
        finally:
            if target is not None:target.env.close()


if __name__=='__main__':main()
