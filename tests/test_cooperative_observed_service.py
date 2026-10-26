"""Actual temp workflow + native FastAPI gate + common Pi provider; zero motors."""
from copy import deepcopy
import hashlib
import threading
import uuid

import pytest

from test_cooperative_service import fixture as central_fixture,session
from test_native_activation import fixture as native_fixture,TOKEN
from test_station_observed_activation import rig,authorize as pi_authorize


class Pair:
    def __init__(self,tmp_path,helper):
        (tmp_path/'native').mkdir()
        self.client,self.gate,self.mux,self.teleop,self.arm,self.native_tick,self.jpeg=native_fixture(tmp_path/'native')
        self.provider,self.station,self.camera,self.pi_tick,_=rig();self.calls=[];self.pins={};self.block=None
        self.helper=helper
        helper.requests={'giver':self.giver,'receiver':self.receiver};helper.binary=self.binary
    def giver(self,method,path,body):
        self.calls.append(('giver',method,path,deepcopy(body)))
        assert path.startswith('/legacy/explorer/retarget/')
        endpoint='/api/'+path.removeprefix('/legacy/')
        response=self.client.request(method,endpoint,headers={'Authorization':'Bearer '+TOKEN},json=body)
        if response.status_code!=200:raise ValueError(response.text)
        return response.json()
    def receiver(self,method,path,body):
        self.calls.append(('receiver',method,path,deepcopy(body)))
        if method=='GET' and path=='/station/observation':
            frame=self.camera.latest();identity=uuid.uuid4().hex
            value={'observation_id':identity,'frame_id':frame.frame_id,'frame_sha256':hashlib.sha256(frame.jpeg).hexdigest(),
                'frame_boot_id':self.station.boot,'frame_clock_domain':self.station.clock_domain,
                'follower_calibration_sha256':self.station.calibration}
            self.pins[identity]=(frame,deepcopy(value))
            return {'schema':'explorer_so101_cached_observation_v1','observation':value}
        assert path.startswith('/station/observed-test/')
        operation=path.rsplit('/',1)[-1]
        if method=='GET':assert operation=='status';return self.provider.status()
        if operation=='challenge':return self.provider.challenge(body['binding'])
        if operation=='authorize':
            result=self.provider.authorize(body)
            if self.block:self.block[0].set();assert self.block[1].wait(3)
            return result
        if operation=='observe':return self.provider.observe(body)
        if operation=='check':return self.provider.check(body['grant_id'],body['binding'],body.get('expected_control_epoch'))
        if operation=='revoke':return self.provider.revoke(body['reason'],expected_grant_id=body['expected_grant_id'],
            expected_control_epoch=body['expected_control_epoch'],expected_binding=body['binding'])
        raise AssertionError('No native command belongs to metadata setup: '+path)
    def binary(self,role,path):
        self.calls.append((role,'GET',path,None))
        if role=='giver':
            response=self.client.get('/api/explorer/retarget/activation/frame/'+path.rsplit('/',1)[-1],
                headers={'Authorization':'Bearer '+TOKEN});assert response.status_code==200
            return response.content,dict(response.headers)
        if '/challenges/' in path:
            raw,metadata=self.provider.frame(path.split('/')[-2]);frame_id=metadata['frame_id'];boot=metadata['boot_id']
        else:
            frame,metadata=self.pins[path.split('/')[-2]];raw=frame.jpeg;frame_id=frame.frame_id;boot=metadata['frame_boot_id']
        return raw,{'X-JPEG-SHA256':hashlib.sha256(raw).hexdigest(),'X-Frame-ID':frame_id,'X-Boot-ID':boot}
    def no_native_commands(self):
        assert self.station.calls==self.arm.calls==self.arm.publications==self.teleop.native_drive==[]
        assert all('/activation/' in path or '/observed-test/' in path or path.endswith('/status') or
            path.startswith('/station/observation') or path.startswith('/native-activation/frame/') for _,_,path,_ in self.calls)
    def close(self):self.client.close()


def configured(tmp_path):
    service,store,calls,active,contexts,client=central_fixture(tmp_path)
    row,spec=session(service);pair=Pair(tmp_path,service.observed_setup)
    return service,row,spec,pair,client


def command(service,row,op,**body):
    return service.command(op,{'session_id':row['session_id'],'expected_epoch':row['epoch'],**body},
        authenticated_actor='trusted-owner')['result']


