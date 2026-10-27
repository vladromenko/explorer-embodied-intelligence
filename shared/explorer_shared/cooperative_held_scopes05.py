"""ENGINEERING_GLUE: source-owned parked observations, never motion authority.

Getters read existing camera/reference/measured caches only. Source I/O is outside
Station/publication/provider or teleop/publication locks, followed by exact CAS.
Explicit metadata arms retain the actual sealed origin and durable safety latch.
No native start/target, lease, grant or controller setting is written.
"""
from copy import deepcopy
import math

from .contracts import digest

SCHEMA='explorer_cooperative_held_scope_v1'


def _result(role,**fields):
    return dict(schema=SCHEMA,role=role,ready=False,reason='PARKED_SOURCE_CONTEXT_UNAVAILABLE',
        dispatch_permission=False,stationarity_verified=False,object_holding_verified=False,
        physical_attainment_claimed=False,native_commands_sent=0,native_lease_renewed=False,**fields)


def _six(value):
    if not isinstance(value,list) or len(value)!=6 or any(type(v) not in (int,float) or not math.isfinite(v) for v in value):
        raise ValueError('EXACT_FINITE_SOURCE_NATIVE6_REQUIRED')
    return value


class GiverParkedObservationScope:
    def __init__(self,owner):self.owner=owner;self.http_mounted=False;self.arm_http_mounted=False
    def _capture(self):
        owner=self.owner;guard=owner.guard;mux=owner.mux
        # mux.status() invokes chassis_permit/camera and is deliberately absent
        # here. All fields in this capture are original in-memory source state.
        with mux.teleop.lock:
            with guard.lock:
                native={'control_epoch':mux.epoch,'active':mux.active,'state':mux.state,
                    'owner_generation':mux.teleop.generation,'native_generation':mux.generation,
                    'native_owner':mux.teleop.owner,'stop_latched':mux.teleop.stop_latched,
                    'boot_id':mux.tap.boot_id,'clock_domain':mux.tap.clock_domain}
                return {'native':native,'binding':deepcopy(guard.binding),'parked':deepcopy(guard.parked),
                    'revision':guard.revision,'armed':guard.armed,'persistence':guard.journal.status(guard.revision),
                    'reference_fault':deepcopy(guard.reference_fault),'drained':guard.drained(),
                    'authorization_id':owner.activation.current_authorization_id(),
                    'authorization_receipt':owner.activation.receipt((guard.binding or {}).get('authorization_id'))}
    @staticmethod
    def _validate(state):
        persistence=state['persistence'];binding=state['binding'] or {};parked=state['parked'] or {};native=state['native']
        proof=state['authorization_receipt'] or {};origin=parked.get('origin') or {}
        if (not all(persistence.get(key) is True for key in ('installed','healthy','initialized','fail_closed_publication_gate')) or
                persistence.get('pending') is not False or persistence.get('recovery_required') is not False or
                persistence.get('fault') is not None or state['reference_fault'] is not None):raise ValueError('GIVER_DURABLE_REFERENCE_OR_WAL_NOT_HEALTHY')
        if (parked.get('stage') not in ('PARKED_NEW_USER','BEGIN_CLUTCHED') or not parked.get('park_request_id') or
                state['drained'].get('ready') is not True):raise ValueError('CURRENT_DRAINED_NEW_USER_PARKED_GIVER_REQUIRED')
        if (state['authorization_id']!=binding.get('authorization_id') or proof!=binding.get('observed_source_authorization') or
                not proof.get('actor_id') or proof.get('input_mode')!=parked.get('previous_input_mode') or
                native['owner_generation']!=binding.get('native_generation') or native['native_owner']!='keyboard' or
                proof.get('boot_id')!=native['boot_id']):raise ValueError('EXACT_CURRENT_NEW_USER_GIVER_BINDING_REQUIRED')
        if parked['stage']=='BEGIN_CLUTCHED':
            if (proof['input_mode']!='cooperative_policy' or native['active'] is not True or native['state']!='CLUTCHED' or
                    native['control_epoch']!=parked.get('control_epoch') or native['control_epoch']!=binding.get('native_control_epoch')):
                raise ValueError('EXACT_ORIGINAL_CLUTCHED_PHASE_REQUIRED')
        elif native['active'] is not False or native['stop_latched'] is not True:raise ValueError('EXACT_ORIGINAL_PARKED_STOP_REQUIRED')
        if (origin.get('schema')!='explorer_native_giver_park_command_context_v1' or origin.get('state_source')!='command_estimate' or
                origin.get('measured_feedback') is not None or origin.get('measured_feedback_mask')!=[False]*6 or
                origin.get('park_request_id')!=parked['park_request_id']):raise ValueError('SOURCE_SEALED_COMMAND_ESTIMATE_ORIGIN_REQUIRED')
        return binding,parked,origin
    def snapshot(self):
        result=_result('giver',state_source='command_estimate',measured_feedback=None,measured_feedback_mask=[False]*6)
        try:
            before=self._capture();binding,parked,origin=self._validate(before)
            self.owner.activation.require(binding['authorization_id'])  # existing cached camera; outside control locks
            from .giver_hold_persistence05 import read,SCHEMA as WAL_SCHEMA
            journal=self.owner.guard.journal;journal._marker()
            envelope=read(journal.path)
            if (envelope.get('schema')!=WAL_SCHEMA or envelope.get('state_id')!=before['persistence']['state_id'] or
                    envelope.get('sha256')!=digest({key:value for key,value in envelope.items() if key!='sha256'}) or
                    envelope.get('sha256')!=before['persistence']['last_sealed_sha256'] or
                    envelope.get('pending') is not None or envelope.get('fault') is not None):
                raise ValueError('GIVER_CURRENT_SEALED_WAL_REQUIRED')
            reference=self.owner.scope_reference()  # original guard, explicitly cannot clear a cooperative fault
            actual=self.owner.mux.tap.issued_snapshot();now=self.owner.mux.tap.clock()
            expected=origin.get('actual_issued') or {};native=before['native']
            if (actual.get('arm_receipt')!=expected.get('arm_receipt') or actual.get('command9')!=expected.get('command9') or
                    actual['command9'][6:]!=[0.,0.,0.] or reference.get('servo_deg')!=actual['command9'][:6] or
                    reference.get('boot_id')!=native['boot_id'] or reference.get('measured') is not False or
                    (self.owner.mux.tap.reference or {}).get('source_sha256')!=origin.get('original_reference_sha256') or
                    (self.owner.mux.tap.reference or {}).get('native_guard_verified') is not True):
                raise ValueError('ACTUAL_ORIGINAL_ARM_BASE9_OR_REFERENCE_CHANGED')
            # BEGIN genuinely publishes another neutral base request. Preserve
            # that literal receipt; do not compare it to the earlier base ID.
            if parked['stage']=='PARKED_NEW_USER' and actual.get('base_receipt')!=expected.get('base_receipt'):
                raise ValueError('PARKED_BASE_PUBLICATION_CHANGED')
            stamp=actual.get('stamp') or {}
            if (stamp.get('boot_id')!=native['boot_id'] or stamp.get('clock_domain')!=native['clock_domain'] or
                    type(stamp.get('monotonic_ns')) is not int or not 0<=stamp['monotonic_ns']<=now):
                raise ValueError('CURRENT_SOURCE_CLOCK_BOOT_REQUIRED')
            after=self._capture();self._validate(after)
            if after!=before:raise ValueError('GIVER_SCOPE_CHANGED_DURING_READ_ONLY_CAPTURE')
            latest=self.owner.mux.tap.issued_snapshot()
            if any(latest[key]!=actual[key] for key in ('arm_receipt','base_receipt','command9')):
                raise ValueError('GIVER_PUBLICATION_CHANGED_DURING_SCOPE_CAPTURE')
            result.update(ready=True,reason=None,binding=deepcopy(binding),native_owner=deepcopy(native),
                actor_id=before['authorization_receipt']['actor_id'],input_mode=parked['previous_input_mode'],
                authorization_receipt=deepcopy(before['authorization_receipt']),park_request_id=parked['park_request_id'],
                stage=parked['stage'],source_origin=deepcopy(origin),source_origin_sha256=digest(origin),
                actual_issued=actual,original_reference_guard_result=reference,
                persistence=before['persistence'],armed=before['armed'],
                source_stamp={'monotonic_ns':now,'boot_id':native['boot_id'],'clock_domain':native['clock_domain']})
        except (ValueError,OSError,KeyError,TypeError,AttributeError) as error:result['reason']=str(error)
        return dict(result,scope_sha256=digest(result))


