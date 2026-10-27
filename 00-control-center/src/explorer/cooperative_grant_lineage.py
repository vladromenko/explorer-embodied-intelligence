"""ENGINEERING_GLUE: explicit source-receipted USER grant succession.

The original cooperative binding is immutable. A new grant is accepted only
after the source validates its parked successor and Root archives that reply.
This records lineage, not a lease, observation renewal or dispatch permission.
"""
from copy import deepcopy
import hashlib
import json

from explorer_shared.contracts import digest


class CooperativeGrantLineage:
    def __init__(self,service):self.service=service
    def expected(self,role,row,spec):
        initial=row['observed_test_binding']['native_grants'][role]
        entry=(row.get('native_grant_successors') or {}).get(role)
        if entry is None:return deepcopy(initial)
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
        source=document.get('source_receipt') or document.get('source_response') or {}
        event=source.get('successor') or {}
        previous,new=event.get('previous_owner') or {},event.get('new_owner') or {}
        key='grant_id' if role=='receiver' else 'authorization_id'
        if (previous.get(key)!=entry['previous_permission'].get(key) or new.get(key)!=entry['permission'].get(key) or
                event.get('native_command_called') not in (None,False) or
                event.get('execution_authority') not in (None,False)):
            raise ValueError('Exact archived metadata successor lineage required')
        return deepcopy(entry['permission'])
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
