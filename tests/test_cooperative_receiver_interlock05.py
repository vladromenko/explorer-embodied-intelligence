"""Pure receiver metadata with explicit software source records; no devices."""
from copy import deepcopy
import hashlib
import json

import pytest

from explorer_shared.contracts import digest
from explorer_shared.cooperative_receiver_interlock05 import ReceiverHoldInterlock,observed_gripper_direction,CONTRACT


def endpoint(label,position,*,calibration='a'*64,uncertainty=.25):
    frame={'frame_id':'frame-'+label,'jpeg_sha256':hashlib.sha256(label.encode()).hexdigest(),
        'monotonic_ns':100,'clock_domain':'pi:boot','boot_id':'boot'}
    record={'schema':'explorer_so101_gripper_endpoint_observation_v1','role':'follower','state_source':'measured_state',
        'verified_on_connected_hardware':True,'follower_calibration_sha256':calibration,'measured_native6':[0.]*5+[position],
        'measurement_uncertainty_native':uncertainty,'measurement_stamp':{'monotonic_ns':101,'clock_domain':'pi:boot','boot_id':'boot'},
        'frame':frame,'operator_confirmation':{'label':label,'observing':True,'user_observed_test_confirmed':True,
            'empty_gripper_confirmed':True,'actor_id':'owner'}}
    return json.dumps(record,indent=2,sort_keys=True).encode()


def rig(*,direction=True):
    tick=[10_000_000_000];helper=ReceiverHoldInterlock(clock_domain='pi:boot',boot_id='boot',clock=lambda:tick[0])
    binding={'purpose':'cooperative05','cooperative_session_id':'run','coordinator_epoch':'coord','object_id':'object',
        'action_contract_id':CONTRACT,'source_domain':'PHYSICAL','input_mode':'leader'}
    permission={'execution_permission':True,'binding':binding,'grant_id':'grant','provider_process':'provider','actor_id':'owner',
        'control_epoch':'native-epoch','native_mode':'teleoperation','clock_domain':'pi:boot','boot_id':'boot',
        'follower_calibration_sha256':'a'*64,'observation_deadline_ns':tick[0]+2_000_000_000}
    profile=observed_gripper_direction(endpoint('empty_open',80.),endpoint('empty_closed',20.)) if direction else None
    bound=helper.bind(binding=binding,permission=permission,direction_profile=profile,expected_revision=0)
    command={'control_epoch':'native-epoch','sequence':4,'requested':[1.,2.,3.,4.,5.,60.],
        'dispatched':[1.,2.,3.,4.,5.,60.],'monotonic_ns':tick[0]-1,'clock_domain':'pi:boot','source_observation':{'frame_id':'real-fake-driver-frame'}}
    return helper,permission,command,tick,profile,bound


def arm(helper,permission,command):
    status=helper.status(permission)
    return helper.arm(interlock_id=status['interlock_id'],expected_revision=status['revision'],permission=permission,last_command=command)


def check(helper,permission,command,target,**extra):
    return helper.check(permission=permission,control_epoch='native-epoch',mode='teleoperation',target=target,last_command=command,**extra)


def giver_release():
    binding={'run_id':'run','object_id':'object','coordinator_epoch':'coord','authorization_id':'actual-giver-observed-grant'}
    return {'schema':'explorer_native_cooperative_publication_v1','receipt_id':'actual-giver-opening-return','binding':binding,
        'opening':True,'host_publication_started_native_ns':500,'host_publication_returned_native_ns':501,
        'native_clock_domain':'nano:separate-boot','native_boot_id':'nano-boot',
        'guard_grant':{'grant_id':'current-giver-hold-grant','challenge':deepcopy(binding)}}


def challenge(helper,permission):
    status=helper.status(permission)
    return helper.finish_challenge(interlock_id=status['interlock_id'],expected_revision=status['revision'],
        permission=permission,giver_release_receipt=giver_release())


def confirmation(pending,*,receipt='independent-proof'):
    return {'coordinator_verified_confirmation_created_after_challenge':True,'coordinator_clock_domain':'nx:third-boot',
        'coordinator_challenge_received_ns':1000,'coordinator_confirmation_checked_ns':1003,
        'operator_confirmation':{'challenge_id':receipt,'statement':'object_transferred_to_receiver','role':'receiver',
            'run_id':'run','object_id':'object','after_command_id':pending['giver_release_receipt_id'],'state':'consumed',
            'affirmed':True,'observing':True,'authenticated_actor':'owner','created_ns':1001,'received_ns':1002,'clock_domain':'nx:third-boot'}}


