"""ENGINEERING_GLUE: Experiment06 metadata/data/learning routes; global app auth+CSRF apply."""
from fastapi import APIRouter,HTTPException,Body
from fastapi.responses import FileResponse,JSONResponse
from . import lab_skill_workflow as service


def install_router(app,store_provider):
    router=APIRouter(prefix='/api/lab/skills')
    def call(function,*args,**kwargs):
        try:return function(*args,**kwargs)
        except service.WorkflowBlocked as error:return JSONResponse({'detail':'Workflow incomplete','reasons':error.reasons},409)
        except (ValueError,KeyError,OSError,TypeError) as error:raise HTTPException(400,str(error)) from error
    @router.get('/catalogue')
    def catalogue():return service.catalogue()
    @router.get('/imports')
    def imports():return {'imports':service.imports(store_provider())}
    @router.get('/imports/{job_id}/review')
    def timeline(job_id:str,after:int=0,limit:int=100):return call(service.review_timeline,store_provider(),job_id,after=after,limit=limit)
    @router.get('/imports/{job_id}/frame/{index}')
    def frame(job_id:str,index:int):
        result=call(service.review_frame,store_provider(),job_id,index)
        if isinstance(result,JSONResponse):return result
        return FileResponse(result[0],media_type='image/jpeg',headers={'X-Content-SHA256':result[1],'Cache-Control':'no-store'})
    @router.get('')
    def listing():return {'skills':service.list_skills(store_provider())}
    @router.post('')
    def propose(body:dict=Body(...)):return call(service.propose,store_provider(),body)
    @router.get('/{skill_id}')
    def state(skill_id:str,version:int|None=None):return call(service.state_for_skill,store_provider(),skill_id,version)
    @router.get('/{skill_id}/readiness')
    def readiness(skill_id:str,version:int|None=None):return call(service.model_readiness,store_provider(),skill_id,version)
    def mutation(operation):
        def endpoint(skill_id:str,body:dict=Body(...)):
            version=body.get('version',body.get('skill_version'))
            if type(version) is not int or version<1:raise HTTPException(400,'Explicit skill version required')
            return call(operation,store_provider(),skill_id,version,body)
        return endpoint
    for path,operation in [('revision',service.revise),('confirm',service.confirm),('session',service.create_session),('capture-context',service.capture_context),('bind',service.bind_episode),('review',service.review_episode),('train',service.enqueue_training)]:
        router.add_api_route('/{skill_id}/'+path,mutation(operation),methods=['POST'],name='lab_skill_'+path)
    @router.post('/{skill_id}/aggregate')
    def aggregate(skill_id:str,body:dict=Body(...)):
        version=body.get('version',body.get('skill_version'))
        if type(version) is not int or version<1:raise HTTPException(400,'Explicit skill version required')
        return call(service.build_aggregate,store_provider(),skill_id,version)
    app.include_router(router)
    return router
