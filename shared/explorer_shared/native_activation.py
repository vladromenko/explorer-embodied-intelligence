"""ENGINEERING_GLUE: explicit observed-session permission, cached camera only.

Authorization/challenge/revoke are metadata operations. They neither publish a
command nor renew the original .45s native operator lease. Original HOME stays
an independent, visibly confirmed user action through its existing endpoint.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time
import uuid


class NativeCachedObservation:
    def __init__(self,root,boot_id,*,clock=time.monotonic_ns):
        self.root,self.boot,self.clock=Path(root),boot_id,clock
    def snapshot(self,*,with_jpeg=False):
        path=self.root/'data/camera-frame.json'
        if path.stat().st_size>128*1024:
            raise ValueError('Cached native camera metadata exceeds bound')
        raw=path.read_bytes()
        value=json.loads(raw);tick=value.get('received_monotonic_ns')
        # Existing camera freshness contract, not a new controller lease.
        if (type(tick) is not int or not 0<=self.clock()-tick<=2_000_000_000 or
                value.get('same_frame_hash_verified') is not True or not value.get('frame_id') or
                not isinstance(value.get('jpeg_sha256'),str) or len(value['jpeg_sha256'])!=64):
            raise ValueError('Fresh hash-bound native cached camera frame required')
        result={'frame_id':value['frame_id'],'jpeg_sha256':value['jpeg_sha256'],
                'received_monotonic_ns':tick,'boot_id':self.boot,
                'clock_domain':'native_kernel_monotonic:'+self.boot,
                'timestamp_basis':'native_camera_callback_receive_estimate_not_exposure',
                'camera_id':value.get('camera_id'),'metadata_sha256':hashlib.sha256(raw).hexdigest()}
        if with_jpeg:
            if value.get('jpeg')!='frame-raw.jpg':
                raise ValueError('Only existing original frame-raw.jpg cache is allowed')
            image=self.root/'data/frame-raw.jpg'
            if image.stat().st_size>2*1024**2:
                raise ValueError('Cached native JPEG exceeds source bound')
            jpeg=image.read_bytes()
            if (len(jpeg)>2*1024**2 or not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9') or
                    hashlib.sha256(jpeg).hexdigest()!=value['jpeg_sha256'] or path.read_bytes()!=raw):
                raise ValueError('Original camera cache advanced while pinning; retry challenge')
            result['jpeg']=jpeg
        return result


class ObservedNativeActivation:
    def __init__(self,mux,camera,*,clock=time.monotonic_ns):
        self.mux,self.camera,self.clock=mux,camera,clock
        self.lock=threading.RLock();self.pending=None;self.authorization=None;self.revoked_reason='NOT_AUTHORIZED'
        self.revision=0
        self.events=[]
    def _event(self,kind,**value):
        event={'event_id':uuid.uuid4().hex,'kind':kind,'boot_id':self.mux.tap.boot_id,
               'native_monotonic_ns':self.clock(),**value,'physical_command_count':0,
               'native_operator_lease_renewed':False}
        self.events.append(event);self.events=self.events[-128:]
        return event
    def challenge(self):
        frame=self.camera.snapshot(with_jpeg=True);native=self.mux.status()
        with self.lock:
            self.revision+=1
            ident=uuid.uuid4().hex
            self.pending={'challenge_id':ident,'frame':frame,'native_generation':native['owner_generation'],
                          'control_epoch':native['control_epoch'],'revision':self.revision}
            return {'schema':'explorer_native_observed_challenge_v1','challenge_id':ident,
                    'frame':{key:value for key,value in frame.items() if key!='jpeg'},
                    'native_generation':native['owner_generation'],'control_epoch':native['control_epoch'],
                    'frame_path':'/api/explorer/retarget/activation/frame/'+ident,
                    'execution_permission':False,'physical_command_count':0,
                    'required_confirmations':['observing','neutral','space_clear','user_observed_test_confirmed'],
                    'authorization_is_not_hardware_success':True}
    def frame(self,challenge_id):
        with self.lock:
            pending=self.pending
            if not pending or not secrets.compare_digest(str(challenge_id),pending['challenge_id']):
                raise ValueError('Unknown observed camera challenge')
            frame=pending['frame']
            if not 0<=self.clock()-frame['received_monotonic_ns']<=2_000_000_000:
                raise ValueError('Observed camera challenge expired')
            return frame['jpeg'],{key:value for key,value in frame.items() if key!='jpeg'}
    def authorize(self,body):
        actor=body.get('actor_id')
        if not isinstance(actor,str) or not 0<len(actor)<=128:
            raise ValueError('Bounded explicit operator identity required')
        input_mode=body.get('input_mode','so101_leader')
        if input_mode not in ('so101_leader','keyboard','browser_gamepad','cooperative_policy'):
            raise ValueError('Explicit supported observed input adapter required')
        if not all(body.get(key) is True for key in ('observing','neutral','space_clear','user_observed_test_confirmed')):
            raise ValueError('Separate explicit current user-observed test confirmation required')
        with self.lock:
            expected_revision=self.revision
        self.camera.snapshot()  # current source remains live, independently of pinned frame
        # Same order as original STOP invalidation: teleop -> activation. Camera
        # I/O is outside both locks; revoke/STOP during it permanently fences the
        # pending confirmation. Re-read ownership at the actual attach boundary.
        with self.mux.teleop.lock:
            native=self.mux.status()
            if (native['active'] or native.get('stop_latched') is not True or
                    any(abs(value)>1e-8 for value in self.mux.teleop.inputs.values())):
                raise ValueError('Existing native STOP and neutral inputs required before observed authorization')
            with self.lock:
                pending=self.pending
                if (pending is None or expected_revision!=self.revision or pending['revision']!=expected_revision or
                        body.get('challenge_id')!=pending['challenge_id'] or
                        body.get('displayed_jpeg_sha256')!=pending['frame']['jpeg_sha256'] or
                        not 0<=self.clock()-pending['frame']['received_monotonic_ns']<=2_000_000_000 or
                        native['owner_generation']!=pending['native_generation'] or
                        native['control_epoch']!=pending['control_epoch']):
                    raise ValueError('Pinned displayed camera challenge or native ownership changed')
                receipt=self._event('EXPLICIT_USER_OBSERVED_AUTHORIZATION',challenge_id=pending['challenge_id'],
                    displayed_jpeg_sha256=pending['frame']['jpeg_sha256'],camera_frame=deepcopy({
                        key:value for key,value in pending['frame'].items() if key!='jpeg'}),
                    actor_id=body.get('actor_id'),qualification_scope='observed_session_permission_not_attainment')
                receipt['input_mode']=input_mode
                receipt['owner_chassis_test_authorized']=body.get('owner_chassis_test_authorized') is True
                self.authorization={'authorization_id':receipt['event_id'],'receipt':receipt,
                    'generation':self.mux.teleop.generation,'native_epoch':None,'input_mode':input_mode,
                    'owner_chassis_test_authorized':body.get('owner_chassis_test_authorized') is True,
                    'bound_manual_generation':None}
                self.pending=None;self.revoked_reason=None
        return self.status()
    def bind_epoch(self,epoch,authorization_id):
        with self.lock:
            if self.authorization is None or self.authorization['authorization_id']!=authorization_id:
                raise ValueError('Observed authorization was revoked before native begin/recenter')
            self.authorization['native_epoch']=epoch;self.authorization['generation']=self.mux.teleop.generation
            self.authorization.pop('transition',None)
    def transition(self,function,authorization_id):
        self.require(authorization_id)
        with self.lock:
            if self.authorization is None or self.authorization['authorization_id']!=authorization_id:
                raise ValueError('Observed authorization changed')
            if self.authorization.get('input_mode') not in ('so101_leader','cooperative_policy'):
                raise ValueError('Leader mux requires its explicitly observed input adapter')
            self.authorization['transition']=True
        result=None
        try:
            result=function();self.bind_epoch(result['control_epoch'],authorization_id);return result
        except BaseException:
            # A bounded begin/recenter may have returned after a metadata STOP
            # revoked its authorization. Revoke ONLY that exact returned native
            # owner, never a later USER epoch/generation.
            if isinstance(result,dict):
                with self.mux.teleop.lock:
                    if (self.mux.active and self.mux.epoch==result.get('control_epoch') and
                            self.mux.generation==result.get('native_generation') and
                            self.mux.teleop.generation==result.get('owner_generation')):
                        try:self.mux.stop(result['control_epoch'])
                        except Exception:pass  # scoped delivery unknown; no automatic recovery.
            self.revoke('NATIVE_TRANSITION_FAILED',expected_authorization_id=authorization_id)
            raise
    def revoke(self,reason='EXPLICIT_OPERATOR_REVOKE',*,expected_authorization_id=None):
        # May run inside original STOP lock: memory only, no network/fsync.
        with self.lock:
            if expected_authorization_id is not None and (
                    self.authorization is None or self.authorization['authorization_id']!=expected_authorization_id):
                return {'revoked':False,'reason':'SUPERSEDED_AUTHORIZATION','physical_command_count':0}
            self.revision+=1;self.pending=None
            if self.authorization is not None:
                self._event('OBSERVED_AUTHORIZATION_REVOKED',reason=reason,
                            authorization_id=self.authorization['authorization_id'])
            self.authorization=None;self.revoked_reason=reason
        return {'revoked':True,'reason':reason,'physical_command_count':0}
    def allowed(self):
        with self.lock:
            authorization=deepcopy(self.authorization)
        if authorization is None:
            return False
        try:self.camera.snapshot()
        except (OSError,ValueError,TypeError,KeyError):
            self.revoke('CAMERA_SOURCE_UNAVAILABLE',expected_authorization_id=authorization['authorization_id']);return False
        # No teleop lock is acquired under the activation lock. Native permit
        # independently checks owner/generation/lease under its original lock.
        epoch=authorization['native_epoch'];owner=self.mux.teleop.owner
        if epoch is not None:
            valid=(self.mux.active and (self.mux.epoch==epoch or (
                       authorization.get('transition') is True and self.mux.state=='CLUTCHED')) and
                   self.mux.teleop.generation==authorization['generation'] and owner=='keyboard')
        else:
            manual=authorization.get('input_mode') in ('keyboard','browser_gamepad')
            if manual and authorization.get('bound_manual_generation') is not None:
                valid=(not self.mux.active and owner=='keyboard' and
                       self.mux.teleop.generation==authorization['bound_manual_generation'] and
                       (self.mux.teleop.stop_latched or self.mux.clock()<self.mux.teleop.lease))
            else:
                valid=((not self.mux.active or self.mux.state=='CLUTCHED') and
                       (self.mux.teleop.stop_latched or manual) and owner in (None,'keyboard') and
                       self.mux.teleop.generation in (authorization['generation'],authorization['generation']+1) and
                       (manual or not any(abs(value)>1e-8 for value in self.mux.teleop.inputs.values())))
                if manual and not self.mux.teleop.stop_latched:
                    valid=valid and owner=='keyboard' and self.mux.clock()<self.mux.teleop.lease
                if valid and manual and owner=='keyboard' and not self.mux.teleop.stop_latched:
                    with self.lock:
                        if self.authorization and self.authorization['authorization_id']==authorization['authorization_id']:
                            self.authorization['bound_manual_generation']=self.mux.teleop.generation
        if not valid:
            self.revoke('NATIVE_OWNER_OR_EPOCH_CHANGED',expected_authorization_id=authorization['authorization_id'])
        with self.lock:
            return bool(valid and self.authorization and
                        self.authorization['authorization_id']==authorization['authorization_id'])
    def require(self,authorization_id):
        if not self.allowed():
            raise ValueError('SEPARATE_USER_OBSERVED_TEST_REQUIRED')
        with self.lock:
            if self.authorization is None or self.authorization['authorization_id']!=authorization_id:
                raise ValueError('Old or missing observed authorization ID')
        return True
    def receipt(self,authorization_id):
        with self.lock:
            if self.authorization is None or self.authorization['authorization_id']!=authorization_id:
                return None
            return deepcopy(self.authorization['receipt'])
    def current_authorization_id(self):
        """Memory-only identity for scoped cleanup under the original owner lock."""
        with self.lock:return self.authorization['authorization_id'] if self.authorization is not None else None
    def base_allowed(self):
        if not self.allowed():return False
        if not self.mux.teleop.stop_latched and not self.mux.clock()<self.mux.teleop.lease:return False
        with self.lock:
            return bool(self.authorization and self.authorization.get('owner_chassis_test_authorized') is True)
    def status(self):
        permitted=self.allowed();native=self.mux.status()
        with self.lock:
            authorization=deepcopy(self.authorization)
            return {'schema':'explorer_native_observed_activation_v1','execution_permission':permitted,
                    'authorization_id':authorization['authorization_id'] if authorization else None,
                    'authorization_receipt':authorization['receipt'] if authorization else None,
                    'revoked_reason':self.revoked_reason,'native_active':native['active'],
                    'native_stop_latched':native['stop_latched'],'control_epoch':native['control_epoch'],
                    'input_mode':authorization.get('input_mode') if authorization else None,
                    'chassis_motion_allowed':bool(permitted and authorization and authorization.get('owner_chassis_test_authorized') is True),
                    'events':deepcopy(self.events),'physical_command_count':0,
                    'native_operator_lease_renewed':False,'hardware_success_claimed':False}
