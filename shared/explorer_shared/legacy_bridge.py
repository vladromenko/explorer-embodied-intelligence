"""ENGINEERING_GLUE: adapt the running Nano HTTP API without importing ROS.

Only existing operational commands are exposed. Controller configuration,
firmware, calibration settings and service lifecycle are never written here.
"""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

GET_PATHS = {'status', 'teleop', 'teleop/controls', 'arm/manual', 'arm/feedback', 'teaching/mobile', 'skills'}
POST_PATHS = {'control', 'teleop/select', 'teleop/mode', 'teleop/speed', 'teleop/input',
    'teleop/stop', 'teleop/resume', 'teleop/disconnect', 'teaching/mobile/start',
    'teaching/mobile/lease', 'teaching/mobile/finish', 'teaching/mobile/stage', 'arm/jog', 'arm/finite'}


class LegacyBridge:
    def __init__(self, root=Path('/home/vlad/Explorer')):
        self.root = Path(root)

    def request(self, path, body=None):
        allowed = GET_PATHS if body is None else POST_PATHS
        if path not in allowed:
            raise ValueError('Operation is outside the existing manual interface')
        if body is not None and not isinstance(body, dict):
            raise ValueError('Expected an object')
        if path == 'control' and body.get('op') not in ('stop', 'clear_stop'):
            raise ValueError('Use existing teleop input for movement')
        token = (self.root / 'config/access_token').read_text().strip()
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        request = Request('http://127.0.0.1:8080/api/' + path, data=data,
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

