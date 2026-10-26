"""ENGINEERING_GLUE: adapt the running Nano HTTP API without importing ROS.

Only existing operational commands are exposed. Controller configuration,
firmware, calibration settings and service lifecycle are never written here.
"""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import parse_qsl, urlencode

GET_PATHS = {'status', 'teleop', 'teleop/controls', 'arm/manual', 'arm/feedback', 'teaching/mobile', 'skills', 'arm/trajectory'}
GET_PATHS |= {'explorer/retarget/status', 'explorer/retarget/reference', 'explorer/retarget/dispatch'}
POST_PATHS = {'control', 'teleop/select', 'teleop/mode', 'teleop/speed', 'teleop/input',
    'teleop/stop', 'teleop/resume', 'teleop/disconnect', 'teaching/mobile/start',
    'teaching/mobile/lease', 'teaching/mobile/finish', 'teaching/mobile/stage', 'arm/jog', 'arm/finite',
    'arm/trajectory/plan', 'arm/trajectory/start', 'arm/trajectory/lease', 'arm/trajectory/stop'}
POST_PATHS |= {'explorer/retarget/begin', 'explorer/retarget/recenter', 'explorer/retarget/resume',
               'explorer/retarget/input', 'explorer/retarget/base', 'explorer/retarget/stop'}


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

