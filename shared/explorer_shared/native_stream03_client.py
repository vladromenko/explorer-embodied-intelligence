"""ENGINEERING_GLUE: Authenticated Pi -> existing Nano observer -> original native-owner stream.

No device access or motor setting writes; a source sample does not renew the
original native user-observation lease. This client does not run automatically.
"""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


class NativeStreamClient:
    def __init__(self, nano_observer_url, token, *, timeout=1., opener=urlopen):
        parsed = urlsplit(nano_observer_url)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or
                parsed.query or parsed.fragment or parsed.path not in ('','/') or
                not isinstance(token,str) or len(token)<16):
            raise ValueError('Explicit authenticated Nano observer origin required')
        self.origin, self.token, self.timeout, self.opener = nano_observer_url.rstrip('/'),token,timeout,opener

    def _request(self, operation, body=None, *, preserve_source_bytes=False):
        content = json.dumps(body,allow_nan=False).encode() if body is not None else None
        request = Request(self.origin+'/legacy/explorer/retarget/'+operation,data=content,
                          headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with self.opener(request,timeout=self.timeout) as response:
                raw=response.read(8*1024**2+1)
                if len(raw)>8*1024**2:
                    raise ValueError('Native receipt page exceeds its source bound')
                payload = json.loads(raw)
        except HTTPError as exc:
            raise RuntimeError('Native stream request rejected with HTTP '+str(exc.code)) from exc
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

    def begin(self, *, observing, neutral):
        return self._request('begin',{'observing':observing,'neutral':neutral})

    def recenter(self, epoch):
        return self._request('recenter',{'control_epoch':epoch})

    def resume(self, epoch, *, observing):
        return self._request('resume',{'control_epoch':epoch,'observing':observing})

    def input(self, epoch, sequence, velocity, evidence):
        return self._request('input',{'control_epoch':epoch,'source_sequence':sequence,
                                     'joint_velocity_deg_s':velocity,'source_evidence':evidence})

    def keyboard_base(self, epoch, actions, *, observing):
        return self._request('base',{'control_epoch':epoch,'actions':actions,'observing':observing})

    def stop(self, epoch):
        return self._request('stop',{'control_epoch':epoch})
