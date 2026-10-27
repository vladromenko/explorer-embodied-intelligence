"""Real COMMON grant + temp WAL + fake original owner; zero hardware I/O."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from explorer_shared import station_cooperative_interlock as module
from explorer_shared.station_cooperative_interlock import StationCooperativeInterlock
from explorer_shared.contracts import canonical,digest
from explorer_shared.cooperative_receiver_interlock05 import observed_gripper_direction
from test_station_observed_activation import rig as permission_rig,authorize
from test_cooperative_receiver_interlock05 import endpoint


def rig(tmp_path):
    provider,station,camera,tick,old=permission_rig()
    station.root=tmp_path/'original-station';station.root.mkdir();station.profiles={'follower':{
        'calibration_sha256':station.calibration,'verified_on_connected_hardware':True}}
    provider.publication_gate=SimpleNamespace(publication_lock=threading.RLock())
    binding={key:old[key] for key in ('skill_id','skill_version','spec_sha256','action_contract_id','source_domain')}
    binding.update(purpose='cooperative05',input_mode='leader',cooperative_session_id='run',coordinator_epoch='coord',object_id='object')
    grant,_=authorize(provider,binding)
    grant=provider.command(grant['grant_id'],binding,'teleop/start',{'expected_control_epoch':grant['control_epoch']})['grant']
    station.last_command={'control_epoch':station.control_epoch,'sequence':1,'requested':[1.,2.,3.,4.,5.,60.],
        'dispatched':[1.,2.,3.,4.,5.,60.],'monotonic_ns':tick[0]-1,'clock_domain':station.clock_domain}
    profile=observed_gripper_direction(endpoint('empty_open',80.),endpoint('empty_closed',20.))
    folder=station.root/'cooperative-direction-profiles';folder.mkdir()
    (folder/(profile['profile_sha256']+'.json')).write_bytes(canonical(profile))
    wrapper=StationCooperativeInterlock(provider,station.root/'cooperative-hold.json')
    return wrapper,provider,station,camera,tick,grant,profile


def base(grant):return {'grant_id':grant['grant_id'],'expected_control_epoch':grant['control_epoch']}
def call(wrapper,operation,body):return wrapper.dispatch(operation,body,authenticated_actor='actual-server-owner')


def bind_arm(wrapper,grant,profile):
    bound=call(wrapper,'bind',{**base(grant),'expected_revision':0,'direction_profile_id':profile['profile_sha256']})
    return call(wrapper,'arm',{**base(grant),'expected_revision':bound['revision'],'interlock_id':bound['interlock_id']})


def test_real_grant_id_only_bind_arm_actual_cached_write_and_cached_hook_no_camera_rpc_or_file(tmp_path,monkeypatch):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path);calls=list(station.calls)
    armed=bind_arm(wrapper,grant,profile)
    saved=json.loads(wrapper.path.read_bytes())
    assert saved['pending'] is None and saved['state']['armed'] and armed['binding_current']
    assert saved['envelope_sha256']==digest({key:value for key,value in saved.items() if key!='envelope_sha256'})
    def prohibited(*args,**kwargs):raise AssertionError('No camera/RPC/file read belongs to publication admission/status')
    monkeypatch.setattr(camera,'latest',prohibited);monkeypatch.setattr(provider,'check',prohibited);monkeypatch.setattr(module,'_read',prohibited)
    assert wrapper.status()['capability_ready']
    assert wrapper.check_publication(station.control_epoch,station.mode,[1.,2.,3.,4.,5.,50.])['allowed']
    assert not wrapper.check_publication(station.control_epoch,station.mode,[2.,2.,3.,4.,5.,50.])['allowed']
    assert not wrapper.check_publication('new-legacy','teleoperation',[0.]*6)['allowed']
    assert station.calls==calls


def test_pending_or_corrupt_wal_never_recovers_fresh_unarmed_and_status_remains_readable(tmp_path):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path);bind_arm(wrapper,grant,profile)
    original=json.loads(wrapper.path.read_bytes())
    for raw in (b'{corrupt',canonical(dict(original,pending={'transaction_id':'lost-fsync'},
            envelope_sha256=digest({key:value for key,value in dict(original,pending={'transaction_id':'lost-fsync'}).items() if key!='envelope_sha256'})))):
        wrapper.path.write_bytes(raw);recovered=StationCooperativeInterlock(provider,wrapper.path)
        assert recovered.status()['armed'] and recovered.status()['all_target_writes_blocked']
        assert not recovered.check_publication('any-unowned','policy',[0.]*6)['allowed']
        assert recovered.path.read_bytes()==raw


def test_lost_wal_after_initialized_arm_never_becomes_first_install_unarmed(tmp_path):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path);bind_arm(wrapper,grant,profile)
    marker=wrapper.initialized_path.read_bytes();wrapper.path.unlink()
    recovered=StationCooperativeInterlock(provider,wrapper.path)
    assert recovered.status()['armed'] and recovered.status()['all_target_writes_blocked']
    assert not recovered.check_publication('unowned','teleoperation',[0.]*6)['allowed']
    assert recovered.initialized_path.read_bytes()==marker and not recovered.path.exists()
    provider.command(grant['grant_id'],grant['binding'],'stop',{'expected_control_epoch':grant['control_epoch']})
    fresh,_=authorize(provider,grant['binding']);fault=recovered.status()
    call(recovered,'reset_safe',{**base(fresh),'interlock_id':fault['interlock_id'],'expected_revision':fault['revision'],
        'object_safe_no_dual_hold':True})
    assert not recovered.status()['armed'] and recovered.initialized_path.read_bytes()==marker and recovered.path.exists()


def test_durable_marker_precedes_first_wal_and_is_never_used_as_safety_proof(tmp_path,monkeypatch):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path);writes=[];original=module._write
    def observe(path,value):
        assert wrapper.initialized_path.exists();writes.append(value)
        return original(path,value)
    monkeypatch.setattr(module,'_write',observe)
    call(wrapper,'bind',{**base(grant),'expected_revision':0,'direction_profile_id':profile['profile_sha256']})
    assert len(writes)==2 and writes[0]['pending'] and not writes[1]['pending']
    wrapper.initialized_path.write_bytes(b'corrupt')
    recovered=StationCooperativeInterlock(provider,wrapper.path)
    assert recovered.status()['all_target_writes_blocked'] and not recovered.check_publication('any','policy',[0.]*6)['allowed']


def test_first_wal_write_failure_keeps_marker_and_restart_requires_user_safe_reset(tmp_path,monkeypatch):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path)
    def fail(*args,**kwargs):raise OSError('first WAL write failed before replace')
    monkeypatch.setattr(module,'_write',fail)
    with pytest.raises(OSError,match='first WAL'):call(wrapper,'bind',{**base(grant),'expected_revision':0,'direction_profile_id':profile['profile_sha256']})
    assert wrapper.initialized_path.exists() and not wrapper.path.exists()
    recovered=StationCooperativeInterlock(provider,wrapper.path)
    assert recovered.status()['all_target_writes_blocked'] and not recovered.check_publication('legacy','teleoperation',[0.]*6)['allowed']


def test_marker_corruption_repair_is_only_after_pending_wal_and_explicit_user_safe_reset(tmp_path,monkeypatch):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path);bind_arm(wrapper,grant,profile)
    marker=wrapper.initialized_path.read_bytes();wrapper.initialized_path.write_bytes(b'corrupt')
    recovered=StationCooperativeInterlock(provider,wrapper.path);fault=recovered.status()
    provider.command(grant['grant_id'],grant['binding'],'stop',{'expected_control_epoch':grant['control_epoch']})
    fresh,_=authorize(provider,grant['binding']);original=module._write;order=[]
    def observed(path,value):
        order.append(path)
        if path==recovered.initialized_path:
            assert json.loads(recovered.path.read_bytes())['pending'] is not None
        return original(path,value)
    monkeypatch.setattr(module,'_write',observed)
    call(recovered,'reset_safe',{**base(fresh),'interlock_id':fault['interlock_id'],'expected_revision':fault['revision'],
        'object_safe_no_dual_hold':True})
    assert order==[recovered.path,recovered.initialized_path,recovered.path]
    assert recovered.initialized_path.read_bytes()==marker and not recovered.status()['all_target_writes_blocked']
    assert not StationCooperativeInterlock(provider,recovered.path).status()['armed']


def test_final_fsync_failure_leaves_runtime_and_restart_global_latch_closed(tmp_path,monkeypatch):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path)
    bound=call(wrapper,'bind',{**base(grant),'expected_revision':0,'direction_profile_id':profile['profile_sha256']})
    original=module._write;writes=[]
    def failed(path,value):
        writes.append(value)
        if len(writes)==2:raise OSError('explicit fake final-fsync failure')
        return original(path,value)
    monkeypatch.setattr(module,'_write',failed)
    with pytest.raises(OSError,match='fake final-fsync'):call(wrapper,'arm',{**base(grant),'expected_revision':bound['revision'],'interlock_id':bound['interlock_id']})
    assert wrapper.status()['all_target_writes_blocked'] and wrapper.helper.status()['armed']
    assert json.loads(wrapper.path.read_bytes())['pending'] is not None
    recovered=StationCooperativeInterlock(provider,wrapper.path)
    assert recovered.status()['armed'] and not recovered.check_publication('new','policy',[0.]*6)['allowed']


def test_rejected_old_revision_seals_unchanged_state_and_does_not_fault_or_replace_reference(tmp_path):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path);armed=bind_arm(wrapper,grant,profile)
    before=wrapper.helper.snapshot_state()
    with pytest.raises(ValueError,match='ID/revision'):call(wrapper,'arm',{**base(grant),'interlock_id':armed['interlock_id'],'expected_revision':0})
    assert wrapper.helper.snapshot_state()==before and not wrapper.status()['persistence_fault']
    assert json.loads(wrapper.path.read_bytes())['pending'] is None


def test_fault_reset_requires_fresh_actual_user_idle_and_exact_fault_id_revision(tmp_path):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path)
    wrapper.path.write_bytes(b'corrupt');wrapper=StationCooperativeInterlock(provider,wrapper.path);fault=wrapper.status()
    provider.command(grant['grant_id'],grant['binding'],'stop',{'expected_control_epoch':grant['control_epoch']})
    new,_=authorize(provider,grant['binding'])
    body={**base(new),'interlock_id':fault['interlock_id'],'expected_revision':fault['revision'],'object_safe_no_dual_hold':True}
    with pytest.raises(ValueError):call(wrapper,'reset-safe',{**body,'object_safe_no_dual_hold':False})
    result=call(wrapper,'reset-safe',body)
    assert result['phase']=='UNBOUND' and not wrapper.status()['persistence_fault']
    assert not wrapper.status()['armed'] and not json.loads(wrapper.path.read_bytes())['pending']
    with pytest.raises(ValueError):call(wrapper,'reset_safe',body)


def test_browser_native_values_or_changed_grant_never_enter_wal_or_metadata_mutation(tmp_path):
    wrapper,provider,station,camera,tick,grant,profile=rig(tmp_path)
    with pytest.raises(ValueError,match='ID-only'):call(wrapper,'bind',{**base(grant),'expected_revision':0,'direction_profile':profile,'native_position':0})
    assert not wrapper.path.exists()
    with pytest.raises(ValueError,match='current USER'):call(wrapper,'bind',{**base(grant),'expected_revision':0,'grant_id':'different'})
    assert not wrapper.path.exists()
    with pytest.raises(ValueError,match='current USER'):wrapper.dispatch('bind',{**base(grant),'expected_revision':0,'actor_id':'forged'})
    assert not wrapper.path.exists() and wrapper.helper.status()['phase']=='UNBOUND'
