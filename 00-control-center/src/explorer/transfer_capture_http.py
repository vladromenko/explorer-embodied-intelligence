"""Fixed Nano DATA endpoints only; no native controller/grant/STOP transport."""
from contextlib import contextmanager
import json
import re

import httpx

from .source_evaluation_transport import bounded_request


def _recording(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('Known original recording ID required')
    return value


class NanoCaptureData:
    def __init__(self,client):self.client=client
    def _request(self,method,path,body=None,*,limit=4*1024**2):
        raw,_=bounded_request(self.client,method,path,body,limit=limit,source_name='Nano DATA')
        result=json.loads(raw)
        if not isinstance(result,dict):raise ValueError('Typed original Nano DATA response required')
        return result
    def capabilities(self):return self._request('GET','/native-capture/capabilities')
    def status(self):return self._request('GET','/native-capture/status')
    def manifest(self,identity):return self._request('GET','/native-capture/recordings/'+_recording(identity)+'/manifest',limit=32*1024**2)
    def review_state(self,identity):return self._request('GET','/native-capture/recordings/'+_recording(identity)+'/review',limit=32*1024)
    def start_recording(self,context):return self._request('POST','/native-capture/record/start',dict(skill_context=context))
    def record_operation(self,operation,identity,body):
        if operation not in ('stage','pause','resume','finish','cancel','review'):raise ValueError('Fixed DATA recording operation required')
        if not isinstance(body,dict) or 'recording_id' in body:raise ValueError('Server-owned original recording identity required')
        return self._request('POST','/native-capture/record/'+operation,dict(body,recording_id=_recording(identity)))


@contextmanager
def nano_capture_connection(config,*,client_factory=httpx.Client,timeout=3):
    node=next((row for row in config.get('nodes',[]) if row['id']=='nano'),None)
    if node is None:raise ValueError('Existing Nano DATA observer is not configured')
    with client_factory(base_url='http://'+node['host']+':'+str(node['port']),
            headers={'Authorization':'Bearer '+node['token']},timeout=timeout,trust_env=False,follow_redirects=False) as client:
        yield NanoCaptureData(client)
