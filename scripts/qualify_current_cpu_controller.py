#!/usr/bin/env python3
"""ENGINEERING_GLUE: finite private ACT/DP supervisor process-resume proof.

This exercises unchanged existing official8 tests against a clean NEW source
commit with committed measured DP binding. No native node/hardware is present.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

import qualify_current_cpu_sources as q

SOURCE_PROFILES={'legacy':'tests/test_official_queue_worker_pause.py',
    'approved_capture':'tests/test_so101_approved_capture_queue.py'}


def typed_exact(left,right):
    import torch
    if isinstance(left,torch.Tensor):
        if not isinstance(right,torch.Tensor) or left.dtype!=right.dtype or left.layout!=right.layout or left.shape!=right.shape or not torch.equal(left,right):
            raise q.QualificationRejected('Exact tensor dtype/layout/shape/values differ')
    elif isinstance(left,dict):
        if not isinstance(right,dict) or left.keys()!=right.keys():raise q.QualificationRejected('Exact state dictionary differs')
        for key in left:typed_exact(left[key],right[key])
    elif isinstance(left,(list,tuple)):
        if type(left)!=type(right) or len(left)!=len(right):raise q.QualificationRejected('Exact state sequence differs')
        for a,b in zip(left,right):typed_exact(a,b)
    elif type(left)!=type(right) or left!=right:raise q.QualificationRejected('Exact scalar state differs')


def verify_actual(case,result):
    import torch
    def payload(identity):
        directory=case/'state/jobs'/identity/('act_resume' if result['backend']=='act' else 'diffusion_resume')
        pointer=q.read(directory/'latest.json')
        if Path(pointer['checkpoint']).name!=pointer['checkpoint'] or q.sha(directory/pointer['checkpoint'])!=pointer['sha256'] or pointer['completed_steps']!=8:
            raise q.QualificationRejected('Actual controller durable checkpoint changed')
        return torch.load(directory/pointer['checkpoint'],map_location='cpu',weights_only=True)
    left=payload(result['reference_job_id']);right=payload(result['resumed_job_id'])
    for key in ('model','optimizer','losses','sampler'):typed_exact(left[key],right[key])
    typed_exact(left['rng']['torch_cpu'],right['rng']['torch_cpu'])
    return True


def run(root,output,commit,station,gate,unit,source_profile='legacy'):
    if source_profile not in SOURCE_PROFILES:raise q.QualificationRejected('Fixed native6 source qualification profile required')
    bound=q.limits()
    if Path(bound['path']).name!=unit+'.service' or Path(sys.executable).absolute()!=q.BASE_PYTHON.absolute():
        raise q.QualificationRejected('Declared own controller unit/base interpreter required')
    root=q.assert_source(root,commit,station,gate);output=Path(output).resolve()
    if output.exists():raise q.QualificationRejected('New private controller output required')
    names=q.ADMISSION+q.COMMON+q.EXTRA_ACT+q.EXTRA_DP+q.CONTROLLER+(q.GATE,'conftest.py','tests/test_lab_skill_workflow.py','tests/test_skill_workflow.py','tests/test_embodied_episode.py')
    if source_profile=='approved_capture':
        names+=('tests/test_so101_approved_capture_queue.py','tests/test_so101_approved_capture_cohort.py',
            'tests/test_so101_capture_adapter.py','04-so101-source-learning/src/explorer_so101/capture_adapter.py',
            '04-so101-source-learning/src/explorer_so101/capture_archive.py','shared/explorer_shared/sealed_capture_review.py')
    source=q.file_hashes(root,names);output.mkdir(mode=0o700,parents=True)
    q.write(output/'private-config.json',dict(data_dir=str(output/'private-initial-state'),nodes=[]))
    os.environ.update(EXPLORER_CONFIG=str(output/'private-config.json'),EXPLORER_RUN_OFFICIAL_QUEUE_PAUSE='1',HF_HUB_OFFLINE='1',HF_DATASETS_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2')
    if source_profile=='approved_capture':os.environ['EXPLORER_RUN_APPROVED_SOURCE04_QUEUE']='1'
    sys.path[:0]=[str(root/name) for name in ('shared','00-control-center/src','01-autonomous-learning/src','04-so101-source-learning/src','tests','01-autonomous-learning/tests')]
    from explorer.policy_runtime import qualified_diffusion_runtime
    selector=qualified_diffusion_runtime(root);runtime=q.runtime('act');script_sha=q.sha(__file__);recipe_sha=q.sha(q.__file__)
    active=root/'shared/training/DIFFUSION_NATIVE6_ACTIVE_BINDING.json';index=q.read(active)
    bindings={str(active.relative_to(root)):q.sha(active)}
    for value in (index['source_binding'],index['qualification_result'],index['implementation_binding']):
        path=active.parent/value;bindings[str(path.relative_to(root))]=q.sha(path)
    q.write(output/'preflight.json',dict(source_commit=commit,source_profile=source_profile,implementation_sha256=source,binding_files_sha256=bindings,actual_runtime=runtime,selector=selector,limits=bound,launcher_sha256=script_sha,recipe_sha256=recipe_sha,physical_io=False,production_state_writes=False))
    import pytest
    started=time.monotonic();status=None;failure=None;results={}
    try:
        status=pytest.main(['-x','-q',str(root/SOURCE_PROFILES[source_profile]),'--basetemp='+str(output/'pytest')])
        if status==0:
            candidates={path.resolve() for path in (output/'pytest').rglob('qualification.json')}
            for path in candidates:
                if not path.is_relative_to((output/'pytest').resolve()):raise q.QualificationRejected('Controller result escaped own basetemp')
                result=q.read(path)
                if result.get('schema')=='explorer_official_native6_queue_pause_qualification_v1':
                    backend=result['backend']
                    if backend in results or backend not in ('act','diffusion') or result.get('source_commit')!=commit or result.get('source_profile','legacy')!=source_profile:
                        raise q.QualificationRejected('Unexpected/duplicate controller proof')
                    if not all(result.get(key) is True for key in ('old_process_gone','actual_supervisor_bootstrap_job_runner_used','model_optimizer_losses_Torch_RNG_sampler_exactly_equal','own_Python_NumPy_RNG_checkpoint_continuation_verified','immutable_source_bytes_preserved')) or result.get('completed_updates')!=8 or result.get('physical_io') is not False:
                        raise q.QualificationRejected('Actual exact process-exit/new-PID evidence missing')
                    results[backend]=dict(result_path=str(path),result_sha256=q.sha(path),typed_tensor_equality_verified=verify_actual(path.parent,result),result=result)
            if set(results)!={'act','diffusion'}:raise q.QualificationRejected('Both actual ACT and DP process proofs required')
        if (runtime!=q.runtime('act') or selector!=qualified_diffusion_runtime(root) or source!=q.file_hashes(root,source) or
                bindings!=q.file_hashes(root,bindings) or script_sha!=q.sha(__file__) or recipe_sha!=q.sha(q.__file__) or q.git(root,'status','--porcelain')):
            raise q.QualificationRejected('Controller source/runtime/binding changed during proof')
    except BaseException as error:
        failure=dict(error_type=type(error).__name__,detail=str(error));raise
    finally:
        q.write(output/'runtime-after.json',dict(schema='explorer_current_station_CPU_controller_qualification_v1',source_commit=commit,source_profile=source_profile,
            implementation_sha256=source,binding_files_sha256=bindings,actual_runtime=q.runtime('act'),selector=selector,
            limits=bound,launcher_sha256=script_sha,recipe_sha256=recipe_sha,pytest_exit_code=status,failure=failure,results=results,elapsed_s=time.monotonic()-started,
            **q.resource_end(bound),physical_io=False,production_state_writes=False,task_success_claimed=False))
    if status:raise SystemExit(status)
    return dict(output=str(output),backends=sorted(results),pytest_exit_code=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('source-root','output','commit','station-sha256','gate-sha256','unit'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--source-profile',choices=tuple(SOURCE_PROFILES),default='legacy')
    a=parser.parse_args();print(q.canonical(run(a.source_root,a.output,a.commit,a.station_sha256,a.gate_sha256,a.unit,a.source_profile)).decode())
