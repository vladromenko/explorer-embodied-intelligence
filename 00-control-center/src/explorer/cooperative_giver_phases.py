"""ENGINEERING_GLUE: one-use explicit Root USER giver phase facade.

Refresh uses authenticated GETs only. Source/control IDs are server-derived;
NEW authorization must belong to this Root intent, actor and immutable Spec.
Park/adopt do not attach a transport or renew a running native lease.
"""
from copy import deepcopy
import math

from explorer_shared.contracts import digest

OPERATIONS=('native_phase_park','native_phase_adopt','native_phase_begin','native_phase_resume')
SOURCE_OPERATIONS={name:'phase/'+name.removeprefix('native_phase_') for name in OPERATIONS}


class CooperativeGiverPhases:
    def __init__(self,native):self.native=native;self.service=native.service
    def permission_record(self,row,spec,pair,actor):
        permission=pair['permission'];identity=permission.get('authorization_id');proof=permission.get('authorization_receipt') or {}
        if (permission.get('execution_permission') is not True or not identity or proof.get('actor_id')!=actor or
                permission.get('input_mode')!=self.service.observed_setup.giver_mode(row)):
            raise ValueError('NEW current source USER authorization for this input mode required')
        context=self.service.observed_setup._context(row,spec)
        with self.service.observed_setup.lock:
            matches=[deepcopy(record) for record in self.service.observed_setup.permissions.values() if record['role']=='giver' and
                record['context']==context and record.get('actor')==actor and record['grants'].get('authorization_id')==identity and
                (record.get('spec') or {}).get('spec_sha256')==spec['spec_sha256']]
        if not matches:raise ValueError('Current NEW giver permission must be confirmed through this exact Root intent')
        record=matches[-1]
        return {'role':'giver','context':deepcopy(record['context']),'actor':record['actor'],
            'authorization_id':identity,'authorization_receipt_sha256':digest(proof),'spec_sha256':record['spec']['spec_sha256']}
    def _base(self,row,spec,pair,actor):
        source=pair['source'];native=source.get('native') or {};boundary=source.get('release_boundary') or {};binding=boundary.get('binding') or {}
        persistence=boundary.get('persistence') or {};expected=self.service.grant_lineage.expected('giver',row,spec)
        if (source.get('schema')!='explorer_native_cooperative_owner_v1' or
                source.get('capabilities',{}).get('explicit_user_giver_phase_http') is not True or
                row['giver_input_mode'] not in ('policy','keyboard','browser_gamepad') or
                not all(persistence.get(key) is True for key in ('installed','healthy','initialized','fail_closed_publication_gate')) or
                persistence.get('recovery_required') is not False or persistence.get('pending') is not False or
                not isinstance(persistence.get('state_id'),str) or type(persistence.get('revision')) is not int or
                binding.get('run_id')!=row['session_id'] or binding.get('object_id')!=row['protocol_object_id'] or
                binding.get('coordinator_epoch')!=row['epoch'] or binding.get('authorization_id')!=expected['authorization_id'] or
                (binding.get('observed_source_authorization') or {}).get('actor_id')!=actor or
                (binding.get('observed_source_authorization') or {}).get('input_mode')!=self.service.observed_setup.giver_mode(row)):
            raise ValueError('Exact healthy source giver scope anchored to the original Root USER binding required')
        return source,native,boundary,binding,persistence,expected
    def describe(self,row,spec,pair,actor):
        operations={name:{'implemented':False,'available':False,'choice_id':None} for name in OPERATIONS}
        selections={};reason=None
        try:
            source,native,boundary,binding,persistence,expected=self._base(row,spec,pair,actor)
            for item in operations.values():item['implemented']=True
            parked=boundary.get('parked');current=pair['permission'].get('authorization_id')
            original={'source_before':{'binding':deepcopy(binding),'parked':deepcopy(parked),'model':deepcopy(source.get('policy')),
                    'constraint':deepcopy(source.get('registered_action_constraint'))},
                'initial_permission_sha256':digest(row['observed_test_binding']['native_grants']['giver']),
                'previous_permission':deepcopy(expected),'permission_record':None}
            owned=self.native.owned_scope(row,'giver')
            if not parked and owned is not None and current in (None,expected['authorization_id']):
                known=self.native.giver_expected(owned['pair'],owned['result'],owned['operation'])
                if all(native.get(key)==known.get(key) for key in ('control_epoch','owner_generation','active')):
                    operations['native_phase_park']['available']=True;selections['native_phase_park']=deepcopy(original)
            if parked:
                token=parked.get('park_request_id');stage=parked.get('stage')
                if not isinstance(token,str) or not token:raise ValueError('Source-owned giver parked token unavailable')
                if (boundary.get('original_quiescence') or {}).get('ready') is not True or boundary.get('reference_fault') is not None:
                    raise ValueError('Original giver command stream/reference is not ready for a parked successor')
                record=self.permission_record(row,spec,pair,actor)
                original['permission_record']=record
                if current!=expected['authorization_id'] and stage in ('STOPPED_BARRIER','PARKED_NEW_USER') and not native.get('active') and native.get('stop_latched') is True:
                    operations['native_phase_adopt']['available']=True;selections['native_phase_adopt']=deepcopy(original)
                elif current==expected['authorization_id']:
                    if row['giver_input_mode']=='policy' and stage=='PARKED_NEW_USER' and not native.get('active') and native.get('stop_latched') is True:
                        operations['native_phase_begin']['available']=True;selections['native_phase_begin']=deepcopy(original)
                    if (row['giver_input_mode']=='policy' and stage=='BEGIN_CLUTCHED' and native.get('active') is True and native.get('state')=='CLUTCHED' or
                            row['giver_input_mode']!='policy' and stage=='PARKED_NEW_USER' and not native.get('active') and native.get('stop_latched') is True):
                        operations['native_phase_resume']['available']=True;selections['native_phase_resume']=deepcopy(original)
            if row['giver_input_mode']=='policy' and operations['native_phase_begin']['available']:
                self.native._ready_prediction(row,spec,'giver')
        except (ValueError,OSError,KeyError,TypeError) as error:
            reason=str(error)
            for item in operations.values():item.update(available=False,reason=reason)
            selections={}
        return operations,selections,reason
    def call(self,operation,row,spec,pair,selection,actor):
        source,native,boundary,binding,persistence,expected=self._base(row,spec,pair,actor)
        described,selections,_=self.describe(row,spec,pair,actor)
        if not described[operation]['available'] or selections.get(operation)!=selection:
            raise ValueError('Fresh exact source/Root intent phase selection changed')
        body={key:binding[key] for key in ('run_id','object_id','coordinator_epoch')}
        body.update(authorization_id=pair['permission'].get('authorization_id') or expected['authorization_id'],
            expected_state_id=persistence['state_id'],expected_revision=persistence['revision'],phase_id=row['protocol_state'],
            observing=True,neutral=True,user_observed_test_confirmed=True)
        parked=boundary.get('parked') or {}
        if operation=='native_phase_park':
            body.update({'control_epoch':native['control_epoch']} if row['giver_input_mode']=='policy' else {'expected_native_generation':native['owner_generation']})
        else:body['park_request_id']=parked['park_request_id']
        if operation=='native_phase_adopt':
            body.update(previous_authorization_id=binding['authorization_id'],previous_native_generation=binding['native_generation'],
                previous_control_epoch=binding.get('native_control_epoch'))
        if operation=='native_phase_resume' and row['giver_input_mode']=='policy':body['control_epoch']=native['control_epoch']
        with self.service.lock:self.service.transports.pop((row['session_id'],'giver'),None)
        result=self.service._request('giver')('POST','/legacy/explorer/cooperative/'+SOURCE_OPERATIONS[operation],body)
        if not isinstance(result,dict):raise ValueError('Parsed source giver phase reply unavailable')
        return result,{'selection':deepcopy(selection),'request':body,'phase_pin':self.native._phase_pin(row),
            'previous_lineage':deepcopy((row.get('native_grant_successors') or {}).get('giver'))}
    def validate(self,operation,row,spec,pair,result,intent):
        before=intent['selection']['source_before'];boundary=result.get('interlock') or {};parked=boundary.get('parked')
        persistence=boundary.get('persistence') or {};previous=before['binding'];request=intent['request']
        if (persistence.get('state_id')!=request['expected_state_id'] or type(persistence.get('revision')) is not int or
                persistence['revision']<=request['expected_revision'] or boundary.get('armed') is not pair['source']['release_boundary'].get('armed') or
                not all(persistence.get(key) is True for key in ('installed','healthy','initialized','fail_closed_publication_gate')) or
                persistence.get('recovery_required') is not False or persistence.get('pending') is not False):
            raise ValueError('Actual healthy source phase CAS/latch receipt required')
        if operation=='native_phase_adopt':
            if result.get('native_command_called') is not False or result.get('execution_authority') is not False:
                raise ValueError('Source USER successor must be metadata only')
            event=result.get('successor') or {}
            if parked is None or parked.get('stage')!='PARKED_NEW_USER' or event.get('previous_owner')!=previous or event.get('new_owner')!=boundary.get('binding'):
                raise ValueError('Actual source previous/new parked owner receipt required')
        elif operation=='native_phase_park':
            event=result.get('park') or {}
            original=parked.get('original_stop') if isinstance(parked,dict) else None
            payload={key:value for key,value in event.items() if key not in ('source_event_ordinal','prior_event_chain_sha256','source_event_sha256')}
            if (result.get('native_command_called') is not True or event.get('kind')!='ORIGINAL_CANCEL_ONLY_PARK' or
                    event.get('previous_owner')!=previous or not isinstance(parked,dict) or original!=payload or
                    parked.get('stage')!='STOPPED_BARRIER' or not isinstance(parked.get('park_request_id'),str)):
                raise ValueError('Actual original cancel-only parked barrier receipt required')
        else:
            history=boundary.get('phase_history') or [];event=history[-1] if history else {};native=result.get('native') or {}
            expected='EXPLICIT_USER_BEGIN_CLUTCHED' if operation=='native_phase_begin' else 'EXPLICIT_USER_PHASE_RESUMED'
            if (result.get('native_command_called') is not True or event.get('kind')!=expected or event.get('native_result')!=native or
                    (boundary.get('binding') or {}).get('authorization_id')!=request['authorization_id']):
                raise ValueError('Actual original source phase control return required')
            state=self.native.giver_expected(pair,result,operation)
            if operation=='native_phase_begin':
                if (not isinstance(parked,dict) or parked.get('stage')!='BEGIN_CLUTCHED' or state.get('state')!='CLUTCHED' or
                        state.get('active') is not True or not state.get('control_epoch') or
                        state['control_epoch']==pair['source']['native'].get('control_epoch')):
                    raise ValueError('Exact new source CLUTCHED epoch/base-zero return required')
            elif parked is not None or state.get('stop_latched') is not False or state.get('native_lease_remaining_s',0)<=0:
                raise ValueError('Current explicitly resumed original source scope required')
        if (event.get('schema')!='explorer_native_giver_phase_event_v1' or event.get('phase_id')!=row['protocol_state'] or
                event.get('source_event_sha256')!=digest({key:value for key,value in event.items() if key!='source_event_sha256'}) or
                type(event.get('source_event_ordinal')) is not int or event['source_event_ordinal']<=0 or
                event.get('measured_hold_or_stationarity_claimed') is not False or event.get('native_command_called') is not (operation!='native_phase_adopt') or
                type(event.get('native_monotonic_ns')) is not int or event['native_monotonic_ns']<0 or
                not isinstance(event.get('boot_id'),str) or not event['boot_id'] or not isinstance(event.get('clock_domain'),str) or not event['clock_domain'] or
                event.get('boot_id')!=pair['source']['native'].get('boot_id') or
                event.get('clock_domain')!=pair['source']['native'].get('clock_domain')):
            raise ValueError('Source-native phase/clock/effect provenance required')
    @staticmethod
    def validate_origin(origin,event):
        issued=origin.get('actual_issued') or {};values=issued.get('command9');arm=issued.get('arm_receipt') or {};base=issued.get('base_receipt') or {}
        stamp=issued.get('stamp') or {};arm_stamp=arm.get('stamp') or {};reference=origin.get('original_reference_guard_result') or {}
        mask=origin.get('measured_feedback_mask')
        if (origin.get('schema')!='explorer_native_giver_park_command_context_v1' or origin.get('state_source')!='command_estimate' or
                origin.get('measured_feedback') is not None or not isinstance(mask,list) or len(mask)!=6 or any(value is not False for value in mask) or
                origin.get('measured_hold_or_stationarity_claimed') is not False or
                not isinstance(values,list) or len(values)!=9 or any(type(value) not in (int,float) or not math.isfinite(value) for value in values) or
                values[6:]!=[0.,0.,0.] or arm.get('pose_deg')!=values[:6] or base.get('velocity')!=values[6:] or
                arm.get('native_host_publication_returned') is not True or not arm.get('receipt_id') or not base.get('receipt_id') or
                reference.get('servo_deg')!=values[:6] or reference.get('measured') is not False or
                reference.get('boot_id')!=event.get('boot_id') or
                not isinstance(origin.get('original_reference_sha256'),str) or len(origin['original_reference_sha256'])!=64 or
                any(value not in '0123456789abcdef' for value in origin['original_reference_sha256']) or
                any(stamp.get(key)!=event.get(key) or arm_stamp.get(key)!=event.get(key) for key in ('clock_domain','boot_id')) or
                any(type(value) is not int or value<0 for value in (stamp.get('monotonic_ns'),arm_stamp.get('monotonic_ns'),base.get('stamp_ns'),event.get('native_monotonic_ns'))) or
                not arm_stamp['monotonic_ns']<=stamp['monotonic_ns']<=event['native_monotonic_ns'] or
                base['stamp_ns']>stamp['monotonic_ns']):
            raise ValueError('Genuine actual issued9/base-zero command-estimate origin required; never measured feedback')
