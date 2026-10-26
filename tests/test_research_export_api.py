"""Authenticated API -> real selected evidence copy/restore -> verified download."""
import hashlib
import importlib
import io
import json
import tarfile

from fastapi.testclient import TestClient

from explorer.store import Store


def test_actual_export_api_queue_copy_independent_restore_and_verified_fixed_download(tmp_path,monkeypatch):
    from explorer.research_export_job import run_research_export
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state')
    source=store.create_job('train_act','01',{'seed':73,'experiment_numbering_version':2})
    folder=store.root/'jobs'/source['id'];folder.mkdir(parents=True)
    (folder/'model.safetensors').write_bytes(b'explicit opaque scientific weights, never loaded')
    store.update_job(source['id'],status='failed',error='Retained independent task failure',result={'trained':False,'independent_success':False})
    with store.connect() as db:
        original=tuple(db.execute('SELECT config,result,error FROM jobs WHERE id=?',(source['id'],)).fetchone())
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    with TestClient(module.app) as client:
        assert client.get('/api/research/exports').status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        assert client.post('/api/research/exports',json={'job_ids':[source['id']]}).status_code==403
        for body in ({'job_ids':[]},{'job_ids':[source['id']],'estimated_payload_bytes':0},{'job_ids':['../etc/passwd']},{'path':'/etc/passwd'}):
            assert client.post('/api/research/exports',json=body,headers=csrf).status_code==400
        listed=client.get('/api/research/exports').json()
        assert listed['choices']==[{key:source[key] if key!='status' else 'failed' for key in ('id','kind','project_id','status','created')}]
        created=client.post('/api/research/exports',json={'job_ids':[source['id']]},headers=csrf)
        assert created.status_code==200,created.text
        identity=created.json()['id'];job=store.job(identity)
        assert job['kind']=='research_export' and job['config']['job_ids']==[source['id']]
        assert type(job['config']['estimated_payload_bytes']) is int and job['config']['estimated_payload_bytes']>0
        route='/api/research/exports/'+identity+'/download'
        assert client.get(route).status_code in (404,409)
        result=run_research_export(store,job,job['config'])
        assert result['verification']=='PARTIAL' and result['independent_restore_verified'] is True
        store.complete_job(identity,result)
        downloaded=client.get(route)
        assert downloaded.status_code==200,downloaded.text
        assert downloaded.headers['x-content-sha256']==hashlib.sha256(downloaded.content).hexdigest()==result['archive_sha256']
        assert downloaded.headers['cache-control']=='no-store'
        with tarfile.open(fileobj=io.BytesIO(downloaded.content)) as archive:
            names=archive.getnames();assert names and all(not name.startswith('/') and '..' not in name.split('/') for name in names)
            assert any(name.endswith('manifest.json') for name in names)
        listed=client.get('/api/research/exports').json()
        export=next(row for row in listed['exports'] if row['id']==identity)
        assert export['download_url']==route and export['result']['verification']=='PARTIAL'
        assert client.get(route+'?path=/etc/passwd').status_code==400
        with store.connect() as db:assert tuple(db.execute('SELECT config,result,error FROM jobs WHERE id=?',(source['id'],)).fetchone())==original
        from explorer.research_export_job import download_validation
        artifact=download_validation(store,identity);path=artifact['path'];path.write_bytes(path.read_bytes()+b'late mutation')
        assert client.get(route).status_code==409
