#!/usr/bin/env python3
"""ENGINEERING_GLUE: new real CAD initial capture and RGB-only diagnostic stop.

Explicit SOFTWARE_SCRIPTED_SIM source fixture, no actual source arm or target
advance. Only current injected MuJoCo provider/config APIs execute. All source
and partial attempts persist in a private Store, never production state.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

GIB=1024**3


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def write(path,value):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())


def resources():
    relative=next(line.split('::',1)[1] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    root=Path('/sys/fs/cgroup')/relative.lstrip('/')
    return dict(pid=os.getpid(),cgroup=str(root),limits={name:(root/name).read_text().strip() for name in ('memory.max','memory.swap.max','cpu.max')},
        memory_peak_bytes=int((root/'memory.peak').read_text()),swap_peak_bytes=int((root/'memory.swap.peak').read_text()),memory_events=(root/'memory.events').read_text())


def require_resources(unit):
    value=resources();quota,period=value['limits']['cpu.max'].split()
    if (Path(value['cgroup']).name!=unit+'.service' or value['limits']['memory.max']!=str(3*GIB) or
            value['limits']['memory.swap.max']!='0' or quota=='max' or int(quota)!=int(period)):
        raise ValueError('Declared own CPU1/3GiB/swap0 capture unit required before imports')
    return value


def run(root,output,commit,unit,meshes):
    started=time.monotonic();before=require_resources(unit);root=Path(root).resolve();output=Path(output).resolve();meshes=Path(meshes).resolve()
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True).strip()
    if actual!=commit or dirty or output.exists():raise ValueError('Clean exact frozen source and new immutable output required')
    output.mkdir(mode=0o700,parents=True)
    driver_sha=sha(__file__);write(output/'resources-start.json',before)
    sys.path[:0]=[str(root/name) for name in ('shared','00-control-center/src','01-autonomous-learning/src','scripts')]
    os.environ.update(EXPLORER_SIM_MESH_DIR=str(meshes),MUJOCO_GL='egl',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',HF_HUB_OFFLINE='1')
    names=['scripts/qualify_sim_initial_state.py','shared/explorer_shared/sim_initial_state.py','shared/explorer_shared/skill_transfer.py',
        'shared/explorer_shared/skill_transfer_mujoco.py','01-autonomous-learning/src/explorer_sim/manipulation_env.py',
        '00-control-center/src/explorer/initial_state_export.py','00-control-center/src/explorer/research_export_job.py']
    source_map={name:sha(root/name) for name in names}
    write(output/'preflight.json',dict(source_commit=commit,source_root=str(root),implementation_sha256=source_map,driver_sha256=driver_sha,
        mesh_directory=str(meshes),physical_io=False,production_state_writes=False))
    from explorer.store import Store
    from explorer_shared.skill_transfer import load_source_trace,initial_state_capture_config,record_target_trial,SCHEMA
    from explorer_shared.so101_station import NAMES
    from explorer_shared.transfer_mapping04 import TransferBlocked
    from explorer_shared.experiments import experiment_id
    store=Store(output/'state');source=store.create_job('software_initial_capture_qualification',experiment_id('cross_embodiment_transfer'),
        dict(purpose='capture_smoke',source_kind='SOFTWARE_SCRIPTED_SIM',_execution_commit=commit,experiment_numbering_version=2,physical_io=False))
    store.update_job(source['id'],status='running');folder=store.root/'jobs'/source['id'];assert not folder.exists()
    source_folder=store.root/'artifacts/capture-smoke-source';source_folder.mkdir(parents=True)
    (source_folder/'capture-smoke-driver.py').write_bytes(Path(__file__).read_bytes())
    proof=dict(schema='explorer_explicit_source_fixture_v1',fixture_domain='SOFTWARE_SCRIPTED_SIM',method='SCRIPTED_SIM',closed_loop_learned_source=False,
        actual_source_arm_executed=False,object_expert_qualified=False,full_task_success=False,physical_io=False)
    write(source_folder/'scripted-provider.json',proof)
    phases=['approach','grasp','lift','transport','place_verify'];skill_id=str(uuid.uuid4())
    trace=dict(schema=SCHEMA,experiment_slug='so101_source_learning',source_kind='SCRIPTED_SIM',task_type='pick_object_to_container',
        skill_id=skill_id,skill_version=1,action_contract_id='so101_follower_calibrated_position6_v1',action_names=list(NAMES),
        split=dict(partition='development',episode_id='new-capture-'+source['id'],scene_id='diagnostic-candy-CAD',session_id='session-'+source['id'],group_id='group-'+source['id']),
        source_files_sha256={p.name:sha(p) for p in source_folder.iterdir() if p.is_file()},
        frames=[dict(native_action6=[0,0,0,0,0,50],phase_id=phase,reference='goal',source_kind='SCRIPTED_SIM') for phase in phases])
    write(source_folder/'source-trace.json',trace);trace_path=source_folder/'source-trace.json';bundle=load_source_trace(trace_path,sha(trace_path))
    capture_config=initial_state_capture_config(source_bindings=dict(capture_smoke_driver=dict(path=str(source_folder/'capture-smoke-driver.py'),sha256=driver_sha)),
        scenario=dict(seed=417,purpose='new_initial_capture_and_one_RGB_before_controlled_diagnostic_stop',source_fixture='SOFTWARE_SCRIPTED_SIM'))
    spec=dict(skill_id=skill_id,version=2,stages=[dict(id=phase) for phase in phases],diagnostic_only=True,complete_skill_claim=False)
    class StopMapper:
        lineage=dict(method='SOFTWARE_FIXTURE_DIAGNOSTIC_STOP',learned_policy=False,geometric_transfer_executed=False)
        def plan(self,frame,observation):raise TransferBlocked('POST_CAPTURE_SOFTWARE_DIAGNOSTIC_STOP','A')
    target=None;failure=None
    try:
        specifier=importlib.util.spec_from_file_location('current_sim_initial_qualifier',root/'scripts/qualify_sim_initial_state.py')
        module=importlib.util.module_from_spec(specifier);specifier.loader.exec_module(module)
        target=module.provider()
        result=record_target_trial(bundle,StopMapper(),target,folder,target_version=2,target_skill_spec=spec,seed=417,max_steps=1,initial_state_capture=capture_config)
        # Literal producer result is persisted unchanged, including intentional
        # later failure and its real automatically RECORDED initial snapshot.
        store.update_job(source['id'],status='failed',result=result,error='POST_CAPTURE_SOFTWARE_DIAGNOSTIC_STOP (controlled software qualification)')
        current=store.job(source['id']);assert current['result']==result
        assert result['initial_state']['status']=='RECORDED' and result['failure']['code']=='POST_CAPTURE_SOFTWARE_DIAGNOSTIC_STOP'
        assert result['frames']==0 and not result['full_target_simulation_success'] and not result['eligible_for_target_dataset'] and not result['optimizer_partition_allowed']
        rgb=list((folder/'rgb').glob('*.png'));observations=list((folder/'observations').glob('*.json'))
        assert len(rgb)==len(observations)==1 and not (folder/'dispatch').exists() and not (folder/'requests').exists() and target.env.steps==0
        from PIL import Image
        with Image.open(rgb[0]) as image:shape=[image.height,image.width,3];assert image.mode=='RGB'
        initial=result['initial_state'];manifest=Path(initial['manifest_path']);assert sha(manifest)==initial['manifest_sha256']
        files={str(p.relative_to(folder)):dict(sha256=sha(p),size_bytes=p.stat().st_size) for p in folder.rglob('*') if p.is_file()}
        assert all(sha(manifest.parent/name)==entry['sha256'] and (manifest.parent/name).stat().st_size==entry['bytes'] for name,entry in initial['payloads'].items())
        report=dict(schema='explorer_recorded_initial_capture_smoke_v1',source_commit=commit,driver_sha256=driver_sha,implementation_sha256=source_map,
            source_job_id=source['id'],source_job_status='failed',private_store_root=str(store.root),source_fixture='SOFTWARE_SCRIPTED_SIM',source_partition='development',
            actual_MuJoCo_CAD_provider=True,initial_state_status='RECORDED',initial_manifest_sha256=initial['manifest_sha256'],
            compiled_MJB_bytes=initial['payloads']['compiled-model.mjb']['bytes'],CAD_assets=len(json.loads(manifest.read_bytes())['assets']),
            actual_target_RGB_frames=1,RGB_shape=shape,target_advance_calls=0,target_finite_steps=0,
            controlled_post_capture_failure=result['failure'],actual_result_sha256=sha(folder/'result.json'),job_files=files,
            full_target_success=False,learned_policy_executed=False,numerical_reexecution_verified=False,RGB_reexecution_verified=False,
            original_runtime_OS_closure='PARTIAL',physical_io=False,production_state_writes=False,elapsed_s=time.monotonic()-started)
        assert source_map=={name:sha(root/name) for name in names} and driver_sha==sha(__file__)
        write(output/'capture-qualification.json',report)
        print(json.dumps({key:report[key] for key in ('source_job_id','initial_state_status','compiled_MJB_bytes','CAD_assets','actual_target_RGB_frames','target_advance_calls','elapsed_s')}))
        return report
    except BaseException as error:
        failure=dict(error_type=type(error).__name__,detail=str(error));write(output/'capture-failure.json',failure);raise
    finally:
        write(output/'resources-final.json',dict(resources(),failure=failure))
        if target is not None:target.env.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('source-root','output','commit','unit','meshes'):parser.add_argument('--'+key,required=True)
    a=parser.parse_args();run(a.source_root,a.output,a.commit,a.unit,a.meshes)
