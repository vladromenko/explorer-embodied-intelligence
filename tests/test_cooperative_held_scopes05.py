"""Actual source providers/caches/original workers; temporary fake buses only."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import pytest

from explorer_shared.contracts import digest
from explorer_shared.node_agent import make_handler
from explorer_shared.station_observed_http import StationObservedHttp
from test_cooperative_giver_phases import prepare,phase,new_permission,real_node
from test_native_activation import TOKEN
from test_station_hold_successor import rig as receiver_rig,invoke as adopt_receiver


def giver(tmp_path,*,policy=False):
    values=prepare(tmp_path,policy=policy);service,store,row,spec,pair,client=values
    phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt')
    return values


def close(values):
    service,store,row,spec,pair,client=values;service.close();pair.close();client.close()


@pytest.mark.parametrize('policy',[False,True])
def test_giver_snapshot_uses_same_actual_arm_and_new_begin_base0_without_lease_or_command(tmp_path,policy):
    values=giver(tmp_path,policy=policy);service,store,row,spec,pair,client=values
    try:
        first=pair.owner.held_scope.snapshot();assert first['ready'],first
        assert first['state_source']=='command_estimate' and first['measured_feedback'] is None and first['measured_feedback_mask']==[False]*6
        original=deepcopy(first['source_origin']);assert first['actual_issued']['base_receipt']==original['actual_issued']['base_receipt']
        if policy:phase(service,row,'native_phase_begin')
        lease=pair.teleop.lease;publications=len(pair.owner.original_publisher.messages);drives=deepcopy(pair.teleop.native_drive)
        saved=pair.owner.guard.journal.path.read_bytes();state=pair.owner.guard._snapshot()
        packet=pair.owner.held_scope.snapshot();assert packet['ready'],packet
        assert packet['source_origin']==original and packet['actual_issued']['command9']==[90]*6+[0.,0.,0.]
        if policy:assert packet['actual_issued']['base_receipt']!=original['actual_issued']['base_receipt'] and packet['stage']=='BEGIN_CLUTCHED'
        assert packet['scope_sha256']==digest({key:value for key,value in packet.items() if key!='scope_sha256'})
        assert not any(packet[key] for key in ('dispatch_permission','stationarity_verified','object_holding_verified','physical_attainment_claimed'))
        assert pair.teleop.lease==lease and pair.teleop.native_drive==drives and len(pair.owner.original_publisher.messages)==publications
        assert pair.owner.guard.journal.path.read_bytes()==saved and pair.owner.guard._snapshot()==state
    finally:close(values)


@pytest.mark.parametrize('failure',['fault','wal','corrupt_wal','missing_marker','revoke','generation','new_grant','changed_arm','changed_base','unknown_base','reference','boot','stage'])
def test_giver_changed_or_unknown_source_facts_fail_closed_without_native_commands(tmp_path,failure):
    values=giver(tmp_path);service,store,row,spec,pair,client=values
    try:
        guard=pair.owner.guard;tap=pair.mux.tap
        if failure=='fault':guard.reference_fault={'reason':'ACTUAL_ORIGINAL_FAULT'}
        elif failure=='wal':guard.journal.fail('SOURCE_WAL_UNCERTAIN')
        elif failure=='corrupt_wal':guard.journal.path.write_text('{}')
        elif failure=='missing_marker':guard.journal.marker.unlink()
        elif failure=='revoke':pair.gate.revoke('USER_FOCUS_LOSS')
        elif failure=='generation':pair.teleop.generation+=1
        elif failure=='new_grant':pair.gate.authorization['authorization_id']='foreign-source-grant'
        elif failure=='changed_arm':tap.publisher_returned([91]*6,40,tap.clock())
        elif failure=='changed_base':tap.observe('base_drive_host_request',{'velocity':[.1,0.,0.]},source_id='SOFTWARE-returned-original-drive')
        elif failure=='unknown_base':tap.base_unknown=True
        elif failure=='reference':pair.owner.scope_reference=lambda:{'servo_deg':[91]*6,'boot_id':'boot','measured':False}
        elif failure=='boot':tap.boot_id='new-boot'
        else:guard.parked['stage']='STOPPED_BARRIER'
        publications=len(pair.owner.original_publisher.messages);drives=deepcopy(pair.teleop.native_drive);lease=pair.teleop.lease
        result=pair.owner.held_scope.snapshot()
        assert not result['ready'] and result['reason'] and result['native_commands_sent']==0
        assert len(pair.owner.original_publisher.messages)==publications and pair.teleop.native_drive==drives and pair.teleop.lease==lease
        if failure=='fault':assert guard.reference_fault=={'reason':'ACTUAL_ORIGINAL_FAULT'}
    finally:close(values)


def test_giver_reference_io_holds_no_control_locks_and_stop_fences_late_scope(tmp_path):
    values=giver(tmp_path,policy=True);service,store,row,spec,pair,client=values
    entered,release=threading.Event(),threading.Event();results=[]
    try:
        phase(service,row,'native_phase_begin');original=pair.owner.scope_reference
        def delayed():
            assert not pair.owner.guard.lock._is_owned() and not pair.teleop.lock._is_owned()
            entered.set();assert release.wait(3);return original()
        pair.owner.scope_reference=delayed
        reader=threading.Thread(target=lambda:results.append(pair.owner.held_scope.snapshot()));reader.start();assert entered.wait(2)
        stopped=threading.Event()
        stopper=threading.Thread(target=lambda:(pair.mux._stop_all(),stopped.set()));stopper.start();assert stopped.wait(1)
        release.set();reader.join(2);stopper.join(2)
        assert not reader.is_alive() and not results[0]['ready'] and pair.teleop.stop_latched
    finally:
        release.set()
        if 'reader' in locals():reader.join(3)
        close(values)


@pytest.mark.parametrize('denial',[False,True])
def test_receiver_uses_actual_source_hold_and_fresh_exact_measurement_without_write(tmp_path,monkeypatch,denial):
    values=receiver_rig(tmp_path,monkeypatch,denial=denial);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    source=adopt_receiver(wrapper,body);writes=deepcopy(bus.writes);saved=wrapper.path.read_bytes();state=wrapper.helper.snapshot_state()
    result=wrapper.held_scope();assert result['ready'],result
    assert result['state_source']=='measured_state' and result['source_origin']==source['source_hold_origin']
    assert result['measured_native6']==[0.,0.,0.,0.,0.,50.] and result['native_owner']['grant_id']==fresh['grant_id']
    assert result['grant']['binding']==binding and result['actor_id']==old['actor_id']
    assert not any(result[key] for key in ('dispatch_permission','stationarity_verified','object_holding_verified','physical_attainment_claimed'))
    assert bus.writes==writes and wrapper.helper.snapshot_state()==state and wrapper.path.read_bytes()==saved


def test_later_actual_user_display_confirmation_uses_observed_stamp_not_initial_grant_creation(tmp_path,monkeypatch):
    values=receiver_rig(tmp_path,monkeypatch);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt_receiver(wrapper,body);created=fresh['created_monotonic_ns'];tick[0]+=100_000_000
    station.stop_event=StopAfter();station._work();frame=station.camera.latest();frame.frame_id='new-actually-confirmed-frame'
    station.camera.latest=lambda:frame;station.camera.frame_by_id=lambda identity:frame if identity==frame.frame_id else None
    updated=provider.observe({'binding':binding,'grant_id':fresh['grant_id'],'expected_control_epoch':fresh['control_epoch'],
        'frame_id':frame.frame_id,'displayed_jpeg_sha256':__import__('hashlib').sha256(frame.jpeg).hexdigest(),'observing':True,'actor_id':fresh['actor_id']})
    writes=deepcopy(bus.writes);result=wrapper.held_scope()
    assert result['ready'],result
    assert updated['created_monotonic_ns']==created<updated['displayed_frame']['received_monotonic_ns']<=updated['observed_monotonic_ns']
    assert result['grant']['observation_deadline_ns']==updated['observation_deadline_ns'] and bus.writes==writes


@pytest.mark.parametrize('failure',['revoke','provider','actor','object','calibration','epoch','pose','stale','future','clock','frame','deadline','wal','pending','fault'])
def test_receiver_scope_rejects_changed_stale_or_unknown_sources_without_write(tmp_path,monkeypatch,failure):
    values=receiver_rig(tmp_path,monkeypatch);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt_receiver(wrapper,body);grant=provider.grant['data']
    if failure=='revoke':provider.revoke('USER_FOCUS_LOSS')
    elif failure=='provider':grant['provider_process']='foreign-source-provider'
    elif failure=='actor':grant['actor_id']='foreign-operator'
    elif failure=='object':grant['binding']['object_id']='foreign-object'
    elif failure=='calibration':station.profiles['follower']['calibration_sha256']='f'*64
    elif failure=='epoch':station.control_epoch='foreign-idle-epoch'
    elif failure=='pose':station.measurements['follower'][1]=1.
    elif failure=='stale':station.measurements['monotonic_ns']=tick[0]-500_000_001
    elif failure=='future':station.measurements['monotonic_ns']=tick[0]+1
    elif failure=='clock':station.measurements['clock_domain']='foreign-clock'
    elif failure=='frame':grant['displayed_frame']['received_monotonic_ns']=0
    elif failure=='deadline':grant['observation_deadline_ns']=tick[0]
    elif failure=='wal':wrapper.path.write_text('{}')
    elif failure=='pending':wrapper.phase_prepared={'unknown-start':True}
    else:wrapper._fault('PENDING_WAL_RECOVERY_REQUIRED')
    writes=deepcopy(bus.writes);result=wrapper.held_scope()
    assert not result['ready'] and result['reason'] and result['native_commands_sent']==0 and bus.writes==writes
    assert wrapper.helper.armed


def test_receiver_camera_and_wal_io_never_hold_station_or_publication_locks_revoke_fences_result(tmp_path,monkeypatch):
    from explorer_shared import station_cooperative_interlock as module
    values=receiver_rig(tmp_path,monkeypatch);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt_receiver(wrapper,body);entered,release=threading.Event(),threading.Event();results=[];read=module._read
    def delayed(path,*args):
        assert not station.lock._is_owned() and not provider.lock._is_owned() and not provider.publication_gate.publication_lock._is_owned()
        if path==wrapper.path:entered.set();assert release.wait(3)
        return read(path,*args)
    monkeypatch.setattr(module,'_read',delayed);writes=deepcopy(bus.writes)
    reader=threading.Thread(target=lambda:results.append(wrapper.held_scope()));reader.start();assert entered.wait(2)
    provider.revoke('USER_FOCUS_LOSS',expected_grant_id=fresh['grant_id'],expected_control_epoch=fresh['control_epoch'])
    release.set();reader.join(2)
    assert not reader.is_alive() and not results[0]['ready'] and bus.writes==writes


def test_actual_giver_authenticated_node_bridge_fixed_get_only_scope(tmp_path,monkeypatch):
    values=giver(tmp_path);service,store,row,spec,pair,client=values
    try:
        path='/api/explorer/cooperative/phase/held-status'
        assert pair.owner.status()['capabilities']['parked_observation_scope_http']
        assert pair.client.get(path).status_code==401
        assert pair.client.get(path+'?revision=123',headers={'Authorization':'Bearer '+TOKEN}).status_code==400
        before=len(pair.calls);drives=deepcopy(pair.teleop.native_drive)
        with real_node(pair,tmp_path,monkeypatch) as calls:
            reply=service._request('giver')('GET','/legacy/explorer/cooperative/phase/held-status',None)
            assert reply['ready'] and reply['role']=='giver' and not reply['dispatch_permission']
            assert calls==[('GET',path,None)]
        assert all(method=='GET' for _,method,_,_ in pair.calls[before:]) and pair.teleop.native_drive==drives
    finally:close(values)


def test_actual_pi_authenticated_fixed_get_and_cached_status_never_invoke_dynamic_getter_implicitly(tmp_path,monkeypatch):
    values=receiver_rig(tmp_path,monkeypatch);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt_receiver(wrapper,body);writes=deepcopy(bus.writes);token='PRIVATE-SOFTWARE-ONLY-scope-token';calls=[]
    agent=SimpleNamespace(token=token,mode='pi',station=station,station_observed=provider,mobile_records=None,legacy_bridge=None,
        lab_vision=None,arm_retarget=SimpleNamespace(status=lambda:{'state':'IDLE'}))
    original=wrapper.held_scope
    def counted():calls.append('GET_SCOPE');return original()
    wrapper.held_scope=counted
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
    def request(method,path,body):
        with urlopen(Request(base+path,method=method,headers={'Authorization':'Bearer '+token},
                data=None if body is None else json.dumps(body).encode()),timeout=2) as response:return json.load(response)
    facade=StationObservedHttp(request,binding)
    try:
        assert not calls and wrapper.status()['parked_observation_scope_http'] and provider.status()['execution_permission']
        assert not calls
        path='/station/observed-test/cooperative/held/status'
        with pytest.raises(HTTPError) as denied:urlopen(base+path,timeout=2)
        assert denied.value.code==401 and not calls
        with pytest.raises(HTTPError):request('GET',path+'?grant_id=browser-forged',None)
        assert not calls
        packet=facade.held_scope();assert packet['ready'] and packet['native_owner']['grant_id']==fresh['grant_id']
        assert calls==['GET_SCOPE'] and bus.writes==writes
        with pytest.raises(HTTPError):request('POST',path,{'grant_id':fresh['grant_id']})
        assert calls==['GET_SCOPE'] and bus.writes==writes
    finally:server.shutdown();server.server_close();thread.join(2)
