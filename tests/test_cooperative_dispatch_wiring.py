"""Fresh gate is mandatory at Workflow and service submission; explicit fixtures."""
from copy import deepcopy
import time

import pytest

from explorer_shared.contracts import digest
from explorer_shared.handover_runtime import DispatchReceipt
from test_cooperative_workflow05 import fixture,metric_zone,policy_capabilities,run_step,DOMAIN


def rig(tmp_path):
    submits=[]
    def dispatch(command,authorization):
        submits.append(deepcopy(command))
        return DispatchReceipt(command['command_id'],command['run_id'],command['object_id'],'giver',True,
            time.monotonic_ns(),DOMAIN,{'fixture_only_no_hardware':True})
    workflow,row,inbox,calls,refs=fixture(tmp_path,mode='giver_policy_receiver_manual',models=True,
        execution_domain='user_observed_physical_test',giver=policy_capabilities(dispatch))
    metric_zone(workflow,row)
    row=workflow.snapshot(row['session_id']);row['observed_test_binding']={'actor_id':'USER-fixture'};workflow._save(row)
    workflow.authority=lambda current:{'explicit_user_observed_test_current':False,
        'dispatch_authority_by_role':{'giver':True,'receiver':False},'per_role_authority_is_metadata_preview':True}
    return workflow,row,submits


def qualified(row,role,operation):
    return {'schema':'explorer_cooperative_fresh_dispatch_scope_v1','run_id':row['session_id'],
        'object_id':row['protocol_object_id'],'epoch':row['epoch'],'role':role,'operation':operation,
        'actor_id':'USER-fixture','dispatch_authority_by_role':{role:True},'per_role_authority_is_metadata_preview':False,
        'source_revalidation_required_before_native_dispatch':False,'original_native_boundary_validation_still_required':True,
        'received_nx_ns':time.monotonic_ns(),'received_clock_domain':DOMAIN,
        'root_pin':{'original_binding_sha256':digest(row.get('observed_test_binding')),
            'lineage_sha256':digest(row.get('native_grant_successors')),'ledger_sha256':digest(row.get('native_control_ledger')),
            'phase':{'protocol_state':row.get('protocol_state'),'pending_sha256':digest(row.get('pending_operation'))}}}


def test_fresh_role_receipt_allows_only_its_step_even_while_both_active_preview_is_false(tmp_path):
    workflow,row,submits=rig(tmp_path);seen=[]
    def fresh(current,role,operation):seen.append((role,operation));return qualified(current,role,operation)
    workflow.fresh_dispatch_authority=fresh
    try:
        actual=run_step(workflow,row,'giver_holding')
        assert actual['status']=='READY_FOR_OBSERVED_STEPS' and actual['protocol_state']=='ROSMASTER_HOLDING'
        assert seen==[('giver','acquire')] and len(submits)==1
        assert submits[0]['observed_authority_receipt']['per_role_authority_is_metadata_preview'] is False
        assert len(actual['dispatch_receipts'])==1 and actual['protocol_completed'] is False
    finally:workflow.close()


@pytest.mark.parametrize('changed',['preview','refresh','role','role_flag','actor','epoch','operation','schema','clock','stale',
    'binding_pin','lineage_pin','ledger_pin','phase_pin','context_changed','native_boundary'])
