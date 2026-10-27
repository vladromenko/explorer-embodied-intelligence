"""Existing source04 Station and mocked CV/VLM only; no real devices/native IO."""
from types import SimpleNamespace
import threading

import pytest

from explorer_shared.lab_vision_service import SourceSemanticAdmission
from explorer_shared.lab_semantics import EventSemanticWorker
from test_source_demo_permission import demo_rig
from test_lab_semantics import observer_fixture,selected_frame,eventually


def rig(tmp_path,monkeypatch):
    station,provider,choice,_,bus,_,_,_,_=demo_rig(tmp_path,monkeypatch)
    vision=SimpleNamespace(worker=None);admission=SourceSemanticAdmission(station,vision)
    provider.command_admission=admission.control;provider.command_admission_status=admission.status
    challenge=provider.challenge(choice)
    grant=provider.authorize(dict(binding=choice,challenge_id=challenge['challenge_id'],
        displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],expected_control_epoch=challenge['control_epoch'],
        observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True,actor_id='SOFTWARE-TEST-OPERATOR'))
    return station,provider,choice,bus,vision,admission,grant


def command(provider,choice,grant,operation):
    return provider.command(grant['grant_id'],choice,operation,{'expected_control_epoch':grant['control_epoch']})


def test_semantic_admission_is_idle_only_and_never_stops_or_finishes_the_original_station(tmp_path,monkeypatch):
    station,provider,choice,bus,vision,admission,grant=rig(tmp_path,monkeypatch)
    with admission.semantic():pass
    assert bus.writes==[] and station.record is None
    started=command(provider,choice,grant,'teleop/start')['grant'];writes=len(bus.writes)
    with pytest.raises(RuntimeError,match='explicitly stop'):
        with admission.semantic():raise AssertionError('Must not enter a VLM backend')
    assert len(bus.writes)==writes and station.mode=='teleoperation'
    command(provider,choice,started,'stop');station.record={'episode_id':'recording-metadata-only','samples':[]}
    with pytest.raises(RuntimeError,match='Finish recording'):
        with admission.semantic():raise AssertionError('An open raw capture must not be repurposed')


def test_actual_COMMON_commands_fail_before_native_write_but_scoped_STOP_bypasses_busy_semantics(tmp_path,monkeypatch):
    station,provider,choice,bus,vision,admission,grant=rig(tmp_path,monkeypatch)
    vision.worker=SimpleNamespace(busy=lambda:True)
    assert provider.status()['runtime_admission']['native_commands_blocked']
    with pytest.raises(RuntimeError,match='Semantic inference'):
        command(provider,choice,grant,'teleop/start')
    assert station.mode=='idle' and bus.writes==[]
    vision.worker=None;started=command(provider,choice,grant,'teleop/start')['grant'];before=len(bus.writes)
    vision.worker=SimpleNamespace(busy=lambda:True)
    with pytest.raises(RuntimeError):command(provider,choice,started,'teleop/recenter')
    assert len(bus.writes)==before
    stopped=command(provider,choice,started,'stop')
    assert stopped['native_command_called'] and station.mode=='idle' and len(bus.writes)==before+1
    assert admission.status()['native_commands_blocked']  # STOP did not invent VLM release.


def test_semantic_submission_wins_start_race_before_original_native_callback(tmp_path,monkeypatch):
    station,provider,choice,bus,vision,admission,grant=rig(tmp_path,monkeypatch)
    entered,release,attempted=threading.Event(),threading.Event(),threading.Event();errors=[]
    def semantic():
        with admission.semantic():
            entered.set();assert release.wait(2)
            vision.worker=SimpleNamespace(busy=lambda:True)  # SAME worker.submit commits pending before admission exits.
    def start():
        attempted.set()
        try:command(provider,choice,grant,'teleop/start')
        except RuntimeError as error:errors.append(str(error))
    first=threading.Thread(target=semantic);second=threading.Thread(target=start)
    first.start();assert entered.wait(1);second.start();assert attempted.wait(1)
    release.set();first.join(2);second.join(2)
    assert not first.is_alive() and not second.is_alive() and errors and bus.writes==[] and station.mode=='idle'


