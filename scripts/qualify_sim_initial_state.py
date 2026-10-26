#!/usr/bin/env python3
"""ENGINEERING_GLUE: finite CPU CAD initial-state replay in a new PID.

No renderer, policy, physical device or archived Python program is used. These
are new software fixtures, never amendments of any historical V9 experiment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import random
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from explorer_shared.sim_initial_state import (InitialStateRejected,canonical,capture_initial_trial,
    restore_initial_trial,save_initial_bundle,load_initial_bundle,digest,_runtime)


def write(path,value):
    with path.open('xb') as stream:stream.write(canonical(value))


def provider():
    from explorer_sim.manipulation_env import ManipulationEnv
    from explorer_shared.skill_transfer_mujoco import MuJoCoTargetProvider
    from legacy.simulator.explorer_model import resolve_mesh_dir,ROOT
    root=Path(__file__).resolve().parents[1]
    mesh=resolve_mesh_dir();env=ManipulationEnv(mesh,'candy_to_tray',horizon=32)
    expected=json.loads((ROOT/'config/arm_model.json').read_bytes())['asset_sha256']
    assets={mesh/Path(name).name:sha for name,sha in expected.items() if name.endswith('.STL')}
    result=MuJoCoTargetProvider(env,target_urdf_sha256=digest((ROOT/'config/explorer.urdf').read_bytes()),mesh_files=assets,phase_evidence_map={'approach':['approach_proximity']})
    return result


def rng_values(target):
    import random
    return dict(env=target.env.__dict__['_np_random'].standard_normal(8).tolist(),numpy=np.random.standard_normal(8).tolist(),python=[random.random() for _ in range(8)])


def execute(target,commands):
    states=[target.env.integration_state().copy()];reports=[]
    for command in commands:
        target.advance(command,phase_id='approach');states.append(target.env.integration_state().copy());reports.append(target.report())
    return np.asarray(states),reports


def resource_snapshot():
    group=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
    root=Path('/sys/fs/cgroup')/group.lstrip('/')
    return dict(pid=os.getpid(),cgroup=group,
        limits={name:(root/name).read_text().strip() if (root/name).is_file() else None for name in ('memory.max','memory.current','memory.peak','memory.swap.max','memory.swap.peak','memory.events','cpu.max')},
        process_status=[line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith(('VmHWM:','VmRSS:'))])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--child',action='store_true');parser.add_argument('--sha');parser.add_argument('--model-metadata-only',action='store_true')
    args=parser.parse_args();output=args.output.resolve();target=None;started=time.monotonic();owns_output=args.child
    try:
        if not args.child:output.mkdir(mode=0o700,parents=True,exist_ok=False);owns_output=True
        resources=resource_snapshot();write(output/('child-resources-start.json' if args.child else 'resources-start.json'),resources)
        if resources['limits']['memory.max']!='2147483648' or resources['limits']['memory.swap.max']!='0' or resources['limits']['cpu.max']!='100000 100000':
            raise RuntimeError('Actual own cgroup CPU1/Memory2GiB/Swap0 limits required before CAD allocation')
        if not args.child:random.seed(713);np.random.seed(619)
        target=provider();target.reset(seed=919 if args.child else 417)
        if args.model_metadata_only:
            import mujoco
            embedded=mujoco.mj_embedAssets(target.env.model)
            metadata=dict(schema='explorer_initial_compiled_model_size_preflight_v1',compiled_MJB_bytes=int(mujoco.mj_sizeModel(embedded)),
                original_model_MJB_bytes=int(mujoco.mj_sizeModel(target.env.model)),CAD_asset_files=len(target.mesh_files),
                CAD_asset_bytes=sum(Path(name).stat().st_size for name in target.mesh_files),
                XML_bytes=len(target.env.xml.encode()),native_state_bytes=int(target.env.integration_state().nbytes),
                model_binary_buffer_allocated=False,embedded_model_copy_created=True,renderer_created=target.env.renderer is not None,
                physical_io=False,elapsed_s=time.monotonic()-started)
            write(output/'model-size.json',metadata);print(json.dumps(metadata,sort_keys=True));return
        sources={'qualification_driver':dict(path=str(Path(__file__).resolve()),sha256=digest(Path(__file__).read_bytes()))}
        if args.child:
            snapshot=load_initial_bundle(output/'initial',expected_sha256=args.sha)
            write(output/'child-runtime-before-restore.json',_runtime())
            receipt=restore_initial_trial(target,snapshot,expected_sha256=args.sha,expected_source_bindings=sources)
            write(output/'child-initial-restore.json',receipt);write(output/'child-rng.json',rng_values(target))
            commands=json.loads((output/'commands.json').read_bytes());states,reports=execute(target,commands)
            np.save(output/'child-states.npy',states,allow_pickle=False);write(output/'child-reports.json',reports)
        else:
            # Both snapshot capture and a failed preflight must preserve actual
            # generator state and native simulation bytes exactly.
            before=target.env.integration_state().tobytes();rng_before=canonical(target.env.__dict__['_np_random'].bit_generator.state)
            target.env.unregistered_profile_field={'must':'reject'}
            try:capture_initial_trial(target,scenario=dict(seed=417),source_bindings=sources)
            except InitialStateRejected:unknown_rejected=True
            else:raise AssertionError('Unknown mutable profile accepted')
            del target.env.unregistered_profile_field
            snapshot=capture_initial_trial(target,scenario=dict(seed=417,kind='NEW_SOFTWARE_FIXTURE'),source_bindings=sources)
            assert before==target.env.integration_state().tobytes() and rng_before==canonical(target.env.__dict__['_np_random'].bit_generator.state)
            save_initial_bundle(snapshot,output/'initial');write(output/'parent-rng.json',rng_values(target))
            current=target.env.command.copy();commands=[]
            for index in range(12):
                command=current.copy();command[0]+=(-1 if index%2 else 1)*2;command[1]-=min(index,4);commands.append(command.tolist()+[0.,0.,0.])
            write(output/'commands.json',commands);states,reports=execute(target,commands)
            np.save(output/'parent-states.npy',states,allow_pickle=False);write(output/'parent-reports.json',reports)
            child=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--output',str(output),'--child','--sha',snapshot.sha256],capture_output=True,text=True,timeout=45)
            (output/'child.stdout').write_text(child.stdout);(output/'child.stderr').write_text(child.stderr)
            if child.returncode:raise RuntimeError('New-PID initial restore failed; exact failure retained')
            actual=np.load(output/'child-states.npy',allow_pickle=False)
            exact=states.dtype==actual.dtype and states.shape==actual.shape and states.tobytes()==actual.tobytes()
            max_error=float(np.max(np.abs(states-actual)))
            rng_equal=(output/'parent-rng.json').read_bytes()==(output/'child-rng.json').read_bytes()
            report_equal=(output/'parent-reports.json').read_bytes()==(output/'child-reports.json').read_bytes()
            result=dict(schema='explorer_stationary_sim_initial_new_pid_qualification_v1',snapshot_sha256=snapshot.sha256,
                source_helper_sha256=digest((Path(__file__).resolve().parents[1]/'shared/explorer_shared/sim_initial_state.py').read_bytes()),
                actual_CAD_model=True,commands_reexecuted=len(commands),native_shape=list(states.shape),native_dtype=states.dtype.str,
                native_states_bitwise_equal=exact,maximum_native_error=max_error,independent_reports_equal=report_equal,RNG_draws_equal=rng_equal,
                unknown_profile_rejected_before_mutation=unknown_rejected,capture_did_not_advance_native_or_env_RNG=True,
                renderer_created=target.env.renderer is not None,initial_RGB_render_reexecution='UNQUALIFIED',
                original_runtime_OS_closure='PARTIAL',physical_io=False,learned_policy_executed=False,
                no_retrospective_V9_amendment=True,elapsed_s=time.monotonic()-started,passed=exact and rng_equal and report_equal)
            write(output/'qualification.json',result);print(json.dumps(result,sort_keys=True))
            if not result['passed']:raise RuntimeError('Actual new-PID finite-command parity did not pass')
    except Exception as error:
        if owns_output:
            failure=output/('child-failure.json' if args.child else 'failure.json')
            if not failure.exists():write(failure,dict(error_type=type(error).__name__,detail=str(error),physical_io=False))
        raise
    finally:
        try:
            if owns_output:write(output/('child-resources-final.json' if args.child else 'resources-final.json'),resource_snapshot())
        finally:
            if target is not None:target.env.close()


if __name__=='__main__':main()
