"""Real COMMON provider/fake original native driver; ACK is never bus evidence."""
from copy import deepcopy
import hashlib
import threading
import uuid

import pytest

from explorer_shared.cooperative_receiver_transport05 import NativeReceiverTransport
from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS
from explorer_shared.station_observed_activation import StationObservedPermission,CONTRACT
from test_station_observed_activation import Camera,Station,authorize


class Original(Station):
    def __init__(self,tick,*,emit):
        super().__init__();self.tick=tick;self.emit=emit;self.pending=None;self.writes=[];self.last=None
    def status(self):return {**super().status(),'configuration_writes':0,'last_command':deepcopy(self.last)}
    def command(self,operation,body):
        result=super().command(operation,body)
        if operation=='action':self.pending=deepcopy(body)
        return result
    def pump_original_driver(self):
        if self.emit and self.pending:
            body=self.pending;self.pending=None;self.writes.append(deepcopy(body['action']))
            self.last={'control_epoch':self.control_epoch,'sequence':body['sequence'],'requested':deepcopy(body['action']),
                'dispatched':deepcopy(body['action']),'monotonic_ns':self.tick[0]+1,'clock_domain':self.clock_domain,
                'source_observation':{'frame_id':body['source_frame_id'],'monotonic_ns':body['source_frame_monotonic_ns'],
                    'clock_domain':body['source_clock_domain']}}


def rig(*,emit=True,mode='policy'):
    tick=[10_000_000_000];station=Original(tick,emit=emit);camera=Camera(tick);camera.publish()
    provider=StationObservedPermission(station,camera,clock=lambda:tick[0]);calls=[]
    binding={'purpose':'cooperative05','input_mode':mode,'source_domain':'PHYSICAL','action_contract_id':CONTRACT,
        'cooperative_session_id':'run','coordinator_epoch':'coord','object_id':'block','skill_id':str(uuid.uuid4()),
        'skill_version':1,'spec_sha256':'b'*64}
    reference={'skill_id':binding['skill_id'],'skill_version':1,'spec_sha256':'b'*64,'action_contract':ROLE_CONTRACTS['receiver']}
    if mode=='policy':binding.update(model_job_id='a'*32,checkpoint_content_sha256='c'*64);reference.update(model_id='a'*32,checkpoint_sha256='c'*64)
    grant,_=authorize(provider,binding)
    grant=provider.command(grant['grant_id'],binding,'policy/start' if mode=='policy' else 'teleop/start',
        {'expected_control_epoch':grant['control_epoch']})['grant']
    def request(method,path,body):
        calls.append((method,path,deepcopy(body)))
        if path=='/station/observed-test/status':assert method=='GET';return provider.status()
        if path=='/station/status':assert method=='GET';station.pump_original_driver();return station.status()
        assert method=='POST' and path.startswith('/station/observed-test/')
        if path.endswith('/command'):return provider.command(body['grant_id'],body['binding'],body['operation'],body['body'])
        assert path.endswith('/revoke')
        return provider.revoke(body['reason'],expected_grant_id=body['expected_grant_id'],expected_control_epoch=body['expected_control_epoch'],expected_binding=body['binding'])
    frame_sha=hashlib.sha256(camera.current.jpeg).hexdigest()
    prediction={'model':{key:reference.get(key) for key in ('model_id','checkpoint_sha256','spec_sha256')},
        'predicted_native6':[1.,2.,3.,4.,5.,60.],'action_contract':deepcopy(ROLE_CONTRACTS['receiver']),
        'dispatch_performed':False,'physical_execution_authorized':False,
        'source_evidence':{'original_artifacts':{'frame.jpg':{'sha256':frame_sha,'bytes':len(camera.current.jpeg)}}},
        'source_observation':{'camera_id':camera.camera_id,'frame_id':camera.current.frame_id,'frame_sha256':frame_sha,'state_basis':'measured_state',
            'frame_boot_id':station.boot,'state_boot_id':station.boot,'source_cutoff_boot_id':station.boot,
            'frame_clock_domain':station.clock_domain,'state_clock_domain':station.clock_domain,'source_cutoff_clock_domain':station.clock_domain,
            'frame_monotonic_ns':tick[0],'state_monotonic_ns':tick[0],'source_cutoff_monotonic_ns':tick[0],
            'follower_calibration_sha256':station.calibration}}
    run={'run_id':'run','object_id':'block','coordinator_epoch':'coord'}
    transport=NativeReceiverTransport(request,run,binding,grant,reference,lambda command:deepcopy(prediction),clock_domain='nx:separate-boot',timeout_s=.03)
    assert not calls and not station.writes
    command={'command_id':'command','role':'receiver','reference':reference,**run}
    return transport,command,provider,station,camera,tick,prediction,calls,grant


