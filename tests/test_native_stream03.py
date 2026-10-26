"""Exact retained native method ABI, mocked ownership and real repository FK/IK."""
from copy import deepcopy
from pathlib import Path
import threading
import io
import json

import numpy as np
import pytest

from explorer_shared.kinematics import URDFChain
from explorer_shared.native_stream03 import NativeDispatchTap, NativeTeleopMux, RelativeLeaderGeometry
from explorer_shared.native_stream03_api import mount_native_stream_api
from explorer_shared.native_stream03_client import NativeStreamClient


class Arm:
    supports_velocity_stream = True
    def __init__(self):
        self.streaming_active, self.calls, self.publications = False, [], []
        self.stream_lock, self.stream_intent = threading.Lock(), None
    def stream_velocity(self,joint_velocity,xyz_velocity,generation,valid_until,permit,owner='operator',coordinated=True):
        self.calls.append(dict(joints=list(joint_velocity),xyz=list(xyz_velocity),generation=generation,
                               valid_until=valid_until,permit=permit,owner=owner,coordinated=coordinated))
        self.streaming_active = any(joint_velocity)
        self.stream_intent = {'owner':owner,'generation':generation,'received_at':float(len(self.calls)),
                              'valid_until':valid_until}
        return {'accepted':True,'latest_intent':True,'measured':False}
    def _publish_command(self,pose,runtime_ms,record,phase='command_in_progress',revision=None):
        self.publications.append(list(pose))
        record.update(q_commanded_deg=list(pose),servo_deg=list(pose),measured=False,attained=False,
                       runtime_ms=runtime_ms,phase=phase)
    def status(self):
        return {'servo_deg':[90]*6,'estimated_only':True,'velocity_stream_supported':True}


class Teleop:
    def __init__(self,arm):
        self.arm = arm; self.lock = threading.RLock()
        self.owner = 'keyboard'; self.generation = 8; self.stop_latched = True
        self.inputs = {}; self.lease = 10.45; self.arm_busy = False
        self.native_drive, self.native_stop, self.original_arm_updates = [], 0, []
        self.drive = lambda values:self.native_drive.append(list(values))
        self.release = lambda:None
        self.stop_all = self._global_stop
    def _global_stop(self):
        self.native_stop += 1
        return {'queued':True,'id':'native-stop'}
    def _stream_permitted(self,generation):
        return generation == self.generation and self.owner is not None and not self.stop_latched
    def _update_arm_stream_locked(self,values,precision):
        self.original_arm_updates.append(deepcopy(values))
    def resume(self,source,observing):
        assert source == self.owner and observing is True and not self.inputs
        self.stop_latched = False
        return {'owner':source,'stop_latched':False}
    def update(self,source,inputs,observing=True,precision=False):
        assert source == self.owner and observing is True
        self.inputs = dict(inputs); self.lease += .1
        if not self.stop_latched:
            self._update_arm_stream_locked(self.inputs,precision)
        return {'owner':source,'lease':self.lease}
    def stop(self):
        self.stop_latched = True; self.generation += 1; self.inputs = {}
        self.stop_all()
        return {'stop_latched':True}


def setup_mux(*, chassis=False, capacity=10):
    arm = Arm(); teleop = Teleop(arm); clock = [10.]
    tap = NativeDispatchTap(boot_id='boot',clock_domain='nano',capacity=capacity,clock=lambda:int(clock[0]*1e9))
    mux = NativeTeleopMux(teleop,arm,tap,chassis_motion_allowed=chassis,clock=lambda:clock[0])
    mux.install()
    epoch = mux.begin(observing=True,neutral=True)['control_epoch']
    return mux,teleop,arm,tap,epoch,clock


def evidence():
    return {'clock_domain':'pi','boot_id':'pi-boot','sample_id':'leader-1',
            'calibration_sha256':'a'*64,'state_source':'measured_leader'}


def test_mirror_needs_no_recording_and_base_keyboard_does_not_preempt_leader_arm():
    mux,teleop,arm,tap,epoch,clock = setup_mux(chassis=True)
    mux.resume(epoch,observing=True)
    before_lease = teleop.lease
    receipt = mux.leader_intent(epoch,1,[1.,2.,3.,4.,5.,6.],evidence())
    assert teleop.lease == before_lease  # source frames never manufacture user observation
    assert receipt['accepted'] and not receipt['native_dispatched']
    mux.keyboard_base_input(epoch,{'forward':.5},observing=True)
    assert arm.calls[-1]['joints'] == [1.,2.,3.,4.,5.,6.]
    assert arm.calls[-1]['owner'] == 'keyboard' and arm.calls[-1]['coordinated'] is True
    assert not teleop.original_arm_updates and tap.since()['receipts'] == []
    assert mux.status()['native_queue_length'] == 0


