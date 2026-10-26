"""method_id: ENGINEERING_GLUE / HANDOVER_VERIFIED_FSM: runtime orchestration, not an oracle.

Transports execute existing arm-only operations. Evidence and state readings are
injected and correlated to this run/object/command. No hardware driver imports,
controller settings, chassis operations, or automatic post-crash replay.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import fcntl
import json
import os
from pathlib import Path
import time
from typing import Callable

from .handover import HandoverFSM, HoldEvidence


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ArmCommand:
    command_id: str
    role: str
    operation: str
    payload: dict

    def __post_init__(self):
        if not self.command_id or self.role not in ('giver', 'receiver') or self.operation not in ('offer', 'approach', 'close', 'release', 'finish'):
            raise ValueError('Named run-local arm command required')
        _digest(self.payload)


@dataclass(frozen=True)
class DispatchReceipt:
    command_id: str
    run_id: str
    object_id: str
    role: str
    dispatched: bool
    received_ns: int
    clock_domain: str
    source: dict


@dataclass(frozen=True)
class ArmObservation:
    role: str
    stationary: bool
    received_ns: int
    clock_domain: str
    source_receipt: dict
    after_command_id: str | None = None


@dataclass(frozen=True)
class CorrelatedEvidence:
    run_id: str
    object_id: str
    role: str
    after_command_id: str | None
    evidence: HoldEvidence
    source_receipt: dict


@dataclass(frozen=True)
class HandoverPlan:
    run_id: str
    object_id: str
    zone_registration_id: str
    commands: tuple[ArmCommand, ...]

    def __post_init__(self):
        expected = (('giver', 'offer'), ('receiver', 'approach'), ('receiver', 'close'), ('giver', 'release'), ('receiver', 'finish'))
        if not self.run_id or not self.object_id or not self.zone_registration_id or tuple((c.role, c.operation) for c in self.commands) != expected:
            raise ValueError('A registered zone and complete giver/receiver arm-only sequence are required')
        if len({c.command_id for c in self.commands}) != len(self.commands):
            raise ValueError('Distinct command identities are required')


class CallbackArmTransport:
    """Bind root-owned existing adapters; accepted-only is not a dispatch receipt."""
    def __init__(self, role, submit: Callable, observe: Callable, stop: Callable):
        self.role, self._submit, self._observe, self._stop = role, submit, observe, stop

    def submit(self, command, run_id, object_id, authorization):
        return self._submit(command, run_id, object_id, authorization)

    def observe(self):
        return self._observe()

    def stop(self, run_id):
        return self._stop(run_id)


class SO101StationTransport(CallbackArmTransport):
    """Existing /station/action API; wait for the servo loop's actual receipt.

    Policy/start and its interactive control epoch remain root-owned. request is
    the existing authenticated client's callable(method,path,json_or_none).
    observation translates real measured samples into stationary observations;
    a successful POST is never used as object holding or stationary evidence.
    """
    def __init__(self, request, control_epoch, source_frame, observation, *, role='receiver',
                 operator_present=False, clock=time.monotonic_ns, sleep=time.sleep, dispatch_timeout_s=1):
        self.request, self.epoch, self.frame = request, control_epoch, source_frame
        self.clock, self.sleep, self.timeout = clock, sleep, dispatch_timeout_s
        self.sequence, self.operator_present = -1, operator_present
        super().__init__(role, self._dispatch, observation, self._stop_station)

    def _dispatch(self, command, run_id, object_id, authorization):
        from .so101_station import NAMES, UNITS
        if command.role != self.role or not self.epoch:
            raise ValueError('Current station role/control epoch is required')
        source = self.frame()
        self.sequence += 1
        body = dict(command.payload, observing=True, operator_present=self.operator_present,
            control_epoch=self.epoch, sequence=self.sequence, action_names=list(NAMES), action_units=UNITS,
            source_frame_monotonic_ns=source['monotonic_ns'], source_clock_domain=source['clock_domain'], source_frame_id=source['frame_id'])
        # The coordinator revalidates release immediately before this call. A
        # transport must honor this deadline at submission, never queue a retry.
        if authorization and self.clock() > authorization['valid_until_ns']:
            raise ValueError('Receiver hold authorization expired before transport submission')
        self.request('POST', '/station/action', body)
        for _ in range(max(1, int(self.timeout/.01))):
            status = self.request('GET', '/station/status', None)
            if status.get('control_epoch') != self.epoch:
                raise RuntimeError('Station session ended or was taken over')
            receipt = status.get('last_command') or {}
            if receipt.get('control_epoch') == self.epoch and receipt.get('sequence') == self.sequence:
                if receipt.get('source_observation', {}).get('frame_id') != source['frame_id'] or receipt.get('requested') != body.get('action'):
                    raise RuntimeError('Station dispatch came from a different source observation')
                return DispatchReceipt(command.command_id, run_id, object_id, self.role, True, self.clock(),
                    'handover-coordinator', {'station_receipt': receipt, 'source_observation': source, 'submitted_body_sha256': _digest(body)})
            self.sleep(.01)
        raise TimeoutError('Accepted station command has no matching dispatched receipt; not retried')

    def _stop_station(self, run_id):
        return self.request('POST', '/station/stop', {'expected_control_epoch': self.epoch})


class _Journal:
    def __init__(self, path):
        self.path = path

    def append(self, event):
        with self.path.open('a') as stream:
            stream.write(json.dumps(event, sort_keys=True, allow_nan=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())


def _run_handover_locked(plan, transports, evidence_provider, folder, *, mode, authority,
                 clock=time.monotonic_ns, clock_domain='handover-coordinator', hold_ttl_s=2.0,
                 phase_timeout_s=30.0, cancelled=lambda: False):
    """Execute a finite sequence via adapters. Never synthesize hold evidence.

    evidence_provider(statement, run_id, object_id, role, after_command_id,
    timeout_s) must wait for a matching independent/operator event and return a
    CorrelatedEvidence. Its timeout never authorizes release. Physical source
    authentication and current stationary measurements belong to root adapters.
    """
    folder = Path(folder);folder.mkdir(parents=True, exist_ok=True)
    signature = _digest({'plan': asdict(plan), 'mode': mode, 'clock_domain': clock_domain,
        'hold_ttl_s': hold_ttl_s, 'phase_timeout_s': phase_timeout_s})
    result_path, journal_path = folder/'handover-result.json', folder/'handover-journal.jsonl'
    if result_path.exists():
        final = json.loads(journal_path.read_text().splitlines()[-1]) if journal_path.is_file() else {}
        if final.get('result_sha256') != hashlib.sha256(result_path.read_bytes()).hexdigest():
            raise ValueError('Completed handover result/journal checksum mismatch')
        saved = json.loads(result_path.read_text())
        if saved['signature'] != signature:
            raise ValueError('Run folder already belongs to a different handover')
        return saved
    if journal_path.exists():
        raise ValueError('Interrupted handover requires a new run and renewed observation; no automatic command replay')
    if set(transports) != {'giver', 'receiver'}:
        raise ValueError('Both existing arm transports are required')
    fsm = HandoverFSM(plan.object_id, mode=mode, clock_domain=clock_domain,
        hold_ttl_s=hold_ttl_s, phase_timeout_s=phase_timeout_s)
    journal = _Journal(journal_path)
    started = clock()
    journal.append({'kind': 'start', 'signature': signature, 'plan': asdict(plan), 'monotonic_ns': started})
    receipts, holds, stop_receipts = [], [], []

    def check():
        if cancelled():
            raise RuntimeError('Handover cancelled')
        now = clock()
        fsm.apply('tick', event_id='tick-'+str(now)+'-'+str(len(fsm.trace)), now_ns=now)
        if fsm.state in ('SAFE_STOP', 'FAULT'):
            raise TimeoutError('Protocol stopped; no release authorized')
        current = authority()
        if mode == 'physical' and not (current.get('pi_arms_visible') is True or current.get('operator_near') is True):
            raise ValueError('Arms must be visible on Pi or operator must be near')
        return now

    def observe(role, after=None):
        check()
        observation = transports[role].observe()
        if (not isinstance(observation, ArmObservation) or observation.role != role or not observation.stationary
                or observation.clock_domain != clock_domain or not 0 <= clock()-observation.received_ns <= fsm.hold_ttl_ns
                or not observation.source_receipt or (after and observation.after_command_id != after)):
            raise ValueError('Fresh stationary observation correlated to the current command is required')
        journal.append({'kind': 'observation', **asdict(observation)})
        return observation

    def evidence(statement, role, after=None):
        check()
        envelope = evidence_provider(statement, plan.run_id, plan.object_id, role, after, phase_timeout_s)
        check()
        if (not isinstance(envelope, CorrelatedEvidence) or envelope.run_id != plan.run_id or envelope.object_id != plan.object_id
                or envelope.role != role or envelope.after_command_id != after or not envelope.source_receipt
                or envelope.evidence.received_ns < started or (after and envelope.evidence.received_ns < receipts[-1].received_ns)):
            raise ValueError('Evidence belongs to another run/object/command or predates dispatch')
        if envelope.evidence.evidence_id in {e['evidence']['evidence_id'] for e in holds}:
            raise ValueError('An evidence identity cannot authorize two protocol events')
        holds.append(asdict(envelope));journal.append({'kind': 'evidence', **asdict(envelope)})
        return envelope.evidence

    def send(command, authorization=None):
        check()
        if command.operation == 'release':
            if not fsm.release_permitted(clock()):
                raise ValueError('Giver release requires current confirmed receiver hold')
            fsm.apply('release_requested', event_id='release-request', now_ns=clock(), both_stationary=True)
        intent_ns = clock()
        journal.append({'kind': 'dispatch_intent', 'run_id': plan.run_id, 'object_id': plan.object_id,
            'command': asdict(command), 'authorization': authorization, 'monotonic_ns': clock()})
        command_sha = _digest(asdict(command))
        receipt = transports[command.role].submit(command, plan.run_id, plan.object_id, authorization)
        if _digest(asdict(command)) != command_sha:
            raise RuntimeError('Command changed during transport submission')
        if (not isinstance(receipt, DispatchReceipt) or receipt.command_id != command.command_id or receipt.role != command.role
                or receipt.run_id != plan.run_id or receipt.object_id != plan.object_id or receipt.dispatched is not True
                or receipt.clock_domain != clock_domain or receipt.received_ns < intent_ns or receipt.received_ns > clock()
                or not receipt.source):
            raise RuntimeError('No matching dispatched receipt; sequence is not retried')
        receipts.append(receipt);journal.append({'kind': 'dispatch_receipt', **asdict(receipt)})
        return receipt

    error = None
    try:
        initial_hold = evidence('giver_holds_object', 'giver')
        fsm.apply('giver_holding', event_id='giver-held', now_ns=check(), evidence=initial_hold)
        offer, approach, close, release, finish = plan.commands
        send(offer);observe('giver', offer.command_id)
        fsm.apply('offer_ready', event_id='offered', now_ns=check(), zone_registered=True, giver_stationary=True)
        observe('receiver')
        fsm.apply('receiver_ready', event_id='receiver-ready', now_ns=check(), receiver_ready=True)
        send(approach);observe('receiver', approach.command_id);observe('giver', offer.command_id)
        fsm.apply('receiver_at_object', event_id='at-object', now_ns=check(), both_stationary=True)
        send(close)
        fsm.apply('receiver_closed', event_id='receiver-close-dispatched', now_ns=check())
        hold = evidence('receiver_holds_object', 'receiver', close.command_id)
        fsm.apply('receiver_hold_confirmed', event_id='receiver-held', now_ns=check(), evidence=hold)
        observe('giver', offer.command_id);observe('receiver', close.command_id)
        authorization = {'run_id': plan.run_id, 'object_id': plan.object_id, 'evidence_id': hold.evidence_id,
            'after_command_id': close.command_id, 'clock_domain': clock_domain, 'valid_until_ns': hold.received_ns+fsm.hold_ttl_ns}
        # Validate in RELEASE_AUTHORIZED. send journals the transition/intent;
        # the adapter must also honor the permit at the actual dispatch boundary.
        if not fsm.release_permitted(check()):raise ValueError('Receiver hold expired')
        send(release, authorization)
        fsm.apply('giver_released', event_id='giver-released', now_ns=clock())
        transferred = evidence('object_transferred_to_receiver', 'receiver', release.command_id)
        fsm.apply('transfer_confirmed', event_id='transferred', now_ns=check(), evidence=transferred)
        send(finish);observe('receiver', finish.command_id)
        finished = evidence('receiver_finished_task', 'receiver', finish.command_id)
        fsm.apply('receiver_finished', event_id='finished', now_ns=check(), evidence=finished)
    except Exception as exc:
        error = type(exc).__name__+': '+str(exc)
        fsm.apply('stop', event_id='runtime-stop', now_ns=clock())
    finally:
        # Stop uses existing measured-position hold/stop behavior, never an open
        # gripper command. Preserve failures instead of reporting a clean stop.
        for role in ('giver', 'receiver'):
            try:
                stop_receipts.append({'role': role, 'confirmed': True, 'receipt': transports[role].stop(plan.run_id)})
            except Exception as exc:
                stop_receipts.append({'role': role, 'confirmed': False, 'error': type(exc).__name__+': '+str(exc)})
    result = {'signature': signature, 'run_id': plan.run_id, 'object_id': plan.object_id, 'mode': mode,
        'scope': 'runtime_protocol_with_supplied_execution_and_evidence_adapters', 'protocol_completed': fsm.completed,
        'state': fsm.state, 'error': error, 'elapsed_ns': clock()-started, 'commands_dispatched': [asdict(r) for r in receipts],
        'evidence': holds, 'fsm_trace': fsm.trace, 'stop_receipts': stop_receipts,
        'physical_skill_success_claimed': False, 'outcome': 'unknown', 'chassis_commands': 0,
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'fsm_source_sha256': hashlib.sha256(Path(__file__).with_name('handover.py').read_bytes()).hexdigest(),
        'method_ids': ['HANDOVER_VERIFIED_FSM', 'ENGINEERING_GLUE']}
    temporary = result_path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(result, stream, indent=2, allow_nan=False);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary, result_path)
    journal.append({'kind': 'finished', 'result_sha256': hashlib.sha256(result_path.read_bytes()).hexdigest()})
    return result


def run_handover(plan, transports, evidence_provider, folder, **kwargs):
    """One owner per attempt; a crashed attempt never replays old commands."""
    folder = Path(folder);folder.mkdir(parents=True, exist_ok=True)
    with (folder/'.handover-owner.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('This handover already has an active coordinator') from exc
        return _run_handover_locked(plan, transports, evidence_provider, folder, **kwargs)
