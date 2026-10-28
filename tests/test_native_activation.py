"""Pinned real cached bytes + authenticated metadata-only observed activation."""
from copy import deepcopy
import hashlib
import io
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from explorer_shared.native_activation import NativeCachedObservation,ObservedNativeActivation
from explorer_shared.native_stream03 import NativeTeleopMux,NativeDispatchTap
from explorer_shared.native_stream03_api import mount_native_stream_api
from explorer_shared import legacy_bridge
from explorer_shared import node_agent
from test_native_stream03 import Arm,Teleop,evidence


TOKEN='local-only-observed-mock-token'


def fixture(tmp_path):
    tick=[10_000_000_000];data=tmp_path/'data';data.mkdir()
    jpeg=b'\xff\xd8exact-existing-cached-camera-frame\xff\xd9'
    (data/'frame-raw.jpg').write_bytes(jpeg)
    (data/'camera-frame.json').write_text(json.dumps({'received_monotonic_ns':tick[0],
        'same_frame_hash_verified':True,'frame_id':'source-native-frame-1','jpeg_sha256':hashlib.sha256(jpeg).hexdigest(),
        'camera_id':'onboard_rgbd','jpeg':'frame-raw.jpg'}))
    arm=Arm();teleop=Teleop(arm);teleop.arm=arm
    tap=NativeDispatchTap(boot_id='boot',clock_domain='nano:boot',clock=lambda:tick[0])
    mux=NativeTeleopMux(teleop,arm,tap,clock=lambda:tick[0]/1e9);mux.install()
    camera=NativeCachedObservation(tmp_path,'boot',clock=lambda:tick[0])
    gate=ObservedNativeActivation(mux,camera,clock=lambda:tick[0]);mux.observation_permit=gate.allowed;mux.on_invalidate=gate.revoke
    mux.chassis_permit=gate.base_allowed
    app=FastAPI();mount_native_stream_api(app,mux,token=TOKEN,activation=gate,observed_test_permit=gate.allowed)
    return TestClient(app),gate,mux,teleop,arm,tick,jpeg


def authorize(client,**extra):
    headers={'Authorization':'Bearer '+TOKEN}
    challenge=client.get('/api/explorer/retarget/activation/challenge',headers=headers).json()
    body={key:True for key in ('observing','neutral','space_clear','user_observed_test_confirmed')}
    body.update(challenge_id=challenge['challenge_id'],displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],actor_id='explicit-mock-operator')
    body.update(extra)
    response=client.post('/api/explorer/retarget/activation/authorize',json=body,headers=headers)
    assert response.status_code==200,response.text
    return response.json(),challenge


def test_metadata_authorization_has_exact_pinned_binary_frame_and_never_moves_or_renews_native_lease(tmp_path):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path);headers={'Authorization':'Bearer '+TOKEN}
    with client:
        denied=client.get('/api/explorer/retarget/activation/challenge');assert denied.status_code==401
        challenge=client.get('/api/explorer/retarget/activation/challenge',headers=headers).json()
        frame=client.get(challenge['frame_path'],headers=headers)
        assert frame.status_code==200 and frame.content==jpeg
        assert frame.headers['X-JPEG-SHA256']==hashlib.sha256(jpeg).hexdigest()
        assert frame.headers['X-Frame-ID']=='source-native-frame-1'
        lease=teleop.lease;result,_=authorize(client)
        assert result['execution_permission'] and result['authorization_id']
        assert teleop.lease==lease and arm.calls==[] and arm.publications==[] and teleop.native_drive==[]
        assert not mux.active and result['authorization_receipt']['physical_command_count']==0
        assert not result['hardware_success_claimed']


