"""Real localhost authenticated node HTTP, measured-cache fixtures; never robots."""
import ast
import copy
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from explorer_shared import node_agent
from explorer_shared.retarget_service import PiRetargetService, registered_geometry
from explorer_shared.pi_nano_route import NanoArmHTTPClient
from test_pi_nano_route import geometry, OriginalHTTPFixture, sample

TOKEN = 'software-fixture-node-token-20261026'


class StationCache:
    def __init__(self, g):
        self.g, self.positions, self.mode, self.epoch = g, [0., -60., 90., -30., 0., 50.], 'idle', 'station-cache-fixture'
        self.stamp, self.available = time.monotonic_ns(), True
    def update(self, positions=None):
        self.positions = positions or self.positions
        self.stamp = time.monotonic_ns()
    def status(self):
        return dict(available=self.available, mode=self.mode, control_epoch=self.epoch,
            roles={'leader':{'calibration_sha256':self.g.profile['source_calibration']['sha256']}},
            measurements=dict(leader=list(self.positions), monotonic_ns=self.stamp, clock_domain='fixture-pi-boot'))
    def close(self):pass


class Camera:
    stale_s = 2.
    def latest(self):return object()
    def close(self):pass


def request(base, path, body=None, auth=True):
    headers = {'Authorization':'Bearer '+TOKEN} if auth else {}
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(base+path, data=data, headers=headers), timeout=3) as response:
        return json.load(response)


@pytest.fixture
def http_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path/'empty-home')
    config = tmp_path/'config';config.mkdir()
    g, profile, _ = geometry(config)
    profile.rename(config/'arm-retarget-registration.json')
    nano = node_agent.NodeAgent('nano','nano',tmp_path/'nano-state',TOKEN,sources={},
        legacy_dir=tmp_path/'original/data',retarget_config_dir=config)
    original = OriginalHTTPFixture()
    nano.legacy_bridge.request = original.request
    nano.arm_retarget.legacy_request = original.request
    nano_server = node_agent.AgentHTTPServer(('127.0.0.1',0),node_agent.make_handler(nano))
    threading.Thread(target=nano_server.serve_forever,daemon=True).start()
    nano_url = 'http://127.0.0.1:'+str(nano_server.server_port)
    station = StationCache(g)
    pi = node_agent.NodeAgent('pi','pi',tmp_path/'pi-state',TOKEN,sources={},retarget_config_dir=config)
    pi.station = station
    pi.arm_retarget = PiRetargetService(g,station,Camera(),tmp_path/'pi-state',NanoArmHTTPClient(nano_url,TOKEN))
    pi.arm_retarget.start_worker()
    pi_server = node_agent.AgentHTTPServer(('127.0.0.1',0),node_agent.make_handler(pi))
    threading.Thread(target=pi_server.serve_forever,daemon=True).start()
    pi_url='http://127.0.0.1:'+str(pi_server.server_port)
    try:
        yield pi_url,nano_url,pi,nano,station,original
    finally:
        pi.arm_retarget.close()
        for server in (pi_server,nano_server):
            server.shutdown();server.server_close()
        pi.close();nano.close()


def begin(base):
    status=request(base,'/arm-retarget/status')
    return request(base,'/arm-retarget/start',dict(expected_control_epoch=status['control_epoch'],observing=True))


def held(base, state, sequence):
    return request(base,'/arm-retarget/lease',dict(control_epoch=state['control_epoch'],sequence=sequence,
        held=True,observing=True))


