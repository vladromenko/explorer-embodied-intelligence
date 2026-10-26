"""method_id: ENGINEERING_GLUE / HANDOVER_VERIFIED_FSM.

Bind the existing authenticated Nano arm gateway and explicit human evidence to
handover_runtime. Native HTTP acceptance is never promoted to hardware dispatch,
stationarity or object holding. Default M3 motion admission stays blocked until
a real correlated dispatch source and native release-deadline guard are supplied.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .arm_only_retarget import six_servo_goal
from .handover import HoldEvidence
from .handover_runtime import ArmObservation, CorrelatedEvidence, DispatchReceipt


QUESTIONS = {
    'giver_holds_object': 'Подтвердите, что подающий робот действительно удерживает указанный предмет.',
    'receiver_holds_object': 'Подтвердите удержание предмета принимающим роботом до раскрытия подающего.',
    'object_transferred_to_receiver': 'Подтвердите, что предмет у принимающего, а подающий его больше не удерживает.',
    'receiver_finished_task': 'Подтвердите полное завершающее действие принимающего робота с предметом.',
    'giver_stationary': 'Подтвердите по текущему наблюдению неподвижность подающей руки.',
    'receiver_stationary': 'Подтвердите по текущему наблюдению неподвижность принимающей руки.',
}
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z')


def _identity(value):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError('A bounded explicit run/object/command identity is required')
    return value


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stream.flush();os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


class BindingBlocked(RuntimeError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(code+': '+detail)


class OperatorEvidenceInbox:
    """Durable single-use challenges, completed only by a root-authenticated user.

    Root HTTP handlers call pending() and confirm(..., authenticated_actor=...).
    Actor identity must come from the authenticated session, never a body field.
    A new process session cannot consume old pending/confirmed challenges. Server
    receipt time is recorded explicitly; it is not a camera exposure timestamp.
    """
    def __init__(self, folder, *, clock=time.monotonic_ns, clock_domain='handover-coordinator', session_id=None):
        self.folder = Path(folder);self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if session_id is not None and (not isinstance(session_id,str) or not re.fullmatch('[0-9a-f]{32}',session_id)):
            raise ValueError('Attach requires the trusted current worker attempt session ID')
        self.clock, self.clock_domain, self.session_id = clock, clock_domain, session_id or uuid.uuid4().hex
        self.condition = threading.Condition(threading.RLock())
        (self.folder/'cancelled-runs').mkdir(exist_ok=True,mode=0o700)

    @contextmanager
    def _disk_guard(self):
        with (self.folder/'.inbox.lock').open('a') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

    def _row(self, challenge_id):
        if not isinstance(challenge_id,str) or not re.fullmatch('[0-9a-f]{32}',challenge_id):
            raise ValueError('A bounded server-issued challenge ID is required')
        path=self.folder/(challenge_id+'.json')
        if not path.is_file():raise ValueError('Unknown challenge')
        row=json.loads(path.read_text())
        if row.get('challenge_id')!=challenge_id or row.get('clock_domain')!=self.clock_domain:
            raise ValueError('Challenge identity or host clock differs')
        return row

    def _rows(self):
        rows=[]
        for path in self.folder.glob('*.json'):
            row=json.loads(path.read_text())
            if row.get('session_id')==self.session_id:
                rows.append(self._row(path.stem))
        return rows

    def _cancel_path(self,run_id):
        key=hashlib.sha256((self.session_id+'\0'+run_id).encode()).hexdigest()
        return self.folder/'cancelled-runs'/(key+'.json')

    def _cancelled(self,run_id):
        path=self._cancel_path(run_id)
        if not path.is_file():return False
        row=json.loads(path.read_text())
        if row.get('session_id')!=self.session_id or row.get('run_id')!=run_id or row.get('clock_domain')!=self.clock_domain:
            raise ValueError('Cancellation identity or host clock differs')
        return True

    def pending(self, run_id=None):
        with self.condition:
            with self._disk_guard():
                now = self.clock()
                return [dict(row) for row in self._rows() if row['state']=='pending' and row['session_id']==self.session_id
                    and now < row['deadline_ns'] and not self._cancelled(row['run_id']) and (run_id is None or row['run_id']==run_id)]

    def confirm(self, challenge_id, body, *, authenticated_actor):
        _identity(authenticated_actor)
        if not isinstance(body, dict) or set(body)-{'affirmed','observing','note'}:
            raise ValueError('Confirmation accepts only affirmative observation and an optional note')
        if body.get('affirmed') is not True or body.get('observing') is not True:
            raise ValueError('An explicit affirmative current observation is required')
        with self.condition:
            with self._disk_guard():
                row = self._row(challenge_id)
                now = self.clock()
                if row['session_id'] != self.session_id or row['state'] != 'pending' or now >= row['deadline_ns'] or self._cancelled(row['run_id']):
                    raise ValueError('Challenge is expired, consumed or belongs to another process session')
                result = dict(row, state='confirmed', authenticated_actor=authenticated_actor,
                    received_ns=now, note=str(body.get('note',''))[:1000],
                    acquired_time_basis='operator_confirmation_received_at_server_not_camera_exposure',
                    source_clock_domain=self.clock_domain, observing=True, affirmed=True)
                _write(self.folder/(challenge_id+'.json'), result)
            self.condition.notify_all()
            return dict(result)

    def cancel_run(self, run_id):
        _identity(run_id)
        with self.condition:
            with self._disk_guard():
                _write(self._cancel_path(run_id),dict(session_id=self.session_id,run_id=run_id,
                    clock_domain=self.clock_domain,cancelled_ns=self.clock()))
                for row in self._rows():
                    if row['run_id']==run_id and row['session_id']==self.session_id and row['state'] in ('pending','confirmed'):
                        cancelled=dict(row,state='cancelled',cancelled_ns=self.clock())
                        _write(self.folder/(row['challenge_id']+'.json'),cancelled)
            self.condition.notify_all()

    def _wait(self, statement, run_id, object_id, role, after_command_id, timeout_s):
        for value in (run_id, object_id):_identity(value)
        if after_command_id is not None:_identity(after_command_id)
        if statement not in QUESTIONS or role not in ('giver','receiver') or not isinstance(timeout_s,(int,float)) or isinstance(timeout_s,bool) or not 0 < timeout_s <= 300:
            raise ValueError('An explicit supported statement/role and finite timeout are required')
        if statement.startswith('giver_') != (role=='giver'):
            raise ValueError('Confirmation statement differs from the named robot role')
        challenge_id=uuid.uuid4().hex
        with self.condition:
            row=dict(challenge_id=challenge_id,session_id=self.session_id,run_id=run_id,object_id=object_id,
                role=role,after_command_id=after_command_id,statement=statement,question=QUESTIONS[statement],
                created_ns=self.clock(),deadline_ns=self.clock()+int(timeout_s*1e9),clock_domain=self.clock_domain,state='pending')
            with self._disk_guard():
                if self._cancelled(run_id):raise RuntimeError('Handover attempt was cancelled; a new attempt session is required')
                _write(self.folder/(challenge_id+'.json'),row)
            while True:
                with self._disk_guard():
                    row=self._row(challenge_id)
                    if self._cancelled(run_id) or row['state']=='cancelled':
                        raise RuntimeError('Handover confirmation was cancelled; no old evidence is reusable')
                    remaining=(row['deadline_ns']-self.clock())/1e9
                    if remaining <= 0:
                        expired=dict(row,state='expired',expired_ns=self.clock())
                        _write(self.folder/(challenge_id+'.json'),expired)
                        raise TimeoutError('Operator confirmation expired before consumption; no release authorized')
                    if row['state']=='confirmed':
                        consumed=dict(row,state='consumed',consumed_ns=self.clock())
                        _write(self.folder/(challenge_id+'.json'),consumed)
                        return consumed
                self.condition.wait(min(remaining,.1))

    def __call__(self, statement, run_id, object_id, role, after_command_id, timeout_s):
        if statement.endswith('_stationary'):
            raise ValueError('Stationary confirmation uses stationary_observation, not hold evidence')
        row=self._wait(statement,run_id,object_id,role,after_command_id,timeout_s)
        evidence=HoldEvidence(row['challenge_id'],object_id,'operator','operator_confirmation',statement,
            False,row['received_ns'],self.clock_domain,row['received_ns'],self.clock_domain,True)
        return CorrelatedEvidence(run_id,object_id,role,after_command_id,evidence,
            {'kind':'authenticated_operator_confirmation','confirmation':row})

    def stationary_observation(self, run_id, object_id, role, after_command_id, timeout_s):
        row=self._wait(role+'_stationary',run_id,object_id,role,after_command_id,timeout_s)
        return ArmObservation(role,True,row['received_ns'],self.clock_domain,
            {'kind':'explicit_authenticated_operator_stationary_confirmation','confirmation':row,
             'measured_joint_feedback_claimed':False},after_command_id)


@dataclass(frozen=True)
class NativeDispatchProof:
    """A trusted native-send receipt, never produced from a plan/start ACK."""
    run_id: str
    object_id: str
    command_id: str
    plan_id: str
    target_session: str
    goal_deg: tuple
    source_monotonic_ns: int
    source_clock_domain: str
    received_ns: int
    received_clock_domain: str
    source_receipt: dict


class ROSMASTERArmTransport:
    """Actual existing NanoArmHTTPClient binding, blocked without native evidence.

    dispatch_proof(context, timeout_s) is root-owned and must read an actual
    native-send receipt. release_submit(client, body, authorization) must enforce
    its permit in the authoritative Nano dispatcher, not just before an HTTP
    call on NX. Neither capability exists in the currently audited old API, so
    both default to absent and no physical action is submitted by default.
    """
    def __init__(self, client, confirmations, run_id, object_id, ownership, authority, *,
                 dispatch_proof=None, release_submit=None, clock=time.monotonic_ns,
                 clock_domain='handover-coordinator', evidence_timeout_s=30):
        self.client,self.confirmations=client,confirmations
        self.run_id,self.object_id=_identity(run_id),_identity(object_id)
        self.ownership,self.authority=ownership,authority
        self.dispatch_proof,self.release_submit=dispatch_proof,release_submit
        self.clock,self.clock_domain,self.timeout=clock,clock_domain,evidence_timeout_s
        self.role,self.owner_id='giver','handover:'+run_id
        self.epoch,self.sequence,self.last_command=None,-1,None
        self.last_receipt,self.last_goal=None,None

    def capability(self):
        blockers=[]
        if self.dispatch_proof is None:blockers.append('BLOCKED_NATIVE_DISPATCH')
        if self.release_submit is None:blockers.append('BLOCKED_RELEASE_DEADLINE_AT_NATIVE_BOUNDARY')
        return dict(available=not blockers,blockers=blockers,physical_validation=False,
            original_operator_held_lease_unchanged=True,chassis_requests=0,
            native_ack_is_dispatch=False,busy_false_is_stationary=False)

    def _admit(self):
        owner=self.ownership()
        if owner.get('run_id') != self.run_id or owner.get('owner_id') != self.owner_id or owner.get('resource') != 'm3-arm' or owner.get('valid') is not True:
            raise BindingBlocked('BLOCKED_ARM_OWNERSHIP','A current root-owned M3 arm grant for this run is required')
        authority=self.authority()
        if not (authority.get('pi_arms_visible') is True or authority.get('operator_near') is True):
            raise BindingBlocked('BLOCKED_CURRENT_OBSERVATION','Both arms must be currently visible or the operator must be nearby')
        return owner

    def submit(self, command, run_id, object_id, authorization):
        if run_id!=self.run_id or object_id!=self.object_id or command.role!='giver' or command.operation not in ('offer','release'):
            raise ValueError('ROSMASTER giver accepts only the named run/object offer and release')
        if set(command.payload) != {'goal_deg'}:
            raise ValueError('M3 payload must contain only the six original servo endpoint degrees')
        goal=six_servo_goal(command.payload['goal_deg'])
        owner=self._admit()
        if self.dispatch_proof is None:
            raise BindingBlocked('BLOCKED_NATIVE_DISPATCH','Original plan/start ACK has no correlated native-send proof')
        if command.operation=='release':
            if self.release_submit is None:
                raise BindingBlocked('BLOCKED_RELEASE_DEADLINE_AT_NATIVE_BOUNDARY','No existing authoritative endpoint enforces this release permit at dispatch')
            if self.last_goal is None or goal[:5]!=self.last_goal[:5]:
                raise ValueError('Giver release may change only gripper; the previous dispatched arm reference must remain unchanged')
            if (not isinstance(authorization,dict) or authorization.get('run_id')!=run_id or authorization.get('object_id')!=object_id
                    or authorization.get('clock_domain')!=self.clock_domain or not authorization.get('evidence_id')
                    or type(authorization.get('valid_until_ns')) is not int or self.clock()>authorization['valid_until_ns']):
                raise ValueError('A current correlated receiver-hold permit is required')
        if self.epoch is None:
            remote=self.client.status()
            registration=remote.get('registration') or {}
            if registration.get('state')!='REGISTERED_DECLARATION':
                raise BindingBlocked('BLOCKED_MEASURED_REGISTRATION','Nano arm gateway has no matching measured registration declaration')
            if self._admit()!=owner:
                raise BindingBlocked('BLOCKED_ARM_OWNERSHIP','Ownership changed before native gateway start')
            anchored=self.client.start(dict(expected_control_epoch=remote['control_epoch'],owner_id=self.owner_id,
                registration_id=registration['registration_id'],registration_sha256=registration['registration_sha256'],observing=True))
            if anchored.get('state')!='ACTIVE':
                raise BindingBlocked('BLOCKED_NATIVE_GATEWAY',str(anchored.get('state','unknown')))
            self.epoch=anchored['control_epoch']
        if self._admit()!=owner:
            raise BindingBlocked('BLOCKED_ARM_OWNERSHIP','Ownership changed before arm action')
        self.sequence+=1
        body=dict(control_epoch=self.epoch,owner_id=self.owner_id,sequence=self.sequence,goal=goal,
            source_measurement={'basis':'handover_command_context_not_measured_state','run_id':run_id,'object_id':object_id,'command_id':command.command_id})
        sent_at=self.clock()
        receipt=self.release_submit(self.client,body,authorization) if command.operation=='release' else self.client.action(body)
        self.last_receipt=receipt
        if receipt.get('state')!='API_ACCEPTED' or receipt.get('accepted')!=goal or not receipt.get('target_session'):
            raise BindingBlocked('BLOCKED_NATIVE_DISPATCH','Native gateway did not acknowledge the same endpoint/session; command is not retried')
        if command.operation=='release' and receipt.get('authorization_checked_at_native_dispatch') is not True:
            raise BindingBlocked('BLOCKED_RELEASE_DEADLINE_AT_NATIVE_BOUNDARY','Release dispatcher supplied no native permit-check receipt')
        context=dict(run_id=run_id,object_id=object_id,command_id=command.command_id,goal_deg=goal,
            plan_id=(receipt.get('original_plan') or {}).get('plan_id'),target_session=receipt['target_session'])
        proof=self.dispatch_proof(context,self.timeout)
        if (not isinstance(proof,NativeDispatchProof) or asdict(proof).get('run_id')!=run_id or proof.object_id!=object_id
                or proof.command_id!=command.command_id or proof.plan_id!=context['plan_id'] or proof.target_session!=context['target_session']
                or list(proof.goal_deg)!=goal or proof.received_clock_domain!=self.clock_domain
                or not proof.source_clock_domain or type(proof.source_monotonic_ns) is not int or proof.source_monotonic_ns<0
                or not sent_at<=proof.received_ns<=self.clock() or not proof.source_receipt
                or proof.source_receipt.get('basis')!='native_robotio_send_receipt'):
            raise BindingBlocked('BLOCKED_NATIVE_DISPATCH','No fresh native-send receipt correlated to this exact run/plan/session/endpoint')
        self.last_command=command.command_id
        self.last_goal=list(goal)
        return DispatchReceipt(command.command_id,run_id,object_id,'giver',True,proof.received_ns,self.clock_domain,
            {'native_dispatch_proof':asdict(proof),'gateway_api_receipt':receipt,'hardware_reach_or_hold_claimed':False})

    def observe(self):
        self._admit()
        return self.confirmations.stationary_observation(self.run_id,self.object_id,'giver',self.last_command,self.timeout)

    def held(self, *, held, observing):
        """Only a current explicit user event calls this; no automatic renewal."""
        if self.epoch is None or held is not True or observing is not True:
            raise ValueError('A current active explicit observed held event is required')
        self._admit()
        return self.client.heartbeat(dict(control_epoch=self.epoch,owner_id=self.owner_id,held=True))

    def stop(self, run_id):
        if run_id!=self.run_id:raise ValueError('Cannot stop a different handover run')
        self.confirmations.cancel_run(run_id)
        if self.epoch is None:
            return dict(motion_requested=False,physical_stop_confirmed=False)
        epoch,self.epoch=self.epoch,None
        result=self.client.stop(dict(control_epoch=epoch,owner_id=self.owner_id))
        return dict(target_receipt=result,physical_stop_confirmed=False)


def inspect_native_observability(request):
    """GET-only source qualification. It never executes or changes the controller."""
    feedback=request('GET','/legacy/arm/feedback',None)
    manual=request('GET','/legacy/arm/manual',None)
    measured=feedback.get('measured') or {}
    return dict(scope='read_only_existing_native_API_probe',measured_joint_feedback=measured.get('available') is True,
        measured_feedback_reason=measured.get('reason'),force_measured=feedback.get('force_measured') is True,
        manual_estimated_only=manual.get('estimated_only'),native_dispatch_qualified=False,
        stationary_qualified=False,readback_is_not_hold_evidence=True,
        blockers=['BLOCKED_NATIVE_DISPATCH','BLOCKED_MEASURED_STATIONARITY_OR_OPERATOR_CONFIRMATION'],
        source_response_sha256={name:hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
            for name,value in (('arm_feedback',feedback),('arm_manual',manual))},controller_setting_writes=0,chassis_requests=0)
