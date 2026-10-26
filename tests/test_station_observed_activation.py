"""Exact cached-display/user/native-epoch fences with fake original station only."""
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import threading
import uuid

import pytest

from explorer_shared.station_observed_activation import StationObservedPermission,CONTRACT


class Camera:
    camera_id='pi-overview';stale_s=2.
    def __init__(self,tick):self.tick=tick;self.frames={};self.current=None;self.reads=0;self.live=True;self.block=None
    def publish(self):
        frame=SimpleNamespace(frame_id=uuid.uuid4().hex,jpeg=b'\xff\xd8cached-fixture-only\xff\xd9',monotonic_ns=self.tick[0],timestamp_basis='cached_receive')
        self.frames[frame.frame_id]=frame;self.current=frame;return frame
    def latest(self):
        self.reads+=1
        if self.block and threading.current_thread().name=='old-authorize':self.block[0].set();assert self.block[1].wait(2)
        return self.current if self.live else None
    def frame_by_id(self,identity):return self.frames.get(identity)


class Station:
    boot='actual-fixture-boot';clock_domain='pi:actual-fixture-boot'
    def __init__(self):
        self.lock=threading.RLock();self.control_epoch='native-epoch0';self.mode='idle';self.calls=[];self.status_calls=0
        self.available=True;self.calibration='a'*64;self.block=None
    def status(self):
        self.status_calls+=1
        return {'available':self.available,'mode':self.mode,'control_epoch':self.control_epoch,
            'roles':{'follower':{'verified_on_connected_hardware':True,'calibration_sha256':self.calibration}},'last_command':None}
    def command(self,operation,body):
        self.calls.append((operation,deepcopy(body)))
        if self.block and operation=='policy/start':self.block[0].set();assert self.block[1].wait(2)
        if operation in ('policy/start','teleop/start','teleop/recenter','stop'):self.control_epoch='native-'+uuid.uuid4().hex
        self.mode='idle' if operation=='stop' else 'policy' if operation in ('policy/start','action') else 'teleoperation'
        return self.status()


def rig():
    tick=[10_000_000_000];station=Station();camera=Camera(tick)
    provider=StationObservedPermission(station,camera,clock=lambda:tick[0])
    assert camera.reads==station.status_calls==0  # constructor has no source IO.
    camera.publish()
    selected={'purpose':'source04_eval','evaluation_id':uuid.uuid4().hex,'model_job_id':'b'*32,'skill_id':str(uuid.uuid4()),'skill_version':1,
        'spec_sha256':'c'*64,'checkpoint_content_sha256':'d'*64,'action_contract_id':CONTRACT,'source_domain':'PHYSICAL'}
    return provider,station,camera,tick,selected


def authorize(provider,selected):
    challenge=provider.challenge(selected)
    raw,metadata=provider.frame(challenge['challenge_id'])
    assert hashlib.sha256(raw).hexdigest()==metadata['jpeg_sha256']
    body={'binding':selected,'challenge_id':challenge['challenge_id'],'displayed_jpeg_sha256':metadata['jpeg_sha256'],
        'expected_control_epoch':challenge['control_epoch'],'observing':True,'robot_workspace_checked':True,
        'user_observed_test_confirmed':True,'actor_id':'actual-server-owner'}
    return provider.authorize(body),body


def test_metadata_authorization_is_exact_displayed_frame_and_never_native_command():
    provider,station,camera,tick,selected=rig();challenge=provider.challenge(selected)
    with pytest.raises(ValueError):provider.authorize({'binding':selected,'actor_id':'owner'})
    grant,body=authorize(provider,selected)
    assert grant['control_epoch']==station.control_epoch and grant['binding']==selected
    assert provider.check(grant['grant_id'],selected)['execution_permission'] and station.calls==[]
    assert provider.status()['permission_is_not_grasp_stationarity_or_physical_success']
    with pytest.raises(ValueError,match='binding'):
        provider.check(grant['grant_id'],dict(selected,evaluation_id=uuid.uuid4().hex))
    assert station.calls==[] and provider.status()['grant']['grant_id']==grant['grant_id']


def test_native_start_rotates_grant_epoch_and_old_stop_or_revoke_never_clears_current_epoch():
    provider,station,camera,tick,selected=rig();grant,_=authorize(provider,selected);old=grant['control_epoch']
    reply=provider.command(grant['grant_id'],selected,'policy/start',{'expected_control_epoch':old})
    current=reply['grant'];assert current['control_epoch']==reply['status']['control_epoch']!=old
    with pytest.raises(ValueError,match='Old request'):provider.check(grant['grant_id'],selected,old)
    with pytest.raises(ValueError,match='Old STOP'):provider.command(grant['grant_id'],selected,'stop',{'expected_control_epoch':old})
    revoked=provider.revoke('OLD_REPLY',expected_grant_id=grant['grant_id'],expected_control_epoch=old,expected_binding=selected)
    assert not revoked['revoked'] and provider.status()['execution_permission'] and len(station.calls)==1
    stopped=provider.command(current['grant_id'],selected,'stop',{'expected_control_epoch':current['control_epoch']})
    assert stopped['status']['mode']=='idle' and not stopped['grant']['execution_permission']


