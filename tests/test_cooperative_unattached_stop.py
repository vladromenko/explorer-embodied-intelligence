"""Actual COMMON/native API cleanup before transport attach; no hardware."""
import threading
import pytest

from test_cooperative_native_sessions import configured,choice,execute
from test_cooperative_observed_service import command
from test_station_observed_activation import authorize as pi_authorize
from test_native_activation import authorize as native_authorize


def close(service,pair,client):service.close();pair.close();client.close()


def test_completed_pi_begin_without_attach_is_scoped_stopped_immediately_and_old_metadata_revoke_is_not_relied_on(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        result=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))
        started=result['native_receipt']['grant'];assert not service.transports and pair.station.mode=='teleoperation'
        assert started['control_epoch']!=row['observed_test_binding']['native_grants']['receiver']['control_epoch']
        stopped=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner',reason='USER_GLOBAL_STOP')
        command(service,row,'stop')
        assert stopped['roles']['receiver']['source_cleanup_receipt']['native_command_called']
        assert [op for op,_ in pair.station.calls]==['teleop/start','stop'] and pair.station.mode=='idle'
        assert not pair.provider.status()['execution_permission'] and not stopped['dual_hold_interlocks_unarmed']
    finally:close(service,pair,client)


def test_expired_or_unreadable_pi_permission_status_uses_only_cached_returned_epoch(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        returned=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))['native_receipt']['grant']
        pair.pi_tick[0]+=2_100_000_000;pair.camera.publish()
        original=service._request('receiver')
        def offline_status(method,path,body):
            if method=='GET' and path=='/station/observed-test/status':raise OSError('fixture: permission GET unavailable')
            return original(method,path,body)
        service._request=lambda role:pair.giver if role=='giver' else offline_status
        stopped=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        assert stopped['roles']['receiver']['source_cleanup_receipt']['native_command_called']
        stop=next(body for op,body in pair.station.calls if op=='stop')
        assert stop['expected_control_epoch']==returned['control_epoch'] and pair.station.mode=='idle'
    finally:close(service,pair,client)


def test_new_pi_grant_or_same_grant_foreign_epoch_cannot_be_stopped_by_old_owned_receipt(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        old=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))['native_receipt']['grant']
        # A different current native epoch under this same grant is NOT adopted.
        newer=pair.provider.command(old['grant_id'],old['binding'],'teleop/recenter',{'expected_control_epoch':old['control_epoch']})['grant']
        before=list(pair.station.calls);result=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        assert result['roles']['receiver']['source_cleanup_receipt']['new_native_epoch_not_owned'] and pair.station.calls==before
        # Nor can a genuinely new USER grant be cleaned by the old receipt.
        pair.provider.command(newer['grant_id'],newer['binding'],'stop',{'expected_control_epoch':newer['control_epoch']})
        fresh,_=pi_authorize(pair.provider,old['binding']);fresh=pair.provider.command(fresh['grant_id'],fresh['binding'],'teleop/start',
            {'expected_control_epoch':fresh['control_epoch']})['grant'];before=list(pair.station.calls)
        result=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        assert result['roles']['receiver']['source_cleanup_receipt']['new_native_grant_not_owned'] and pair.station.calls==before
        assert pair.provider.status()['grant']['grant_id']==fresh['grant_id']
    finally:close(service,pair,client)


