"""ENGINEERING_GLUE: same-owner cooperative policy input and release boundary.

No device/thread/publisher is created. The existing ManualArm velocity worker,
native observation lease, factory geometry, limits and Ruckig constants remain
authoritative. A wrapped EXISTING ROS publisher checks opening at publication.
"""
from copy import deepcopy
import math
import re
import threading
import uuid
from collections import OrderedDict
from functools import wraps

from .cooperative_contract05 import digest,ROLE_CONTRACTS
from .contracts import action_vector
from .handover import HandoverFSM
from .cooperative_action_constraint05 import verify_constraint,project_stationary_action
from .giver_hold_persistence05 import GiverHoldJournal


def durable_metadata(*,recover=False):
    def decorate(method):
        @wraps(method)
        def call(self,*args,**kwargs):
            if recover:self._check_reset(*args,**kwargs)
            return self._durable(method.__name__,lambda:method(self,*args,**kwargs),recover=recover)
        return call
    return decorate


def identity(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}',value):
        raise ValueError('Bounded explicit cooperative identity required')
    return value


class ExistingPublisherBoundary:
    """Proxy the SAME Publisher. No second ROS publisher or output loop."""
    def __init__(self,publisher,guard):self.original,self.guard=publisher,guard
    def __getattr__(self,key):return getattr(self.original,key)
    def publish(self,message):
        pose=[getattr(message,'joint'+str(index)) for index in range(1,7)]
        tap=self.guard.mux.tap;started=tap.clock();attempted=False;tap.publisher_begin()
        def original():
            nonlocal attempted
            attempted=True;result=self.original.publish(message)
            # Keep receipt order inside the existing publication guard. A
            # second caller cannot return and then be overwritten by this one.
            tap.publisher_returned(pose,getattr(message,'time',None),started)
            return result
        try:
            return self.guard.publish(pose,original)
        except Exception:
            if attempted:tap.publisher_failed()
            raise
        finally:tap.publisher_end()


class ExistingBaseDriveBoundary:
    """Same traced original teleop drive; genuine zero STOP always delegates."""
    def __init__(self,drive,guard):self.original,self.guard=drive,guard
    def __getattr__(self,key):return getattr(self.original,key)
    def __call__(self,values):
        zero=(isinstance(values,(list,tuple)) and len(values)==3 and all(type(value) in (int,float) and
            math.isfinite(value) and value==0 for value in values))
        # An original zero STOP must not wait for an in-flight arm Publisher.
        # The existing traced drive retains its own real publication receipt.
        if zero:return self.original(values)
        with self.guard.lock:
            if self.guard.base_motion_blocked():
                raise PermissionError('COOPERATIVE_GIVER_LATCH_REQUIRES_STATIONARY_BASE')
            return self.original(values)


