"""Actual native mux plus memory-only owners: no devices, TCP or physical POST."""
from copy import deepcopy
import io
import json
import threading
import time
from urllib.error import HTTPError

import pytest

from explorer_shared.native_stream03_client import NativeStreamClient,NativeStreamRejected
from explorer_shared.pi_native_retarget import PiNativeRetargetService,HOLD_SCHEMA
from test_pi_native_retarget import service,start,wait_for,run_captured,Peer,Geometry,Station
from test_native_stream03 import setup_mux


def held(source,sequence=0,**values):
    return source.lease({'control_epoch':source.epoch,'sequence':sequence,'held':True,'observing':True,'base_actions':{},**values})


class MuxPeer(Peer):
    def __init__(self):
        super().__init__();self.mux,self.teleop,self.arm,self.tap,_,self.clock=setup_mux(begin=False)
        self.arm.motion_config={'streaming':{'publish_period_s':.04}}
    def status(self):
        self._call('status');return {**self.mux.status(),'execution_permission':True,'activation':{'authorization_id':self.authorization_id}}
    def reference(self):
        self._call('reference');return {'boot_id':'boot','servo_deg':[90.]*6,'measured':False}
    def begin(self,**kwargs):
        self._call('begin',kwargs);return self.mux.begin(observing=kwargs['observing'],neutral=kwargs['neutral'])
    def resume(self,epoch,**kwargs):
        self._call('resume',epoch);return self.mux.resume(epoch,observing=kwargs['observing'])
    def keyboard_base(self,epoch,actions,**kwargs):
        self._call('base',actions);return self.mux.keyboard_base_input(epoch,actions,observing=kwargs['observing'])
    def input(self,epoch,sequence,velocity,evidence,**kwargs):
        self._call('input',velocity);return {**self.mux.leader_intent(epoch,sequence,velocity,evidence), 'command_estimate':self.reference()}
    def stop(self,epoch):self._call('stop',epoch);return self.mux.stop(epoch)


def test_native_mux_start_is_clutched_and_anchor_is_taken_at_actual_explicit_held_edge(tmp_path):
    station,peer=Station(),MuxPeer();source=PiNativeRetargetService(Geometry(),station,tmp_path,peer,allow_synthetic=True)
    state=start(source,held=False);original_lease=peer.teleop.lease;source.start_worker()
    try:
        assert state['state']=='CLUTCHED' and state['source_hold_gate_schema']==HOLD_SCHEMA and state['operator_profile']=='ARM_ONLY'
        station.update(1);source.wake.set();time.sleep(.03)
        assert not any(name in ('input','resume','base') for name,_ in peer.calls)
        assert peer.teleop.lease==original_lease and peer.teleop.native_drive==[[0.,0.,0.]]
        held(source);assert source.mode=='ACTIVE' and source.geometry.anchor[0]==[1.]*6
        time.sleep(.02);assert not any(name=='input' for name,_ in peer.calls)  # Fresh anchor is not echoed.
        station.update(2);source.wake.set();wait_for(lambda:source.sample_count==1)
        assert peer.mux.latest==[2.]*6 and not any(any(row['joints']) for row in peer.arm.calls)
        peer.teleop._update_arm_stream_locked({},False)  # The ORIGINAL native tick is separate from HTTP ACK.
        assert peer.arm.calls[-1]['joints']==[2.]*6 and peer.mux.status()['native_queue_length']==0
        last=deepcopy(source.status()['last_proposal']);held(source,1)
        assert source.status()['last_proposal']==last and source.status()['last_lease_receipt']
        assert source.status()['timing_diagnostics']['hardware_latency_verified'] is False
    finally:source.close()


def test_clutched_recenter_requires_new_explicit_held_and_sequence_restarts_per_source_epoch(tmp_path):
    source,station,peer=service(tmp_path);start(source);old=source.epoch
    state=source.recenter({'expected_control_epoch':old,'observing':True});assert state['state']=='CLUTCHED'
    assert len([name for name,_ in peer.calls if name=='resume'])==1
    station.update(3);held(source,0);assert source.mode=='ACTIVE' and source.geometry.anchor[0]==[3.]*6
    assert len([name for name,_ in peer.calls if name=='resume'])==2
    source.close()


