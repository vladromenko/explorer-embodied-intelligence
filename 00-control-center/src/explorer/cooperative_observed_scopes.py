"""ENGINEERING_GLUE: explicit source-held observations per cooperative role.

Construction/cached snapshots never request a node. Fresh checks use only fixed
authenticated GETs and original immutable grant lineage. Cached previews and a
parked role are never native motion permission; action paths revalidate sources.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import threading

from explorer_shared.contracts import digest
from explorer_shared.station_observed_http import StationObservedHttp
from .cooperative_giver_phases import CooperativeGiverPhases

ROLES=('giver','receiver')


class CooperativeObservedScopes:
    def __init__(self,service):
        self.service=service;self.clock=service.clock;self.lock=threading.RLock();self.cache=OrderedDict()
    def _pin(self,row,spec):
        return {'context':self.service.observed_setup._context(row,spec),
            'original_binding_sha256':digest(row.get('observed_test_binding')),
            'lineage_sha256':digest(row.get('native_grant_successors')),
            'control_head_sha256':digest(row.get('native_control_ledger')),
            'phase':self.service.native_controls._phase_pin(row)}
    def invalidate(self,row,role=None):
        with self.lock:
            cached=self.cache.get(row['session_id'])
            if cached is not None:
                if role is None:self.cache.pop(row['session_id'],None)
                else:cached['roles'].pop(role,None)
    def _document(self,role,row,spec):
        entry=(row.get('native_grant_successors') or {}).get(role)
        if entry is None:raise ValueError('EXPLICIT_ARCHIVED_NEW_USER_PARKED_SUCCESSOR_REQUIRED')
        self.service.grant_lineage.expected(role,row,spec)
        pointer=entry['receipt'];path,_=self.service.artifact(pointer['kind'],pointer['receipt_id']);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pointer['sha256']:raise ValueError('ORIGINAL_PARKED_SUCCESSOR_ARTIFACT_CHANGED')
        document=json.loads(raw)
        if (document.get('context')!=self.service.observed_setup._context(row,spec) or
                document.get('actor_id')!=row['observed_test_binding']['actor_id'] or document.get('authority_publication_allowed') is False):
            raise ValueError('ORIGINAL_PARKED_SUCCESSOR_CONTEXT_CHANGED')
        return entry,document
    @staticmethod
    def _common(role,row,packet):
        stamp=packet.get('source_stamp') or {};actor=row['observed_test_binding']['actor_id']
        if (packet.get('schema')!='explorer_cooperative_held_scope_v1' or packet.get('role')!=role or packet.get('ready') is not True or
                packet.get('scope_sha256')!=digest({key:value for key,value in packet.items() if key!='scope_sha256'}) or
                any(packet.get(key) is not False for key in ('dispatch_permission','stationarity_verified','object_holding_verified',
                    'physical_attainment_claimed','native_lease_renewed')) or type(packet.get('native_commands_sent')) is not int or
                packet['native_commands_sent']!=0 or packet.get('actor_id')!=actor or
                not all(isinstance(stamp.get(key),str) and stamp[key] for key in ('boot_id','clock_domain')) or
                type(stamp.get('monotonic_ns')) is not int or stamp['monotonic_ns']<0):
            raise ValueError('EXACT_SOURCE_PARKED_OBSERVATION_ONLY_PROOF_REQUIRED:'+str(packet.get('reason')))
    def _giver(self,row,spec,packet,pair,entry,document):
        source=pair['source'];permission=pair['permission'];boundary=source.get('release_boundary') or {};native=source.get('native') or {}
        expected=self.service.grant_lineage.expected('giver',row,spec);binding=packet.get('binding') or {};stamp=packet['source_stamp']
        origin=packet.get('source_origin') or {};saved=((document.get('source_response') or {}).get('successor') or {}).get('origin')
        actual=packet.get('actual_issued') or {};proof=packet.get('authorization_receipt') or {};stage=packet.get('stage')
        feedback_mask=packet.get('measured_feedback_mask')
        if (source.get('capabilities',{}).get('parked_observation_scope_http') is not True or
                not self.service._durable_giver(boundary) or packet.get('persistence')!=boundary.get('persistence') or
                boundary.get('reference_fault') is not None or (boundary.get('original_quiescence') or {}).get('ready') is not True or
                binding!=boundary.get('binding') or any(binding.get(key)!=row[field] for key,field in
                    (('run_id','session_id'),('object_id','protocol_object_id'),('coordinator_epoch','epoch'))) or
                binding.get('authorization_id')!=expected['authorization_id'] or permission.get('authorization_id')!=expected['authorization_id'] or
                permission.get('execution_permission') is not True or permission.get('authorization_receipt')!=proof or
                binding.get('observed_source_authorization')!=proof or proof.get('actor_id')!=row['observed_test_binding']['actor_id'] or
                packet.get('input_mode')!=self.service.observed_setup.giver_mode(row) or proof.get('input_mode')!=packet['input_mode'] or
                packet.get('state_source')!='command_estimate' or packet.get('measured_feedback') is not None or
                not isinstance(feedback_mask,list) or len(feedback_mask)!=6 or any(value is not False for value in feedback_mask) or
                origin!=saved or packet.get('source_origin_sha256')!=digest(origin) or
                stage not in ('PARKED_NEW_USER','BEGIN_CLUTCHED') or packet.get('park_request_id')!=(boundary.get('parked') or {}).get('park_request_id') or
                any(packet['native_owner'].get(key)!=native.get(key) for key in
                    ('control_epoch','owner_generation','native_generation','native_owner','active','state','stop_latched','boot_id','clock_domain')) or
                stamp['boot_id']!=native.get('boot_id') or stamp['clock_domain']!=native.get('clock_domain') or
                actual.get('arm_receipt')!=(origin.get('actual_issued') or {}).get('arm_receipt') or
                actual.get('command9')!=(origin.get('actual_issued') or {}).get('command9') or
                (packet.get('original_reference_guard_result') or {}).get('servo_deg')!=actual.get('command9',[])[:6]):
            raise ValueError('GIVER_SCOPE_DIFFERS_FROM_EXACT_ROOT_LINEAGE_CURRENT_SOURCE_OR_COMMAND_ORIGIN')
        CooperativeGiverPhases.validate_origin(origin,dict(stamp,native_monotonic_ns=stamp['monotonic_ns']))
        adopted=(document.get('source_response') or {}).get('interlock') or {};registered=adopted.get('parked') or {}
        if source.get('policy')!=registered.get('model') or source.get('registered_action_constraint')!=registered.get('constraint'):
            raise ValueError('CURRENT_ORIGINAL_GIVER_MODEL_OR_CONSTRAINT_CHANGED')
        if row['giver_input_mode']=='policy':
            model={key:spec['subskills']['giver'][key] for key in ('model_id','checkpoint_sha256','spec_sha256')}
            if source.get('policy')!=model:
                raise ValueError('CURRENT_ORIGINAL_GIVER_MODEL_OR_CONSTRAINT_CHANGED')
        if stage=='BEGIN_CLUTCHED':
            owned=self.service.native_controls.owned_scope(row,'giver')
            if owned is None:raise ValueError('ONLY_EXPLICIT_ROOT_RETURNED_CLUTCHED_GIVER_OWNER_ACCEPTED')
            known=self.service.native_controls.giver_expected(owned['pair'],owned['result'],owned['operation'])
            if any(native.get(key)!=known.get(key) for key in ('control_epoch','owner_generation','active')):
                raise ValueError('FOREIGN_OR_UNKNOWN_CLUTCHED_GIVER_OWNER')
        return {'original_publication_guard_current':True,'durable_guard_current':True,'armed':boundary.get('armed') is True,
            'parked_arm_metadata_supported':source.get('capabilities',{}).get('parked_observation_arm_http') is True}
    def _receiver(self,row,spec,packet,pair,entry,document):
        wrapper=pair['permission'];native=pair['source'];grant=wrapper.get('grant') or {};boundary=wrapper.get('cooperative_hold_interlock') or {}
        expected=self.service.grant_lineage.expected('receiver',row,spec);origin=packet.get('source_origin') or {};stamp=packet['source_stamp']
        saved=(document.get('source_receipt') or {}).get('source_hold_origin')
        wanted=self.service.observed_setup.receiver_binding(row,spec);profile=packet.get('direction_profile') or {}
        measured=packet.get('measurement_stamp') or {};current_measured=native.get('measurements') or {};age=native.get('measurement_age_ms')
        if (boundary.get('parked_observation_scope_http') is not True or wrapper.get('execution_permission') is not True or
                wrapper.get('native_publication_permission_gate_installed') is not True or (wrapper.get('publication_gate') or {}).get('installed') is not True or
                boundary.get('publication_gate_required') is not True or boundary.get('persistence_fault') is not None or
                boundary.get('all_target_writes_blocked') is not False or boundary.get('binding_current') is not True or
                boundary.get('parked_observation_only') is not True or native.get('available') is not True or native.get('configuration_writes')!=0 or
                native.get('mode')!='idle' or packet.get('binding')!=wanted or grant.get('binding')!=wanted or
                grant.get('grant_id')!=expected['grant_id'] or grant.get('provider_process')!=expected['provider_process'] or
                any(packet['grant'].get(key)!=grant.get(key) for key in ('grant_id','provider_process','control_epoch','actor_id','boot_id','clock_domain',
                    'follower_calibration_sha256','binding','displayed_frame','observation_deadline_ns')) or
                grant.get('control_epoch')!=native.get('control_epoch') or packet.get('native_owner')!=boundary.get('native_owner') or
                packet.get('interlock_id')!=boundary.get('interlock_id') or packet.get('revision')!=boundary.get('revision') or
                packet.get('state_source')!='measured_state' or origin!=saved or packet.get('source_origin_sha256')!=origin.get('origin_sha256') or
                origin.get('origin_sha256')!=digest({key:value for key,value in origin.items() if key!='origin_sha256'}) or
                packet.get('measured_native6')!=origin.get('held_native6') or packet.get('follower_calibration_sha256')!=grant.get('follower_calibration_sha256') or
                any(measured.get(key)!=stamp.get(key) for key in ('clock_domain','boot_id')) or
                type(measured.get('monotonic_ns')) is not int or not origin['write_returned_native_ns']<=measured['monotonic_ns']<=stamp['monotonic_ns'] or
                stamp['monotonic_ns']-measured['monotonic_ns']>500_000_000 or
                current_measured.get('follower')!=packet.get('measured_native6') or current_measured.get('clock_domain')!=stamp['clock_domain'] or
                type(age) not in (int,float) or not 0<=age<=500 or
                stamp['boot_id']!=grant.get('boot_id') or stamp['clock_domain']!=grant.get('clock_domain') or
                ((native.get('roles') or {}).get('follower') or {}).get('calibration_sha256')!=grant.get('follower_calibration_sha256')):
            raise ValueError('RECEIVER_SCOPE_DIFFERS_FROM_EXACT_ROOT_LINEAGE_CURRENT_SOURCE_OR_MEASURED_HOLD')
        direction=(bool(profile) and profile.get('profile_sha256')==digest({key:value for key,value in profile.items() if key!='profile_sha256'}) and
            profile.get('follower_calibration_sha256')==grant['follower_calibration_sha256'] and type(profile.get('closing_sign')) is int and profile['closing_sign'] in (-1,1))
        return {'original_publication_guard_current':True,'durable_guard_current':packet['persistence'].get('healthy') is True,
            'armed':boundary.get('armed') is True,'direction_current':direction,
            'parked_arm_metadata_supported':boundary.get('parked_observation_arm_http') is True}
    def refresh(self,row,spec,*,actor):
        row=self.service.native_controls._fresh(row,spec,actor);pin=self._pin(row,spec);roles={}
        for role in ROLES:
            began=self.clock()
            try:
                entry,document=self._document(role,row,spec)
                path='/legacy/explorer/cooperative/phase/held-status' if role=='giver' else '/station/observed-test/cooperative/held/status'
                packet=self.service._request(role)('GET',path,None);pair=self.service.native_controls._source(role)
                self._common(role,row,packet)
                facts=self._giver(row,spec,packet,pair,entry,document) if role=='giver' else self._receiver(row,spec,packet,pair,entry,document)
                if not 0<=self.clock()-began<=2_000_000_000:raise ValueError('PARKED_SCOPE_SOURCE_RECEIVE_WINDOW_EXPIRED')
                current=self.service.native_controls._fresh(row,spec,actor)
                if self._pin(current,spec)!=pin:raise ValueError('ROOT_SCOPE_OR_LINEAGE_CHANGED_DURING_GET')
                age_ns=2_000_000_000
                if role=='receiver':age_ns=min(age_ns,packet['grant']['observation_deadline_ns']-packet['source_stamp']['monotonic_ns'])
                roles[role]={'current':True,'reason':None,'source_scope':deepcopy(packet),'source_pair':deepcopy(pair),
                    'request_started_nx_ns':began,'received_nx_ns':self.clock(),'expires_nx_ns':began+max(0,age_ns),
                    'received_clock_domain':self.service.domain,'facts':facts,
                    'expiry_basis':'NX_request_start_plus_source_remaining_duration_not_absolute_clock_conversion'}
            except Exception as error:roles[role]={'current':False,'reason':type(error).__name__+':'+str(error),'received_nx_ns':self.clock()}
        current=self.service.native_controls._fresh(row,spec,actor)
        if self._pin(current,spec)!=pin:
            # A checkpoint may close while its already admitted phase change
            # completes. Drop the preview; do not turn that metadata refresh
            # into a replay or cancellation of a genuine returned source call.
            pin=self._pin(current,spec)
            roles={role:{'current':False,'reason':'ROOT_PHASE_OR_LINEAGE_CHANGED_DURING_GET','received_nx_ns':self.clock()} for role in ROLES}
        with self.lock:
            self.cache[row['session_id']]={'pin':pin,'roles':roles};self.cache.move_to_end(row['session_id'])
            while len(self.cache)>64:self.cache.popitem(last=False)
        return self.snapshot(current,spec)
    def snapshot(self,row,spec):
        with self.lock:record=deepcopy(self.cache.get(row['session_id']))
        roles={role:{'current':False,'reason':'EXPLICIT_FRESH_SOURCE_SCOPE_GET_REQUIRED'} for role in ROLES}
        try:
            current=self.service.native_controls._fresh(row,spec)
            if record and record['pin']==self._pin(current,spec):
                for role,value in record['roles'].items():
                    if 0<=self.clock()-value['received_nx_ns']<=2_000_000_000 and self.clock()<value.get('expires_nx_ns',value['received_nx_ns']+2_000_000_000):roles[role]=value
        except (ValueError,OSError,KeyError,TypeError):pass
        return {'schema':'explorer_cooperative_observed_scopes_v1','roles':roles,'cached_preview_only':True,
            'source_revalidation_required_before_native_dispatch':True,'parked_is_not_own_dispatch_authority':True,
            'stationarity_or_object_holding_inferred':False,'native_commands_sent':0}
    def require_current(self,row,spec,roles=ROLES,*,actor):
        if not isinstance(roles,(tuple,list)) or not roles or any(role not in ROLES for role in roles):raise ValueError('Fixed native scope roles required')
        result=self.refresh(row,spec,actor=actor)
        for role in roles:
            if result['roles'][role].get('current') is not True:raise ValueError(role+':'+result['roles'][role]['reason'])
        return result
    def arm_role(self,row,spec,role,*,actor):
        if role not in ROLES:raise ValueError('Fixed cooperative source role required')
        before=self.require_current(row,spec,(role,),actor=actor)['roles'][role];source=before['source_scope'];result=None;pointer=None
        if before['facts'].get('parked_arm_metadata_supported') is not True:raise ValueError('ACTUAL_SOURCE_PARKED_METADATA_ARM_HTTP_REQUIRED')
        if role=='receiver':
            body={'grant_id':source['grant']['grant_id'],'expected_control_epoch':source['grant']['control_epoch'],
                'interlock_id':source['interlock_id'],'expected_revision':source['revision'],'observing':True,'user_observed_test_confirmed':True}
            post=lambda:StationObservedHttp(self.service._request(role),source['binding']).cooperative('held/arm',body,authenticated_actor=actor)
            revision=source['revision']
        else:
            body={key:source['binding'][key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')}
            body.update(expected_state_id=source['persistence']['state_id'],expected_revision=source['persistence']['revision'],
                park_request_id=source['park_request_id'],phase_id=row['protocol_state'],observing=True,neutral=True,user_observed_test_confirmed=True)
            post=lambda:self.service._request(role)('POST','/legacy/explorer/cooperative/phase/arm',body)
            revision=source['persistence']['revision']
        self.invalidate(row,role)
        try:
            self.service.native_controls._fresh(row,spec,actor);result=post()
            interlock=(result or {}).get('interlock') or {};actual_rev=(interlock.get('persistence') or {}).get('revision') if role=='giver' else interlock.get('revision')
            admitted=(result or {}).get('source_scope') or {};self._common(role,row,admitted)
            stable=('binding','native_owner','actor_id','source_origin','source_origin_sha256','stage','park_request_id','state_source',
                'measured_feedback','measured_feedback_mask','follower_calibration_sha256','measured_native6','interlock_id','armed')
            if (not isinstance(result,dict) or result.get('schema')!='explorer_cooperative_parked_arm_receipt_v1' or result.get('role')!=role or
                    result.get('armed') is not True or type(result.get('already_armed')) is not bool or
                    interlock.get('armed') is not True or result.get('binding')!=source['binding'] or result.get('native_owner')!=source['native_owner'] or
                    result.get('source_origin_sha256')!=source['source_origin_sha256'] or result.get('source_scope_sha256')!=admitted['scope_sha256'] or
                    any(admitted.get(key)!=source.get(key) for key in stable) or
                    type(result.get('native_commands_sent')) is not int or result['native_commands_sent']!=0 or
                    any(result.get(key) is not False for key in ('native_command_called','native_lease_renewed','physical_execution_authorized','hold_contact_or_stationarity_claimed')) or
                    actual_rev!=revision+(0 if result['already_armed'] else 1)):
                raise ValueError('EXACT_ORIGINAL_METADATA_LATCH_RECEIPT_REQUIRED')
            if role=='giver' and not self.service._durable_giver(interlock):raise ValueError('ACTUAL_DURABLE_GIVER_LATCH_REQUIRED_AFTER_ARM')
            pointer=self.service.native_receipts.archive(row,spec,role,'parked_scope_arm',result,actor,
                intent_evidence={'source_scope':source,'request':body,'root_context':self._pin(row,spec),'metadata_only':True})
            custody=self.service.native_receipts.publish(row,spec,pointer,actor)
            current=self.service.native_controls._fresh(row,spec,actor)
            after=self.require_current(current,spec,(role,),actor=actor)['roles'][role]
            if after['facts'].get('armed') is not True:raise ValueError('CURRENT_SOURCE_LATCH_NOT_ARMED_AFTER_METADATA_RECEIPT')
            return {'source_receipt':result,'control_receipt':custody,'current_source_scope':after['source_scope'],
                'native_commands_sent':0,'source_latch_armed':True,'stationarity_or_holding_claimed':False}
        except Exception:
            if isinstance(result,dict) and result and pointer is None:
                try:self.service.native_receipts.archive(row,spec,role,'parked_scope_arm',result,actor,allow_unpublished=True,
                    intent_evidence={'source_scope':source,'request':body,'metadata_only':True})
                except Exception:pass  # Preserve original error; no authority or unarm from custody failure.
            raise
