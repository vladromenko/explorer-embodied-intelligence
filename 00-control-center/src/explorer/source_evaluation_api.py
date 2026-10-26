"""ENGINEERING_GLUE: authenticated explicit source04 evaluation workflow.

Paths, models, native bindings and worker sessions are server-owned. Requests
prepare cold computation, display original frames, then separately let the USER
authorize one current attempt. No constructor/background poll grants authority.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import re
from collections import OrderedDict
import threading
import time
import httpx

from fastapi import HTTPException,Request
from fastapi.responses import Response

from . import so101_source_evaluation as evaluation
from .source_evaluation_transport import pi_connection

ID=re.compile('[0-9a-f]{32}')


def typed_job(store,identity,*,active=False):
    if not isinstance(identity,str) or not ID.fullmatch(identity):raise ValueError('Choose a source evaluation from the server list')
    job=store.job(identity)
    if not job or job['kind']!='so101_skill_eval' or job['config'].get('schema')!=evaluation.REQUEST_SCHEMA:
        raise ValueError('Current typed source evaluation required')
    if active and job['status']!='running':raise ValueError('The current source evaluation is no longer running')
    return job


def outcome_inbox(store,job):
    result=job.get('result') or {};session=result.get('source_owner_session_id')
    if not isinstance(session,str) or not ID.fullmatch(session):raise ValueError('The source worker has not prepared this session')
    folder=Path(store.root).resolve()/'jobs'/job['id']/'outcomes'
    if folder.resolve()!=folder:raise ValueError('Private source outcome path changed')
    return evaluation.SourceOutcomeInbox(folder,clock_domain='source04:'+Path('/proc/sys/kernel/random/boot_id').read_text().strip(),session_id=session)


def attempt_view(store,job):
    result=job.get('result') or {};public={key:result[key] for key in ('phase','model_ready','source_owner_session_id','current_source_phase','control_epoch','frames','steps',
        'independent_full_task_success','physical_success_claimed','native_stop_confirmed','error','per_phase','end_to_end') if key in result}
    row={key:job[key] for key in ('id','status','created','progress')};row['result']=public
    row['model_job_id']=job['config']['source_model_job_id'];row['context']=job['config']['context']
    row['phase']=evaluation.current_phase(store,job['id']);row['phases']=job['config']['source_phases']
    row['pending_outcomes']=outcome_inbox(store,job).pending(job['id']) if result.get('phase')=='source_outcome_review_after_native_stop' and job['status']=='running' else []
    timeline=[];total=0
    folder=Path(store.root).resolve()/'jobs'/job['id'];steps=folder/'steps'
    if steps.resolve()!=steps:raise ValueError('Source timeline path changed')
    for path in sorted(steps.glob('*.json')):
        if len(timeline)>=601 or not re.fullmatch('[0-9]{6}\\.json',path.name) or path.is_symlink():raise ValueError('Source timeline inventory bound exceeded')
        size=path.stat().st_size;total+=size
        if size>128*1024 or total>8*1024**2:raise ValueError('Source timeline metadata bound exceeded')
        step=json.loads(path.read_bytes());observation=step['packet']['observation']
        timeline.append(dict(index=step['index'],phase_id=step['phase_id'],command_id=step['native_dispatch_receipt_id'],
            frame_url='/api/lab/source-evaluations/'+job['id']+'/frames/'+observation['observation_id'],frame_sha256=observation['frame_sha256']))
    row['timeline']=timeline
    return row


def checked_jpeg(raw,headers):
    checksum=headers.get('x-jpeg-sha256')
    if (not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9') or hashlib.sha256(raw).hexdigest()!=checksum or
            not all(headers.get(key) for key in ('x-frame-id','x-boot-id'))):raise ValueError('Exact original source JPEG identity differs')
    return Response(raw,media_type='image/jpeg',headers={key:headers[key] for key in ('x-jpeg-sha256','x-frame-id','x-boot-id')})


def install_router(app,get_store,get_config,get_actor):
    base='/api/lab/source-evaluations'
    displays=OrderedDict();display_lock=threading.RLock()
    def displayed(kind,job_id,identity,actor,checksum):
        with display_lock:
            displays[(kind,job_id,identity,actor)]=(checksum,time.monotonic())
            while len(displays)>256:displays.popitem(last=False)
    def consume_display(kind,job_id,identity,actor,checksum):
        with display_lock:receipt=displays.pop((kind,job_id,identity,actor),None)
        if receipt is None or receipt[0]!=checksum or not 0<=time.monotonic()-receipt[1]<2:
            raise ValueError('This exact source JPEG must first be delivered to this authenticated observer for this attempt; display receipt expired or consumed')

    async def safe(call):
        try:return await asyncio.to_thread(call)
        except httpx.HTTPError as error:raise HTTPException(503,'The existing Pi source connection is unavailable; retry an explicit read') from error
        except (ValueError,KeyError,TypeError,OSError) as error:raise HTTPException(409,str(error)) from error

    @app.get(base)
    async def listing(request:Request):
        if request.query_params:raise HTTPException(400,'Source evaluation list takes no query parameters')
        def inspect():
            from .transfer_source import current_source_choices
            store=get_store();models=[]
            catalog=current_source_choices(store)
            for source in catalog['choices']:
                models.append(dict(id=source['job_id'],title=source.get('task',source['job_id'][:8]),
                    backend=source.get('backend'),eligible=source.get('technical_source_ready') is True and source.get('source_domain')=='PHYSICAL' and source.get('task_type') not in (None,'hold','trajectory','joint_trajectory'),
                    reason='Нужна модель из настоящего source04 cohort с совпадающей камерой и калибровкой; SIM остаётся офлайн.'))
            attempts=[job for job in store.jobs(10000) if job['kind']=='so101_skill_eval'][:32]
            return dict(models=models,attempts=[attempt_view(store,job) for job in attempts],
                physical_execution_authorized=False,schema='explorer_source_evaluation_workbench_v1')
        return await safe(inspect)

    @app.post(base)
    async def prepare(request:Request):
        body=await request.json()
        if request.query_params or not isinstance(body,dict) or 'model_job_id' not in body:raise HTTPException(400,'Choose one current source model and object context')
        def create():
            store=get_store();settings=evaluation.request_settings(store,body['model_job_id'],{key:value for key,value in body.items() if key!='model_job_id'})
            if settings['source_domain']!='PHYSICAL':raise ValueError('The live Pi evaluator requires an actual physical-source cohort; simulation has its separate offline evaluator')
            job=store.create_job('so101_skill_eval','04',settings)
            return {key:job[key] for key in ('id','kind','status','project_id')}
        return await safe(create)

    @app.get(base+'/{job_id}/permission')
    async def permission_status(job_id:str,request:Request):
        if request.query_params:raise HTTPException(400,'Fixed source permission endpoint')
        def read():
            job=typed_job(get_store(),job_id,active=True)
            with pi_connection(get_config(),job['config']['binding']) as (_,permission):return permission.status()
        return await safe(read)

    @app.get(base+'/{job_id}/challenge/{challenge_id}/frame')
    async def display_challenge(job_id:str,challenge_id:str,request:Request):
        if request.query_params:raise HTTPException(400,'Pinned display frame takes no query')
        def read():
            job=typed_job(get_store(),job_id,active=True)
            with pi_connection(get_config(),job['config']['binding']) as (station,_):
                raw,headers=station.challenge_frame(challenge_id);response=checked_jpeg(raw,headers)
            displayed('challenge',job_id,challenge_id,get_actor(request),headers['x-jpeg-sha256'])
            return response
        return await safe(read)

    @app.get(base+'/{job_id}/observation')
    async def observation(job_id:str,request:Request):
        if request.query_params:raise HTTPException(400,'Fixed source observation endpoint')
        def read():
            job=typed_job(get_store(),job_id,active=True)
            with pi_connection(get_config(),job['config']['binding']) as (station,_):packet=station.observation()
            ident=packet['observation']['observation_id']
            if not ID.fullmatch(ident):raise ValueError('Native source observation identity differs')
            return dict(packet=packet,frame_url=base+'/'+job_id+'/observation/'+ident+'/frame')
        return await safe(read)

    @app.get(base+'/{job_id}/observation/{observation_id}/frame')
    async def observation_frame(job_id:str,observation_id:str,request:Request):
        if request.query_params:raise HTTPException(400,'Pinned observation frame takes no query')
        def read():
            job=typed_job(get_store(),job_id,active=True)
            with pi_connection(get_config(),job['config']['binding']) as (station,_):
                raw,headers=station.frame(observation_id);response=checked_jpeg(raw,headers)
            displayed('observe',job_id,headers['x-frame-id'],get_actor(request),headers['x-jpeg-sha256'])
            return response
        return await safe(read)

    @app.get(base+'/{job_id}/frames/{observation_id}')
    async def retained_frame(job_id:str,observation_id:str,request:Request):
        if request.query_params or not ID.fullmatch(observation_id):raise HTTPException(400,'Choose an original frame from this evaluation timeline')
        def read():
            store=get_store();job=typed_job(store,job_id);folder=Path(store.root).resolve()/'jobs'/job['id']/'observations'
            path=folder/(observation_id+'.jpg');metadata=folder/(observation_id+'.json')
            if path.resolve()!=path or metadata.resolve()!=metadata or path.stat().st_size>2*1024**2 or metadata.stat().st_size>128*1024:
                raise ValueError('Retained source frame path or size differs')
            source=json.loads(metadata.read_bytes())['observation']
            return checked_jpeg(path.read_bytes(),{'x-jpeg-sha256':source['frame_sha256'],'x-frame-id':source['frame_id'],'x-boot-id':source['frame_boot_id']})
        return await safe(read)

    @app.post(base+'/{job_id}/{operation}')
    async def operation(job_id:str,operation:str,request:Request):
        body=await request.json()
        if request.query_params or not isinstance(body,dict) or operation not in ('challenge','authorize','observe','phase'):
            raise HTTPException(400,'Known explicit source operation required')
        def perform():
            store=get_store();job=typed_job(store,job_id,active=True);actor=get_actor(request)
            if operation=='phase':return evaluation.record_phase(store,job_id,body,authenticated_actor=actor)
            with pi_connection(get_config(),job['config']['binding']) as (_,permission):
                if operation=='challenge':
                    if body or (job.get('result') or {}).get('phase')!='awaiting_source_observed_grant':raise ValueError('Wait until the current model is loaded before requesting the observed challenge')
                    return permission.challenge()
                if operation=='authorize':
                    allowed={'challenge_id','displayed_jpeg_sha256','expected_control_epoch','observing','robot_workspace_checked','user_observed_test_confirmed'}
                    if set(body)!=allowed:raise ValueError('Exact displayed frame and explicit USER test confirmations required')
                    current=job.get('result') or {}
                    if current.get('phase')!='awaiting_source_observed_grant' or not current.get('source_owner_session_id'):raise ValueError('Current prepared source worker required')
                    consume_display('challenge',job_id,body['challenge_id'],actor,body['displayed_jpeg_sha256'])
                    grant=permission.authorize(body,authenticated_actor=actor)
                    try:
                        evaluation.attach_permission(store,job_id,grant['grant_id'],grant['control_epoch'],current['source_owner_session_id'],permission=permission)
                    except Exception:
                        permission.revoke('SOURCE_ATTACH_FAILED',expected_grant_id=grant['grant_id'],expected_control_epoch=grant['control_epoch'])
                        raise
                    return grant
                if set(body)!={'grant_id','frame_id','displayed_jpeg_sha256','expected_control_epoch','observing'}:raise ValueError('Exact displayed current observer frame required')
                consume_display('observe',job_id,body['frame_id'],actor,body['displayed_jpeg_sha256'])
                return permission.observe(body['grant_id'],{key:value for key,value in body.items() if key!='grant_id'},authenticated_actor=actor)
        return await safe(perform)

    @app.post(base+'/{job_id}/outcomes/{challenge_id}')
    async def outcome(job_id:str,challenge_id:str,request:Request):
        body=await request.json()
        if request.query_params or not isinstance(body,dict) or set(body)-{'status','observing','note'} or body.get('status') not in ('success','failure','unknown'):
            raise HTTPException(400,'Record one explicit observed outcome')
        def record():
            store=get_store();job=typed_job(store,job_id,active=True)
            if (job.get('result') or {}).get('phase')!='source_outcome_review_after_native_stop':raise ValueError('Outcomes are recorded after the native attempt has stopped')
            inbox=outcome_inbox(store,job)
            if challenge_id not in {item['challenge_id'] for item in inbox.pending(job_id)}:raise ValueError('Current attempt outcome challenge required')
            if body['status']=='success':return inbox.confirm(challenge_id,dict(affirmed=True,observing=body.get('observing'),note=body.get('note','')),authenticated_actor=get_actor(request))
            return inbox.record_status(challenge_id,body['status'],authenticated_actor=get_actor(request),observing=body.get('observing',False),note=body.get('note',''))
        return await safe(record)