def test_fresh_camera_and_sticky_boolean_do_not_renew_user_observation_or_revive_expired_grant():
    provider,station,camera,tick,selected=rig();grant,_=authorize(provider,selected)
    with pytest.raises(ValueError,match='sticky'):provider.observe({'grant_id':grant['grant_id'],'binding':selected,'observing':True})
    tick[0]+=2_100_000_000;frame=camera.publish()
    with pytest.raises(ValueError,match='expired'):provider.check(grant['grant_id'],selected)
    with pytest.raises(ValueError):provider.observe({'grant_id':grant['grant_id'],'binding':selected,'observing':True,
        'frame_id':frame.frame_id,'displayed_jpeg_sha256':hashlib.sha256(frame.jpeg).hexdigest(),
        'expected_control_epoch':grant['control_epoch'],'actor_id':'actual-server-owner'})
    fresh,_=authorize(provider,selected)
    assert fresh['grant_id']!=grant['grant_id'] and not station.calls


def test_explicit_new_displayed_frame_can_renew_current_grant_with_same_actor_only():
    provider,station,camera,tick,selected=rig();grant,_=authorize(provider,selected);tick[0]+=1_000_000_000;frame=camera.publish()
    body={'grant_id':grant['grant_id'],'binding':selected,'observing':True,'frame_id':frame.frame_id,
        'displayed_jpeg_sha256':hashlib.sha256(frame.jpeg).hexdigest(),'expected_control_epoch':grant['control_epoch'],'actor_id':'other-actor'}
    with pytest.raises(ValueError,match='observer identity'):provider.observe(body)
    body['actor_id']='actual-server-owner';renewed=provider.observe(body)
    assert renewed['observation_deadline_ns']==frame.monotonic_ns+int(camera.stale_s*1e9) and not station.calls


def test_late_authorize_and_frame_after_revoke_cannot_replace_new_user_grant():
    provider,station,camera,tick,selected=rig();challenge=provider.challenge(selected);entered,release=threading.Event(),threading.Event();camera.block=(entered,release)
    body={'binding':selected,'challenge_id':challenge['challenge_id'],'displayed_jpeg_sha256':challenge['frame']['jpeg_sha256'],
        'expected_control_epoch':challenge['control_epoch'],'observing':True,'robot_workspace_checked':True,
        'user_observed_test_confirmed':True,'actor_id':'actual-server-owner'}
    errors=[]
    def old():
        try:provider.authorize(body)
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=old,name='old-authorize');thread.start();assert entered.wait(1)
    provider.revoke('NAVIGATION_DROP');new,_=authorize(provider,dict(selected,checkpoint_content_sha256='e'*64))
    release.set();thread.join(2)
    assert errors and provider.status()['grant']['grant_id']==new['grant_id'] and not station.calls
    with pytest.raises(ValueError):provider.frame(challenge['challenge_id'])
    assert not provider.revoke('OLD_CLEANUP',expected_grant_id='0'*32)['revoked']
    assert provider.status()['execution_permission']


def test_revoke_during_bounded_native_start_cleans_only_its_new_epoch_and_cannot_latch_permission():
    provider,station,camera,tick,selected=rig();grant,_=authorize(provider,selected)
    entered,release=threading.Event(),threading.Event();station.block=(entered,release);errors=[]
    def start():
        try:provider.command(grant['grant_id'],selected,'policy/start',{'expected_control_epoch':grant['control_epoch']})
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=start);thread.start();assert entered.wait(1)
    revoked=threading.Thread(target=lambda:provider.revoke('STOP',expected_grant_id=grant['grant_id']));revoked.start()
    assert provider.grant['revoked'].wait(1);release.set();thread.join(2);revoked.join(2)
    assert errors and [row[0] for row in station.calls]==['policy/start','stop'] and station.mode=='idle'
    assert not provider.status()['execution_permission']
    station.block=None;new,_=authorize(provider,selected)
    with pytest.raises(ValueError):provider.command(grant['grant_id'],selected,'stop',{'expected_control_epoch':grant['control_epoch']})
    assert provider.status()['grant']['grant_id']==new['grant_id'] and len(station.calls)==2


def test_camera_loss_revokes_but_exact_owned_expired_stop_remains_available_and_cannot_stop_foreign_epoch():
    provider,station,camera,tick,selected=rig();grant,_=authorize(provider,selected)
    current=provider.command(grant['grant_id'],selected,'policy/start',{'expected_control_epoch':grant['control_epoch']})['grant']
    camera.live=False
    with pytest.raises(ValueError,match='camera'):provider.check(grant['grant_id'],selected)
    result=provider.command(current['grant_id'],selected,'stop',{'expected_control_epoch':current['control_epoch']})
    assert result['status']['mode']=='idle' and len(station.calls)==2
    camera.live=True;new,_=authorize(provider,selected);station.control_epoch='unrelated-native-owner'
    with pytest.raises(ValueError):provider.check(new['grant_id'],selected)
    with pytest.raises(ValueError):provider.command(new['grant_id'],selected,'stop',{'expected_control_epoch':new['control_epoch']})
    assert len(station.calls)==2


def test_unknown_original_return_revokes_and_scoped_cleanup_never_claims_dispatch():
    provider,station,camera,tick,selected=rig();grant,_=authorize(provider,selected);original=station.command
    def failed(operation,body):
        result=original(operation,body)
        if operation=='policy/start':raise OSError('Original endpoint returned unknown after begin')
        return result
    station.command=failed
    with pytest.raises(OSError):provider.command(grant['grant_id'],selected,'policy/start',{'expected_control_epoch':grant['control_epoch']})
    assert [item[0] for item in station.calls]==['policy/start','stop'] and station.mode=='idle'
    assert not provider.status()['execution_permission']
