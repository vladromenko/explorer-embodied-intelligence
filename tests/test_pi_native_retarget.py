"""Latest measured-cache + native lease/mux source integration, no hardware I/O."""
from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import pytest

from explorer_shared.native_leader_geometry import registered_native_leader_geometry,original_servo_coordinates
from explorer_shared.pi_native_retarget import PiNativeRetargetService
from explorer_shared import node_agent


class Station:
    boot='pi-boot'
    camera=SimpleNamespace(stale_s=2.)
    def __init__(self):
        self.tick=time.monotonic_ns();self.positions=[0.]*6
    def status(self):
        return {'available':True,'mode':'idle','roles':{'leader':{'calibration_sha256':'a'*64}},
                'measurements':{'leader':list(self.positions),'monotonic_ns':self.tick,'clock_domain':'pi:pi-boot'}}
    def update(self,value):
        self.positions=[float(value)]*6;self.tick=time.monotonic_ns()


class Geometry:
    registration={'provenance':'synthetic','sha256':'b'*64,'source_calibration_sha256':'a'*64}
    def status(self):
        return {'state':'REGISTERED_DECLARATION','physical_acceptance':False}
    def recenter(self,positions,target):
        self.anchor=(list(positions),list(target))
    def propose(self,positions,target,*,original_native_period_s):
        assert original_native_period_s==.04
        return {'accepted':True,'joint_velocity_deg_s':list(positions)}


class Peer:
    def __init__(self):
        self.calls=[];self.active=False;self.epoch=None;self.serial=0
        self.entered=threading.Event();self.release=threading.Event();self.block=None
    def _call(self,name,value=None):
        self.calls.append((name,deepcopy(value)))
        if self.block==name:
            self.entered.set();assert self.release.wait(3)
    def status(self):
        self._call('status')
        return {'execution_permission':True,'active':self.active,'stop_latched':not self.active,
                'boot_id':'nano-boot','control_epoch':self.epoch,'original_stream_config':{'publish_period_s':.04}}
    def reference(self):
        self._call('reference')
        return {'boot_id':'nano-boot','servo_deg':[90.]*6,'measured':False}
    def select_keyboard(self,**kwargs):
        self._call('select',kwargs)
    def begin(self,**kwargs):
        self._call('begin',kwargs);self.serial+=1;self.epoch='native-'+str(self.serial);self.active=True
        return {'control_epoch':self.epoch}
    def resume(self,epoch,**kwargs):
        self._call('resume',epoch);assert epoch==self.epoch
    def recenter(self,epoch):
        self._call('recenter',epoch);assert epoch==self.epoch
        self.serial+=1;self.epoch='native-'+str(self.serial)
        return {'control_epoch':self.epoch}
    def input(self,epoch,sequence,velocity,evidence):
        self._call('input',{'epoch':epoch,'sequence':sequence,'velocity':velocity,'evidence':evidence})
        if epoch!=self.epoch or not self.active:
            raise ValueError('Native rejects stopped/preclutch epoch')
        return {'accepted':True,'native_dispatched':False,'command_estimate':self.reference()}
    def keyboard_base(self,epoch,actions,**kwargs):
        self._call('base',{'epoch':epoch,'actions':actions,**kwargs});assert epoch==self.epoch and self.active
        return {'accepted':True,'native_dispatched':False}
    def stop(self,epoch):
        self._call('stop',epoch);assert epoch==self.epoch;self.active=False
    def capture_start(self,context):
        self._call('record/start',context);return {'recording_id':'e'*32,'owner':'nano'}
    def capture_stage(self,ident,phase,**metadata):
        self._call('record/stage',{'id':ident,'phase':phase,**metadata});return {'recording_id':ident}
    def capture_finish(self,ident,outcome):
        self._call('record/finish',{'id':ident,'outcome':outcome});return {'recording_id':ident}


def service(tmp_path):
    station,peer=Station(),Peer()
    return PiNativeRetargetService(Geometry(),station,tmp_path,peer,allow_synthetic=True),station,peer


def start(service):
    return service.start({'expected_control_epoch':service.epoch,'observing':True})


def wait_for(predicate):
    deadline=time.monotonic()+3
    while not predicate() and time.monotonic()<deadline:
        time.sleep(.005)
    assert predicate()


def run_captured(call,errors):
    try:
        call()
    except Exception as error:
        errors.append(error)


