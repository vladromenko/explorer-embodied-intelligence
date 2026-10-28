"""ENGINEERING_GLUE: fixed authenticated Pi transport for explicit source tests.

Instantiation/import performs no I/O. The worker never authorizes or renews a
user observation. Every native command uses the COMMON atomic permission owner.
"""
from contextlib import contextmanager
import json
import re
from urllib.parse import urlencode

import httpx

from explorer_shared.station_observed_http import StationObservedHttp

ID=re.compile('[0-9a-f]{32}')


def bounded_request(client,method,path,body=None,*,limit=128*1024,source_name='Pi'):
    with client.stream(method,path,**({'json':body} if body is not None else {})) as response:
        if not response.is_success:
            raise ValueError(source_name+' source endpoint unavailable (HTTP '+str(response.status_code)+'); native settings were not changed')
        chunks=[];size=0
        for chunk in response.iter_bytes():
            size+=len(chunk)
            if size>limit:raise ValueError(source_name+' source response exceeds its declared bound')
            chunks.append(chunk)
        return b''.join(chunks),dict(response.headers)


@contextmanager
def pi_metadata_connection(config,*,client_factory=httpx.Client,timeout=2):
    node=next((item for item in config.get('nodes',[]) if item['id']=='pi'),None)
    if node is None:raise ValueError('The existing Pi Station is not configured')
    with client_factory(base_url='http://'+node['host']+':'+str(node['port']),
            headers={'Authorization':'Bearer '+node['token']},timeout=timeout,trust_env=False,follow_redirects=False) as client:
        yield PiStationHttp(client)


@contextmanager
def pi_connection(config,selected,*,client_factory=httpx.Client,timeout=2):
    with pi_metadata_connection(config,client_factory=client_factory,timeout=timeout) as station:
        def request(method,path,body):
            raw,_=bounded_request(station.client,method,path,body)
            result=json.loads(raw)
            if not isinstance(result,dict):raise ValueError('Typed Pi response required')
            return result
        yield station,StationObservedHttp(request,selected)


class PiStationHttp:
    execution_domain='PHYSICAL'
    def __init__(self,client):self.client=client
    def calibration_artifact(self,role,expected_sha256):
        if role not in ('leader','follower') or not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256):
            raise ValueError('Choose configured role and exact calibration SHA')
        path='/station/calibration-artifact?'+urlencode({'role':role,'expected_sha256':expected_sha256})
        return json.loads(bounded_request(self.client,'GET',path,limit=256*1024)[0])
    def status(self):return json.loads(bounded_request(self.client,'GET','/station/status')[0])
    def observation(self):return json.loads(bounded_request(self.client,'GET','/station/observation')[0])
    def frame(self,observation_id):
        if not isinstance(observation_id,str) or not ID.fullmatch(observation_id):raise ValueError('Exact server source observation ID required')
        return bounded_request(self.client,'GET','/station/observations/'+observation_id+'/frame',limit=2*1024**2)
    def challenge_frame(self,challenge_id):
        if not isinstance(challenge_id,str) or not ID.fullmatch(challenge_id):raise ValueError('Exact server display challenge required')
        return bounded_request(self.client,'GET','/station/observed-test/challenges/'+challenge_id+'/frame',limit=2*1024**2)


def run_source_evaluation(store,job,config):
    from .so101_source_evaluation import current_phase,run_so101_skill_eval
    with pi_connection(config,job['config']['binding']) as (station,permission):
        return run_so101_skill_eval(store,job,config,station=station,permission=permission,
            phase_provider=lambda:current_phase(store,job['id']))


def revoke_source_attempt(store,job,config):
    """USER cancel only: exact attached grant; never global Station STOP."""
    from .so101_source_evaluation import current_permission
    result=job.get('result') or {};owner=result.get('source_owner_session_id')
    if not owner:return dict(revoked=False,reason='No prepared source owner')
    attached=current_permission(store,job['id'],owner)
    if attached is None:return dict(revoked=False,reason='No attached native grant')
    grant_id=attached['grant_id'];binding=job['config']['binding']
    with pi_connection(config,binding) as (_,permission):
        status=permission.status();grant=status.get('grant') or {}
        epoch=result.get('control_epoch') or attached['expected_control_epoch']
        if grant.get('grant_id')==grant_id and grant.get('binding')==binding:epoch=grant['control_epoch']
        permission.revoke('USER_SOURCE_ATTEMPT_CANCEL',expected_grant_id=grant_id,expected_control_epoch=epoch)
        if result.get('phase')=='observed_native_source_policy':
            return permission.command(grant_id,binding,'stop',{'expected_control_epoch':epoch})
    return dict(revoked=True,scoped_stop_requested=False)