def receiver_held_scope(wrapper):
    provider=wrapper.provider;station=wrapper.station
    result=_result('receiver',state_source='measured_state')
    try:
        # Only metadata writers take this lock. Original STOP/publication never
        # waits on it; no camera/file I/O holds Station/publication/provider locks.
        with wrapper.metadata_lock:
            if wrapper.fault is not None or wrapper.phase_prepared is not None:raise ValueError('RECEIVER_WAL_OR_PHASE_NOT_HEALTHY')
            with wrapper._locks():
                before=wrapper.helper.snapshot_state();current=wrapper._cached_permission()
                if current is None:raise ValueError('CURRENT_SOURCE_USER_RECEIVER_GRANT_REQUIRED')
                permission=deepcopy(current)
            provider.check(permission['grant_id'],permission['binding'],permission['control_epoch'])
            # Verify the actual immutable initialization marker and current WAL,
            # rather than inferring persistence health from an idle mode.
            wrapper._initialized()
            from .station_cooperative_interlock import _read,ENVELOPE
            envelope=_read(wrapper.path)
            if (envelope.get('schema')!=ENVELOPE or envelope.get('envelope_sha256')!=digest({key:value for key,value in envelope.items() if key!='envelope_sha256'}) or
                    envelope.get('pending') is not None or envelope.get('fault') is not None or envelope.get('state')!=before):
                raise ValueError('RECEIVER_CURRENT_SEALED_WAL_REQUIRED')
            with wrapper._locks():
                current=wrapper._locked_current(permission,permission['actor_id']);state=wrapper.helper.snapshot_state()
                frozen=state.get('frozen') or {};now=wrapper.clock()
                if (state!=before or not wrapper.helper._current(current) or frozen.get('parked_observation_only') is not True or
                        station.mode!='idle' or current['native_mode']!='idle' or current['binding'].get('purpose')!='cooperative05' or
                        wrapper.fault is not None or wrapper.phase_prepared is not None):raise ValueError('EXACT_CURRENT_PARKED_RECEIVER_BINDING_REQUIRED')
                origin=wrapper._hold_origin(state,current);measured=deepcopy(station.measurements);stamp=measured.get('monotonic_ns')
                frame=current.get('displayed_frame') or {};received=frame.get('received_monotonic_ns');created=current.get('created_monotonic_ns')
                observed=current.get('observed_monotonic_ns')
                if (origin.get('origin_sha256')!=digest({key:value for key,value in origin.items() if key!='origin_sha256'}) or
                        origin.get('held_native_epoch')!=station.control_epoch or current['control_epoch']!=station.control_epoch or
                        origin.get('boot_id')!=station.boot or current['boot_id']!=station.boot or
                        origin.get('clock_domain')!=station.clock_domain or current['clock_domain']!=station.clock_domain or
                        origin.get('binding')!=current['binding'] or origin.get('follower_calibration_sha256')!=current['follower_calibration_sha256'] or
                        measured.get('clock_domain')!=station.clock_domain or type(stamp) is not int or
                        not origin['write_returned_native_ns']<=stamp<=now or not 0<=now-stamp<=500_000_000 or
                        _six(measured.get('follower'))!=origin['held_native6'] or frame.get('boot_id')!=station.boot or
                        frame.get('clock_domain')!=station.clock_domain or any(type(v) is not int for v in (received,created,observed)) or
                        not origin['write_returned_native_ns']<=created<=now or not origin['write_returned_native_ns']<=received<=observed<=now or
                        not now<current['observation_deadline_ns']):
                    raise ValueError('FRESH_EXACT_MEASURED6_NEW_USER_FRAME_AND_ORIGINAL_HOLD_REQUIRED')
                result.update(ready=True,reason=None,binding=deepcopy(state['binding']),native_owner=deepcopy(state['owner']),
                    actor_id=current['actor_id'],grant=deepcopy(current),source_origin=origin,source_origin_sha256=origin['origin_sha256'],
                    measured_native6=deepcopy(measured['follower']),measurement_stamp={'monotonic_ns':stamp,'boot_id':station.boot,'clock_domain':station.clock_domain},
                    follower_calibration_sha256=current['follower_calibration_sha256'],interlock_id=state['interlock_id'],revision=state['revision'],
                    armed=state['armed'],direction_profile=deepcopy(state['direction']),
                    persistence={'installed':True,'healthy':True,'initialized':True,'pending':False,'state_sha256':state['state_sha256']},
                    source_stamp={'monotonic_ns':now,'boot_id':station.boot,'clock_domain':station.clock_domain})
    except (ValueError,OSError,KeyError,TypeError,AttributeError) as error:result['reason']=str(error)
    return dict(result,scope_sha256=digest(result))


