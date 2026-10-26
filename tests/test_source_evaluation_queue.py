"""Source evaluation queue fences only; no real policy/transport/device calls."""
import os
from pathlib import Path
import signal
import sys
from types import ModuleType

import pytest

from explorer import job_runner,scheduler,worker_bootstrap
from explorer.queue_policy import GIB,PHYSICAL_JOBS,INFERENCE_JOBS,default_metadata,queue_plan,validate_options
from explorer.store import Store
from explorer_shared.learning.checkpoint_control import worker_identity
from test_supervisor_checkpoint_queue import rig


def test_native_source_eval_has_explicit_CPU_inference_physical_budget_and_shared_slot(tmp_path):
    store=Store(tmp_path/'state');job=store.create_job('so101_skill_eval','04',dict(backend='act',device='cpu'))
    metadata=default_metadata(job)
    assert job['kind'] in PHYSICAL_JOBS and job['kind'] in INFERENCE_JOBS
    assert metadata['resources']==dict(cpu_cores=2,memory_bytes=2*GIB,disk_bytes=GIB//4,gpu_slots=0)
    assert not metadata['preemptible'] and metadata['backend']=='act'
    with pytest.raises(ValueError,match='preemptible'):validate_options(job,metadata,dict(preemptible=True))
    shadow=store.create_job('so101_skill_shadow','04',{});store.update_job(shadow['id'],status='running');shadow=store.job(shadow['id'])
    active=[dict(job_id=shadow['id'],resources=default_metadata(shadow)['resources'],resident_memory_bytes=GIB)]
    rows=[job,shadow];schedules={row['id']:default_metadata(row) for row in rows}
    host=dict(available_memory_bytes=12*GIB,free_disk_bytes=20*GIB,cpu_count=8,thermal=[])
    result=queue_plan(rows,schedules,host,active)
    assert 'single_interactive_inference_budget_occupied' in result['waiting'][job['id']]['reasons']


@pytest.mark.parametrize('status',['queued','running','paused','pause_requested','resume_requested','cancel_requested'])
def test_all_old_nonterminal_physical_evaluations_cancel_on_supervisor_recovery_without_replay(tmp_path,monkeypatch,status):
    store=Store(tmp_path/'state');job=store.create_job('so101_skill_eval','04',dict(backend='act',source_model='original'))
    store.update_job(job['id'],status=status);before=store.job(job['id'])['config']
    signals=[];monkeypatch.setattr(scheduler.os,'killpg',lambda *args:signals.append(args))
    assert scheduler.recover_jobs(store)=={}
    current=store.job(job['id'])
    assert current['status']=='cancelled' and current['config']==before and current['result'] is None
    assert 'fresh explicit observed physical attempt' in current['error'] and not signals


@pytest.mark.parametrize('status',['completed','failed','cancelled'])
def test_terminal_native_evaluation_evidence_is_not_rewritten_on_recovery(tmp_path,status):
    store=Store(tmp_path/'state');job=store.create_job('so101_skill_eval','04',{'backend':'act'})
    store.update_job(job['id'],status=status,result={'original':'preserved'});before=store.job(job['id'])
    assert scheduler.recover_jobs(store)=={} and store.job(job['id'])==before


def test_live_old_physical_process_is_never_adopted_and_termination_is_intercepted(tmp_path,monkeypatch):
    store=Store(tmp_path/'state');job=store.create_job('so101_skill_eval','04',{})
    store.update_job(job['id'],status='running');identity=worker_identity()
    store.update_schedule_runtime(job['id'],worker=identity,phase='running',memory_retained=True)
    class FakeProcess:
        def cmdline(self):return ['python','-m','explorer.worker_bootstrap',job['id']]
    monkeypatch.setattr(scheduler.psutil,'Process',lambda pid:FakeProcess())
    signals=[];monkeypatch.setattr(scheduler.os,'killpg',lambda *values:signals.append(values))
    assert scheduler.recover_jobs(store)=={}
    assert store.job(job['id'])['status']=='cancelled'
    assert signals==[(identity['pid'],signal.SIGCONT),(identity['pid'],signal.SIGTERM)]


def test_user_pause_of_running_physical_eval_requests_cancel_without_SIGSTOP(tmp_path,monkeypatch):
    supervisor,store,clock,signals,started=rig(tmp_path,monkeypatch)
    job=store.create_job('so101_skill_eval','04',{'backend':'act','device':'cpu'})
    supervisor.tick();store.update_schedule_runtime(job['id'],phase='running',bootstrap_admitted=True)
    for operation in ('pause','resume'):
        with pytest.raises(ValueError,match='never pause/resume'):store.request_job_operation(job['id'],operation)
    assert store.job(job['id'])['status']=='running' and not signals
    # A retained historical pause intent also follows the existing physical
    # cancellation path; it cannot SIGSTOP a physical authorization in RAM.
    store.update_job(job['id'],status='pause_requested');supervisor.tick()
    assert store.job(job['id'])['status']=='cancel_requested'
    assert signal.SIGSTOP not in [which for pid,which in signals]


def test_prestart_cancel_does_not_create_job_folder_import_transport_or_run_callback(tmp_path,monkeypatch):
    store=Store(tmp_path/'state');job=store.create_job('so101_skill_eval','04',{})
    store.request_job_operation(job['id'],'cancel')
    monkeypatch.setattr(job_runner,'load',lambda:dict(data_dir=str(store.root),nodes=[]))
    monkeypatch.delenv('EXPLORER_WORKER_LEASE_NONCE',raising=False)
    job_runner.main(job['id'])
    assert store.job(job['id'])['status']=='cancelled' and not (store.root/'jobs'/job['id']).exists()


def test_cancel_at_bootstrap_admission_is_rechecked_before_any_source_eval_job_callback(tmp_path,monkeypatch):
    store=Store(tmp_path/'state');job=store.create_job('so101_skill_eval','04',{})
    store.claim_job_start(job['id']);nonce='a'*32
    store.update_schedule_runtime(job['id'],worker=worker_identity(),launch_nonce=nonce,phase='starting',bootstrap_admitted=False)
    monkeypatch.setattr(worker_bootstrap,'load',lambda:dict(data_dir=str(store.root)))
    monkeypatch.setattr(worker_bootstrap,'Store',lambda unused:store)
    called=[];monkeypatch.setattr(job_runner,'main',lambda *args:called.append(args))
    update=store.update_schedule_runtime
    def interleave(identity,**values):
        result=update(identity,**values)
        if values.get('bootstrap_admitted'):store.request_job_operation(identity,'cancel')
        return result
    monkeypatch.setattr(store,'update_schedule_runtime',interleave)
    worker_bootstrap.main(job['id'],nonce)
    assert not called and store.job(job['id'])['status']=='cancelled' and store.job(job['id'])['result'] is None


def test_source_eval_DP_uses_same_qualified_runtime_and_pinned_binding_as_training(tmp_path,monkeypatch):
    from explorer import policy_runtime
    store=Store(tmp_path/'state');release=tmp_path/'release';calls=[]
    def qualified(path):calls.append(path);return dict(executable='/qualified/dp/python',binding_sha256='a'*64)
    monkeypatch.setattr(policy_runtime,'qualified_diffusion_runtime',qualified)
    for kind in ('so101_skill_train','so101_skill_eval'):
        job=store.create_job(kind,'04',dict(backend='diffusion',device='cpu'))
        assert scheduler.executable_for(job,release,store)=='/qualified/dp/python'
        assert store.job(job['id'])['config']['_policy_runtime_binding_sha256']=='a'*64
    assert calls==[release,release]
    wrong=store.create_job('so101_skill_eval','04',dict(backend='diffusion',_policy_runtime_binding_sha256='b'*64))
    before=store.job(wrong['id'])['config']
    with pytest.raises(ValueError,match='Pinned policy runtime differs'):scheduler.executable_for(wrong,release,store)
    assert store.job(wrong['id'])['config']==before


@pytest.mark.parametrize('config',[{'backend':'act','device':'cuda'},{'backend':'unknown'},{'backend':None}])
def test_unqualified_GPU_or_unknown_source_backend_cannot_launch(config):
    with pytest.raises(ValueError,match='CPU-only|backend'):scheduler.executable_for(dict(kind='so101_skill_eval',config=config))


def test_wrong_DP_binding_blocks_supervisor_before_any_process_or_transport(tmp_path,monkeypatch):
    from explorer import policy_runtime
    supervisor,store,clock,signals,started=rig(tmp_path,monkeypatch)
    job=store.create_job('so101_skill_eval','04',dict(backend='diffusion',_policy_runtime_binding_sha256='b'*64))
    monkeypatch.setattr(policy_runtime,'qualified_diffusion_runtime',lambda release:dict(executable='/qualified/dp/python',binding_sha256='a'*64))
    supervisor.tick()
    assert store.job(job['id'])['status']=='failed' and not started and not signals
    assert 'Pinned policy runtime differs' in store.job(job['id'])['error']


def test_lazy_transport_hook_installs_interrupt_handlers_and_passes_original_runtime_config(tmp_path,monkeypatch):
    store=Store(tmp_path/'state');job=store.create_job('so101_skill_eval','04',{'backend':'act'})
    store.update_job(job['id'],status='running');runtime=dict(data_dir=str(store.root),nodes=[])
    monkeypatch.setattr(job_runner,'load',lambda:runtime);monkeypatch.setattr(worker_bootstrap,'prework_guard',lambda *args:True)
    callbacks=[];handlers={};module=ModuleType('explorer.source_evaluation_transport')
    def evaluate(actual_store,actual_job,actual_runtime):
        callbacks.append((actual_store.root,actual_job['id'],actual_runtime))
        return dict(schema='SOFTWARE_TRANSPORT_FIXTURE',physical_io=False)
    module.run_source_evaluation=evaluate
    monkeypatch.setitem(sys.modules,'explorer.source_evaluation_transport',module)
    monkeypatch.setattr(job_runner.signal,'signal',lambda number,callback:handlers.update({number:callback}))
    job_runner.main(job['id'])
    assert callbacks==[(store.root,job['id'],runtime)] and store.job(job['id'])['status']=='completed'
    assert set(handlers)=={signal.SIGTERM,signal.SIGINT}
    with pytest.raises(RuntimeError,match='Source evaluation process interrupted'):handlers[signal.SIGTERM](signal.SIGTERM,None)
