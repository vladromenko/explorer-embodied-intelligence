"""Characterize the actual parked-BEGIN race with ORIGINAL worker/fake buses.

This negative proof must be superseded by a separate atomic phase-activation
route; it does not permit bypassing the durable latch or extend a goal/lease.
"""
from copy import deepcopy

from test_station_hold_successor import rig,invoke


def test_separate_native_begin_then_metadata_link_is_too_late_for_original_worker(tmp_path,monkeypatch):
    wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=rig(tmp_path,monkeypatch)
    invoke(wrapper,body);parked=wrapper.helper.snapshot_state();old_receipt=deepcopy(station.last_command)
    # Characterize the pre-fix boundary only. The new admission callback blocks
    # this unsafe separate route before original start; positive route tests
    # use the real callback without any bypass.
    monkeypatch.setattr(wrapper,'require_start_admission',lambda *args:None)
    # Only fake-native USER start. The provider legitimately rotates its grant
    # epoch, while a separate later metadata request has not linked that epoch.
    reply=provider.command(fresh['grant_id'],binding,'teleop/start',{'expected_control_epoch':fresh['control_epoch']})
    started=reply['grant'];assert started['control_epoch']!=parked['owner']['control_epoch']
    before=deepcopy(bus.writes);station.stop_event=StopAfter();station._work()
    assert station.error is None and station.mode=='idle' and station.control_epoch!=started['control_epoch']
    assert wrapper.helper.snapshot_state()==parked and wrapper.helper.armed
    assert station.last_command==old_receipt  # No target return or false new-epoch receipt.
    assert len(bus.writes)==len(before)+1  # Genuine ORIGINAL measured-position denial hold.
    hold=provider.publication_gate.last_measured_hold
    assert hold['denied_native_epoch']==started['control_epoch'] and hold['original_position_write_returned']
    assert hold['old_target_sample_emitted'] is False
