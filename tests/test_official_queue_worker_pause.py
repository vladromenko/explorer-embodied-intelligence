"""Explicit isolated official ACT worker/supervisor proof, never production IO."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time

import psutil
import pytest
import torch

from explorer import lab_skill_workflow as flow
from explorer.scheduler import Supervisor
from explorer_shared.learning.checkpoint_control import active_optimizer

ROOT=Path(__file__).parents[1]


def same(left,right):
    if isinstance(left,torch.Tensor):assert torch.equal(left,right)
    elif isinstance(left,dict):
        assert left.keys()==right.keys()
        for key in left:same(left[key],right[key])
    elif isinstance(left,(tuple,list)):
        assert len(left)==len(right)
        for a,b in zip(left,right):same(a,b)
    else:assert left==right


def pump(supervisor,predicate,timeout=80):
    began=time.monotonic()
    while not predicate():
        if time.monotonic()-began>timeout:raise TimeoutError('Isolated official worker did not reach its durable state')
        supervisor.tick();time.sleep(.02)


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_OFFICIAL_QUEUE_PAUSE')!='1',reason='Explicit private Store/official ACT8 workers, not repeated broad-suite work')
@pytest.mark.parametrize('backend', ['act', 'diffusion'])
def test_actual_official_native6_supervisor_bootstrap_pause_process_exit_restart_and_full_equality(tmp_path,monkeypatch,backend):
    run_official_queue_pause(tmp_path,monkeypatch,backend)


def run_official_queue_pause(tmp_path,monkeypatch,backend,*,cohort_factory=None):
    """One real process/resume proof, reusable with an approved source producer."""
    if cohort_factory is None:
        from test_lab_skill_workflow import full_cohort
        cohort_factory=full_cohort
    store,chosen,sources=cohort_factory(tmp_path);spec=chosen['spec'];identity=spec['skill_id']
    aggregate=flow.build_aggregate(store,identity,1)
    original_bytes={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for _,manifest,*_ in sources for path in manifest.parent.iterdir() if path.is_file()}
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip();release=tmp_path/'releases'/commit;release.mkdir(parents=True)
    archive=tmp_path/'source.tar'
    with archive.open('wb') as stream:subprocess.run(['git','-C',str(ROOT),'archive',commit],stdout=stream,check=True)
    with tarfile.open(archive) as stream:stream.extractall(release,filter='data')
    (release/'RELEASE_COMMIT').write_text(commit+'\n')
    private_config=tmp_path/'private-config.json';private_config.write_text(json.dumps(dict(data_dir=str(store.root),nodes=[])))
    monkeypatch.setenv('EXPLORER_CONFIG',str(private_config));monkeypatch.setenv('HF_HUB_OFFLINE','1')
    supervisor=Supervisor(dict(root=str(release)),store,quantum_s=3600)
    body=dict(act_steps=8,batch_size=2,image_size=32,chunk_size=4,device='cpu',backend=backend)
    if backend == 'diffusion':
        from explorer_shared.learning.diffusion_backend import PROFILE_ID
        body.update(profile_id=PROFILE_ID,image_size=64)
        body.pop('chunk_size')
    reference=flow.enqueue_training(store,identity,1,dict(body,request_id='isolated-reference'))
    worker=None
    try:
        pump(supervisor,lambda:store.job(reference['id'])['status'] in ('completed','failed') and reference['id'] not in supervisor.processes)
        assert store.job(reference['id'])['status']=='completed',store.job(reference['id'])
        target=flow.enqueue_training(store,identity,1,dict(body,request_id='isolated-paused'))
        folder=store.root/'jobs'/target['id']
        def optimizer_started():
            process=supervisor.processes.get(target['id'])
            active=active_optimizer(folder,process.pid) if process else None
            return active is not None and active['completed_steps']>=1
        pump(supervisor,optimizer_started)
        worker=supervisor.processes[target['id']];old_pid=worker.pid;resident=psutil.Process(old_pid).memory_info().rss
        store.request_job_operation(target['id'],'pause');supervisor.tick()
        pump(supervisor,lambda:store.job(target['id'])['status']=='paused' and target['id'] not in supervisor.processes)
        paused=store.schedule_status(target['id'])['runtime'];step=paused['checkpoint_pause_receipt']['completed_steps']
        assert 1<=step<8 and paused['phase']=='paused_checkpoint_process_exited' and paused['exact_optimizer_resume']
        assert not paused['memory_retained'] and not psutil.pid_exists(old_pid) and resident>0
        resume_directory='act_resume' if backend == 'act' else 'diffusion_resume'
        pause_directory=folder/resume_directory;pause_pointer=json.loads((pause_directory/'latest.json').read_text())
        pause_payload=torch.load(pause_directory/pause_pointer['checkpoint'],weights_only=True,map_location='cpu')
        store.request_job_operation(target['id'],'resume')
        pump(supervisor,lambda:store.job(target['id'])['status'] in ('completed','failed') and target['id'] not in supervisor.processes)
        actual=store.job(target['id']);expected=store.job(reference['id'])
        assert actual['status']=='completed',actual
        assert actual['result']['training_resume']['resumed_from_step']==step
        assert actual['result']['training_resume']['optimizer_steps_executed_this_process']==8-step
        assert actual['result']['optimizer_steps']==8 and actual['result']['save_reload_passed']
        assert actual['result']['checkpoint_sha256']['model.safetensors']==expected['result']['checkpoint_sha256']['model.safetensors']
        def payload(job):
            directory=store.root/'jobs'/job['id']/resume_directory;pointer=json.loads((directory/'latest.json').read_text())
            assert hashlib.sha256((directory/pointer['checkpoint']).read_bytes()).hexdigest()==pointer['sha256']
            return torch.load(directory/pointer['checkpoint'],weights_only=True,map_location='cpu')
        ref_payload,actual_payload=payload(reference),payload(target)
        for key in ('model','optimizer','losses','sampler'):same(ref_payload[key],actual_payload[key])
        same(ref_payload['rng']['torch_cpu'],actual_payload['rng']['torch_cpu'])
        # Independent processes initialize unused Python/NumPy RNG differently;
        # continuation must restore this paused job's own exact states.
        for key in ('python','numpy'):same(pause_payload['rng'][key],actual_payload['rng'][key])
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected_sha for path,expected_sha in original_bytes.items())
        result=dict(schema='explorer_official_native6_queue_pause_qualification_v1',source_commit=commit,source_kind='SIMULATION',backend=backend,
            reference_job_id=reference['id'],resumed_job_id=target['id'],paused_pid=old_pid,paused_at_completed_step=step,
            old_process_gone=True,old_resident_bytes=resident,completed_updates=8,updates_in_resumed_process=8-step,
            official_ACT_used=backend=='act',official_Diffusion_used=backend=='diffusion',actual_supervisor_bootstrap_job_runner_used=True,model_optimizer_losses_Torch_RNG_sampler_exactly_equal=True,
            own_Python_NumPy_RNG_checkpoint_continuation_verified=True,
            immutable_source_bytes_preserved=True,aggregate_manifest_sha256=aggregate['aggregate_manifest_sha256'],
            model_weights_sha256=actual['result']['checkpoint_sha256']['model.safetensors'],saved_official_processors_executed=actual['result'].get('official_saved_processors_executed',False),
            checkpoint_pause_receipt=paused['checkpoint_pause_receipt'],physical_io=False,production_state_writes=False,task_success_claimed=False)
        (tmp_path/'qualification.json').write_text(json.dumps(result,indent=2))
    finally:
        for process in supervisor.processes.values():
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
