"""No physical devices: explicit start, cleanup and ownership transfer regressions."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from so101_lab import app as web
from so101_lab import teleoperation as teleop


class Source:
    calls = []
    failure = None
    cleanup_errors = []

    def __init__(self, settings, teleoperate, capture_cameras=True):
        assert teleoperate is True
        assert capture_cameras is False
        self.cameras = {}
        self.closed = False
        Source.calls.append("construct")

    def start(self, *, explicit_user_start, stop):
        assert explicit_user_start
        Source.calls.append("start")
        return {"source": "isolated_test_fixture"}

    def sample(self, frame_id, *, stop):
        if Source.failure:
            raise Source.failure
        if stop():
            raise InterruptedError("stop")
        Source.calls.append("sample")
        return {"images": {}, "observation.state": [0.] * 6,
                "action": [0.] * 6, "timestamp_ns": time.monotonic_ns()}

    def close(self):
        Source.calls.append("close")
        self.closed = True
        return list(Source.cleanup_errors)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    Source.calls = []
    Source.failure = None
    Source.cleanup_errors = []
    monkeypatch.setattr(teleop, "HardwareSource", Source)
    monkeypatch.setattr(teleop, "assert_hardware_available", lambda paths: None)
    result = teleop.TeleoperationManager(tmp_path)
    yield result
    result.stop()
    result.wait()


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        threading.Event().wait(.005)
    assert predicate()


def test_no_implicit_start_and_no_dataset(manager):
    assert not Source.calls
    with pytest.raises(PermissionError):
        manager.start()
    assert not Source.calls
    manager.start(explicit_user_start=True)
    wait_for(lambda: manager.status()["samples"] >= 2)
    assert manager.status()["state"] == "teleoperating"
    assert not manager.status()["recording"]
    manager.stop_for_recording()
    assert Source.calls[-1] == "close"
    assert not manager.status()["active"]
    assert not (manager.root / "data").exists()


def test_sample_failure_closes_once_and_does_not_retry(manager):
    Source.failure = RuntimeError("USB sample failed")
    manager.start(explicit_user_start=True)
    assert manager.wait(2)
    assert manager.status()["state"] == "failed"
    assert Source.calls == ["construct", "start", "close"]
    assert "USB sample failed" in manager.status()["error"]


def test_unconfirmed_torque_blocks_recording_transfer_and_restart(manager):
    Source.cleanup_errors = ["motor1 Torque_Enable failed"]
    manager.start(explicit_user_start=True)
    wait_for(lambda: manager.status()["samples"] > 0)
    with pytest.raises(RuntimeError, match="Снятие torque"):
        manager.stop_for_recording()
    assert not manager.status()["active"]
    with pytest.raises(RuntimeError, match="Снятие torque"):
        manager.start(explicit_user_start=True)


def test_stop_during_leader_read_never_sends():
    from so101_lab.hardware import HardwareSource
    source = object.__new__(HardwareSource)
    source.teleoperate = True
    stopped = threading.Event()
    source.observe = lambda: {"observation.state": [0.] * 6}

    class Bus:
        def sync_read(self, *args, **kwargs):
            stopped.set()
            return {}

    source.buses = {"leader": Bus()}
    source.send = lambda *args: pytest.fail("No command after operator STOP")
    with pytest.raises(InterruptedError):
        source.sample(1, stop=stopped.is_set)


def test_http_explicit_teleop_then_recording_transfer_order(manager, monkeypatch):
    calls = []

    class Preview:
        def stop(self):
            calls.append("preview_stop")
        def start(self,**kwargs):
            calls.append('preview_start')
            return {'active':True}

    class Recorder:
        def status(self):
            return {"active": False}

        def dataset_info(self, did):
            return {"source": "hardware"}

        def start(self, did, **options):
            assert not manager.status()["active"]
            assert Source.calls[-1] == "close"
            calls.append("record_start")
            return {"state": "starting"}

    monkeypatch.setattr(web, "teleoperation", manager)
    monkeypatch.setattr(web, "camera_preview", Preview())
    monkeypatch.setattr(web, "manager", Recorder())
    client = TestClient(web.app)
    assert client.post("/api/teleoperation/start", json={}).status_code == 200
    wait_for(lambda: manager.status()["samples"] > 0)
    assert client.post("/api/preview/start", json={}).status_code == 200
    result = client.post("/api/record/start", json={"dataset_id": "fixture"})
    assert result.status_code == 200, result.text
    assert calls == ["preview_start", "preview_start", "record_start"]


def test_recording_blocks_another_controller(manager, monkeypatch):
    class Recording:
        def status(self):
            return {"active": True}
    monkeypatch.setattr(web, "teleoperation", manager)
    monkeypatch.setattr(web, "manager", Recording())
    response = TestClient(web.app).post("/api/teleoperation/start", json={})
    assert response.status_code == 409
    assert not Source.calls


def test_stop_cancels_pending_transfer_before_recorder_start(manager, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def wait_for_transfer():
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(manager, "stop_for_recording", wait_for_transfer)
    class Recorder:
        def status(self):return {"active": False}
        def dataset_info(self, did):return {"source": "hardware"}
        def command(self, action):calls.append(action)
        def start(self, *args, **kwargs):pytest.fail("STOP cancelled this pending start")
    monkeypatch.setattr(web, "manager", Recorder())
    monkeypatch.setattr(web, "teleoperation", manager)
    monkeypatch.setattr(web, "camera_preview", None)
    monkeypatch.setattr(web.jobs, "list_jobs", lambda: [])
    client = TestClient(web.app)
    responses = []
    thread = threading.Thread(target=lambda: responses.append(client.post("/api/record/start", json={"dataset_id": "fixture"})))
    thread.start()
    assert entered.wait(2)
    assert client.post("/api/teleoperation/stop", json={}).status_code == 200
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    assert responses[0].status_code == 409
    assert "STOP" in responses[0].json()["detail"]
    assert calls == ["abort"]


def test_hardware_stop_also_stops_waiting_policy(manager, monkeypatch):
    calls = []
    class Recorder:
        def command(self, action):calls.append(("record", action))
    monkeypatch.setattr(web, "manager", Recorder())
    monkeypatch.setattr(web, "teleoperation", manager)
    monkeypatch.setattr(web.jobs, "list_jobs", lambda: [
        {"id": "own-policy", "kind": "policy", "state": "waiting_resources"},
        {"id": "safe-train", "kind": "train", "state": "running"}])
    monkeypatch.setattr(web.jobs, "stop", lambda jid: calls.append(("job", jid)))
    response = TestClient(web.app).post("/api/teleoperation/stop", json={})
    assert response.status_code == 200
    assert calls == [("record", "abort"), ("job", "own-policy")]


def test_queued_policy_blocks_teleoperation_before_camera_release(manager, monkeypatch):
    monkeypatch.setattr(web,'manager',type('Recorder',(),{'status':lambda self:{'active':False}})())
    monkeypatch.setattr(web,'teleoperation',manager)
    monkeypatch.setattr(web.jobs,'list_jobs',lambda:[{'kind':'policy','state':'queued'}])
    monkeypatch.setattr(web,'camera_preview',type('Preview',(),{'stop':lambda self:pytest.fail('Do not release preview')})())
    response=TestClient(web.app).post('/api/teleoperation/start',json={})
    assert response.status_code==409
    assert 'STOP' in response.json()['detail']
    assert not Source.calls
