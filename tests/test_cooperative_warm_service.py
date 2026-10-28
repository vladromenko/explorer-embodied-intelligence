"""Actual temporary common queue/service metadata; no model/device/control I/O."""
from copy import deepcopy
import sys
import threading
import uuid

import pytest

from explorer.cooperative_warm_service import CooperativeWarmService
from test_cooperative_queued_shadows import configured


def warm(service,session,**extra):
    return service.command('warm_models',{'session_id':session['session_id'],'expected_epoch':session['epoch'],**extra},
        authenticated_actor='actual-server-owner')['result']


def test_real_cpu4_queue_profile_and_service_bootstrap_never_loads_models_or_calls_source(tmp_path):
    before=set(sys.modules);service,store,calls,client,spec,session,models=configured(tmp_path,input_policy_roles=('giver','receiver'))
    try:
        first=warm(service,session,request_id='actual-click',requested_by_actor='forged-browser-actor')
        second=warm(service,session,request_id='new-click-while-already-warming')
        assert first['job_id']==second['job_id'] and not first['compute_ready']
        assert not first['physical_execution_authorized'] and not first['hardware_lease_renewed']
        job=store.job(first['job_id']);metadata=store.schedule_status(job['id'])['metadata']
        assert job['kind']=='cooperative_policy_warm' and job['project_id']=='05'
        assert job['config']['requested_by_actor']=='actual-server-owner'
        assert metadata['resources']['cpu_cores']==4 and metadata['resources']['memory_bytes']==4*1024**3
        assert metadata['resources']['gpu_slots']==0 and metadata['preemptible'] is False
        assert set(job['config']['native_plans'])=={'giver','receiver'}
        snapshot=service.snapshot(session['session_id'])
        assert len(snapshot['warm_compute_sessions'])==1 and not snapshot['persistent_warm_model_actor_ready']
        assert not any(snapshot['model_adapters_bound'].values()) and not calls and 'torch' not in set(sys.modules)-before
        stopped=service.command('stop',{'session_id':session['session_id'],'expected_epoch':session['epoch']},authenticated_actor='owner')['result']
        assert first['job_id'] in stopped['warm_compute_jobs_cancelled']
        assert store.job(first['job_id'])['status'] in ('cancel_requested','cancelled')
        with pytest.raises(ValueError,match='epoch changed'):warm(service,session)
    finally:service.close();client.close()


def test_parallel_explicit_warm_requests_and_reconstructed_manager_do_not_duplicate_or_autostart(tmp_path):
    service,store,calls,client,spec,session,models=configured(tmp_path,input_policy_roles=('giver','receiver'));outputs=[];errors=[]
    def enqueue():
        try:outputs.append(service.warm_compute.enqueue(session,spec,request_id=uuid.uuid4().hex,authenticated_actor='owner'))
        except Exception as error:errors.append(error)
    workers=[threading.Thread(target=enqueue) for _ in range(4)]
    try:
        for thread in workers:thread.start()
        for thread in workers:thread.join(3)
        assert not errors and len({row['job_id'] for row in outputs})==1
        replacement=CooperativeWarmService(store)
        assert replacement.snapshots(session)[0]['status']=='queued' and not calls
        assert len([job for job in store.jobs() if job['kind']=='cooperative_policy_warm'])==1
    finally:service.close();client.close()


def test_shutdown_cancels_only_current_coordinator_compute_and_terminal_request_is_not_silently_restarted(tmp_path):
    service,store,calls,client,spec,session,models=configured(tmp_path,input_policy_roles=('giver','receiver'))
    try:
        first=warm(service,session,request_id='one')
        store.update_job(first['job_id'],status='completed',result={'fixture_only':True})
        assert warm(service,session,request_id='one')['job_id']==first['job_id']
        second=warm(service,session,request_id='two');assert second['job_id']!=first['job_id']
        unrelated=store.create_job('cooperative_policy_warm','05',dict(store.job(second['job_id'])['config'],cooperative_process_session='different-process'))
        service.close()
        assert store.job(second['job_id'])['status'] in ('cancelled','cancel_requested')
        assert store.job(first['job_id'])['status']=='completed' and store.job(unrelated['id'])['status']=='queued' and not calls
    finally:service.close();client.close()


def test_source_changed_and_invalid_ttl_fail_before_any_compute_queue_insert(tmp_path):
    service,store,calls,client,spec,session,models=configured(tmp_path,input_policy_roles=('giver','receiver'))
    try:
        for ttl in (0,29,601,True,'120'):
            with pytest.raises(ValueError):warm(service,session,compute_ttl_s=ttl)
        from pathlib import Path
        Path(store.job(models['receiver'])['result']['source_manifest_path']).write_bytes(b'changed original source')
        with pytest.raises(ValueError,match='original cohort'):warm(service,session)
        assert not [job for job in store.jobs() if job['kind']=='cooperative_policy_warm'] and not calls
    finally:service.close();client.close()


@pytest.mark.parametrize('role',['giver','receiver'])
def test_mixed_manual_policy_warms_only_actual_required_head_without_manual_model_placeholder(tmp_path,role):
    service,store,calls,client,spec,session,models=configured(tmp_path,input_policy_roles=(role,),pin_manual_models=False)
    manual='receiver' if role=='giver' else 'giver'
    try:
        assert 'model_id' not in spec['subskills'][manual]
        from pathlib import Path
        Path(store.job(models[manual])['result']['checkpoint_dir'],'dummy-weight').write_bytes(b'UNUSED_MANUAL_MODEL_MUST_NOT_BE_READ')
        result=warm(service,session,request_id='explicit-mixed')
        job=store.job(result['job_id'])
        assert job['config']['required_policy_roles']==[role] and set(job['config']['native_plans'])=={role}
        snapshot=service.snapshot(session['session_id'])
        assert snapshot['required_policy_roles']==[role]
        assert not snapshot['policy_role_readiness'][manual]['required']
        assert result['role_readiness'][manual]['compute_requirement_satisfied'] and not result['role_readiness'][manual]['loaded_model_current']
        with pytest.raises(ValueError,match='Manual input role'):service.warm_compute.prediction_adapter(job['id'],session,manual)
        assert not calls and not result['physical_execution_authorized']
    finally:service.close();client.close()


def test_both_manual_without_any_model_records_normally_and_never_creates_compute_placeholder(tmp_path):
    from test_cooperative_service import fixture,session as manual_session
    service,store,calls,active,contexts,client=fixture(tmp_path);row,spec=manual_session(service)
    try:
        state=service.snapshot(row['session_id'])
        assert state['required_policy_roles']==[] and not any(item['required'] for item in state['policy_role_readiness'].values())
        with pytest.raises(ValueError,match='both manual inputs work without models'):warm(service,row)
        recorded=service.command('record_start',{'session_id':row['session_id'],'expected_epoch':row['epoch']},authenticated_actor='owner')['result']
        service.command('record_finish',{'session_id':row['session_id'],'expected_epoch':row['epoch'],
            'recording_id':recorded['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='owner')
        assert not store.jobs()
        assert all('/record/' in path or path.endswith('/status') or path.endswith('/capabilities') or
            (method=='GET' and path=='/legacy/explorer/retarget/reference') or
            (method=='GET' and path.startswith(('/native-capture/recordings/','/so101-capture/recordings/')) and path.endswith('/manifest'))
            for method,_,path,_ in calls)
        assert sum(method=='GET' and path=='/legacy/explorer/retarget/reference' for method,_,path,_ in calls)==1
    finally:service.close();client.close()
