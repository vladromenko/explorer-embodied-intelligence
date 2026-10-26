"""Exclusive mocked accelerator ownership, original cached frames, no devices."""
from types import SimpleNamespace
import threading
import time

import numpy as np
import pytest

from explorer_shared.hailo_observer import HailoObserver
from explorer_shared.lab_semantics import BackendInitializationError, EventSemanticWorker, HailoVLMBackend
from explorer_shared.tabletop_grounding import CachedFrame


def eventually(predicate, timeout=3.):
    deadline = time.monotonic() + timeout
    ready = predicate()
    while not ready and time.monotonic() < deadline:
        threading.Event().wait(.01)
        ready = predicate()
    assert ready


class Source:
    camera_id = 'pi-overview'
    def __init__(self):
        self.counter = 0
    def latest(self):
        self.counter += 1
        return SimpleNamespace(frame_id='frame-' + str(self.counter), bgr=np.zeros((2,2,3),np.uint8),
                               wall_utc='fixture', monotonic_ns=time.monotonic_ns(), timestamp_basis='host_receive_estimate')


def observer_fixture():
    source = Source()
    owners, events = set(), []
    class Detector:
        model_info = {'model': 'mock-cv'}
        def __init__(self, path):
            assert not owners
            owners.add('cv'); events.append('cv-open')
        def detect(self, image):
            assert owners == {'cv'}
            return [], 1.
        def close(self):
            assert owners == {'cv'}
            owners.remove('cv'); events.append('cv-close')
    observer = HailoObserver(source, 'unused-mock-path', lambda _:None, detector_factory=Detector, inference_hz=5.)
    observer.start()
    eventually(lambda: observer.snapshot()['available'])
    return observer, source, owners, events


def selected_frame():
    frame = CachedFrame('pi-overview','original-frame',b'original-frozen-JPEG',2,2,'pi','boot-1',time.monotonic_ns())
    return frame, {**frame.metadata(), 'purpose':'object', 'pixel':[1.,1.], 'box':[0.,0.,1.,1.],
                   'mask_sha256':'2'*64, 'track_id':4, 'track_session_id':'track-1'}


def test_cv_releases_before_semantics_and_camera_cache_still_operates():
    observer, source, owners, events = observer_fixture()
    release = threading.Event(); entered = threading.Event()
    class Backend:
        info = {'model_sha256':'1'*64, 'model':'mock-not-real-Hailo'}
        def __init__(self):
            assert not owners
            owners.add('semantic'); events.append('semantic-open')
        def infer(self, frame, selection, prompt):
            assert owners == {'semantic'} and frame.jpeg == b'original-frozen-JPEG'
            entered.set(); assert release.wait(2.)
            return {'semantic_text': 'mock-only', 'metric_coordinates_produced':False}
        def close(self):
            owners.remove('semantic'); events.append('semantic-close')
    worker = EventSemanticWorker(observer, Backend, clock_domain='pi', boot_id='boot-1')
    try:
        worker.start(); frame, selection = selected_frame(); worker.submit(frame, selection, 'Describe selected object')
        assert entered.wait(2.)
        assert observer.snapshot()['state'] == 'cv_released_for_semantic_request'
        before = source.counter
        for _ in range(4):
            source.latest()  # capture/cache remains callable; no camera ownership is transferred
        assert source.counter == before+4
        release.set()
        eventually(lambda: worker.snapshot()['state'] == 'SEMANTIC_RESULT')
        eventually(lambda: observer.snapshot()['available'])
        result = worker.snapshot()['result']
        assert result['source_frame']['frame_sha256'] == frame.sha256
        assert result['selection']['mask_sha256'] == '2'*64
        assert result['result']['semantic_text'] == 'mock-only' and not result['physical_actions_emitted']
        assert events[:4] == ['cv-open','cv-close','semantic-open','semantic-close']
    finally:
        release.set(); worker.close(); observer.close()
    assert owners == set()