def test_actual_native_epoch_sequence_frame_action_write_receipt_not_api_ack_or_stationarity():
    transport,command,provider,station,camera,tick,prediction,calls,grant=rig()
    result=transport.submit_observed(command)
    assert result.dispatched and result.role=='receiver' and result.clock_domain=='nx:separate-boot'
    assert result.source['native_receipt']['clock_domain']!=result.clock_domain
    assert station.writes==[prediction['predicted_native6']] and not result.source['MCU_attainment_verified']
    action=next(body for _,path,body in calls if path.endswith('/command'))
    assert action['operation']=='action' and 'duration_s' not in action['body']
    assert not any(path.endswith('/observe') for _,path,_ in calls)
    assert transport.observe_state()['attained_stationarity'] is None


def test_native_acceptance_without_original_driver_write_is_unknown_not_success():
    transport,command,provider,station,camera,tick,prediction,calls,grant=rig(emit=False)
    with pytest.raises(TimeoutError,match='no exact original native'):transport.submit_observed(command)
    assert not station.writes and [op for op,_ in station.calls]==['policy/start','action']


def test_stop_during_prediction_prevents_action_and_stale_cleanup_never_stops_new_user():
    transport,command,provider,station,camera,tick,prediction,calls,grant=rig()
    entered,release=threading.Event(),threading.Event();errors=[]
    def predict(command):entered.set();assert release.wait(2);return deepcopy(prediction)
    transport.predict=predict
    def run():
        try:transport.submit_observed(command)
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=run);thread.start();assert entered.wait(1)
    transport.stop('run');new,_=authorize(provider,grant['binding']);release.set();thread.join(2)
    assert errors and not thread.is_alive() and not station.writes
    with pytest.raises(ValueError,match='newer grant'):transport.stop('run')
    assert provider.status()['grant']['grant_id']==new['grant_id'] and [op for op,_ in station.calls]==['policy/start','stop']


def test_changed_source_calibration_and_manual_input_never_submit_policy_targets():
    transport,command,provider,station,camera,tick,prediction,calls,grant=rig()
    prediction['source_observation']['frame_boot_id']='wrong'
    with pytest.raises(ValueError,match='same-boot'):transport.submit_observed(command)
    assert [op for op,_ in station.calls]==['policy/start']
    station.calibration='f'*64
    assert not transport.observe_state()['current_native_owner_ready'] and not station.writes
    manual,body,_,native,_,_,_,_,_=rig(mode='leader')
    assert manual.observe_state()['current_native_owner_ready']
    with pytest.raises(ValueError,match='Manual receiver'):manual.submit_observed(body)
    assert [op for op,_ in native.calls]==['teleop/start']


@pytest.mark.parametrize('field,value',[('skill_id','other-Skill'),('skill_version',2),('spec_sha256','f'*64),
    ('model_id','f'*32),('checkpoint_sha256','f'*64),('action_contract',ROLE_CONTRACTS['giver'])])
def test_different_model_or_skill_cannot_use_an_authentic_other_user_grant(field,value):
    transport,command,provider,station,camera,tick,prediction,calls,grant=rig()
    reference=deepcopy(transport.reference);reference[field]=value
    with pytest.raises(ValueError,match='permission binding'):NativeReceiverTransport(transport.request,transport.binding,
        grant['binding'],grant,reference,transport.predict,clock_domain=transport.domain)
    assert not calls and not station.writes
