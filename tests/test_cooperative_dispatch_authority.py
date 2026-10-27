"""Fresh per-role GET gates over actual fake Source/Node wrappers, no targets."""
from copy import deepcopy
import threading

import pytest

from explorer.cooperative_dispatch_authority import CooperativeDispatchAuthority
from test_cooperative_observed_scopes import Sources,ACTOR
from test_cooperative_giver_phases import phase
from test_cooperative_native_sessions import execute,choice


def active(sources,role):
    service=sources.service;service._arm_guard(sources.row)
    if role=='giver':phase(service,sources.row,'native_phase_resume')
    else:execute(service,'native_phase_activate',{**choice(service,sources.row,'receiver','native_phase_activate'),'neutral':True})
    execute(service,'native_attach',choice(service,sources.row,role,'native_attach'))
    return service.workflow.snapshot(sources.row['session_id'])


@pytest.mark.parametrize('role',['giver','receiver'])
def test_fresh_own_native_transport_and_exact_parked_partner_qualify_only_this_role_without_commands(tmp_path,monkeypatch,role):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        gate=CooperativeDispatchAuthority(service);before=(len(sources.calls),len(sources.pair.calls))
        with pytest.raises(ValueError,match='attachment'):gate.require(sources.row,sources.spec,role,'release' if role=='giver' else 'close')
        assert before==(len(sources.calls),len(sources.pair.calls))
        row=active(sources,role);before=(len(sources.calls),len(sources.pair.calls));writes=deepcopy(sources.bus.writes);drives=deepcopy(sources.pair.teleop.native_drive)
        proof=gate.require(row,sources.spec,role,'release' if role=='giver' else 'close',
            expected_transport=service.transports[(row['session_id'],role)])
        other='receiver' if role=='giver' else 'giver'
        assert proof['dispatch_authority_by_role']=={role:True,other:False}
        assert proof['source_proofs'][role]['kind']=='active_native_transport' and proof['source_proofs'][other]['kind']=='parked_observation'
        assert proof['per_role_authority_is_metadata_preview'] is False and proof['source_revalidation_required_before_native_dispatch'] is False
        assert proof['native_commands_sent']==0 and not proof['native_lease_renewed'] and not proof['physical_attainment_claimed']
        assert not service._authority(row)['explicit_user_observed_test_current']
        assert all(method=='GET' for method,_,_ in sources.calls[before[0]:])
        assert all(method=='GET' for _,method,_,_ in sources.pair.calls[before[1]:])
        assert sources.bus.writes==writes and sources.pair.teleop.native_drive==drives
    finally:sources.close()


