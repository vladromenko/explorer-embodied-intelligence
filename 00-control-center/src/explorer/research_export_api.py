"""ENGINEERING_GLUE: authenticated queued selected scientific evidence exports.

Browser input contains terminal job IDs only. Worker derives artifact paths,
resource estimates and copy/restore receipts; fixed download is verified again.
Authentication and CSRF are supplied by the central application's middleware.
"""
import asyncio
import re

from fastapi import HTTPException,Request
from fastapi.responses import FileResponse


def install_router(app,get_store):
    @app.get('/api/research/exports')
    async def listing(request:Request):
        if request.query_params:raise HTTPException(400,'Export selection takes no filesystem or query input')
        jobs=await asyncio.to_thread(lambda:get_store().jobs(10000))
        choices=[{key:job[key] for key in ('id','kind','project_id','status','created')}
                 for job in jobs if job['status'] in ('completed','failed','cancelled')
                 and job['kind'] not in ('research_export','cooperative_policy_warm')]
        exports=[]
        for job in jobs:
            if job['kind']=='research_export':
                result=job.get('result') or {}
                row={key:job[key] for key in ('id','status','created')}
                row['result']={key:result[key] for key in ('verification','archive_sha256','files','total_payload_bytes',
                    'independent_restore_verified','manifest_sha256','missing_dependencies_count') if key in result}
                if job.get('error'):row['error']='Экспорт не завершён; исходные задания сохранены. Подробности доступны в общей очереди.'
                if job['status']=='completed':row['download_url']='/api/research/exports/'+job['id']+'/download'
                exports.append(row)
        return dict(schema='explorer_research_export_selection_v1',choices=choices,exports=exports,
                    unknown_dependencies_are_partial=True,physical_control_authorized=False)

    @app.post('/api/research/exports')
    async def create(request:Request):
        from .research_export_job import request_settings
        try:
            body=await request.json()
            if request.query_params or not isinstance(body,dict) or set(body)!={'job_ids'}:
                raise ValueError('Only selected terminal job IDs are accepted')
            store=get_store();settings=await asyncio.to_thread(request_settings,store,body['job_ids'])
            # The project labels the copy job only; its selected evidence retains
            # every source's distinct original/canonical experiment identity.
            selected=store.job(settings['job_ids'][0])
            job=store.create_job('research_export',selected['project_id'],settings)
            return {key:job[key] for key in ('id','kind','project_id','status')}
        except (ValueError,KeyError,TypeError,OSError) as error:raise HTTPException(400,str(error)) from error

    @app.get('/api/research/exports/{job_id}/download')
    async def download(job_id:str,request:Request):
        from .research_export_job import download_validation
        if not re.fullmatch('[0-9a-f]{32}',job_id) or request.query_params:
            raise HTTPException(400,'Choose one completed managed evidence archive')
        try:
            artifact=await asyncio.to_thread(download_validation,get_store(),job_id)
            return FileResponse(artifact['path'],media_type='application/x-tar',filename='explorer-evidence-'+job_id+'.tar',
                headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Content-SHA256':artifact['sha256']})
        except FileNotFoundError as error:raise HTTPException(404,'Completed evidence archive is unavailable') from error
        except (ValueError,KeyError,TypeError,OSError) as error:raise HTTPException(409,str(error)) from error
