"""Real authenticated06 HTTP/queue preparation; generated inputs, no optimizer/IO."""
import concurrent.futures
import json
from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import pytest

from explorer import transfer_target as target
from explorer_shared.learning.artifacts import sha
from explorer_shared.learning.rosmaster_skill_features import PipelineBlocked
from explorer.queue_policy import default_metadata,GIB
from test_transfer_geometry_api import configured
from test_transfer_target_corpus import generated_target_corpus


def prepared(tmp_path,monkeypatch,*,count=5):
    app,store,ident,_=configured(tmp_path,monkeypatch)
    client=TestClient(app.app);client.post('/api/login',json={'password':'fixture'})
    csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
    created=client.post('/api/transfer/calibration',headers=csrf,json=dict(
        model_job_id=ident,target_version=2,request_id=str(uuid.uuid4())))
    assert created.status_code==200,created.text
    wizard=created.json();source=store.job(ident)
    source_spec=target.source_for_wizard(store,wizard['wizard_id'])[1]['manifest']['skill_spec']
    path,binding,_=generated_target_corpus(store.root/'datasets',train_count=count,
        source_binding=wizard['source_model_binding'],source_spec=source_spec)
    job=store.create_job(target.DATASET_KIND,'06',dict(inputs='GENERATED_ONLY'))
    store.complete_job(job['id'],dict(dataset_manifest=str(path),dataset_manifest_sha256=sha(path)))
    return app,store,client,csrf,wizard,job['id']


def test_managed_dataset_HTTP_counts_and_bounded_queue_idempotence_without_native_transport(tmp_path,monkeypatch):
    app,store,client,csrf,wizard,dataset_id=prepared(tmp_path,monkeypatch)
    prefix='/api/transfer/calibration/'+wizard['wizard_id']
    assert TestClient(app.app).get(prefix+'/datasets').status_code==401
    response=client.get(prefix+'/datasets');assert response.status_code==200,response.text
    row=response.json()['datasets'][0];assert row['ready'] and row['train_original_demonstrations']==5
    assert row['budgets'][0]['ready'] and not row['budgets'][1]['ready']
    body=dict(dataset_job_id=dataset_id,demonstrations=5,mode='target_only',request_id=str(uuid.uuid4()),act_steps=2)
    assert client.post(prefix+'/train',json=body).status_code==403
    for invalid in (dict(body,dataset_manifest='/etc/passwd'),dict(body,device='cuda'),
                    dict(body,demonstrations=10),dict(body,mode='zero_shot'),dict(body,act_steps=True)):
        assert client.post(prefix+'/train',headers=csrf,json=invalid).status_code==409
    created=client.post(prefix+'/train',headers=csrf,json=body);assert created.status_code==200,created.text
    job=created.json();assert job['kind']==target.JOB_KIND and job['project_id']=='06' and job['status']=='queued'
    assert client.post(prefix+'/train',headers=csrf,json=body).json()['id']==job['id']
    assert client.post(prefix+'/train',headers=csrf,json=dict(body,mode='vision_adapt')).status_code==409
    budget=default_metadata(job);assert budget['resources']['memory_bytes']==4*GIB and budget['resources']['cpu_cores']==2
    assert budget['preemptible'] and budget['resources']['gpu_slots']==0
    assert len([j for j in store.jobs() if j['kind']==target.JOB_KIND])==1


def test_concurrent_retries_share_one_durable_job_and_changed_source_blocks_worker(tmp_path,monkeypatch):
    app,store,client,csrf,wizard,dataset_id=prepared(tmp_path,monkeypatch)
    options=dict(mode='target_only',demonstrations=5,request_id=str(uuid.uuid4()),act_steps=2)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        jobs=list(pool.map(lambda _:target.enqueue_training(store,wizard['wizard_id'],dataset_id,options,actor='SAME_ACTOR'),range(2)))
    assert jobs[0]['id']==jobs[1]['id']
    source=store.job(wizard['source_model_binding']['model_job_id'])
    (Path(source['result']['checkpoint_dir'])/'model.safetensors').write_bytes(b'changed structural fixture')
    with pytest.raises(ValueError):target.run_training(store,jobs[0])
    assert not list((store.root/'jobs'/jobs[0]['id']).glob('student_act/*'))


def test_target_dataset_escape_and_reduced_task_are_retained_but_blocked(tmp_path,monkeypatch):
    app,store,client,csrf,wizard,dataset_id=prepared(tmp_path,monkeypatch)
    job=store.job(dataset_id);path=Path(job['result']['dataset_manifest'])
    document=json.loads(path.read_bytes());document['skill_spec']['task_type']='hold'
    from explorer_shared.contracts import canonical,digest
    document['skill_spec_sha256']=digest(document['skill_spec']);path.write_bytes(canonical(document))
    reduced=store.create_job(target.DATASET_KIND,'06',dict(inputs='GENERATED_REDUCED_NEGATIVE'))
    store.complete_job(reduced['id'],dict(dataset_manifest=str(path),dataset_manifest_sha256=sha(path)))
    with pytest.raises(PipelineBlocked,match='COMPLETE_SOURCE_TARGET_TASK'):target.target_dataset(store,wizard['wizard_id'],reduced['id'])
    outside=tmp_path/'outside.json';outside.write_bytes(path.read_bytes())
    escaped=store.create_job(target.DATASET_KIND,'06',dict(inputs='GENERATED_ESCAPE_NEGATIVE'))
    store.complete_job(escaped['id'],dict(dataset_manifest=str(outside),dataset_manifest_sha256=sha(outside)))
    with pytest.raises(PipelineBlocked,match='ESCAPED'):target.target_dataset(store,wizard['wizard_id'],escaped['id'])
