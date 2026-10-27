"""ENGINEERING_GLUE: Authenticated Pi -> existing Nano observer -> original native-owner stream.

No device access or motor setting writes; a source sample does not renew the
original native user-observation lease. This client does not run automatically.
"""
import json
import hashlib
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

_CURRENT_AUTHORIZATION=object()


class NativeStreamRejected(RuntimeError):
    """Bounded authenticated Source reason, never a manufactured command state."""
    def __init__(self,path,status,raw):
        try:packet=json.loads(raw);reason=packet.get('detail') or packet.get('error')
        except (ValueError,UnicodeError,AttributeError):reason=None
        self.source_error={'status':status,'route':path,'reason':reason or 'Source HTTP'+str(status),
            'response_sha256':hashlib.sha256(raw).hexdigest(),'measured_feedback_required':False}
        code='NATIVE_REFERENCE_UNAVAILABLE' if status==409 and ('reference' in path or 'reference' in str(reason).lower()) else 'NATIVE_STREAM_REJECTED'
        super().__init__(code+': '+str(self.source_error['reason']))


class NativeStreamClient:
    def __init__(self, nano_observer_url, token, *, timeout=1., opener=urlopen):
        parsed = urlsplit(nano_observer_url)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or
                parsed.query or parsed.fragment or parsed.path not in ('','/') or
                not isinstance(token,str) or len(token)<16):
            raise ValueError('Explicit authenticated Nano observer origin required')
        self.origin, self.token, self.timeout, self.opener = nano_observer_url.rstrip('/'),token,timeout,opener
        self.prefix='/legacy/explorer/retarget/'
        self.authorization_id=None

    def _request(self, operation, body=None, *, preserve_source_bytes=False,authorization_id=_CURRENT_AUTHORIZATION):
        if body is not None and operation in ('begin','recenter','resume','input','base'):
            body={**body,'authorization_id':self.authorization_id if authorization_id is _CURRENT_AUTHORIZATION else authorization_id}
        return self._send(self.prefix+operation,body,preserve_source_bytes=preserve_source_bytes)

    def _send(self,path,body=None,*,preserve_source_bytes=False):
        content = json.dumps(body,allow_nan=False).encode() if body is not None else None
        request = Request(self.origin+path,data=content,
                          headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json',
                                   'Origin':self.origin})
        try:
            with self.opener(request,timeout=self.timeout) as response:
                raw=response.read(8*1024**2+1)
                if len(raw)>8*1024**2:
                    raise ValueError('Native receipt page exceeds its source bound')
                payload = json.loads(raw)
        except HTTPError as exc:
            try:raw=exc.read(32*1024+1)
            finally:exc.close()
            if len(raw)>32*1024:raise RuntimeError('Native rejected response exceeds bounded error receipt') from exc
            raise NativeStreamRejected(path,exc.code,raw) from exc
        if not isinstance(payload,dict):
            raise ValueError('Native owner returned an invalid receipt')
        return {'payload':payload,'source_bytes':raw} if preserve_source_bytes else payload

    def status(self):
        return self._request('status')

    def reference(self):
        return self._request('reference')

    def dispatch_since(self, sequence):
        if type(sequence) is not int or not 0<=sequence<=2**63-1:
            raise ValueError('Nonnegative bounded native dispatch cursor required')
        return self._request('dispatch?after_sequence='+str(sequence))

    def dispatch_source_packet(self, sequence):
        if type(sequence) is not int or not 0<=sequence<=2**63-1:
            raise ValueError('Nonnegative bounded native dispatch cursor required')
        return self._request('dispatch?after_sequence='+str(sequence),preserve_source_bytes=True)

    def begin(self, *, observing, neutral,authorization_id=_CURRENT_AUTHORIZATION):
        return self._request('begin',{'observing':observing,'neutral':neutral},authorization_id=authorization_id)

    def recenter(self, epoch,*,authorization_id=_CURRENT_AUTHORIZATION):
        return self._request('recenter',{'control_epoch':epoch},authorization_id=authorization_id)

    def resume(self, epoch, *, observing,authorization_id=_CURRENT_AUTHORIZATION):
        return self._request('resume',{'control_epoch':epoch,'observing':observing},authorization_id=authorization_id)

    def input(self, epoch, sequence, velocity, evidence,*,authorization_id=_CURRENT_AUTHORIZATION):
        return self._request('input',{'control_epoch':epoch,'source_sequence':sequence,
                                     'joint_velocity_deg_s':velocity,'source_evidence':evidence},authorization_id=authorization_id)

    def keyboard_base(self, epoch, actions, *, observing,authorization_id=_CURRENT_AUTHORIZATION):
        return self._request('base',{'control_epoch':epoch,'actions':actions,'observing':observing},authorization_id=authorization_id)

    def stop(self, epoch):
        return self._request('stop',{'control_epoch':epoch})

    def select_keyboard(self,*,observing,neutral):
        return self._send('/legacy/teleop/select',{'source':'keyboard','observing':observing,'neutral':neutral})

    def capture_status(self):
        return self._send('/native-capture/status')

    def capture_start(self,skill_context):
        return self._send('/native-capture/record/start',{'skill_context':skill_context})

    def capture_stage(self,recording_id,phase_id,**metadata):
        return self._send('/native-capture/record/stage',{'recording_id':recording_id,'phase_id':phase_id,**metadata})

    def capture_finish(self,recording_id,outcome_evidence=None):
        return self._send('/native-capture/record/finish',{'recording_id':recording_id,'outcome_evidence':outcome_evidence})

    def activation_status(self):return self._request('activation/status')
    def activation_challenge(self):return self._request('activation/challenge')
    def activation_authorize(self,body):
        result=self._request('activation/authorize',body)
        self.authorization_id=result.get('authorization_id');return result
    def activation_revoke(self,body=None):
        result=self._request('activation/revoke',body or {})
        self.authorization_id=None;return result


class OriginalNativeStreamReader(NativeStreamClient):
    """Read-only local consumer; cannot invoke any inherited control mutation."""
    def __init__(self,controller_root,*,timeout=1.,opener=urlopen):
        token=(Path(controller_root)/'config/access_token').read_text().strip()
        super().__init__('http://127.0.0.1:8080',token,timeout=timeout,opener=opener)
        self.prefix='/api/explorer/retarget/'

    def _request(self,operation,body=None,**kwargs):
        if body is not None or not (operation in ('status','reference') or
                                    re.fullmatch(r'dispatch\?after_sequence=[0-9]{1,19}',operation)):
            raise PermissionError('Native capture source is GET-only')
        return super()._request(operation,None,**kwargs)

    def _send(self,path,body=None,**kwargs):
        operation=path.removeprefix('/api/explorer/retarget/')
        if (not path.startswith('/api/explorer/retarget/') or body is not None or
                not (operation in ('status','reference') or
                     re.fullmatch(r'dispatch\?after_sequence=[0-9]{1,19}',operation))):
            raise PermissionError('Native capture source is GET-only')
        return super()._send(path,None,**kwargs)
