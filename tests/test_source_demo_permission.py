"""Demonstration grants use actual frozen Station methods with fake buses only."""
from copy import deepcopy
import uuid

import pytest

from explorer_shared.station_observed_activation import binding, CONTRACT
from test_station_publication_gate import worker_rig


def selected():
    return dict(purpose='source04_demo',input_mode='leader',source_domain='PHYSICAL',
        skill_id=str(uuid.uuid4()),skill_version=1,spec_sha256='d'*64,action_contract_id=CONTRACT,
        source_session_id=uuid.uuid4().hex,demonstration_id=uuid.uuid4().hex)


def demo_rig(tmp_path,monkeypatch):
    rig=worker_rig(tmp_path,monkeypatch,binding_override=selected())
    station,provider,choice,start,bus,samples,tick,StopAfter,active=rig
    station.profiles['leader']=dict(verified_on_connected_hardware=True,calibration_sha256='c'*64)
    station.measurements={'monotonic_ns':tick[0],'leader':[0.,0.,0.,0.,0.,50.],'clock_domain':station.clock_domain}
    return rig


def test_demo_binding_has_real_attempt_and_no_invented_policy_identity():
    value=selected();assert binding(value)==value
    for patch in ({'model_job_id':'b'*32},{'checkpoint_content_sha256':'b'*64},{'source_domain':'SIMULATION'},
                  {'input_mode':'policy'},{'demonstration_id':'browser-text'},{'source_session_id':None}):
        with pytest.raises(ValueError):binding(dict(value,**patch))
    with pytest.raises(ValueError):binding(dict(value,purpose='cooperative05'))


def test_demonstration_uses_original_steady_relative_targets_without_new_loop_or_policy(tmp_path,monkeypatch):
    station,provider,choice,start,bus,samples,tick,StopAfter,_=demo_rig(tmp_path,monkeypatch)
    grant=start();assert grant['leader_calibration_sha256']=='c'*64
    assert 'model_job_id' not in grant['binding'] and grant['binding']==choice
    station.stop_event=StopAfter();station._work()
    assert station.error is None and station.mode=='teleoperation' and len(samples)==len(bus.writes)==3
    assert all(list(row[1].values())==[10.,0.,0.,0.,0.,50.] for row in bus.writes)
    assert station.control_epoch==grant['control_epoch']


@pytest.mark.parametrize('bad',['unverified','missing','stale','future','invalid_normalized'])
def test_demo_acquire_requires_existing_verified_and_fresh_leader_cache(tmp_path,monkeypatch,bad):
    station,provider,choice,_,bus,_,tick,_,_=demo_rig(tmp_path,monkeypatch)
    if bad=='unverified':station.profiles['leader']['verified_on_connected_hardware']=False
    elif bad=='missing':station.measurements={}
    elif bad=='stale':station.measurements['monotonic_ns']=tick[0]-3_000_000_000
    elif bad=='future':station.measurements['monotonic_ns']=tick[0]+1
    else:station.measurements['leader'][5]=100.001
    with pytest.raises(ValueError,match='leader'):provider.challenge(choice)
    assert not bus.writes


def test_demo_grant_cannot_publish_after_leader_calibration_changes_or_authorize_policy(tmp_path,monkeypatch):
    station,provider,choice,start,bus,samples,tick,StopAfter,_=demo_rig(tmp_path,monkeypatch)
    grant=start();old=station.control_epoch
    for operation in ('policy/start','action'):
        with pytest.raises(ValueError,match='binding'):provider.command(grant['grant_id'],choice,operation,{'expected_control_epoch':old})
    assert bus.writes==[]
    station.profiles['leader']['calibration_sha256']='e'*64
    station.stop_event=StopAfter();station._work()
    assert station.mode=='idle' and not samples and len(bus.writes)==1
    assert list(bus.writes[0][1].values())==[0.,0.,0.,0.,0.,50.]
    assert not provider.status()['execution_permission']


def test_foreign_demo_attempt_and_old_recenter_epoch_cannot_clean_up_another_owner(tmp_path,monkeypatch):
    station,provider,choice,start,bus,_,_,_,_=demo_rig(tmp_path,monkeypatch)
    grant=start();old=grant['control_epoch']
    recentered=provider.command(grant['grant_id'],choice,'teleop/recenter',{'expected_control_epoch':old})['grant']
    assert recentered['control_epoch']!=old
    foreign=dict(choice,demonstration_id=uuid.uuid4().hex)
    with pytest.raises(ValueError):provider.command(grant['grant_id'],foreign,'stop',{'expected_control_epoch':recentered['control_epoch']})
    with pytest.raises(ValueError,match='Old STOP'):provider.command(grant['grant_id'],choice,'stop',{'expected_control_epoch':old})
    assert not provider.revoke('FOREIGN_TAB',expected_grant_id=grant['grant_id'],expected_control_epoch=old,expected_binding=choice)['revoked']
    assert provider.status()['execution_permission'] and not bus.writes