def test_authenticated_pi_to_nano_real_http_uses_only_existing_arm_executor(http_nodes):
    pi_url,nano_url,pi,nano,station,original=http_nodes
    with pytest.raises(HTTPError) as unauthorized:
        request(pi_url,'/arm-retarget/status',auth=False)
    assert unauthorized.value.code==401
    with pytest.raises(HTTPError) as unauthorized:
        request(nano_url,'/arm-retarget/start',{},auth=False)
    assert unauthorized.value.code==401
    started=begin(pi_url)
    assert started['state']=='ACTIVE' and station.mode=='idle'
    assert not any(op.endswith(('plan','start','lease')) for op,_ in original.calls)
    station.update([3.,-60.,90.,-30.,0.,75.])
    held(pi_url,started,0)
    for _ in range(100):
        status=request(pi_url,'/arm-retarget/status')
        if status['cached_samples_consumed']:break
        time.sleep(.01)
    assert status['last_result']['target_receipt']['state']=='API_ACCEPTED'
    assert status['last_result']['target_receipt']['dispatched'] is None
    assert all(op.startswith('arm/') for op,_ in original.calls)
    assert len([op for op,_ in original.calls if op=='arm/trajectory/start'])==1
    count=len(original.calls)
    station.update([4.,-60.,90.,-30.,0.,80.])
    time.sleep(.15)
    assert len(original.calls)==count  # No invented operator lease or post-release loop.
    journal=pi.arm_retarget.run_dir/'events.jsonl'
    rows=[json.loads(row) for row in journal.read_text().splitlines()]
    assert rows[-1]['source_measurement']['clock_domain']=='fixture-pi-boot'
    assert not rows[-1]['physical_success_inferred']
    stopped=request(pi_url,'/arm-retarget/stop',{'expected_control_epoch':started['control_epoch']})
    assert stopped['state']=='STOPPED' and not stopped['physical_stop_confirmed']
    assert original.calls[-1][0]=='arm/trajectory/stop'
    with pytest.raises(HTTPError):held(pi_url,started,1)


def test_stale_measured_source_is_rejected_before_any_native_start(http_nodes):
    pi_url,_,_,_,station,original=http_nodes
    station.stamp=time.monotonic_ns()-3_000_000_000
    with pytest.raises(HTTPError) as rejected:begin(pi_url)
    assert rejected.value.code==400
    assert not any(op.endswith(('plan','start')) for op,_ in original.calls)


def test_recenter_rotates_pi_and_nano_epochs_and_rejects_old_held_event(http_nodes):
    pi_url,nano_url,_,_,station,_=http_nodes
    started=begin(pi_url)
    old_nano=request(nano_url,'/arm-retarget/status')['control_epoch']
    station.update()
    recentered=request(pi_url,'/arm-retarget/recenter',dict(expected_control_epoch=started['control_epoch'],observing=True))
    assert recentered['control_epoch']!=started['control_epoch']
    assert request(nano_url,'/arm-retarget/status')['control_epoch']!=old_nano
    with pytest.raises(HTTPError):held(pi_url,started,0)


def test_existing_manual_stop_invalidates_direct_owner(http_nodes):
    pi_url,nano_url,_,nano,station,original=http_nodes
    started=begin(pi_url)
    # The fixture only needs its original arm STOP ABI here.
    code,result=nano.legacy_request('arm/trajectory/stop',{})
    assert code==200 and result['direct_retarget_cancel']['state']=='STOPPED'
    station.update([3.,-60.,90.,-30.,0.,75.])
    with pytest.raises(HTTPError):held(pi_url,started,0)
    assert request(nano_url,'/arm-retarget/status')['state']=='STOPPED'
    assert not any(op.endswith('start') for op,_ in original.calls)


def test_competing_manual_action_through_shared_adapter_is_rejected(http_nodes):
    pi_url,_,_,nano,_,original=http_nodes
    begin(pi_url)
    code,body=nano.legacy_request('arm/jog',{'joint':1,'delta':1})
    assert code==409 and 'owns' in body['detail']
    assert not any(op=='arm/jog' for op,_ in original.calls)


def test_missing_registration_and_bad_registration_do_not_contact_target(tmp_path):
    empty=tmp_path/'empty';empty.mkdir()
    g=registered_geometry(empty)
    assert g.status()['state']=='UNREGISTERED'
    service=PiRetargetService(g,None,None,tmp_path/'state')
    assert service.command('start',{})['state']=='UNREGISTERED'
    (empty/'arm-retarget-registration.json').write_text('{invalid')
    assert registered_geometry(empty).status()['missing']==['invalid_registration:JSONDecodeError']


def test_no_second_bus_driver_or_python_continue_in_direct_service():
    from explorer_shared import retarget_service
    tree=ast.parse(Path(retarget_service.__file__).read_text())
    assert not any(isinstance(node,ast.Continue) for node in ast.walk(tree))
    imports=[alias.name for node in ast.walk(tree) if isinstance(node,ast.Import) for alias in node.names]
    assert not {'serial','rclpy','lerobot'}.intersection(imports)


