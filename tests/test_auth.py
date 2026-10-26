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


def test_login_limit_is_shared_by_clients_and_cannot_be_bypassed_by_forwarded_header(tmp_path,monkeypatch):
    from types import SimpleNamespace
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'rate-limit-state')
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt='rate-fixture',
        password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'rate-fixture',310000).hex()))
    clock=[1000.];monkeypatch.setattr(module,'time',SimpleNamespace(time=lambda:clock[0]))
    first,second=TestClient(module.app),TestClient(module.app)
    try:
        for index in range(16):
            client=first if index%2==0 else second
            assert client.post('/api/login',json={'password':'wrong'}).status_code==401
        assert second.post('/api/login',json={'password':'fixture'},headers={'X-Forwarded-For':'another-host'}).status_code==429
        clock[0]+=301
        assert first.post('/api/login',json={'password':'fixture'}).status_code==200
        assert len(module.LOGIN_ATTEMPTS['testclient'])==1
    finally:first.close();second.close()
