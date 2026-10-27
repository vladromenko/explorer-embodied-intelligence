"""Actual Node HTTP profile on localhost; no devices or physical peers."""
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from explorer_shared.node_agent import NodeAgent, AgentHTTPServer, make_handler
from explorer_shared.source_only_runtime import SourceOnlyRetarget


def test_source_only_constructor_and_lifecycle_never_construct_peer_service_or_worker(tmp_path,monkeypatch):
    from explorer_shared.pi_native_retarget import PiNativeRetargetService
    from explorer_shared.native_stream03_client import NativeStreamClient
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append('FORBIDDEN');raise AssertionError('Source-only profile cannot construct/read a physical retarget peer')
    monkeypatch.setattr(PiNativeRetargetService,'from_config',forbidden)
    monkeypatch.setattr(NativeStreamClient,'__init__',forbidden)
    monkeypatch.setattr(Path,'home',lambda:tmp_path/'isolated-home')
    config=tmp_path/'config';config.mkdir();(config/'arm-retarget-peer.json').write_text('INVALID PRIVATE PEER MUST NOT BE READ')
    agent=NodeAgent('pi','pi',tmp_path/'state','local-test-source-only-token',sources={},retarget_config_dir=config,pi_runtime_profile='so101_source_only')
    try:
        assert isinstance(agent.arm_retarget,SourceOnlyRetarget)
        agent.start()
        state=agent.retarget_status()
        assert not state['enabled'] and state['worker_started'] is False and state['native_control_calls']==0
        assert agent.inventory()['pi_runtime_profile']=='so101_source_only'
        assert state['recording']['active'] is None
        assert agent.arm_retarget.stop()['native_STOP_delivered'] is False
    finally:agent.close()
    assert calls==[]


def test_authenticated_localhost_source_only_route_rejects_all03_mutations_before_peer_IO(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path/'isolated-home')
    token='local-test-source-only-token'
    agent=NodeAgent('pi','pi',tmp_path/'state',token,sources={},pi_runtime_profile='so101_source_only')
    server=AgentHTTPServer(('127.0.0.1',0),make_handler(agent));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    def request(path,body=None,authenticated=True):
        return urlopen(Request(origin+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+token} if authenticated else {}),timeout=2)
    try:
        with request('/arm-retarget/status') as response:assert json.load(response)['enabled'] is False
        with request('/arm-retarget/recordings') as response:assert json.load(response)['recordings']==[]
        for operation in ('start','recenter','lease','stop','activation/status','activation/challenge','activation/authorize','activation/revoke',
                          'calibration/status','calibration/preview','calibration/capture','calibration/accept','record/start'):
            with pytest.raises(HTTPError) as error:request('/arm-retarget/'+operation,{})
            assert error.value.code==409
            assert 'CONTINUITY_QUALIFICATION_REQUIRED' in error.value.read().decode()
        with pytest.raises(HTTPError) as error:request('/arm-retarget/start',{},authenticated=False)
        assert error.value.code==401
        assert agent.retarget_status()['native_control_calls']==0
    finally:server.shutdown();server.server_close();thread.join(2);agent.close()


@pytest.mark.parametrize('mode,profile',[('pi','unknown'),('nano','so101_source_only'),('nano','so101_lab')])
def test_invalid_profiles_fail_before_constructing_devices(tmp_path,mode,profile):
    with pytest.raises(ValueError,match='runtime profile'):
        NodeAgent('fixture',mode,tmp_path/'state','local-test-source-only-token',sources={},pi_runtime_profile=profile)