def finish(helper,permission,pending,proof):
    return helper.finish(interlock_id=pending['interlock_id'],expected_revision=pending['revision'],permission=permission,
        challenge_id=pending['challenge_id'],confirmation=proof)


def test_observed_direction_retains_original_bytes_and_never_guesses_from_normalization():
    opened,closed=endpoint('empty_open',80.),endpoint('empty_closed',20.)
    profile=observed_gripper_direction(opened,closed)
    assert profile['closing_sign']==-1 and profile['original_source_sha256']['open']==hashlib.sha256(opened).hexdigest()
    assert not profile['metric_aperture_or_force_claimed'] and profile['empty_closed_is_not_object_hold_or_squeeze_target']
    with pytest.raises(ValueError,match='Distinct unambiguous'):observed_gripper_direction(opened,endpoint('empty_closed',79.75))
    with pytest.raises(ValueError,match='same calibration'):observed_gripper_direction(opened,endpoint('empty_closed',20.,calibration='b'*64))
    wrong=json.loads(closed);wrong['operator_confirmation']['empty_gripper_confirmed']=False
    with pytest.raises(ValueError,match='explicit USER'):observed_gripper_direction(opened,json.dumps(wrong).encode())


def test_actual_returned_pose_arm5_and_monotonic_gripper_closing_are_enforced_without_clipping():
    helper,permission,command,tick,profile,bound=rig()
    assert bound['binding_current'] and bound['capability_ready'] and not bound['armed']
    armed=arm(helper,permission,command);assert armed['frozen']['original_last_command']==command
    assert check(helper,permission,command,[1.,2.,3.,4.,5.,50.])['allowed']
    assert not check(helper,permission,command,[1.,2.,3.,4.01,5.,50.])['allowed']
    actual=deepcopy(command);actual['dispatched'][5]=50.;actual['monotonic_ns']=tick[0]
    assert not check(helper,permission,actual,[1.,2.,3.,4.,5.,55.])['allowed']  # still below original60, but opens relative to actual50.
    assert not check(helper,permission,actual,[1.,2.,3.,4.,5.,-1.])['allowed']
    assert helper.status(permission)['armed'] and helper.status(permission)['native_commands_sent']==0


def test_missing_direction_stays_notready_and_can_only_freeze_all_six_known_coordinates():
    helper,permission,command,tick,profile,bound=rig(direction=False)
    assert bound['binding_current'] and not bound['capability_ready']
    arm(helper,permission,command)
    assert check(helper,permission,command,command['dispatched'])['allowed']
    denied=check(helper,permission,command,[1.,2.,3.,4.,5.,50.])
    assert not denied['allowed'] and denied['reason']=='GRIPPER_DIRECTION_UNREGISTERED_FREEZE_ALL6'
    assert not helper.status(permission)['capability_ready']


def test_arm_requires_actual_original_returned_write_not_ack_measurement_or_foreign_epoch():
    helper,permission,command,tick,profile,bound=rig()
    for source in ({'accepted':True},{'measured_native6':[0.]*6},{**command,'control_epoch':'foreign'},
                   {**command,'monotonic_ns':tick[0]+1},{**command,'clock_domain':'nx:other'}):
        with pytest.raises(ValueError):arm(helper,permission,source)
    assert not helper.status(permission)['armed']
    arm(helper,permission,command)
    with pytest.raises(ValueError,match='already armed'):arm(helper,permission,command)


def test_global_armed_latch_denies_revoked_new_unowned_epochs_and_never_timer_unarms():
    helper,permission,command,tick,profile,bound=rig();arm(helper,permission,command)
    revoked={**permission,'execution_permission':False}
    assert not check(helper,revoked,command,command['dispatched'])['allowed']
    unknown=helper.check(permission=None,control_epoch='unowned-legacy-new-epoch',mode='policy',
        target=command['dispatched'],last_command=command)
    assert not unknown['allowed'] and unknown['armed']
    tick[0]+=3_000_000_000
    assert not helper.status(permission)['binding_current'] and helper.status()['armed']
    with pytest.raises(ValueError):helper.bind(binding=permission['binding'],permission=permission,direction_profile=profile,expected_revision=helper.revision)


