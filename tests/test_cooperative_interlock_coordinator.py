"""Software-only coordinator ordering and evidence causality, no devices."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from explorer.cooperative_service import CooperativeService
from test_cooperative_workflow05 import fixture,zone,run_step


def test_root_arms_receiver_before_giver_and_never_implicitly_unarms_after_partial_failure():
    order=[];latched={'receiver':False}
    def receiver(row):order.append('receiver');latched['receiver']=True;return {'native_receiver_dual_hold_guard':{'armed':True}}
    durability={'installed':True,'healthy':True,'initialized':True,'fail_closed_publication_gate':True,'recovery_required':False,
        'pending':False,'fault':None,'state_id':'a'*32,'revision':1,'last_sealed_sha256':'b'*64}
    source=lambda:{'current_native_owner_ready':True,'source_receipt':{'schema':'explorer_native_cooperative_owner_v1','release_boundary':{'persistence':durability}}}
    def giver(row):order.append('giver');return {'armed':True,'persistence':durability}
    service=CooperativeService.__new__(CooperativeService)
    service.transports={('run','receiver'):SimpleNamespace(arm_release_guard=receiver),('run','giver'):SimpleNamespace(arm_release_guard=giver,observe_state=source)}
    result=service._arm_guard({'session_id':'run'})
    assert order==['receiver','giver'] and result['both_original_publication_boundaries_armed']
    order.clear()
    def failed(row):order.append('giver');raise ValueError('unknown giver arm return')
    service.transports[('run','giver')]=SimpleNamespace(arm_release_guard=failed,observe_state=source)
    with pytest.raises(ValueError,match='unknown giver'):service._arm_guard({'session_id':'run'})
    assert order==['receiver','giver'] and latched['receiver']
    order.clear();durability['initialized']=False
    with pytest.raises(ValueError,match='durable giver'):service._arm_guard({'session_id':'run'})
    assert order==[]


@pytest.mark.parametrize('manual',[False,True])
def test_actual_opening_history_survives_nonopening_last_publication_and_rejects_another_binding(manual):
    row={'session_id':'run','protocol_object_id':'object','epoch':'coord','dispatch_receipts':[]}
    bound={'run_id':'run','object_id':'object','coordinator_epoch':'coord','authorization_id':'actual-current'}
    def publication(ident,tick,binding=bound,opening=True):return {'schema':'explorer_native_cooperative_publication_v1','receipt_id':ident,'opening':opening,
        'binding':deepcopy(binding),'host_publication_returned_native_ns':tick,'native_clock_domain':'nano:clock','native_boot_id':'nano-boot'}
    current=publication('current-opening',20);old=publication('old-opening',10)
    foreign=publication('foreign-newer',1000,dict(bound,authorization_id='foreign'))
    source={'schema':'explorer_native_cooperative_owner_v1','release_boundary':{'binding':bound,'last_publication':publication('later-hold',40,opening=False),
        'recent_opening_publications':[old,current,foreign]}}
    receipt={'native':source,'activation':{'fixture':True}} if manual else source
    service=CooperativeService.__new__(CooperativeService)
    service.transports={('run','giver'):SimpleNamespace(observe_state=lambda:{'current_native_owner_ready':True,'source_receipt':receipt})}
    assert service._giver_opening(row)==current
    source['release_boundary']['recent_opening_publications']=[foreign]
    with pytest.raises(ValueError,match='Actual current source-bound'):service._giver_opening(row)


def transfer_ready(tmp_path,receiver):
    giver={'arm_release_guard':lambda row:{'armed':True},'prepare_release':lambda row,after:{'challenge':{'challenge_id':'software-giver-challenge'},'after_receiver_command_id':'software-receiver-close'},
        'authorize_manual_release':lambda row,proof:{'fixture':True}}
    service,row,inbox,calls,_=fixture(tmp_path,execution_domain='user_observed_physical_test',giver=giver,receiver=receiver)
    service.authority=lambda row:{'explicit_user_observed_test_current':True,'source':'EXPLICIT-SOFTWARE-FIXTURE'}
    zone(service,row)
    for event in ('giver_holding','offer_ready','receiver_ready','receiver_at_object','receiver_closed','receiver_hold_confirmed','release_requested','giver_released'):
        current=run_step(service,row,event);assert current['status']=='READY_FOR_OBSERVED_STEPS',current['errors']
    return service,row,inbox,calls


def test_independent_transfer_confirmation_is_after_pi_challenge_and_correlated_to_actual_giver_opening(tmp_path):
    events=[]
    def prepare(row):events.append('Pi-challenge');return {'challenge':{'challenge_id':'actual-Pi-challenge','giver_release_receipt_id':'actual-giver-publication'}}
    def finish(row,challenge,confirmation):
        events.append(('finish',challenge,confirmation));return {'unfrozen':True,'hardware_command_sent':False}
    service,row,inbox,calls=transfer_ready(tmp_path,{'prepare_receiver_finish':prepare,'authorize_receiver_finish':finish})
    original=inbox.__class__.__call__
    def evidence(self,statement,run,obj,role,after,timeout):
        if statement=='object_transferred_to_receiver':
            assert events==['Pi-challenge'];assert after=='actual-giver-publication';events.append('fresh-transfer-proof')
        return original(self,statement,run,obj,role,after,timeout)
    inbox.__class__.__call__=evidence
    try:current=run_step(service,row,'transfer_confirmed')
    finally:inbox.__class__.__call__=original
    assert current['protocol_state']=='SO101_FINISH' and current['status']=='READY_FOR_OBSERVED_STEPS'
    assert events[:2]==['Pi-challenge','fresh-transfer-proof'] and events[2][0]=='finish'
    assert events[2][1]['challenge']['challenge_id']=='actual-Pi-challenge'
    assert events[2][2]=={'authenticated_operator':'owner'} and calls==[]
    saved=current['observation_receipts'][-1]
    assert saved['actual_giver_opening_receipt_id']=='actual-giver-publication'
    assert saved['native_receiver_independent_transfer_finish']['hardware_command_sent'] is False


def test_missing_receiver_finish_boundary_stops_protocol_without_claiming_transfer(tmp_path):
    service,row,inbox,calls=transfer_ready(tmp_path,{})
    current=run_step(service,row,'transfer_confirmed')
    assert current['status']=='STOPPED' and current['protocol_state']=='SAFE_STOP'
    assert not any(item[0]=='object_transferred_to_receiver' for item in inbox.calls)
    assert len(calls)==2


def test_root_full_authority_requires_actual_durable_giver_not_only_two_live_owners(tmp_path):
    from test_native_giver_persistence import fixture as actual_giver
    owner,*_=actual_giver(tmp_path)
    row={'session_id':'run','epoch':'epoch','status':'ACTIVE','observed_test_binding':{},'execution_domain':'user_observed_physical_test'}
    row['observed_test_binding']={'actor_id':'SOFTWARE-FIXTURE'}
    service=CooperativeService.__new__(CooperativeService);service.native_bindings={'run':{'epoch':'epoch'}}
    state={'current_native_owner_ready':True,'source_receipt':owner.status()}
    def observed():
        state['source_receipt']=owner.status();return state
    service.transports={('run','giver'):SimpleNamespace(observe_state=observed),('run','receiver'):SimpleNamespace(observe_state=lambda:{'current_native_owner_ready':True,'cooperative_dual_hold_publication_interlock_current':True})}
    assert service._authority(row)['explicit_user_observed_test_current']
    owner.guard.journal.fail('EXPLICIT_SOFTWARE_WAL_FAILURE')
    blocked=service._authority(row);assert not blocked['explicit_user_observed_test_current'] and not blocked['giver_durable_dual_hold_publication_interlock_current']
    assert blocked['reason']=='NATIVE_GIVER_DURABLE_DUAL_HOLD_PUBLICATION_INTERLOCK_REQUIRED'
    state['source_receipt']['release_boundary'].pop('persistence')
    service.transports[('run','giver')]=SimpleNamespace(observe_state=lambda:state)
    assert not service._authority(row)['explicit_user_observed_test_current']
