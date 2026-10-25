"""method_id: HANDOVER_VERIFIED_FSM
ENGINEERING_GLUE: evidence-gated handover protocol, with no actuator IO."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import time


STATES = ('PREPARE', 'ROSMASTER_HOLDING', 'OFFER', 'SO101_APPROACH', 'RECEIVER_GRASP_CHECK',
          'RELEASE_AUTHORIZED', 'RELEASE', 'CONFIRM_TRANSFER', 'SO101_FINISH', 'FAULT', 'SAFE_STOP')


@dataclass(frozen=True)
class HoldEvidence:
    evidence_id: str
    object_id: str
    actor: str
    kind: str
    statement: str
    independent: bool
    acquired_ns: int
    source_clock_domain: str
    received_ns: int
    received_clock_domain: str
    value: bool = True


class HandoverFSM:
    def __init__(self, object_id, *, mode='physical', clock_domain='coordinator', hold_ttl_s=2.0, phase_timeout_s=30.0):
        if mode not in ('physical', 'virtual') or hold_ttl_s <= 0 or phase_timeout_s <= 0:
            raise ValueError('Invalid handover mode or timeout')
        self.object_id, self.mode, self.clock_domain = object_id, mode, clock_domain
        self.hold_ttl_ns, self.phase_timeout_ns = int(hold_ttl_s * 1e9), int(phase_timeout_s * 1e9)
        self.state, self.completed, self.hold = 'PREPARE', False, None
        self.trace, self.seen = [], {}
        self.last_ns, self.phase_started_ns = None, None

    def _valid_hold(self, evidence, now, statement):
        if not isinstance(evidence, HoldEvidence):
            return False
        local_age = now - evidence.received_ns
        expected_actor = 'giver' if statement == 'giver_holds_object' else 'receiver'
        trusted_kind = (evidence.kind == 'operator_confirmation' and evidence.actor == 'operator') or (
            evidence.kind in ('force_sensor', 'tactile_sensor') and evidence.independent is True and evidence.actor == expected_actor) or (
            self.mode == 'virtual' and evidence.kind == 'virtual_test_oracle' and evidence.independent is True)
        return bool(evidence.object_id == self.object_id and evidence.value is True and trusted_kind
            and evidence.statement == statement and evidence.received_clock_domain == self.clock_domain
            and evidence.source_clock_domain and evidence.acquired_ns >= 0 and 0 <= local_age <= self.hold_ttl_ns)

    def release_permitted(self, now_ns=None):
        now = time.monotonic_ns() if now_ns is None else int(now_ns)
        return self.state == 'RELEASE_AUTHORIZED' and self._valid_hold(self.hold, now, 'receiver_holds_object')

    def apply(self, event, *, event_id, now_ns=None, evidence=None, **facts):
        now = time.monotonic_ns() if now_ns is None else int(now_ns)
        if not event_id:
            raise ValueError('Event ID is required')
        body = dict(event=event, evidence=asdict(evidence) if evidence else None, facts=facts)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if event_id in self.seen:
            if self.seen[event_id] != fingerprint:
                raise ValueError('Conflicting handover event identity')
            return self.state
        if self.last_ns is not None and now < self.last_ns:
            raise ValueError('Coordinator monotonic clock moved backwards')
        before, reason = self.state, event
        if event in ('stop', 'connection_lost'):
            self.state, self.hold = 'SAFE_STOP', None
        elif event == 'fault':
            self.state, self.hold = 'FAULT', None
        elif event == 'reset':
            if self.state not in ('SAFE_STOP', 'FAULT', 'SO101_FINISH'):
                raise ValueError('Reset requires stopped or finished protocol')
            self.state, self.hold, self.completed = 'PREPARE', None, False
        elif self.state in ('SAFE_STOP', 'FAULT'):
            raise ValueError('An explicit reset is required; old actions cannot resume')
        elif event == 'tick':
            if self.phase_started_ns is not None and now - self.phase_started_ns > self.phase_timeout_ns:
                self.state, self.hold, reason = 'SAFE_STOP', None, 'phase_timeout_no_automatic_release'
            elif self.state == 'RELEASE_AUTHORIZED' and not self.release_permitted(now):
                self.state, self.hold, reason = 'SAFE_STOP', None, 'hold_evidence_expired'
        elif event == 'motion_observed':
            speed = float(facts.get('relative_speed_m_s', 0))
            if facts.get('both_holding') is True and (not math.isfinite(speed) or abs(speed) > 0.01):
                self.state, self.hold, reason = 'SAFE_STOP', None, 'relative_motion_during_dual_hold'
        elif self.state == 'PREPARE' and event == 'giver_holding':
            if not self._valid_hold(evidence, now, 'giver_holds_object'):
                raise ValueError('Giver holding evidence is required')
            self.state = 'ROSMASTER_HOLDING'
        elif self.state == 'ROSMASTER_HOLDING' and event == 'offer_ready':
            if facts.get('zone_registered') is not True or facts.get('giver_stationary') is not True:
                raise ValueError('Registered zone and stationary giver are required')
            self.state = 'OFFER'
        elif self.state == 'OFFER' and event == 'receiver_ready':
            if facts.get('receiver_ready') is not True:
                raise ValueError('Receiver readiness is required')
            self.state = 'SO101_APPROACH'
        elif self.state == 'SO101_APPROACH' and event == 'receiver_at_object':
            if facts.get('both_stationary') is not True:
                raise ValueError('Both mechanisms must be stationary at grasp check')
            self.state = 'RECEIVER_GRASP_CHECK'
        elif self.state == 'RECEIVER_GRASP_CHECK' and event == 'receiver_closed':
            reason = 'closed_joint_command_is_not_hold_evidence'
        elif self.state == 'RECEIVER_GRASP_CHECK' and event == 'receiver_hold_confirmed':
            if not self._valid_hold(evidence, now, 'receiver_holds_object'):
                raise ValueError('Independent hold measurement or explicit operator confirmation required')
            self.hold, self.state = evidence, 'RELEASE_AUTHORIZED'
        elif self.state == 'RELEASE_AUTHORIZED' and event == 'release_requested':
            if not self.release_permitted(now) or facts.get('both_stationary') is not True:
                raise ValueError('Fresh receiver hold evidence and stationary mechanisms are required')
            self.state = 'RELEASE'
        elif self.state == 'RELEASE' and event == 'giver_released':
            self.state = 'CONFIRM_TRANSFER'
        elif self.state == 'CONFIRM_TRANSFER' and event == 'transfer_confirmed':
            if not self._valid_hold(evidence, now, 'object_transferred_to_receiver'):
                raise ValueError('Independent transfer confirmation required')
            self.state = 'SO101_FINISH'
        elif self.state == 'SO101_FINISH' and event == 'receiver_finished':
            if not self._valid_hold(evidence, now, 'receiver_finished_task'):
                raise ValueError('Receiver finishing evidence required')
            self.completed = True
        else:
            raise ValueError('Illegal handover transition: ' + self.state + ' / ' + event)
        self.seen[event_id], self.last_ns = fingerprint, now
        if self.phase_started_ns is None or before != self.state:
            self.phase_started_ns = now
        self.trace.append(dict(event_id=event_id, coordinator_monotonic_ns=now, clock_domain=self.clock_domain,
                               before=before, after=self.state, reason=reason, **body))
        return self.state
