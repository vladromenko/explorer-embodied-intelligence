"""ENGINEERING_GLUE: thin04 human-demo API over COMMON permission and capture."""
import asyncio
import hashlib
import json
import re

import httpx
from fastapi import HTTPException,Request
from fastapi.responses import Response

from explorer_shared.observed_display_receipts import ObservedDisplayReceipts
from . import source_demo_workflow as flow
from .source_evaluation_api import bounded_body,checked_jpeg
from .source_evaluation_transport import pi_connection,bounded_request
from .capture_media_proxy import archive_response

ID=re.compile('[0-9a-f]{32}')


def install_router(app,get_store,get_config,get_actor,get_client):
    base='/api/lab/demonstrations';displays=ObservedDisplayReceipts()
    async def safe(function):
        try:return await asyncio.to_thread(function)
        except httpx.HTTPError as error:raise HTTPException(503,'The existing Pi source connection is unavailable; inspect status before retrying a mutation') from error
        except (ValueError,KeyError,TypeError,OSError) as error:raise HTTPException(409,str(error)) from error
    def capture_request(row,method,operation,body=None):
        with pi_connection(get_config(),row['binding'],timeout=60) as (station,_):
            raw,_=bounded_request(station.client,method,'/so101-capture/'+operation,body,limit=64*1024**2)
            return json.loads(raw)
    def original(row):
        ident=flow.capture_identity(get_store(),row)
        manifest=capture_request(row,'GET','recordings/'+ident+'/manifest')
        if (manifest.get('recording_id')!=ident or manifest.get('project_id')!='04' or
                (manifest.get('lifecycle') or {}).get('actor_id')!=row['actor_id'] or
                (((manifest.get('source_context') or {}).get('context') or {}).get('source04_demo_permission') or {}).get('binding')!=row['binding']):
            raise ValueError('Source capture manifest belongs to a different demonstration owner')
        return ident,manifest

    @app.get(base)
    async def listing(request:Request):
        if request.query_params:raise HTTPException(400,'Fixed demonstration list takes no query')
        return await safe(lambda:{'schema':'explorer_source04_demonstration_workbench_v1','attempts':flow.listing(get_store(),get_actor(request)),'physical_execution_authorized':False})

    @app.post(base)
    async def prepare(request:Request):
        body=await bounded_body(request)
        return await safe(lambda:flow.prepare(get_store(),body,get_actor(request)))

    @app.get(base+'/{identity}/status')
    async def status(identity:str,request:Request):
        def read():
            row=flow.attempt(get_store(),identity,get_actor(request))
            with pi_connection(get_config(),row['binding']) as (station,permission):
                native=station.status();observed=permission.status()
            grant=observed.get('grant') or {}
            owned=grant.get('binding')==row['binding'] and grant.get('actor_id')==row['actor_id']
            return {'attempt':row,'station':native,'permission':dict(observed,grant=grant if owned else None,
                execution_permission=bool(owned and observed.get('execution_permission'))),'capture':capture_request(row,'GET','status')}
        return await safe(read)

    @app.get(base+'/{identity}/challenge/{challenge_id}/frame')
    async def challenge_frame(identity:str,challenge_id:str,request:Request):
        def read():
            actor=get_actor(request);row=flow.attempt(get_store(),identity,actor)
            with pi_connection(get_config(),row['binding']) as (station,_):raw,headers=station.challenge_frame(challenge_id)
            response=checked_jpeg(raw,headers)
            displays.record('challenge',identity,challenge_id,actor,headers['x-jpeg-sha256'])
            return response
        return await safe(read)

    @app.get(base+'/{identity}/observation')
    async def observation(identity:str,request:Request):
        def read():
            row=flow.attempt(get_store(),identity,get_actor(request))
            with pi_connection(get_config(),row['binding']) as (station,_):packet=station.observation()
            ident=packet['observation']['observation_id']
            if not ID.fullmatch(ident):raise ValueError('Native observation identity changed')
            return dict(packet=packet,frame_url=base+'/'+identity+'/observation/'+ident+'/frame')
        return await safe(read)

    @app.get(base+'/{identity}/observation/{observation_id}/frame')
    async def observation_frame(identity:str,observation_id:str,request:Request):
        def read():
            actor=get_actor(request);row=flow.attempt(get_store(),identity,actor)
            with pi_connection(get_config(),row['binding']) as (station,_):raw,headers=station.frame(observation_id)
            response=checked_jpeg(raw,headers);displays.record('observe',identity,headers['x-frame-id'],actor,headers['x-jpeg-sha256'])
            return response
        return await safe(read)

    @app.post(base+'/{identity}/{operation}')
    async def operation(identity:str,operation:str,request:Request):
        body=await bounded_body(request)
        if request.query_params or not isinstance(body,dict):raise HTTPException(400,'Fixed demonstration operation form required')
        def perform():
            actor=get_actor(request);row=flow.attempt(get_store(),identity,actor)
            if operation in ('capture-start','pause','resume','cancel','finish','stage','review'):
                if operation=='capture-start':
                    if set(body)!={'grant_id','expected_control_epoch'}:raise ValueError('Exact acknowledged demo grant/epoch required')
                    reply=capture_request(row,'POST','record/start',{'record_start_body':row['record_start_body'],
                        'permission':{'binding':row['binding'],'grant_id':body['grant_id'],'control_epoch':body['expected_control_epoch']},'actor_id':actor})
                    if not ID.fullmatch(str(reply.get('recording_id'))):raise ValueError('Original recorder did not acknowledge its recording identity')
                    flow.record_return(get_store(),row,'capture_return',{'recording_id':reply['recording_id']})
                    return reply
                ident=flow.capture_identity(get_store(),row)
                if body.get('recording_id')!=ident:raise ValueError('Exact acknowledged recording identity required')
                if 'actor_id' in body:raise ValueError('Capture actor is derived from authentication')
                return capture_request(row,'POST','record/'+operation,dict(body,actor_id=actor))
            with pi_connection(get_config(),row['binding']) as (_,permission):
                if operation=='challenge':
                    if body:raise ValueError('Challenge takes no client-selected binding')
                    return permission.challenge()
                if operation=='authorize':
                    allowed={'challenge_id','displayed_jpeg_sha256','expected_control_epoch','observing','robot_workspace_checked','user_observed_test_confirmed'}
                    if set(body)!=allowed:raise ValueError('Exact displayed source frame and explicit USER confirmations required')
                    displays.consume('challenge',identity,body['challenge_id'],actor,body['displayed_jpeg_sha256'])
                    return permission.authorize(body,authenticated_actor=actor)
                if operation=='observe':
                    if set(body)!={'grant_id','frame_id','displayed_jpeg_sha256','expected_control_epoch','observing'}:raise ValueError('Exact delivered current observer frame required')
                    displays.consume('observe',identity,body['frame_id'],actor,body['displayed_jpeg_sha256'])
                    return permission.observe(body['grant_id'],{key:value for key,value in body.items() if key!='grant_id'},authenticated_actor=actor)
                if operation in ('start','recenter','stop','revoke'):
                    if set(body)!={'grant_id','expected_control_epoch'}:raise ValueError('Exact acknowledged native grant/returned epoch required')
                    if operation=='revoke':return permission.revoke('USER_DEMO_REVOKE',expected_grant_id=body['grant_id'],expected_control_epoch=body['expected_control_epoch'])
                    native={'start':'teleop/start','recenter':'teleop/recenter','stop':'stop'}[operation]
                    reply=permission.command(body['grant_id'],row['binding'],native,{'expected_control_epoch':body['expected_control_epoch']})
                    flow.record_return(get_store(),row,'control_return',reply)
                    return reply
                raise ValueError('Known explicit source demonstration operation required')
        return await safe(perform)

    @app.get(base+'/{identity}/capture/{operation:path}')
    async def media(identity:str,operation:str,request:Request):
        row=await safe(lambda:flow.attempt(get_store(),identity,get_actor(request)))
        ident=await safe(lambda:flow.capture_identity(get_store(),row))
        if operation in ('manifest','review'):
            await safe(lambda:original(row))
            return await safe(lambda:capture_request(row,'GET','recordings/'+ident+'/'+operation))
        if operation=='download':
            await safe(lambda:original(row))
            node=next(node for node in get_config()['nodes'] if node['id']=='pi')
            headers={'Authorization':'Bearer '+node['token']}
            for name in ('Range','If-Range'):
                if request.headers.get(name):headers[name]=request.headers[name]
            return await archive_response(get_client(),'http://'+node['host']+':'+str(node['port'])+'/so101-capture/recordings/'+ident+'/download',headers,timeout=60)
        if re.fullmatch('frame/[0-9a-f]{64}',operation):
            await safe(lambda:original(row));checksum=operation.split('/')[1]
            def frame():
                with pi_connection(get_config(),row['binding'],timeout=60) as (station,_):
                    raw,_=bounded_request(station.client,'GET','/so101-capture/recordings/'+ident+'/'+operation,limit=8*1024**2)
                if hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Original recording JPEG changed')
                return Response(raw,media_type='image/jpeg',headers={'X-Content-SHA256':checksum})
            return await safe(frame)
        raise HTTPException(404,'Fixed source capture media operation required')
