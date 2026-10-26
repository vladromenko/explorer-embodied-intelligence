"""Public job selection, resource faults and correlated coordinator receipts."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from explorer.scheduler import HEAVY_JOBS, admission, recover_jobs
from explorer.store import Store
from explorer.transfer import prepare_policy_transfer, policy_transfer_sources, run_handover_runtime_test


def test_heavy_jobs_reserve_ui_memory_disk_cpu_and_existing_thermal_headroom():
    host={'available_memory_bytes':8*1024**3,'free_disk_bytes':20*1024**3,'cpu_count':8,'load_1m':1,'thermal':[]}
    job={'kind':'policy_transfer'}
    assert all(kind in HEAVY_JOBS for kind in ('policy_transfer','manipulation','manipulation_student'))
    assert admission(job,host)['admitted']
    for changed,reason in (({'available_memory_bytes':3*1024**3},'available_memory'),
                           ({'free_disk_bytes':1024**3},'free_disk'),({'load_1m':8},'cpu_reserve'),
                           ({'thermal':[{'name':'cpu','temperature_c':97,'existing_hot_critical_trip_c':100}]},'thermal_trip')):
        result=admission(job,dict(host,**changed))
        assert not result['admitted'] and any(reason in r for r in result['reasons'])
    assert admission(job,dict(host,thermal=[{'name':'cpu','temperature_c':80,'existing_hot_critical_trip_c':None}]))['admitted']


@pytest.mark.parametrize('status',['queued','running','paused','pause_requested','resume_requested'])
def test_supervisor_recovery_never_reuses_old_physical_observation_authorization(tmp_path,status):
    store=Store(tmp_path)
    physical=store.create_job('station_rollout','personal',{'observing':True,'expected_control_epoch':'old-epoch'})
    software=store.create_job('train_act','01',{})
    store.update_job(physical['id'],status=status)
    store.update_job(software['id'],status='running')
    recover_jobs(store)
    assert store.job(physical['id'])['status']=='cancelled'
    assert 'fresh explicit' in store.job(physical['id'])['error']
    assert store.job(software['id'])['status']=='queued'


def test_real_source_selector_keeps_hashes_and_does_not_guess_mapping(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path))
    store=Store(tmp_path/'state')
    source=store.create_job('train_act','04',{})
    folder=store.root/'jobs'/source['id'];checkpoint=folder/'student_act';checkpoint.mkdir(parents=True)
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'action_names':['shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper'],
        'episodes':[{'id':'heldout','split':'sealed_test'},{'id':'dev','split':'development'}], 'contains_physical_recordings':True}))
    result={'trained':True,'feature_dimension':6,'source_manifest_path':str(manifest),
        'source_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'checkpoint_sha256':{'weights':'test-source-hash'}}
    (folder/'result.json').write_text(json.dumps(result))
    assert policy_transfer_sources(store)==[]
    store.complete_job(source['id'],result)
    choices=policy_transfer_sources(store)
    assert len(choices)==1 and choices[0]['episodes']==[{'id':'dev','split':'development'}]
    bound=prepare_policy_transfer(store,{'source_job_id':source['id']})
    assert bound['source_episode_id']=='dev' and 'mapping_profile' not in bound and 'registration' not in bound
    assert bound['checkpoint_sha256']==result['checkpoint_sha256']
    with pytest.raises(ValueError,match='unsealed'):prepare_policy_transfer(store,{'source_job_id':source['id'],'source_episode_id':'heldout'})
    manifest.write_text('{}')
    with pytest.raises(ValueError):prepare_policy_transfer(store,{'source_job_id':source['id']})


def test_coordinator_job_runs_fault_scenarios_and_reuses_journal_without_replay(tmp_path):
    store=Store(tmp_path)
    job=store.create_job('handover_runtime_test','05',{})
    result=run_handover_runtime_test(store,job)
    assert result['passed'] and not result['physics_simulation'] and not result['physical_execution']
    assert result['cases'][0]['dispatched_operations']==['offer','approach','close','release','finish']
    assert all('release' not in case['dispatched_operations'] for case in result['cases'][1:])
    sizes={path:path.stat().st_size for path in (store.root/'jobs'/job['id']).glob('*/handover-journal.jsonl')}
    assert run_handover_runtime_test(store,job)==result
    assert all(path.stat().st_size==size for path,size in sizes.items())


def test_authenticated_new_jobs_reject_wrong_project_and_unavailable_source(tmp_path,monkeypatch):
    import explorer.app as module
    monkeypatch.setattr(module,'STORE',Store(tmp_path))
    client=TestClient(module.app)
    response=module.sign_in(JSONResponse({'ok':True}))
    for value in response.headers.getlist('set-cookie'):
        name,value=value.split(';',1)[0].split('=',1);client.cookies.set(name,value)
    headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
    assert client.post('/api/jobs',json={'kind':'manipulation','project_id':'01','config':{}}).status_code==403
    for body in ({'kind':'manipulation','project_id':'04','config':{}},
                 {'kind':'policy_transfer','project_id':'04','config':{'source_job_id':'missing'}},
                 {'kind':'manipulation_student','project_id':'01','config':{'device':'cuda'}}):
        assert client.post('/api/jobs',json=body,headers=headers).status_code==400
    response=client.post('/api/jobs',json={'kind':'manipulation','project_id':'01','config':{'task':'sock_to_basket'}},headers=headers)
    assert response.status_code==200 and response.json()['status']=='queued'
    assert client.get('/api/transfer/sources').json()=={'sources':[]}