def test_old_authorization_id_cannot_operate_new_epoch_and_stop_camera_loss_revoke_existing_permit(tmp_path):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path);headers={'Authorization':'Bearer '+TOKEN}
    with client:
        auth,_=authorize(client);ident=auth['authorization_id']
        def post(op,body):return client.post('/api/explorer/retarget/'+op,json={**body,'authorization_id':ident},headers=headers)
        denied=client.post('/api/explorer/retarget/begin',json={'observing':True,'neutral':True,'authorization_id':'old'},headers=headers)
        assert denied.status_code==409
        epoch=post('begin',{'observing':True,'neutral':True}).json()['control_epoch']
        assert post('resume',{'control_epoch':epoch,'observing':True}).status_code==200
        assert post('input',{'control_epoch':epoch,'source_sequence':1,'joint_velocity_deg_s':[1.]*6,'source_evidence':evidence()}).status_code==200
        teleop._update_arm_stream_locked({},False);permit=arm.calls[-1]['permit'];assert permit()
        new_epoch=post('recenter',{'control_epoch':epoch}).json()['control_epoch']
        assert new_epoch!=epoch and gate.allowed() and not permit()
        post('resume',{'control_epoch':new_epoch,'observing':True})
        post('input',{'control_epoch':new_epoch,'source_sequence':2,'joint_velocity_deg_s':[1.]*6,'source_evidence':evidence()})
        teleop._update_arm_stream_locked({},False);permit=arm.calls[-1]['permit'];assert permit()
        tick[0]+=2_000_000_001
        # Keep mock user/source leases live solely to isolate camera revocation.
        teleop.lease=100.;mux.intent_deadline=100.
        assert not permit() and gate.status()['revoked_reason']=='CAMERA_SOURCE_UNAVAILABLE'
        assert not arm.publications  # all control boundaries were mocked


def test_challenge_expiry_changed_hash_and_missing_user_test_confirmation_are_rejected(tmp_path):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path)
    challenge=gate.challenge();body={key:True for key in ('observing','neutral','space_clear','user_observed_test_confirmed')}
    body.update(challenge_id=challenge['challenge_id'],displayed_jpeg_sha256='0'*64,actor_id='explicit-mock-operator')
    with pytest.raises(ValueError,match='Pinned'):gate.authorize(body)
    body['displayed_jpeg_sha256']=challenge['frame']['jpeg_sha256'];body['user_observed_test_confirmed']=False
    with pytest.raises(ValueError,match='Separate'):gate.authorize(body)
    body['user_observed_test_confirmed']=True;tick[0]+=2_000_000_001
    with pytest.raises(ValueError):gate.authorize(body)
    assert not gate.allowed() and arm.calls==[]


def test_late_web_revoke_for_old_authorization_does_not_revoke_a_new_explicit_session(tmp_path):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path)
    with client:
        old,_=authorize(client);new,_=authorize(client)
        assert old['authorization_id']!=new['authorization_id']
        response=client.post('/api/explorer/retarget/activation/revoke',headers={'Authorization':'Bearer '+TOKEN},json={'expected_authorization_id':old['authorization_id']})
        assert response.status_code==200 and response.json()['revoked'] is False
        assert gate.status()['authorization_id']==new['authorization_id'] and gate.allowed()
    assert not arm.publications and not teleop.native_drive


def test_status_during_recenter_transition_cannot_revoke_new_clutched_epoch(tmp_path):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path)
    with client:auth,_=authorize(client)
    ident=auth['authorization_id'];epoch=gate.transition(lambda:mux.begin(observing=True,neutral=True),ident)['control_epoch']
    mux.resume(epoch,observing=True)
    def recenter_with_concurrent_status():
        result=mux.recenter(epoch)
        assert gate.status()['execution_permission']  # before bind_epoch returns
        return result
    new=gate.transition(recenter_with_concurrent_status,ident)['control_epoch']
    assert new!=epoch and gate.allowed()
    mux._stop_all();assert not gate.allowed() and gate.authorization is None


