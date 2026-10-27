"""Real temp workflow/observed cache/COMMON/WAL; fake native owners, no motors."""
from copy import deepcopy
import hashlib
import threading

import pytest

from explorer.cooperative_receiver_setup import CooperativeReceiverSetup
from explorer_shared.contracts import canonical
from explorer_shared.station_cooperative_interlock import StationCooperativeInterlock
from explorer_shared.station_publication_gate import StationPublicationGate
from test_cooperative_observed_service import configured,grant,command


def rig(tmp_path):
    service,row,spec,pair,client=configured(tmp_path)
    station=pair.station;station.root=tmp_path/'actual-fake-station';station.root.mkdir()
    calibration=station.root/'follower.json';calibration.write_bytes(canonical({'gripper':{'id':6,'range_min':500,'range_max':3500}}))
    station.calibration=hashlib.sha256(calibration.read_bytes()).hexdigest()
    station.profiles={'follower':{'calibration_sha256':station.calibration,'calibration_path':str(calibration),'verified_on_connected_hardware':True}}
    station.measurements={};pair.provider.publication_gate=StationPublicationGate(pair.provider)
    wrapper=StationCooperativeInterlock(pair.provider,station.root/'cooperative-hold.json');pair.provider.cooperative_interlock=wrapper
    original=pair.receiver
    def receiver(method,path,body):
        if path=='/station/status':
            pair.calls.append(('receiver',method,path,None));assert method=='GET'
            return {**station.status(),'configuration_writes':0}
        prefix='/station/observed-test/cooperative/'
        if path.startswith(prefix):
            pair.calls.append(('receiver',method,path,deepcopy(body)))
            if path==prefix+'status':assert method=='GET';return wrapper.status()
            assert method=='POST';value=deepcopy(body);actor=value.pop('actor_id')
            return wrapper.dispatch(path[len(prefix):],value,authenticated_actor=actor)
        return original(method,path,body)
    pair.receiver=receiver;service.observed_setup.requests['receiver']=receiver
    service._request=lambda role:pair.giver if role=='giver' else receiver
    grant(service,row,pair,'receiver');helper=CooperativeReceiverSetup(service)
    return helper,service,row,spec,pair,wrapper,client


def close(service,pair,client):service.close();pair.close();client.close()
def choose(helper,row,spec,operation):
    state=helper.refresh(row,spec,actor='trusted-owner');op=state['operations'][operation]
    assert op['available'],op
    return {'session_id':row['session_id'],'expected_epoch':row['epoch'],'receiver_setup_choice_id':op['choices'][0]['choice_id'],
        'observing':True,'user_observed_test_confirmed':True}


def displayed(service,row,pair,label,position):
    pair.pi_tick[0]+=100_000_000;frame=pair.camera.publish();frame.monotonic_ns-=1
    frame.jpeg=b'original-cache-fixture-'+label.encode()+str(pair.pi_tick[0]).encode()
    pair.station.measurements={'follower':[1.,2.,3.,4.,5.,position],'monotonic_ns':pair.pi_tick[0],'clock_domain':pair.station.clock_domain}
    setup=command(service,row,'observed_refresh')
    observation=command(service,row,'observed_display',permission_choice_id=setup['permission_choices']['receiver']['permission_choice_id'])
    return observation


def capture(helper,service,row,spec,pair,label,position):
    observation=displayed(service,row,pair,label,position)
    service.observed_frame(observation['observation_choice_id'],authenticated_actor='trusted-owner')
    body={**choose(helper,row,spec,'capture_'+label),'observation_choice_id':observation['observation_choice_id'],
        'displayed_jpeg_sha256':observation['frame']['frame_sha256'],'empty_gripper_confirmed':True}
    return helper.execute('capture_'+label,row,spec,body,actor='trusted-owner')


