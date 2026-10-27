"""Actual COMMON provider + measured/cache-only endpoint records; no devices."""
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from explorer_shared import station_cooperative_interlock as module
from explorer_shared.contracts import canonical
from explorer_shared.station_cooperative_interlock import StationCooperativeInterlock
from test_station_observed_activation import rig as permission_rig,authorize


def rig(tmp_path):
    provider,station,camera,tick,original=permission_rig()
    station.root=tmp_path/'original-station';station.root.mkdir()
    calibration=station.root/'follower-original.json'
    calibration.write_bytes(canonical({'gripper':{'id':6,'drive_mode':0,'homing_offset':0,'range_min':500,'range_max':3500}}))
    station.calibration=hashlib.sha256(calibration.read_bytes()).hexdigest()
    station.profiles={'follower':{'calibration_sha256':station.calibration,'calibration_path':str(calibration),
        'verified_on_connected_hardware':True}}
    station.measurements={}
    provider.publication_gate=SimpleNamespace(publication_lock=threading.RLock())
    binding={key:original[key] for key in ('skill_id','skill_version','spec_sha256','action_contract_id','source_domain')}
    binding.update(purpose='cooperative05',input_mode='leader',cooperative_session_id='run',coordinator_epoch='coord',object_id='object')
    grant,_=authorize(provider,binding)
    wrapper=StationCooperativeInterlock(provider,station.root/'cooperative-hold.json')
    return wrapper,provider,station,camera,tick,grant,calibration


def observation(station,camera,tick,grant,label,value):
    tick[0]+=10_000_000;frame=camera.publish();frame.jpeg=b'fixture-original-cache-'+label.encode()+str(tick[0]).encode()
    station.measurements={'follower':[1.,2.,3.,4.,5.,value],'monotonic_ns':tick[0],'clock_domain':station.clock_domain}
    return {'grant_id':grant['grant_id'],'expected_control_epoch':grant['control_epoch'],'label':label,
        'frame_id':frame.frame_id,'displayed_jpeg_sha256':hashlib.sha256(frame.jpeg).hexdigest(),
        'observing':True,'empty_gripper_confirmed':True,'user_observed_test_confirmed':True}


def capture(wrapper,body):return wrapper.dispatch('direction/observe',body,authenticated_actor='actual-server-owner')


def test_actual_cached_measured_pair_original_bytes_quantization_only_and_no_native_commands(tmp_path,monkeypatch):
    wrapper,provider,station,camera,tick,grant,calibration=rig(tmp_path)
    opened=capture(wrapper,observation(station,camera,tick,grant,'empty_open',80.))
    assert opened['direction_profile_id'] is None and 'CAPTURES_REQUIRED' in opened['direction_profile_blocker']
    closed=capture(wrapper,observation(station,camera,tick,grant,'empty_closed',20.))
    identity=closed['direction_profile_id'];profile=wrapper.profiles[identity]
    assert profile['closing_sign']==-1 and profile['measurement_uncertainty_native']==pytest.approx(100/3000)
    assert not profile['physical_position_accuracy_known'] and not profile['metric_aperture_or_force_claimed']
    assert profile['uncertainty_basis']=='encoder_quantization_only_not_physical_accuracy'
    for row,key in ((opened,'open'),(closed,'empty_closed')):
        raw=(wrapper.capture_root/(row['capture_id']+'.json')).read_bytes();source=json.loads(raw)
        assert hashlib.sha256(raw).hexdigest()==row['source_sha256']==profile['original_source_sha256'][key]
        assert hashlib.sha256((wrapper.capture_root/(row['capture_id']+'.jpg')).read_bytes()).hexdigest()==row['jpeg_sha256']
        assert source['physical_accuracy_unknown'] and source['source_binding']==grant['binding']
        assert source['capture_cutoff_monotonic_ns']>=source['measurement_stamp']['monotonic_ns']>=source['frame']['monotonic_ns']
    bound=wrapper.dispatch('bind',{'grant_id':grant['grant_id'],'expected_control_epoch':grant['control_epoch'],
        'expected_revision':0,'direction_profile_id':identity},authenticated_actor='actual-server-owner')
    assert bound['capability_ready'] and station.calls==[]
    def prohibited(*args,**kwargs):raise AssertionError('cached publication/status must not read source files or camera')
    monkeypatch.setattr(provider,'check',prohibited);monkeypatch.setattr(camera,'latest',prohibited);monkeypatch.setattr(module,'_read',prohibited)
    assert wrapper.status()['direction_profile_choices'][0]['profile_id']==identity
    assert wrapper.check_publication(station.control_epoch,station.mode,[0.]*6)['allowed']
    assert station.calls==[]


@pytest.mark.parametrize('failure',['missing','foreign_clock','before_frame','future','stale','outside_range','boolean'])
def test_unavailable_or_noncausal_measurement_never_creates_endpoint_original(tmp_path,failure):
    wrapper,provider,station,camera,tick,grant,calibration=rig(tmp_path)
    body=observation(station,camera,tick,grant,'empty_open',80.)
    if failure=='missing':station.measurements={}
    elif failure=='foreign_clock':station.measurements['clock_domain']='pi:other-boot'
    elif failure=='before_frame':station.measurements['monotonic_ns']-=1
    elif failure=='future':station.measurements['monotonic_ns']+=1
    elif failure=='stale':tick[0]+=500_000_001
    elif failure=='outside_range':station.measurements['follower'][5]=101.
    elif failure=='boolean':station.measurements['follower'][5]=True
    with pytest.raises(ValueError,match='fresh same-boot'):capture(wrapper,body)
    assert not wrapper.capture_root.exists() and not wrapper.profile_root.exists() and station.calls==[]


