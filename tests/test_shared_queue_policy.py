"""Pure queue/fairness and isolated SQLite tests; never start a worker/device."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from explorer.store import Store
from explorer.queue_policy import GIB,default_metadata,queue_plan


def job(identity,role='01',*,kind='so101_skill_train',device='cpu',created=100,status='queued'):
    return dict(id=identity,project_id=role,experiment_slug='role-'+role,kind=kind,status=status,created=created,
        config=dict(device=device,image_size=64,batch_size=2,skill_id='selected-skill',skill_version=2,aggregate_manifest_sha256='a'*64))


def host(**changed):
    return dict(dict(available_memory_bytes=14*GIB,free_disk_bytes=40*GIB,cpu_count=8,load_1m=0,thermal=[]),**changed)


def plan(jobs,*,active=(),usage=None,now=100,resources=None,edits=None):
    metadata={row['id']:default_metadata(row) for row in jobs}
    for identity,values in (edits or {}).items():metadata[identity].update(values)
    return queue_plan(jobs,metadata,resources or host(),active,service_seconds=usage,now=now)


def test_scheduler_metadata_is_durable_separate_from_all_historical_job_config_result_event_card_bytes(tmp_path):
    store=Store(tmp_path/'state');a=store.create_job('so101_skill_train','04',dict(skill_id='s',skill_version=2,backend='act',aggregate_manifest_sha256='a'*64,image_size=64,batch_size=2))
    b=store.create_job('mobile_import','02',{})
    with store.connect() as db:before=[tuple(row) for row in db.execute('SELECT id,kind,project_id,status,config,result FROM jobs ORDER BY id')]
    first=store.schedule_job(a['id'],dict(priority=2,dependencies=[b['id']]))
    assert first['metadata']['experiment_slug']=='so101_source_learning' and first['metadata']['skill_version']==2
    assert first['metadata']['dataset_sha256']=='a'*64 and first['metadata']['backend']=='act'
    store.update_schedule_runtime(a['id'],phase='waiting',wait_reasons=['dependency_waiting:'+b['id']])
    loaded=Store(store.root);assert loaded.schedule_status(a['id'])['runtime']['phase']=='waiting'
    assert loaded.job(a['id'])['scheduling']['revision']==first['revision']
    with pytest.raises(ValueError,match='changed'):loaded.schedule_job(a['id'],dict(priority=3),expected_revision='bad')
    with pytest.raises(ValueError,match='cycle'):loaded.schedule_job(b['id'],dict(dependencies=[a['id']]))
    with pytest.raises(ValueError,match='minimum'):loaded.schedule_job(a['id'],dict(resources={'memory_bytes':1}))
    with pytest.raises(ValueError,match='server-derived'):loaded.schedule_job(a['id'],dict(resources={'gpu_slots':0}))
    with store.connect() as db:assert [tuple(row) for row in db.execute('SELECT id,kind,project_id,status,config,result FROM jobs ORDER BY id')]==before


def test_dependencies_wait_fail_cancel_and_sealed_model_identity_never_changes_to_priority_input(tmp_path):
    a=job('a');b=job('b',role='02',status='running');c=job('c',role='03',status='failed')
    result=plan([a,b,c],edits={'a':{'dependencies':['b','c','missing']}})
    assert result['start']==[] and set(result['waiting']['a']['reasons'])>={'dependency_waiting:b','dependency_failed:c','dependency_missing:missing'}
    b['status']='completed';c['status']='completed'
    assert plan([a,b,c],edits={'a':{'dependencies':['b','c']}})['start']==['a']


@pytest.mark.parametrize('new_status',['running','failed'])
def test_schedule_budget_guard_reads_durable_status_inside_transaction(tmp_path,monkeypatch,new_status):
    store=Store(tmp_path/'state');selected=store.create_job('so101_skill_train','04',dict(image_size=64,batch_size=2))
    initial=store.schedule_job(selected['id']);original=store.job;changed=[False]
    def snapshot_then_transition(identity):
        snapshot=original(identity)
        if not changed[0]:
            changed[0]=True;store.update_job(identity,status=new_status)
        return snapshot
    monkeypatch.setattr(store,'job',snapshot_then_transition)
    with pytest.raises(ValueError,match='Active|Terminal'):
        store.schedule_job(selected['id'],dict(resources={'memory_bytes':4*GIB}))
    assert store.schedule_status(selected['id'])['revision']==initial['revision']


def test_worker_claim_rejects_stale_schedule_revision_without_changing_status(tmp_path):
    store=Store(tmp_path/'state');selected=store.create_job('so101_skill_train','04',dict(image_size=64,batch_size=2))
    old=store.schedule_job(selected['id']);new=store.schedule_job(selected['id'],dict(resources={'memory_bytes':4*GIB}))
    assert not store.claim_job_start(selected['id'],expected_schedule_revision=old['revision'])
    assert store.job(selected['id'])['status']=='queued'
    assert store.claim_job_start(selected['id'],expected_schedule_revision=new['revision'])


@pytest.mark.parametrize('status',['completed','failed','cancelled','cancel_requested'])
def test_late_execution_pins_cannot_rewrite_terminal_or_cancelling_history(tmp_path,status):
    store=Store(tmp_path/'state');job=store.create_job('world_model','01',{})
    store.update_job(job['id'],status=status)
    with store.connect() as db:before=tuple(db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
    for operation in (lambda:store.pin_job_release(job['id'],'c'*40),lambda:store.pin_job_policy_binding(job['id'],'a'*64)):
        with pytest.raises(ValueError,match='Terminal/cancelling'):operation()
    with store.connect() as db:assert tuple(db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())==before
    pinned=store.create_job('world_model','01',{});store.pin_job_release(pinned['id'],'c'*40)
    store.pin_job_gpu_binding(pinned['id'],'c'*40,'a'*64);store.pin_job_policy_binding(pinned['id'],'b'*64)
    store.update_job(pinned['id'],status=status)
    with store.connect() as db:
        settings=json.loads(db.execute('SELECT config FROM jobs WHERE id=?',(pinned['id'],)).fetchone()['config'])
        # Preserve even a historical JSON representation, not only its parsed value.
        db.execute('UPDATE jobs SET config=? WHERE id=?',(json.dumps(settings,indent=3),pinned['id']))
    with store.connect() as db:original=tuple(db.execute('SELECT * FROM jobs WHERE id=?',(pinned['id'],)).fetchone())
    assert store.pin_job_release(pinned['id'],'d'*40)=='c'*40
    assert store.pin_job_gpu_binding(pinned['id'],'c'*40,'a'*64)=='a'*64
    assert store.pin_job_policy_binding(pinned['id'],'b'*64)=='b'*64
    with store.connect() as db:assert tuple(db.execute('SELECT * FROM jobs WHERE id=?',(pinned['id'],)).fetchone())==original


def test_atomic_cancel_intent_dominates_delayed_pause_resume_and_late_worker_status(tmp_path):
    store=Store(tmp_path/'state');job=store.create_job('world_model','01',{});store.update_job(job['id'],status='running')
    store.request_job_operation(job['id'],'cancel');runtime=store.schedule_status(job['id'])['runtime']
    for operation in ('pause','resume'):
        with pytest.raises(ValueError,match='cancel'):store.request_job_operation(job['id'],operation)
    assert store.job(job['id'])['status']=='cancel_requested'
    assert store.schedule_status(job['id'])['runtime']['user_control_generation']==runtime['user_control_generation']
    store.update_job(job['id'],status='paused');assert store.job(job['id'])['status']=='cancel_requested'
    store.update_job(job['id'],status='cancelled');before=store.job(job['id'])
    store.update_job(job['id'],status='running',result={'late':'must_not_rewrite_terminal_history'})
    assert store.job(job['id'])==before
    other=store.create_job('world_model','01',{});store.update_job(other['id'],status='running')
    def request(operation):
        try:return store.request_job_operation(other['id'],operation)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as executor:list(executor.map(request,['pause','cancel']))
    assert store.job(other['id'])['status']=='cancel_requested' and not store.schedule_status(other['id'])['runtime']['user_pause_requested']


def test_late_complete_cannot_revive_terminal_outcome_and_same_finalized_event_is_byte_exact_idempotent(tmp_path):
    store=Store(tmp_path/'state');failed=store.create_job('world_model','01',{})
    store.update_job(failed['id'],status='failed',error='first_terminal_outcome')
    with store.connect() as db:before=tuple(db.execute('SELECT * FROM jobs WHERE id=?',(failed['id'],)).fetchone())
    with pytest.raises(ValueError,match='Terminal'):store.complete_job(failed['id'],{'late_actual_result':'retained_in_worker_artifact'})
    with store.connect() as db:
        assert tuple(db.execute('SELECT * FROM jobs WHERE id=?',(failed['id'],)).fetchone())==before
        assert db.execute('SELECT 1 FROM events WHERE event_id=?',('job.completed:'+failed['id'],)).fetchone() is None
    complete=store.create_job('world_model','01',{});result={'second_key':2,'first_key':1}
    assert store.complete_job(complete['id'],result)=='completed'
    with store.connect() as db:raw=tuple(db.execute('SELECT * FROM jobs WHERE id=?',(complete['id'],)).fetchone())
    assert store.complete_job(complete['id'],{'first_key':1,'second_key':2})=='completed'
    with store.connect() as db:assert tuple(db.execute('SELECT * FROM jobs WHERE id=?',(complete['id'],)).fetchone())==raw
    cancelled=store.create_job('world_model','01',{});store.request_job_operation(cancelled['id'],'cancel')
    assert store.complete_job(cancelled['id'],{'actual_finish_after_cancel_request':True})=='cancelled'
    assert store.job(cancelled['id'])['status']=='cancelled'


def test_CPU_parallel_budget_and_single_GPU_UMA_growth_reserve_include_stopped_process_memory():
    cpu=[job(str(index),role=str(index)) for index in range(4)]
    selected=plan(cpu)
    assert len(selected['start'])==3 and selected['cpu_reserved_or_selected']==6
    assert 'cpu_concurrency_budget_exhausted' in selected['waiting']['3']['reasons']
    gpu=[job('gpu1',device='cuda'),job('gpu2',device='cuda')]
    selected=plan(gpu)
    assert selected['start']==['gpu1'] and selected['gpu_reserved_or_selected']==1
    assert 'single_heavy_GPU_or_inference_slot_occupied' in selected['waiting']['gpu2']['reasons']
    lease=dict(job_id='gpu1',resources=default_metadata(gpu[0])['resources'],resident_memory_bytes=3*GIB,process_stopped=True)
    gpu[0]['status']='paused'
    selected=plan(gpu,active=[lease],resources=host(available_memory_bytes=5*GIB))
    assert selected['start']==[] and selected['gpu_reserved_or_selected']==1
    assert 'available_UMA_memory_below_reserve_plus_budgets' in selected['waiting']['gpu2']['reasons']


def test_inference_priority_fairshare_roles_and_aging_nonstarvation_are_deterministic():
    a=job('old',role='01',created=0);b=job('new',role='02',created=100)
    assert plan([a,b],now=100,usage={'role-01':300})['ranked'][0]=='new'
    assert plan([a,b],now=1000,usage={'role-01':300},edits={'old':{'priority':10},'new':{'priority':-10}})['ranked'][0]=='old'
    # A fixed old low-priority job eventually outranks newly arriving high-priority jobs.
    new=job('new',role='02',created=1000)
    assert plan([a,new],now=1000,edits={'old':{'priority':-10},'new':{'priority':10}})['ranked'][0]=='old'
    inference=job('shadow',role='04',kind='station_shadow')
    assert plan([job('training'),inference])['ranked'][0]=='shadow'
    native=job('source04-shadow',role='04',kind='so101_skill_shadow')
    budget=default_metadata(native)['resources']
    assert budget['cpu_cores']==2 and budget['memory_bytes']==2*GIB and budget['gpu_slots']==0
    assert plan([job('training'),native])['ranked'][0]=='source04-shadow'
    rosmaster=job('native106-shadow',role='03',kind='rosmaster_skill_shadow')
    assert default_metadata(rosmaster)['resources']==default_metadata(native)['resources']
    assert not default_metadata(rosmaster)['preemptible'] and default_metadata(rosmaster)['backend']=='act'