def test_real_displayed_source_pair_then_explicit_profile_bind_never_native_control_or_authority(tmp_path):
    helper,service,row,spec,pair,wrapper,client=rig(tmp_path)
    try:
        before=list(pair.calls);assert helper.snapshot(row)['operations']=={} and pair.calls==before
        refreshed=helper.refresh(row,spec,actor='trusted-owner')
        assert not refreshed['operations']['bind_guard']['available'] and all(method=='GET' for _,method,_,_ in pair.calls[len(before):])
        assert 'direction_profile' not in refreshed['source_status'] and 'frozen' not in refreshed['source_status']
        assert capture(helper,service,row,spec,pair,'empty_open',80.)['source_receipt']['direction_profile_id'] is None
        closed=capture(helper,service,row,spec,pair,'empty_closed',20.)
        assert closed['source_receipt']['direction_profile_id'] and len(wrapper.captures)==2
        body=choose(helper,row,spec,'bind_guard');result=helper.execute('bind_guard',row,spec,body,actor='trusted-owner')
        assert result['source_receipt']['capability_ready'] and not wrapper.status()['armed']
        with pytest.raises(ValueError,match='One-use'):helper.execute('bind_guard',row,spec,body,actor='trusted-owner')
        assert helper.snapshot(row)['operations']=={} and not service.transports
        assert not service.snapshot(row['session_id'])['execution_authority']['explicit_user_observed_test_current']
        assert pair.station.calls==[] and not result['physical_execution_authorized']
        assert not any(path=='/station/observed-test/observe' or path.endswith('/command') or path.endswith('/lease') for _,_,path,_ in pair.calls)
    finally:close(service,pair,client)


def test_capture_requires_actual_served_existing_cache_and_rejects_browser_native_fields(tmp_path):
    helper,service,row,spec,pair,wrapper,client=rig(tmp_path)
    try:
        observation=displayed(service,row,pair,'empty_open',80.)
        body={**choose(helper,row,spec,'capture_empty_open'),'observation_choice_id':observation['observation_choice_id'],
            'displayed_jpeg_sha256':observation['frame']['frame_sha256'],'empty_gripper_confirmed':True}
        before=len(pair.calls)
        with pytest.raises(ValueError,match='served existing'):helper.execute('capture_empty_open',row,spec,body,actor='trusted-owner')
        assert not wrapper.capture_root.exists() and all(method=='GET' for _,method,_,_ in pair.calls[before:])
        for extra in ({'actor_id':'forged'},{'frame_id':'browser-frame'},{'measured_native6':[0]*6},{'direction_profile_id':'f'*64},{'measurement_uncertainty_native':0}):
            with pytest.raises(ValueError,match='ID-only'):helper.execute('capture_empty_open',row,spec,dict(body,**extra),actor='trusted-owner')
        service.observed_frame(observation['observation_choice_id'],authenticated_actor='trusted-owner')
        body.update(choose(helper,row,spec,'capture_empty_open'))
        result=helper.execute('capture_empty_open',row,spec,body,actor='trusted-owner')
        assert result['source_receipt']['capture_id'] and pair.station.calls==[]
    finally:close(service,pair,client)


def test_different_actor_expired_or_old_epoch_choice_never_posts_source_metadata(tmp_path):
    helper,service,row,spec,pair,wrapper,client=rig(tmp_path)
    try:
        body={**choose(helper,row,spec,'capture_empty_open'),'observation_choice_id':'0'*32,'displayed_jpeg_sha256':'0'*64};before=len(pair.calls)
        with pytest.raises(ValueError,match='One-use'):helper.execute('capture_empty_open',row,spec,dict(body,empty_gripper_confirmed=True),actor='other-owner')
        old=helper.clock;stamp=helper.clock();helper.clock=lambda:stamp+2_000_000_001
        with pytest.raises(ValueError,match='One-use'):helper.execute('capture_empty_open',row,spec,dict(body,empty_gripper_confirmed=True),actor='trusted-owner')
        helper.clock=old
        with pytest.raises(ValueError,match='session/epoch'):helper.execute('capture_empty_open',row,spec,dict(body,empty_gripper_confirmed=True,expected_epoch='old'),actor='trusted-owner')
        assert len(pair.calls)==before and not wrapper.capture_root.exists()
    finally:close(service,pair,client)


