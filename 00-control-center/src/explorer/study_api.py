"""ENGINEERING_GLUE: authenticated registered Study queue/download router.

Authentication, Origin and CSRF remain the central application's middleware.
Only a registered Study ID is accepted; no filesystem or scientific claims are
client input. Listing reads bounded typed metadata, never source payloads.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from fastapi import HTTPException,Request
from fastapi.responses import FileResponse

from .study_registry import registered_studies

MAX_EXPORTS=50
MAX_ROW_METADATA_BYTES=128*1024
MAX_TOTAL_METADATA_BYTES=2*1024**2
MAX_REQUEST_BYTES=4096
ID=re.compile(r'[0-9a-f]{32}\Z')
SHA=re.compile(r'[0-9a-f]{64}\Z')


def _export_rows(store):
    """Read no unrelated job bodies, and preflight typed body sizes in SQL."""
    exports=[];total=0;known={row['study_id'] for row in registered_studies()}
    with closing(sqlite3.connect(Path(store.path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        selected=db.execute('''SELECT id,kind,project_id,status,created,updated,progress,
            error IS NOT NULL AS has_error,
            COALESCE(LENGTH(CAST(config AS BLOB)),0)+COALESCE(LENGTH(CAST(result AS BLOB)),0) AS metadata_bytes
            FROM jobs WHERE kind='study_export' ORDER BY created DESC LIMIT ?''',(MAX_EXPORTS,)).fetchall()
        for source in selected:
            row={key:source[key] for key in ('id','kind','project_id','status','created','updated','progress')}
            row['result']={}
            if source['has_error']:row['error']='Study export не завершён; исходные записи и частичные артефакты сохранены. Подробности доступны в общей очереди.'
            if source['metadata_bytes']>MAX_ROW_METADATA_BYTES or total+source['metadata_bytes']>MAX_TOTAL_METADATA_BYTES:
                row['details_withheld_reason']='typed_metadata_byte_budget_exceeded'
            else:
                total+=source['metadata_bytes']
                body=db.execute('SELECT config,result FROM jobs WHERE id=?',(source['id'],)).fetchone()
                try:
                    config=json.loads(body['config']) if body['config'] else {}
                    result=json.loads(body['result']) if body['result'] else {}
                    if not isinstance(config,dict) or not isinstance(result,dict):raise ValueError('typed metadata object required')
                    row['study_id']=config.get('study_id') if config.get('study_id') in known else None
                    if result and (result.get('schema')!='explorer_study_export_job_result_v1' or result.get('study_id')!=row['study_id'] or
                            result.get('verification')!='PARTIAL' or result.get('completeness')!='PARTIAL' or
                            any(result.get(key) is not False for key in ('target_optimizer_allowed','physical_io','physical_success_claimed','learned_policy_executed','master_prompt_completed'))):
                        raise ValueError('Unverified recorded Study scope')
                    keys=('schema','verification','completeness','archive_sha256','archive_bytes','manifest_sha256','files',
                        'actual_payload_bytes','independent_restore_verified','dependency_closure','recorded_attempt_ids','outcomes')
                    row['result']={key:result[key] for key in keys if key in result}
                    gaps=result.get('missing_dependencies')
                    row['result']['required_dependency_gap_count']=sum(isinstance(gap,dict) and gap.get('required_for_original_reexecution') is True for gap in gaps) if isinstance(gaps,list) else 0
                    valid=(row['status']=='completed' and ID.fullmatch(row['id']) and row['study_id'] is not None and
                        result.get('schema')=='explorer_study_export_job_result_v1' and result.get('study_id')==row['study_id'] and
                        result.get('verification')=='PARTIAL' and result.get('independent_restore_verified') is True and
                        isinstance(result.get('archive_sha256'),str) and SHA.fullmatch(result['archive_sha256']) and
                        type(result.get('archive_bytes')) is int and result['archive_bytes']>0)
                    if valid:row['download_url']='/api/research/studies/'+row['id']+'/download'
                except (ValueError,TypeError,RecursionError):row['details_withheld_reason']='typed_metadata_invalid'
            exports.append(row)
    return exports


async def _body(request):
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_REQUEST_BYTES:raise HTTPException(400,'Only one registered Study ID is accepted')
        data.extend(chunk)
    try:return json.loads(data)
    except (ValueError,UnicodeDecodeError,RecursionError) as error:raise HTTPException(400,'A JSON Study selection is required') from error


def install_router(app,get_store):
    @app.get('/api/research/studies')
    async def listing(request:Request):
        if request.query_params:raise HTTPException(400,'Study listing accepts no filesystem or query input')
        exports=await asyncio.to_thread(_export_rows,get_store())
        return dict(schema='explorer_registered_studies_v1',studies=registered_studies(),exports=exports,
            target_partition='development',target_optimizer_allowed=False,source_method='SCRIPTED_SIM',
            unknown_original_dependencies_are_partial=True,physical_control_authorized=False,master_prompt_completed=False)

    @app.post('/api/research/studies')
    async def create(request:Request):
        from .study_job import request_settings
        if request.query_params:raise HTTPException(400,'Only one registered Study ID is accepted')
        body=await _body(request)
        if not isinstance(body,dict) or set(body)!={'study_id'}:
            raise HTTPException(400,'Only one registered Study ID is accepted')
        try:
            store=get_store();settings=await asyncio.to_thread(request_settings,store,body['study_id'])
            job=store.create_job('study_export','06',settings)
            return dict({key:job[key] for key in ('id','kind','project_id','status')},study_id=settings['study_id'])
        except (ValueError,KeyError,TypeError) as error:raise HTTPException(400,str(error)) from error
        except OSError as error:raise HTTPException(409,'Registered Study originals are unavailable; no original is changed') from error

    @app.get('/api/research/studies/{job_id}/download')
    async def download(job_id:str,request:Request):
        from .study_job import download_validation
        if not ID.fullmatch(job_id) or request.query_params:raise HTTPException(400,'Choose one completed managed Study export')
        try:
            artifact=await asyncio.to_thread(download_validation,get_store(),job_id)
            return FileResponse(artifact['path'],media_type=artifact['media_type'],filename=artifact['filename'],
                headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Content-SHA256':artifact['sha256']})
        except FileNotFoundError as error:raise HTTPException(404,'Completed Study archive is unavailable') from error
        except (ValueError,KeyError,TypeError,OSError) as error:raise HTTPException(409,'Completed Study archive failed its managed path/SHA/size validation') from error
