"""Isolated HTTP validation tests. Queue and all hardware paths stay stubbed."""
import json
from fastapi.testclient import TestClient
from so101_lab import app as web
from so101_lab.recording import RecordingManager
from so101_lab.training_options import preview
from test_training import fixture_dataset

def test_api_invalid_inputs_do_not_launch_worker(tmp_path,monkeypatch):
    m=RecordingManager(tmp_path);d=m.create_dataset('Synthetic API safety','test')
    monkeypatch.setattr(web,'manager',m);calls=[];monkeypatch.setattr(web.jobs,'submit',lambda *a,**kw:calls.append(a))
    c=TestClient(web.app)
    for opts in ({'policy_type':'missing'},{'policy_type':'act','batch_size':0},{'policy_type':'act','lr':-1},{'tiny_model':True}):
        r=c.post('/api/jobs',json={'kind':'train','dataset_id':d['dataset_id'],'options':opts});assert r.status_code==422
    assert not calls
    assert c.post('/api/training/preview',json={}).status_code==422
    assert c.post('/api/jobs',json={'kind':'train','dataset_id':'../outside'}).status_code==422
    assert c.get('/api/training/catalog/'+d['dataset_id']).status_code==200
    assert c.post('/api/jobs',json={'kind':'annotate','dataset_id':d['dataset_id'],'options':{'prompts':[]}}).status_code==422
    assert not calls

def test_api_act_split_and_submit_same_options(tmp_path,monkeypatch):
    m=RecordingManager(tmp_path);d=m.create_dataset('Fixture only, never hardware','api')
    path=tmp_path/'data'/d['dataset_id'];fixture_dataset(tmp_path/'fixture',episodes=3)
    import shutil
    shutil.copytree(tmp_path/'fixture'/'episodes',path/'episodes',dirs_exist_ok=True)
    # Sensor validity is fixture-only for validation; no queue process is started.
    meta=json.loads((path/'dataset.json').read_text());meta['source']='hardware';(path/'dataset.json').write_text(json.dumps(meta))
    for ep in (path/'episodes').iterdir():
        j=ep/'episode.json';info=json.loads(j.read_text());info['source']='hardware';j.write_text(json.dumps(info))
        f=ep/'frames.jsonl';rows=[json.loads(l) for l in f.read_text().splitlines()]
        for row in rows:row.update(source='hardware',state_valid=True,action_valid=True)
        f.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    monkeypatch.setattr(web,'manager',m);calls=[]
    def submit(kind,dataset,options):calls.append(options);return {'id':'0'*32,'options':options,'state':'queued'}
    monkeypatch.setattr(web.jobs,'submit',submit);c=TestClient(web.app)
    opts={'policy_type':'act','mode':'rgb_state','device':'cpu','validation_fraction':.3,'test_fraction':.3,'batch_size':1,'lr':.00002}
    r=c.post('/api/training/preview',json={'dataset_id':d['dataset_id'],'options':opts});assert r.status_code==200,r.text
    plan=r.json()['split'];assert len(plan['test'])==1
    j=c.post('/api/jobs',json={'kind':'train','dataset_id':d['dataset_id'],'options':opts});assert j.status_code==200,j.text
    assert calls[0]['policy_type']=='act' and calls[0]['lr']==.00002
    assert calls[0]['expected_split_sha256']==plan['sha256']
    assert len(calls)==1