@pytest.mark.parametrize('profile',['so101_source_only','so101_lab'])
def test_profile_wires_capture_to_the_same_single_station_and_camera_without_opening_another_owner(tmp_path,monkeypatch,profile):
    from explorer_shared import so101_station
    from explorer_shared.node_agent import CameraSource
    home=tmp_path/'home';(home/'.config/explorer').mkdir(parents=True)
    (home/'.config/explorer/arms.json').write_text('{}');monkeypatch.setattr(Path,'home',lambda:home)
    constructions=[];commands=[]
    class ExistingStation:
        def __init__(self,config,state,camera):
            constructions.append(self);self.root=state/'station';self.root.mkdir();self.camera=camera
            self.boot='software-pi';self.clock_domain='pi:software-pi';self.lock=threading.RLock();self.mode='idle'
        def status(self):return {'available':False,'mode':'idle','roles':{},'measurements':{},'control_epoch':'fixture-native'}
        def command(self,operation,body):
            commands.append((operation,body))
            assert operation=='stop','Source-only legacy mutations must not enter the original Station'
            return self.status()
        def archive(self,*args):raise AssertionError('Constructor must not read station archive')
        def close(self):pass
    monkeypatch.setattr(so101_station,'SO101Station',ExistingStation)
    if profile=='so101_lab':
        from types import SimpleNamespace
        from explorer_shared.pi_native_retarget import PiNativeRetargetService
        def existing_direct(station,state,config):
            assert station is constructions[0]
            geometry=SimpleNamespace(status=lambda:{'state':'BOOTSTRAP_DERIVED','physical_acceptance':False})
            return PiNativeRetargetService(geometry,station,state,client=None)
        monkeypatch.setattr(PiNativeRetargetService,'from_config',existing_direct)
    camera=CameraSource('pi-overview','single-software-cache')
    agent=NodeAgent('pi','pi',tmp_path/'state','local-test-source-only-token',sources={'pi-overview':camera},pi_runtime_profile=profile)
    try:
        assert len(constructions)==1 and agent.station is constructions[0]
        assert agent.station_startup_guard.buses is agent.station.buses
        assert agent.station_observed.validated_start==agent.station_startup_guard.validated_start
        assert agent.inventory()['station_startup_safety']['disarmed_until_observed_start']
        assert agent.station_capture.station is agent.station and agent.station.camera is camera
        assert list(agent.sources)==['pi-overview'] and agent.station_capture.status()['capture'] is None
        server=AgentHTTPServer(('127.0.0.1',0),make_handler(agent));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            origin='http://127.0.0.1:'+str(server.server_port)
            with urlopen(Request(origin+'/station/status',headers={'Authorization':'Bearer local-test-source-only-token'}),timeout=2) as response:
                status=json.load(response)
                assert status['startup_safety']['disarmed_until_observed_start']
                assert status['startup_safety']['forwarded_write_calls']==0
            if profile=='so101_lab':
                with urlopen(Request(origin+'/arm-retarget/status',headers={'Authorization':'Bearer local-test-source-only-token'}),timeout=2) as response:
                    direct=json.load(response)
                    assert direct['registration']['state']=='BOOTSTRAP_DERIVED'
                    assert not direct['physical_path_admission']['allowed'] and direct['source04_capture_supported']
                for operation in ('start','recenter','lease','activation/challenge','activation/authorize'):
                    with pytest.raises(HTTPError) as denied:
                        urlopen(Request(origin+'/arm-retarget/'+operation,data=b'{}',headers={'Authorization':'Bearer local-test-source-only-token'}),timeout=2)
                    assert denied.value.code==409 and b'CONTINUITY_QUALIFICATION_REQUIRED' in denied.value.read()
            def post(operation):
                return urlopen(Request(origin+'/station/'+operation,data=b'{}',headers={'Authorization':'Bearer local-test-source-only-token'}),timeout=2)
            for operation in ('teleop/start','teleop/recenter','policy/start','action','record/start','record/stage','record/finish'):
                with pytest.raises(HTTPError) as denied:post(operation)
                assert denied.value.code==409
            assert commands==[]
            with post('stop') as response:
                reply=json.load(response);assert reply['original_station_stop_confirmed'] is True and reply['direct_source_stop_confirmed'] is False
            assert commands==[('stop',{})]
        finally:server.shutdown();server.server_close();thread.join(2)
    finally:agent.close()