def _arm_receipt(role,scope,interlock,already):
    return {'schema':'explorer_cooperative_parked_arm_receipt_v1','role':role,'armed':True,'already_armed':already,
        'binding':deepcopy(scope['binding']),'native_owner':deepcopy(scope['native_owner']),
        'source_scope_sha256':scope['scope_sha256'],'source_origin_sha256':scope['source_origin_sha256'],
        'source_scope':deepcopy(scope),
        'interlock':interlock,'native_commands_sent':0,'native_command_called':False,'native_lease_renewed':False,
        'physical_execution_authorized':False,'hold_contact_or_stationarity_claimed':False}


def arm_giver_parked(owner,body,*,authenticated_actor):
    guard=owner.guard;phase=owner.phases
    with guard.metadata_lock:
        _,parked=phase._current(body,authenticated_actor,parked_stage={'PARKED_NEW_USER','BEGIN_CLUTCHED'})
        scope=owner.held_scope.snapshot()
        if not scope['ready'] or scope['actor_id']!=authenticated_actor:raise ValueError('CURRENT_EXACT_PARKED_GIVER_SCOPE_REQUIRED_TO_ARM')
        if scope['park_request_id']!=body.get('park_request_id'):raise ValueError('EXACT_SOURCE_PARKED_ARM_TOKEN_REQUIRED')
        def mutate():
            with guard.lock:
                current=owner.held_scope._capture();native=current['native'];before=scope['native_owner']
                issued=owner.mux.tap.issued_snapshot()
                if (guard.binding!=scope['binding'] or guard.parked!=parked or guard.revision!=body['expected_revision'] or
                        current['reference_fault'] is not None or current['drained']['ready'] is not True or
                        current['authorization_id']!=body['authorization_id'] or current['authorization_receipt']!=scope['authorization_receipt'] or
                        native!=before or any(issued[key]!=scope['actual_issued'][key] for key in ('command9','arm_receipt','base_receipt')) or
                        (owner.mux.tap.reference or {}).get('source_sha256')!=scope['source_origin']['original_reference_sha256'] or
                        guard.pending is not None or guard.grant is not None):
                    raise ValueError('PARKED_GIVER_OWNER_PUBLICATION_OR_PENDING_PROOF_CHANGED_BEFORE_ARM')
                already=guard.armed
                if not already:
                    guard.armed=True;guard.last_pose=list(issued['command9'][:6]);guard._bump_revision()
                    phase._retain(phase._event('PARKED_GIVER_DUAL_HOLD_ARMED',phase_id=body.get('phase_id'),
                        source_origin_sha256=scope['source_origin_sha256'],native_command_called=False,
                        source_state='command_estimate',old_reference_replaced=False))
                return already
        already=guard._durable('metadata_parked_giver_arm',mutate)
        return _arm_receipt('giver',scope,guard.status(),already)


