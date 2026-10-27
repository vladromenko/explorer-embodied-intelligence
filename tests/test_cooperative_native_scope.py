"""Typed original native scopes and real Root→source policy STOP; fake devices."""
import pytest

from explorer.cooperative_native_scope import returned_giver_scope
from test_cooperative_native_sessions import configured,choice,execute


def test_policy_resume_keeps_outer_actual_scope_instead_of_inner_teleop_ack():
    scope={'control_epoch':'original-returned-epoch','owner_generation':9,'active':True,'state':'ACTIVE','native':{'owner':'keyboard','stop_latched':False}}
    assert returned_giver_scope({},scope,'native_resume') is scope
    assert returned_giver_scope({},{'native':scope},'native_phase_resume') is scope
    stopped={'control_epoch':None,'owner_generation':8,'active':False}
    assert returned_giver_scope({},{'native':stopped},'native_resume') is stopped


@pytest.mark.parametrize('invalid',[{}, {'control_epoch':None,'owner_generation':8,'active':True},
    {'control_epoch':'x','owner_generation':True,'active':True},{'control_epoch':'x','owner_generation':8,'active':1}])
def test_unknown_return_never_adopts_a_later_remote_scope(invalid):
    with pytest.raises(ValueError):returned_giver_scope({'source':{'native':{'control_epoch':'foreign','owner_generation':99,'active':True}}},invalid,'native_resume')


def test_completed_policy_resume_without_attach_stops_its_exact_returned_epoch_immediately(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=configured(tmp_path,policy_roles=('giver',))
    monkeypatch.setattr(service.native_controls,'_ready_prediction',lambda *args:lambda *args,**kwargs:None)
    try:
        execute(service,'native_begin',choice(service,row,'giver','native_begin'))
        resumed=execute(service,'native_resume',choice(service,row,'giver','native_resume'));actual=resumed['native_receipt']
        assert actual['state']=='ACTIVE' and not service.transports and not pair.teleop.stop_latched
        reply=service.native_controls.cancel_owned_sessions(row,actor='trusted-owner')
        cleanup=reply['roles']['giver']['source_cleanup_receipt']
        assert cleanup and pair.teleop.stop_latched and not pair.mux.active
        source_stops=[body for role,method,path,body in pair.calls if role=='giver' and method=='POST' and path.endswith('/cooperative/stop')]
        assert source_stops[-1]['control_epoch']==actual['control_epoch']
        assert source_stops[-1]['expected_native_generation']==actual['owner_generation']
    finally:service.close();pair.close();client.close()
