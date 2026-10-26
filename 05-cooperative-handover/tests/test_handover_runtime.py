import json
from dataclasses import replace

import pytest

from explorer_shared.handover import HoldEvidence
from explorer_shared.handover_runtime import (ArmCommand, ArmObservation, CallbackArmTransport,
    CorrelatedEvidence, DispatchReceipt, HandoverPlan, SO101StationTransport, run_handover)


class Clock:
    def __init__(self): self.ns = 1_000_000_000
    def __call__(self): self.ns += 1_000_000; return self.ns


class Fixture:
    def __init__(self):
        self.clock, self.commands, self.last, self.stops = Clock(), [], {}, []
        self.bad_evidence, self.fail, self.stale, self.wrong_receipt = None, None, False, False
        self.plan = HandoverPlan('run-A', 'object-A', 'virtual-registration', tuple(ArmCommand(str(i),r,o,{'fixture_target':i})
            for i,(r,o) in enumerate([('giver','offer'),('receiver','approach'),('receiver','close'),('giver','release'),('receiver','finish')])))
        self.transports = {r: CallbackArmTransport(r,self.submit,lambda r=r:self.observe(r),lambda run,r=r:self.stop(r)) for r in ('giver','receiver')}

    def submit(self, command, run, obj, authorization):
        self.commands.append((command.operation, authorization))
        if command.operation == self.fail: raise ConnectionError('fixture disconnected')
        self.last[command.role] = command.command_id
        return DispatchReceipt(command.command_id,run,obj,command.role,not self.wrong_receipt,self.clock(),'handover-coordinator',{'fixture':'dispatched'})

    def observe(self, role):
        return ArmObservation(role,True,self.clock()-int(self.stale)*10**10,'handover-coordinator',{'fixture':'measured'},self.last.get(role))

    def stop(self, role): self.stops.append(role); return {'existing_hold':True}

    def evidence(self, statement, run, obj, role, after, timeout):
        stamp=self.clock()
        e=HoldEvidence(statement,obj,role,'virtual_test_oracle',statement,True,stamp,'fixture-source',stamp,'handover-coordinator')
        body=CorrelatedEvidence(run,obj,role,after,e,{'fixture':'object-observer'})
        if statement == 'receiver_holds_object' and self.bad_evidence:
            return self.bad_evidence(body)
        return body

    def run(self, path, **kwargs):
        return run_handover(self.plan,self.transports,self.evidence,path,mode='virtual',authority=lambda:{},clock=self.clock,**kwargs)


def test_drives_transports_in_order_and_authorizes_release_from_correlated_hold(tmp_path):
    f=Fixture();r=f.run(tmp_path)
    assert r['protocol_completed'] and r['outcome']=='unknown'
    assert [x[0] for x in f.commands] == ['offer','approach','close','release','finish']
    auth=f.commands[3][1]
    assert auth['object_id']=='object-A' and auth['after_command_id']=='2'
    assert auth['evidence_id']=='receiver_holds_object'
    assert f.stops==['giver','receiver'] and not r['physical_skill_success_claimed']
    assert any(json.loads(line)['kind']=='dispatch_intent' for line in (tmp_path/'handover-journal.jsonl').read_text().splitlines())
    assert f.run(tmp_path)==r and len(f.commands)==5  # completed receipt is idempotent.


@pytest.mark.parametrize('corrupt',[
    lambda b: replace(b,run_id='old-run'), lambda b: replace(b,object_id='other-object'),
    lambda b: replace(b,after_command_id='old-close'),
    lambda b: replace(b,evidence=replace(b.evidence,kind='closed_gripper_command',independent=False)),
    lambda b: replace(b,evidence=replace(b.evidence,value=False)),
    lambda b: replace(b,evidence=replace(b.evidence,received_ns=0)),
])
def test_unconfirmed_or_uncorrelated_hold_never_releases_giver(tmp_path,corrupt):
    f=Fixture();f.bad_evidence=corrupt;r=f.run(tmp_path)
    assert not r['protocol_completed'] and r['state']=='SAFE_STOP'
    assert 'release' not in [x[0] for x in f.commands]
    assert f.stops==['giver','receiver']


@pytest.mark.parametrize('failure',['close','approach'])
def test_disconnect_stops_both_without_release_or_retry(tmp_path,failure):
    f=Fixture();f.fail=failure;r=f.run(tmp_path)
    assert 'ConnectionError' in r['error'] and not r['protocol_completed']
    assert 'release' not in [x[0] for x in f.commands]
    assert f.stops==['giver','receiver']