def test_metadata_stop_while_begin_reply_pending_scoped_stops_exact_late_native_owner(tmp_path):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path)
    with client:auth,_=authorize(client)
    entered,release=threading.Event(),threading.Event();errors=[]
    def begin_then_wait():
        result=mux.begin(observing=True,neutral=True);entered.set();assert release.wait(2);return result
    def late():
        try:gate.transition(begin_then_wait,auth['authorization_id'])
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=late);thread.start();assert entered.wait(1)
    gate.revoke('EXPLICIT_COOPERATIVE_STOP');release.set();thread.join(2)
    assert errors and not thread.is_alive() and not mux.active and teleop.stop_latched and teleop.native_stop==1
    assert arm.publications==[] and teleop.native_drive==[[0.,0.,0.]]


def test_bridge_pinned_frame_has_fixed_typed_path_and_home_keeps_original_observed_ABI(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir();(tmp_path/'config/access_token').write_text(TOKEN)
    bridge=legacy_bridge.LegacyBridge(tmp_path);calls=[]
    class Reply(io.BytesIO):
        status=200
        def __init__(self,raw,headers=None):super().__init__(raw);self.headers=headers or {}
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    jpeg=b'\xff\xd8pinned-camera\xff\xd9'
    def opener(request,timeout):
        calls.append((request.full_url,json.loads(request.data) if request.data else None))
        if request.full_url.endswith('/activation/status'):
            return Reply(json.dumps({'execution_permission':True,'authorization_id':'observed-id',
                                      'native_active':False,'native_stop_latched':True}).encode())
        if '/activation/frame/' in request.full_url:
            return Reply(jpeg,{'X-JPEG-SHA256':hashlib.sha256(jpeg).hexdigest(),'X-Frame-ID':'native-id',
                               'X-Monotonic-NS':'100','X-Boot-ID':'boot'})
        return Reply(b'{"mock_only":true}')
    monkeypatch.setattr(legacy_bridge,'urlopen',opener)
    with pytest.raises(ValueError):bridge.activation_frame('../../status')
    assert bridge.activation_frame('a'*32)[0]==jpeg
    with pytest.raises(ValueError):bridge.request('arm/prepare',{'observing':True,'space_clear':True})
    bridge.request('arm/prepare',{'observing':True,'space_clear':True,'authorization_id':'observed-id','acknowledge_factory_home_motion':True})
    assert calls[-1][0].endswith('/api/arm/prepare') and calls[-1][1]=={'observing':True,'space_clear':True}


def test_failed_old_camera_check_cannot_revoke_a_new_user_authorization(tmp_path):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path)
    with client:auth,_=authorize(client)
    old=auth['authorization_id'];entered,release=threading.Event(),threading.Event()
    original=gate.camera.snapshot
    def snapshot(**kwargs):
        if threading.current_thread().name=='old-camera-check':
            entered.set();assert release.wait(2);raise ValueError('old failed source read')
        return original(**kwargs)
    gate.camera.snapshot=snapshot
    result=[];worker=threading.Thread(name='old-camera-check',target=lambda:result.append(gate.allowed()))
    worker.start();assert entered.wait(2)
    gate.revoke('old_session_closed')
    with client:new,_=authorize(client)
    release.set();worker.join(2)
    assert result==[False] and new['authorization_id']!=old
    assert gate.authorization['authorization_id']==new['authorization_id'] and gate.allowed()


@pytest.mark.parametrize('kind',['revoke','global_stop','owner_generation_change'])
def test_late_observed_confirmation_after_revoke_or_native_change_cannot_authorize(tmp_path,kind):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path)
    challenge=gate.challenge();body={key:True for key in ('observing','neutral','space_clear','user_observed_test_confirmed')}
    body.update(challenge_id=challenge['challenge_id'],displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],actor_id='mock-owner')
    entered,release=threading.Event(),threading.Event();original=gate.camera.snapshot;errors=[]
    def snapshot(**kwargs):
        if threading.current_thread().name=='late-authorize':
            entered.set();assert release.wait(2)
        return original(**kwargs)
    gate.camera.snapshot=snapshot
    def late():
        try:gate.authorize(body)
        except Exception as error:errors.append(error)
    worker=threading.Thread(name='late-authorize',target=late);worker.start();assert entered.wait(2)
    if kind=='global_stop':mux._stop_all()
    elif kind=='owner_generation_change':teleop.generation+=1
    else:gate.revoke('EXPLICIT_OPERATOR_REVOKE')
    release.set();worker.join(2)
    assert len(errors)==1 and not gate.allowed() and gate.authorization is None
    gate.camera.snapshot=original
    with client:new,_=authorize(client)
    assert new['execution_permission'] and arm.calls==[] and arm.publications==[]


