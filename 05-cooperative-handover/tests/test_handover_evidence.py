import pytest

from explorer_shared.handover import HandoverFSM, HoldEvidence
from explorer.transfer import run_handover_protocol_tests


def evidence(statement, at=1_000_000_000, kind='operator_confirmation', actor='operator', independent=False, received_clock='host'):
    return HoldEvidence(statement + str(at), 'object', actor, kind, statement, independent,
                        17, 'receiver-local-clock', at, received_clock)


def approach(fsm, at=1_000_000_000):
    fsm.apply('giver_holding', event_id='g', now_ns=at, evidence=evidence('giver_holds_object', at))
    fsm.apply('offer_ready', event_id='o', now_ns=at + 1, zone_registered=True, giver_stationary=True)
    fsm.apply('receiver_ready', event_id='r', now_ns=at + 2, receiver_ready=True)
    fsm.apply('receiver_at_object', event_id='a', now_ns=at + 3, both_stationary=True)


def test_neither_close_command_nor_timer_authorizes_release():
    fsm = HandoverFSM('object', clock_domain='host')
    approach(fsm)
    fsm.apply('receiver_closed', event_id='close', now_ns=1_000_000_004)
    assert not fsm.release_permitted(1_000_000_005)
    with pytest.raises(ValueError):
        fsm.apply('release_requested', event_id='release', now_ns=1_000_000_005, both_stationary=True)
    fsm.apply('tick', event_id='timer', now_ns=40_000_000_000)
    assert fsm.state == 'SAFE_STOP'


@pytest.mark.parametrize('kind,actor,independent', [('joint_target', 'receiver', True), ('vlm_text', 'receiver', True), ('force_sensor', 'giver', True), ('virtual_test_oracle', 'test', True)])
def test_nonholding_evidence_never_accepts_physical_release(kind, actor, independent):
    fsm = HandoverFSM('object', clock_domain='host'); approach(fsm)
    with pytest.raises(ValueError):
        fsm.apply('receiver_hold_confirmed', event_id='bad', now_ns=1_000_000_004,
            evidence=evidence('receiver_holds_object', 1_000_000_004, kind, actor, independent))


def test_operator_confirmation_is_supported_but_expires():
    fsm = HandoverFSM('object', clock_domain='host', hold_ttl_s=.5); approach(fsm)
    fsm.apply('receiver_hold_confirmed', event_id='hold', now_ns=1_000_000_004, evidence=evidence('receiver_holds_object', 1_000_000_004))
    assert fsm.release_permitted(1_100_000_000)
    assert not fsm.release_permitted(2_000_000_000)
    fsm.apply('tick', event_id='expired', now_ns=2_000_000_000)
    assert fsm.state == 'SAFE_STOP'


def test_boolean_string_cannot_claim_stationary_or_holding():
    fsm = HandoverFSM('object', clock_domain='host'); approach(fsm)
    fsm.apply('receiver_hold_confirmed', event_id='hold', now_ns=1_000_000_004, evidence=evidence('receiver_holds_object', 1_000_000_004))
    with pytest.raises(ValueError):
        fsm.apply('release_requested', event_id='false-string', now_ns=1_000_000_005, both_stationary='false')


def test_receipt_clock_must_be_coordinator_clock():
    fsm = HandoverFSM('object', clock_domain='host')
    with pytest.raises(ValueError):
        fsm.apply('giver_holding', event_id='bad-clock', now_ns=1_000_000_000,
            evidence=evidence('giver_holds_object', received_clock='other-monotonic-domain'))


def test_motion_during_dual_hold_stops_without_release():
    fsm = HandoverFSM('object', clock_domain='host'); approach(fsm)
    fsm.apply('motion_observed', event_id='moving', now_ns=1_000_000_004, both_holding=True, relative_speed_m_s=.02)
    assert fsm.state == 'SAFE_STOP'
    assert not fsm.release_permitted(1_000_000_005)


def test_events_idempotent_conflicts_rejected():
    fsm = HandoverFSM('object', clock_domain='host')
    ev = evidence('giver_holds_object')
    fsm.apply('giver_holding', event_id='same', now_ns=1_000_000_000, evidence=ev)
    fsm.apply('giver_holding', event_id='same', now_ns=1_000_000_000, evidence=ev)
    assert len(fsm.trace) == 1
    with pytest.raises(ValueError):
        fsm.apply('stop', event_id='same', now_ns=1_000_000_001)


def test_full_protocol_tests_use_no_hardware():
    result = run_handover_protocol_tests()
    assert result['passed']
    assert len(result['cases']) == 4
    assert result['physical_execution'] is False