def grant(service,row,pair,role):
    setup=command(service,row,'observed_refresh')
    challenge=command(service,row,'observed_challenge',source_setup_choice_id=setup['setup_choices'][role]['choice_id'])
    with pytest.raises(ValueError,match='Load the exact'):
        command(service,row,'observed_authorize',challenge_choice_id=challenge['challenge_choice_id'],
            displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'])
    raw,_=service.observed_frame(challenge['challenge_choice_id'],authenticated_actor='trusted-owner')
    assert hashlib.sha256(raw).hexdigest()==challenge['frame']['jpeg_sha256']
    result=command(service,row,'observed_authorize',challenge_choice_id=challenge['challenge_choice_id'],
        displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],observing=True,neutral=True,space_clear=True,
        robot_workspace_checked=True,user_observed_test_confirmed=True,actor_id='forged-client')
    assert result['native_permission_receipt']['execution_permission']
    return result['native_permission_receipt']


def test_metadata_intent_exact_original_frames_grants_and_binding_never_start_control_or_load_models(tmp_path):
    service,row,spec,pair,client=configured(tmp_path)
    try:
        snapshot=service.snapshot(row['session_id']);assert not snapshot['execution_authority']['explicit_user_observed_test_current']
        assert pair.calls==[] and snapshot['observed_setup']['metadata_intent_only']
        giver=grant(service,row,pair,'giver');receiver=grant(service,row,pair,'receiver')
        assert giver['authorization_receipt']['actor_id']==receiver['actor_id']=='trusted-owner'
        setup=command(service,row,'observed_refresh');assert setup['bind_observed_test_available']
        selected=command(service,row,'bind_observed_test',observed_choice_id=setup['bind_choice_id'])
        assert selected['epoch']==row['epoch'] and selected['execution_domain']=='user_observed_physical_test'
        assert selected['observed_test_binding']['metadata_binding_only'] and not service.transports
        assert not service.snapshot(row['session_id'])['execution_authority']['explicit_user_observed_test_current']
        assert not service.warm_compute.snapshots(selected)
        pair.no_native_commands()
    finally:service.close();pair.close();client.close()


def test_wrong_operator_hash_prior_recording_shadow_and_changed_native_grant_never_bind(tmp_path):
    service,row,spec,pair,client=configured(tmp_path)
    try:
        grant(service,row,pair,'giver');grant(service,row,pair,'receiver');setup=command(service,row,'observed_refresh')
        with pytest.raises(ValueError,match='Both exact current'):
            service.command('bind_observed_test',{'session_id':row['session_id'],'expected_epoch':row['epoch'],
                'observed_choice_id':setup['bind_choice_id']},authenticated_actor='other-owner')
        pair.provider.revoke('USER_REPLACED');pi_authorize(pair.provider,service.observed_setup.receiver_binding(row,spec))
        with pytest.raises(ValueError,match='Both exact current'):command(service,row,'bind_observed_test',observed_choice_id=setup['bind_choice_id'])
        for key,value in (('last_recording_id','retained-original'),('dispatch_receipts',[{'original':True}]),('protocol_trace',[{'original':True}])):
            with service.workflow.lock:
                changed=service.workflow.snapshot(row['session_id']);changed[key]=value;service.workflow._save(changed)
            with pytest.raises(ValueError,match='fresh metadata intent'):service.observed_setup.bind(setup['bind_choice_id'],changed,spec,actor='trusted-owner')
            with service.workflow.lock:changed[key]=[] if key!='last_recording_id' else None;service.workflow._save(changed)
        service.observed_setup.prior=lambda _:True
        assert not command(service,row,'observed_refresh')['bind_observed_test_available']
        pair.no_native_commands()
    finally:service.close();pair.close();client.close()


def test_fresh_exact_pi_observation_only_renews_observation_and_old_revoke_cannot_clear_new_grant(tmp_path):
    service,row,spec,pair,client=configured(tmp_path)
    try:
        grant(service,row,pair,'giver');old=grant(service,row,pair,'receiver');setup=command(service,row,'observed_refresh')
        choice=setup['permission_choices']['receiver']['permission_choice_id'];pair.pi_tick[0]+=1_000_000_000;frame=pair.camera.publish()
        observation=command(service,row,'observed_display',permission_choice_id=choice)
        body={'observation_choice_id':observation['observation_choice_id'],'displayed_jpeg_sha256':observation['frame']['frame_sha256'],'observing':True}
        with pytest.raises(ValueError,match='view and confirm'):command(service,row,'observed_observe',**body)
        service.observed_frame(observation['observation_choice_id'],authenticated_actor='trusted-owner')
        result=command(service,row,'observed_observe',**body)
        assert result['native_permission_receipt']['observation_deadline_ns']==frame.monotonic_ns+2_000_000_000
        assert not result['native_operator_lease_renewed'] and result['native_permission_receipt']['grant_id']==old['grant_id']
        pair.provider.revoke('NEW_TEST');new,_=pi_authorize(pair.provider,service.observed_setup.receiver_binding(row,spec))
        result=command(service,row,'observed_revoke',permission_choice_id=choice)
        assert not result['revoked'] and pair.provider.status()['grant']['grant_id']==new['grant_id']
        pair.no_native_commands()
    finally:service.close();pair.close();client.close()