def test_browser_numbers_original_calibration_change_and_false_user_label_rejected(tmp_path):
    wrapper,provider,station,camera,tick,grant,calibration=rig(tmp_path)
    body=observation(station,camera,tick,grant,'empty_open',80.)
    for extra in ({'measurement_uncertainty_native':0},{'measured_native6':[0.]*6},{'empty_gripper_confirmed':False},{'displayed_jpeg_sha256':'f'*64}):
        with pytest.raises(ValueError):capture(wrapper,dict(body,**extra))
    calibration.write_bytes(calibration.read_bytes()+b' ')
    with pytest.raises(ValueError,match='calibration SHA changed'):capture(wrapper,body)
    assert not wrapper.capture_root.exists() and station.calls==[]


def test_retained_open_from_another_boot_is_not_clock_compared_or_paired(tmp_path):
    wrapper,provider,station,camera,tick,grant,calibration=rig(tmp_path)
    opened=capture(wrapper,observation(station,camera,tick,grant,'empty_open',80.))
    path=wrapper.capture_root/(opened['capture_id']+'.json');old=json.loads(path.read_bytes())
    old['measurement_stamp'].update(boot_id='archived-boot',clock_domain='pi:archived-boot',monotonic_ns=10**18)
    old['frame'].update(boot_id='archived-boot',clock_domain='pi:archived-boot',monotonic_ns=10**18-1)
    path.write_bytes(canonical(old));wrapper._load_captures()
    closed=capture(wrapper,observation(station,camera,tick,grant,'empty_closed',20.))
    assert closed['direction_profile_id'] is None and not wrapper.profiles and station.calls==[]
    current=capture(wrapper,observation(station,camera,tick,grant,'empty_open',80.))
    assert wrapper.profiles[current['direction_profile_id']]['closing_sign']==-1 and len(wrapper.captures)==3


@pytest.mark.parametrize('failure',['ambiguous','same_image'])
def test_same_image_or_ambiguous_endpoint_pair_preserves_source_without_claiming_direction(tmp_path,failure):
    wrapper,provider,station,camera,tick,grant,calibration=rig(tmp_path)
    capture(wrapper,observation(station,camera,tick,grant,'empty_open',80.))
    opened_jpeg=camera.current.jpeg
    body=observation(station,camera,tick,grant,'empty_closed',79.99 if failure=='ambiguous' else 20.)
    if failure=='same_image':
        camera.current.jpeg=opened_jpeg;body['displayed_jpeg_sha256']=hashlib.sha256(opened_jpeg).hexdigest()
    closed=capture(wrapper,body)
    assert closed['direction_profile_id'] is None and 'NOT_QUALIFIED' in closed['direction_profile_blocker']
    assert len(wrapper.captures)==2 and not wrapper.profiles and station.calls==[]


def test_bounded_corrupt_source_is_reported_not_turned_into_profile_or_global_write_fault(tmp_path):
    wrapper,provider,station,camera,tick,grant,calibration=rig(tmp_path)
    opened=capture(wrapper,observation(station,camera,tick,grant,'empty_open',80.))
    path=wrapper.capture_root/(opened['capture_id']+'.json');path.write_bytes(b'[]')
    wrapper._load_captures()
    assert opened['capture_id'] in wrapper.status()['direction_capture_errors'] and not wrapper.status()['persistence_fault']
    result=capture(wrapper,observation(station,camera,tick,grant,'empty_closed',20.))
    assert result['direction_profile_id'] is None and station.calls==[]


def test_source_io_occurs_outside_original_publication_locks_and_endpoint_tamper_blocks_bind(tmp_path,monkeypatch):
    wrapper,provider,station,camera,tick,grant,calibration=rig(tmp_path)
    raw,original=module._raw,module._original
    def outside(callback,*args,**kwargs):
        assert not station.lock._is_owned() and not provider.lock._is_owned() and not provider.publication_gate.publication_lock._is_owned()
        return callback(*args,**kwargs)
    monkeypatch.setattr(module,'_raw',lambda *args,**kwargs:outside(raw,*args,**kwargs))
    monkeypatch.setattr(module,'_original',lambda *args,**kwargs:outside(original,*args,**kwargs))
    opened=capture(wrapper,observation(station,camera,tick,grant,'empty_open',80.))
    closed=capture(wrapper,observation(station,camera,tick,grant,'empty_closed',20.))
    profile_id=closed['direction_profile_id']
    assert StationCooperativeInterlock(provider,wrapper.path).status()['direction_profile_choices'][0]['profile_id']==profile_id
    source=wrapper.capture_root/(opened['capture_id']+'.json');source.write_bytes(source.read_bytes()+b' ')
    with pytest.raises(ValueError,match='no longer matches'):wrapper.dispatch('bind',{'grant_id':grant['grant_id'],
        'expected_control_epoch':grant['control_epoch'],'expected_revision':0,'direction_profile_id':profile_id},authenticated_actor='actual-server-owner')
    assert wrapper.status()['all_target_writes_blocked'] and not wrapper.check_publication('legacy','teleoperation',[0.]*6)['allowed']
    assert StationCooperativeInterlock(provider,wrapper.path).status()['all_target_writes_blocked'] and station.calls==[]
