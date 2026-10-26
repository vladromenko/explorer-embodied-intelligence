"""Pure queue/fairness and isolated SQLite tests; never start a worker/device."""
import json

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