def test_bound_attachment_actor_epoch_transport_and_grant_cannot_be_substituted_by_cached_ready_flags(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        row=active(sources,'receiver');gate=CooperativeDispatchAuthority(service);transport=service.transports[(row['session_id'],'receiver')]
        original=deepcopy(service.native_bindings[row['session_id']])
        for field,value in [('actor','foreign-user'),('epoch','foreign-epoch')]:
            service.native_bindings[row['session_id']][field]=value
            with pytest.raises(ValueError,match='attachment'):gate.require(row,sources.spec,'receiver','close')
            service.native_bindings[row['session_id']]=deepcopy(original)
        with pytest.raises(ValueError,match='attachment'):gate.require(row,sources.spec,'receiver','close',expected_transport=object())
        saved=deepcopy(transport.binding);transport.binding['coordinator_epoch']='old-epoch'
        with pytest.raises(ValueError,match='another Root'):gate.require(row,sources.spec,'receiver','close')
        transport.binding=saved
        old=transport.grant['provider_process'];transport.grant['provider_process']='another-native-process'
        with pytest.raises(ValueError,match='receiver model/grant'):gate.require(row,sources.spec,'receiver','close')
        transport.grant['provider_process']=old
        assert gate.require(row,sources.spec,'receiver','close')['dispatch_authority_by_role']['receiver']
    finally:sources.close()


def test_cached_partner_preview_cannot_hide_actual_source_permission_revocation(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        row=active(sources,'receiver');gate=CooperativeDispatchAuthority(service)
        assert service.observed_scopes.require_current(row,sources.spec,('giver',),actor=ACTOR)['roles']['giver']['current']
        sources.pair.gate.revoke('fixture: current USER observation lost')
        assert service.observed_scopes.snapshot(row,sources.spec)['roles']['giver']['current']  # Cached preview only.
        before=(len(sources.calls),len(sources.pair.calls))
        with pytest.raises(ValueError):gate.require(row,sources.spec,'receiver','close')
        assert len(sources.pair.calls)>before[1]
        assert all(method=='GET' for method,_,_ in sources.calls[before[0]:])
        assert all(method=='GET' for _,method,_,_ in sources.pair.calls[before[1]:])
    finally:sources.close()


def test_partner_scope_io_cannot_extend_an_expiring_moving_native_lease(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        row=active(sources,'giver');gate=CooperativeDispatchAuthority(service);request=service._request;expired=[]
        def routed(role):
            callback=request(role)
            def invoke(method,path,body):
                result=callback(method,path,body)
                if role=='receiver' and path.endswith('/held/status') and not expired:
                    expired.append(True);sources.pair.native_tick[0]+=451_000_000
                return result
            return invoke
        service._request=routed
        lease=sources.pair.teleop.lease;before=(len(sources.calls),len(sources.pair.calls))
        with pytest.raises(ValueError,match='current|Fresh actual source owner'):gate.require(row,sources.spec,'giver','release')
        assert expired and sources.pair.teleop.lease==lease
        assert all(method=='GET' for method,_,_ in sources.calls[before[0]:])
        assert all(method=='GET' for _,method,_,_ in sources.pair.calls[before[1]:])
    finally:sources.close()


def test_stop_during_partner_get_cannot_return_a_late_dispatch_admission(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service;entered,release=threading.Event(),threading.Event();errors=[]
    worker=None
    try:
        row=active(sources,'receiver');gate=CooperativeDispatchAuthority(service);request=service._request
        def routed(role):
            callback=request(role)
            def invoke(method,path,body):
                if role=='giver' and method=='GET' and path.endswith('/phase/held-status'):
                    entered.set();assert release.wait(4)
                return callback(method,path,body)
            return invoke
        service._request=routed
        def checking():
            try:gate.require(row,sources.spec,'receiver','close')
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=checking);worker.start();assert entered.wait(2)
        stopped=service.workflow.cancel_session(row['session_id'],'USER_STOP_DURING_SCOPE_GET')
        assert stopped['session']['status']=='STOPPED' and worker.is_alive()
        release.set();worker.join(4);assert not worker.is_alive() and errors
        assert not any(method=='POST' and path.endswith(('/input','/action','/lease')) for _,method,path,_ in sources.pair.calls)
    finally:
        release.set()
        if worker is not None:worker.join(4)
        sources.close()


@pytest.mark.parametrize('unarmed',['giver','receiver'])
def test_close_requires_both_actual_native_latches_not_only_ready_transport_and_scope(tmp_path,monkeypatch,unarmed):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        row=active(sources,'receiver');gate=CooperativeDispatchAuthority(service)
        if unarmed=='giver':
            guard=sources.pair.owner.guard
            def change():
                with guard.lock:guard.armed=False;guard._bump_revision()
            guard._durable('SOFTWARE_FIXTURE_UNARMED_PARTNER',change)
        else:
            from explorer_shared import station_cooperative_interlock as wal
            with sources.wrapper.helper.lock:sources.wrapper.helper.armed=False;sources.wrapper.helper.revision+=1
            wal._write(sources.wrapper.path,sources.wrapper._envelope())
        # Before a grasp the ordinary approach may remain possible. Closing
        # is a different protocol action and must see both actual armed bits.
        assert gate.require(row,sources.spec,'receiver','approach')['dispatch_authority_by_role']['receiver']
        with pytest.raises(ValueError,match='Both original dual-hold'):gate.require(row,sources.spec,'receiver','close')
    finally:sources.close()