def test_cached_wrong_or_stale_receipt_never_falls_back_to_old_global_true_or_submits(tmp_path,changed):
    workflow,row,submits=rig(tmp_path)
    workflow.authority=lambda current:{'explicit_user_observed_test_current':True}
    def fresh(current,role,operation):
        proof=qualified(current,role,operation)
        if changed=='preview':proof['per_role_authority_is_metadata_preview']=True
        elif changed=='refresh':proof['source_revalidation_required_before_native_dispatch']=True
        elif changed=='role':proof['role']='receiver'
        elif changed=='role_flag':proof['dispatch_authority_by_role']={'receiver':True}
        elif changed=='actor':proof['actor_id']='foreign'
        elif changed=='epoch':proof['epoch']='old-epoch'
        elif changed=='operation':proof['operation']='release'
        elif changed=='schema':proof['schema']='cached-ui-preview'
        elif changed=='clock':proof['received_clock_domain']='native-nano-domain'
        elif changed=='stale':proof['received_nx_ns']-=2_000_000_001
        elif changed=='phase_pin':proof['root_pin']['phase']={'protocol_state':'OFFER','pending_sha256':'0'*64}
        elif changed.endswith('_pin'):proof['root_pin'][{'binding_pin':'original_binding_sha256','lineage_pin':'lineage_sha256','ledger_pin':'ledger_sha256'}[changed]]='0'*64
        elif changed=='context_changed':
            updated=workflow.snapshot(current['session_id']);updated['native_control_ledger']={'giver':{'receipt_id':'new-control'}};workflow._save(updated)
        else:proof['original_native_boundary_validation_still_required']=False
        return proof
    workflow.fresh_dispatch_authority=fresh
    try:
        actual=run_step(workflow,row,'giver_holding')
        assert actual['status']=='STOPPED' and not actual['dispatch_receipts'] and not submits
        assert any('cached preview' in error['error'] for error in actual['errors'])
    finally:workflow.close()


def test_service_submit_rechecks_after_workflow_and_preserves_original_native_receipt_fields(tmp_path,monkeypatch):
    from test_cooperative_service import fixture as service_fixture,session
    service,store,calls,active,contexts,client=service_fixture(tmp_path)
    try:
        initial,old_spec=session(service)
        selected=service.workflow.propose_skill({'name':'explicit wrapper policy fixture','object_description':'fixture block',
            'receiver_goal_description':'fixture box','outcome_criteria':['fixture only'],'mode':'giver_policy_receiver_manual',
            'subskills':deepcopy(old_spec['subskills'])})
        spec=selected['spec'];service.workflow.confirm_skill(spec['skill_id'],spec['version'],spec['spec_sha256'])
        row=service.workflow.create_session(spec['skill_id'],spec['version'],{'scene_id':'wrapper-fixture','object_instance_id':'wrapper-block',
            'split':'train','giver_input_mode':'policy','receiver_input_mode':'leader','execution_domain':'user_observed_physical_test'})
        row['observed_test_binding']={'actor_id':'USER-fixture'};service.workflow._save(row)
        original={'native_receipt':{'literal_source_field':'unchanged'},'fixture_only':True};gate_calls=[];native=[]
        class Transport:
            def submit_observed(self,command,authorization):
                native.append(command)
                return DispatchReceipt(command['command_id'],row['session_id'],row['protocol_object_id'],'giver',True,
                    time.monotonic_ns(),service.workflow.domain,original)
        transport=Transport();service.transports[(row['session_id'],'giver')]=transport
        proof={'fixture':'fresh-service-gate'}
        def require(current,selected,role,operation,*,expected_transport):
            gate_calls.append((role,operation));assert expected_transport is transport;return proof
        monkeypatch.setattr(service.dispatch_admission,'require',require)
        monkeypatch.setattr(service.native_controls,'_fresh',lambda current,*args:current)
        command={'command_id':'command','run_id':row['session_id'],'object_id':row['protocol_object_id'],
            'coordinator_epoch':row['epoch'],'role':'giver','operation':'acquire','reference':spec['subskills']['giver']}
        receipt=service._submit('giver',command,None)
        assert gate_calls==[('giver','acquire')] and native==[command]
        assert receipt.source['native_receipt']==original['native_receipt'] and original=={'native_receipt':{'literal_source_field':'unchanged'},'fixture_only':True}
        assert receipt.source['root_fresh_dispatch_scope']==proof
        def rejected(*args,**kwargs):raise ValueError('fixture: current Source permission lost after Workflow gate')
        monkeypatch.setattr(service.dispatch_admission,'require',rejected)
        with pytest.raises(ValueError,match='permission lost'):service._submit('giver',command,None)
        assert len(native)==1
        command['coordinator_epoch']='old-epoch'
        with pytest.raises(ValueError,match='Exact current policy'):service._submit('giver',command,None)
        assert len(native)==1
    finally:service.close();client.close()
