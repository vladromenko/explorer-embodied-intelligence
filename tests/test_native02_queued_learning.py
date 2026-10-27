"""Explicit CPU-only Supervisor/bootstrap/job_runner proof using fake native data.

This complements the retained direct-worker16-update parity proof. It does not
repeat parity, create real demonstrations or open a Source transport/device.
"""
import json
import hashlib
import importlib
import os
from pathlib import Path

import pytest

from explorer.scheduler import Supervisor,queue_status
from explorer.queue_policy import GIB,INFERENCE_JOBS,default_metadata,queue_plan
from explorer.rosmaster_learning import model_versions
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from native02_queue_helpers import freeze_source,private_config,QueueTrace,pump,stop_fixture_workers,sha
from test_rosmaster_native02_e2e import native_aggregate

ROOT=Path(__file__).parents[1]
SOURCES=['00-control-center/src/explorer/scheduler.py','00-control-center/src/explorer/worker_bootstrap.py',
    '00-control-center/src/explorer/job_runner.py','00-control-center/src/explorer/queue_policy.py','00-control-center/src/explorer/store.py',
    '00-control-center/src/explorer/rosmaster_eval_job.py','00-control-center/src/explorer/rosmaster_learning.py',
    'shared/explorer_shared/learning/rosmaster_skill_pipeline.py','shared/explorer_shared/learning/rosmaster_lerobot_dataset.py',
    'shared/explorer_shared/learning/rosmaster_skill_segments.py','shared/explorer_shared/learning/act_backend.py']


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_NATIVE02_QUEUE_QUALIFICATION')!='1',reason='Requires separate bounded CPU2/4GiB/swap0 approval')
def test_real_supervisor_bootstrap_dispatch_native02_ACT4_then_queued_saved_DEV(tmp_path,monkeypatch):
    assert 'rosmaster_skill_eval' in INFERENCE_JOBS,'Root typed eval resource/dispatch hook must be merged before qualification'
    path,store,originals=native_aggregate(tmp_path,monkeypatch);corpus=CanonicalCorpus(path)
    release,frozen=freeze_source(ROOT,tmp_path/'frozen',SOURCES)
    config=private_config(tmp_path/'config.json',store.root);monkeypatch.setenv('EXPLORER_CONFIG',str(config))
    for key in ('HF_HUB_OFFLINE','HF_DATASETS_OFFLINE','TRANSFORMERS_OFFLINE'):monkeypatch.setenv(key,'1')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','')
    # Actual authenticated ASGI queue/model routes. No lifespan/poller is started.
    from fastapi.testclient import TestClient
    module=importlib.import_module('explorer.app');salt='private-native02-queue-proof'
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,data_dir=str(store.root),nodes=[],password_salt=salt,
        password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture-only',salt.encode(),310000).hex()))
    api=TestClient(module.app);base='/api/skill-workflow/'+corpus.skill_id
    assert api.get(base+'/models?version=1').status_code==401
    assert api.post('/api/login',json={'password':'fixture-only'}).status_code==200
    headers={'X-Explorer-CSRF':api.cookies['explorer_csrf']}
    trace=QueueTrace(store);supervisor=Supervisor({'root':str(release)},store,quantum_s=3600,popen=trace.popen)
    response=api.post(base+'/train',json={'version':1,'settings':{'act_steps':4,'batch_size':2,'image_size':32,'chunk_size':2}},headers=headers)
    assert response.status_code==200,response.text;train=response.json()
    records={};evaluation=None
    try:
        for name,job in [('train',train)]:
            pump(supervisor,trace,lambda:job['id'] in trace.admitted,required_job_id=job['id'])
            current=store.job(job['id']);record=trace.admitted[job['id']];budget=record['metadata']['resources']
            assert current['status']=='running' and current['config']['_execution_commit']==frozen['source_commit']
            assert budget['cpu_cores']==2 and budget['memory_bytes']==4*GIB and budget['gpu_slots']==0
            assert record['runtime']['bootstrap_admitted'] and len(record['runtime']['worker_cpus'])==2
            assert trace.launches[-1]['command'][1:3]==['-m','explorer.worker_bootstrap']
            assert trace.launches[-1]['lease_nonce']==record['runtime']['launch_nonce']
            assert trace.launches[-1]['pythonpath'].split(':')==[str(release/'00-control-center/src'),str(release/'shared')]
            records[name]={'admitted':record,'queue_preview':queue_status(store)}
            pump(supervisor,trace,lambda:store.job(job['id'])['status'] in ('completed','failed','cancelled') and job['id'] not in supervisor.processes)
            completed=store.job(job['id']);assert completed['status']=='completed',completed
            result=completed['result'];assert result['trained'] and result['optimizer_steps']==4 and result['weights_changed'] and result['save_reload_passed']
            assert result['official_dataset']['original_train_demo_count']==3 and result['official_dataset']['train_segment_episode_count']==6
            assert result['feature_dimension']==106 and result['output_action_dimension']==9
            runtime=store.schedule_status(job['id'])['runtime'];assert runtime['process_exit_code']==0 and runtime['worker'] is None and not runtime['memory_retained']
            model=json.loads(Path(result['model_manifest']).read_bytes())
            for file,checksum in model['implementation_sha256'].items():assert sha(release/'shared/explorer_shared/learning'/file)==checksum
        versions=model_versions(store,corpus.skill_id,1)
        assert next(item for item in versions['models'] if item['model_job_id']==train['id'])['compatible_native9_106']
        assert not versions['model_loaded'] and versions['network_requests_sent']==0
        api_versions=api.get(base+'/models?version=1').json();assert api_versions==versions
        response=api.post(base+'/evaluate',json={'version':1,'model_job_id':train['id']},headers=headers)
        assert response.status_code==200,response.text;evaluation=response.json()
        pump(supervisor,trace,lambda:evaluation['id'] in trace.admitted,required_job_id=evaluation['id'])
        record=trace.admitted[evaluation['id']];budget=record['metadata']['resources'];runtime=record['runtime']
        assert budget['cpu_cores']==2 and budget['memory_bytes']==2*GIB and budget['gpu_slots']==0 and not record['metadata']['preemptible']
        assert runtime['bootstrap_admitted'] and store.job(evaluation['id'])['config']['_execution_commit']==frozen['source_commit']
        assert runtime['worker']['pid']!=trace.admitted[train['id']]['runtime']['worker']['pid']
        assert runtime['launch_nonce']!=trace.admitted[train['id']]['runtime']['launch_nonce']
        # Exact shared serial inference reservation, without enqueuing/source-fetching a shadow.
        shadow={'id':'not-dispatched-shadow','kind':'rosmaster_skill_shadow','project_id':'02','created':0,'status':'queued','config':{'device':'cpu'}}
        active={'job_id':evaluation['id'],'resources':budget,'resident_memory_bytes':0}
        waiting=queue_plan([shadow,store.job(evaluation['id'])],
            {shadow['id']:default_metadata(shadow),evaluation['id']:record['metadata']},queue_status(store)['resources'],[active])
        assert 'single_interactive_inference_budget_occupied' in waiting['waiting'][shadow['id']]['reasons']
        records['evaluation']={'admitted':record,'queue_preview':queue_status(store)}
        pump(supervisor,trace,lambda:store.job(evaluation['id'])['status'] in ('completed','failed','cancelled') and evaluation['id'] not in supervisor.processes)
        evaluated=store.job(evaluation['id']);assert evaluated['status']=='completed',evaluated
        result=evaluated['result'];assert result['weights_unchanged'] and result['optimizer_steps_executed']==0
        assert result['evaluation']['ACT9']['evaluated_samples']==10 and not result['evaluation']['sealed_test_used']
        assert result['source_network_requests_sent']==0 and result['physical_control_requests_sent']==0 and not result['dispatch_performed']
        assert sha(result['evaluation_artifact'])==result['evaluation_artifact_sha256']
        assert not supervisor.processes and all(launch['process'].returncode==0 for launch in trace.launches)
        for name,checksum in frozen['frozen_source_files_sha256'].items():assert sha(release/name)==checksum
        assert all(sha(name)==checksum for name,checksum in originals['originals_sha256'].items())
        final_versions=api.get(base+'/models?version=1').json()
        assert next(item for item in final_versions['checks'] if item['id']==evaluation['id'])['status']=='completed'
        report={'schema':'explorer_native02_queued_official_learning_qualification_v1','frozen_source':frozen,'runtime_admissions':records,
            'actual_popen_bootstrap_job_runner':True,'actual_optimizer_updates':4,'queued_DEV_evaluation':evaluated,
            'launches':trace.public(),'trained_job_id':train['id'],'model_versions':final_versions,'authenticated_ASGI_queue_and_models':True,
            'all_workers_exited_and_reservations_released':True,
            'all_inputs_are_software_fixtures':True,'physical_requests_sent':0,'real_demonstrations':0,'production_state_used':False,
            'independent_full_task_success':False,'direct_worker_parity_proof_repeated':False,'source_originals':originals}
        (tmp_path/'queue-qualification.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    finally:
        api.close()
        stop_fixture_workers(supervisor)
