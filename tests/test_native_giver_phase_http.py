"""Authenticated source HTTP + actual localhost Node/Bridge, fake native only."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import io
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from explorer_shared.legacy_bridge import LegacyBridge
from explorer_shared import giver_hold_persistence05 as wal
from explorer_shared.native_stream03_api import mount_native_stream_api
from explorer_shared.node_agent import make_handler
from test_giver_phase_lifecycle05 import fixture,ACTOR
from test_native_giver_persistence import fixture as native_fixture

TOKEN='SOFTWARE-ONLY-native-phase-secret'


def client_for(owner,mux,activation):
    app=FastAPI();mount_native_stream_api(app,mux,token=TOKEN,observed_test_permit=activation.allowed,activation=activation,
        cooperative=owner,reference_reader=owner.reference)
    return TestClient(app)


def authorize_fixture(values,receipts,identity):
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    tick[0]+=.1;activation.id=identity;activation.valid=True
    receipts[identity]={'actor_id':ACTOR,'input_mode':'cooperative_policy','native_monotonic_ns':tap.clock(),
        'camera_frame':{'received_monotonic_ns':tap.clock(),'clock_domain':tap.clock_domain,'boot_id':tap.boot_id,
            'frame_id':'fake-source-frame-'+identity,'jpeg_sha256':'f'*64}}


def test_fixed_authenticated_http_runs_explicit_control_and_metadata_actor_is_source_derived(tmp_path):
    values,body,new_user,park,receipts=fixture(tmp_path,auto_park=False)
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values;headers={'Authorization':'Bearer '+TOKEN}
    old_epoch=mux.epoch;request=body(control_epoch=old_epoch)
    assert not owner.status()['capabilities']['explicit_user_giver_phase_http']
    with client_for(owner,mux,activation) as client:
        assert client.get('/api/explorer/cooperative/status',headers=headers).json()['phase_lifecycle']['authenticated_fixed_http_operations_installed']
        assert client.get('/api/explorer/cooperative/status',headers=headers).json()['capabilities']['explicit_user_giver_phase_http']
        for operation in ('park','adopt','begin','resume'):
            assert client.post('/api/explorer/cooperative/phase/'+operation,json={}).status_code==401
        assert teleop.native_stop==0
        base='/api/explorer/cooperative/phase/'
        assert client.post(base+'park?unknown=1',json=request,headers=headers).status_code==400
        assert client.post(base+'park',json={**request,'actor_id':'FORGED'},headers=headers).status_code==409
        assert client.post(base+'park',json={**request,'servo_deg':[90]*6},headers=headers).status_code==409
        assert teleop.native_stop==0 and mux.active
        stopped=client.post(base+'park',json=request,headers=headers);assert stopped.status_code==200,stopped.text
        source=stopped.json();assert source['native_command_called'] and teleop.native_stop==1
        token=source['park']['park_request_id'];previous=deepcopy(owner.guard.binding)
        authorize_fixture(values,receipts,'HTTP-new-USER')
        adopt=body(authorization_id='HTTP-new-USER',park_request_id=token,previous_authorization_id=previous['authorization_id'],
            previous_native_generation=previous['native_generation'],previous_control_epoch=previous.get('native_control_epoch'))
        before=(len(publisher.messages),deepcopy(teleop.native_drive),teleop.lease)
        response=client.post(base+'adopt',json=adopt,headers=headers);assert response.status_code==200,response.text
        assert not response.json()['native_command_called'] and not response.json()['execution_authority']
        assert before==(len(publisher.messages),teleop.native_drive,teleop.lease)
        assert response.json()['successor']['new_owner']['observed_source_authorization']['actor_id']==ACTOR
        begun=client.post(base+'begin',json=body(park_request_id=token),headers=headers);assert begun.status_code==200,begun.text
        epoch=begun.json()['native']['control_epoch'];lease=teleop.lease
        generic=client.post('/api/explorer/cooperative/resume',json={**owner.guard.binding,'control_epoch':epoch,'observing':True},headers=headers)
        assert generic.status_code==409 and 'parked-phase' in generic.json()['detail'] and teleop.lease==lease
        for operation in ('resume','recenter','base','input','begin'):
            response=client.post('/api/explorer/retarget/'+operation,json={**owner.guard.binding,'control_epoch':epoch,'observing':True,'actions':{}},headers=headers)
            assert response.status_code==409 and 'parked' in response.json()['detail'] and teleop.lease==lease
        resumed=client.post(base+'resume',json=body(park_request_id=token,control_epoch=epoch),headers=headers)
        assert resumed.status_code==200,resumed.text
        assert resumed.json()['native_command_called'] and owner.guard.parked is None and owner.guard.armed
        assert teleop.lease==tick[0]+.45 and len(publisher.messages)==1


def test_real_localhost_node_bridge_preserves_fixed_phase_post_and_source_auth_without_actor_payload(tmp_path,monkeypatch):
    (tmp_path/'native').mkdir()
    values,body,new_user,park,receipts=fixture(tmp_path/'native',auto_park=False)
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    private=tmp_path/'bridge';(private/'config').mkdir(parents=True);(private/'config/access_token').write_text(TOKEN)
    bridge=LegacyBridge(private);seen=[];client=client_for(owner,mux,activation)
    class Response(io.BytesIO):
        def __init__(self,status,raw):super().__init__(raw);self.status=status
    def fake_loopback(request,timeout):
        route=request.full_url.removeprefix('http://127.0.0.1:8080');payload=json.loads(request.data) if request.data else None
        seen.append((request.get_method(),route,payload))
        reply=client.request(request.get_method(),route,json=payload,headers={'Authorization':request.headers['Authorization']})
        return Response(reply.status_code,reply.content)
    monkeypatch.setattr('explorer_shared.legacy_bridge.urlopen',fake_loopback)
    node_token='SOFTWARE-ONLY-node-phase-secret'
    agent=SimpleNamespace(mode='nano',mobile_records=None,mobile_capture=None,station=None,lab_vision=None,legacy_bridge=bridge,token=node_token,
        legacy_request=lambda path,payload=None,query=None:bridge.request(path,payload,query=query))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
    request_body=body(control_epoch=mux.epoch)
    def send(path,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        return urlopen(Request(origin+path,data=json.dumps(request_body).encode(),headers=headers),timeout=2)
    try:
        with pytest.raises(HTTPError) as unauth:send('/legacy/explorer/cooperative/phase/park')
        assert unauth.value.code==401 and not seen
        with pytest.raises(HTTPError):send('/legacy/explorer/cooperative/phase/park?unknown=1',node_token)
        assert not seen
        with send('/legacy/explorer/cooperative/phase/park',node_token) as reply:
            result=json.load(reply);assert reply.status==200 and result['native_command_called']
        assert seen==[('POST','/api/explorer/cooperative/phase/park',request_body)] and teleop.native_stop==1
        for unknown in ('status','release','reset','../resume','resume?duration=1'):
            with pytest.raises(ValueError):bridge.request('explorer/cooperative/phase/'+unknown,{})
        with pytest.raises(ValueError):bridge.request('explorer/cooperative/phase/park')  # GET never dispatches.
    finally:
        server.shutdown();server.server_close();thread.join(2);client.close()


def test_malformed_unarmed_saved_binding_fails_closed_before_phase_actor_lookup(tmp_path):
    values=native_fixture(tmp_path);path=values[-1];saved=json.loads(path.read_bytes())
    assert saved['state']['armed'] is False;saved['state']['binding']=[]
    saved['sha256']=wal.digest({key:value for key,value in saved.items() if key!='sha256'});path.write_bytes(wal.canonical(saved))
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=native_fixture(tmp_path,bind=False)
    with client_for(owner,mux,activation) as client:
        reply=client.post('/api/explorer/cooperative/phase/park',json={},headers={'Authorization':'Bearer '+TOKEN})
        assert reply.status_code==409
    assert owner.guard.journal.recovery_required and owner.guard.armed and owner.guard.binding is None
    assert publisher.messages==[] and teleop.native_stop==0
