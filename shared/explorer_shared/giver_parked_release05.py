"""ENGINEERING_GLUE: parked hold challenge and explicit bounded resume/release.

The human confirms AFTER delivery of the actual source challenge. Its original
native issue/expiry times are never reset. Only a separate USER control resumes
the same original owner and installs the consumed proof before unpark commits.
No servo target, heartbeat, model output, feedback or controller setting here.
"""
from copy import deepcopy
import re
import uuid

from .contracts import digest


class GiverParkedRelease:
    def __init__(self,owner):
        self.owner,self.phases,self.guard,self.mux=owner,owner.phases,owner.guard,owner.mux
        self.http_mounted=False
    @staticmethod
    def _id(value):
        if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}',value):
            raise ValueError('Exact source-bound release transaction identity required')
        return value
    def _body(self,body,extra):
        keys={'run_id','object_id','coordinator_epoch','authorization_id','expected_state_id','expected_revision',
            'observing','neutral','user_observed_test_confirmed','phase_id','park_request_id','control_epoch'}
        if not isinstance(body,dict) or set(body)-keys-set(extra):raise ValueError('Fixed parked release fields required')
        return {key:deepcopy(value) for key,value in body.items() if key in keys}
    def _current(self,body,actor):
        receipt,parked=self.phases._current(body,actor,parked_stage={'PARKED_NEW_USER','BEGIN_CLUTCHED'})
        if (not self.guard.armed or self.guard.reference_fault is not None or
                parked['previous_input_mode']=='cooperative_policy' and
                    (parked['stage']!='BEGIN_CLUTCHED' or body.get('control_epoch')!=parked.get('control_epoch'))):
            raise ValueError('Armed exact parked manual/CLUTCHED policy context required')
        return receipt,parked,self.phases._origin(parked)
    def _context(self,origin):
        actual=origin['actual_issued']
        return {'command9':deepcopy(actual['command9']),
            'arm_receipt_sha256':digest(actual['arm_receipt']),'base_receipt_sha256':digest(actual['base_receipt']),
            'original_reference_sha256':origin['original_reference_sha256'],
            'native_clock_domain':self.mux.tap.clock_domain,'native_boot_id':self.mux.tap.boot_id,
            'state_source':'command_estimate','measured_feedback':None,'measured_feedback_mask':[False]*6,
            'measured_hold_or_stationarity_claimed':False}
    def challenge(self,body,*,authenticated_actor):
        base=self._body(body,{'release_request_id','after_receiver_command_id'})
        request=self._id(body.get('release_request_id'));after=self._id(body.get('after_receiver_command_id'))
        with self.guard.metadata_lock:
            receipt,parked,origin=self._current(base,authenticated_actor)
            initial_revision=self.guard.revision
            def prepare():
                with self.guard.lock:
                    self.phases._stable_origin(origin)
                    if (self.guard.revision!=initial_revision or self.guard.parked!=parked or
                            self.owner.activation.current_authorization_id()!=base['authorization_id'] or
                            self.owner.activation.receipt(base['authorization_id'])!=receipt):
                        raise ValueError('Source USER/context changed before parked challenge')
                    revision=self.guard._bump_revision();issued=self.guard.clock()
                    challenge={'schema':'explorer_native_parked_receiver_hold_challenge_v1','challenge_id':uuid.uuid4().hex,
                        **deepcopy(self.guard.binding),'release_request_id':request,'park_request_id':parked['park_request_id'],
                        'after_receiver_command_id':after,'revision':revision,'issued_native_ns':issued,
                        'expires_native_ns':issued+self.guard.ttl_ns,'native_clock_domain':self.mux.tap.clock_domain,
                        'native_boot_id':self.mux.tap.boot_id,'source_context':self._context(origin),
                        'original_origin_bytes_sha256':digest(origin),'source_snapshot_stamp':deepcopy(origin['actual_issued']['stamp']),
                        'source_control_epoch':self.guard.binding.get('native_control_epoch'),
                        'source_native_generation':self.guard.binding['native_generation'],
                        'actor_id':authenticated_actor,'native_command_called':False,'execution_authority':False}
                    self.guard.grant=None;self.guard.pending=deepcopy(challenge)
                    self.phases._retain(self.phases._event('PARKED_RECEIVER_HOLD_CHALLENGE',challenge=challenge,native_command_called=False))
                    return deepcopy(challenge)
            result=self.guard._durable('parked_receiver_hold_challenge_no_control',prepare)
            self.owner.activation.require(base['authorization_id'])
            if self.guard.pending!=result or not result['issued_native_ns']<=self.guard.clock()<result['expires_native_ns']:
                raise ValueError('Parked hold challenge expired/revoked before response')
            return {'challenge':result,'native_command_called':False,'execution_authority':False,'interlock':self.guard.status()}
    def _proofs(self,body,challenge,actor):
        proofs=body.get('operator_confirmations')
        if (not isinstance(proofs,dict) or set(proofs)!={'receiver_holds_object','giver_stationary','receiver_stationary'} or
                body.get('coordinator_verified_confirmation_created_after_challenge') is not True):
            raise ValueError('Three independently affirmed post-challenge USER facts required')
        received=body.get('challenge_received_coordinator_ns');checked=body.get('proofs_checked_coordinator_ns');domain=body.get('coordinator_clock_domain')
        if (type(received) is not int or type(checked) is not int or not 0<=received<=checked or
                not isinstance(domain,str) or not domain or domain==challenge['native_clock_domain']):
            raise ValueError('Actual same-coordinator challenge/proof receive context required')
        ids=set()
        for statement,proof in proofs.items():
            expected_role='giver' if statement=='giver_stationary' else 'receiver'
            if (not isinstance(proof,dict) or proof.get('statement')!=statement or proof.get('role')!=expected_role or
                    proof.get('run_id')!=challenge['run_id'] or proof.get('object_id')!=challenge['object_id'] or
                    proof.get('authenticated_actor')!=actor or proof.get('state')!='consumed' or
                    proof.get('affirmed') is not True or proof.get('observing') is not True or proof.get('clock_domain')!=domain or
                    any(type(proof.get(key)) is not int for key in ('created_ns','received_ns','consumed_ns')) or
                    not received<=proof['created_ns']<=proof['received_ns']<=proof['consumed_ns']<=checked or
                    checked-proof['received_ns']>self.guard.ttl_ns):
                raise ValueError('Actual consumed fresh same-actor post-challenge proof required')
            identity=self._id(proof.get('challenge_id'))
            if identity in ids or identity in self.guard.used:raise ValueError('Post-challenge proof was reused')
            ids.add(identity)
            if expected_role=='receiver' and proof.get('after_command_id')!=challenge['after_receiver_command_id']:
                raise ValueError('Receiver facts must follow the exact actual receiver command')
        if len(self.guard.used|ids)>2048:raise ValueError('Retained native receiver-proof budget reached')
        return deepcopy(proofs),ids
    def resume_release(self,body,*,authenticated_actor):
        base=self._body(body,{'release_request_id','challenge_id','operator_confirmations','coordinator_clock_domain',
            'challenge_received_coordinator_ns','proofs_checked_coordinator_ns','coordinator_verified_confirmation_created_after_challenge'})
        with self.guard.metadata_lock:
            receipt,parked,origin=self._current(base,authenticated_actor);challenge=deepcopy(self.guard.pending)
            if (not isinstance(challenge,dict) or challenge.get('schema')!='explorer_native_parked_receiver_hold_challenge_v1' or
                    challenge.get('challenge_id')!=body.get('challenge_id') or challenge.get('release_request_id')!=body.get('release_request_id') or
                    challenge.get('park_request_id')!=parked['park_request_id'] or challenge.get('actor_id')!=authenticated_actor or
                    challenge.get('revision')!=self.guard.revision or challenge.get('source_context')!=self._context(origin) or
                    challenge.get('source_control_epoch')!=self.guard.binding.get('native_control_epoch') or
                    challenge.get('source_native_generation')!=self.guard.binding['native_generation'] or
                    not challenge['issued_native_ns']<=self.guard.clock()<challenge['expires_native_ns']):
                raise ValueError('Exact unchanged live parked release challenge required')
            proofs,proof_ids=self._proofs(body,challenge,authenticated_actor);installed={}
            def transition(stage,current_origin,current_parked,native,previous_revision):
                if stage=='before_resume':
                    if (self.guard.pending!=challenge or self.guard.revision!=challenge['revision'] or
                            self._context(current_origin)!=self._context(origin) or current_parked!=parked or
                            not challenge['issued_native_ns']<=self.guard.clock()<challenge['expires_native_ns']):
                        raise ValueError('Source challenge changed before explicit original resume')
                    self._proofs(body,challenge,authenticated_actor)
                else:
                    actual=self.mux.tap.issued_snapshot()
                    if (self.guard.revision!=previous_revision+1 or previous_revision!=challenge['revision'] or
                            self.owner.activation.current_authorization_id()!=base['authorization_id'] or
                            self.owner.activation.receipt(base['authorization_id'])!=receipt or
                            self.guard.binding.get('native_control_epoch')!=challenge['source_control_epoch'] or
                            self.guard.binding['native_generation']!=challenge['source_native_generation'] or
                            actual['arm_receipt']!=origin['actual_issued']['arm_receipt'] or
                            actual['command9']!=origin['actual_issued']['command9'] or actual['command9'][6:]!=[0.,0.,0.] or
                            not challenge['issued_native_ns']<=self.guard.clock()<challenge['expires_native_ns']):
                        raise ValueError('Explicit resume context/revoke/expiry changed before opening grant')
                    with self.guard.revision_lock:
                        if self.guard.revision!=previous_revision+1:raise ValueError('Opening grant revoked before atomic installation')
                        self.guard.used.update(proof_ids)
                        grant={'grant_id':uuid.uuid4().hex,'challenge':deepcopy(challenge),
                            'operator_confirmation':deepcopy(proofs['receiver_holds_object']),
                            'stationary_confirmations':{key:deepcopy(proofs[key]) for key in ('giver_stationary','receiver_stationary')},
                            'expires_native_ns':challenge['expires_native_ns'],'revision':self.guard.revision,
                            'source_resume_lineage':{'challenge_sha256':digest(challenge),'previous_revision':previous_revision,
                                'resumed_revision':self.guard.revision,'same_original_native_epoch':challenge['source_control_epoch'],
                                'after_resume_command9':deepcopy(actual['command9']),
                                'unchanged_arm_receipt_sha256':digest(actual['arm_receipt']),
                                'after_resume_original_base_receipt':deepcopy(actual['base_receipt'])},
                            'clock_conversion_assumed':False,'force_or_attainment_measured':False}
                        self.guard.grant=deepcopy(grant);installed.update(grant)
                    self.phases._retain(self.phases._event('EXPLICIT_USER_PARKED_RELEASE_GRANT',grant=grant,native_command_called=False))
            result=self.phases._resume(base,authenticated_actor=authenticated_actor,metadata_transition=transition)
            if self.guard.grant!=installed or not challenge['issued_native_ns']<=self.guard.clock()<challenge['expires_native_ns']:
                epoch=challenge['source_control_epoch']
                self.guard.revoke('PARKED_RELEASE_EXPIRED_DURING_FINAL_PERSISTENCE')
                self.phases._cleanup(epoch,base['authorization_id'],None if epoch is not None else self.guard.binding['native_generation'])
                raise ValueError('Opening grant expired/revoked during final persistence')
            return {**result,'release_grant':deepcopy(installed),'old_native_challenge_times_retained':True,
                'actual_arm_opening_command_sent':False,'automatic_lease_renewal':False}
