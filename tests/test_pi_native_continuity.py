"""Finite actual source service / fake native sinks; no hardware or HTTP."""
from copy import deepcopy
import threading

import numpy as np
import pytest

from explorer_shared.native_gripper_alignment import gripper_alignment
from test_pi_native_retarget import service,start,run_captured,Geometry
from test_pi_native_hold_gate import held,MuxPeer,Station,PiNativeRetargetService


@pytest.mark.parametrize('waiting',['status','reference','resume','base'])
def test_first_held_reanchors_after_all_admission_network_io(tmp_path,waiting):
    source,station,peer=service(tmp_path);start(source,held=False);peer.block=waiting;errors=[]
    worker=threading.Thread(target=run_captured,args=(lambda:held(source),errors));worker.start()
    assert peer.entered.wait(1);station.update(7)
    assert source.mode=='ACTIVATING' and not any(name=='input' for name,_ in peer.calls)
    peer.block=None;peer.release.set();worker.join(2)
    assert not errors and source.mode=='ACTIVE' and source.geometry.anchor[0]==[7.]*6
    source._step_once();assert not any(name=='input' for name,_ in peer.calls)
    source.close()


@pytest.mark.parametrize('drop',['stale','duplicate','out_of_order'])
def test_single_dropped_packet_does_not_zero_interrupt_reacquire_or_add_rpc(tmp_path,drop):
    source,station,peer=service(tmp_path);start(source);epoch=source.epoch
    station.update(1);source._step_once();accepted=station.tick;before=deepcopy(peer.calls)
    if drop=='stale':station.tick-=3_000_000_000
    elif drop=='out_of_order':station.tick-=1
    source._step_once()
    assert peer.calls==before and source.epoch==epoch and source.mode=='ACTIVE' and source.error is None
    station.update(2);assert station.tick>accepted;source._step_once()
    assert [data['velocity'] for name,data in peer.calls if name=='input']==[[1.]*6,[2.]*6]
    assert sum(source.packet_drops.values())==1
    assert len([1 for name,_ in peer.calls if name=='resume'])==1
    assert len([1 for name,_ in peer.calls if name=='base'])==1
    source.close()


def test_stale_packet_with_changed_identity_hard_fences_attempt(tmp_path):
    source,station,peer=service(tmp_path);start(source);original=station.status
    station.tick-=3_000_000_000
    station.status=lambda:{**original(),'measurements':{**original()['measurements'],'clock_domain':'pi:foreign'}}
    before=len(peer.calls);source._step_once()
    assert source.mode=='INTERRUPTED' and len(peer.calls)==before and 'changed' in source.error
    source.close()


def test_stable_mobile_lease_adds_no_permission_get_and_native_still_rejects_revoked_chassis(tmp_path):
    source,_,peer=service(tmp_path);peer.chassis=True;start(source,operator_profile='MOBILE_MANIPULATION')
    before=len(peer.calls);held(source,1,base_actions={'forward':.123456789})
    assert [name for name,_ in peer.calls[before:]]==['base']
    assert peer.calls[-1][1]['actions']=={'forward':.123456789}
    peer.chassis=False
    with pytest.raises(PermissionError):held(source,2,base_actions={'forward':.123456789})
    assert source.mode=='INTERRUPTED';source.close()


def test_actual_native_mux_unchanged_intents_across_jitter_then_original_expiry_requires_explicit_reacquire(tmp_path):
    station,peer=Station(),MuxPeer();source=PiNativeRetargetService(Geometry(),station,tmp_path,peer,allow_synthetic=True)
    start(source);source_epoch=source.epoch
    for delta,value in ((.01,1.),(.08,1.25),(.02,-.5),(.10,2.)):
        peer.clock[0]+=delta;station.update(value);source._step_once()
        peer.teleop._update_arm_stream_locked({},False)
        assert peer.arm.calls[-1]['joints']==[value]*6
        assert source.epoch==source_epoch and source.mode=='ACTIVE'
    assert len([1 for name,_ in peer.calls if name=='resume'])==1
    peer.clock[0]=peer.teleop.lease+.01;station.update(3);source._step_once()
    assert source.mode=='INTERRUPTED' and source.error=='ValueError: Observed native user lease/recenter is not active'
    before=len(peer.calls);source._step_once();assert len(peer.calls)==before
    assert len([1 for name,_ in peer.calls if name=='resume'])==1
    source.close()


