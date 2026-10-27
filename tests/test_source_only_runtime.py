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


@pytest.mark.parametrize('mode,profile',[('pi','unknown'),('nano','so101_source_only')])
def test_invalid_profiles_fail_before_constructing_devices(tmp_path,mode,profile):
    with pytest.raises(ValueError,match='runtime profile'):
        NodeAgent('fixture',mode,tmp_path/'state','local-test-source-only-token',sources={},pi_runtime_profile=profile)


def test_profile_wires_capture_to_the_same_single_station_and_camera_without_opening_another_owner(tmp_path,monkeypatch):
    from explorer_shared import so101_station
    from explorer_shared.node_agent import CameraSource
    home=tmp_path/'home';(home/'.config/explorer').mkdir(parents=True)
    (home/'.config/explorer/arms.json').write_text('{}');monkeypatch.setattr(Path,'home',lambda:home)
    constructions=[]
    class ExistingStation:
        def __init__(self,config,state,camera):
            constructions.append(self);self.root=state/'station';self.root.mkdir();self.camera=camera
            self.boot='software-pi';self.clock_domain='pi:software-pi';self.lock=threading.RLock();self.mode='idle'
        def status(self):return {'available':False,'mode':'idle','roles':{},'measurements':{},'control_epoch':'fixture-native'}
        def command(self,*args):raise AssertionError('Constructor must not issue a station operation')
        def archive(self,*args):raise AssertionError('Constructor must not read station archive')
        def close(self):pass
    monkeypatch.setattr(so101_station,'SO101Station',ExistingStation)
    camera=CameraSource('pi-overview','single-software-cache')
    agent=NodeAgent('pi','pi',tmp_path/'state','local-test-source-only-token',sources={'pi-overview':camera},pi_runtime_profile='so101_source_only')
    try:
        assert len(constructions)==1 and agent.station is constructions[0]
        assert agent.station_capture.station is agent.station and agent.station.camera is camera
        assert list(agent.sources)==['pi-overview'] and agent.station_capture.status()['capture'] is None
    finally:agent.close()