@pytest.mark.parametrize('change',['stale_source','foreign_epoch','foreign_grant','source_owner'])
def test_first_held_revalidates_retained_scope_without_adopting_new_owner(tmp_path,change):
    source,station,peer=service(tmp_path);start(source,held=False)
    if change=='stale_source':station.tick-=3_000_000_000
    elif change=='foreign_epoch':peer.epoch='other-owner'
    elif change=='foreign_grant':peer.authorization_id='other-user-grant'
    else:
        original=station.status
        station.status=lambda:{**original(),'measurements':{**original()['measurements'],'clock_domain':'pi:another-owner'}}
    with pytest.raises(ValueError):held(source)
    assert not any(name in ('input','resume','base') for name,_ in peer.calls)
    assert source.mode=='INTERRUPTED'
    source.close()


def test_ARM_ONLY_is_session_pinned_even_with_hypothetical_chassis_grant_and_mobile_checks_current_grant(tmp_path):
    source,_,peer=service(tmp_path/'arm');peer.chassis=True;start(source)
    before=len(peer.calls)
    with pytest.raises(PermissionError,match='ARM_ONLY'):
        held(source,1,operator_profile='MOBILE_MANIPULATION',base_actions={'forward':1})
    with pytest.raises(PermissionError,match='ARM_ONLY'):
        held(source,1,base_actions={'forward':1e-14})
    assert len(peer.calls)==before and source.operator_profile=='ARM_ONLY';source.close()
    source,_,peer=service(tmp_path/'mobile');peer.chassis=True;start(source,held=False,operator_profile='MOBILE_MANIPULATION')
    with pytest.raises(ValueError,match='NEUTRAL_FIRST'):held(source,base_actions={'forward':1})
    held(source);held(source,1,base_actions={'forward':1});assert source.chassis_requests==1
    peer.chassis=False
    with pytest.raises(PermissionError,match='PROHIBITED'):held(source,2,base_actions={'forward':1})
    assert source.chassis_requests==1;source.close()


def test_source_scoped_stop_without_owned_epoch_never_queries_or_cancels_foreign_owner(tmp_path):
    source,_,peer=service(tmp_path);peer.active=True;peer.epoch='foreign-owner'
    result=source.stop();assert peer.active and not peer.calls
    assert result['last_stop_receipt']['outcome']=='NO_OWNED_NATIVE_EPOCH'
    assert not result['last_stop_receipt']['hardware_stop_verified'];source.close()


def test_HTTP_stop_requires_captured_source_epoch_and_cannot_cancel_a_new_attempt(tmp_path):
    source,_,peer=service(tmp_path);old=start(source,held=False)['control_epoch']
    with pytest.raises(ValueError,match='epoch'):source.command('stop',{})
    source.command('stop',{'control_epoch':old});fresh=start(source,held=False)['control_epoch']
    before=len(peer.calls)
    with pytest.raises(ValueError,match='epoch'):source.command('stop',{'control_epoch':old})
    assert len(peer.calls)==before and peer.active and source.epoch==fresh
    source.stop()  # Existing Node /station/stop internal global path remains valid.
    assert not peer.active;source.close()


def test_begin_reply_unknown_is_not_false_scoped_stop_success_or_foreign_epoch_adoption(tmp_path):
    source,_,peer=service(tmp_path);original=peer.begin
    def lost_reply(**kwargs):original(**kwargs);raise TimeoutError('SOFTWARE lost begin reply')
    peer.begin=lost_reply
    with pytest.raises(TimeoutError):start(source,held=False)
    assert source.status()['source_cache_claimed'] and source.stop_uncertain
    with pytest.raises(ValueError,match='global STOP'):source.stop()
    assert not any(name=='stop' for name,_ in peer.calls) and source.stop_uncertain and peer.active
    peer.active=False;peer.state='STOPPED'  # Fake explicit global original STOP, not a service adoption.
    result=source.stop();assert not source.status()['source_cache_claimed'] and not result['stop_delivery_uncertain']
    assert result['last_stop_receipt']['outcome']=='UNKNOWN_BEGIN_SOURCE_NOW_INACTIVE';source.close()