class NativeReceiverHoldBoundary:
    def __init__(self,mux,activation,*,clock=None,state_path=None):
        self.mux,self.activation=mux,activation;self.clock=clock or mux.tap.clock
        self.lock=threading.RLock();self.revision_lock=threading.Lock();self.binding=None;self.armed=False;self.pending=None;self.grant=None
        self.revision=0;self.used=set();self.last_pose=None;self.last_publication=None;self.events=[]
        self.reference_fault=None
        self.parked=None;self.phase_history=[];self.phase_event_count=0;self.phase_event_chain_sha256=None
        self.ttl_ns=HandoverFSM('native-cooperative-hold').hold_ttl_ns
        self.metadata_lock=threading.RLock();self.journal=GiverHoldJournal(state_path)
        if self.journal.state is not None:
            try:self._restore(self.journal.state)
            except (ValueError,KeyError,TypeError,AttributeError) as error:self.journal.fail('GIVER_STATE_UNCERTAIN:'+str(error))
        stored=self.journal.state if isinstance(self.journal.state,dict) else {}
        if self.reference_fault is not None and stored.get('native_boot_id')!=self.mux.tap.boot_id:
            self.reference_fault={'invalidated_native_ns':self.clock(),'reason':'NEW_BOOT_REFERENCE_REVALIDATION_REQUIRED',
                'original_reference_sha256':stored.get('reference_source_sha256'),'native_boot_id':self.mux.tap.boot_id,
                'previous_native_boot_id':stored.get('native_boot_id'),'previous_fault_sha256':digest(self.reference_fault),
                'previous_boot_timestamps_compared_to_current_boot':False}
        if self.armed or self.journal.fault is not None:
            self.journal.restored_armed=self.armed;self.journal.recovery_required=True;self.armed=True
            if self.reference_fault is None:self.reference_fault={'invalidated_native_ns':self.clock(),
                'reason':'DURABLE_GIVER_LATCH_RESTORED_REFERENCE_UNVERIFIED',
                'original_reference_sha256':(self.journal.state if isinstance(self.journal.state,dict) else {}).get('reference_source_sha256')}
        if self.parked is not None:self.journal.recovery_required=True
    def _snapshot(self):
        return {'schema':'explorer_native_giver_hold_state_v1','binding':deepcopy(self.binding),'armed':self.armed,'revision':self.revision,
            'used_confirmations':sorted(self.used),'last_pose':deepcopy(self.last_pose),'last_publication':deepcopy(self.last_publication),
            'events':deepcopy(self.events[-8:]),'reference_fault':deepcopy(self.reference_fault),
            'parked':deepcopy(self.parked),'phase_history':deepcopy(self.phase_history),
            'phase_event_count':self.phase_event_count,'phase_event_chain_sha256':self.phase_event_chain_sha256,
            'reference_source_sha256':(self.mux.tap.reference or {}).get('source_sha256'),
            'native_clock_domain':self.mux.tap.clock_domain,'native_boot_id':self.mux.tap.boot_id,
            'active_permissions_not_restored':True,'latest_pose_not_qualified_for_automatic_restart':True}
    def _bump_revision(self):
        # Revoke increments before waiting for an admitted Publisher. A
        # dedicated tiny lock prevents lost increments against metadata code.
        with self.revision_lock:self.revision+=1;return self.revision
    def _restore(self,state):
        if (not isinstance(state,dict) or state.get('schema')!='explorer_native_giver_hold_state_v1' or type(state.get('armed')) is not bool or
                type(state.get('revision')) is not int or state['revision']<0 or not isinstance(state.get('used_confirmations'),list) or
                len(state['used_confirmations'])>2048 or len(set(state['used_confirmations']))!=len(state['used_confirmations']) or
                not isinstance(state.get('events'),list) or len(state['events'])>8 or
                state.get('binding') is not None and not isinstance(state['binding'],dict) or
                state.get('reference_fault') is not None and not isinstance(state['reference_fault'],dict)):
            raise ValueError('Typed bounded retained giver latch required')
        for value in state['used_confirmations']:identity(value)
        fault=state.get('reference_fault')
        if fault is not None and (type(fault.get('invalidated_native_ns')) is not int or fault['invalidated_native_ns']<0 or
                not isinstance(fault.get('reason'),str) or not fault['reason']):raise ValueError('Original reference-fault identity required')
        if state['armed']:
            if not isinstance(state.get('binding'),dict) or not isinstance(state.get('last_pose'),list) or len(state['last_pose'])!=6 or any(
                    type(value) not in (int,float) or not math.isfinite(value) for value in state['last_pose']):raise ValueError('Original armed giver reference required')
            for key in ('run_id','object_id','coordinator_epoch','authorization_id'):identity(state['binding'].get(key))
        self.binding=deepcopy(state.get('binding'));self.armed=state['armed'];self.revision=state['revision'];self.used=set(state['used_confirmations'])
        self.last_pose=deepcopy(state.get('last_pose'));self.last_publication=deepcopy(state.get('last_publication'))
        self.events=deepcopy(state['events']);self.reference_fault=deepcopy(state.get('reference_fault'))
        if (state.get('parked') is not None and not isinstance(state['parked'],dict) or
                not isinstance(state.get('phase_history',[]),list) or len(state.get('phase_history',[]))>32):
            raise ValueError('Bounded retained giver phase lineage required')
        self.parked=deepcopy(state.get('parked'));self.phase_history=deepcopy(state.get('phase_history',[]))
        count=state.get('phase_event_count',0);chain=state.get('phase_event_chain_sha256')
        if (type(count) is not int or count<len(self.phase_history) or chain is not None and
                (not isinstance(chain,str) or not re.fullmatch('[0-9a-f]{64}',chain))):raise ValueError('Exact retained source phase-history chain required')
        if (count==0)!=(chain is None) or count>0 and not self.phase_history:raise ValueError('Retained source phase chain is incomplete')
        for index,event in enumerate(self.phase_history):
            if (not isinstance(event,dict) or event.get('source_event_ordinal')!=count-len(self.phase_history)+index+1 or
                    event.get('source_event_sha256')!=digest({key:value for key,value in event.items() if key!='source_event_sha256'}) or
                    index>0 and event.get('prior_event_chain_sha256')!=self.phase_history[index-1]['source_event_sha256']):
                raise ValueError('Retained source control-history digest/order differs')
        if self.phase_history and self.phase_history[-1]['source_event_sha256']!=chain:raise ValueError('Source phase-history tail digest differs')
        self.phase_event_count=count;self.phase_event_chain_sha256=chain
        self.pending=self.grant=None  # A disk record is never live receiver-hold authority.
    def _durable(self,operation,callback,*,recover=False):
        if self.journal.path is None:return callback()
        with self.metadata_lock:
            if (self.journal.fault is not None or self.journal.recovery_required) and not recover:
                raise ValueError('Durable giver recovery requires explicit USER safe-object reset')
            with self.lock:before=self._snapshot();revision=self.revision
            self.journal.begin(operation,before,recover=recover)
            try:
                # File fsync is outside both publication and teleop locks.
                with self.mux.teleop.lock:
                    with self.lock:
                        if self.revision!=revision:raise ValueError('Giver metadata superseded by revoke/publication failure')
                    result=callback()
                    with self.lock:after=self._snapshot()
                self.journal.seal(after,recover=recover)
                if isinstance(result,dict) and result.get('schema')=='explorer_native_receiver_hold_boundary_v1':
                    result['persistence']=self.journal.status(self.revision)
                return result
            except Exception:
                with self.lock:after=self._snapshot()
                try:
                    if after==before and not recover:self.journal.seal(before)
                    else:self.journal.fail('GIVER_MUTATION_OR_SEAL_UNCERTAIN')
                except Exception:self.journal.fail('GIVER_REJECTED_METADATA_SEAL_FAILED')
                raise
    def _current(self,*,cached=False):
        if self.journal.fault is not None or self.journal.recovery_required:raise ValueError('Durable giver recovery is required')
        if self.parked is not None:raise ValueError('Explicit USER parked-phase transition required')
        binding=self.binding
        if (binding is None or self.mux.teleop.owner!='keyboard' or self.mux.teleop.generation!=binding['native_generation'] or
                self.mux.teleop.stop_latched or not self.mux.clock()<self.mux.teleop.lease or
                (self.activation.current_authorization_id()!=binding['authorization_id'] if cached else
                    not self.activation.allowed() or self.activation.receipt(binding['authorization_id']) is None)):
            raise ValueError('Current original native owner/observation lease required')
        if binding.get('native_control_epoch') is not None and (
                not self.mux.active or self.mux.epoch!=binding['native_control_epoch']):raise ValueError('Native cooperative epoch changed')
        return binding
    def base_motion_blocked(self):
        with self.lock:return bool(self.binding is not None or self.armed or self.parked is not None or self.reference_fault is not None or
            self.journal.pending or self.journal.fault is not None or self.journal.recovery_required)
    @durable_metadata()
    def bind(self,body):
        self.activation.require(body.get('authorization_id'))
        for key in ('run_id','object_id','coordinator_epoch'):identity(body.get(key))
        if body.get('observing') is not True:raise ValueError('Explicit current observed cooperative binding required')
        with self.mux.teleop.lock:
            if not self.mux.teleop.stop_latched:raise ValueError('Original STOP required before cooperative metadata binding')
            with self.lock:
                if self.armed or self.parked is not None:raise ValueError('Explicit observed safe-object reset required before replacing dual-hold protection')
                self._bump_revision();self.pending=self.grant=None;self.last_pose=None
                self.binding={key:body[key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')}
                self.binding.update(native_generation=self.mux.teleop.generation,native_control_epoch=None)
                self.binding['observed_source_authorization']=self.activation.receipt(body['authorization_id'])
        return self.status()
    def bind_control_epoch(self,epoch):
        with self.lock:
            if self.binding is None:raise ValueError('Observed cooperative binding required')
            self._bump_revision();self.pending=self.grant=None
            self.binding.update(native_generation=self.mux.teleop.generation,native_control_epoch=epoch)
    def _match(self,body):
        current=self.binding
        if current is None or any(body.get(key)!=current[key] for key in ('run_id','object_id','coordinator_epoch')):
            raise ValueError('Exact current cooperative run/object/epoch required')
        return current
    @durable_metadata()
    def arm(self,body,reference):
        self._current();self._match(body)
        values=reference.get('servo_deg') if isinstance(reference,dict) else None
        if (reference.get('boot_id')!=self.mux.tap.boot_id or reference.get('measured') is not False or
                not isinstance(values,list) or len(values)!=6 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in values)):
            raise ValueError('Original same-boot command estimate required; not measured feedback')
        with self.lock:
            self._current();self._match(body);self._bump_revision();self.pending=self.grant=None
            actual=self.mux.tap.issued_snapshot()
            self.armed=True;self.last_pose=list(actual['command9'][:6])
        return self.status()
    def challenge(self,body):
        self._current();self._match(body);identity(body.get('after_receiver_command_id'))
        with self.lock:
            self._current();self._match(body)
            if not self.armed:raise ValueError('Dual-hold publication protection must be armed first')
            revision=self._bump_revision();self.grant=None
            self.pending={'schema':'explorer_native_receiver_hold_challenge_v1','challenge_id':uuid.uuid4().hex,
                **deepcopy(self.binding),'after_receiver_command_id':body['after_receiver_command_id'],
                'issued_native_ns':self.clock(),'native_clock_domain':self.mux.tap.clock_domain,
                'native_boot_id':self.mux.tap.boot_id,'revision':revision}
            self.pending['expires_native_ns']=self.pending['issued_native_ns']+self.ttl_ns
            return deepcopy(self.pending)
    @durable_metadata()
    def authorize(self,body):
        self._current();self._match(body)
        with self.lock:
            self._current();self._match(body);challenge=self.pending;proof=body.get('operator_confirmation') or {}
            if (challenge is None or body.get('challenge_id')!=challenge['challenge_id'] or
                    not challenge['issued_native_ns']<=self.clock()<challenge['expires_native_ns'] or
                    challenge['revision']!=self.revision):raise ValueError('Native receiver-hold challenge expired/revoked')
            # Trusted authenticated coordinator must first consume its real
            # one-use inbox response CREATED AFTER receiving this challenge.
            # Source timestamps remain their own domains, never compared here.
            if (proof.get('statement')!='receiver_holds_object' or proof.get('role')!='receiver' or
                    proof.get('run_id')!=challenge['run_id'] or proof.get('object_id')!=challenge['object_id'] or
                    proof.get('after_command_id')!=challenge['after_receiver_command_id'] or
                    proof.get('affirmed') is not True or proof.get('observing') is not True or
                    proof.get('state')!='consumed' or not proof.get('authenticated_actor') or
                    body.get('coordinator_verified_confirmation_created_after_challenge') is not True):
                raise ValueError('Source-bound new explicit receiver observation required')
            receipt_id=identity(proof.get('challenge_id'))
            if receipt_id in self.used:raise ValueError('Receiver holding receipt cannot be reused')
            if len(self.used)>=2048:raise ValueError('Retained native receiver-proof budget reached')
            selected={'grant_id':uuid.uuid4().hex,'challenge':deepcopy(challenge),'operator_confirmation':deepcopy(proof),
                'expires_native_ns':challenge['expires_native_ns'],'revision':challenge['revision'],
                'clock_conversion_assumed':False,'force_or_attainment_measured':False}
            with self.revision_lock:
                if self.revision!=challenge['revision'] or self.pending is not challenge:
                    raise ValueError('Native receiver-hold authorization superseded by revoke')
                self.used.add(receipt_id);self.grant=selected;self.pending=None
            return deepcopy(selected)
    def revoke(self,reason):
        # Logical invalidation NEVER waits for an admitted arm Publisher: the
        # original global STOP must reach its independent base latch promptly.
        # A concurrently finishing authorization carries its captured OLD
        # challenge revision and cannot restore opening authority.
        with self.revision_lock:self.revision+=1;self.pending=self.grant=None
        return {'revoked':True,'reason':reason,'dual_hold_protection_remains_armed':self.armed}
    def drained(self):
        """Cached ORIGINAL worker/publisher state, not hold or attainment."""
        arm=self.mux.arm;lock=getattr(arm,'lock',None);locked=getattr(lock,'locked',None)
        busy=locked() if callable(locked) else None
        with self.mux.tap.lock:pending=self.mux.tap.publisher_pending;base=self.mux.tap.base_pending
        stream=getattr(arm,'streaming_active',None)
        return {'ready':busy is False and stream is False and pending==0 and base==0,
            'original_arm_worker_busy':busy,'original_streaming_active':stream,'publisher_pending':pending,'base_pending':base,
            'basis':'cached_original_worker_and_real_boundary_counters_not_measured_hold_or_stationarity'}
    def require_drained(self):
        current=self.drained()
        if not current['ready']:raise ValueError('NOT_READY_ORIGINAL_WORKERS_OR_PUBLICATIONS_NOT_DRAINED')
        return current
    def _check_reset(self,body):
        self.require_drained()
        self.activation.require(body.get('authorization_id'))
        with self.mux.teleop.lock:
            if (body.get('observing') is not True or body.get('object_safe_no_dual_hold') is not True or
                    not self.mux.teleop.stop_latched or self.activation.receipt(body.get('authorization_id')) is None):
                raise ValueError('Explicit observed safe-object reset under original STOP required')
            with self.lock:
                self.require_drained()
                if self.journal.path is not None and (body.get('expected_state_id')!=self.journal.state_id or
                        type(body.get('expected_revision')) is not int or body['expected_revision']!=self.revision):
                    raise ValueError('Exact durable giver state ID/revision required for USER safe reset')
    @durable_metadata(recover=True)
    def reset_safe(self,body):
        with self.mux.teleop.lock:
            self._check_reset(body)
            with self.lock:
                self._bump_revision();self.binding=self.pending=self.grant=None;self.armed=False;self.last_pose=None
                self.parked=None
        return self.status()
    def publish(self,pose,callback):
        with self.lock:
            if not self.armed:self.last_publication=None
            try:
                if self.journal.pending or self.journal.fault is not None or self.journal.recovery_required:
                    raise ValueError('DURABLE_GIVER_PUBLICATION_RECOVERY_REQUIRED')
                if self.parked is not None:raise ValueError('PARKED_GIVER_REQUIRES_EXPLICIT_USER_PHASE_RESUME')
                if self.armed and self.reference_fault is not None:
                    raise ValueError('OBSERVED_FACTORY_REFERENCE_REQUIRED_AFTER_BLOCKED_PUBLICATION')
                if self.armed and (self.last_pose is None or any(pose[index]!=self.last_pose[index] for index in range(5))):
                    raise ValueError('DUAL_HOLD_REQUIRES_STATIONARY_GIVER_ARM_NO_RELATIVE_MOTION')
                if self.armed:self._current()  # Closing/legacy callers also require this exact current observed owner.
                opening=self.armed and (self.last_pose is None or pose[5]<self.last_pose[5])
                if opening:
                    self._current();grant=self.grant
                    if (grant is None or grant['revision']!=self.revision or
                            not grant['challenge']['issued_native_ns']<=self.clock()<grant['expires_native_ns']):
                        raise ValueError('NO_FRESH_NATIVE_RECEIVER_HOLD_AT_OPENING_PUBLICATION')
                admitted_binding=deepcopy(self.binding);admitted_grant=deepcopy(self.grant) if opening else None
                # Serialize only cached admission with logical revoke. Camera
                # reads and the ORIGINAL Publisher are outside this tiny lock.
                with self.revision_lock:
                    if self.armed:self._current(cached=True)
                    revision=self.revision;started=self.clock()
                    if opening and (self.grant is None or admitted_grant is None or
                            self.grant['grant_id']!=admitted_grant['grant_id'] or self.grant['revision']!=revision or
                            not self.grant['challenge']['issued_native_ns']<=started<self.grant['expires_native_ns']):
                        raise ValueError('NO_FRESH_NATIVE_RECEIVER_HOLD_AT_OPENING_PUBLICATION')
            except ValueError as error:
                self._publication_failed(str(error));raise
            try:result=callback()  # Same original Publisher, serialized transaction.
            except Exception:
                if self.armed:self._publication_failed('ORIGINAL_PUBLISHER_RETURN_UNKNOWN')
                raise
            finished=self.clock()
            if self.armed:
                receipt={'schema':'explorer_native_cooperative_publication_v1','receipt_id':uuid.uuid4().hex,
                    'binding':admitted_binding,'requested_servo_deg':list(pose),'opening':opening,
                    'guard_grant':admitted_grant,'guard_revision':revision,
                    'host_publication_started_native_ns':started,'host_publication_returned_native_ns':finished,
                    'native_clock_domain':self.mux.tap.clock_domain,'native_boot_id':self.mux.tap.boot_id,
                    'semantics':'original_publisher_returned_not_MCU_ack_or_attainment',
                    'bounded_endpoint_may_finish_after_stop':True}
                self.last_publication=receipt;self.events.append(receipt);self.events=self.events[-128:]
                self.last_pose=list(pose)
            return result
    def _publication_failed(self,reason):
        # Factory journal is written BEFORE Publisher.publish. A rejected target
        # must not become a future starting estimate or restart an old stream.
        if self.reference_fault is None:
            self.reference_fault={'invalidated_native_ns':self.clock(),'reason':reason,
                'original_reference_sha256':(self.mux.tap.reference or {}).get('source_sha256')}
        self.revoke(reason);self.mux.active=False;self.mux.state='COOPERATIVE_PUBLICATION_REJECTED'
        self.mux.latest=[0.]*6;self.mux.intent_revision+=1
        self.activation.revoke('NATIVE_COOPERATIVE_PUBLICATION_REFERENCE_UNKNOWN')
    def status(self):
        with self.lock:return {'schema':'explorer_native_receiver_hold_boundary_v1','binding':deepcopy(self.binding),
            'armed':self.armed,'pending_challenge':deepcopy(self.pending),'grant':deepcopy(self.grant),
            'last_publication':deepcopy(self.last_publication),'hold_ttl_ns':self.ttl_ns,
            'recent_opening_publications':deepcopy([entry for entry in self.events if entry.get('opening') is True][-8:]),
            'opening_history_basis':'actual_original_publisher_returns_not_current_hold_or_reusable_release_authority',
            'reference_fault':deepcopy(self.reference_fault),
            'persistence':self.journal.status(self.revision),'revision':self.revision,
            'original_quiescence':self.drained(),
            'parked':deepcopy(self.parked),'phase_history':deepcopy(self.phase_history),
            'phase_event_count':self.phase_event_count,'phase_event_chain_sha256':self.phase_event_chain_sha256,
            'complete_phase_event_bytes_retained':self.phase_event_count==len(self.phase_history),
            'controller_settings_written':False,'new_publishers':0,'physical_qualification':False}


class NativeCooperativeOwner:
    def __init__(self,mux,activation,reference_reader,*,target_limits,state_path=None,scope_reference_reader=None):
        self.mux,self.activation,self.reference=mux,activation,reference_reader
        self.scope_reference=scope_reference_reader or reference_reader
        self.limits=target_limits;self.guard=NativeReceiverHoldBoundary(mux,activation,state_path=state_path);self.policy=None;self.action_constraint=None
        self.frames=OrderedDict();self.frame_lock=threading.Lock()
        self.original_publisher=mux.arm.pub
        mux.arm.pub=ExistingPublisherBoundary(self.original_publisher,self.guard)
        from .giver_phase_lifecycle05 import GiverPhaseLifecycle
        self.phases=GiverPhaseLifecycle(self)
        from .giver_parked_release05 import GiverParkedRelease
        self.parked_release=GiverParkedRelease(self)
        from .cooperative_held_scopes05 import GiverParkedObservationScope
        self.held_scope=GiverParkedObservationScope(self)
    def park(self,body,*,authenticated_actor=None):return self.phases.park(body,authenticated_actor=authenticated_actor)
    def adopt_parked(self,body,*,authenticated_actor=None):return self.phases.adopt(body,authenticated_actor=authenticated_actor)
    def begin_parked(self,body,*,authenticated_actor=None):return self.phases.begin(body,authenticated_actor=authenticated_actor)
    def resume_parked(self,body,*,authenticated_actor=None):return self.phases.resume(body,authenticated_actor=authenticated_actor)
    def parked_release_challenge(self,body,*,authenticated_actor=None):return self.parked_release.challenge(body,authenticated_actor=authenticated_actor)
    def resume_parked_release(self,body,*,authenticated_actor=None):return self.parked_release.resume_release(body,authenticated_actor=authenticated_actor)
    def observation(self):
        frame=self.activation.camera.snapshot(with_jpeg=True);self.reference();ident=uuid.uuid4().hex
        issued=self.mux.tap.issued_snapshot()
        with self.mux.tap.lock:
            header=deepcopy(self.mux.tap.reference)
        record={'observation_id':ident,'frame_id':frame['frame_id'],'jpeg_sha256':frame['jpeg_sha256'],
            'monotonic_ns':frame['received_monotonic_ns'],'clock_domain':self.mux.tap.clock_domain,
            'boot_id':self.mux.tap.boot_id,'state_source':'command_estimate',
            'reference_servo_deg':list(issued['command9'][:6]),
            'reference_read_native_ns':issued['stamp']['monotonic_ns'],'reference_is_not_measured':True,
            'timestamp_basis':frame['timestamp_basis'],'exposure_time_verified':False,
            'command_reference9':issued['command9'],'command_reference9_basis':issued['basis'],
            'issued_context_receipts':{'arm':issued['arm_receipt'],'base':issued['base_receipt']},
            'journal_pose_used_for_command9':False,
            'base_reference_receipt':issued['base_receipt'],'original_reference_header':header,
            'unknown_base_never_replaced_with_zero':True}
        with self.frame_lock:
            self.frames[ident]=(deepcopy(record),frame['jpeg'])
            while len(self.frames)>8:self.frames.popitem(last=False)
        return record
    def observation_frame(self,ident):
        with self.frame_lock:
            if ident not in self.frames:raise ValueError('Exact native policy source frame is no longer retained')
            record,jpeg=self.frames[ident]
            if not 0<=self.mux.tap.clock()-record['monotonic_ns']<=2_000_000_000:
                raise ValueError('Native policy observation expired')
            return jpeg,deepcopy(record)
    def prepare(self,body):
        if self.guard.journal.fault is not None or self.guard.journal.recovery_required:raise ValueError('Durable giver safe-object recovery required before native prepare')
        self.activation.require(body.get('authorization_id'))
        if body.get('observing') is not True or body.get('neutral') is not True:raise ValueError('Explicit USER observed neutral selection required')
        with self.mux.teleop.lock:
            if (self.mux.active or not self.mux.teleop.stop_latched or any(abs(value)>1e-8 for value in self.mux.teleop.inputs.values()) or
                    self.guard.armed or self.guard.parked is not None or self.activation.receipt(body.get('authorization_id')) is None):
                raise ValueError('Exact current observed STOP and neutral owner required before preparation')
            # Exact audited original ManualTeleop ABI. No new input driver.
            result=self.mux.teleop.select_source('keyboard',True,True)
            return {'source_selection':result,'native':self.mux.status(),'native_begin_performed':False}
    def manual_resume(self,body):
        if self.guard.parked is not None and not self.phases.in_transition():raise ValueError('Explicit parked-phase resume required')
        self.activation.require(body.get('authorization_id'))
        if self.activation.status().get('input_mode') not in ('keyboard','browser_gamepad') or body.get('observing') is not True:
            raise ValueError('Exact separately USER-observed keyboard/gamepad input required')
        with self.mux.teleop.lock:
            self.guard._match(body);bound=self.guard.binding
            if (self.mux.active or bound['authorization_id']!=body.get('authorization_id') or
                    self.mux.teleop.generation!=body.get('expected_native_generation') or
                    self.mux.teleop.generation!=bound['native_generation'] or not self.mux.teleop.stop_latched or
                    self.activation.receipt(body.get('authorization_id')) is None):
                raise ValueError('Exact neutral original manual generation/STOP required')
            result=self.mux.teleop.resume('keyboard',True)
            try:self.mux._base_drive([0.,0.,0.])  # explicit USER action, genuine original zero receipt only.
            except Exception:
                self.guard.revoke('MANUAL_RESUME_NEUTRAL_DELIVERY_UNKNOWN');self.mux.teleop.stop()
                raise
            return {'source_resume':result,'native':self.mux.status(),'manual_generation':self.mux.teleop.generation,
                'neutral_base_host_request_returned':True,'native_operator_lease_automatically_renewed':False}
    def register_constraint(self,body):
        if self.guard.parked is not None:raise ValueError('Parked phase preserves its existing exact constraint')
        self.activation.require(body.get('authorization_id'))
        selected=verify_constraint(body.get('constraint'),binding=body)
        with self.mux.teleop.lock:
            self.guard._match(body)
            if (self.mux.active or not self.mux.teleop.stop_latched or self.guard.armed or
                    self.activation.receipt(body.get('authorization_id')) is None):
                raise ValueError('Register constraint only in this exact observed STOP before explicit begin')
            self.action_constraint={'constraint':selected,'authorization_id':body['authorization_id'],
                'registration_id':uuid.uuid4().hex,'boot_id':self.mux.tap.boot_id,'clock_domain':self.mux.tap.clock_domain,
                'registered_native_ns':self.mux.tap.clock(),'native_command_sent':False,'controller_settings_written':False}
            return deepcopy(self.action_constraint)
    def begin(self,body):
        if self.guard.parked is not None and not self.phases.in_transition():raise ValueError('Explicit parked-phase begin required')
        if self.guard.journal.fault is not None or self.guard.journal.recovery_required:raise ValueError('Durable giver safe-object recovery required before native begin')
        if self.activation.status().get('input_mode')!='cooperative_policy':
            raise ValueError('Separate observed cooperative_policy authorization required')
        self.guard._match(body)
        model=body.get('model') or {}
        identity(model.get('model_id'))
        if (not model.get('model_id') or any(not isinstance(model.get(key),str) or not re.fullmatch('[0-9a-f]{64}',model[key])
                for key in ('checkpoint_sha256','spec_sha256'))):raise ValueError('Pinned real policy/model source required')
        registered=self.action_constraint
        if registered is not None:
            verify_constraint(registered['constraint'],binding=body,model=model)
            if registered['authorization_id']!=body.get('authorization_id'):raise ValueError('Constraint belongs to another observed authorization')
        self.reference()  # Pure existing same-boot reference guard; no HOME.
        with self.mux.teleop.lock:
            with self.guard.lock:
                self.guard._match(body)
                if self.action_constraint is not registered:raise ValueError('Constraint changed before native begin')
                result=self.mux.begin(observing=body.get('observing'),neutral=body.get('neutral'))
                self.mux.source_mode='cooperative_policy'
                self.guard.bind_control_epoch(result['control_epoch']);self.policy=deepcopy(model)
        return result
    def recenter(self,body):
        if self.guard.parked is not None:raise ValueError('Explicit parked-phase transition required')
        if self.guard.journal.fault is not None or self.guard.journal.recovery_required:raise ValueError('Durable giver safe-object recovery required before native recenter')
        self.guard._match(body);result=self.mux.recenter(body.get('control_epoch'))
        self.guard.bind_control_epoch(result['control_epoch']);return result
    def lease(self,body):
        self.guard._current();self.guard._match(body)
        return self.mux.keyboard_base_input(body.get('control_epoch'),{},observing=body.get('observing'))
    def stop(self,body):
        self.guard._match(body)
        if body.get('control_epoch') is not None:
            self.mux._check(body.get('control_epoch'))
            self.guard.revoke('EXPLICIT_COOPERATIVE_STOP');return self.mux.stop(body['control_epoch'])
        with self.mux.teleop.lock:
            self.guard._match(body);bound=self.guard.binding
            current=self.activation.current_authorization_id()
            if (self.mux.active or self.mux.teleop.owner!='keyboard' or
                    type(body.get('expected_native_generation')) is not int or
                    body['expected_native_generation']!=self.mux.teleop.generation or
                    body['expected_native_generation']!=bound['native_generation'] or
                    body.get('authorization_id')!=bound['authorization_id'] or
                    current not in (None,bound['authorization_id'])):
                raise ValueError('Old manual STOP cannot affect another native generation/authorization')
            self.guard.revoke('EXPLICIT_SCOPED_MANUAL_COOPERATIVE_STOP')
            return self.mux.teleop.stop()
    def input(self,body):
        if self.guard.parked is not None or self.guard.journal.pending:raise ValueError('NOT_READY_GIVER_PHASE_PARKED_OR_SEAL_PENDING')
        if not isinstance(body.get('action9'),list) or any(type(value) not in (int,float) for value in body['action9']):
            raise ValueError('Explicit finite numeric native action9 required')
        action=action_vector(body.get('action9',[]));mux=self.mux;epoch=body.get('control_epoch');projection=None
        registered=self.action_constraint
        # Source JSON/hash validation stays outside the original control lock.
        # The immutable object identity is fenced again at the native boundary.
        if registered is not None or body.get('coordinator_action_projection') is not None:
            if registered is None or registered['authorization_id']!=body.get('authorization_id'):
                raise ValueError('Native registered same-authorization coordinator constraint required')
            selected=verify_constraint(registered['constraint'],binding=body,model=body.get('model'))
            projected,projection=project_stationary_action(body.get('raw_policy_action9'),selected)
            if action!=projected or body.get('coordinator_action_projection')!=projection:
                raise ValueError('Raw9/projected requested9/registered constraint receipt differs')
        command_id=identity(body.get('command_id'))
        with mux.teleop.lock:
            if self.action_constraint is not registered:raise ValueError('Constraint changed before native input')
            self.guard._current();self.guard._match(body);mux._check(epoch)
            if (mux.source_mode!='cooperative_policy' or self.policy is None or body.get('model')!=self.policy or mux.state!='ACTIVE' or
                    mux.clock()>=mux.teleop.lease or mux.clock()>=mux.intent_deadline):
                raise ValueError('Current observed native policy stream/lease/model required')
            sequence=body.get('source_sequence')
            if type(sequence) is not int or sequence<=mux.sequence:raise ValueError('Old policy prediction dropped')
            if any(abs(value)>1e-12 for value in action[6:]):
                raise ValueError('Initial cooperative skill requires actual issued neutral base; nonzero model base rejected, never masked')
            observation=body.get('source_observation') or {}
            _,retained=self.observation_frame(observation.get('observation_id'))
            if (observation.get('state_source')!='command_estimate' or observation.get('boot_id')!=mux.tap.boot_id or
                    observation.get('clock_domain')!=mux.tap.clock_domain or not observation.get('frame_id') or
                    not re.fullmatch('[0-9a-f]{64}',str(observation.get('jpeg_sha256',''))) or
                    type(observation.get('monotonic_ns')) is not int or
                    not 0<=mux.tap.clock()-observation['monotonic_ns']<=2_000_000_000 or observation!=retained):
                raise ValueError('Fresh actual native source observation/command estimate identity required')
            self.reference()  # Guard validity/fault only, never journal pose.
            current=mux.tap.issued_snapshot()['command9'][:6]
            if len(self.limits)!=6 or any(not low<=target<=high for target,(low,high) in zip(action[:6],self.limits)):
                raise ValueError('Policy target outside unchanged original servo limits')
            horizon=mux.arm.motion_config.get('streaming',{}).get('lookahead_s')
            if not isinstance(horizon,(int,float)) or not math.isfinite(horizon) or horizon<=0:
                raise ValueError('Original Ruckig lookahead unavailable')
            for shape in (0.,-.2,-.4,-.6,-.8):
                if not mux.arm.model().path(current[:5],action[:5],shape)['valid']:
                    raise ValueError('Policy target collides with original robot/floor geometry')
            velocities=[(target-prior)/horizon for target,prior in zip(action[:6],current)]
            source={'input_adapter':{'id':'cooperative_policy','version':'1','input_mode':'cooperative_policy'},
                'state_source':'policy_prediction_not_measured_leader','policy':deepcopy(self.policy),
                'cooperative':deepcopy(self.guard.binding),'command_id':command_id,
                'source_observation':deepcopy(observation),'requested_target_action9':action,
                'source_target_sha256':digest(action),'received_target_monotonic_ns':mux.tap.clock(),
                'target_clock_domain':mux.tap.clock_domain,'target_boot_id':mux.tap.boot_id,
                'conversion':'target_minus_current_command_estimate_divided_by_original_streaming_lookahead',
                'measured_feedback_claimed':False,'action_contract':deepcopy(ROLE_CONTRACTS['giver'])}
            if projection is not None:source.update(raw_policy_action9=deepcopy(projection['raw_policy_action9']),
                raw_policy_action9_sha256=projection['raw_policy_action9_sha256'],
                coordinator_action_projection=deepcopy(projection),native_constraint_registration=deepcopy(registered))
            # This is the original real base request, not zeros invented from a
            # stationary-mode flag. It does not refresh the observation lease.
            mux._base_drive(action[6:])
            mux.latest,mux.sequence,mux.latest_source=velocities,sequence,source;mux.intent_revision+=1
            mux.intent_deadline=mux.teleop.lease
            try:result=mux._arm_update({},False)
            except Exception:
                mux.latest,mux.state=[0.]*6,'POLICY_INPUT_FAILED_RECENTER_REQUIRED';mux.intent_revision+=1
                raise
            if result.get('accepted') is not True:
                mux.latest,mux.state=[0.]*6,'POLICY_INPUT_NOT_ACCEPTED_RECENTER_REQUIRED';mux.intent_revision+=1
            return {'accepted':result.get('accepted') is True,'native_dispatched':False,'latest_intent':True,
                    'command_id':body['command_id'],'control_epoch':epoch,'source_sequence':sequence}
    def status(self):
        return {'schema':'explorer_native_cooperative_owner_v1','policy':deepcopy(self.policy),
            'registered_action_constraint':deepcopy(self.action_constraint),
            'capabilities':{'stationary_base_constraint_schema':'explorer_cooperative_stationary_base_constraint_v1',
                'raw_policy9_projection_provenance_required_when_registered':True,
                'explicit_user_prepare':True,'explicit_user_manual_resume':True,'scoped_manual_stop':True,
                'explicit_user_giver_phase_http':self.phases.http_mounted,
                'explicit_user_parked_release_http':self.parked_release.http_mounted,
                'parked_observation_scope_http':self.held_scope.http_mounted},
            'release_boundary':self.guard.status(),'native':self.mux.status(),'same_original_stream_worker':True,
            'phase_lifecycle':self.phases.status(),
            'controller_settings_written':False,'native_dispatch_proof':'read actual correlated dispatch ring',
            'automatic_lease_heartbeat':False,'physical_qualification':False}


def mount_native_cooperative_api(app,owner,*,authenticate,allowed,operation,activation):
    from fastapi import Request,HTTPException
    from fastapi.responses import Response
    original_authenticate,original_allowed=authenticate,allowed
    def authenticate(request):
        original_authenticate(request)
        if request.url.query:raise HTTPException(400,'Fixed cooperative native API takes no query parameters')
    def allowed(request,body):
        authenticate(request);return original_allowed(request,body)
    def phase_actor(request,body,*,park=False):
        # The private authenticated service already minted this source USER
        # authorization. Never accept a replacement actor from this payload.
        authenticate(request)
        if 'actor_id' in body:raise HTTPException(409,'Phase actor is resolved from the authenticated source authorization, never this payload')
        receipt=activation.receipt(body.get('authorization_id')) if activation is not None else None
        if park and receipt is None:receipt=(owner.guard.binding or {}).get('observed_source_authorization')
        actor=(receipt or {}).get('actor_id')
        if not isinstance(actor,str) or not 0<len(actor)<=128:raise HTTPException(409,'Actual source USER actor unavailable')
        if not park:allowed(request,body)
        return actor
    prefix='/api/explorer/cooperative/'
    @app.get(prefix+'status')
    def status(request:Request):authenticate(request);return owner.status()
    @app.get(prefix+'phase/held-status')
    def held_status(request:Request):authenticate(request);return operation(owner.held_scope.snapshot)
    owner.held_scope.http_mounted=True
    @app.get(prefix+'observation')
    def observation(request:Request):authenticate(request);return operation(owner.observation)
    @app.get(prefix+'observations/{observation_id}/frame')
    def frame(observation_id:str,request:Request):
        authenticate(request);jpeg,source=operation(owner.observation_frame,observation_id)
        return Response(jpeg,media_type='image/jpeg',headers={'Cache-Control':'no-store',
            'X-JPEG-SHA256':source['jpeg_sha256'],'X-Frame-ID':source['frame_id'],
            'X-Monotonic-NS':str(source['monotonic_ns']),'X-Boot-ID':source['boot_id'],
            'X-Clock-Domain':source['clock_domain']})
    @app.post(prefix+'bind')
    def bind(request:Request,body:dict):allowed(request,body);return operation(owner.guard.bind,body)
    @app.post(prefix+'prepare')
    def prepare(request:Request,body:dict):allowed(request,body);return operation(owner.prepare,body)
    @app.post(prefix+'manual/resume')
    def manual_resume(request:Request,body:dict):allowed(request,body);return operation(owner.manual_resume,body)
    @app.post(prefix+'constraint/register')
    def register_constraint(request:Request,body:dict):allowed(request,body);return operation(owner.register_constraint,body)
    @app.post(prefix+'begin')
    def begin(request:Request,body:dict):
        allowed(request,body);return operation(activation.transition,lambda:owner.begin(body),body.get('authorization_id'))
    @app.post(prefix+'recenter')
    def recenter(request:Request,body:dict):
        allowed(request,body);return operation(activation.transition,lambda:owner.recenter(body),body.get('authorization_id'))
    @app.post(prefix+'resume')
    def resume(request:Request,body:dict):
        allowed(request,body);operation(owner.guard._match,body)
        if owner.guard.parked is not None:raise HTTPException(409,'Explicit parked-phase resume required')
        return operation(owner.mux.resume,body.get('control_epoch'),observing=body.get('observing'))
    @app.post(prefix+'phase/park')
    def phase_park(request:Request,body:dict):
        actor=phase_actor(request,body,park=True);return operation(owner.park,body,authenticated_actor=actor)
    @app.post(prefix+'phase/adopt')
    def phase_adopt(request:Request,body:dict):
        actor=phase_actor(request,body);return operation(owner.adopt_parked,body,authenticated_actor=actor)
    @app.post(prefix+'phase/begin')
    def phase_begin(request:Request,body:dict):
        actor=phase_actor(request,body);return operation(owner.begin_parked,body,authenticated_actor=actor)
    @app.post(prefix+'phase/resume')
    def phase_resume(request:Request,body:dict):
        actor=phase_actor(request,body);return operation(owner.resume_parked,body,authenticated_actor=actor)
    owner.phases.http_mounted=True
    @app.post(prefix+'phase/release/challenge')
    def parked_release_challenge(request:Request,body:dict):
        actor=phase_actor(request,body);return operation(owner.parked_release_challenge,body,authenticated_actor=actor)
    @app.post(prefix+'phase/resume-release')
    def resume_parked_release(request:Request,body:dict):
        actor=phase_actor(request,body);return operation(owner.resume_parked_release,body,authenticated_actor=actor)
    owner.parked_release.http_mounted=True
    @app.post(prefix+'lease')
    def lease(request:Request,body:dict):allowed(request,body);return operation(owner.lease,body)
    @app.post(prefix+'input')
    def source_input(request:Request,body:dict):allowed(request,body);return operation(owner.input,body)
    @app.post(prefix+'guard/arm')
    def arm(request:Request,body:dict):
        allowed(request,body);return operation(owner.guard.arm,body,operation(owner.reference))
    @app.post(prefix+'guard/challenge')
    def challenge(request:Request,body:dict):allowed(request,body);return operation(owner.guard.challenge,body)
    @app.post(prefix+'guard/authorize')
    def authorize(request:Request,body:dict):allowed(request,body);return operation(owner.guard.authorize,body)
    @app.post(prefix+'guard/reset')
    def reset(request:Request,body:dict):allowed(request,body);return operation(owner.guard.reset_safe,body)
    @app.post(prefix+'guard/revoke')
    def revoke(request:Request,body:dict):
        authenticate(request);operation(owner.guard._match,body);return owner.guard.revoke('EXPLICIT_COORDINATOR_REVOKE')
    @app.post(prefix+'stop')
    def stop(request:Request,body:dict):authenticate(request);return operation(owner.stop,body)
