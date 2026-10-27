"""Isolated ASGI + in-memory upstream only; no node/socket/control requests."""
import hashlib
import importlib
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from explorer.store import Store


@pytest.fixture
def isolated_camera_api(tmp_path,monkeypatch):
    config={'data_dir':str(tmp_path/'state'),'public_url':'https://testserver','password_salt':'software-salt',
        'password_hash':'unused-no-login-request','ingest_token':'software-ingest',
        'nodes':[{'id':'nano','host':'never-network.invalid','port':8091,'token':'software-node-secret'}]}
    path=tmp_path/'config.json';path.write_text(json.dumps(config));monkeypatch.setenv('EXPLORER_CONFIG',str(path))
    module=importlib.import_module('explorer.app');store=Store(config['data_dir'])
    monkeypatch.setattr(module,'CONFIG',dict(config,root=str(module.ROOT)));monkeypatch.setattr(module,'STORE',store)
    calls=[]
    def upstream(request):
        calls.append(request)
        assert request.method=='GET','Camera qualification must never request upstream control'
        assert request.url.host=='never-network.invalid'
        assert request.headers['authorization']=='Bearer software-node-secret'
        return httpx.Response(200,content=b'\xff\xd8software-jpeg-fixture\xff\xd9',headers={
            'Content-Type':'image/jpeg','X-Frame-ID':'source-frame-17','X-Frame-Clock':'nano-boot-source',
            'X-Wall-UTC':'2026-10-27T09:00:00Z','X-Monotonic-NS':'9007199254740993','X-Timestamp-Basis':'cache_not_exposure',
            'Authorization':'must-never-be-forwarded','X-Unrelated-Internal':'not-public'})
    client=httpx.AsyncClient(transport=httpx.MockTransport(upstream),trust_env=False)
    monkeypatch.setattr(module,'CLIENT',client)
    token,csrf='isolated-session','isolated-csrf'
    with store.connect() as db:
        db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+60,csrf))
    # No TestClient lifespan: it must not create background inventory polling.
    browser=TestClient(module.app,base_url='https://testserver')
    yield browser,calls,token,csrf
    import asyncio
    asyncio.run(client.aclose())


def test_camera_GET_requires_owner_session_before_any_upstream_access(isolated_camera_api):
    browser,calls,token,csrf=isolated_camera_api
    assert browser.get('/api/cameras/nano-rgbd/snapshot').status_code==401
    assert calls==[]
    browser.cookies.set('explorer_session',token)
    response=browser.get('/api/cameras/nano-rgbd/snapshot?preview_epoch=7&rx=999')
    assert response.status_code==200 and response.content==b'\xff\xd8software-jpeg-fixture\xff\xd9'
    assert response.headers['content-type']=='image/jpeg'
    assert response.headers['x-frame-id']=='source-frame-17'
    assert response.headers['x-frame-clock']=='nano-boot-source'
    assert response.headers['x-monotonic-ns']=='9007199254740993' and response.headers['x-timestamp-basis']=='cache_not_exposure'
    assert 'authorization' not in response.headers and 'x-unrelated-internal' not in response.headers
    assert response.headers['cache-control']=='no-store' and response.headers['x-content-type-options']=='nosniff'
    assert len(calls)==1 and calls[0].url.path=='/cameras/nano-rgbd/snapshot'


def test_control_POST_without_csrf_or_cross_origin_never_reaches_upstream(isolated_camera_api):
    browser,calls,token,csrf=isolated_camera_api;browser.cookies.set('explorer_session',token)
    assert browser.post('/api/robot/teleop/input',json={'keys':['KeyW']}).status_code==403
    assert browser.post('/api/robot/teleop/input',json={'keys':['KeyW']},headers={'X-Explorer-CSRF':csrf,'Origin':'https://other.invalid'}).status_code==403
    assert calls==[]


def test_csrf_logout_invalidates_future_camera_GET_without_any_robot_control(isolated_camera_api):
    browser,calls,token,csrf=isolated_camera_api;browser.cookies.set('explorer_session',token)
    assert browser.post('/api/logout').status_code==403
    assert browser.get('/api/cameras/nano-rgbd/snapshot').status_code==200
    before=len(calls)
    assert browser.post('/api/logout',headers={'X-Explorer-CSRF':csrf}).status_code==200
    assert browser.get('/api/cameras/nano-rgbd/snapshot').status_code==401
    assert len(calls)==before
    assert all(request.method=='GET' and request.url.path=='/cameras/nano-rgbd/snapshot' for request in calls)
