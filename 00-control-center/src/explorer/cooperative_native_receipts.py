"""ENGINEERING_GLUE: immutable custody of parsed native USER control replies.

Each role has an append-only predecessor chain. The session keeps a bounded
projection, not the only copy of old replies. Artifact I/O never owns the
workflow/STOP lock. A late reply can be archived without restoring authority.
"""
from copy import deepcopy
import json
import re
import uuid

from explorer_shared.contracts import canonical,digest
from explorer_shared.cooperative_recording05 import write_original,sha_file


class CooperativeNativeReceipts:
    def __init__(self,service):
        self.service,self.workflow=service,service.workflow
    def archive(self,row,spec,role,operation,result,actor):
        if role not in ('giver','receiver') or not isinstance(result,dict):
            raise ValueError('Parsed authenticated native role reply required')
        current=self.service.native_controls._fresh(row,spec,actor)
        prior=deepcopy((current.get('native_control_ledger') or {}).get(role))
        identity=uuid.uuid4().hex
        document={'schema':'explorer_cooperative_native_control_evidence_v1','receipt_id':identity,
            'context':self.service.observed_setup._context(row,spec),'actor_id':actor,
            'role':role,'operation':operation,'source_response':deepcopy(result),'previous':prior,
            'ordinal':1 if prior is None else prior['ordinal']+1,
            'received_nx_ns':self.service.clock(),'received_clock_domain':self.workflow.domain,
            'basis':'parsed_authenticated_source_reply_not_hardware_attainment',
            'automatic_native_command_replay_allowed':False,'physical_attainment_claimed':False}
        raw=canonical(document);checksum=digest(document)
        if len(raw)>16*1024**2:raise ValueError('Native control reply exceeds custody bound')
        folder=self.service.root/'native-control'/identity
        write_original(folder/'receipt.json',raw);(folder/'receipt.json').chmod(0o600)
        registration={key:deepcopy(document[key]) for key in ('receipt_id','context','role','operation','ordinal')}
        registration.update(schema='explorer_cooperative_native_control_registration_v1',receipt_sha256=checksum)
        write_original(folder/'registration.json',canonical(registration));(folder/'registration.json').chmod(0o600)
        return {'receipt_id':identity,'sha256':checksum,'role':role,'operation':operation,
            'ordinal':document['ordinal'],'previous':prior,
            'url':'/api/cooperative/artifacts/native-control/'+identity}
    def publish(self,row,spec,pointer,actor):
        # Call only after archive() has completed. No artifact read/write here.
        with self.workflow.lock:
            current=self.service.native_controls._fresh(row,spec,actor)
            heads=current.setdefault('native_control_ledger',{});role=pointer['role']
            if heads.get(role)!=pointer['previous']:
                raise ValueError('Native reply custody predecessor changed; refresh without replay')
            head={key:deepcopy(pointer[key]) for key in ('receipt_id','sha256','ordinal','url')}
            heads[role]=head
            history=current.setdefault('native_user_control_receipts',[])
            history.append({**{key:deepcopy(pointer[key]) for key in ('role','operation','receipt_id','sha256','ordinal','url')},
                'received_nx_ns':self.service.clock(),'received_clock_domain':self.workflow.domain,
                'actor_id':actor,'basis':'parsed_authenticated_source_reply_not_hardware_attainment'})
            current['native_user_control_receipts']=history[-64:]
            self.workflow._save(current)
        return {key:deepcopy(pointer[key]) for key in ('receipt_id','sha256','role','operation','ordinal','url')}
    def artifact(self,identity):
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):
            raise ValueError('Fixed server native control receipt ID required')
        folder=self.service.root/'native-control'/identity;path=folder/'receipt.json'
        if folder.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(self.service.root.resolve()):
            raise ValueError('Managed native control original required')
        registration=json.loads((folder/'registration.json').read_bytes())
        if (registration.get('schema')!='explorer_cooperative_native_control_registration_v1' or
                registration.get('receipt_id')!=identity or sha_file(path)!=registration.get('receipt_sha256')):
            raise ValueError('Original native control receipt changed')
        document=json.loads(path.read_bytes())
        if (document.get('schema')!='explorer_cooperative_native_control_evidence_v1' or
                any(document.get(key)!=registration.get(key) for key in ('receipt_id','context','role','operation','ordinal'))):
            raise ValueError('Original native control receipt context changed')
        return path,'application/json'