def test_stop_during_pi_start_discards_late_reply_and_new_route_cannot_resume(http_nodes):
    pi_url,nano_url,pi,nano,station,original=http_nodes
    entered,release=threading.Event(),threading.Event()
    native_start=nano.arm_retarget.start
    errors=[]
    def delayed_start(body):
        entered.set()
        assert release.wait(3)
        return native_start(body)
    nano.arm_retarget.start=delayed_start
    def starting():
        try:begin(pi_url)
        except HTTPError as error:errors.append(error.code)
    thread=threading.Thread(target=starting)
    thread.start()
    try:
        assert entered.wait(3)
        stopped=request(pi_url,'/arm-retarget/stop',{})
        assert stopped['state']=='STOPPED'
        release.set();thread.join(3)
        assert not thread.is_alive() and errors==[400]
        assert request(pi_url,'/arm-retarget/status')['state']=='STOPPED'
        assert request(nano_url,'/arm-retarget/status')['state']=='STOPPED'
        assert not any(op.endswith(('plan','start')) for op,_ in original.calls)
    finally:
        release.set();thread.join(3)


def test_lost_native_start_and_cancel_replies_block_new_start_until_explicit_cancel(tmp_path):
    g,path,profile=geometry(tmp_path)
    original=OriginalHTTPFixture()
    original.lose_start_reply=True
    fail_stop=True
    def transport(operation,body=None):
        if operation=='arm/trajectory/stop' and fail_stop:
            raise TimeoutError('lost STOP reply fixture')
        return original.request(operation,body)
    from explorer_shared.pi_nano_route import NanoArmGateway,PiNanoRetargetSession
    from test_pi_nano_route import DirectClientFixture
    gateway=NanoArmGateway(transport,g.status())
    source=PiNanoRetargetSession(g,DirectClientFixture(gateway))
    source.start(sample(g),observing=True)
    result=source.step(sample(g,2,[3.,-60.,90.,-30.,0.,75.]))['target_receipt']
    assert result['state']=='UNKNOWN_OUTCOME' and result['cancel_error']=='TimeoutError'
    assert gateway.status()['stop_delivery_uncertain']
    with pytest.raises(ValueError):source.start(sample(g,3),observing=True)
    fail_stop=False
    gateway.cancel_all()
    original.complete()
    assert source.start(sample(g,4),observing=True)['state']=='ACTIVE'


def test_bad_private_peer_metadata_does_not_disable_existing_observer(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path/'empty-home')
    config=tmp_path/'config';config.mkdir()
    (config/'arm-retarget-peer.json').write_text('{bad-private-json')
    node=node_agent.NodeAgent('pi','pi',tmp_path/'state',TOKEN,sources={},retarget_config_dir=config)
    try:
        status=node.retarget_status()
        assert status['state']=='UNREGISTERED' and not status['peer_configured']
        assert 'JSONDecodeError' in status['error']
        assert node.cameras()==[]
    finally:node.close()


def test_local_follower_control_cannot_start_while_direct_rosmaster_route_owns_source(http_nodes):
    pi_url,_,_,_,station,_=http_nodes
    begin(pi_url)
    with pytest.raises(HTTPError) as rejected:
        request(pi_url,'/station/policy/start',{'observing':True})
    assert rejected.value.code==409 and station.mode=='idle'


def test_pi_explicit_stop_recovers_uncertain_native_stop_without_motion_retry(http_nodes):
    pi_url,nano_url,pi,nano,station,original=http_nodes
    started=begin(pi_url)
    fail_stop=True
    def uncertain_stop(operation,body=None):
        result=original.request(operation,body)
        if operation=='arm/trajectory/stop' and fail_stop:
            raise TimeoutError('native STOP reply lost after delivery')
        return result
    nano.arm_retarget.legacy_request=uncertain_stop
    with pytest.raises(HTTPError) as lost:
        request(pi_url,'/arm-retarget/stop',{'expected_control_epoch':started['control_epoch']})
    assert lost.value.code==503
    assert request(pi_url,'/arm-retarget/status')['stop_delivery_uncertain']
    assert request(nano_url,'/arm-retarget/status')['stop_delivery_uncertain']
    with pytest.raises(HTTPError):begin(pi_url)
    fail_stop=False
    recovered=request(pi_url,'/arm-retarget/stop',{})
    assert recovered['state']=='STOPPED' and not recovered['stop_delivery_uncertain']
    assert not request(nano_url,'/arm-retarget/status')['stop_delivery_uncertain']
    station.update()
    assert begin(pi_url)['state']=='ACTIVE'
    assert not any(op.endswith(('plan','start')) for op,_ in original.calls)