def test_finish_requires_actual_giver_opening_then_new_oneuse_independent_proof_with_separate_clocks():
    helper,permission,command,tick,profile,bound=rig();arm(helper,permission,command)
    status=helper.status(permission)
    with pytest.raises(ValueError,match='Actual correlated giver opening'):
        helper.finish_challenge(interlock_id=status['interlock_id'],expected_revision=status['revision'],permission=permission,
            giver_release_receipt={**giver_release(),'opening':False})
    pending=challenge(helper,permission);proof=confirmation(pending)
    for replacement in ({'state':'confirmed'},{'after_command_id':'old-opening'},{'clock_domain':'pi:boot'},{'created_ns':999}):
        bad=deepcopy(proof);bad['operator_confirmation'].update(replacement)
        with pytest.raises(ValueError,match='Fresh exact independent'):finish(helper,permission,pending,bad)
    result=finish(helper,permission,pending,proof)
    assert result['phase']=='UNFROZEN_TO_FINISH' and not result['armed'] and result['native_commands_sent']==0
    assert not result['hold_contact_attainment_or_physical_success_claimed']
    with pytest.raises(ValueError):finish(helper,permission,pending,proof)
    assert helper.check(permission=None,control_epoch='next-user',mode='policy',target=[0.]*6,last_command=None)['allowed']


def test_finish_timeout_keeps_armed_and_old_challenge_never_releases_new_revision():
    helper,permission,command,tick,profile,bound=rig();arm(helper,permission,command);pending=challenge(helper,permission)
    tick[0]=pending['expires_native_ns'];permission['observation_deadline_ns']=tick[0]+2_000_000_000
    with pytest.raises(ValueError,match='Fresh exact independent'):finish(helper,permission,pending,confirmation(pending))
    assert helper.status(permission)['armed']
    fresh=challenge(helper,permission)
    with pytest.raises(ValueError,match='ID/revision'):finish(helper,permission,pending,confirmation(pending))
    assert finish(helper,permission,fresh,confirmation(fresh))['phase']=='UNFROZEN_TO_FINISH'


def test_restored_armed_state_blocks_new_boot_and_only_exact_current_user_idle_reset_clears_it():
    helper,permission,command,tick,profile,bound=rig();armed=arm(helper,permission,command);state=helper.snapshot_state()
    restored=ReceiverHoldInterlock(clock_domain='pi:new-boot',boot_id='new-boot',clock=lambda:tick[0],state=state)
    assert restored.status()['armed'] and not restored.status()['binding_current']
    assert not restored.check(permission=None,control_epoch='new',mode='teleoperation',target=[0.]*6,last_command=None)['allowed']
    new=deepcopy(permission);new.update(grant_id='new-user-grant',control_epoch='new-native-epoch',native_mode='idle',
        provider_process='new-provider',clock_domain='pi:new-boot',boot_id='new-boot')
    new['binding']['cooperative_session_id']='fresh-user-intent'
    for extra in ({'object_safe_no_dual_hold':False},{'actor_id':'stale-owner'},{'native_mode':'policy'},{'expected_revision':armed['revision']-1}):
        body={'interlock_id':armed['interlock_id'],'expected_revision':armed['revision'],'permission':new,
            'object_safe_no_dual_hold':True,'actor_id':'owner','native_mode':'idle',**extra}
        with pytest.raises(ValueError):restored.reset_safe(**body)
    reset=restored.reset_safe(interlock_id=armed['interlock_id'],expected_revision=armed['revision'],permission=new,
        object_safe_no_dual_hold=True,actor_id='owner',native_mode='idle')
    assert not reset['armed'] and reset['phase']=='UNBOUND'
    next_bound=restored.bind(binding=new['binding'],permission=new,direction_profile=profile,expected_revision=reset['revision'])
    with pytest.raises(ValueError,match='ID/revision'):restored.reset_safe(interlock_id=armed['interlock_id'],expected_revision=armed['revision'],
        permission=new,object_safe_no_dual_hold=True,actor_id='owner',native_mode='idle')
    assert restored.status(new)['interlock_id']==next_bound['interlock_id']
    corrupt=deepcopy(state);corrupt['armed']=False
    with pytest.raises(ValueError,match='retained receiver interlock state'):ReceiverHoldInterlock(clock_domain='pi:boot',boot_id='boot',state=corrupt)
