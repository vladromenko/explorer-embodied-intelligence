"""Authenticated UI proxies preserve native bytes; every upstream call is local mock."""
import hashlib
import importlib
import json

import httpx
from fastapi.testclient import TestClient

from explorer.store import Store


def test_observed_panel_pins_jpeg_assigns_actor_and_separates_software_blockers(tmp_path,monkeypatch):
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state');calls=[]
    ident='a'*32;jpeg=b'\xff\xd8cached-original\xff\xd9';checksum=hashlib.sha256(jpeg).hexdigest()
    def transport(request):
        body=json.loads(request.content) if request.content else None;calls.append(dict(method=request.method,path=request.url.path,body=body))
        if request.url.path=='/native-activation/frame/'+ident:
            return httpx.Response(200,content=jpeg,headers={'X-JPEG-SHA256':checksum,'X-Frame-ID':'original-frame','X-Monotonic-NS':'123','X-Boot-ID':'original-boot'})
        if request.url.path=='/legacy/explorer/retarget/status':return httpx.Response(404,json={})
        return httpx.Response(200,json=dict(physical_commands=0))
    config=dict(module.CONFIG,data_dir=str(store.root),password_salt='fixture',
        password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex(),
        nodes=[dict(id=name,host='memory-'+name,port=8092,token='not-real') for name in ('pi','nano')])
    monkeypatch.setattr(module,'STORE',store);monkeypatch.setattr(module,'CONFIG',config)
    monkeypatch.setattr(module,'CLIENT',httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    with TestClient(module.app) as client:
        assert client.get('/api/native-activation/frame/'+ident).status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        frame=client.get('/api/native-activation/frame/'+ident)
        assert frame.content==jpeg and frame.headers['X-JPEG-SHA256']==checksum and frame.headers['X-Frame-ID']=='original-frame'
        before=len(calls)
        assert client.get('/api/native-activation/frame/'+ident+'?other=source').status_code==400 and len(calls)==before
        body=dict(actor_id='forged-client-actor',observing=True)
        assert client.post('/api/robot/explorer/retarget/activation/authorize',json=body,headers=headers).status_code==200
        assert calls[-1]['body']['actor_id'].startswith('authenticated-session:') and calls[-1]['body']['actor_id']!='forged-client-actor'
        assert client.post('/api/retarget/calibration/capture',json=dict(body,kind='task_axes'),headers=headers).status_code==200
        assert calls[-1]['path']=='/arm-retarget/calibration/capture' and calls[-1]['body']['actor_id'].startswith('authenticated-session:')
        before=len(calls);state=client.get('/api/evening').json()
        assert all(row['method']=='GET' for row in calls[before:])
        assert next(row for row in state['checks'] if row['id']=='native_gateway')['status']=='SOFTWARE_BLOCKER'
        assert not state['whole_task_completed'] and state['physical_commands_issued_by_status']==0
        assert client.get('/evening').status_code==200
