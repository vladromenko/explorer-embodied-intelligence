"""ENGINEERING_GLUE: authenticated UI bindings for immutable skill workflows.

The application owns authentication and CSRF. All expensive retained-source
inspection runs outside its event loop. This router never dispatches motors.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException


def install_skill_workflow_routes(app, store_provider):
    from . import skill_workflow as service
    router = APIRouter(prefix='/api/skill-workflow')

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, store_provider(), *args, **kwargs)
        except service.WorkflowBlocked as error:
            raise HTTPException(409, '; '.join(error.reasons)) from error
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise HTTPException(400, str(error)) from error

    def version(body):
        value = body.get('version')
        if type(value) is not int or value < 1:
            raise HTTPException(400, 'Explicit positive skill version required')
        return value

    @router.get('')
    async def catalogue():
        return dict(skills=await call(service.list_skills), imports=await call(service.available_imports))

    @router.post('')
    async def proposal(body: dict):
        return await call(service.create_skill, body)

    @router.get('/capture-context')
    async def capture_context(episode_id: str):
        return dict(capture=await call(service.capture_for_episode, episode_id))

    @router.get('/{skill_id}')
    async def workflow(skill_id: str, version: int | None = None):
        return await call(service.workflow, skill_id, version)

    @router.post('/{skill_id}/revision')
    async def revision(skill_id: str, body: dict):
        return await call(service.revise_skill, skill_id, version(body), body.get('patch'), body.get('spec_sha256'))

    @router.post('/{skill_id}/confirm')
    async def confirm(skill_id: str, body: dict):
        return await call(service.confirm_skill, skill_id, version(body), body.get('spec_sha256'))

    @router.post('/{skill_id}/session')
    async def session(skill_id: str, body: dict):
        return await call(service.create_session, skill_id, version(body), body)

    @router.post('/{skill_id}/bind')
    async def bind(skill_id: str, body: dict):
        return await call(service.bind_episode, skill_id, version(body), body.get('variant_id'), body)

    @router.post('/{skill_id}/aggregate')
    async def aggregate(skill_id: str, body: dict):
        return await call(service.build_aggregate, skill_id, version(body))

    @router.post('/{skill_id}/train')
    async def train(skill_id: str, body: dict):
        # Admission, aggregate reservation, queue entry and lineage commit together.
        return await call(service.enqueue_training, skill_id, version(body), body.get('settings'))

    app.include_router(router)