def test_conflicting_real_source_profiles_never_get_a_silently_selected_bind_choice(tmp_path):
    helper,service,row,spec,pair,wrapper,client=rig(tmp_path)
    try:
        for label,value in (('empty_open',80.),('empty_closed',20.),('empty_open',20.),('empty_closed',80.)):
            capture(helper,service,row,spec,pair,label,value)
        assert len(wrapper.profiles)==2
        state=helper.refresh(row,spec,actor='trusted-owner')
        assert not state['operations']['bind_guard']['available'] and not state['operations']['bind_guard']['choices']
        assert state['operations']['bind_guard']['reason']=='MULTIPLE_DIRECTION_PROFILES_REQUIRE_SOURCE_REVIEW'
        assert pair.station.calls==[]
    finally:close(service,pair,client)


def test_safe_reset_is_separate_explicit_idle_current_user_metadata_only(tmp_path):
    helper,service,row,spec,pair,wrapper,client=rig(tmp_path)
    try:
        capture(helper,service,row,spec,pair,'empty_open',80.);capture(helper,service,row,spec,pair,'empty_closed',20.)
        helper.execute('bind_guard',row,spec,choose(helper,row,spec,'bind_guard'),actor='trusted-owner')
        body=choose(helper,row,spec,'reset_safe');before=len(pair.calls)
        with pytest.raises(ValueError,match='explicit USER'):helper.execute('reset_safe',row,spec,body,actor='trusted-owner')
        assert len(pair.calls)==before
        result=helper.execute('reset_safe',row,spec,dict(body,object_safe_no_dual_hold=True),actor='trusted-owner')
        assert result['source_receipt']['phase']=='UNBOUND' and pair.station.calls==[]
    finally:close(service,pair,client)


def test_late_get_refresh_after_local_invalidation_never_repopulates_choices(tmp_path):
    helper,service,row,spec,pair,wrapper,client=rig(tmp_path);entered,release=threading.Event(),threading.Event();errors=[]
    original=service._request('receiver')
    def delayed(method,path,body):
        result=original(method,path,body)
        if path.endswith('/cooperative/status'):entered.set();assert release.wait(2)
        return result
    service._request=lambda role:delayed if role=='receiver' else pair.giver
    try:
        def refresh():
            try:helper.refresh(row,spec,actor='trusted-owner')
            except ValueError as error:errors.append(error)
        thread=threading.Thread(target=refresh);thread.start();assert entered.wait(1)
        helper.invalidate_session(row);release.set();thread.join(2)
        assert errors and not thread.is_alive() and helper.snapshot(row)['operations']=={}
        assert pair.station.calls==[]
    finally:release.set();close(service,pair,client)


def test_local_revoke_during_prepost_get_fences_consumed_choice_without_sending_capture(tmp_path):
    helper,service,row,spec,pair,wrapper,client=rig(tmp_path);entered,release=threading.Event(),threading.Event();errors=[]
    try:
        observation=displayed(service,row,pair,'empty_open',80.)
        service.observed_frame(observation['observation_choice_id'],authenticated_actor='trusted-owner')
        body={**choose(helper,row,spec,'capture_empty_open'),'observation_choice_id':observation['observation_choice_id'],
            'displayed_jpeg_sha256':observation['frame']['frame_sha256'],'empty_gripper_confirmed':True}
        original=service._request('receiver')
        def delayed(method,path,payload):
            result=original(method,path,payload)
            if path.endswith('/cooperative/status'):entered.set();assert release.wait(2)
            return result
        service._request=lambda role:delayed if role=='receiver' else pair.giver
        def execute():
            try:helper.execute('capture_empty_open',row,spec,body,actor='trusted-owner')
            except ValueError as error:errors.append(error)
        before=len(pair.calls);thread=threading.Thread(target=execute);thread.start();assert entered.wait(1)
        helper.invalidate_session(row);release.set();thread.join(2)
        assert errors and not thread.is_alive() and all(method=='GET' for _,method,_,_ in pair.calls[before:])
        assert not wrapper.capture_root.exists() and pair.station.calls==[]
    finally:release.set();close(service,pair,client)


