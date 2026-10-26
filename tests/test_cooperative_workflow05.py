"""Source-only full05 paths: real protocol/inbox, injected native owners only."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import threading
import time
import uuid

import pytest

from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS,PHASES,digest
from explorer_shared.cooperative_providers05 import CooperativeProviders,NativeSkillProvider,existing_workflow_providers
from explorer_shared.cooperative_workflow05 import CooperativeWorkflow
from explorer_shared.handover import HandoverFSM,HoldEvidence
from explorer_shared.handover_runtime import ArmObservation,CorrelatedEvidence,DispatchReceipt
from explorer_shared.cooperative_recording05 import write_original,canonical,sha_file

DOMAIN='nx:test-boot'


def reference(role):
    return {'experiment_slug':'rosmaster_keyboard_learning' if role=='giver' else 'so101_source_learning',
            'skill_id':str(uuid.uuid4()),'skill_version':1,'spec_sha256':('a' if role=='giver' else 'b')*64,
            'action_contract':deepcopy(ROLE_CONTRACTS[role]),'phase_map':{phase:'native_phase' for phase in PHASES}}


class Evidence:
    def __init__(self):self.cancelled=[];self.block=None;self.entered=threading.Event();self.release=threading.Event();self.calls=[]
    def __call__(self,statement,run,obj,role,after,timeout):
        self.calls.append((statement,run,obj,role,after))
        if statement==self.block:self.entered.set();assert self.release.wait(3)
        now=time.monotonic_ns()
        return CorrelatedEvidence(run,obj,role,after,HoldEvidence(uuid.uuid4().hex,obj,'operator',
            'operator_confirmation',statement,False,now,DOMAIN,now,DOMAIN),{'authenticated_operator':'owner'})
    def stationary_observation(self,run,obj,role,after,timeout):
        return ArmObservation(role,True,time.monotonic_ns(),DOMAIN,{'operator_stationary':True},after)
    def cancel_run(self,run):self.cancelled.append(run)


def fixture(tmp_path,mode='both_manual',models=False,execution_domain='observation_only',**overrides):
    registry=CooperativeProviders();refs={role:reference(role) for role in ROLE_CONTRACTS};calls=[]
    if models:
        for role,ref in refs.items():ref.update(model_id=role+'-fixture-model',checkpoint_sha256='c'*64)
    for role in ROLE_CONTRACTS:
        ref=refs[role]
        def inspect(ref):return {**ref,'confirmed':True,'phase_ids':['native_phase'],
            'model':{'model_id':ref.get('model_id'),'checkpoint_sha256':ref.get('checkpoint_sha256')}}
        def stop(run,_role=role):calls.append(('stop',_role,run));return {'accepted':True,'attained_stationarity':None}
        bindings={'stop':stop,**overrides.get(role,{})}
        registry.register(NativeSkillProvider(ref['experiment_slug'],role,inspect,**bindings))
    inbox=Evidence();service=CooperativeWorkflow(tmp_path,registry,evidence_inbox=inbox,clock_domain=DOMAIN)
    selected=service.propose_skill({'name':'give then put','object_description':'light rigid block',
        'receiver_goal_description':'put into box','outcome_criteria':['block in box'], 'subskills':refs,'mode':mode})
    spec=selected['spec'];service.confirm_skill(spec['skill_id'],1,spec['spec_sha256'])
    row=service.create_session(spec['skill_id'],1,{'scene_id':'table-A','object_instance_id':'block-A','split':'train',
        'giver_input_mode':'policy' if mode in ('giver_policy_receiver_manual','both_policy') else 'keyboard',
        'receiver_input_mode':'policy' if mode in ('giver_manual_receiver_policy','both_policy') else 'leader',
        'execution_domain':execution_domain})
    return service,row,inbox,calls,refs


def run_step(service,row,event):
    result=service.request_step(row['session_id'],event,expected_epoch=row['epoch'])
    service.threads[result['operation_id']].join(4)
    assert not service.threads[result['operation_id']].is_alive()
    return service.snapshot(row['session_id'])


def zone(service,row):
    raw=b'actual source calibration/camera evidence bytes';name='original-scene.txt'
    return service.register_zone(row['session_id'],{'scene_id':row['scene_id'],'method':'operator_checked_shared_zone',
        'both_robot_workspaces_checked':True,'actor_id':'owner','observing':True,
        'source_files_sha256':{name:hashlib.sha256(raw).hexdigest()}},{name:raw})


def test_full_manual_protocol_separate_clocks_native_dimensions_and_independent_hold(tmp_path):
    service,row,inbox,calls,refs=fixture(tmp_path)
    zone(service,row)
    for event in ('giver_holding','offer_ready','receiver_ready','receiver_at_object','receiver_closed'):
        current=run_step(service,row,event)
        assert current['status']=='READY_FOR_OBSERVED_STEPS'
    assert current['protocol_state']=='RECEIVER_GRASP_CHECK'  # closure != hold
    current=run_step(service,row,'receiver_hold_confirmed')
    assert current['protocol_state']=='RELEASE_AUTHORIZED'
    # Explicit new release-time operator hold renews, without changing native
    # motor settings, the protocol TTL or inferring force from a command.
    current=run_step(service,row,'release_requested')
    assert current['protocol_state']=='RELEASE'
    for event in ('giver_released','transfer_confirmed','receiver_finished'):current=run_step(service,row,event)
    assert current['protocol_completed'] and current['status']=='COMPLETED'
    assert calls==[]  # manual annotations are not hidden actuator calls
    assert len([item for item in inbox.calls if item[0]=='receiver_holds_object'])==2
    assert refs['giver']['action_contract']['dimension']==9 and refs['receiver']['action_contract']['dimension']==6


def test_stop_while_waiting_rejects_late_confirmation_and_old_epoch(tmp_path):
    service,row,inbox,calls,_=fixture(tmp_path)
    inbox.block='giver_holds_object'
    op=service.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch'])
    assert inbox.entered.wait(1)
    stopped=service.cancel_session(row['session_id']);inbox.release.set()
    service.threads[op['operation_id']].join(2)
    current=service.snapshot(row['session_id'])
    assert current['status']=='STOPPED' and not current['protocol_trace']
    assert current['epoch']!=row['epoch'] and len(calls)==2 and not stopped['physical_stop_confirmed']
    with pytest.raises(ValueError):service.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch'])


def test_wrong_phase_rejected_before_any_provider_and_policy_default_blocked(tmp_path):
    submits=[]
    service,row,inbox,calls,_=fixture(tmp_path,mode='giver_policy_receiver_manual',giver={'submit_observed':lambda *args:submits.append(args)})
    with pytest.raises(ValueError):service.request_step(row['session_id'],'release_requested',expected_epoch=row['epoch'])
    current=run_step(service,row,'giver_holding')
    assert not submits and current['status']=='STOPPED'
    assert 'outside a separate user-observed physical test' in current['errors'][0]['error']


def test_one_leader_conflict_and_no_split_reassignment(tmp_path):
    service,row,_,_,_=fixture(tmp_path)
    body={'scene_id':row['scene_id'],'object_instance_id':'other','split':'development','giver_input_mode':'keyboard','receiver_input_mode':'leader'}
    with pytest.raises(ValueError,match='cross dataset splits'):service.create_session(row['skill_id'],1,body)
    body.update(scene_id='table-B',split='train',giver_input_mode='so101_leader')
    other=service.create_session(row['skill_id'],1,body)
    assert any('ONE_LEADER' in reason for reason in service.readiness(other['session_id'])['metadata_and_recording_blockers'])


def authorized_fsm():
    fsm=HandoverFSM('obj',run_id='run',clock_domain=DOMAIN)
    def hold(statement,ident,now):return HoldEvidence(ident,'obj','operator','operator_confirmation',statement,False,now,DOMAIN,now,DOMAIN)
    for index,(event,facts,evidence) in enumerate((
        ('giver_holding',{},hold('giver_holds_object','giver',100)),
        ('offer_ready',{'zone_registered':True,'giver_stationary':True},None),
        ('receiver_ready',{'receiver_ready':True},None),('receiver_at_object',{'both_stationary':True},None),
        ('receiver_hold_confirmed',{},hold('receiver_holds_object','first',104)))):
        fsm.apply(event,event_id=str(index),now_ns=100+index,evidence=evidence,**facts)
    return fsm,hold


def test_hold_renewal_same_run_new_evidence_freshness_and_original_phase_deadline():
    fsm,hold=authorized_fsm();ttl=fsm.hold_ttl_ns;deadline=fsm.phase_started_ns
    with pytest.raises(ValueError,match='same current run'):fsm.apply('receiver_hold_confirmed',event_id='wrong-run',now_ns=105,
        evidence=hold('receiver_holds_object','new',105),run_id='other',receiver_role='receiver')
    with pytest.raises(ValueError,match='reused'):fsm.apply('receiver_hold_confirmed',event_id='reused',now_ns=105,
        evidence=fsm.hold,run_id='run',receiver_role='receiver')
    with pytest.raises(ValueError,match='Independent'):fsm.apply('receiver_hold_confirmed',event_id='stale',now_ns=ttl+200,
        evidence=hold('receiver_holds_object','stale',105),run_id='run',receiver_role='receiver')
    now=ttl+300;fsm.apply('receiver_hold_confirmed',event_id='fresh',now_ns=now,
        evidence=hold('receiver_holds_object','fresh',now),run_id='run',receiver_role='receiver')
    assert fsm.hold_ttl_ns==ttl and fsm.phase_started_ns==deadline and fsm.release_permitted(now)
    with pytest.raises(ValueError,match='unexpired protocol phase'):fsm.apply('receiver_hold_confirmed',event_id='expired-phase',
        now_ns=deadline+fsm.phase_timeout_ns+1,evidence=hold('receiver_holds_object','last',deadline+fsm.phase_timeout_ns+1),
        run_id='run',receiver_role='receiver')


class FakeRecorder:
    def __init__(self):self.starts=[];self.finishes=[]
    def start(self,context,refs):self.starts.append((context,refs));return {'recording_id':uuid.uuid4().hex}
    def finish(self,outcome,refs):self.finishes.append((outcome,refs));return {'state':'SEALING'}


def test_stop_during_native_record_start_revokes_without_waiting_for_disk_or_late_start(tmp_path):
    entered,release=threading.Event(),threading.Event();finished=[]
    def start(context):entered.set();assert release.wait(3);return {'recording_id':'native-original'}
    def finish(native,outcome):finished.append((native,outcome));return {'saved':True}
    service,row,_,calls,_=fixture(tmp_path,giver={'record_start':start,'record_finish':finish})
    recorder=FakeRecorder();service.recorder=recorder;errors=[]
    def recording():
        try:service.record_start(row['session_id'])
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=recording);thread.start();assert entered.wait(1)
    with pytest.raises(ValueError,match='pending'):service.record_start(row['session_id'])
    service.cancel_session(row['session_id']);assert len(calls)==2  # no blocked file-lock STOP
    release.set();thread.join(3)
    assert not recorder.starts and len(finished)==1 and errors
    assert not service.snapshot(row['session_id'])['recording_id']


def test_native_inspection_unavailable_structured_and_transfer_not_rebranded_as02(tmp_path):
    service,row,_,_,refs=fixture(tmp_path)
    provider=service.registry.provider('giver',refs['giver'])
    service.registry.providers[('giver',provider.experiment_slug)]=replace(provider,inspect_skill=lambda ref:(_ for _ in ()).throw(OSError('offline')))
    assert 'NATIVE_SKILL_INSPECTION_FAILED' in service.readiness(row['session_id'])['metadata_and_recording_blockers'][0]
    calls=[]
    def native(*args,**kwargs):calls.append(kwargs);return {}
    registry=existing_workflow_providers(None,rosmaster_workflow=native,rosmaster_aggregate=native,rosmaster_training=native,
        lab_workflow=native,lab_readiness=native,lab_aggregate=native)
    transfer={**refs['giver'],'experiment_slug':'cross_embodiment_transfer'}
    assert 'EXPLICIT_BINDING' in registry.inspect('giver',transfer)['blockers'][0]
    assert calls==[]


def sidecar_and_native_sources(service,row,refs,*,incomplete=False):
    ident=uuid.uuid4().hex;folder=service.root/'recordings'/ident;folder.mkdir(parents=True)
    raw=b'original fixture bytes; not physical evidence';write_original(folder/'frame.jpg',raw)
    native_refs={};started={}
    for role in ROLE_CONTRACTS:
        episode=role+'-'+row['session_id'];started[role]={'recording_id':episode}
        report={**refs[role],'action_contract':ROLE_CONTRACTS[role],'admitted_for_training':True,
            'eligible_as_successful_bc_expert':True,'cooperative_parent_session_id':row['session_id'],
            'split':row['split'],'original_episode_id':episode,'execution_domain':'SIMULATION',
            'source_bytes_sha256':hashlib.sha256(raw).hexdigest()}
        path=folder/(role+'.json');write_original(path,canonical(report));native_refs[role]={'path':str(path),'sha256':sha_file(path)}
        provider=service.registry.provider(role,refs[role])
        def inspect(source):
            if sha_file(source['path'])!=source['sha256']:raise ValueError('Native source changed')
            return json.loads(open(source['path']).read())
        service.registry.providers[(role,provider.experiment_slug)]=replace(provider,inspect_episode=inspect)
    source={'schema':'explorer_cooperative_raw_recording_v1','state':'SEALED','recording_id':ident,
        'context':{**row,'source_execution_domain':'SIMULATION'},
        'source_files':{'frame.jpg':{'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}},
        'native_role_sources':started,'block_reasons':['FAKE_DROP'] if incomplete else [],'dropped_packets':0,
        'samples':[{'late_after_finish':False,'roles':{'giver':{'frame_id':'nano-1'},'receiver':{'frame_id':'pi-1'}}}]}
    write_original(folder/'manifest.json',canonical(source))
    body={'outcome':'success','whole_episode_seen':True,'object_instance_id':row['object_instance_id'],
        'phase_outcomes':{phase:'success' for phase in PHASES},'native_episode_refs':native_refs,
        'reviewed_by':'client-untrusted-actor'}
    return ident,folder/'manifest.json',body


def test_native_paired_review_aggregate_rechecks_originals_and_never_merges_actions(tmp_path):
    service,row,_,_,refs=fixture(tmp_path);zone(service,row)
    ident,path,body=sidecar_and_native_sources(service,service.snapshot(row['session_id']),refs)
    review=service.review_recording(row['session_id'],ident,body,authenticated_actor='trusted-owner',manifest_path=path)
    assert review['admitted_for_training'] and review['reviewed_by']=='trusted-owner'
    with pytest.raises(ValueError,match='already reviewed'):
        service.review_recording(row['session_id'],ident,body,authenticated_actor='trusted-owner',manifest_path=path)
    with pytest.raises(ValueError,match='train and development'):service.build_aggregate(row['skill_id'],1)
    dev=service.create_session(row['skill_id'],1,{'scene_id':'table-DEV','object_instance_id':'block-DEV','split':'development',
        'giver_input_mode':'keyboard','receiver_input_mode':'leader'});zone(service,dev)
    ident2,path2,body2=sidecar_and_native_sources(service,service.snapshot(dev['session_id']),refs)
    service.review_recording(dev['session_id'],ident2,body2,authenticated_actor='trusted-owner',manifest_path=path2)
    aggregate=service.build_aggregate(row['skill_id'],1)
    assert not aggregate['manifest']['action_spaces_merged'] and not aggregate['jobs_enqueued']
    assert aggregate['manifest']['train_recordings']==[ident]
    native_path=body['native_episode_refs']['giver']['path'];open(native_path,'ab').write(b'changed')
    with pytest.raises(ValueError,match='native admission/source evidence changed'):service.build_aggregate(row['skill_id'],1)


def test_complete_looking_operator_review_cannot_promote_incomplete_source_sidecar(tmp_path):
    service,row,_,_,refs=fixture(tmp_path);zone(service,row)
    ident,path,body=sidecar_and_native_sources(service,service.snapshot(row['session_id']),refs,incomplete=True)
    review=service.review_recording(row['session_id'],ident,body,authenticated_actor='owner',manifest_path=path)
    assert not review['admitted_for_training'] and 'DUAL_CAMERA_SIDECAR_SOURCE_INCOMPLETE' in review['reasons']


def metric_zone(service,row):
    matrix=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
    source={role:{'base_from_zone':matrix,'scene_id':row['scene_id'],'role':role,'translation_unit':'m',
        'physical_registration_measured':True,'fixture_not_physical_proof':True} for role in ROLE_CONTRACTS}
    raw=canonical(source);name='source-registration.json'
    return service.register_zone(row['session_id'],{'scene_id':row['scene_id'],'method':'measured_frame_registration',
        'both_robot_workspaces_checked':True,'actor_id':'fixture-owner','observing':True,
        'source_files_sha256':{name:hashlib.sha256(raw).hexdigest()},'base_from_zone':{role:matrix for role in ROLE_CONTRACTS},
        'transform_provenance':{role:{'source_file':name,'json_pointer':'/'+role} for role in ROLE_CONTRACTS}},{name:raw})


def policy_capabilities(submit):
    return {'predict_shadow':lambda *args:{},'observe_state':lambda:{'current_native_owner_ready':True,'source_receipt':{'fixture':True}},
            'native_dispatch_receipts':True,'authoritative_release_deadline':True,'submit_observed':submit}


def test_native_http_acceptance_cannot_be_promoted_to_dispatch_or_hold(tmp_path):
    submits=[]
    def ack(command,authorization):submits.append(command);return {'accepted':True,'command_id':command['command_id']}
    service,row,_,_,_=fixture(tmp_path,mode='giver_policy_receiver_manual',models=True,
        execution_domain='user_observed_physical_test',giver=policy_capabilities(ack))
    metric_zone(service,row);service.authority=lambda row:{'explicit_user_observed_test_current':True,'source':'mock-only'}
    current=run_step(service,row,'giver_holding')
    assert len(submits)==1 and current['status']=='STOPPED'
    assert not current['dispatch_receipts'] and not current['protocol_trace']
    assert current['dispatch_attempts'][0]['returned_receipt']['accepted']
    assert 'API ACK is insufficient' in current['errors'][0]['error']


def test_native_dispatch_reply_after_stop_retained_but_cannot_advance_protocol(tmp_path):
    entered,release=threading.Event(),threading.Event()
    def dispatch(command,authorization):
        entered.set();assert release.wait(3)
        return DispatchReceipt(command['command_id'],command['run_id'],command['object_id'],'giver',True,
            time.monotonic_ns(),DOMAIN,{'stage':'mock_host_publication_only','attainment':None})
    service,row,_,_,_=fixture(tmp_path,mode='giver_policy_receiver_manual',models=True,
        execution_domain='user_observed_physical_test',giver=policy_capabilities(dispatch))
    metric_zone(service,row);service.authority=lambda row:{'explicit_user_observed_test_current':True}
    result=service.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch']);assert entered.wait(1)
    service.cancel_session(row['session_id']);release.set();service.threads[result['operation_id']].join(3)
    current=service.snapshot(row['session_id'])
    assert current['status']=='STOPPED' and not current['dispatch_receipts'] and not current['protocol_trace']
    assert current['dispatch_attempts'][0]['late_after_epoch_revocation']
    assert current['dispatch_attempts'][0]['returned_receipt']['source']['attainment'] is None


def test_source_bound_metric_registration_and_shadow_separate_native_predictions(tmp_path):
    def predict(ref,request):return {'dispatch_performed':False,'checkpoint_sha256':ref['checkpoint_sha256'],
        'action_contract':ref['action_contract'],'source_evidence':{'actual_fixture_history_sha256':'d'*64},
        'actions':[[0.0]*ref['action_contract']['dimension']]}
    service,row,_,_,_=fixture(tmp_path,models=True,giver={'predict_shadow':predict},receiver={'predict_shadow':predict})
    receipt=service.shadow(row['session_id'])
    assert len(receipt['native_outputs']['giver']['actions'][0])==9
    assert len(receipt['native_outputs']['receiver']['actions'][0])==6
    assert not receipt['dispatch_performed'] and not receipt['physical_mastery_qualified']
    matrix=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
    with pytest.raises(ValueError,match='original measured registration'):
        service.register_zone(row['session_id'],{'scene_id':row['scene_id'],'method':'measured_frame_registration',
            'both_robot_workspaces_checked':True,'actor_id':'owner','observing':True,
            'source_files_sha256':{'unrelated.json':hashlib.sha256(b'{}').hexdigest()},
            'base_from_zone':{'giver':matrix,'receiver':matrix}},{'unrelated.json':b'{}'})


def test_release_cannot_reset_an_expired_grasp_check_phase_with_new_fresh_hold(tmp_path):
    service,row,inbox,calls,_=fixture(tmp_path);zone(service,row)
    for event in ('giver_holding','offer_ready','receiver_ready','receiver_at_object'):run_step(service,row,event)
    before=service.snapshot(row['session_id']);fsm=service._fsm(before)
    now=[fsm.phase_started_ns+fsm.phase_timeout_ns-100];service.clock=lambda:now[0]
    class LateFresh(Evidence):
        def __call__(self,statement,run,obj,role,after,timeout):
            now[0]=fsm.phase_started_ns+fsm.phase_timeout_ns+1
            return CorrelatedEvidence(run,obj,role,after,HoldEvidence('fresh-after-phase-expired',obj,'operator',
                'operator_confirmation',statement,False,now[0],DOMAIN,now[0],DOMAIN),{'operator':'fixture'})
        def stationary_observation(self,run,obj,role,after,timeout):
            return ArmObservation(role,True,now[0],DOMAIN,{'operator':'fixture'},after)
    service.inbox=LateFresh();current=run_step(service,row,'release_requested')
    assert current['status']=='STOPPED' and current['protocol_trace']==before['protocol_trace']
    assert 'phase deadline expired' in current['errors'][0]['error'] and not current['dispatch_receipts']


def test_two_missing_native_domains_are_unknown_not_equal_simulation_or_real(tmp_path):
    service,row,_,_,refs=fixture(tmp_path);zone(service,row)
    ident,path,body=sidecar_and_native_sources(service,service.snapshot(row['session_id']),refs)
    for reference in body['native_episode_refs'].values():
        native_path=reference['path'];report=json.loads(open(native_path).read());report.pop('execution_domain')
        open(native_path,'wb').write(canonical(report));reference['sha256']=sha_file(native_path)
    review=service.review_recording(row['session_id'],ident,body,authenticated_actor='owner',manifest_path=path)
    assert not review['admitted_for_training']
    assert 'ORIGINAL_ROLE_DOMAINS_NOT_EXPLICITLY_BOUND_TO_COOPERATIVE_SOURCE' in review['reasons']