def test_completed_giver_manual_resume_without_attach_stops_exact_generation(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        execute(service,'native_begin',choice(service,row,'giver','native_begin'))
        resumed=execute(service,'native_resume',choice(service,row,'giver','native_resume'))
        generation=resumed['native_receipt']['native']['owner_generation'];assert not pair.teleop.stop_latched and not service.transports
        result=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        assert result['roles']['giver']['source_cleanup_receipt'] and pair.teleop.stop_latched
        post=next(body for _,method,path,body in reversed(pair.calls) if method=='POST' and path.endswith('/cooperative/stop'))
        assert post['expected_native_generation']==generation and post['control_epoch'] is None
        assert not any(path.endswith('/input') or path.endswith('/lease') for _,_,path,_ in pair.calls)
    finally:close(service,pair,client)


def test_completed_giver_policy_begin_without_attach_stops_exact_epoch_and_new_generation_is_preserved(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=configured(tmp_path,policy_roles=('giver',))
    monkeypatch.setattr(service.native_controls,'_ready_prediction',lambda *args:lambda *a,**kw:pytest.fail('No prediction during cleanup'))
    try:
        returned=execute(service,'native_begin',choice(service,row,'giver','native_begin'))['native_receipt']['begin']
        assert pair.mux.active and not service.transports
        result=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        assert result['roles']['giver']['source_cleanup_receipt'] and not pair.mux.active and pair.teleop.stop_latched
        post=next(body for _,method,path,body in reversed(pair.calls) if method=='POST' and path.endswith('/cooperative/stop'))
        assert post['control_epoch']==returned['control_epoch']
    finally:close(service,pair,client)


def test_new_giver_generation_and_other_actor_are_not_owned_and_cancel_fences_queued_choices(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        execute(service,'native_begin',choice(service,row,'giver','native_begin'))
        execute(service,'native_resume',choice(service,row,'giver','native_resume'))
        pending=choice(service,row,'receiver','native_begin');pair.teleop.generation+=1;before=len(pair.calls)
        with pytest.raises(ValueError,match='operator'):service.native_controls.cancel_owned_sessions(row,actor='another-owner')
        assert len(pair.calls)==before
        result=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        assert result['roles']['giver']['source_cleanup_receipt']['new_native_epoch_or_generation_not_owned'] and not pair.teleop.stop_latched
        before=len(pair.calls)
        with pytest.raises(ValueError,match='USER-bound'):execute(service,'native_begin',pending)
        assert len(pair.calls)==before and not pair.station.calls
    finally:close(service,pair,client)


def test_local_cancel_before_workflow_epoch_changes_fences_late_begin_reply_and_cleans_its_exact_returned_epoch(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path);entered,release=threading.Event(),threading.Event();errors=[]
    try:
        body=choice(service,row,'receiver','native_begin');pair.delayed=(entered,release)
        def begin():
            try:execute(service,'native_begin',body)
            except ValueError as error:errors.append(error)
        thread=threading.Thread(target=begin);thread.start();assert entered.wait(1)
        cleanup=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        assert not cleanup['roles']['receiver']['known_owned_native_session']  # Return scope still genuinely unknown.
        pair.delayed=None;release.set();thread.join(2)
        assert errors and not thread.is_alive() and [op for op,_ in pair.station.calls]==['teleop/start','stop']
        assert pair.station.mode=='idle' and not service.native_controls.snapshot(row)['roles']
        assert not service.workflow.snapshot(row['session_id']).get('native_user_control_receipts')
    finally:release.set();close(service,pair,client)


def test_fresh_giver_authorization_cannot_be_stopped_or_revoked_by_old_record(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        execute(service,'native_begin',choice(service,row,'giver','native_begin'))
        execute(service,'native_resume',choice(service,row,'giver','native_resume'))
        pair.teleop.stop()  # Mock another USER ends the old native generation.
        fresh,_=native_authorize(pair.client,input_mode='keyboard')
        pair.teleop.resume('keyboard',True);before=pair.teleop.native_stop
        result=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        assert result['roles']['giver']['source_cleanup_receipt']['new_native_authorization_not_owned']
        assert not pair.teleop.stop_latched and pair.teleop.native_stop==before and pair.gate.current_authorization_id()==fresh['authorization_id']
    finally:close(service,pair,client)


def test_late_get_refresh_after_cancel_never_repopulates_native_choices(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path);entered,release=threading.Event(),threading.Event();errors=[]
    original=service.native_controls._source
    try:
        def delayed(role):
            result=original(role)
            if role=='receiver':entered.set();assert release.wait(2)
            return result
        service.native_controls._source=delayed
        def refresh():
            try:service.native_controls.refresh(row,spec,actor='trusted-owner')
            except ValueError as error:errors.append(error)
        thread=threading.Thread(target=refresh);thread.start();assert entered.wait(1)
        service.native_controls.cancel_owned_sessions(row,actor='trusted-owner');release.set();thread.join(2)
        assert errors and not thread.is_alive() and service.native_controls.snapshot(row)['roles']=={}
        assert pair.station.calls==[]
    finally:release.set();close(service,pair,client)


def test_service_global_stop_reaches_completed_unattached_pi_using_rotated_native_epoch(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        returned=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))['native_receipt']['grant']
        assert not service.transports and pair.station.mode=='teleoperation'
        result=command(service,row,'stop')
        cleanup=result['native_owned_session_cleanup']['roles']['receiver']['source_cleanup_receipt']
        assert cleanup['native_command_called'] and pair.station.mode=='idle'
        stop=next(body for op,body in pair.station.calls if op=='stop')
        assert stop['expected_control_epoch']==returned['control_epoch']
        assert service.workflow.snapshot(row['session_id'])['status']=='STOPPED'
        assert not pair.provider.status()['execution_permission']
    finally:close(service,pair,client)
