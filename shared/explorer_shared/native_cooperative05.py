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

from .cooperative_contract05 import digest,ROLE_CONTRACTS
from .contracts import action_vector
from .handover import HandoverFSM


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


class NativeReceiverHoldBoundary:
    def __init__(self,mux,activation,*,clock=None):
        self.mux,self.activation=mux,activation;self.clock=clock or mux.tap.clock
        self.lock=threading.RLock();self.binding=None;self.armed=False;self.pending=None;self.grant=None
        self.revision=0;self.used=set();self.last_pose=None;self.last_publication=None;self.events=[]
        self.reference_fault=None
        self.ttl_ns=HandoverFSM('native-cooperative-hold').hold_ttl_ns
    def _current(self):
        binding=self.binding
        if (binding is None or self.mux.teleop.owner!='keyboard' or self.mux.teleop.generation!=binding['native_generation'] or
                self.mux.teleop.stop_latched or not self.mux.clock()<self.mux.teleop.lease or
                not self.activation.allowed() or self.activation.receipt(binding['authorization_id']) is None):
            raise ValueError('Current original native owner/observation lease required')
        if binding.get('native_control_epoch') is not None and (
                not self.mux.active or self.mux.epoch!=binding['native_control_epoch']):raise ValueError('Native cooperative epoch changed')
        return binding
    def bind(self,body):
        self.activation.require(body.get('authorization_id'))
        for key in ('run_id','object_id','coordinator_epoch'):identity(body.get(key))
        if body.get('observing') is not True:raise ValueError('Explicit current observed cooperative binding required')
        with self.mux.teleop.lock:
            if not self.mux.teleop.stop_latched:raise ValueError('Original STOP required before cooperative metadata binding')
            with self.lock:
                if self.armed:raise ValueError('Explicit observed safe-object reset required before replacing dual-hold protection')
                self.revision+=1;self.pending=self.grant=None;self.last_pose=None
                self.binding={key:body[key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')}
                self.binding.update(native_generation=self.mux.teleop.generation,native_control_epoch=None)
        return self.status()
    def bind_control_epoch(self,epoch):
        with self.lock:
            if self.binding is None:raise ValueError('Observed cooperative binding required')
            self.revision+=1;self.pending=self.grant=None
            self.binding.update(native_generation=self.mux.teleop.generation,native_control_epoch=epoch)
    def _match(self,body):
        current=self.binding
        if current is None or any(body.get(key)!=current[key] for key in ('run_id','object_id','coordinator_epoch')):
            raise ValueError('Exact current cooperative run/object/epoch required')
        return current
    def arm(self,body,reference):
        self._current();self._match(body)
        values=reference.get('servo_deg') if isinstance(reference,dict) else None
        if (reference.get('boot_id')!=self.mux.tap.boot_id or reference.get('measured') is not False or
                not isinstance(values,list) or len(values)!=6 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in values)):
            raise ValueError('Original same-boot command estimate required; not measured feedback')
        with self.lock:
            self._current();self._match(body);self.revision+=1;self.pending=self.grant=None
            actual=self.mux.tap.issued_snapshot()
            self.armed=True;self.last_pose=list(actual['command9'][:6])
        return self.status()
    def challenge(self,body):
        self._current();self._match(body);identity(body.get('after_receiver_command_id'))
        with self.lock:
            self._current();self._match(body)
            if not self.armed:raise ValueError('Dual-hold publication protection must be armed first')
            self.revision+=1;self.grant=None
            self.pending={'schema':'explorer_native_receiver_hold_challenge_v1','challenge_id':uuid.uuid4().hex,
                **deepcopy(self.binding),'after_receiver_command_id':body['after_receiver_command_id'],
                'issued_native_ns':self.clock(),'native_clock_domain':self.mux.tap.clock_domain,
                'native_boot_id':self.mux.tap.boot_id,'revision':self.revision}
            self.pending['expires_native_ns']=self.pending['issued_native_ns']+self.ttl_ns
            return deepcopy(self.pending)
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
            self.used.add(receipt_id)
            self.grant={'grant_id':uuid.uuid4().hex,'challenge':deepcopy(challenge),'operator_confirmation':deepcopy(proof),
                'expires_native_ns':challenge['expires_native_ns'],'revision':self.revision,
                'clock_conversion_assumed':False,'force_or_attainment_measured':False}
            self.pending=None;return deepcopy(self.grant)
    def revoke(self,reason):
        # Increment before waiting on an already-started bounded publication.
        # This never emits HOLD/open/HOME or changes a controller constant.
        self.revision+=1
        with self.lock:self.pending=self.grant=None
        return {'revoked':True,'reason':reason,'dual_hold_protection_remains_armed':self.armed}
    def reset_safe(self,body):
        self.activation.require(body.get('authorization_id'))
        with self.mux.teleop.lock:
            if (body.get('observing') is not True or body.get('object_safe_no_dual_hold') is not True or
                    not self.mux.teleop.stop_latched or self.activation.receipt(body.get('authorization_id')) is None):
                raise ValueError('Explicit observed safe-object reset under original STOP required')
            with self.lock:
                self.revision+=1;self.binding=self.pending=self.grant=None;self.armed=False;self.last_pose=None
        return self.status()
    def publish(self,pose,callback):
        with self.lock:
            if not self.armed:self.last_publication=None
            try:
                if self.armed and self.reference_fault is not None:
                    raise ValueError('OBSERVED_FACTORY_REFERENCE_REQUIRED_AFTER_BLOCKED_PUBLICATION')
                if self.armed and (self.last_pose is None or any(pose[index]!=self.last_pose[index] for index in range(5))):
                    raise ValueError('DUAL_HOLD_REQUIRES_STATIONARY_GIVER_ARM_NO_RELATIVE_MOTION')
                opening=self.armed and (self.last_pose is None or pose[5]<self.last_pose[5])
                if opening:
                    self._current();grant=self.grant
                    if (grant is None or grant['revision']!=self.revision or
                            not grant['challenge']['issued_native_ns']<=self.clock()<grant['expires_native_ns']):
                        raise ValueError('NO_FRESH_NATIVE_RECEIVER_HOLD_AT_OPENING_PUBLICATION')
            except ValueError as error:
                self._publication_failed(str(error));raise
            started=self.clock();revision=self.revision
            try:result=callback()  # Same original Publisher, serialized transaction.
            except Exception:
                if self.armed:self._publication_failed('ORIGINAL_PUBLISHER_RETURN_UNKNOWN')
                raise
            finished=self.clock()
            if self.armed:
                receipt={'schema':'explorer_native_cooperative_publication_v1','receipt_id':uuid.uuid4().hex,
                    'binding':deepcopy(self.binding),'requested_servo_deg':list(pose),'opening':opening,
                    'guard_grant':deepcopy(self.grant) if opening else None,'guard_revision':revision,
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
            'reference_fault':deepcopy(self.reference_fault),
            'controller_settings_written':False,'new_publishers':0,'physical_qualification':False}


class NativeCooperativeOwner:
    def __init__(self,mux,activation,reference_reader,*,target_limits):
        self.mux,self.activation,self.reference=mux,activation,reference_reader
        self.limits=target_limits;self.guard=NativeReceiverHoldBoundary(mux,activation);self.policy=None
        self.frames=OrderedDict();self.frame_lock=threading.Lock()
        self.original_publisher=mux.arm.pub
        mux.arm.pub=ExistingPublisherBoundary(self.original_publisher,self.guard)
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
    def begin(self,body):
        if self.activation.status().get('input_mode')!='cooperative_policy':
            raise ValueError('Separate observed cooperative_policy authorization required')
        self.guard._match(body)
        model=body.get('model') or {}
        identity(model.get('model_id'))
        if (not model.get('model_id') or any(not isinstance(model.get(key),str) or not re.fullmatch('[0-9a-f]{64}',model[key])
                for key in ('checkpoint_sha256','spec_sha256'))):raise ValueError('Pinned real policy/model source required')
        self.reference()  # Pure existing same-boot reference guard; no HOME.
        with self.mux.teleop.lock:
            with self.guard.lock:
                self.guard._match(body)
                result=self.mux.begin(observing=body.get('observing'),neutral=body.get('neutral'))
                self.mux.source_mode='cooperative_policy'
                self.guard.bind_control_epoch(result['control_epoch']);self.policy=deepcopy(model)
        return result
    def recenter(self,body):
        self.guard._match(body);result=self.mux.recenter(body.get('control_epoch'))
        self.guard.bind_control_epoch(result['control_epoch']);return result
    def lease(self,body):
        self.guard._current();self.guard._match(body)
        return self.mux.keyboard_base_input(body.get('control_epoch'),{},observing=body.get('observing'))
    def stop(self,body):
        self.guard._match(body);self.mux._check(body.get('control_epoch'))
        self.guard.revoke('EXPLICIT_COOPERATIVE_STOP');return self.mux.stop(body['control_epoch'])
    def input(self,body):
        if not isinstance(body.get('action9'),list) or any(type(value) not in (int,float) for value in body['action9']):
            raise ValueError('Explicit finite numeric native action9 required')
        action=action_vector(body.get('action9',[]));mux=self.mux;epoch=body.get('control_epoch')
        command_id=identity(body.get('command_id'))
        with mux.teleop.lock:
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
            'release_boundary':self.guard.status(),'native':self.mux.status(),'same_original_stream_worker':True,
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
    prefix='/api/explorer/cooperative/'
    @app.get(prefix+'status')
    def status(request:Request):authenticate(request);return owner.status()
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
    @app.post(prefix+'begin')
    def begin(request:Request,body:dict):
        allowed(request,body);return operation(activation.transition,lambda:owner.begin(body),body.get('authorization_id'))
    @app.post(prefix+'recenter')
    def recenter(request:Request,body:dict):
        allowed(request,body);return operation(activation.transition,lambda:owner.recenter(body),body.get('authorization_id'))
    @app.post(prefix+'resume')
    def resume(request:Request,body:dict):
        allowed(request,body);operation(owner.guard._match,body)
        return operation(owner.mux.resume,body.get('control_epoch'),observing=body.get('observing'))
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
