import hashlib
import importlib
import json
import sys
import time
from fastapi.testclient import TestClient
import httpx
from explorer.store import Store


def test_authentication_origin_csrf_and_one_time_ticket(tmp_path, monkeypatch):
    salt = 'test-salt'
    config = dict(data_dir=str(tmp_path/'data'),public_url='http://testserver',password_salt=salt,
        password_hash=hashlib.pbkdf2_hmac('sha256',b'test-code',salt.encode(),310000).hex(),ingest_token='node-secret',nodes=[])
    path=tmp_path/'config.json'
    path.write_text(json.dumps(config))
    monkeypatch.setenv('EXPLORER_CONFIG',str(path))
    module=importlib.import_module('explorer.app')
    monkeypatch.setattr(module,'CONFIG',dict(config,root=str(module.ROOT)))
    monkeypatch.setattr(module,'STORE',Store(config['data_dir']))
    monkeypatch.setattr(module,'CLIENT',httpx.AsyncClient(timeout=4,trust_env=False))
    with TestClient(module.app) as client:
        assert client.get('/api/state').status_code==401
        assert client.post('/api/login',json={'password':'test-code'},headers={'Origin':'https://other.example'}).status_code==403
        assert client.post('/api/login',json={'password':'wrong'}).status_code==401
        assert client.post('/api/login',json={'password':'test-code'}).status_code==200
        assert client.get('/api/jobs').status_code==200
        assert client.post('/api/jobs',json={'kind':'sim_pilot','project_id':'01','config':{}}).status_code==403
        csrf=client.cookies['explorer_csrf']
        response=client.post('/api/jobs',json={'kind':'sim_pilot','project_id':'01','config':{}},headers={'X-Explorer-CSRF':csrf})
        assert response.status_code==200
        token='test-one-use-ticket'
        with module.STORE.connect() as db:
            db.execute('INSERT INTO tickets VALUES (?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+30))
        assert client.get('/api/login?ticket='+token,follow_redirects=False).status_code==303
        assert client.get('/api/login?ticket='+token,follow_redirects=False).status_code==401