@pytest.mark.parametrize('kind',['activation','cooperative'])
def test_node_binary_proxy_requires_auth_and_returns_only_pinned_typed_frame(tmp_path,monkeypatch,kind):
    monkeypatch.setattr(Path,'home',lambda:tmp_path/'home')
    agent=node_agent.NodeAgent('nano','nano',tmp_path/'node',TOKEN,sources={},legacy_dir=tmp_path/'original/data',
                               retarget_config_dir=tmp_path/'config')
    jpeg=b'\xff\xd8native-pinned-only\xff\xd9';seen=[]
    def pinned(ident):
        if ident!='a'*32:raise ValueError('Unknown challenge')
        seen.append(ident);return jpeg,{'X-JPEG-SHA256':hashlib.sha256(jpeg).hexdigest(),'X-Frame-ID':'native-original-frame',
                                      'X-Monotonic-NS':'100','X-Boot-ID':'boot'}
    setattr(agent.legacy_bridge,kind+'_frame',pinned)
    server=node_agent.AgentHTTPServer(('127.0.0.1',0),node_agent.make_handler(agent))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port);path='/native-'+kind+'/frame/'+'a'*32
    try:
        with pytest.raises(HTTPError) as denied:urlopen(origin+path,timeout=2)
        assert denied.value.code==401 and seen==[]
        with urlopen(Request(origin+path,headers={'Authorization':'Bearer '+TOKEN}),timeout=2) as response:
            assert response.read()==jpeg and response.headers['X-Frame-ID']=='native-original-frame'
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(origin+path+'?unknown=1',headers={'Authorization':'Bearer '+TOKEN}),timeout=2)
        assert denied.value.code==404 and seen==['a'*32]
    finally:
        server.shutdown();server.server_close();agent.close()


@pytest.mark.parametrize('input_mode',['so101_leader','keyboard','browser_gamepad','local_gamepad'])
def test_chassis_remains_default_blocked_and_only_fresh_explicit_observed_grant_opens_existing_drive(tmp_path,input_mode):
    client,gate,mux,teleop,arm,tick,jpeg=fixture(tmp_path)
    with client:
        if input_mode=='local_gamepad':teleop.owner='gamepad'
        auth,_=authorize(client,input_mode=input_mode)
        assert not gate.base_allowed() and gate.status()['chassis_motion_allowed'] is False
        with pytest.raises(PermissionError):teleop.drive([.1,0.,0.])
        gate.revoke();auth,_=authorize(client,input_mode=input_mode,owner_chassis_test_authorized=True)
        if input_mode=='so101_leader':
            epoch=gate.transition(lambda:mux.begin(observing=True,neutral=True),auth['authorization_id'])['control_epoch']
            mux.resume(epoch,observing=True)
        else:
            teleop.resume('gamepad' if input_mode=='local_gamepad' else 'keyboard',True)  # SAME original executor; no leader mux
            assert not mux.active
        assert gate.base_allowed()
        teleop.drive([.1,0.,0.])
        assert teleop.native_drive==([[0.,0.,0.],[.1,0.,0.]] if input_mode=='so101_leader' else [[.1,0.,0.]])
        gate.revoke('EXPLICIT_OPERATOR_REVOKE')
        with pytest.raises(PermissionError):teleop.drive([.1,0.,0.])
        assert not gate.status()['chassis_motion_allowed']