def test_independent_latest_loop_coalesces_cache_without_finite_queue_or_auto_native_lease(tmp_path):
    source,station,peer=service(tmp_path)
    start(source);source.start_worker()
    try:
        time.sleep(.04)
        assert not any(name=='input' for name,_ in peer.calls)  # anchor isn't echoed
        peer.block='input';station.update(1);source.wake.set();assert peer.entered.wait(2)
        station.update(2);station.update(3);peer.block=None;peer.release.set()
        wait_for(lambda:source.sample_count==2)
        inputs=[value for name,value in peer.calls if name=='input']
        assert [value['velocity'] for value in inputs]==[[1.]*6,[3.]*6]
        assert not any(name=='base' for name,_ in peer.calls)
        assert inputs[-1]['evidence']['state_source']=='measured_leader'
        assert inputs[-1]['evidence']['cross_device_clock_mapping_assumed'] is False
        assert not source.status()['last_result']['native_dispatched']
        before=len(peer.calls);source.close();assert len(peer.calls)==before
    finally:
        peer.release.set();source.close()


def test_explicit_lease_uses_same_native_base_mux_and_rejects_chassis_motion_old_epoch(tmp_path):
    source,_,peer=service(tmp_path);state=start(source);old=state['control_epoch']
    source.lease({'control_epoch':old,'sequence':0,'held':True,'observing':True,'base_actions':{}})
    with pytest.raises(PermissionError,match='CHASSIS'):
        source.lease({'control_epoch':old,'sequence':1,'held':True,'observing':True,'base_actions':{'forward':1}})
    with pytest.raises(ValueError,match='heartbeat'):
        source.lease({'control_epoch':old,'sequence':0,'held':True,'observing':True})
    state=source.recenter({'expected_control_epoch':old,'observing':True})
    assert state['control_epoch']!=old and state['target_control_epoch']=='native-2'
    with pytest.raises(ValueError,match='epoch'):
        source.lease({'control_epoch':old,'sequence':2,'held':True,'observing':True})
    assert len([1 for name,_ in peer.calls if name=='base'])==1
    source.close()


def test_recenter_revokes_source_immediately_and_delayed_http_frame_is_rejected_by_target(tmp_path):
    source,station,peer=service(tmp_path);state=start(source);source.start_worker()
    try:
        peer.block='input';station.update(1);source.wake.set();assert peer.entered.wait(2)
        state=source.recenter({'expected_control_epoch':state['control_epoch'],'observing':True})
        assert state['state']=='ACTIVE' and state['target_control_epoch']=='native-2'
        peer.block=None;peer.release.set();wait_for(lambda:not source.step_inflight)
        assert source.sample_count==0 and source.status()['state']=='ACTIVE'
        station.update(2);source.wake.set();wait_for(lambda:source.sample_count==1)
        assert source.status()['last_result']['target_receipt']['accepted']
    finally:
        peer.release.set();source.close()


@pytest.mark.parametrize('pending',['begin','recenter'])
def test_stop_waits_for_pending_lifecycle_epoch_and_no_late_reply_reactivates_source(tmp_path,pending):
    source,_,peer=service(tmp_path);errors=[]
    if pending=='recenter':
        start(source)
    epoch=source.epoch;peer.block=pending
    call=lambda:getattr(source,'start' if pending=='begin' else 'recenter')(
        {'expected_control_epoch':epoch,'observing':True})
    life=threading.Thread(target=run_captured,args=(call,errors));life.start();assert peer.entered.wait(2)
    stopped=threading.Event()
    stop=threading.Thread(target=lambda:(source.stop(),stopped.set()));stop.start()
    wait_for(lambda:source.pending_stops==1)
    with pytest.raises(ValueError):
        start(source)
    assert not stopped.is_set()
    peer.block=None;peer.release.set();life.join(2);stop.join(2)
    assert stopped.is_set() and not peer.active and source.status()['state']=='STOPPED'
    assert source.target_epoch is None and source.pending_stops==0 and errors
    assert peer.calls[-1]==('stop',peer.epoch)
    source.close()


def test_stale_leader_interrupts_without_automatic_lease_or_restart(tmp_path):
    source,station,peer=service(tmp_path);start(source)
    station.tick-=3_000_000_000;source.start_worker()
    try:
        wait_for(lambda:source.status()['state']=='INTERRUPTED')
        assert not any(name in ('input','base','stop') for name,_ in peer.calls)
        assert 'stale' in source.error
    finally:
        source.close()


