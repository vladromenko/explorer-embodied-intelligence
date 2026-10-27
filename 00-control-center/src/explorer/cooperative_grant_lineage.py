"""ENGINEERING_GLUE: explicit source-receipted USER grant succession.

The original cooperative binding is immutable. A new grant is accepted only
after the source validates its parked successor and Root archives that reply.
This records lineage, not a lease, observation renewal or dispatch permission.
"""
from copy import deepcopy
import hashlib
import json

from explorer_shared.contracts import digest
from .cooperative_giver_phases import CooperativeGiverPhases


class CooperativeGrantLineage:
    def __init__(self,service):self.service=service
    def expected(self,role,row,spec):
        initial=row['observed_test_binding']['native_grants'][role]
        entry=(row.get('native_grant_successors') or {}).get(role)
        if entry is None:return deepcopy(initial)
        if role=='giver':return self._giver_expected(row,spec,entry,initial)
        if (entry.get('context')!=self.service.observed_setup._context(row,spec) or
                entry.get('initial_permission_sha256')!=digest(initial) or
                entry.get('entry_sha256')!=digest({key:value for key,value in entry.items() if key!='entry_sha256'})):
            raise ValueError('Original cooperative grant succession anchor changed')
        pointer=entry['receipt'];path,_=self.service.artifact(pointer['kind'],pointer['receipt_id'])
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pointer['sha256']:raise ValueError('Native successor artifact SHA changed')
        document=json.loads(raw)
        if document.get('context')!=entry['context'] or document.get('actor_id')!=row['observed_test_binding']['actor_id']:
            raise ValueError('Native successor artifact belongs to another intent/operator')
        if document.get('authority_publication_allowed') is False:raise ValueError('Unpublished custody cannot change native grant lineage')
        if role=='giver':
            self._giver_source(row,spec,entry['permission'],document.get('source_response') or {},document.get('intent_evidence'),entry['previous_permission'])
        source=document.get('source_receipt') or document.get('source_response') or {}
        event=source.get('successor') or {}
        previous,new=event.get('previous_owner') or {},event.get('new_owner') or {}
        key='grant_id' if role=='receiver' else 'authorization_id'
        if (previous.get(key)!=entry['previous_permission'].get(key) or new.get(key)!=entry['permission'].get(key) or
                event.get('native_command_called') not in (None,False) or
                event.get('execution_authority') not in (None,False)):
            raise ValueError('Exact archived metadata successor lineage required')
        return deepcopy(entry['permission'])
    def _giver_expected(self,row,spec,entry,initial):
        # Each prior entry is sealed inside its successor's immutable source
        # receipt, not only a replaceable session projection. Walk successors,
        # never all control events or the bounded 64-row UI history.
        selected=deepcopy(entry.get('permission'));seen=set()
        for _ in range(1024):
            checksum=entry.get('entry_sha256')
            if (checksum in seen or not isinstance(checksum,str) or
                    checksum!=digest({key:value for key,value in entry.items() if key!='entry_sha256'}) or
                    entry.get('context')!=self.service.observed_setup._context(row,spec) or
                    entry.get('initial_permission_sha256')!=digest(initial)):
                raise ValueError('Original giver succession chain/anchor changed')
            seen.add(checksum);pointer=entry['receipt']
            if pointer.get('kind')!='native-control':raise ValueError('Original native giver custody required')
            path,_=self.service.artifact('native-control',pointer['receipt_id']);raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=pointer['sha256']:raise ValueError('Native giver succession artifact SHA changed')
            document=json.loads(raw);intent=document.get('intent_evidence') or {}
            if (document.get('context')!=entry['context'] or document.get('actor_id')!=row['observed_test_binding']['actor_id'] or
                    document.get('role')!='giver' or document.get('operation')!='native_phase_adopt' or
                    document.get('authority_publication_allowed') is not True):
                raise ValueError('Exact published native giver succession custody required')
            actual=self._giver_source(row,spec,entry['permission'],document.get('source_response') or {},intent,entry['previous_permission'])
            if actual!=entry['permission']:raise ValueError('Native giver permission differs from original source successor')
            prior=intent.get('previous_lineage')
            if prior is None:
                if entry.get('previous_entry_sha256') is not None or entry['previous_permission']!=initial:
                    raise ValueError('Giver succession must terminate at immutable original USER grant')
                return selected
            if (not isinstance(prior,dict) or entry.get('previous_entry_sha256')!=prior.get('entry_sha256') or
                    entry['previous_permission']!=prior.get('permission')):
                raise ValueError('Native giver succession predecessor changed')
            entry=prior
        raise ValueError('Native giver succession chain exceeds bounded verification; manual review required')
    def _giver_source(self,row,spec,permission,source,intent,previous):
        actor=row['observed_test_binding']['actor_id'];context=self.service.observed_setup._context(row,spec)
        intent=intent or {};selection=intent.get('selection') or {};before=selection.get('source_before') or {};record=selection.get('permission_record') or {}
        event=source.get('successor') or {};old,new=event.get('previous_owner') or {},event.get('new_owner') or {}
        boundary=source.get('interlock') or {};parked=boundary.get('parked') or {};origin=event.get('origin') or {};proof=new.get('observed_source_authorization') or {}
        expected_id=permission.get('authorization_id');mode=self.service.observed_setup.giver_mode(row)
        frame=proof.get('camera_frame') or {};relation=event.get('verified_clock_relation') or {}
        cutoff=((before.get('parked') or {}).get('original_stop') or {}).get('native_monotonic_ns')
        received,created,issued=frame.get('received_monotonic_ns'),proof.get('native_monotonic_ns'),event.get('native_monotonic_ns')
        same_clock=(frame.get('clock_domain')==event.get('clock_domain') or
            frame.get('clock_domain')=='native_kernel_monotonic:'+str(event.get('boot_id')) and relation.get('same_source_clock_callable') is True)
        if (source.get('native_command_called') is not False or source.get('execution_authority') is not False or
                event.get('schema')!='explorer_native_giver_phase_event_v1' or event.get('kind')!='NEW_USER_COMMAND_ESTIMATE_SUCCESSOR' or
                event.get('source_event_sha256')!=digest({key:value for key,value in event.items() if key!='source_event_sha256'}) or
                not isinstance(event.get('clock_domain'),str) or not event['clock_domain'] or
                not isinstance(event.get('boot_id'),str) or not event['boot_id'] or
                event.get('native_command_called') is not False or event.get('measured_hold_or_stationarity_claimed') is not False or
                event.get('phase_id')!=(intent.get('phase_pin') or {}).get('protocol_state') or
                old!=before.get('binding') or old.get('authorization_id')!=previous.get('authorization_id') or new!=boundary.get('binding') or
                not expected_id or new.get('authorization_id')!=expected_id or expected_id==old.get('authorization_id') or
                any(old.get(key)!=row[field] or new.get(key)!=row[field] for key,field in
                    (('run_id','session_id'),('object_id','protocol_object_id'),('coordinator_epoch','epoch'))) or
                proof.get('actor_id')!=actor or proof.get('input_mode')!=mode or
                (old.get('observed_source_authorization') or {}).get('actor_id')!=actor or
                record.get('role')!='giver' or record.get('actor')!=actor or record.get('context')!=context or
                record.get('authorization_id')!=expected_id or record.get('spec_sha256')!=spec['spec_sha256'] or
                record.get('authorization_receipt_sha256')!=digest(proof) or proof.get('event_id')!=expected_id or
                proof.get('kind')!='EXPLICIT_USER_OBSERVED_AUTHORIZATION' or proof.get('boot_id')!=event.get('boot_id') or
                frame.get('boot_id')!=event.get('boot_id') or proof.get('displayed_jpeg_sha256')!=frame.get('jpeg_sha256') or
                relation.get('frame_domain')!=frame.get('clock_domain') or relation.get('native_domain')!=event.get('clock_domain') or
                not same_clock or any(type(value) is not int or value<0 for value in (cutoff,received,created,issued)) or
                not cutoff<=received<=created<=issued or
                selection.get('previous_permission')!=previous or
                selection.get('initial_permission_sha256')!=digest(row['observed_test_binding']['native_grants']['giver']) or
                parked.get('park_request_id')!=(before.get('parked') or {}).get('park_request_id') or
                origin.get('park_request_id')!=parked.get('park_request_id') or parked.get('origin')!=origin or
                parked.get('stage')!='PARKED_NEW_USER' or parked.get('model')!=before.get('model')):
            raise ValueError('Exact original-root/new-intent USER giver command-estimate succession required')
        CooperativeGiverPhases.validate_origin(origin,event)
        prior=before.get('constraint');expected=deepcopy(prior)
        if expected is not None:
            expected['authorization_id']=expected_id
            expected['previous_authorization_lineage']={'previous_registration_sha256':digest(prior),
                'previous_authorization_id':previous['authorization_id'],'reason':'exact_same_constraint_model_Spec_explicit_USER_successor'}
        if parked.get('constraint')!=expected:raise ValueError('Original stationary constraint/model/Spec registration was changed or dropped')
        if row['giver_input_mode']=='policy':
            model={key:spec['subskills']['giver'][key] for key in ('model_id','checkpoint_sha256','spec_sha256')}
            if parked.get('model')!=model:raise ValueError('Pinned source policy model differs from original Root SkillSpec')
        return {'authorization_id':expected_id,'input_mode':mode,'actor_id':actor,
            'control_epoch':permission.get('control_epoch'),'owner_generation':new.get('native_generation')}
    def giver(self,row,spec,permission,source,pointer,*,actor):
        current=self.service.native_controls._fresh(row,spec,actor);previous=self.expected('giver',current,spec)
        path,_=self.service.artifact('native-control',pointer['receipt_id']);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pointer['sha256']:raise ValueError('Archived native giver successor changed')
        document=json.loads(raw)
        if (document.get('context')!=self.service.observed_setup._context(current,spec) or document.get('actor_id')!=actor or
                document.get('operation')!='native_phase_adopt' or document.get('source_response')!=source or
                document.get('authority_publication_allowed') is not True):raise ValueError('Actual published custody of this source successor required')
        selected=self._giver_source(current,spec,permission,source,document.get('intent_evidence'),previous)
        before=deepcopy((current.get('native_grant_successors') or {}).get('giver'))
        entry={'context':document['context'],'initial_permission_sha256':digest(current['observed_test_binding']['native_grants']['giver']),
            'previous_permission':previous,'permission':selected,'previous_entry_sha256':None if before is None else before['entry_sha256'],
            'receipt':{**deepcopy(pointer),'kind':'native-control'},'native_lease_or_observation_renewed':False}
        entry['entry_sha256']=digest(entry)
        with self.service.workflow.lock:
            actual=self.service.native_controls._fresh(row,spec,actor)
            if (actual.get('native_grant_successors') or {}).get('giver')!=before:raise ValueError('Root giver grant lineage changed during source custody')
            actual.setdefault('native_grant_successors',{})['giver']=entry;self.service.workflow._save(actual)
        with self.service.lock:self.service.transports.pop((row['session_id'],'giver'),None)
        return deepcopy(entry)
    def receiver(self,row,spec,permission,source,pointer,*,actor):
        current=self.service.native_controls._fresh(row,spec,actor)
        previous=self.expected('receiver',current,spec);event=source.get('successor') or {}
        old,new=event.get('previous_owner') or {},event.get('new_owner') or {}
        origin=source.get('source_hold_origin') or {};context=self.service.observed_setup._context(current,spec)
        if (source.get('schema')!='explorer_station_cooperative_successor_receipt_v1' or source.get('native_commands_sent')!=0 or
                source.get('physical_execution_authorized') is not False or source.get('parked_observation_only') is not True or
                old.get('grant_id')!=previous.get('grant_id') or old.get('provider_process')!=previous.get('provider_process') or
                old.get('grant_id')==permission.get('grant_id') or origin.get('binding')!=self.service.observed_setup.receiver_binding(row,spec) or
                any(new.get(key)!=permission.get(key) for key in ('grant_id','provider_process','control_epoch','actor_id','boot_id','clock_domain','follower_calibration_sha256')) or
                any(old.get(key)!=new.get(key) for key in ('provider_process','actor_id','boot_id','clock_domain','follower_calibration_sha256')) or
                permission.get('actor_id')!=actor or permission.get('binding')!=origin.get('binding') or
                event.get('native_commands_sent')!=0 or event.get('execution_authority') is not False):
            raise ValueError('Exact original-root/source-owned measured hold successor required')
        selected={key:deepcopy(permission.get(key)) for key in ('grant_id','provider_process','actor_id','control_epoch')}
        selected['binding_sha256']=digest(permission['binding'])
        before=deepcopy((current.get('native_grant_successors') or {}).get('receiver'))
        entry={'context':context,'initial_permission_sha256':digest(current['observed_test_binding']['native_grants']['receiver']),
            'previous_permission':previous,'permission':selected,'previous_entry_sha256':None if before is None else before['entry_sha256'],
            'receipt':{**deepcopy(pointer),'kind':'receiver-metadata'},'native_lease_or_observation_renewed':False}
        entry['entry_sha256']=digest(entry)
        with self.service.workflow.lock:
            actual=self.service.native_controls._fresh(row,spec,actor)
            if (actual.get('native_grant_successors') or {}).get('receiver')!=before:
                raise ValueError('Root native grant lineage changed while archiving successor')
            actual.setdefault('native_grant_successors',{})['receiver']=entry
            self.service.workflow._save(actual)
        # Neither an old transport nor its model prediction gets rebound here.
        with self.service.lock:self.service.transports.pop((row['session_id'],'receiver'),None)
        return deepcopy(entry)