def aligned_geometry(slope=1.):
    geometry=Geometry();geometry.gripper_mapper=lambda value:90.+slope*(value-50.)
    return geometry


def test_admission_uses_original_wire_endpoint_not_floating_command_estimate_and_no_sum_tolerance():
    geometry=aligned_geometry();leader=[0.]*5+[50.4]
    result=gripper_alignment(geometry,leader,[90.]*5+[90.1],calibration_sha256='a'*64,wire_target=[90]*6)
    assert result['aligned'] and result['current_original_wire_endpoint_deg']==90
    assert result['delta_deg']==pytest.approx(.3) and result['source_one_tick_bound_deg']>0
    result=gripper_alignment(geometry,[0.]*5+[50.54],[90.]*6,calibration_sha256='a'*64)
    assert not result['aligned']  # .54 + encoder uncertainty crosses ORIGINAL .55 boundary.
    assert result['serialized_candidate_deg']==90 and not result['encoder_uncertainty_same_endpoint']
    not_drained=gripper_alignment(geometry,leader,[90.]*5+[90.6],calibration_sha256='a'*64,wire_target=[90]*6)
    assert not not_drained['aligned'] and not_drained['serialized_command_estimate_deg']==91
    result=gripper_alignment(geometry,leader,[90.]*5+[90.1],calibration_sha256='a'*64)
    assert not result['aligned'] and 'integer endpoint' in result['detail']


@pytest.mark.parametrize('candidate',[89.449,89.45,89.5,89.99,90.,90.5,90.549,90.55,90.551])
def test_admitted_uncertainty_never_changes_initial_original_serialized_endpoint(candidate):
    geometry=aligned_geometry();result=gripper_alignment(geometry,[0.]*5+[candidate-40],[90.]*6,calibration_sha256='a'*64)
    # Independently reproduce ORIGINAL serialization, including bankers ties.
    def original_serialization(value):
        q=np.rint(np.array([value])).astype(int);hold=np.abs(np.array([value])-np.array([90]))<.55
        q[hold]=np.array([90])[hold];return int(q[0])
    if result['aligned']:
        for endpoint in (candidate,candidate-result['source_one_tick_bound_deg'],candidate+result['source_one_tick_bound_deg']):
            assert original_serialization(endpoint)==90
    else:assert result['reason']=='ALIGN_LEADER_GRIPPER_TO_CURRENT_COMMAND_ESTIMATE'


def test_missing_alignment_profile_and_moved_gripper_during_neutral_never_emit_intent(tmp_path):
    source,station,peer=service(tmp_path/'unknown');source.geometry.context=None;start(source,held=False)
    with pytest.raises(ValueError,match='QUANTIZATION_UNAVAILABLE'):held(source)
    assert not any(name in ('resume','base','input') for name,_ in peer.calls);source.close()
    source,station,peer=service(tmp_path/'moved');source.geometry.gripper_mapper=lambda value:90.+value
    start(source,held=False);original=peer.keyboard_base
    def delayed_neutral(*args,**kwargs):
        receipt=original(*args,**kwargs);station.update(10);return receipt
    peer.keyboard_base=delayed_neutral
    with pytest.raises(ValueError,match='ALIGN_LEADER_GRIPPER'):held(source)
    assert source.mode=='INTERRUPTED' and not any(name=='input' for name,_ in peer.calls)
    assert source.status()['gripper_alignment']['delta_deg']==10;source.close()