def test_old_leader_is_neutralized_despite_continuing_keyboard_lease_and_never_auto_resumes():
    mux,teleop,arm,_,epoch,clock = setup_mux()
    mux.resume(epoch,observing=True); mux.leader_intent(epoch,1,[3.]*6,evidence())
    original_deadline = teleop.lease
    teleop.lease = 100.
    clock[0] = original_deadline + .01
    mux.keyboard_base_input(epoch,{},observing=True)
    assert arm.calls[-1]['joints'] == [0.]*6
    assert mux.status()['state'] == 'SOURCE_EXPIRED_RECENTER_REQUIRED'
    with pytest.raises(ValueError):
        mux.leader_intent(epoch,2,[3.]*6,evidence())
    mux.recenter(epoch); mux.resume(epoch,observing=True)
    mux.leader_intent(epoch,2,[1.]*6,evidence())
    assert mux.status()['state'] == 'ACTIVE'


def test_old_nonzero_permit_is_revoked_immediately_by_clutch_and_cannot_revive_after_resume():
    mux,teleop,arm,_,epoch,_=setup_mux()
    mux.resume(epoch,observing=True);mux.leader_intent(epoch,1,[2.]*6,evidence())
    teleop._update_arm_stream_locked({},False);old_permit=arm.calls[-1]['permit']
    assert old_permit()
    mux.recenter(epoch)
    assert not old_permit()  # no next native tick required
    mux.resume(epoch,observing=True)
    assert not old_permit()  # same native generation must not resurrect it


def test_old_nonzero_permit_expires_before_tick_even_if_keyboard_renews_native_lease():
    mux,teleop,arm,_,epoch,clock=setup_mux()
    mux.resume(epoch,observing=True);mux.leader_intent(epoch,1,[2.]*6,evidence())
    teleop._update_arm_stream_locked({},False);old_permit=arm.calls[-1]['permit']
    source_deadline=mux.intent_deadline;teleop.lease=100.;clock[0]=source_deadline+.01
    assert not old_permit()
    with pytest.raises(ValueError,match='explicit recenter'):
        mux.leader_intent(epoch,2,[2.]*6,evidence())
    mux.recenter(epoch);mux.resume(epoch,observing=True)
    assert not old_permit()


def test_global_stop_preemption_and_old_generations_cannot_restart_mirror():
    mux,teleop,arm,_,epoch,_ = setup_mux()
    mux.resume(epoch,observing=True); mux.leader_intent(epoch,1,[2.]*6,evidence())
    teleop._update_arm_stream_locked({},False)
    old_permit = arm.calls[-1]['permit']
    teleop.stop()
    assert not old_permit() and not mux.active
    with pytest.raises(ValueError):
        mux.leader_intent(epoch,2,[2.]*6,evidence())
    assert mux.status()['state'] == 'STOPPED'
    assert teleop.native_stop == 1


def test_out_of_order_samples_drop_without_fifo_or_lease_extension():
    mux,teleop,arm,_,epoch,_ = setup_mux()
    mux.resume(epoch,observing=True)
    mux.leader_intent(epoch,2,[2.]*6,evidence())
    with pytest.raises(ValueError):
        mux.leader_intent(epoch,1,[-2.]*6,evidence())
    teleop._update_arm_stream_locked({},False)
    assert arm.calls[-1]['joints'] == [2.]*6 and mux.status()['out_of_order_dropped'] == 1


def test_physical_chassis_prohibition_blocks_before_original_dispatch():
    mux,teleop,arm,tap,epoch,_ = setup_mux()
    mux.resume(epoch,observing=True)
    with pytest.raises(PermissionError):
        mux.keyboard_base_input(epoch,{'forward':1.},observing=True)
    with pytest.raises(PermissionError):
        teleop.drive([.1,0.,0.])
    assert teleop.native_drive == [] and tap.since()['receipts'] == []
    with pytest.raises(ValueError):
        mux.keyboard_base_input(epoch,{'joint1_increase':1.},observing=True)