def test_native_start_wins_semantic_race_and_only_original_fake_station_is_entered(tmp_path,monkeypatch):
    station,provider,choice,bus,vision,admission,grant=rig(tmp_path,monkeypatch)
    entered,release,attempted=threading.Event(),threading.Event(),threading.Event();errors=[];returns=[]
    original=station.command
    def pending(operation,body):
        if operation=='teleop/start':entered.set();assert release.wait(2)
        return original(operation,body)
    station.command=pending
    def semantic():
        attempted.set()
        try:
            with admission.semantic():raise AssertionError('Native owner already won')
        except RuntimeError as error:errors.append(str(error))
    first=threading.Thread(target=lambda:returns.append(command(provider,choice,grant,'teleop/start')))
    second=threading.Thread(target=semantic)
    first.start();assert entered.wait(1);second.start();assert attempted.wait(1);release.set()
    first.join(2);second.join(2)
    assert not first.is_alive() and not second.is_alive() and returns and errors and bus.writes and station.mode=='teleoperation'
    assert vision.worker is None
    command(provider,choice,returns[0]['grant'],'stop')


def test_cancelled_native_VLM_keeps_admission_closed_until_actual_backend_release(tmp_path,monkeypatch):
    station,provider,choice,bus,vision,admission,grant=rig(tmp_path,monkeypatch)
    observer,_,owners,_=observer_fixture();entered,release=threading.Event(),threading.Event()
    class Backend:
        info={'model':'SOFTWARE-ONLY'}
        def __init__(self):assert not owners;owners.add('semantic')
        def infer(self,*args):entered.set();assert release.wait(2);return {'semantic_text':'DISCARDED SOFTWARE RESULT'}
        def close(self):owners.remove('semantic')
    worker=EventSemanticWorker(observer,Backend,clock_domain='pi',boot_id='boot-1');vision.worker=worker
    try:
        worker.start();frame,selected=selected_frame()
        with admission.semantic():worker.submit(frame,selected,'SOFTWARE TEST')
        assert entered.wait(1);worker.cancel()
        assert worker.snapshot()['state']=='CANCELLED' and worker.busy()
        with pytest.raises(RuntimeError):command(provider,choice,grant,'teleop/start')
        assert not bus.writes and station.mode=='idle'
        release.set();eventually(lambda:not worker.busy());eventually(lambda:observer.snapshot()['available'])
        assert worker.snapshot()['receipts'][-1]['outcome']=='LATE_OR_CANCELLED_RESULT_DISCARDED'
        assert not admission.status()['native_commands_blocked']
    finally:release.set();worker.close();observer.close()


def test_unknown_accelerator_release_remains_blocked_after_no_active_request():
    observer=SimpleNamespace(_semantic_release_unknown=True)
    worker=EventSemanticWorker(observer,lambda:None,clock_domain='pi',boot_id='software')
    assert worker.snapshot()['active_request_id'] is None and worker.busy()
    vision=SimpleNamespace(worker=worker)
    guard=SourceSemanticAdmission(SimpleNamespace(status=lambda:{'mode':'idle'}),vision)
    with pytest.raises(RuntimeError):
        with guard.control('teleop/start'):raise AssertionError('Unknown release must not permit native entry')


def test_status_under_original_station_lock_does_not_invert_native_admission_lock(tmp_path,monkeypatch):
    station,_,_,_,_,admission,_=rig(tmp_path,monkeypatch)
    owner,control_entered,allow_status,done=threading.Event(),threading.Event(),threading.Event(),threading.Event();results=[]
    def status_owner():
        with station.lock:
            owner.set();assert allow_status.wait(2);results.append(admission.status())
        done.set()
    def native_owner():
        assert owner.wait(1)
        with admission.control('teleop/start'):
            control_entered.set();station.status()
    reader=threading.Thread(target=status_owner,daemon=True);native=threading.Thread(target=native_owner,daemon=True)
    reader.start();assert owner.wait(1);native.start();assert control_entered.wait(1);allow_status.set()
    assert done.wait(1);reader.join(1);native.join(1)
    assert not reader.is_alive() and not native.is_alive() and results[0]['native_mode']=='idle'