def test_cancelled_and_coalesced_events_do_not_publish_late_current_result():
    observer, _, owners, _ = observer_fixture()
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Backend:
        info = {'model':'mock'}
        def __init__(self):
            assert not owners; owners.add('semantic')
        def infer(self, frame, selection, prompt):
            calls.append(prompt)
            if len(calls) == 1:
                entered.set(); assert release.wait(2.)
            return {'semantic_text':prompt}
        def close(self):
            owners.remove('semantic')
    worker = EventSemanticWorker(observer, Backend, clock_domain='pi', boot_id='boot-1')
    try:
        worker.start(); frame, selection = selected_frame(); worker.submit(frame, selection, 'one')
        assert entered.wait(2.)
        worker.submit(frame,selection,'two'); worker.submit(frame,selection,'three')
        release.set()
        eventually(lambda: worker.snapshot().get('result',{}).get('result',{}).get('semantic_text') == 'three')
        report = worker.snapshot()
        assert calls == ['one','three']
        assert any(row['outcome'] == 'COALESCED_BEFORE_START' for row in report['receipts'])
        assert any(row['outcome'] == 'LATE_OR_CANCELLED_RESULT_DISCARDED' for row in report['receipts'])
        worker.cancel()
        assert worker.snapshot()['result'] is None
    finally:
        release.set(); worker.close(); observer.close()


@pytest.mark.parametrize('released', [True, False])
def test_initialization_failure_only_resumes_cv_with_positive_release_proof(released):
    observer, _, owners, _ = observer_fixture()
    def backend():
        assert not owners
        raise BackendInitializationError('mock initialization failure', resources_released=released)
    worker = EventSemanticWorker(observer, backend, clock_domain='pi', boot_id='boot-1')
    try:
        worker.start(); frame, selection = selected_frame(); worker.submit(frame, selection,'mock')
        eventually(lambda: worker.snapshot()['state'] == 'FAILED')
        if released:
            eventually(lambda: observer.snapshot()['available'])
        else:
            eventually(lambda: observer.snapshot()['state'] == 'semantic_release_unknown')
            with pytest.raises(RuntimeError):
                with observer.semantic_slot(timeout_s=.1):
                    pytest.fail('Unknown previous release must not open another accelerator owner')
            assert observer._pause_requested.is_set()  # failed entry must not accidentally resume CV
    finally:
        worker.close(); observer.close()


def test_backend_close_failure_keeps_unknown_owner_from_restarting_cv():
    observer, _, owners, _ = observer_fixture()
    class Backend:
        info = {'model':'mock'}
        def __init__(self):
            assert not owners; owners.add('semantic')
        def infer(self, *args):
            return {'semantic_text':'never a current result'}
        def close(self):
            raise RuntimeError('mock release not confirmed')
    worker = EventSemanticWorker(observer, Backend, clock_domain='pi', boot_id='boot-1')
    try:
        worker.start(); frame, selection = selected_frame(); worker.submit(frame, selection,'mock')
        eventually(lambda: worker.snapshot()['state'] == 'FAILED')
        eventually(lambda: observer.snapshot()['state'] == 'semantic_release_unknown')
        assert owners == {'semantic'}
    finally:
        worker.close(); observer.close(); owners.clear()  # a MOCK owner only


def test_future_stale_or_wrong_frame_request_is_rejected_without_cv_pause():
    observer, _, owners, _ = observer_fixture()
    worker = EventSemanticWorker(observer, lambda:pytest.fail('must not create backend'),
                                  clock_domain='pi', boot_id='boot-1', monotonic_ns=lambda:1)
    try:
        worker.start(); frame, selection = selected_frame()
        with pytest.raises(ValueError):
            worker.submit(frame,selection,'mock')
        assert owners == {'cv'}
    finally:
        worker.close(); observer.close()


def test_unqualified_runtime_refuses_before_any_hardware_import_or_model_open(tmp_path):
    with pytest.raises(BackendInitializationError) as failure:
        HailoVLMBackend(tmp_path/'not-a-model', {}, runtime_version='5.4.0')
    assert failure.value.resources_released is True
