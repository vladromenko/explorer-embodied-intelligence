"""Camera-only preview tests: all device acquisition is replaced by inert fixtures."""
import pytest
from fastapi.testclient import TestClient
from so101_lab import preview as p
from so101_lab import app as web
from so101_lab.resources import ResourceBusy

class FakeCamera:
    opened=[]
    def __init__(self,name,device,cfg):self.name=name;self.device=device;self.sequence=1;self.error=None
    def start(self):self.opened.append(self.name)
    def close(self):pass
    def latest_before(self,timestamp):return None


def test_preview_constructor_and_status_do_not_open_anything(monkeypatch):
    monkeypatch.setattr(p,'CameraCapture',lambda *a:pytest.fail('No acquisition on construction/status'))
    v=p.CameraPreview();assert not v.status()['active'];assert v.status()['torque_enabled'] is False
    with pytest.raises(PermissionError):v.start()


def test_busy_refuses_before_camera_factory(monkeypatch):
    def refuse(paths):raise ResourceBusy('busy')
    monkeypatch.setattr(p,'assert_hardware_available',refuse)
    monkeypatch.setattr(p,'CameraCapture',lambda *a:pytest.fail('Occupied cameras must never be opened'))
    v=p.CameraPreview()
    with pytest.raises(ResourceBusy):v.start(explicit_user_start=True)
    assert not v.status()['active']


def test_explicit_preview_only_acquires_camera_paths(monkeypatch):
    paths=[];FakeCamera.opened=[]
    monkeypatch.setattr(p,'assert_hardware_available',lambda x:paths.extend(x))
    monkeypatch.setattr(p,'CameraCapture',FakeCamera)
    v=p.CameraPreview()
    try:
        s=v.start(explicit_user_start=True);assert s['active'];assert not s['torque_enabled'];assert s['camera_only']
        assert set(FakeCamera.opened)=={'top','wrist'}
        assert all('so101-leader' not in x and 'so101-follower' not in x for x in paths)
    finally:v.stop()
    assert not v.status()['active']


def test_partial_preview_failure_closes_first_camera_and_lease(monkeypatch):
    closed=[]
    class Broken(FakeCamera):
        def start(self):
            if self.name=='wrist':raise RuntimeError('no wrist')
        def close(self):closed.append(self.name)
    monkeypatch.setattr(p,'CameraCapture',Broken);monkeypatch.setattr(p,'assert_hardware_available',lambda x:None)
    v=p.CameraPreview()
    with pytest.raises(RuntimeError,match='wrist'):v.start(explicit_user_start=True)
    assert set(closed)=={'top','wrist'};assert not v.status()['active'];assert v.lease is None


def test_preview_http_is_explicit_and_no_record_start(monkeypatch):
    calls=[]
    class Manager:
        def status(self):return {'active':False}
    class Preview:
        def start(self,**kwargs):calls.append(kwargs);return {'active':True,'torque_enabled':False}
        def stop(self):return {'active':False}
    monkeypatch.setattr(web,'manager',Manager());monkeypatch.setattr(web,'camera_preview',Preview())
    c=TestClient(web.app)
    assert c.post('/api/preview/start',json={}).status_code==200
    assert calls==[{'explicit_user_start':True}]
    assert c.post('/api/preview/stop',json={}).status_code==200


def test_inactive_recording_never_serves_stale_synthetic_frame(monkeypatch):
    class Manager:
        def status(self):return {'active':False,'source':'synthetic_test'}
        def latest_frame(self,*args):pytest.fail('Stale synthetic frame should not be requested')
    monkeypatch.setattr(web,'manager',Manager());monkeypatch.setattr(web,'camera_preview',None)
    c=TestClient(web.app);assert c.get('/api/frame/top').status_code==404

def test_ready_episode_preview_uses_current_camera_not_saved_png(monkeypatch):
    import time,numpy as np
    class Camera:
        def latest_before(self,cutoff):return {'timestamp_ns':time.monotonic_ns(),'image':np.zeros((8,8,3),dtype=np.uint8)}
    class Source:cameras={'top':Camera()}
    class Manager:
        source=Source()
        def status(self):return {'active':True,'state':'ready','source':'hardware'}
        def latest_frame(self,*a):pytest.fail('Paused session must not show stale recording frame')
    monkeypatch.setattr(web,'manager',Manager());monkeypatch.setattr(web,'camera_preview',None)
    r=TestClient(web.app).get('/api/frame/top');assert r.status_code==200;assert r.headers['content-type']=='image/jpeg'
