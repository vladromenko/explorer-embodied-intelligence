"""ENGINEERING_GLUE: separate explicit USER parked release CONTROL transaction.

Questions follow an actual source challenge. Only the explicit user operation
consumes the three facts and resumes the original owner; the waiting workflow
never commands or claims the bundle itself. No targets, automatic leases or
model inference occur here. Parsed source effects retain immutable custody.
"""
from collections import OrderedDict
from copy import deepcopy
import math
import re
import threading
import uuid

from explorer_shared.contracts import digest
from explorer_shared.parked_release_transport05 import InstalledGrantGiverPolicyTransport,InstalledGrantGiverManualTransport

STATEMENTS={'receiver_holds_object','giver_stationary','receiver_stationary'}


def _same_vector(value,expected):
    return (isinstance(value,list) and len(value)==len(expected) and
        all(type(item) in (float,int) and math.isfinite(item) for item in value) and value==expected)


class CooperativeParkedRelease:
    def __init__(self,service):
        self.service=service;self.clock=service.clock;self.condition=threading.Condition(threading.RLock());self.records=OrderedDict()
    def _fresh(self,row,spec,actor):return self.service.native_controls._fresh(row,spec,actor)
    def _intent(self,row,spec):
        pending=row.get('pending_operation') or {}
        return {'context':self.service.observed_setup._context(row,spec),
            'initial_binding_sha256':digest(row.get('observed_test_binding')),
            'lineage_sha256':digest(row.get('native_grant_successors')),
            'protocol_state':row['protocol_state'],'operation_id':pending.get('operation_id')}
    def _current_record(self,row,identity,actor):
        with self.condition:record=deepcopy(self.records.get(identity))
        if not record or record['actor']!=actor or record['state'] in ('CANCELLED','FAILED'):
            raise ValueError('CURRENT_PROCESS_SINGLE_USE_PARKED_RELEASE_TRANSACTION_REQUIRED')
        current=self._fresh(row,record['spec'],actor);pending=current.get('pending_operation') or {}
        if (self._intent(current,record['spec'])!=record['intent'] or pending.get('event')!='release_requested' or
                pending.get('native_release_challenge')!=record['descriptor'] or
                record['descriptor']['release_bundle_id']!=identity or self.clock()>=record['descriptor']['deadline_nx_ns']):
            raise ValueError('PARKED_RELEASE_CONTEXT_PHASE_OPERATION_OR_ORIGINAL_DEADLINE_CHANGED')
        return record,current
    def _receiver_support(self,row,spec,actor):
        scopes=self.service.observed_scopes.refresh(row,spec,actor=actor)
        if scopes['roles']['receiver'].get('current') is True:
            if scopes['roles']['receiver']['facts'].get('armed') is not True or scopes['roles']['receiver']['facts'].get('direction_current') is not True:
                raise ValueError('CURRENT_ARMED_RECEIVER_INTERLOCK_AND_DIRECTION_REQUIRED')
            return scopes['roles']['receiver']
        transport=self.service.transports.get((row['session_id'],'receiver'))
        if transport is None:raise ValueError('CURRENT_RECEIVER_PARKED_SCOPE_OR_EXACT_ACTIVE_TRANSPORT_REQUIRED')
        expected=self.service.grant_lineage.expected('receiver',row,spec);grant=getattr(transport,'grant',{})
        state=transport.observe_state()
        if (state.get('current_native_owner_ready') is not True or state.get('cooperative_dual_hold_publication_interlock_current') is not True or
                (state.get('receiver_interlock') or {}).get('source_armed') is not True or
                grant.get('grant_id')!=expected['grant_id'] or grant.get('provider_process')!=expected['provider_process'] or
                grant.get('actor_id')!=actor or grant.get('binding')!=self.service.observed_setup.receiver_binding(row,spec) or
                getattr(transport,'binding',{})!={'run_id':row['session_id'],'object_id':row['protocol_object_id'],'coordinator_epoch':row['epoch']}):
            raise ValueError('CURRENT_ROOT_OWNED_RECEIVER_GRANT_BINDING_AND_INTERLOCK_REQUIRED')
        return state
    @staticmethod
    def _held_receiver_command(support):
        scope=support.get('source_scope') or {};origin=scope.get('source_origin') or {};raw=origin.get('raw_receipt') or {}
        held=origin.get('held_native6')
        if (support.get('current') is not True or origin.get('schema')!='explorer_station_source_measured_hold_origin_v1' or
                not isinstance(held,list) or len(held)!=6 or not _same_vector(held,held) or
                origin.get('origin_sha256')!=digest({key:value for key,value in origin.items() if key!='origin_sha256'}) or
                origin.get('physical_attainment_or_contact_claimed') is not False or
                scope.get('measured_native6')!=origin.get('held_native6')):
            raise ValueError('EXACT_CURRENT_SOURCE_SEALED_MEASURED_HOLD_RECEIPT_REQUIRED')
        if origin.get('basis')=='original_denial_measured_hold':
            valid=(raw.get('schema')=='explorer_station_observed_denial_measured_hold_v1' and
                raw.get('original_position_write_returned') is True and raw.get('MCU_attainment_verified') is False and
                raw.get('old_target_sample_emitted') is False and raw.get('ended_native_epoch')==origin.get('held_native_epoch') and
                raw.get('boot_id')==origin.get('boot_id') and raw.get('clock_domain')==origin.get('clock_domain') and
                raw.get('write_returned_monotonic_ns')==origin.get('write_returned_native_ns') and
                _same_vector(raw.get('requested_and_dispatched'),origin['held_native6']))
        elif origin.get('basis')=='original_scoped_stop_measured_hold':
            valid=(raw.get('operation')=='hold_current_position' and type(raw.get('sequence')) is int and raw['sequence']==-1 and
                raw.get('control_epoch')==origin.get('held_native_epoch') and raw.get('clock_domain')==origin.get('clock_domain') and
                raw.get('monotonic_ns')==origin.get('write_returned_native_ns') and
                _same_vector(raw.get('requested'),origin['held_native6']) and _same_vector(raw.get('dispatched'),origin['held_native6']))
        else:valid=False
        if not valid:raise ValueError('ACTUAL_ORIGINAL_RETURNED_HOLD_WRITE_NOT_IDLE_OR_CLOSED_ACK_REQUIRED')
        return {'command_id':'receiver-held-native-'+digest(raw),'source_receipt':deepcopy(raw),'source_hold_origin':deepcopy(origin),
            'basis':origin['basis'],'actual_original_native_hold_write_returned':True,'actual_native_host_dispatch':False,
            'host_publication_receipt_invented':False,'object_hold_or_closed_grasp_inferred':False,'MCU_attainment_verified':False}
    @staticmethod
    def _base(row,scope):
        body={key:scope['binding'][key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')}
        body.update(expected_state_id=scope['persistence']['state_id'],expected_revision=scope['persistence']['revision'],
            park_request_id=scope['park_request_id'],phase_id=row['protocol_state'],observing=True,neutral=True,user_observed_test_confirmed=True)
        if scope['input_mode']=='cooperative_policy':body['control_epoch']=scope['native_owner']['control_epoch']
        return body
    def _challenge(self,row,scope,reply,after,request_id):
        challenge=(reply or {}).get('challenge') or {};context=challenge.get('source_context') or {};actual=scope['actual_issued'];stamp=scope['source_stamp']
        expected={'command9':actual['command9'],'arm_receipt_sha256':digest(actual['arm_receipt']),
            'base_receipt_sha256':digest(actual['base_receipt']),'original_reference_sha256':scope['source_origin']['original_reference_sha256'],
            'native_clock_domain':stamp['clock_domain'],'native_boot_id':stamp['boot_id'],'state_source':'command_estimate',
            'measured_feedback':None,'measured_feedback_mask':[False]*6,'measured_hold_or_stationarity_claimed':False}
        mask=context.get('measured_feedback_mask')
        snapshot=challenge.get('source_snapshot_stamp') or {};boundary=reply.get('interlock') or {}
        if (reply.get('native_command_called') is not False or reply.get('execution_authority') is not False or
                boundary.get('schema')!='explorer_native_receiver_hold_boundary_v1' or
                not {'reference_fault','parked','pending_challenge','grant'}<=boundary.keys() or boundary['reference_fault'] is not None or
                challenge.get('schema')!='explorer_native_parked_receiver_hold_challenge_v1' or
                any(challenge.get(key)!=scope['binding'][key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')) or
                challenge.get('actor_id')!=scope['actor_id'] or challenge.get('park_request_id')!=scope['park_request_id'] or
                challenge.get('native_command_called') is not False or challenge.get('execution_authority') is not False or
                type(challenge.get('revision')) is not int or challenge['revision']!=scope['persistence']['revision']+1 or
                boundary.get('revision')!=challenge['revision'] or (boundary.get('persistence') or {}).get('state_id')!=scope['persistence']['state_id'] or
                boundary.get('binding')!=scope['binding'] or not self.service._durable_giver(boundary) or
                challenge.get('after_receiver_command_id')!=after or challenge.get('release_request_id')!=request_id or context!=expected or
                not isinstance(mask,list) or len(mask)!=6 or any(value is not False for value in mask) or
                challenge.get('source_control_epoch')!=scope['binding'].get('native_control_epoch') or
                challenge.get('source_native_generation')!=scope['binding']['native_generation'] or
                challenge.get('native_boot_id')!=stamp['boot_id'] or challenge.get('native_clock_domain')!=stamp['clock_domain'] or
                type(challenge.get('issued_native_ns')) is not int or type(challenge.get('expires_native_ns')) is not int or
                not 0<=challenge['issued_native_ns']<challenge['expires_native_ns']<=challenge['issued_native_ns']+2_000_000_000 or
                snapshot.get('clock_domain')!=stamp['clock_domain'] or snapshot.get('boot_id')!=stamp['boot_id'] or
                type(snapshot.get('monotonic_ns')) is not int or not 0<=snapshot['monotonic_ns']<=challenge['issued_native_ns'] or
                (reply.get('interlock') or {}).get('pending_challenge')!=challenge or
                (reply.get('interlock') or {}).get('armed') is not True):
            raise ValueError('EXACT_ACTUAL_SOURCE_PARKED_CHALLENGE_CONTEXT_AND_ORIGINAL_DURATION_REQUIRED')
        return challenge
    def prepare(self,row,after_receiver_command_id):
        spec=self.service.workflow.skill(row['skill_id'],row['skill_version'])['spec'];actor=(row.get('observed_test_binding') or {}).get('actor_id')
        row=self._fresh(row,spec,actor);pending=row.get('pending_operation') or {}
        if pending.get('event')!='release_requested' or not pending.get('operation_id'):raise ValueError('EXACT_PENDING_USER_RELEASE_STEP_REQUIRED')
        pair=self.service.native_controls._source('giver');self.service.native_controls._check_permission('giver',row,spec,pair)
        if (pair['source'].get('release_boundary') or {}).get('parked') is None:
            transport=self.service.transports.get((row['session_id'],'giver'))
            if transport is not None and transport.observe_state().get('current_native_owner_ready') is True:return None
            raise ValueError('CURRENT_GIVER_MUST_BE_AN_EXACT_ACTIVE_TRANSPORT_OR_PARKED_SOURCE')
        scoped=self.service.observed_scopes.require_current(row,spec,('giver',),actor=actor)['roles']['giver'];scope=scoped['source_scope']
        if not scoped['facts'].get('armed') or pair['source'].get('capabilities',{}).get('explicit_user_parked_release_http') is not True:
            raise ValueError('ACTUAL_ARMED_PARKED_RELEASE_SOURCE_HTTP_REQUIRED')
        receiver_support=self._receiver_support(row,spec,actor)
        after=after_receiver_command_id
        receiver_source=None
        if after is None:
            receiver_source=(self._held_receiver_command(receiver_support) if receiver_support.get('source_scope') is not None
                else self.service._receiver_command())
            after=receiver_source['command_id']
        body=self._base(row,scope);body.update(release_request_id=uuid.uuid4().hex,after_receiver_command_id=after)
        intent=self._intent(row,spec);started=self.clock();reply=None;pointer=None
        try:
            reply=self.service._request('giver')('POST','/legacy/explorer/cooperative/phase/release/challenge',body);received=self.clock()
            challenge=self._challenge(row,scope,reply,after,body['release_request_id'])
            current=self._fresh(row,spec,actor)
            if self._intent(current,spec)!=intent:raise ValueError('ROOT_RELEASE_INTENT_CHANGED_DURING_SOURCE_CHALLENGE')
            pointer=self.service.native_receipts.archive(row,spec,'giver','parked_release_challenge',reply,actor,
                intent_evidence={'source_scope':scope,'request':body,'receiver_source':receiver_source,'intent':intent})
            custody=self.service.native_receipts.publish(row,spec,pointer,actor)
            bundle=self.service.inbox.prepare_release_bundle(run_id=row['session_id'],object_id=row['protocol_object_id'],
                after_giver_command_id=self.service.workflow._last_command(row,'giver'),source_response=reply,
                authenticated_actor=actor,request_started_ns=started,response_received_ns=received)
            descriptor={'release_bundle_id':bundle['bundle_id'],'run_id':row['session_id'],'object_id':row['protocol_object_id'],
                'epoch':row['epoch'],'operation_id':pending['operation_id'],'actor_id':actor,'challenge':deepcopy(challenge),
                'source_challenge_sha256':digest(challenge),'deadline_nx_ns':bundle['deadline_ns'],
                'received_coordinator_ns':received,'received_clock_domain':self.service.domain,'after_receiver_command_id':after,
                'source_challenge_receipt':custody,'source_commands_sent':0,'explicit_user_control_required':True}
            with self.service.workflow.lock:
                current=self._fresh(row,spec,actor)
                if self._intent(current,spec)!=intent:raise ValueError('ROOT_RELEASE_INTENT_CHANGED_WHILE_RETAINING_QUESTIONS')
                with self.condition:
                    if len(self.records)>=64:raise ValueError('RETAINED_PARKED_RELEASE_TRANSACTION_BUDGET_REACHED')
                    self.records[bundle['bundle_id']]={'descriptor':descriptor,'row':deepcopy(row),'spec':deepcopy(spec),'actor':actor,
                        'intent':intent,'scope':scope,'pair':pair,'source_response':reply,'state':'PENDING','completion':None,'attempted':False}
                    self.condition.notify_all()
            return deepcopy(descriptor)
        except Exception:
            if isinstance(reply,dict) and reply and pointer is None:
                try:self.service.native_receipts.archive(row,spec,'giver','parked_release_challenge',reply,actor,allow_unpublished=True)
                except Exception:pass
            raise
    def wait(self,row,descriptor):
        identity=descriptor.get('release_bundle_id');actor=(row.get('observed_test_binding') or {}).get('actor_id')
        while True:
            record,_=self._current_record(row,identity,actor)
            if record['descriptor']!=descriptor:raise ValueError('EXACT_PENDING_SOURCE_RELEASE_DESCRIPTOR_REQUIRED')
            with self.condition:
                actual=self.records.get(identity)
                if actual is None or actual['state'] in ('FAILED','CANCELLED'):raise RuntimeError('PARKED_RELEASE_CONTROL_CANCELLED')
                if actual['state']=='COMPLETED' and actual['completion'] is not None:
                    if actual.get('completion_claimed'):raise ValueError('PARKED_RELEASE_CONTROL_COMPLETION_ALREADY_CLAIMED')
                    actual['completion_claimed']=True;return deepcopy(actual['completion'])
                self.condition.wait(min(.05,max(0,(descriptor['deadline_nx_ns']-self.clock())/1e9)))
    def invalidate(self,row):
        with self.condition:
            for record in self.records.values():
                if record['row']['session_id']==row['session_id'] and record['row']['epoch']==row['epoch']:
                    record['state']='CANCELLED'
            self.condition.notify_all()
    def cancel_owned_attempts(self,row):
        # Only cancel scopes explicitly declared by an actual source challenge
        # for an RPC this process attempted. Never borrow a fresh remote epoch.
        with self.condition:
            records=[deepcopy(record) for record in self.records.values() if record['row']['session_id']==row['session_id'] and
                record['row']['epoch']==row['epoch'] and record.get('attempted')]
        receipts=[]
        for record in records:
            try:receipts.append(self._cleanup_attempt(record))
            except Exception as error:receipts.append({'delivery_unknown':True,'error':type(error).__name__+':'+str(error)})
        return {'basis':'cancel_only_literal_source_challenge_scope_not_returned_owner_or_dispatch',
            'receipts':receipts,'physical_stop_confirmed':False,'native_lease_renewed':False,'interlocks_unarmed':False}
    def _cleanup_attempt(self,record):
        row=record['row'];challenge=record['descriptor']['challenge'];request=self.service._request('giver')
        activation=request('GET','/legacy/explorer/retarget/activation/status',None)
        if activation.get('authorization_id') not in (None,challenge['authorization_id']):return {'foreign_current_authorization_not_owned':True}
        source=request('GET','/legacy/explorer/cooperative/status',None);binding=(source.get('release_boundary') or {}).get('binding') or {};native=source.get('native') or {}
        if any(binding.get(key)!=challenge.get(key) for key in ('run_id','object_id','coordinator_epoch','authorization_id')):
            return {'foreign_current_source_binding_not_owned':True}
        epoch=challenge['source_control_epoch'];generation=challenge['source_native_generation']
        if native.get('owner_generation')!=generation or epoch is not None and (native.get('control_epoch')!=epoch or native.get('active') is not True):
            return {'foreign_or_already_stopped_source_scope_not_adopted':True}
        if epoch is None and native.get('active') is not False:return {'foreign_active_source_not_owned':True}
        return request('POST','/legacy/explorer/cooperative/stop',
            {key:challenge[key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')}|
            {'control_epoch':epoch,'expected_native_generation':generation,'observing':True})
    def _control_result(self,record,result,proofs):
        descriptor=record['descriptor'];challenge=descriptor['challenge'];grant=(result or {}).get('release_grant') or {};boundary=(result or {}).get('interlock') or {}
        native=self.service.native_controls.giver_expected(record['pair'],result,'native_phase_resume');stamp=boundary.get('source_stamp') or {}
        lineage=grant.get('source_resume_lineage') or {};base=lineage.get('after_resume_original_base_receipt') or {}
        if (result.get('native_command_called') is not True or result.get('actual_arm_opening_command_sent') is not False or
                boundary.get('schema')!='explorer_native_receiver_hold_boundary_v1' or
                not {'reference_fault','parked','pending_challenge','grant'}<=boundary.keys() or boundary.get('pending_challenge') is not None or
                result.get('automatic_lease_renewal') is not False or result.get('old_native_challenge_times_retained') is not True or
                result.get('release_grant')!=boundary.get('grant') or boundary.get('parked') is not None or boundary.get('armed') is not True or
                boundary.get('binding')!=record['scope']['binding'] or
                not self.service._durable_giver(boundary) or boundary.get('reference_fault') is not None or
                grant.get('challenge')!=challenge or grant.get('expires_native_ns')!=challenge['expires_native_ns'] or
                grant.get('revision')!=challenge['revision']+1 or boundary.get('revision')!=grant['revision'] or
                grant.get('operator_confirmation')!=proofs['receiver_holds_object'] or
                grant.get('stationary_confirmations')!={key:proofs[key] for key in ('giver_stationary','receiver_stationary')} or
                lineage.get('challenge_sha256')!=digest(challenge) or
                lineage.get('previous_revision')!=challenge['revision'] or lineage.get('resumed_revision')!=grant.get('revision') or
                lineage.get('same_original_native_epoch')!=challenge['source_control_epoch'] or
                not _same_vector(lineage.get('after_resume_command9'),challenge['source_context']['command9']) or
                lineage.get('unchanged_arm_receipt_sha256')!=challenge['source_context']['arm_receipt_sha256'] or
                not _same_vector(base.get('velocity'),[0.,0.,0.]) or not isinstance(base.get('receipt_id'),str) or
                not base['receipt_id'].startswith(challenge['native_boot_id']+':native-host:') or
                type(base.get('stamp_ns')) is not int or not 0<=base['stamp_ns']<=stamp.get('monotonic_ns',-1) or
                grant.get('clock_conversion_assumed') is not False or grant.get('force_or_attainment_measured') is not False or
                any(stamp.get(key)!=challenge.get(source) for key,source in (('boot_id','native_boot_id'),('clock_domain','native_clock_domain'))) or
                type(stamp.get('monotonic_ns')) is not int or not challenge['issued_native_ns']<=stamp['monotonic_ns']<challenge['expires_native_ns'] or
                native.get('owner_generation')!=challenge['source_native_generation'] or native.get('stop_latched') is not False or
                native.get('native_lease_remaining_s',0)<=0 or
                challenge['source_control_epoch'] is not None and (native.get('control_epoch')!=challenge['source_control_epoch'] or native.get('active') is not True) or
                challenge['source_control_epoch'] is None and native.get('active') is not False):
            raise ValueError('EXACT_ACTUAL_SOURCE_RESUME_INSTALLED_GRANT_ORIGINAL_EXPIRY_AND_CONSUMED_FACTS_REQUIRED')
        return grant,native
    def _attach(self,row,spec,record,result,grant,predict):
        challenge=record['descriptor']['challenge'];binding={key:challenge[key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')}
        common={'clock_domain':self.service.domain,'clock':self.clock,'receiver_command':self.service._receiver_command,
            'installed_grant':grant,'release_descriptor':record['descriptor']}
        if row['giver_input_mode']=='policy':
            reference=deepcopy(spec['subskills']['giver']);constraint=deepcopy((record['pair']['source'].get('registered_action_constraint') or {}).get('constraint'))
            def policy(command):return predict(deepcopy(reference),dispatch=False,command=deepcopy(command))
            transport=InstalledGrantGiverPolicyTransport(self.service._request('giver'),binding,challenge['source_control_epoch'],policy,
                action_constraint=constraint,**common)
        else:transport=InstalledGrantGiverManualTransport(self.service._request('giver'),binding,challenge['source_native_generation'],**common)
        transport._authorize_release({'preinstalled_parked_release_grant':grant,'native_release_challenge':record['descriptor'],
            'operator_confirmation_receipt':{'confirmation':grant['operator_confirmation']}})
        state=transport.observe_state()
        if state.get('current_native_owner_ready') is not True:raise ValueError('EXPLICIT_RELEASE_CONTROL_RETURNED_OWNER_EXPIRED_BEFORE_SCOPED_ATTACH')
        with self.service.workflow.lock:
            self._fresh(row,spec,record['actor'])
            with self.service.lock:
                current=self.service.native_bindings.setdefault(row['session_id'],{'epoch':row['epoch'],'actor':record['actor'],'roles':{}})
                if current['epoch']!=row['epoch'] or current['actor']!=record['actor']:raise ValueError('FOREIGN_ROOT_NATIVE_ATTACHMENT_CONTEXT')
                current.setdefault('roles',{})['giver']=deepcopy(state);self.service.transports[(row['session_id'],'giver')]=transport
        return transport
    def resume(self,row,body,*,actor):
        allowed={'session_id','expected_epoch','release_bundle_id','affirmations','observing','neutral','user_observed_test_confirmed'}
        if (not isinstance(body,dict) or set(body)!=allowed or not all(body.get(key) is True for key in ('observing','neutral','user_observed_test_confirmed')) or
                not isinstance(body.get('affirmations'),dict) or set(body['affirmations'])!=STATEMENTS or any(v is not True for v in body['affirmations'].values()) or
                not isinstance(body.get('release_bundle_id'),str) or not re.fullmatch('[0-9a-f]{32}',body['release_bundle_id']) or
                body['session_id']!=row['session_id'] or body['expected_epoch']!=row['epoch']):
            raise ValueError('EXACT_OPAQUE_RELEASE_BUNDLE_AND_SIX_EXPLICIT_CURRENT_USER_CONFIRMATIONS_REQUIRED')
        identity=body['release_bundle_id'];record,current=self._current_record(row,identity,actor);spec=record['spec'];descriptor=record['descriptor'];result=None;pointer=None;attempted=False;token=None
        with self.condition:
            actual=self.records.get(identity)
            if actual is None or actual['state']!='PENDING':raise ValueError('PARKED_RELEASE_CONTROL_CHOICE_ALREADY_USED')
            actual['state']='IN_FLIGHT'
        try:
            token=self.service.workflow.begin_parked_release_control(row['session_id'],row['epoch'],identity)
            scope=self.service.observed_scopes.require_current(current,spec,('giver',),actor=actor)['roles']['giver']['source_scope']
            self._receiver_support(current,spec,actor)
            predict=self.service.native_controls._ready_prediction(current,spec,'giver') if row['giver_input_mode']=='policy' else None
            pair=self.service.native_controls._source('giver');self.service.native_controls._check_permission('giver',current,spec,pair)
            source=pair['source'];boundary=source['release_boundary'];challenge=descriptor['challenge']
            if (boundary.get('pending_challenge')!=challenge or boundary.get('revision')!=challenge['revision'] or
                    scope['binding']['authorization_id']!=challenge['authorization_id'] or scope['park_request_id']!=challenge['park_request_id']):
                raise ValueError('ACTUAL_SOURCE_PENDING_CHALLENGE_CHANGED_BEFORE_USER_CONTROL')
            self.service.inbox.confirm_release_bundle(identity,{'affirmations':deepcopy(body['affirmations']),'observing':True},authenticated_actor=actor)
            claimed=self.service.inbox.wait_release_bundle(identity);proofs=claimed['proofs']
            if (claimed['source_challenge_sha256']!=descriptor['source_challenge_sha256'] or claimed['expected_actor']!=actor or
                    claimed['source_response']['challenge']!=challenge or self.clock()>=descriptor['deadline_nx_ns']):
                raise ValueError('ACTUAL_SINGLE_CLAIMED_FRESH_SOURCE_BUNDLE_REQUIRED')
            payload=self._base(current,scope);payload.update(expected_revision=challenge['revision'],release_request_id=challenge['release_request_id'],
                challenge_id=challenge['challenge_id'],operator_confirmations=proofs,coordinator_clock_domain=self.service.domain,
                challenge_received_coordinator_ns=descriptor['received_coordinator_ns'],proofs_checked_coordinator_ns=self.clock(),
                coordinator_verified_confirmation_created_after_challenge=True)
            self._current_record(row,identity,actor)
            with self.condition:
                actual=self.records.get(identity)
                if actual is None or actual['state']!='IN_FLIGHT':raise ValueError('PARKED_RELEASE_CONTROL_CANCELLED_BEFORE_SOURCE_RPC')
                actual['attempted']=True
            attempted=True;self.service.observed_scopes.invalidate(current,'giver')
            result=self.service._request('giver')('POST','/legacy/explorer/cooperative/phase/resume-release',payload)
            self.service.native_controls._remember_owned(row,spec,'giver','native_phase_resume',pair,result,actor)
            grant,_=self._control_result(record,result,proofs)
            pointer=self.service.native_receipts.archive(row,spec,'giver','native_parked_release_resume',result,actor,
                intent_evidence={'descriptor':descriptor,'operator_confirmations':proofs,'request':payload,'explicit_scoped_transport_attach':True})
            custody=self.service.native_receipts.publish(row,spec,pointer,actor)
            current=self._fresh(row,spec,actor);self._attach(current,spec,record,result,grant,predict)
            completion={'schema':'explorer_cooperative_parked_release_control_completion_v1',
                **{key:descriptor[key] for key in ('release_bundle_id','run_id','object_id','epoch','operation_id')},
                'operator_confirmations':deepcopy(proofs),'release_grant':deepcopy(grant),'source_control_receipt':custody,
                'source_control_validated':True,'native_release_challenge':deepcopy(descriptor),
                'native_arm_opening_command_sent':False,'native_lease_automatically_renewed':False,'explicit_scoped_transport_attached':True}
            self._current_record(row,identity,actor)
            with self.service.workflow.lock:
                self._fresh(row,spec,actor)
                with self.condition:
                    actual=self.records.get(identity)
                    if actual is None or actual['state']!='IN_FLIGHT':raise ValueError('LATE_PARKED_RELEASE_CONTROL_AFTER_STOP')
                    actual['state']='VALIDATED_AWAIT_RELEASE';actual['completion']=deepcopy(completion)
            # The workflow cannot advance while this native phase reservation
            # is held. End it BEFORE waking the validated completion waiter.
            self.service.workflow.end_native_phase_change(token);token=None
            with self.service.workflow.lock:
                self._fresh(row,spec,actor)
                with self.condition:
                    actual=self.records.get(identity)
                    if actual is None or actual['state']!='VALIDATED_AWAIT_RELEASE':raise ValueError('LATE_PARKED_RELEASE_CONTROL_AFTER_RESERVATION_END')
                    actual['state']='COMPLETED';self.condition.notify_all()
            return {'operation':'native_parked_release_resume','completion':completion,'native_control_receipt':result,
                'automatic_native_replay':False,'API_result_claims_object_release_or_hardware_attainment':False}
        except Exception:
            if attempted:
                try:self._cleanup_attempt(record)
                except Exception:pass
            if isinstance(result,dict) and result and pointer is None:
                try:self.service.native_receipts.archive(row,spec,'giver','native_parked_release_resume',result,actor,allow_unpublished=True)
                except Exception:pass
            with self.condition:
                actual=self.records.get(identity)
                if actual is not None and actual['state']!='CANCELLED':actual['state']='FAILED'
                self.condition.notify_all()
            raise
        finally:
            if token is not None:self.service.workflow.end_native_phase_change(token)
