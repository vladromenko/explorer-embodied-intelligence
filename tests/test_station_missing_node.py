"""Empty setup must report unavailable without a request/control fallback."""
import hashlib
import importlib
from fastapi.testclient import TestClient
from explorer.store import Store


def test_authenticated_station_status_and_legacy_mutation_return_unavailable_without_node_IO(tmp_path,monkeypatch):
    module=importlib.import_module('explorer.app');salt='own-missing-node-fixture'
    monkeypatch.setattr(module,'STORE',Store(tmp_path/'state'))
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt=salt,password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',salt.encode(),310000).hex()))
    class NoNetwork:
        async def request(self,*args,**kwargs):raise AssertionError('No configured node: no network request permitted')
    monkeypatch.setattr(module,'CLIENT',NoNetwork());client=TestClient(module.app)
    assert client.get('/api/station/status').status_code==401
    assert client.post('/api/login',json={'password':'fixture'}).status_code==200
    assert client.get('/api/station/status').status_code==503
    assert client.get('/api/station/unrecognized').status_code==404
    assert client.post('/api/station/stop',json={},headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}).status_code==503