def test_stop_during_returning_authorize_fences_late_grant_cleanup_and_never_revokes_new_user(tmp_path):
    service,row,spec,pair,client=configured(tmp_path)
    try:
        setup=command(service,row,'observed_refresh')
        challenge=command(service,row,'observed_challenge',source_setup_choice_id=setup['setup_choices']['receiver']['choice_id'])
        service.observed_frame(challenge['challenge_choice_id'],authenticated_actor='trusted-owner')
        entered,release=threading.Event(),threading.Event();pair.block=(entered,release);errors=[]
        def authorize_old():
            try:command(service,row,'observed_authorize',challenge_choice_id=challenge['challenge_choice_id'],
                displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True)
            except ValueError as error:errors.append(error)
        thread=threading.Thread(target=authorize_old);thread.start();assert entered.wait(1)
        command(service,row,'stop');pair.provider.revoke('NEW_USER_TEST');pair.block=None
        new,_=pi_authorize(pair.provider,service.observed_setup.receiver_binding(row,spec))
        release.set();thread.join(2)
        assert errors and not thread.is_alive() and pair.provider.status()['grant']['grant_id']==new['grant_id']
        assert service.workflow.snapshot(row['session_id'])['status']=='STOPPED'
        pair.no_native_commands()
    finally:service.close();pair.close();client.close()


def test_stop_revokes_only_both_exact_unstarted_metadata_grants(tmp_path):
    service,row,spec,pair,client=configured(tmp_path)
    try:
        grant(service,row,pair,'giver');grant(service,row,pair,'receiver')
        result=command(service,row,'stop')
        assert len(result['observed_metadata_revocations'])==2 and not pair.gate.allowed() and not pair.provider.status()['execution_permission']
        pair.no_native_commands()
    finally:service.close();pair.close();client.close()


def test_old_unbound_native_input_authorization_is_not_new_cooperative_user_consent(tmp_path):
    service,row,spec,pair,client=configured(tmp_path)
    try:
        native=pair.gate.challenge()
        pair.gate.authorize({'challenge_id':native['challenge_id'],'displayed_jpeg_sha256':native['frame']['jpeg_sha256'],
            'actor_id':'trusted-owner','input_mode':'keyboard','observing':True,'neutral':True,'space_clear':True,
            'user_observed_test_confirmed':True})
        grant(service,row,pair,'receiver');setup=command(service,row,'observed_refresh')
        assert not setup['bind_observed_test_available'] and 'giver:CURRENT_INTENT_EXPLICIT_USER_CONFIRMATION_REQUIRED' in setup['blockers']
        grant(service,row,pair,'giver');assert command(service,row,'observed_refresh')['bind_observed_test_available']
        pair.no_native_commands()
    finally:service.close();pair.close();client.close()


def test_coordinator_shutdown_revokes_owned_metadata_without_native_stop_or_lease_renewal(tmp_path):
    service,row,spec,pair,client=configured(tmp_path)
    try:
        grant(service,row,pair,'giver');grant(service,row,pair,'receiver');lease=pair.teleop.lease
        service.close()
        assert not pair.gate.allowed() and not pair.provider.status()['execution_permission'] and pair.teleop.lease==lease
        pair.no_native_commands()
    finally:pair.close();client.close()


def test_cache_eviction_after_final_challenge_read_still_revokes_just_returned_grant(tmp_path,monkeypatch):
    service,row,spec,pair,client=configured(tmp_path)
    try:
        setup=command(service,row,'observed_refresh')
        challenge=command(service,row,'observed_challenge',source_setup_choice_id=setup['setup_choices']['receiver']['choice_id'])
        identity=challenge['challenge_choice_id'];service.observed_frame(identity,authenticated_actor='trusted-owner')
        original=service.observed_setup._challenge;reads=[]
        def evict_after_read(choice,actor):
            result=original(choice,actor);reads.append(choice)
            if len(reads)==2:
                with service.observed_setup.lock:service.observed_setup.challenges.pop(choice,None)
            return result
        monkeypatch.setattr(service.observed_setup,'_challenge',evict_after_read)
        with pytest.raises(ValueError,match='evicted/revoked'):
            command(service,row,'observed_authorize',challenge_choice_id=identity,
                displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],observing=True,
                robot_workspace_checked=True,user_observed_test_confirmed=True)
        assert not pair.provider.status()['execution_permission']
        assert not service.observed_setup.permissions
        pair.no_native_commands()
    finally:service.close();pair.close();client.close()
