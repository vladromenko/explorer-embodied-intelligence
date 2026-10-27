"""Actual authenticated localhost handler; competing fake station never actuates."""
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError

import pytest

from explorer_shared.node_agent import make_handler


@pytest.mark.parametrize('claim',[{'state':'CLUTCHED'},{'state':'ACTIVATING'},{'state':'CLUTCHING'},
    {'state':'INTERRUPTED','source_cache_claimed':True},{'state':'UNKNOWN','target_control_epoch':'owned-target'}])
def test_clutched_or_uncertain_source_blocks_competing_station_controller_at_HTTP_boundary(claim):
    calls=[];status=dict(claim)
    agent=SimpleNamespace(token='localhost-fake-station-private-token',mode='pi',legacy_bridge=None,lab_vision=None,
        station_observed=None,station=SimpleNamespace(command=lambda op,body:calls.append((op,body)) or {'accepted':body}),
        arm_retarget=SimpleNamespace(status=lambda:status))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,body):
        with urlopen(Request('http://127.0.0.1:'+str(server.server_port)+path,data=json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+agent.token}),timeout=2) as response:return json.load(response)
    try:
        for path in ('teleop/start','policy/start','teleop/recenter','action'):
            with pytest.raises(HTTPError) as rejected:request('/station/'+path,{'explicit':'fake owner action'})
            assert rejected.value.code==409
        assert calls==[]
        status.clear();status['state']='IDLE'
        original={'positions':[1.25,2.5,3.75,4,5,6],'duration':.125,'held':True}
        assert request('/station/action',original)['accepted']==original
        assert calls==[('action',original)]  # Same original trajectory fields, no shaping.
    finally:
        server.shutdown();thread.join(timeout=2);server.server_close()


def test_original_station_stop_precedes_blocking_source_cleanup_and_retains_unknown_outcome():
    from concurrent.futures import ThreadPoolExecutor
    stopped=threading.Event();entered=threading.Event();release=threading.Event();order=[]
    def original(operation,body):
        assert operation=='stop';order.append('original_station_stop');stopped.set();return {'mode':'idle'}
    def cleanup():
        order.append('source_cleanup');entered.set();assert release.wait(2)
        raise RuntimeError('Fake native reply unknown; no physical request')
    agent=SimpleNamespace(token='localhost-fake-station-private-token',mode='pi',legacy_bridge=None,lab_vision=None,
        station_observed=None,station=SimpleNamespace(command=original),arm_retarget=SimpleNamespace(stop=cleanup))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request():
        try:
            urlopen(Request('http://127.0.0.1:'+str(server.server_port)+'/station/stop',data=b'{}',
                headers={'Authorization':'Bearer '+agent.token}),timeout=3)
        except HTTPError as error:return error.code,json.load(error)
        raise AssertionError('Unknown cleanup cannot be returned as globally confirmed')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(request);assert stopped.wait(.5);assert entered.wait(.5)
            assert order==['original_station_stop','source_cleanup'] and not pending.done()
            release.set();code,body=pending.result(timeout=2)
            assert code==409 and body['original_station_stop_confirmed'] is True
            assert body['station']['mode']=='idle' and body['direct_source_stop_confirmed'] is False
    finally:
        release.set();server.shutdown();thread.join(timeout=2);server.server_close()