@pytest.mark.parametrize('case',['missing_boot','unknown_reference_basis','missing_returned_epoch'])
def test_unknown_native_identity_or_reference_cannot_enter_held_control(tmp_path,case):
    source,_,peer=service(tmp_path)
    if case=='missing_boot':
        original=peer.status;peer.status=lambda:{**original(),'boot_id':None}
    elif case=='unknown_reference_basis':peer.reference=lambda:{'boot_id':'nano-boot','servo_deg':[90.]*6}
    else:
        original=peer.begin
        def missing(**kwargs):original(**kwargs);return {'control_epoch':None}
        peer.begin=missing
    with pytest.raises(ValueError):start(source,held=False)
    assert not any(name in ('resume','input','base') for name,_ in peer.calls)
    if case=='missing_returned_epoch':assert source.begin_outcome_unknown and source.stop_uncertain
    source.close()


@pytest.mark.parametrize('ending',['stop','close'])
def test_late_first_held_resume_reply_cannot_reactivate_or_write_after_stop_or_close(tmp_path,ending):
    source,_,peer=service(tmp_path);start(source,held=False);peer.block='resume';errors=[]
    activation=threading.Thread(target=run_captured,args=(lambda:held(source),errors));activation.start();assert peer.entered.wait(1)
    stopped=None
    if ending=='stop':
        stopped=threading.Thread(target=source.stop);stopped.start();wait_for(lambda:source.pending_stops==1)
    else:source.close()
    peer.block=None;peer.release.set();activation.join(2)
    if stopped:stopped.join(2)
    assert errors and not any(name in ('input','base') for name,_ in peer.calls)
    assert source.mode==('STOPPED' if ending=='stop' else 'CLOSED')
    if ending=='stop':assert not peer.active
    source.close()


def test_late_ordinary_lease_response_does_not_overwrite_stop_or_retained_proposal(tmp_path):
    source,_,peer=service(tmp_path);start(source);previous=deepcopy(source.last_lease_receipt)
    entered,release=threading.Event(),threading.Event();original=peer.keyboard_base
    def accepted_before_reply(*args,**kwargs):
        response=original(*args,**kwargs);entered.set();assert release.wait(2);return response
    peer.keyboard_base=accepted_before_reply;errors=[]
    request=threading.Thread(target=run_captured,args=(lambda:held(source,1),errors));request.start();assert entered.wait(1)
    source.stop();release.set();request.join(2)
    assert not errors and source.mode=='STOPPED' and source.last_lease_receipt==previous and source.discarded_late_replies==1
    source.close()


def test_transport_explicit_owned_authorization_cannot_be_replaced_by_mutable_shared_client_default():
    calls=[]
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    def opener(request,**kwargs):
        calls.append(json.loads(request.data));return Response(b'{"accepted":true}')
    client=NativeStreamClient('http://software.invalid','fixture-token-not-real',opener=opener);client.authorization_id='new-default'
    client.resume('owned-epoch',observing=True,authorization_id='original-owned-grant')
    assert calls[0]['authorization_id']=='original-owned-grant' and client.authorization_id=='new-default'


def test_reference_HTTP_blocker_is_preserved_without_requesting_readback_or_fabricating_q():
    def opener(request,**kwargs):
        raise HTTPError(request.full_url,409,'Conflict',{},io.BytesIO(b'{"detail":"MCU telemetry stale; original reference unavailable"}'))
    client=NativeStreamClient('http://software.invalid','fixture-token-not-real',opener=opener)
    with pytest.raises(NativeStreamRejected,match='NATIVE_REFERENCE_UNAVAILABLE.*MCU telemetry stale') as error:client.reference()
    assert error.value.source_error['status']==409 and not error.value.source_error['measured_feedback_required']