def test_stale_state_and_accepted_only_receipts_are_not_success(tmp_path):
    f=Fixture();f.stale=True;r=f.run(tmp_path/'stale')
    assert len(f.commands)==1 and not r['protocol_completed']
    f=Fixture();f.wrong_receipt=True;r=f.run(tmp_path/'accepted')
    assert len(f.commands)==1 and 'matching dispatched receipt' in r['error']


def test_physical_presence_condition_and_virtual_oracle_rejection(tmp_path):
    f=Fixture()
    r=run_handover(f.plan,f.transports,f.evidence,tmp_path/'absent',mode='physical',authority=lambda:{},clock=f.clock)
    assert not f.commands and 'visible on Pi' in r['error']
    f=Fixture()
    r=run_handover(f.plan,f.transports,f.evidence,tmp_path/'oracle',mode='physical',authority=lambda:{'operator_near':True},clock=f.clock)
    assert not f.commands and 'Giver holding evidence' in r['error']


def test_interruption_and_tampering_do_not_replay_commands(tmp_path):
    f=Fixture();(tmp_path/'handover-journal.jsonl').write_text('{"kind":"dispatch_intent"}\n')
    with pytest.raises(ValueError,match='Interrupted'):f.run(tmp_path)
    assert not f.commands
    p=tmp_path/'done';f.run(p)
    body=json.loads((p/'handover-result.json').read_text());body['protocol_completed']=False
    (p/'handover-result.json').write_text(json.dumps(body))
    with pytest.raises(ValueError,match='checksum'):f.run(p)
    assert len(f.commands)==5


def test_station_transport_waits_for_native_dispatch_and_preserves_contract():
    clock=Clock();requests=[];counter=[0]
    frame={'monotonic_ns':100,'clock_domain':'pi-boot','frame_id':'frame-1'}
    def request(method,path,body):
        requests.append((method,path,body))
        if method=='GET':
            counter[0]+=1
            native={'control_epoch':'epoch','sequence':0,'source_observation':{'frame_id':'frame-1'},'requested':[0]*6}
            return {'control_epoch':'epoch','last_command':native if counter[0]>1 else None}
        return {'accepted':True}
    transport=SO101StationTransport(request,'epoch',lambda:frame,lambda:None,clock=clock,sleep=lambda _:None)
    receipt=transport.submit(ArmCommand('close','receiver','close',{'action':[0]*6,'duration_s':.5}),'run','obj',None)
    assert receipt.dispatched and counter[0]==2
    body=requests[0][2]
    assert body['sequence']==0 and body['control_epoch']=='epoch'
    assert body['source_frame_id']=='frame-1' and body['action_units'][0]=='calibrated_normalized_-100_100'


def test_station_acceptance_without_servo_receipt_times_out():
    transport=SO101StationTransport(lambda *a:{'control_epoch':'epoch'},'epoch',lambda:{'monotonic_ns':1,'clock_domain':'pi','frame_id':'f'},lambda:None,sleep=lambda _:None,dispatch_timeout_s=.01)
    with pytest.raises(TimeoutError):transport.submit(ArmCommand('c','receiver','close',{'action':[0]*6}),'r','o',None)


def test_evidence_wait_timeout_cannot_authorize_release(tmp_path):
    f=Fixture()
    def delay(body):f.clock.ns+=31_000_000_000;return body
    f.bad_evidence=delay;r=f.run(tmp_path)
    assert not r['protocol_completed'] and 'release' not in [x[0] for x in f.commands]
    assert r['state']=='SAFE_STOP'


def test_cancel_after_receiver_close_preserves_giver_hold(tmp_path):
    f=Fixture();r=f.run(tmp_path,cancelled=lambda:any(c[0]=='close' for c in f.commands))
    assert not r['protocol_completed'] and 'release' not in [x[0] for x in f.commands]
    assert 'cancelled' in r['error'] and f.stops==['giver','receiver']


def test_expired_release_permit_is_rejected_before_station_submission():
    calls=[]
    transport=SO101StationTransport(lambda *args:calls.append(args),'epoch',lambda:{'monotonic_ns':1,'clock_domain':'pi','frame_id':'f'},lambda:None,clock=lambda:100)
    with pytest.raises(ValueError,match='expired'):
        transport.submit(ArmCommand('r','receiver','release',{'action':[0]*6}),'run','obj',{'valid_until_ns':99})
    assert not calls
