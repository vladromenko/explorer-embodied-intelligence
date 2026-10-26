"""Isolated scheduler decisions; signals and dispatch are intercepted, no devices."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from explorer.scheduler import Supervisor,recover_jobs,queue_status
from explorer.store import Store
from explorer.queue_policy import GIB
from explorer_shared.learning.checkpoint_control import worker_identity,atomic_json


class Process:
    pid=os.getpid()
    returncode=None
    def poll(self):return self.returncode
    def wait(self,timeout=None):self.returncode=0;return 0


def rig(tmp_path,monkeypatch):
    release=tmp_path/('a'*40);release.mkdir();(release/'RELEASE_COMMIT').write_text(release.name)
    marker=release/'00-control-center/src/explorer/worker_bootstrap.py';marker.parent.mkdir(parents=True);marker.write_text('software dispatch fixture never imported')
    store=Store(tmp_path/'state');clock=[100.];signals=[];started=[]
    monkeypatch.setattr('explorer.scheduler.os.killpg',lambda pid,which:signals.append((pid,which)))
    monkeypatch.setattr('explorer.scheduler.os.sched_getaffinity',lambda pid:set(range(8)))
    def popen(*args,**kwargs):
        value=Process();started.append((value,args,kwargs));return value
    host=dict(available_memory_bytes=14*GIB,free_disk_bytes=40*GIB,cpu_count=8,load_1m=0,thermal=[])
    supervisor=Supervisor(dict(root=str(release)),store,clock=lambda:clock[0],wall=lambda:clock[0],resource_provider=lambda:host,popen=popen)
    return supervisor,store,clock,signals,started


def test_shared_supervisor_dispatches_parallel_budgeted_CPU_jobs_and_records_only_side_table_waits(tmp_path,monkeypatch):
    supervisor,store,clock,signals,started=rig(tmp_path,monkeypatch)
    jobs=[store.create_job('so101_skill_train','04',dict(image_size=64,batch_size=2)) for _ in range(4)]
    result=supervisor.tick();assert len(started)==3 and len(result['start'])==3
    pending=next(job for job in jobs if store.job(job['id'])['status']=='queued')
    current=store.job(pending['id']);assert current['result'] is None and current['scheduling']['runtime']['wait_reasons']==['cpu_concurrency_budget_exhausted']
    assert len({cpu for _,_,values in started for cpu in values['preexec_fn'].__closure__[0].cell_contents})==6
    assert not signals


def test_unsupported_manual_pause_is_explicit_memory_retained_and_keeps_lease_until_resume(tmp_path,monkeypatch):
    supervisor,store,clock,signals,started=rig(tmp_path,monkeypatch)
    job=store.create_job('world_model','01',{})
    supervisor.tick();store.update_schedule_runtime(job['id'],phase='running',bootstrap_admitted=True)
    store.request_job_operation(job['id'],'pause');supervisor.tick()
    paused=store.job(job['id']);assert paused['status']=='paused' and signals==[(os.getpid(),signal.SIGSTOP)]
    runtime=paused['scheduling']['runtime'];assert runtime['phase']=='paused_process_memory_retained' and runtime['memory_retained'] and not runtime['exact_optimizer_resume']
    store.request_job_operation(job['id'],'resume');supervisor.tick()
    assert store.job(job['id'])['status']=='running' and signals[-1]==(os.getpid(),signal.SIGCONT)


def test_lost_or_invalid_pause_receipt_fails_only_one_job_and_does_not_claim_resume(tmp_path,monkeypatch):
    supervisor,store,clock,signals,started=rig(tmp_path,monkeypatch)
    job=store.create_job('so101_skill_train','04',dict(image_size=64,batch_size=2));supervisor.tick()
    store.update_schedule_runtime(job['id'],phase='running',bootstrap_admitted=True,pause_request=dict(worker=worker_identity(),request_id='missing'))
    store.update_job(job['id'],status='pause_requested');started[0][0].returncode=75
    supervisor.tick();current=store.job(job['id'])
    assert current['status']=='failed' and current['scheduling']['runtime']['phase']=='pause_or_exit_proof_rejected'
    assert not current['scheduling']['runtime']['memory_retained'] and not signals


def test_request_after_last_optimizer_poll_falls_back_honestly_instead_of_hanging_or_replaying(tmp_path,monkeypatch):
    supervisor,store,clock,signals,started=rig(tmp_path,monkeypatch)
    job=store.create_job('so101_skill_train','04',dict(image_size=64,batch_size=2));supervisor.tick()
    worker=worker_identity();request=dict(worker=worker,signature='a'*64,backend='act',request_id='b'*32)
    folder=store.root/'jobs'/job['id'];atomic_json(folder/'optimizer-active.json',dict(schema='explorer_optimizer_checkpoint_control_v1',worker=worker,backend='act',signature='a'*64,phase='optimizer_finished'))
    atomic_json(folder/'optimizer-control.json',request)
    store.update_schedule_runtime(job['id'],phase='waiting_for_actual_optimizer_checkpoint',bootstrap_admitted=True,pause_request=request,pause_mode='checkpoint_process_exit')
    store.request_job_operation(job['id'],'pause');supervisor.tick()
    runtime=store.schedule_status(job['id'])['runtime']
    assert store.job(job['id'])['status']=='paused' and runtime['phase']=='paused_process_memory_retained'
    assert runtime['pause_request'] is None and not (folder/'optimizer-control.json').exists()
    assert signals==[(os.getpid(),signal.SIGSTOP)]


def test_next_optimizer_in_same_process_rebinds_pending_pause_and_preserves_automatic_resume(tmp_path,monkeypatch):
    supervisor,store,clock,signals,started=rig(tmp_path,monkeypatch)
    job=store.create_job('so101_skill_train','04',dict(image_size=64,batch_size=2));supervisor.tick()
    store.update_schedule_runtime(job['id'],phase='running',bootstrap_admitted=True)
    folder=store.root/'jobs'/job['id'];body=dict(schema='explorer_optimizer_checkpoint_control_v1',worker=worker_identity(),backend='act',signature='a'*64,phase='optimizing')
    atomic_json(folder/'optimizer-active.json',body)
    supervisor._pause(store.job(job['id']),started[0][0],automatic=True,reason='fairshare_quantum')
    first=store.schedule_status(job['id'])['runtime']['pause_request']
    atomic_json(folder/'optimizer-active.json',dict(body,signature='b'*64));supervisor.tick()
    runtime=store.schedule_status(job['id'])['runtime']
    assert runtime['pause_request']['signature']=='b'*64 and runtime['pause_request']['request_id']!=first['request_id']
    assert runtime['auto_resume'] and not signals


def test_queue_API_preview_does_not_create_metadata_or_rewrite_job_bodies(tmp_path,monkeypatch):
    supervisor,store,clock,signals,started=rig(tmp_path,monkeypatch)
    job=store.create_job('so101_skill_shadow','04',{})
    before=store.job(job['id'])
    view=queue_status(store,supervisor.resource_provider())
    assert view['owner']=='shared_training_platform' and view['authoritative_table']=='jobs' and view['read_only_preview']
    assert not view['jobs'][0]['scheduling']['persisted'] and store.job(job['id'])==before
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM job_scheduling').fetchone()[0]==0


def test_actual_surviving_isolated_worker_is_adopted_without_duplicate_or_source_config_mutation(tmp_path):
    store=Store(tmp_path/'state');job=store.create_job('world_model','01',{});store.update_job(job['id'],status='running')
    process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)','explorer.job_runner',job['id']],start_new_session=True)
    try:
        store.update_schedule_runtime(job['id'],worker=worker_identity(process.pid),phase='running',memory_retained=True)
        before=store.job(job['id'])['config'];adopted=recover_jobs(store)
        assert list(adopted)==[job['id']] and adopted[job['id']].poll() is None
        assert store.job(job['id'])['status']=='running' and store.job(job['id'])['config']==before
    finally:process.terminate();process.wait(timeout=5)


def test_bootstrap_requires_durable_nonce_and_can_pause_before_any_job_import(tmp_path):
    store=Store(tmp_path/'state');job=store.create_job('unsupported_software_fixture','01',{})
    store.claim_job_start(job['id']);store.update_job(job['id'],status='pause_requested')
    config=tmp_path/'private-config.json';config.write_text(json.dumps(dict(data_dir=str(store.root),nodes=[])))
    root=Path(__file__).parents[1];environment=dict(os.environ,EXPLORER_CONFIG=str(config),PYTHONPATH=os.pathsep.join(str(root/name) for name in ('shared','00-control-center/src')))
    nonce='a'*32
    process=subprocess.Popen([sys.executable,'-m','explorer.worker_bootstrap',job['id'],nonce],env=environment)
    try:
        store.update_schedule_runtime(job['id'],worker=worker_identity(process.pid),launch_nonce=nonce,phase='starting',bootstrap_admitted=False)
        assert process.wait(timeout=10)==77
        assert store.job(job['id'])['result'] is None
        receipt=json.loads((store.root/'jobs'/job['id']/'worker-not-started.json').read_text())
        assert receipt['launch_nonce']==nonce and receipt['phase']=='paused_before_work'
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_bootstrap_wrong_nonce_never_invokes_even_an_unsupported_job_runner(tmp_path):
    store=Store(tmp_path/'state');job=store.create_job('unsupported_software_fixture','01',{});store.claim_job_start(job['id'])
    config=tmp_path/'private-config.json';config.write_text(json.dumps(dict(data_dir=str(store.root),nodes=[])))
    root=Path(__file__).parents[1];environment=dict(os.environ,EXPLORER_CONFIG=str(config),PYTHONPATH=os.pathsep.join(str(root/name) for name in ('shared','00-control-center/src')))
    process=subprocess.Popen([sys.executable,'-m','explorer.worker_bootstrap',job['id'],'a'*32],env=environment)
    try:
        store.update_schedule_runtime(job['id'],worker=worker_identity(process.pid),launch_nonce='b'*32,phase='starting',bootstrap_admitted=False)
        assert process.wait(timeout=10)==78
        assert store.job(job['id'])['status']=='running' and store.job(job['id'])['result'] is None
        assert not store.schedule_status(job['id'])['runtime'].get('bootstrap_admitted')
    finally:
        if process.poll() is None:process.kill();process.wait()
