"""ENGINEERING_GLUE: adapt the running Nano HTTP API without importing ROS.

Only existing operational commands are exposed. Controller configuration,
firmware, calibration settings and service lifecycle are never written here.
"""
import json
import hashlib
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import parse_qsl, urlencode

GET_PATHS = {'status', 'teleop', 'teleop/controls', 'arm/manual', 'arm/feedback', 'teaching/mobile', 'skills', 'arm/trajectory'}
GET_PATHS |= {'explorer/retarget/status', 'explorer/retarget/reference', 'explorer/retarget/dispatch'}
GET_PATHS |= {'explorer/retarget/activation/status','explorer/retarget/activation/challenge'}
GET_PATHS |= {'explorer/cooperative/status','explorer/cooperative/observation'}
GET_PATHS |= {'explorer/cooperative/phase/held-status'}
POST_PATHS = {'control', 'teleop/select', 'teleop/mode', 'teleop/speed', 'teleop/input',
    'teleop/stop', 'teleop/resume', 'teleop/disconnect', 'teaching/mobile/start',
    'teaching/mobile/lease', 'teaching/mobile/finish', 'teaching/mobile/stage', 'arm/jog', 'arm/finite',
    'arm/trajectory/plan', 'arm/trajectory/start', 'arm/trajectory/lease', 'arm/trajectory/stop'}
POST_PATHS |= {'explorer/retarget/begin', 'explorer/retarget/recenter', 'explorer/retarget/resume',
               'explorer/retarget/input', 'explorer/retarget/base', 'explorer/retarget/stop'}
POST_PATHS |= {'explorer/retarget/activation/authorize','explorer/retarget/activation/revoke','arm/prepare'}
POST_PATHS |= {'explorer/cooperative/constraint/register'}
POST_PATHS |= {'explorer/cooperative/prepare','explorer/cooperative/manual/resume'}
POST_PATHS |= {'explorer/cooperative/phase/'+operation for operation in ('park','adopt','begin','resume')}
POST_PATHS |= {'explorer/cooperative/phase/release/challenge','explorer/cooperative/phase/resume-release'}
POST_PATHS |= {'explorer/cooperative/'+operation for operation in ('bind','begin','recenter','resume','lease','input','stop',
    'guard/arm','guard/challenge','guard/authorize','guard/revoke','guard/reset')}


def parse_dispatch_query(raw_query):
    if not isinstance(raw_query,str) or len(raw_query)>100:
        raise ValueError('Invalid native dispatch query')
    pairs=parse_qsl(raw_query,keep_blank_values=True,strict_parsing=True,max_num_fields=2)
    if not pairs:
        return None
    if len(pairs)!=1 or pairs[0][0]!='after_sequence':
        raise ValueError('Only one after_sequence is allowed')
    text=pairs[0][1]
    if not text or len(text)>19 or not text.isascii() or not text.isdigit() or int(text)>2**63-1:
        raise ValueError('Nonnegative bounded native dispatch cursor required')
    return {'after_sequence':int(text)}


class LegacyBridge:
    def __init__(self, root=Path('/home/vlad/Explorer')):
        self.root = Path(root)

    def request(self, path, body=None, *, query=None):
        allowed = GET_PATHS if body is None else POST_PATHS
        if path not in allowed:
            raise ValueError('Operation is outside the existing manual interface')
        if body is not None and not isinstance(body, dict):
            raise ValueError('Expected an object')
        if path == 'control' and body.get('op') not in ('stop', 'clear_stop'):
            raise ValueError('Use existing teleop input for movement')
        if path=='arm/prepare':
            if (body.get('observing') is not True or body.get('space_clear') is not True or
                    body.get('acknowledge_factory_home_motion') is not True or not body.get('authorization_id')):
                raise ValueError('Explicit observed HOME90x6/4000 confirmation and activation ID required')
            code,activation=self.request('explorer/retarget/activation/status')
            if (code!=200 or activation.get('execution_permission') is not True or
                    activation.get('authorization_id')!=body['authorization_id'] or
                    activation.get('native_active') is not False or activation.get('native_stop_latched') is not True):
                raise ValueError('Fresh observed activation and original native STOP required before HOME')
            # Preserve original native ArmHome ABI exactly; metadata stays here.
            body={'observing':True,'space_clear':True}
        suffix=''
        if query is not None:
            if (body is not None or path!='explorer/retarget/dispatch' or not isinstance(query,dict) or
                    set(query)!={'after_sequence'} or type(query['after_sequence']) is not int or
                    not 0<=query['after_sequence']<=2**63-1):
                raise ValueError('Query is outside the fixed native dispatch interface')
            suffix='?'+urlencode(query)
        token = (self.root / 'config/access_token').read_text().strip()
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        request = Request('http://127.0.0.1:8080/api/' + path + suffix, data=data,
            headers={'Authorization':'Bearer ' + token, 'Content-Type':'application/json',
                     'Origin':'http://127.0.0.1:8080'})
        try:
            with urlopen(request, timeout=8) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            try:
                payload = json.loads(error.read())
            except ValueError:
                payload = {'detail':'Existing controller rejected this operation'}
            return error.code, payload

    def activation_frame(self,challenge_id):
        if not isinstance(challenge_id,str) or not re.fullmatch('[0-9a-f]{32}',challenge_id):
            raise ValueError('Exact native activation challenge ID required')
        return self._pinned_source_frame('explorer/retarget/activation/frame/'+challenge_id)

    def cooperative_frame(self,observation_id):
        if not isinstance(observation_id,str) or not re.fullmatch('[0-9a-f]{32}',observation_id):
            raise ValueError('Exact retained native policy observation ID required')
        return self._pinned_source_frame('explorer/cooperative/observations/'+observation_id+'/frame')

    def _pinned_source_frame(self,path):
        token=(self.root/'config/access_token').read_text().strip()
        request=Request('http://127.0.0.1:8080/api/'+path,
                        headers={'Authorization':'Bearer '+token})
        with urlopen(request,timeout=2) as response:
            jpeg=response.read(2*1024**2+1)
            digest=response.headers.get('X-JPEG-SHA256','')
            if (len(jpeg)>2*1024**2 or not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9') or
                    hashlib.sha256(jpeg).hexdigest()!=digest):
                raise ValueError('Pinned observed native JPEG/hash mismatch')
            headers={name:response.headers[name] for name in ('X-JPEG-SHA256','X-Frame-ID','X-Monotonic-NS','X-Boot-ID')}
            return jpeg,headers

