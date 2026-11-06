"""Explicit camera-only preview. No bus constructor, servo writes or torque calls."""
import io,threading,time
from .resources import ResourceLease,assert_hardware_available
from .hardware import CameraCapture,hardware_settings

class CameraPreview:
    def __init__(self):
        self.lock=threading.RLock();self.cameras={};self.lease=None;self.error=None;self.started_ns=None
    def start(self,*,explicit_user_start=False):
        if not explicit_user_start:raise PermissionError('Подключение камер требует явной кнопки в интерфейсе')
        with self.lock:
            if self.cameras:
                if self.error:raise RuntimeError('Сначала отключите предпросмотр: '+self.error)
                return self.status()
            cfg=hardware_settings()
            assert_hardware_available(list(cfg['cameras'].values()))
            lease=ResourceLease('cameras');lease.__enter__();self.lease=lease
            self.error=None
            try:
                # Check again under the common lease, before any camera opens.
                assert_hardware_available(list(cfg['cameras'].values()))
                for name,device in cfg['cameras'].items():
                    cam=CameraCapture(name,device,cfg);self.cameras[name]=cam;cam.start()
                self.started_ns=time.monotonic_ns()
                return self.status()
            except Exception as e:
                self.error=str(e);self.stop();raise
    def stop(self):
        with self.lock:
            errors=[];remaining={}
            for name,cam in self.cameras.items():
                try:cam.close()
                except Exception as e:errors.append(f'{name}: {e}');remaining[name]=cam
                thread=getattr(cam,'thread',None)
                if thread and thread.is_alive():errors.append(f'{name}: поток камеры ещё не остановлен');remaining[name]=cam
            if remaining:
                self.cameras=remaining;self.error='; '.join(errors);raise RuntimeError(self.error)
            self.cameras={};self.started_ns=None
            if self.lease:self.lease.__exit__(None,None,None);self.lease=None
            if errors:self.error='; '.join(errors);raise RuntimeError(self.error)
            return self.status()
    def status(self):
        with self.lock:
            cameras={};now=time.monotonic_ns()
            for name,cam in self.cameras.items():
                error=cam.error;age=None
                try:
                    frame=cam.latest_before(now)
                    if frame is not None:
                        age=(now-frame['timestamp_ns'])/1e6
                        if age>500:error='Нет свежих кадров более500ms'
                except Exception as exc:error=str(exc)
                cameras[name]={'connected':not error,'error':error,'frames_received':cam.sequence,'last_frame_age_ms':age,'observed_fps':getattr(cam,'info',{}).get('fps_observed_recent'),'stream':getattr(cam,'info',{})}
            return {'active':bool(self.cameras),'state':'connected' if self.cameras else 'disconnected','camera_only':True,'torque_enabled':False,'error':self.error,'cameras':cameras}
    def latest_frame(self,name):
        with self.lock:
            cam=self.cameras.get(name)
            if cam is None:return None
            frame=cam.latest_before(time.monotonic_ns())
            if frame is None:return None
            if time.monotonic_ns()-frame['timestamp_ns']>1_000_000_000:raise RuntimeError(f'Нет свежего кадра {name}')
            from PIL import Image
            out=io.BytesIO();Image.fromarray(frame['image']).save(out,format='JPEG',quality=78);return out.getvalue()
