"""Data-only06 API: admitted model → original geometry → durable wizard.

No controller/motion API, arbitrary path, client measurement or new device owner.
"""
import asyncio

import httpx
from fastapi import HTTPException,Request
from fastapi.responses import JSONResponse

from . import transfer_geometry as geometry
from . import transfer_calibration as wizard
from .source_evaluation_api import bounded_body
from .source_evaluation_transport import pi_metadata_connection


def install_router(app,get_store,get_config,get_actor):
    async def safe(function):
        try:return await asyncio.to_thread(function)
        except geometry.transfer_source.SourceBlocked as error:
            return JSONResponse({'detail':'; '.join(row['detail'] for row in error.blockers),'blockers':error.blockers},409)
        except httpx.HTTPError as error:raise HTTPException(503,'Existing source metadata connection unavailable') from error
        except (ValueError,KeyError,TypeError,OSError) as error:raise HTTPException(409,str(error)) from error

    def current(model_job_id):
        model=geometry.admitted_model(get_store(),model_job_id)
        if model['source_domain']=='SIMULATION':envelope=geometry.saved_simulation_calibration(model)
        else:
            with pi_metadata_connection(get_config()) as station:
                envelope=station.calibration_artifact('follower',model['source_calibration_sha256'])
        choice=geometry.transfer_source.inspect_source_model(get_store(),model_job_id)
        qualification=dict(independent_object_skill_qualified=choice['eligible_for_transfer'],
            source_policy_closed_loop_evaluated=choice['source_object_rollout_qualified'],
            source_domain=model['source_domain'].lower(),
            evaluation_job_ids=[row['evaluation_job_id'] for row in choice['qualified_trace_choices']])
        result,_=geometry.build_geometry(model,envelope,source_qualification=qualification)
        # Re-read current source admission after transport; never cache command admission.
        again=geometry.admitted_model(get_store(),model_job_id)
        if any(again[k]!=model[k] for k in ('training_receipt_sha256','checkpoint_content_sha256','source_calibration_sha256')):
            raise ValueError('Source model changed while reading original calibration')
        return result

    @app.get('/api/transfer/geometry')
    async def preview(request:Request):
        query=request.query_params
        if set(query)!={'model_job_id'} or len(query.getlist('model_job_id'))!=1:
            raise HTTPException(400,'Choose one source model ID')
        return await safe(lambda:current(query['model_job_id']))

    @app.post('/api/transfer/calibration')
    async def create(request:Request):
        body=await bounded_body(request)
        if set(body)!={'model_job_id','target_version','request_id'}:
            raise HTTPException(400,'Choose source model, advancing target version and request ID only')
        def prepare():
            result=current(body['model_job_id']);binding=result['source_model_binding']
            return wizard.create_session(get_store(),dict(skill_id=binding['skill_id'],source_version=binding['skill_version'],
                target_version=body['target_version'],request_id=body['request_id'],domain=binding['source_domain'].lower()),
                audit=result['audit'],source_model_binding=binding,authenticated_actor=get_actor(request))
        return await safe(prepare)

    @app.get('/api/transfer/calibration/{wizard_id}')
    async def state(wizard_id:str,request:Request):
        if request.query_params:raise HTTPException(400,'Fixed calibration session query')
        def read():
            result=wizard.session(get_store(),wizard_id);binding=result.get('source_model_binding')
            if not binding:raise ValueError('Legacy unbound wizard requires a new admitted source selection')
            current_geometry=current(binding['model_job_id'])
            if current_geometry['source_model_binding']!=binding:raise ValueError('Source model binding changed; old wizard remains preserved')
            return dict(result,current_geometry=current_geometry)
        return await safe(read)
