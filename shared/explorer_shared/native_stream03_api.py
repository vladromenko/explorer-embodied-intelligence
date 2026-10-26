"""ENGINEERING_GLUE: Authenticated routes mounted INSIDE the existing factory web owner.

No process/service/device is created. Default execution permission is false.
The original global STOP remains independent of these session routes.
"""
import secrets

from fastapi import HTTPException, Request
from .legacy_bridge import parse_dispatch_query


def mount_native_stream_api(app, mux, *, token, observed_test_permit=lambda:False, reference_reader=None):
    if not isinstance(token, str) or len(token) < 16:
        raise ValueError('Existing private original-owner token required')

    def authenticate(request):
        supplied = request.headers.get('authorization', '').removeprefix('Bearer ')
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(401, 'Authentication required')

    def allowed(request):
        authenticate(request)
        if observed_test_permit() is not True:
            raise HTTPException(409, 'SEPARATE_USER_OBSERVED_TEST_REQUIRED')

    def operation(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            raise HTTPException(409, str(error)) from error

    def estimate():
        value=operation(reference_reader or mux.arm.status)
        return {**value,'boot_id':value.get('boot_id',mux.tap.boot_id),
                'clock_domain':mux.tap.clock_domain,'state_source':'command_estimate',
                'measured':False,'attainment_measured':False}

    @app.get('/api/explorer/retarget/status')
    def status(request:Request):
        authenticate(request)
        return {**mux.status(), 'execution_permission': observed_test_permit() is True,
                'native_dispatch_semantics': 'original_host_publication_not_MCU_attainment',
                'recording_required_for_mirror': False,
                'measured_joint_feedback_required': False}

    @app.get('/api/explorer/retarget/dispatch')
    def dispatch(request:Request, after_sequence:int=0):
        authenticate(request)
        query=operation(parse_dispatch_query,request.url.query)
        return operation(mux.tap.since,(query or {}).get('after_sequence',0),limit=128)

    @app.get('/api/explorer/retarget/reference')
    def reference(request:Request):
        authenticate(request)
        # Existing factory command estimate only; no feedback/readback call.
        return estimate()

    @app.post('/api/explorer/retarget/begin')
    def begin(request:Request, body:dict):
        allowed(request)
        return operation(mux.begin, observing=body.get('observing'), neutral=body.get('neutral'))

    @app.post('/api/explorer/retarget/recenter')
    def recenter(request:Request, body:dict):
        allowed(request)
        return operation(mux.recenter, body.get('control_epoch'))

    @app.post('/api/explorer/retarget/resume')
    def resume(request:Request, body:dict):
        allowed(request)
        return operation(mux.resume, body.get('control_epoch'), observing=body.get('observing'))

    @app.post('/api/explorer/retarget/input')
    def leader_input(request:Request, body:dict):
        allowed(request)
        result=operation(mux.leader_intent, body.get('control_epoch'), body.get('source_sequence'),
                         body.get('joint_velocity_deg_s'), body.get('source_evidence'))
        try:
            result['command_estimate']=estimate()
        except HTTPException:
            result['command_estimate_unavailable']=True
        return result

    @app.post('/api/explorer/retarget/base')
    def keyboard_base(request:Request, body:dict):
        allowed(request)
        return operation(mux.keyboard_base_input, body.get('control_epoch'), body.get('actions', {}),
                         observing=body.get('observing'))

    @app.post('/api/explorer/retarget/stop')
    def stop(request:Request, body:dict):
        authenticate(request)
        return operation(mux.stop, body.get('control_epoch'))

    return {'schema': 'explorer_native_retarget_mount_v1', 'mounted': True,
            'new_servo_publishers': 0, 'controller_settings_written': False,
            'physical_qualification': False}
