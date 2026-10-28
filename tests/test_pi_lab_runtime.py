"""Existing original03 source service + fake target; no devices/network."""
import pytest

from explorer_shared.pi_lab_runtime import LabRetargetRuntime
from explorer_shared.pi_native_retarget import PiNativeRetargetService
from test_pi_native_retarget import Geometry,Station,Peer


@pytest.mark.parametrize('operation',['start','recenter','lease','activation/challenge','activation/authorize'])
def test_manual_control_delegates_to_existing_owner_without_research_gate(operation):
    from types import SimpleNamespace
    calls=[];recorder=object()
    service=SimpleNamespace(recorder=recorder,command=lambda operation,body:calls.append((operation,body)) or {'existing_owner':True})
    lab=LabRetargetRuntime(service)
    assert lab.command(operation,{'user_button':True})=={'existing_owner':True}
    assert calls==[(operation,{'user_button':True})] and lab.recorder is recorder
    assert lab.physical_admission()['allowed'] and not lab.physical_admission()['physical_commands_authorized']


def test_original_status_and_idle_worker_close_remain_peer_IO_free(tmp_path):
    peer=Peer();service=PiNativeRetargetService(Geometry(),Station(),tmp_path,client=peer)
    lab=LabRetargetRuntime(service);lab.start_worker()
    try:
        for _ in range(5):
            status=lab.status();assert status['peer_configured'] and status['runtime_profile']=='so101_lab'
            assert status['state']=='IDLE' and status['physical03_commands_enabled']
    finally:lab.close()
    assert peer.calls==[] and not service.thread.is_alive()


def test_scoped_stop_keeps_original_epoch_matching_and_does_not_borrow_a_polled_owner(tmp_path):
    peer=Peer();service=PiNativeRetargetService(Geometry(),Station(),tmp_path,client=peer);lab=LabRetargetRuntime(service)
    with pytest.raises(ValueError):lab.command('stop',{'control_epoch':'foreign'})
    assert peer.calls==[]
    # An idle explicit global cleanup has no retained native epoch to stop.
    stopped=lab.stop();assert not stopped['native_STOP_delivered']
    assert peer.calls==[] and service.target_epoch is None


def test_explicit_owned_stop_retains_original_native_epoch_and_never_claims_hardware_hold(tmp_path):
    peer=Peer();service=PiNativeRetargetService(Geometry(),Station(),tmp_path,client=peer);lab=LabRetargetRuntime(service)
    service.target_epoch=peer.epoch='SOFTWARE-OWNED-TARGET';peer.active=True;service.mode='ACTIVE'
    result=lab.stop()
    assert peer.calls==[('stop','SOFTWARE-OWNED-TARGET')]
    assert result['native_STOP_delivered'] and result['last_stop_receipt']['hardware_stop_verified'] is False


def test_data_capture_keeps_the_same_existing_nano_common_producer(tmp_path):
    peer=Peer();service=PiNativeRetargetService(Geometry(),Station(),tmp_path,client=peer);lab=LabRetargetRuntime(service)
    context={'project_id':'03','fixture':'NO_PHYSICAL_DEMO'}
    result=lab.command('record/start',{'skill_context':context})
    assert peer.calls==[('record/start',context)] and result['owner']=='nano'
    assert lab.recorder is service.recorder and lab.recorder.status()==result
