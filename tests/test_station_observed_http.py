"""Real authenticated local Node HTTP + SAME fake station/provider, no devices."""
import hashlib
from http.server import ThreadingHTTPServer
import json
from types import SimpleNamespace
import threading
from urllib.request import Request,urlopen
from urllib.error import HTTPError

import pytest

from explorer_shared.node_agent import make_handler
from explorer_shared.station_observed_http import StationObservedHttp
from test_station_observed_activation import rig


def test_actual_node_permission_display_authorize_atomic_command_and_scoped_stop_without_retarget_preemption():
    provider,station,camera,tick,selected=rig();retarget_stops=[];token='local-private-source-test-token'
    agent=SimpleNamespace(token=token,mode='pi',station=station,station_observed=provider,mobile_records=None,
        arm_retarget=SimpleNamespace(status=lambda:{'state':'IDLE'},stop=lambda:retarget_stops.append(True)))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port);calls=[]
    def request(method,path,body):
        calls.append((method,path))
        data=json.dumps(body).encode() if body is not None else None
        with urlopen(Request(base+path,data=data,headers={'Authorization':'Bearer '+token},method=method),timeout=2) as response:
            return json.load(response)
    facade=StationObservedHttp(request,selected);assert not calls and not station.calls
    try:
        with pytest.raises(HTTPError) as denied:urlopen(base+'/station/observed-test/status',timeout=2)
        assert denied.value.code==401 and not station.calls
        challenge=facade.challenge()
        with urlopen(Request(base+challenge['frame_path'],headers={'Authorization':'Bearer '+token}),timeout=2) as response:
            assert hashlib.sha256(response.read()).hexdigest()==response.headers['X-JPEG-SHA256']==challenge['frame']['jpeg_sha256']
        grant=facade.authorize({'challenge_id':challenge['challenge_id'],'displayed_jpeg_sha256':challenge['frame']['jpeg_sha256'],
            'expected_control_epoch':challenge['control_epoch'],'observing':True,'robot_workspace_checked':True,
            'user_observed_test_confirmed':True,'actor_id':'forged-browser'},authenticated_actor='actual-server-owner')
        status=facade.status()
        assert grant['actor_id']=='actual-server-owner' and not station.calls and status['execution_permission']
        assert status['source_stamp']=={'monotonic_ns':tick[0],'boot_id':station.boot,'clock_domain':station.clock_domain}
        with pytest.raises(HTTPError):request('POST','/station/policy/start',{'expected_control_epoch':grant['control_epoch'],'observing':True})
        assert not station.calls  # an old ungated client cannot borrow this observed epoch
        assert facade.check(grant['grant_id'],selected)['control_epoch']==grant['control_epoch']
        started=facade.command(grant['grant_id'],selected,'policy/start',{'expected_control_epoch':grant['control_epoch']})
        assert started['status']['control_epoch']==started['grant']['control_epoch']!=grant['control_epoch']
        assert not started['dispatch_claimed_from_HTTP_acceptance']
        assert not facade.revoke('OLD_REPLY',expected_grant_id=grant['grant_id'],expected_control_epoch=grant['control_epoch'])['revoked']
        stopped=facade.command(grant['grant_id'],selected,'stop',{'expected_control_epoch':started['grant']['control_epoch']})
        assert stopped['status']['mode']=='idle' and not stopped['grant']['execution_permission']
        assert not provider.owns_native_epoch()
        assert [item[0] for item in station.calls]==['policy/start','stop'] and not retarget_stops
        with pytest.raises(HTTPError):request('POST','/station/observed-test/revoke',{'expected_grant_id':grant['grant_id']})
        with pytest.raises(HTTPError):request('GET','/station/observed-test/status?unexpected=1',None)
        with pytest.raises(ValueError):facade.command(grant['grant_id'],selected,'firmware/reset',{})
        assert len(station.calls)==2
    finally:
        server.shutdown();server.server_close();thread.join(2)
