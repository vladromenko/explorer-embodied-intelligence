"""ENGINEERING_GLUE: Authenticated routes mounted INSIDE the existing factory web owner.

No process/service/device is created. Default execution permission is false.
The original global STOP remains independent of these session routes.
"""
import secrets

from fastapi import HTTPException, Request
from fastapi.responses import Response
from .legacy_bridge import parse_dispatch_query


def mount_native_stream_api(app, mux, *, token, observed_test_permit=lambda:False, reference_reader=None,activation=None,cooperative=None):
    if not isinstance(token, str) or len(token) < 16:
        raise ValueError('Existing private original-owner token required')

    def authenticate(request):
        supplied = request.headers.get('authorization', '').removeprefix('Bearer ')
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(401, 'Authentication required')

    def allowed(request,body):
        authenticate(request)
        if activation is not None:
            operation(activation.require,body.get('authorization_id'))
        if observed_test_permit() is not True:
            raise HTTPException(409, 'SEPARATE_USER_OBSERVED_TEST_REQUIRED')

    def operation(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except OSError as error:
            raise HTTPException(503,'Existing native cache unavailable: '+type(error).__name__) from error
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            raise HTTPException(409, str(error)) from error

    def estimate():
        value=operation(reference_reader or mux.arm.status)
        return {**value,'boot_id':value.get('boot_id',mux.tap.boot_id),
                'clock_domain':mux.tap.clock_domain,'state_source':'command_estimate',
                'reference_read_native_monotonic_ns':mux.tap.clock(),
                'reference_read_basis':'native_host_read_original_command_reference_not_measured_pose',
                'measured':False,'attainment_measured':False}

    @app.get('/api/explorer/retarget/status')
    def status(request:Request):
        authenticate(request)
        return {**mux.status(), 'execution_permission': observed_test_permit() is True,
                'activation':activation.status() if activation else None,
                'native_dispatch_semantics': 'original_host_publication_not_MCU_attainment',
                'recording_required_for_mirror': False,
                'measured_joint_feedback_required': False}

    if activation is not None:
        @app.get('/api/explorer/retarget/activation/status')
        def activation_status(request:Request):
            authenticate(request);return activation.status()

        @app.get('/api/explorer/retarget/activation/challenge')
        def activation_challenge(request:Request):
            authenticate(request);return operation(activation.challenge)

        @app.get('/api/explorer/retarget/activation/frame/{challenge_id}')
        def activation_frame(challenge_id:str,request:Request):
            authenticate(request);jpeg,frame=operation(activation.frame,challenge_id)
            return Response(jpeg,media_type='image/jpeg',headers={'Cache-Control':'no-store',
                'X-JPEG-SHA256':frame['jpeg_sha256'],'X-Frame-ID':frame['frame_id'],
                'X-Monotonic-NS':str(frame['received_monotonic_ns']),'X-Boot-ID':frame['boot_id']})

        @app.post('/api/explorer/retarget/activation/authorize')
        def activation_authorize(request:Request,body:dict):
            authenticate(request);return operation(activation.authorize,body)

        @app.post('/api/explorer/retarget/activation/revoke')
        def activation_revoke(request:Request,body:dict):
            authenticate(request);return activation.revoke('EXPLICIT_OPERATOR_REVOKE',expected_authorization_id=body.get('expected_authorization_id'))

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
        allowed(request,body)
        if activation is not None and activation.status().get('input_mode')!='so101_leader':
            raise HTTPException(409,'Measured leader begin requires its own explicit observed input adapter')
        function=lambda:mux.begin(observing=body.get('observing'),neutral=body.get('neutral'))
        return operation(activation.transition,function,body.get('authorization_id')) if activation else operation(function)

    @app.post('/api/explorer/retarget/recenter')
    def recenter(request:Request, body:dict):
        allowed(request,body)
        function=lambda:mux.recenter(body.get('control_epoch'))
        return operation(activation.transition,function,body.get('authorization_id')) if activation else operation(function)

    @app.post('/api/explorer/retarget/resume')
    def resume(request:Request, body:dict):
        allowed(request,body)
        return operation(mux.resume, body.get('control_epoch'), observing=body.get('observing'))

    @app.post('/api/explorer/retarget/input')
    def leader_input(request:Request, body:dict):
        allowed(request,body)
        result=operation(mux.leader_intent, body.get('control_epoch'), body.get('source_sequence'),
                         body.get('joint_velocity_deg_s'), body.get('source_evidence'),
                         authorization_id=body.get('authorization_id'))
        try:
            result['command_estimate']=estimate()
        except HTTPException:
            result['command_estimate_unavailable']=True
        return result

    @app.post('/api/explorer/retarget/base')
    def keyboard_base(request:Request, body:dict):
        allowed(request,body)
        return operation(mux.keyboard_base_input, body.get('control_epoch'), body.get('actions', {}),
                         observing=body.get('observing'))

    @app.post('/api/explorer/retarget/stop')
    def stop(request:Request, body:dict):
        authenticate(request)
        return operation(mux.stop, body.get('control_epoch'))

    if cooperative is not None:
        from .native_cooperative05 import mount_native_cooperative_api
        mount_native_cooperative_api(app,cooperative,authenticate=authenticate,allowed=allowed,operation=operation,activation=activation)
    return {'schema': 'explorer_native_retarget_mount_v1', 'mounted': True,
            'new_servo_publishers': 0, 'controller_settings_written': False,
            'physical_qualification': False}