def test_only_actual_original_host_publication_creates_receipt_full9_requires_base_context():
    mux,teleop,arm,tap,epoch,_ = setup_mux()
    mux.resume(epoch,observing=True); mux.leader_intent(epoch,1,[1.]*6,evidence())
    assert tap.since()['receipts'] == []
    record = {'source':'timed_factory_command_estimate','q_estimated_deg':[89.]*6}
    arm._publish_command([90]*6,80,record,revision=0)
    partial = tap.since()['receipts'][-1]
    assert partial['command9'] == [90.]*6 + [None]*3
    assert partial['command9_mask'] == [True]*6 + [False]*3
    teleop.stop_all()  # mock original native STOP publication, no hardware
    row = tap.since()['receipts'][-1]
    assert row['command9'] == [90.]*6 + [0.]*3
    assert row['command9_mask'] == [True]*9
    assert row['components_published_in_this_event'] == [6,7,8]
    assert not row['hardware_dispatch_verified'] and row['measured_q'] is None
    assert not row['attainment_measured'] and not row['contact_verified']


def test_bounded_dispatch_ring_reports_gaps_and_recording_fault_does_not_break_native_publish():
    mux,teleop,arm,tap,_,_ = setup_mux(capacity=2)
    for index in range(4):
        arm._publish_command([90+index]*6,80,{})
    assert tap.since(0)['gap'] and len(tap.since(0)['receipts']) == 2
    original = tap.append
    tap.append = lambda *args,**kwargs: (_ for _ in ()).throw(OSError('mock recording failure'))
    arm._publish_command([90]*6,80,{})
    assert len(arm.publications) == 5 and tap.capture_failures == 1
    tap.append = original


def test_native_publication_before_intent_binding_is_joined_later_by_exact_native_fields():
    mux,teleop,arm,tap,epoch,_ = setup_mux()
    mux.resume(epoch,observing=True); mux.leader_intent(epoch,4,[1.]*6,evidence())
    original_stream = arm.stream_velocity
    def synchronous_publish(*args,**kwargs):
        accepted = original_stream(*args,**kwargs)
        record = {'command_generation':arm.stream_intent['generation'],
                  'input_received_at':arm.stream_intent['received_at']}
        arm._publish_command([90]*6,80,record)
        assert tap.since()['receipts'][-1]['source_intent_binding'] is None
        return accepted
    arm.stream_velocity = synchronous_publish
    teleop._update_arm_stream_locked({},False)
    row = tap.since()['receipts'][-1]
    assert row['source_intent_binding']['source']['source_sequence'] == 4
    assert row['source_intent_binding']['source']['source_evidence']['clock_domain'] == 'pi'
    assert row['stamp']['clock_domain'] == 'nano'
    assert not row['source_intent_binding']['source']['cross_device_clock_mapping_assumed']


def test_prior_command9_uses_original_ruckig_estimate_and_actual_base_context_not_target_or_ban():
    mux,teleop,arm,tap,epoch,_ = setup_mux()
    tap.reference_header({'boot_id':'boot','measured':False,'servo_deg':[90]*6},'e'*64)
    arm._publish_command([92]*6,80,{'q_estimated_deg':[91.]*6})
    assert tap.since()['receipts'][-1]['preceding_command9'] is None
    teleop.stop_all()  # actual MOCK native zero request establishes its provenance
    arm._publish_command([94]*6,80,{'q_estimated_deg':[93.]*6})
    row=tap.since()['receipts'][-1]
    assert row['command9']==[94.]*6+[0.]*3
    assert row['preceding_command9']['values']==[93.]*6+[0.]*3
    assert row['preceding_command9']['reference_id']=='e'*64
    assert row['preceding_command9']['state_source']=='command_estimate'
    assert not row['hardware_dispatch_verified']


def test_base_update_during_arm_publication_is_never_backdated_into_prepublication_state():
    tick=[100];tap=NativeDispatchTap(boot_id='boot',clock_domain='nano',clock=lambda:tick[0])
    tap.append('base_stop_host_request',{'velocity':[0.,0.,0.]},source_id='mock-base',started_ns=90)
    tick[0]=250
    tap.append('base_drive_host_request',{'velocity':[.1,0.,0.]},source_id='mock-base',started_ns=240)
    tick[0]=300
    row=tap.append('arm_host_publish',{'q_commanded_deg':[92.]*6,'q_estimated_deg':[91.]*6},
                   source_id='mock-arm',started_ns=200)
    assert row['preceding_command9']['values']==[91.]*6+[0.,0.,0.]
    assert row['command9']==[92.]*6+[.1,0.,0.]
    assert row['preceding_command9']['base_component_receipt_id'].endswith(':1')
    early=tap.append('arm_host_publish',{'q_commanded_deg':[92.]*6,'q_estimated_deg':[91.]*6},
                     source_id='mock-arm',started_ns=50)
    assert early['preceding_command9'] is None