def test_unsupported_native_serialization_profile_refuses_before_resume(tmp_path):
    source,_,peer=service(tmp_path);start(source,held=False);original=peer.status
    peer.status=lambda:{**original(),'arm_backend':'foreign_backend'}
    with pytest.raises(ValueError,match='PROFILE_UNSUPPORTED'):held(source)
    assert not any(name in ('resume','base','input') for name,_ in peer.calls);source.close()


def test_acquire_uses_last_returned_wire_reference_after_neutral_not_pre_io_estimate(tmp_path):
    source,station,peer=service(tmp_path);start(source,held=False);reference=[90.]*6
    peer.reference=lambda:{'boot_id':'nano-boot','servo_deg':list(reference),'measured':False}
    original=peer.keyboard_base
    def last_old_publication_returns(*args,**kwargs):
        receipt=original(*args,**kwargs);reference[:5]=[92.]*5;station.update(5);return receipt
    peer.keyboard_base=last_old_publication_returns
    held(source)
    assert source.geometry.anchor==([5.]*6,[92.]*5+[90.])
    assert source.estimate['servo_deg']==[92.]*5+[90.]
    source.close()


def test_repeated_clutch_actual_repository_fk_ik_first_goal_remains_current_command(tmp_path):
    from test_native_stream03 import geometry as repository_geometry
    source,station,peer=service(tmp_path);mapper,_=repository_geometry();mapper.context=Geometry.context
    source.geometry=mapper;station.positions=[0.,-.6,.9,-.3,0.,50.]
    start(source,held=False)
    for cycle in range(5):
        station.positions[0]=cycle*.01;station.tick+=1
        held(source)
        station.tick+=1;source._step_once()
        assert source.mode=='ACTIVE' and source.last_proposal['proposal']['accepted']
        assert source.last_proposal['proposal']['joint_velocity_deg_s']==pytest.approx([0.]*6,abs=1e-6)
        assert source.last_proposal['proposal']['desired_servo_deg']==pytest.approx([90.]*6,abs=1e-7)
        source.recenter({'control_epoch':source.epoch,'observing':True})
        assert source.mode=='CLUTCHED'
    assert len([name for name,_ in peer.calls if name=='resume'])==5
    source.close()


def test_original_serialization_source_pin_and_extracted_quantizer(tmp_path):
    import ast
    import hashlib
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from explorer_shared.native_stream03 import AUDITED_FACTORY_SHA256
    root=os.environ.get('EXPLORER_NATIVE_STARTUP_SOURCE')
    if not root:pytest.skip('Exact retained original factory source requires explicit source path')
    raw=(Path(root)/'src/arm_velocity.py').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==AUDITED_FACTORY_SHA256['src/arm_velocity.py']
    tree=ast.parse(raw)
    step=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='step')
    names={'quantized','hold'};statements=[]
    for node in step.body:
        if isinstance(node,ast.Assign):
            target=node.targets[0]
            if isinstance(target,ast.Name) and target.id in names or isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id=='quantized':
                statements.append(node)
    assert len(statements)==3  # EXACT np.rint/retention/masked assignment shape.
    quantizer=compile(ast.fix_missing_locations(ast.Module(body=statements,type_ignores=[])),'actual-original-quantizer','exec')
    for value in (89.449,89.5,90.,90.5,90.549,90.55,90.551):
        geometry=aligned_geometry();proof=gripper_alignment(geometry,[0.]*5+[value-40],[90]*6,calibration_sha256='a'*64)
        if proof['aligned']:
            for endpoint in (value,value-proof['source_one_tick_bound_deg'],value+proof['source_one_tick_bound_deg']):
                scope={'np':np,'endpoint':np.array([endpoint]*6),'self':SimpleNamespace(previous_quantized=np.array([90]*6))}
                exec(quantizer,scope)
                assert scope['quantized'].tolist()==[90]*6
