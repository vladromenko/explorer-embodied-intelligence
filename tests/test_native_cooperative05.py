"""Same native owner, mocked original publisher, never ROS or hardware."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from explorer_shared.native_cooperative05 import NativeCooperativeOwner
from explorer_shared.native_stream03 import NativeTeleopMux,NativeDispatchTap
from explorer_shared.native_stream03_api import mount_native_stream_api
from test_native_stream03 import Arm,Teleop,evidence


class Publisher:
    def __init__(self):self.messages=[]
    def publish(self,message):self.messages.append(message)
    def get_subscription_count(self):return 1


class OriginalArm(Arm):
    def __init__(self):
        super().__init__();self.pub=Publisher();self.motion_config={'streaming':{'lookahead_s':.08}}
    def model(self):return SimpleNamespace(path=lambda *args:{'valid':True})
    def _publish_command(self,pose,runtime_ms,record,phase='command_in_progress',revision=None):
        self.pub.publish(SimpleNamespace(**{'joint'+str(i+1):value for i,value in enumerate(pose)}))
        record.update(servo_deg=list(pose),q_commanded_deg=list(pose),measured=False,attained=False)


class Activation:
    def __init__(self,tick):
        self.valid=True;self.tick=tick;self.mode='cooperative_policy';self.epoch=None
        self.jpeg=b'\xff\xd8original-cached-only\xff\xd9'
        self.camera=SimpleNamespace(snapshot=self.snapshot)
    def snapshot(self,with_jpeg=False):
        return {'jpeg':self.jpeg,'jpeg_sha256':hashlib.sha256(self.jpeg).hexdigest(),'frame_id':'original-native-camera',
                'received_monotonic_ns':self.tick(),'timestamp_basis':'cached_receive_not_exposure'}
    def status(self):return {'input_mode':self.mode,'execution_permission':self.valid}
    def require(self,identity):
        if identity!='auth' or not self.valid:raise ValueError('No current observed authorization')
        return True
    def allowed(self):return self.valid
    def revoke(self,*args,**kwargs):self.valid=False;return {'revoked':True}
    def receipt(self,identity):return {'observed_owner':'fixture'} if self.valid and identity=='auth' else None
    def transition(self,callback,identity):self.require(identity);return callback()


def fixture():
    now=[10.];arm=OriginalArm();original=arm.pub;teleop=Teleop(arm)
    tap=NativeDispatchTap(boot_id='boot',clock_domain='nano:boot',clock=lambda:int(now[0]*1e9))
    mux=NativeTeleopMux(teleop,arm,tap,clock=lambda:now[0]);activation=Activation(tap.clock)
    mux.observation_permit=activation.allowed;mux.install()
    source=NativeCooperativeOwner(mux,activation,lambda:{'servo_deg':[90.]*6,'boot_id':'boot','measured':False},target_limits=[(0,180)]*6)
    mux.publication_evidence=lambda:deepcopy(source.guard.last_publication)
    binding={'run_id':'run-1','object_id':'block-1','coordinator_epoch':'coord-1','authorization_id':'auth','observing':True}
    source.guard.bind(binding)
    return source,mux,teleop,arm,original,tap,activation,now,binding


def start(source,mux,binding):
    model={'model_id':'actual-source-model','checkpoint_sha256':'a'*64,'spec_sha256':'b'*64}
    result=source.begin({**binding,'neutral':True,'model':model});epoch=result['control_epoch']
    mux.resume(epoch,observing=True)
    return epoch,model


def confirmed(binding,challenge):
    return {**binding,'challenge_id':challenge['challenge_id'],
        'coordinator_verified_confirmation_created_after_challenge':True,
        'operator_confirmation':{'challenge_id':'new-operator-receipt','run_id':binding['run_id'],
            'object_id':binding['object_id'],'role':'receiver','statement':'receiver_holds_object',
            'after_command_id':'receiver-close-actual','affirmed':True,'observing':True,'state':'consumed',
            'authenticated_actor':'owner','received_ns':999_000,'clock_domain':'independent-nx-clock'}}


def message(gripper):return SimpleNamespace(**{'joint'+str(i+1):90 if i<5 else gripper for i in range(6)})


def test_install_bind_observation_create_no_commands_or_second_publisher():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture()
    observation=source.observation();jpeg,again=source.observation_frame(observation['observation_id'])
    assert jpeg==activation.jpeg and observation==again
    assert arm.pub.original is publisher and not publisher.messages and not teleop.native_drive
    assert not arm.calls and mux.state=='IDLE'
    assert observation['state_source']=='command_estimate' and observation['reference_is_not_measured']


def test_policy_actual9_same_original_ruckig_constants_lease_and_host_receipts():
    source,mux,teleop,arm,publisher,tap,_,now,binding=fixture();epoch,model=start(source,mux,binding)
    observation=source.observation();lease=teleop.lease
    body={**binding,'control_epoch':epoch,'source_sequence':1,'command_id':'native-command-1',
        'model':model,'action9':[91.]*6+[0.,0.,0.],'source_observation':observation}
    result=source.input(body)
    assert result['accepted'] and not result['native_dispatched'] and teleop.lease==lease
    assert arm.calls[-1]['joints']==[12.5]*6 and arm.calls[-1]['owner']=='keyboard'
    assert arm.motion_config=={'streaming':{'lookahead_s':.08}}
    assert teleop.native_drive==[[0.,0.,0.],[0.,0.,0.]] and not publisher.messages
    record={'command_generation':mux.generation,'input_received_at':arm.stream_intent['received_at']}
    arm._publish_command([91]*6,80,record)
    actual=tap.since()['receipts'][-1]
    assert len(publisher.messages)==1 and actual['kind']=='arm_host_publish'
    source_binding=actual['source_intent_binding']['source']
    assert source_binding['state_source']=='policy_prediction_not_measured_leader'
    assert source_binding['requested_target_action9']==body['action9']
    with pytest.raises(ValueError,match='Measured leader'):mux.leader_intent(epoch,2,[1]*6,evidence())
    with pytest.raises(ValueError,match='nonzero model base'):source.input({**body,'source_sequence':2,'action9':[91]*6+[.1,0,0]})
    assert mux.sequence==1 and len(arm.calls)==1


def test_opening_requires_new_native_clock_challenge_no_nx_timestamp_conversion_or_automatic_renewal():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture();start(source,mux,binding)
    source.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})
    arm.pub.publish(message(100));assert len(publisher.messages)==1  # closing != holding proof
    challenge=source.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'})
    grant=source.guard.authorize(confirmed(binding,challenge))
    assert grant['expires_native_ns']==challenge['issued_native_ns']+source.guard.ttl_ns
    assert not grant['clock_conversion_assumed']
    arm.pub.publish(message(80));assert len(publisher.messages)==2
    receipt=source.guard.status()['last_publication'];assert receipt['opening'] and receipt['guard_grant']['grant_id']==grant['grant_id']
    challenge=source.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'})
    with pytest.raises(ValueError,match='reused'):source.guard.authorize(confirmed(binding,challenge))
    new_proof=confirmed(binding,challenge);new_proof['operator_confirmation']['challenge_id']='another-fresh-operator'
    source.guard.authorize(new_proof)
    teleop.lease=20.  # mock explicit current operator lease; no source heartbeat
    now[0]+=2.01
    with pytest.raises(ValueError,match='NO_FRESH'):arm.pub.publish(message(70))
    assert len(publisher.messages)==2
    source.guard.revoke('STOP')
    with pytest.raises(ValueError):source.guard.authorize(confirmed(binding,challenge))
    with pytest.raises(ValueError):arm.pub.publish(message(70))


def test_stop_or_new_native_authorization_blocks_old_opening_and_reset_requires_observed_safe_object():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture();start(source,mux,binding)
    source.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})
    challenge=source.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'})
    source.guard.authorize(confirmed(binding,challenge));activation.valid=False
    with pytest.raises(ValueError):arm.pub.publish(message(80))
    assert publisher.messages==[]
    activation.valid=True;teleop.stop_latched=True
    with pytest.raises(ValueError):source.guard.reset_safe({**binding,'object_safe_no_dual_hold':False})
    source.guard.reset_safe({**binding,'object_safe_no_dual_hold':True})
    assert not source.guard.status()['armed']


def test_policy_source_frame_hash_id_and_expiry_cannot_be_fabricated_and_errors_leave_intent_unchanged():
    source,mux,teleop,arm,publisher,tap,_,now,binding=fixture();epoch,model=start(source,mux,binding)
    original=source.observation();body={**binding,'control_epoch':epoch,'source_sequence':1,
        'command_id':'valid','action9':[90]*9,'model':model,'source_observation':original}
    body['action9']=[90]*6+[0]*3
    with pytest.raises(ValueError):source.input({**body,'command_id':None})
    with pytest.raises(ValueError):source.input({**body,'source_observation':{**original,'jpeg_sha256':'f'*64}})
    assert mux.sequence==-1 and mux.latest==[0]*6 and not arm.calls
    now[0]+=2.01;teleop.lease=20.;mux.intent_deadline=20.
    with pytest.raises(ValueError,match='observation expired'):source.input(body)


def test_authenticated_source_routes_default_block_and_explicit_observed_metadata_only():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture();app=FastAPI()
    mount_native_stream_api(app,mux,token='private-test-token-only',observed_test_permit=lambda:False,
        activation=activation,cooperative=source,reference_reader=source.reference)
    client=TestClient(app);headers={'Authorization':'Bearer private-test-token-only'}
    assert client.get('/api/explorer/cooperative/status').status_code==401
    assert client.get('/api/explorer/cooperative/observation',headers=headers).status_code==200
    assert client.post('/api/explorer/cooperative/begin',headers=headers,json=binding).status_code==409
    assert not publisher.messages and not teleop.native_drive and not arm.calls


def test_revoke_during_blocked_authorize_cannot_attach_old_grant():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture();start(source,mux,binding)
    source.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})
    challenge=source.guard.challenge({**binding,'after_receiver_command_id':'receiver-close-actual'})
    entered,release=threading.Event(),threading.Event();original=activation.allowed;errors=[]
    def allowed():
        if threading.current_thread().name=='old-authorize':entered.set();assert release.wait(2)
        return original()
    activation.allowed=allowed
    def authorize():
        try:source.guard.authorize(confirmed(binding,challenge))
        except ValueError as error:errors.append(error)
    worker=threading.Thread(target=authorize,name='old-authorize');worker.start();assert entered.wait(1)
    source.guard.revoke('EXPLICIT_REVOKE');release.set();worker.join(2)
    assert errors and source.guard.status()['grant'] is None
    with pytest.raises(ValueError):arm.pub.publish(message(80))
    assert not publisher.messages


@pytest.mark.parametrize('kind',['opening','relative_motion'])
def test_blocked_publication_invalidates_unissued_factory_journal_and_revokes_old_stream(kind):
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture();start(source,mux,binding)
    source.guard.arm(binding,{'servo_deg':[90]*6,'boot_id':'boot','measured':False})
    target=message(80 if kind=='opening' else 90)
    if kind=='relative_motion':target.joint1=91
    with pytest.raises(ValueError):arm.pub.publish(target)
    assert not publisher.messages and source.guard.reference_fault is not None
    assert not mux.active and mux.latest==[0]*6 and not activation.valid
    assert mux.state=='COOPERATIVE_PUBLICATION_REJECTED' and source.guard.armed