def geometry():
    root = Path(__file__).resolve().parents[1]
    source = URDFChain.from_urdf(root/'04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf','base_link','gripper_frame_link')
    target = URDFChain.from_urdf(root/'01-autonomous-learning/src/legacy/config/explorer.urdf','base_footprint','Gripping')
    # EXACT original factory convention, tested offline; production mount passes
    # servo_coordinates functions from that existing original module itself.
    signs = np.array([-1.,1.,1.,1.,1.])
    to_rad = lambda servo:np.radians(np.asarray(servo)-90.)*signs
    to_servo = lambda radians:np.degrees(np.asarray(radians))*signs+90.
    registration = {'provenance':'synthetic','sha256':'b'*64,
                     'source_urdf_sha256':source.source_sha256,'target_urdf_sha256':target.source_sha256}
    mapper = RelativeLeaderGeometry(source,target,lambda q:np.asarray(q),lambda grip:90.,to_rad,to_servo,
                                     registration=registration)
    return mapper,to_servo


def test_actual_fk_clutch_ik_factory_servo_convention_returns_neutral_at_anchor():
    mapper,to_servo = geometry()
    source = [0.,-.6,.9,-.3,0.,50.]
    target = [*to_servo([0.,-.6,1.,-.4,0.]),90.]
    mapper.recenter(source,target)
    result = mapper.propose(source,target,original_native_period_s=.04)
    assert result['accepted'] and np.allclose(result['joint_velocity_deg_s'],0.,atol=1e-6)
    assert not result['native_dispatched']
    assert np.allclose(mapper.to_radians([90]*5),0.)
    assert mapper.to_radians([100,90,90,90,90])[0] < 0


def test_missing_source_normalized_radian_gripper_registration_is_honestly_unregistered():
    mapper,to_servo = geometry()
    mapper.registration = None
    assert mapper.status()['state'] == 'UNREGISTERED'
    with pytest.raises(ValueError,match='registration missing'):
        mapper.recenter([0.]*6,[90.]*6)


def test_authenticated_same_owner_api_defaults_blocked_and_accepted_is_not_dispatch():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    mux,teleop,arm,tap,epoch,_ = setup_mux()
    app = FastAPI(); permit = [False]; token = 'mock-native-owner-token-0123456789'
    mount_native_stream_api(app,mux,token=token,observed_test_permit=lambda:permit[0])
    client = TestClient(app); headers={'Authorization':'Bearer '+token}
    assert client.get('/api/explorer/retarget/status').status_code == 401
    assert client.get('/api/explorer/retarget/status',headers=headers).json()['execution_permission'] is False
    assert client.get('/api/explorer/retarget/reference',headers=headers).json()['estimated_only'] is True
    assert client.post('/api/explorer/retarget/resume',headers=headers,
                       json={'control_epoch':epoch,'observing':True}).status_code == 409
    assert arm.calls == []
    permit[0] = True  # MOCK server-side observation permit, no real actuator route
    assert client.post('/api/explorer/retarget/resume',headers=headers,
                       json={'control_epoch':epoch,'observing':True}).status_code == 200
    response = client.post('/api/explorer/retarget/input',headers=headers,
                            json={'control_epoch':epoch,'source_sequence':1,'joint_velocity_deg_s':[1.]*6,
                                  'source_evidence':evidence()})
    assert response.status_code == 200 and not response.json()['native_dispatched']
    assert client.get('/api/explorer/retarget/dispatch',headers=headers).json()['receipts'] == []
    assert client.post('/api/explorer/retarget/base',headers=headers,
                       json={'control_epoch':epoch,'actions':{'forward':1.},'observing':True}).status_code == 403
    permit[0] = False
    assert client.post('/api/explorer/retarget/stop',headers=headers,json={'control_epoch':epoch}).status_code == 200
    assert teleop.native_stop == 1 and not arm.publications


def test_authenticated_client_uses_only_existing_node_proxy_and_explicit_native_input_no_lease_heartbeat():
    requests = []
    def opener(request,timeout):
        requests.append((request.full_url,request.get_header('Authorization'),
                         json.loads(request.data) if request.data else None))
        return io.BytesIO(json.dumps({'accepted':True,'native_dispatched':False}).encode())
    client = NativeStreamClient('http://nano-observer.test:8092','private-mock-node-token-012345',opener=opener)
    result = client.input('epoch',1,[0.]*6,evidence())
    assert result['accepted'] and not result['native_dispatched']
    assert requests[0][0].endswith('/legacy/explorer/retarget/input')
    assert requests[0][1].startswith('Bearer ')
    assert requests[0][2]['joint_velocity_deg_s'] == [0.]*6
    assert 'observing' not in requests[0][2]  # source samples cannot manufacture operator lease
    assert len(requests) == 1