def test_late_stop_after_close_cannot_remove_permanent_shutdown_latch(tmp_path):
    source,_,peer=service(tmp_path);start(source);peer.block='stop'
    thread=threading.Thread(target=source.stop);thread.start();assert peer.entered.wait(2)
    source.close();assert source.mode=='CLOSED'
    peer.block=None;peer.release.set();thread.join(2)
    assert not peer.active and source.mode=='CLOSED' and source.shutdown.is_set()
    with pytest.raises(ValueError,match='permanently closed'):
        start(source)


def test_capture_proxy_keeps_common_context_and_typed_outcome_but_does_not_duplicate_new_ids(tmp_path):
    source,_,peer=service(tmp_path)
    context={'project_id':'03','skill_id':'skill','phase_id':'reach','split':{'partition':'train'}}
    result=source.command('record/start',{'skill_context':context});ident=result['recording_id']
    source.command('record/stage',{'recording_id':ident,'stage':'grasp'})
    outcome={'operator_observation':{'observed':True,'success':False}}
    source.command('record/finish',{'recording_id':ident,'outcome_evidence':outcome})
    assert peer.calls[0]==('record/start',context)
    assert peer.calls[-1][1]['outcome']==outcome
    assert source.recorder.recordings()==[]
    with pytest.raises(ValueError,match='03'):
        source.command('record/start',{'skill_context':{'project_id':'02'}})
    source.close()


def test_default_missing_crossmap_does_not_request_device_recalibration_or_open_peer(tmp_path):
    (tmp_path/'arm-retarget-peer.json').write_text('{')
    source=PiNativeRetargetService.from_config(None,tmp_path/'state',tmp_path)
    report=source.status()
    assert report['state']=='UNREGISTERED' and not report['peer_configured']
    assert report['registration']['leader_device_recalibration_requested'] is False
    assert 'JSONDecodeError' in report['error']
    assert source.start({})['state']=='UNREGISTERED'
    source.close()


def test_unaudited_factory_coordinate_source_cannot_execute(tmp_path):
    with pytest.raises(ValueError,match='Exact original'):
        original_servo_coordinates(b'raise RuntimeError("must not execute")')
    (tmp_path/'native-leader-registration.json').write_text(json.dumps({'schema':'explorer_native_leader_registration_v1'}))
    assert registered_native_leader_geometry(tmp_path).status()['state']=='UNREGISTERED'


def test_authenticated_pi_node_selects_new_service_and_exposes_native_record_proxy(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path/'home')
    token='local-only-mock-auth-token-20261026'
    agent=node_agent.NodeAgent('pi','pi',tmp_path/'state',token,sources={},retarget_config_dir=tmp_path/'config')
    assert isinstance(agent.arm_retarget,PiNativeRetargetService)
    agent.arm_retarget.close();source,station,peer=service(tmp_path)
    agent.arm_retarget=source;agent.station=station
    server=node_agent.AgentHTTPServer(('127.0.0.1',0),node_agent.make_handler(agent))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    def request(path,body=None,authenticated=True):
        headers={'Authorization':'Bearer '+token} if authenticated else {}
        data=json.dumps(body).encode() if body is not None else None
        with urlopen(Request(origin+path,data=data,headers=headers),timeout=2) as response:
            return json.load(response)
    try:
        with pytest.raises(HTTPError) as denied:
            request('/arm-retarget/status',authenticated=False)
        assert denied.value.code==401 and not peer.calls
        report=request('/arm-retarget/status')
        report=request('/arm-retarget/start',{'expected_control_epoch':report['control_epoch'],'observing':True})
        assert report['native_backend']=='factory_micro_ros_ruckig_velocity'
        context={'project_id':'03','skill_id':'explicit-only','phase_id':'reach'}
        result=request('/arm-retarget/record/start',{'skill_context':context})
        ident=result['recording_id']
        request('/arm-retarget/record/stage',{'recording_id':ident,'stage':'grasp'})
        request('/arm-retarget/record/finish',{'recording_id':ident,'outcome_evidence':{'operator_outcome':'unknown'}})
        assert request('/arm-retarget/recordings')=={'recordings':[]}
        assert peer.calls[-1][1]['outcome']=={'operator_outcome':'unknown'}
    finally:
        server.shutdown();server.server_close();source.close();agent.delivery.close()
