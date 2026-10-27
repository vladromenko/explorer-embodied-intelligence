"""Authenticated loopback metadata against original finite fake-bus Station."""
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError

import pytest

from explorer_shared.node_agent import make_handler
from explorer_shared.station_observed_http import StationObservedHttp
from test_station_publication_gate import worker_rig


def test_authenticated_interlock_status_and_arm_reject_numeric_injection_without_serial_writes(tmp_path,monkeypatch):
    station,provider,selected,start,bus,samples,tick,StopAfter,_=worker_rig(tmp_path,monkeypatch)
    monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:tick[0])
    grant=start();station.stop_event=StopAfter();station._work();before=list(bus.writes)
    token='SOFTWARE-ONLY-LOCAL-INTERLOCK-TOKEN'
    agent=SimpleNamespace(token=token,mode='pi',station=station,station_observed=provider,mobile_records=None,
        legacy_bridge=None,lab_vision=None,arm_retarget=SimpleNamespace(status=lambda:{'state':'IDLE'}))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
    def request(method,path,body):
        raw=json.dumps(body).encode() if body is not None else None
        with urlopen(Request(base+path,data=raw,method=method,headers={'Authorization':'Bearer '+token}),timeout=2) as response:return json.load(response)
    facade=StationObservedHttp(request,selected)
    try:
        with pytest.raises(HTTPError) as denied:urlopen(base+'/station/observed-test/cooperative/status',timeout=2)
        assert denied.value.code==401
        actual=facade.cooperative('status');assert actual['phase']=='UNBOUND'
        body=dict(grant_id=grant['grant_id'],expected_control_epoch=grant['control_epoch'],expected_revision=actual['revision'])
        bound=facade.cooperative('bind',body,authenticated_actor=grant['actor_id'])
        arm={**body,'interlock_id':bound['interlock_id'],'expected_revision':bound['revision']}
        with pytest.raises(ValueError):facade.cooperative('arm',{**arm,'native6':[0]*6},authenticated_actor=grant['actor_id'])
        with pytest.raises(HTTPError):request('POST','/station/observed-test/cooperative/arm',{**arm,'native6':[0]*6,'actor_id':grant['actor_id']})
        with pytest.raises(HTTPError):facade.cooperative('arm',arm,authenticated_actor='foreign-authenticated-actor')
        assert provider.cooperative_interlock.status()['revision']==bound['revision']
        armed=facade.cooperative('arm',arm,authenticated_actor=grant['actor_id'])
        assert armed['armed'] and armed['frozen']['original_last_command']==station.last_command
        assert not armed['capability_ready']  # no made-up gripper-direction proof
        assert facade.cooperative('status')['armed']
        with pytest.raises(HTTPError):request('GET','/station/observed-test/cooperative/status?arbitrary=path',None)
        assert bus.writes==before
    finally:server.shutdown();server.server_close();thread.join(2)
