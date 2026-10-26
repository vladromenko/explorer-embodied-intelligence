"""Actual local HTTP/cache transport with software JPEGs; no physical device."""
import hashlib
import importlib
import io
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from explorer.store import Store
from explorer_shared import node_agent as node


def test_node_wizard_without_hailo_serves_exact_existing_jpeg_and_issued_board(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))
    camera=node.CameraSource('pi-overview','software source')
    camera.device='/dev/v4l/by-id/software-fixture-camera'
    image=io.BytesIO();Image.new('RGB',(160,120),'white').save(image,'JPEG');jpeg=image.getvalue()
    camera.publish(jpeg,160,120)
    agent=node.NodeAgent('pi','pi',tmp_path/'state','software-fixture-token-at-least16',sources={'pi-overview':camera})
    assert agent.observer is None and agent.lab_vision is not None
    assert agent.tabletop_calibration_context()=={}
    profile=dict(port='/dev/serial/by-id/software-follower',calibration_sha256='a'*64,verified_on_connected_hardware=True)
    agent.station=SimpleNamespace(status=lambda:dict(roles={'follower':profile}))
    context=agent.tabletop_calibration_context()
    assert context['base_frame']=='base_link' and context['robot_calibration_sha256']=='a'*64
    assert 'matrix' not in context and 'extrinsics' not in context
    server=node.AgentHTTPServer(('127.0.0.1',0),node.make_handler(agent))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def get(path,authenticated=True):
        headers={'Authorization':'Bearer '+agent.token} if authenticated else {}
        with urlopen(Request('http://127.0.0.1:'+str(server.server_port)+path,headers=headers),timeout=5) as response:
            return response.read(),dict(response.headers)
    try:
        with pytest.raises(HTTPError) as error:get('/lab/vision/calibration/board/pdf',False)
        assert error.value.code==401
        raw,headers=get('/lab/vision/calibration/status');status=json.loads(raw)
        frame=status['current_frame'];assert frame['frame_sha256']==hashlib.sha256(jpeg).hexdigest()
        raw,headers=get('/lab/vision/calibration/preview/'+frame['frame_id'])
        assert raw==jpeg and headers['X-Content-SHA256']==hashlib.sha256(raw).hexdigest()
        assert headers['X-Frame-ID']==frame['frame_id']
        raw,headers=get('/lab/vision/calibration/board/pdf')
        assert raw.startswith(b'%PDF') and headers['Content-Type']=='application/pdf'
        assert headers['X-Content-SHA256']==status['board']['pdf_sha256']
        with pytest.raises(HTTPError):get('/lab/vision/calibration/board/pdf?path=/etc/passwd')
        profile['verified_on_connected_hardware']=False
        assert agent.tabletop_calibration_context()=={}
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3);agent.lab_vision.close()


def test_authenticated_central_calibration_proxy_verifies_bytes_and_server_assigns_operator(tmp_path,monkeypatch):
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state');calls=[];corrupt=[False]
    jpeg=b'\xff\xd8software-jpeg\xff\xd9'
    def transport(request):
        body=json.loads(request.content) if request.content else None
        calls.append(dict(method=request.method,path=request.url.path,query=str(request.url.query),body=body))
        if request.url.path.startswith('/lab/vision/calibration/preview/'):
            return httpx.Response(200,content=jpeg,headers={'Content-Type':'image/jpeg','X-Content-SHA256':'f'*64 if corrupt[0] else hashlib.sha256(jpeg).hexdigest(),'X-Frame-ID':'pi-overview:fixture:1','X-Frame-SHA256':hashlib.sha256(jpeg).hexdigest()})
        return httpx.Response(200,json={'software_fixture':True})
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,data_dir=str(store.root),password_salt='fixture',
        password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex(),nodes=[dict(id='pi',host='memory-pi',port=8092,token='software-token')]))
    monkeypatch.setattr(module,'CLIENT',httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    with TestClient(module.app) as client:
        path='/api/lab/vision/calibration/preview/pi-overview:fixture:1'
        assert client.get(path).status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        response=client.get(path);assert response.status_code==200 and response.content==jpeg
        corrupt[0]=True;assert client.get(path).status_code==503
        before=len(calls)
        for suffix in ('board/pdf?file=/etc/passwd','status?session_id=bad','status?session_id=a&session_id=b'):
            assert client.get('/api/lab/vision/calibration/'+suffix).status_code==400
        assert len(calls)==before
        body={'measurements':{'operator_id':'spoofed-client','printed_ruler_length_mm':100}}
        assert client.post('/api/lab/vision/calibration/solve',json=body).status_code==403
        assert client.post('/api/lab/vision/calibration/solve',json=body,headers=headers).status_code==200
        assert calls[-1]['body']['measurements']['operator_id'].startswith('authenticated-session:')
        assert client.post('/api/lab/vision/calibration/execute',json={},headers=headers).status_code==404
        assert client.post('/api/lab/vision/calibration/accept',json={},headers=headers).status_code==200
        assert all('/robot/' not in row['path'] and 'teleop' not in row['path'] for row in calls)