def arm_receiver_parked(wrapper,body,*,authenticated_actor):
    allowed={'grant_id','expected_control_epoch','interlock_id','expected_revision','observing','user_observed_test_confirmed'}
    if (not isinstance(body,dict) or set(body)!=allowed or not authenticated_actor or
            body.get('observing') is not True or body.get('user_observed_test_confirmed') is not True):
        raise ValueError('ID_ONLY_EXPLICIT_USER_PARKED_RECEIVER_ARM_REQUIRED')
    with wrapper.metadata_lock:
        scope=wrapper.held_scope()
        if not scope['ready'] or scope['actor_id']!=authenticated_actor:raise ValueError('CURRENT_EXACT_PARKED_RECEIVER_SCOPE_REQUIRED_TO_ARM')
        def mutate(permission):
            wrapper.helper._cas(body.get('interlock_id'),body.get('expected_revision'));state=wrapper.helper.snapshot_state()
            origin=wrapper._hold_origin(state,permission);measured=deepcopy(wrapper.station.measurements);now=wrapper.clock()
            if (not wrapper.helper._current(permission) or permission['grant_id']!=scope['grant']['grant_id'] or
                    permission['control_epoch']!=scope['grant']['control_epoch'] or state['owner']!=scope['native_owner'] or
                    state['binding']!=scope['binding'] or (state.get('frozen') or {}).get('parked_observation_only') is not True or
                    wrapper.station.mode!='idle' or origin!=scope['source_origin'] or measured.get('follower')!=scope['measured_native6'] or
                    measured.get('clock_domain')!=scope['source_stamp']['clock_domain'] or type(measured.get('monotonic_ns')) is not int or
                    not origin['write_returned_native_ns']<=measured['monotonic_ns']<=now or not now-measured['monotonic_ns']<=500_000_000 or
                    state.get('pending') is not None):raise ValueError('PARKED_RECEIVER_SOURCE_OR_PENDING_PROOF_CHANGED_BEFORE_ARM')
            already=state['armed']
            if not already:
                # A new latch reuses the actual sealed hold reference. Keep the
                # entire frozen origin/history and used proof IDs unchanged.
                with wrapper.helper.lock:
                    wrapper.helper.armed=True;wrapper.helper.phase='ARMED';wrapper.helper.revision+=1
                    wrapper.helper._event('PARKED_RECEIVER_DUAL_HOLD_ARMED',source_origin_sha256=scope['source_origin_sha256'],
                        source_scope_sha256=scope['scope_sha256'],original_frozen_reference_preserved=True)
            return already
        already=wrapper._transaction('held/arm',body,authenticated_actor,mutate)
        return _arm_receipt('receiver',scope,wrapper.status(),already)
