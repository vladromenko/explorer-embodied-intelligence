"""ENGINEERING_GLUE: durable known receiver DATA starts; no control or grant RPC."""
from copy import deepcopy
import fcntl
import json
import re
import threading
import uuid

from explorer_shared.contracts import canonical,digest
from explorer_shared.cooperative_recording05 import write_original,sha_file
from explorer_shared.source_capture_ack import validate_capture_start


class CooperativeCaptureStarts:
    def __init__(self,service):
        self.service=service;self.root=service.root/'native-capture-starts';self.root.mkdir(exist_ok=True)
        self.owner=(self.root/'.owner.lock').open('a+b');fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.lock=threading.RLock()
    def close(self):self.owner.close()
    def _folder(self,session,attempt):
        if any(not re.fullmatch('[0-9a-f]{32}',str(value)) for value in (session,attempt)):
            raise ValueError('Known server capture intent IDs required')
        folder=self.root/session/attempt
        if not folder.resolve().is_relative_to(self.root.resolve()):raise ValueError('Capture intent escaped managed DATA root')
        return folder
    def _load(self,pointer):
        if not isinstance(pointer,dict) or set(pointer)!={'session_id','attempt_id','intent_sha256'}:
            raise ValueError('Exact saved DATA intent pointer required')
        folder=self._folder(pointer.get('session_id'),pointer.get('attempt_id'));path=folder/'intent.json'
        if sha_file(path)!=pointer.get('intent_sha256'):raise ValueError('Known capture intent bytes changed')
        intent=json.loads(path.read_bytes())
        if (not isinstance(intent,dict) or intent.get('session_id')!=pointer['session_id'] or
                intent.get('attempt_id')!=pointer['attempt_id']):
            raise ValueError('Capture intent pointer changed')
        return folder,intent
    def _ack(self,folder,intent,pointer):
        path=folder/'ack.json';checksum=folder/'ack.sha256'
        if not path.exists():return None
        if not checksum.is_file() or sha_file(path)!=checksum.read_text().strip():
            raise ValueError('Known DATA ACK is incomplete or changed; retain unknown originals')
        value=json.loads(path.read_bytes())
        if not isinstance(value,dict) or not isinstance(value.get('reference'),dict):raise ValueError('Known DATA ACK object required')
        reference=value['reference']
        if (value.get('schema')!='explorer_cooperative_known_capture_ACK_v1' or
                value.get('intent_sha256')!=pointer['intent_sha256'] or
                reference.get('known_capture_intent')!=pointer or
                value.get('recording_id')!=reference.get('recording_id') or
                reference.get('capture_actor_id')!=intent['actor_id'] or
                value.get('control_authority_adopted') is not False or value.get('native_commands_dispatched') is not False):
            raise ValueError('Known DATA ACK context changed')
        ident=validate_capture_start(intent['actor_id'],intent['record_start_body'],intent['permission'],
            intent['source_identity'],reference.get('native_start_result'))
        if ident!=value['recording_id']:raise ValueError('Known DATA ACK original identity changed')
        return value
    def projection(self,row):
        pointer=(row.get('native_capture_start_intents') or {}).get('receiver')
        if not pointer:return None
        try:
            folder,intent=self._load(pointer)
            ack=self._ack(folder,intent,pointer);abort=folder/'aborted-before-rpc.json'
            return {'role':'receiver','attempt_id':intent['attempt_id'],'state':'ACKNOWLEDGED' if ack else
                'ABORTED_BEFORE_RPC' if abort.exists() else 'PENDING_DATA_RECONCILIATION',
                'recording_id':ack['recording_id'] if ack else None,
                'control_authority_adopted':False,'native_commands_dispatched':False}
        except (OSError,ValueError,KeyError,TypeError) as error:
            return {'role':'receiver','attempt_id':pointer.get('attempt_id') if isinstance(pointer,dict) else None,'state':'UNKNOWN_METADATA',
                'error':str(error),'recording_id':None,'control_authority_adopted':False,'native_commands_dispatched':False}
    def require_no_pending(self):
        with self.lock:
            for path in self.root.glob('*/*/intent.json'):
                existing=json.loads(path.read_bytes())
                pointer={'session_id':existing['session_id'],'attempt_id':existing['attempt_id'],'intent_sha256':sha_file(path)}
                aborted=path.with_name('aborted-before-rpc.json')
                if aborted.exists():
                    if json.loads(aborted.read_bytes())!={'source_record_start_issued':False}:
                        raise ValueError('Known pre-RPC abort metadata changed')
                elif not self._ack(path.parent,existing,pointer):
                    raise ValueError('Known receiver start is pending; explicitly reconcile DATA before any new start')
    def begin(self,row,prepared,permission_status):
        """Persist exact body/permission +separate DATA identity before record/start."""
        source=self.service._request('receiver')('GET','/station/status',None)
        grant=permission_status['grant'];stamp=permission_status.get('source_stamp') or {}
        calibrations={role:(source.get('roles') or {}).get(role) or {} for role in ('leader','follower')}
        if (source.get('available') is not True or source.get('control_epoch')!=grant.get('control_epoch') or source.get('recording') is not None or
                source.get('mode')!=grant.get('native_mode') or
                any(profile.get('verified_on_connected_hardware') is not True or
                    not re.fullmatch('[0-9a-f]{64}',str(profile.get('calibration_sha256'))) for profile in calibrations.values()) or
                calibrations['follower']['calibration_sha256']!=grant.get('follower_calibration_sha256') or
                stamp.get('boot_id')!=grant.get('boot_id') or stamp.get('clock_domain')!=grant.get('clock_domain') or
                (source.get('measurements') or {}).get('clock_domain')!=grant.get('clock_domain')):
            raise ValueError('Exact pre-start DATA source identity/native owner required')
        with self.lock:
            self.require_no_pending()
            attempt=uuid.uuid4().hex;body=deepcopy(prepared['body'])
            body['context']['source_capture_attempt_id']=attempt
            intent={'schema':'explorer_cooperative_known_capture_start_v1','session_id':row['session_id'],
                'epoch':row['epoch'],'process_session':row['process_session'],'attempt_id':attempt,'role':'receiver',
                'actor_id':prepared['capture_actor_id'],'record_start_body':body,
                'permission':deepcopy(prepared['capture_permission']),
                'original_grant_literal':deepcopy(grant),
                'source_identity':{'boot_id':grant['boot_id'],'clock_domain':grant['clock_domain'],
                    'calibration_sha256':{role:profile['calibration_sha256'] for role,profile in calibrations.items()}},
                'source_capture':deepcopy(prepared.get('source_capture')),
                'control_authority_created':False,'source_rpc_issued_by_this_intent_builder':False}
            folder=self._folder(row['session_id'],attempt);folder.mkdir(parents=True)
            raw=canonical(intent);write_original(folder/'intent.json',raw)
            pointer={'session_id':row['session_id'],'attempt_id':attempt,'intent_sha256':digest(intent)}
            try:
                with self.service.workflow.lock:
                    current=self.service.workflow._session(row['session_id'])
                    if (self.service.closed or current['epoch']!=row['epoch'] or
                            current['status'] in ('STOPPED','INTERRUPTED','COMPLETED')):
                        raise ValueError('Session changed before recording DATA RPC; start not issued')
                    current.setdefault('native_capture_start_intents',{})['receiver']=pointer
                    self.service.workflow._save(current)
            except Exception:
                write_original(folder/'aborted-before-rpc.json',canonical({'source_record_start_issued':False}));raise
            prepared['body']=body;prepared['capture_intent']=pointer
        return pointer
    def acknowledge(self,prepared,returned_id=None):
        pointer=prepared['capture_intent'];folder,intent=self._load(pointer)
        if folder.joinpath('aborted-before-rpc.json').exists():raise ValueError('Capture intent was aborted before source IO')
        request=self.service._request('receiver')
        packet=request('GET','/so101-capture/status',None);native=request('GET','/station/status',None)
        stamp=request('GET','/station/observed-test/status',None).get('source_stamp')
        identity=validate_capture_start(intent['actor_id'],intent['record_start_body'],intent['permission'],
            intent['source_identity'],packet,native=native,source_stamp=stamp)
        if returned_id is not None and returned_id!=identity:raise ValueError('Returned source recording differs from known original')
        reference={'recording_id':identity,'episode_id':identity,'role':'receiver','capture_profile':'common',
            'capture_actor_id':intent['actor_id'],'native_start_result':deepcopy(packet),
            'source_context_sha256':digest(intent['record_start_body']),'source_capture':deepcopy(intent['source_capture']),
            'known_capture_intent':deepcopy(pointer),'metadata_only_no_control_started':True}
        value={'schema':'explorer_cooperative_known_capture_ACK_v1','recording_id':identity,
            'intent_sha256':pointer['intent_sha256'],'reference':reference,
            'control_authority_adopted':False,'native_commands_dispatched':False}
        with self.lock:
            path=folder/'ack.json'
            if path.exists():
                previous=self._ack(folder,intent,pointer)
                if previous['recording_id']!=identity:raise ValueError('Known capture ACK cannot change original identity')
                return previous
            write_original(path,canonical(value))
            write_original(folder/'ack.sha256',(digest(value)+'\n').encode())
        return value
    def reconcile(self,row,actor):
        pointer=(row.get('native_capture_start_intents') or {}).get('receiver')
        if not pointer:raise ValueError('This session has no known receiver DATA start intent')
        folder,intent=self._load(pointer)
        if intent['session_id']!=row['session_id'] or intent['actor_id']!=actor:
            raise ValueError('Only the original authenticated DATA capture actor may reconcile its intent')
        return self.acknowledge({'capture_intent':pointer})