def test_unavailable_source_stays_unknown_in_readonly_summary_and_has_no_choices(tmp_path):
    helper,service,row,spec,pair,wrapper,client=rig(tmp_path)
    try:
        def missing(method,path,body):raise ValueError('SOFTWARE-FIXTURE source unavailable')
        service._request=lambda role:missing if role=='receiver' else pair.giver
        result=helper.refresh(row,spec,actor='trusted-owner')
        assert 'source unavailable' in result['reason'] and all(not op['available'] for op in result['operations'].values())
        assert result['source_status']['direction_profile_count'] is None and result['source_status']['direction_capture_count'] is None
        assert pair.station.calls==[]
    finally:close(service,pair,client)


def test_real_root_helper_adopts_original_worker_hold_with_server_owned_ids_and_no_new_write(tmp_path,monkeypatch):
    from test_cooperative_service import fixture as central_fixture,session
    from test_station_publication_gate import worker_rig
    from test_station_hold_successor import authorize
    service,store,calls,active,contexts,client=central_fixture(tmp_path);row,spec=session(service)
    path=tmp_path/'original-worker';path.mkdir();binding=service.observed_setup.receiver_binding(row,spec)
    station,provider,selected,start,bus,samples,tick,StopAfter,leader=worker_rig(path,monkeypatch,binding_override=binding,actor='trusted-owner')
    monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:tick[0])
    try:
        old=start();station.stop_event=StopAfter();station._work();wrapper=provider.cooperative_interlock
        bound=wrapper.dispatch('bind',{'grant_id':old['grant_id'],'expected_control_epoch':old['control_epoch'],'expected_revision':0},authenticated_actor='trusted-owner')
        wrapper.dispatch('arm',{'grant_id':old['grant_id'],'expected_control_epoch':old['control_epoch'],'interlock_id':bound['interlock_id'],'expected_revision':bound['revision']},authenticated_actor='trusted-owner')
        provider.command(old['grant_id'],binding,'stop',{'expected_control_epoch':old['control_epoch']})
        tick[0]+=100_000_000;station.stop_event=StopAfter();station._work();fresh=authorize(provider,binding,'trusted-owner');posts=[]
        def source(method,path,body):
            if method=='GET':
                if path=='/station/status':return station.status()
                if path=='/station/observed-test/status':return provider.status()
                if path=='/station/observed-test/cooperative/status':return wrapper.status()
                raise AssertionError(path)
            assert path=='/station/observed-test/cooperative/successor';posts.append(deepcopy(body));fields=deepcopy(body);actor=fields.pop('actor_id')
            return wrapper.dispatch('successor',fields,authenticated_actor=actor)
        service._request=lambda role:source;helper=service.receiver_setup
        state=helper.refresh(row,spec,actor='trusted-owner');choice=state['operations']['adopt_hold']['choices'][0]['choice_id'];writes=deepcopy(bus.writes)
        body={'receiver_setup_choice_id':choice,'observing':True,'user_observed_test_confirmed':True}
        for extra in ({'previous_grant_id':'browser-spoof'},{'held_native6':[0]*6},{'control_epoch':'browser-spoof'}):
            with pytest.raises(ValueError,match='ID-only'):helper.execute('adopt_hold',row,spec,{**body,**extra},actor='trusted-owner')
        result=helper.execute('adopt_hold',row,spec,body,actor='trusted-owner')
        assert posts==[{'grant_id':fresh['grant_id'],'expected_control_epoch':fresh['control_epoch'],'interlock_id':bound['interlock_id'],
            'expected_revision':bound['revision']+1,'previous_grant_id':old['grant_id'],'previous_control_epoch':old['control_epoch'],
            'observing':True,'user_observed_test_confirmed':True,'actor_id':'trusted-owner'}]
        assert result['source_receipt']['schema']=='explorer_station_cooperative_successor_receipt_v1'
        assert result['physical_execution_authorized'] is False and bus.writes==writes
        assert wrapper.status()['armed'] and wrapper.status()['parked_observation_only']
        assert not helper.refresh(row,spec,actor='trusted-owner')['operations']['adopt_hold']['available']
    finally:service.close();client.close()
