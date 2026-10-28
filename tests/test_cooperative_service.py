"""Real temporary STORE/public source APIs, mock authenticated node HTTP only."""
import json
import sys
import time
from copy import deepcopy

import httpx
import pytest

from explorer.store import Store
from explorer import skill_workflow as mobile,lab_skill_workflow as lab
from explorer.cooperative_service import CooperativeService
from explorer_shared.native_capture_identity import normalize_capture_context,native_context_sha256
from explorer_shared.contracts import digest


class Source:
    def __init__(self,role):self.role=role;self.samples=0
    def snapshot(self,cursor=0):
        self.samples+=1;tick=1_000_000_000+self.samples
        return {'role':self.role,'camera_id':'nano-rgbd' if self.role=='giver' else 'pi-overview',
            'frame_id':self.role+'-'+str(self.samples),'jpeg':b'\xff\xd8original-cached-fixture\xff\xd9',
            'telemetry':b'{"original_fake_native_receipt":null}',
            'source_monotonic_ns':tick,'source_clock_domain':self.role+':separate-fake-boot','next_sequence':cursor,
            'exposure_timestamp_verified':False}


def fixture(tmp_path):
    from explorer_shared.so101_station import _record_context
    store=Store(tmp_path/'state');calls=[];active={'nano.test':None,'pi.test':None};contexts={};capture_request={};sealed={}
    domain='pi:fake-capture-boot';boot='fake-capture-boot'
    profiles={role:{'verified_on_connected_hardware':True,'calibration_sha256':('c' if role=='leader' else 'd')*64}
        for role in ('leader','follower')}
    def source_context():
        body=deepcopy(capture_request['record_start_body'])
        body['context']['cooperative05_capture_permission']=deepcopy(capture_request['permission'])
        return _record_context(body)
    def request(req):
        role='nano.test' if req.url.host=='nano.test' else 'pi.test'
        assert req.headers['authorization']=='Bearer '+('nano-token-private-fixture' if role=='nano.test' else 'pi-token-private-fixture')
        body=json.loads(req.content) if req.content else None;calls.append((req.method,role,req.url.path,body))
        path=req.url.path
        if req.method=='GET':
            if path=='/native-capture/capabilities':result={'metadata_skill_context_supported':True,'native_owner_ready':True,'action_dimension':9}
            elif path=='/so101-capture/capabilities':result={'cooperative_permission_recording_supported':True}
            elif path=='/native-capture/status':
                context=normalize_capture_context(contexts[role]['skill_context']) if role in contexts else None
                result={'active_recording_id':active[role],'capture':{'schema':'explorer_native_capture_status_v1',
                    'recording_id':active[role],'state':'RECORDING' if active[role] else 'IDLE',
                    'source_clock_domain':'nano:fake-boot','source_boot_id':'fake-boot',
                    'source_context_sha256':native_context_sha256(context) if context else None,'project_id':context.get('project_id') if context else None}}
            elif path=='/legacy/explorer/retarget/reference':result={'measured':False,'valid_fixture_reference':True}
            elif path=='/station/status':result={'available':True,'mode':'teleoperation','recording_identity_fence':True,
                'control_epoch':'fake-source-epoch','roles':deepcopy(profiles),'measurements':{'clock_domain':domain},
                'recording':{'episode_id':active[role],'clock_domain':domain,'boot_id':boot,
                    'source_context':source_context(),'demonstration_actor':capture_request['actor_id']} if active[role] else None}
            elif path=='/so101-capture/status':
                result={'schema':'explorer_so101_source_capture_v1','recording_id':active[role],
                    'recovery_required':False,'closed':False,'capture':None}
                if active[role]:result['capture']={'recording_id':active[role],'state':'RECORDING',
                    'actor_id':capture_request['actor_id'],'native_control_epoch':'fake-source-epoch',
                    'source_boot_id':boot,'source_clock_domain':domain,'source_context':source_context(),
                    'calibration_sha256':{role:profile['calibration_sha256'] for role,profile in profiles.items()},
                    'original_start_receipt':{'episode_id':active[role]}}
            elif path.startswith(('/so101-capture/recordings/','/native-capture/recordings/')) and path.endswith('/manifest'):
                identity=path.split('/')[-2]
                if identity not in sealed:return httpx.Response(404,json={'detail':'Fake source archive not sealed yet'})
                result=deepcopy(sealed[identity])
            elif path=='/station/observed-test/status':
                row=service.workflow.snapshot()['sessions'][-1];spec=service.workflow._spec_for(row)
                result={'execution_permission':True,'source_stamp':{'boot_id':boot,'clock_domain':domain},'grant':{'execution_permission':True,
                    'grant_id':'fake-source-current-grant','control_epoch':'fake-source-epoch',
                    'native_mode':'teleoperation','clock_domain':domain,'boot_id':boot,'follower_calibration_sha256':'d'*64,
                    'actor_id':row['observed_test_binding']['actor_id'],
                    'binding':service.observed_setup.receiver_binding(row,spec)}}
            else:raise AssertionError('No actual device/status path expected')
        else:
            assert '/record/' in path, 'No control/motor endpoint belongs to this integration fixture'
            if path.endswith('/start'):
                active[role]=('a' if role=='nano.test' else 'b')*32
                contexts[role]=deepcopy(body['record_start_body'] if path.startswith('/so101-capture/') else body)
                if path.startswith('/so101-capture/'):capture_request.update(deepcopy(body))
                result={'recording_id' if role=='nano.test' or path.startswith('/so101-capture/') else 'episode_id':active[role]}
            elif path.endswith('/stage'):
                assert body['recording_id']==active[role];result={'source_annotation':True}
            elif path.endswith('/finish'):
                assert body.get('recording_id',body.get('episode_id'))==active[role]
                if path.startswith('/so101-capture/'):
                    identity=active[role];context=source_context();assert body['actor_id']==capture_request['actor_id']
                    lifecycle={'state':'completed','recording_id':identity,'actor_id':body['actor_id'],
                        'source_context':context,'native_control_epoch':'fake-source-epoch','source_boot_id':boot,
                        'source_clock_domain':domain,'calibration_sha256':{role:p['calibration_sha256'] for role,p in profiles.items()},
                        'original_start_receipt':{'episode_id':identity},'original_finish_receipt':{'episode_id':identity,'sha256':'e'*64}}
                    result={'schema':'explorer_so101_source_capture_v1','project_id':'04','recording_id':identity,
                        'action_contract_id':'so101_follower_calibrated_position6_v1','state':'completed',
                        'source_boot_id':boot,'source_clock_domain':domain,'source_context':context,'lifecycle':lifecycle,
                        'original_manifest':{'sha256':'e'*64},'original_archive':{'sha256':'f'*64},
                        'operator_outcome':body['outcome'],'physical_success_inferred':False,'automatic_import_or_training':False}
                    sealed[identity]=deepcopy(result)
                else:
                    identity=active[role];context=normalize_capture_context(contexts[role]['skill_context'])
                    outcome=body['outcome_evidence']
                    result={'schema':'explorer_native_embodied_capture_manifest_v1','recording_id':identity,
                        'project_id':context['project_id'],'state':'completed','skill_context':context,
                        'embodied_episode':{'episode_id':identity,'clock_domains':{'nano:fake-boot':{'boot_id':'fake-boot'}},
                            'recording':{'status':'completed','operator_outcome':outcome.get('operator_outcome')},
                            'outcomes':deepcopy(outcome.get('outcomes',{}))},
                        'embodied_source_files_sha256':{'sources/outcome.json':native_context_sha256(outcome)},
                        'physical_success_inferred':False,'controller_settings_written':False}
                    sealed[identity]=deepcopy(result)
                    result={'active_recording_id':None,'capture':{'state':'SEALING'}}
                active[role]=None
            else:raise AssertionError('Unknown native metadata operation')
        return httpx.Response(200,json=result)
    client=httpx.Client(transport=httpx.MockTransport(request))
    config={'nodes':[{'id':'nano','host':'nano.test','port':8092,'token':'nano-token-private-fixture'},
                     {'id':'pi','host':'pi.test','port':8092,'token':'pi-token-private-fixture'}]}
    before=set(sys.modules);service=CooperativeService(config,store,client=client)
    assert calls==[] and 'torch' not in set(sys.modules)-before
    # Replace GET-only cache adapters with explicit offline source fixtures;
    # HTTP metadata paths above still execute the real authenticated client.
    service.sources={role:Source(role) for role in ('giver','receiver')}
    service.recorder.sources=service.sources
    selected=mobile.create_skill(store,{'preset':'sock_to_basket'})
    mobile.confirm_skill(store,selected['spec']['skill_id'],1,selected['spec_sha256'])
    selected=lab.propose(store,{'template_id':'pick_object_to_container'})
    lab.confirm(store,selected['spec']['skill_id'],1,{'spec_sha256':selected['spec_sha256']})
    return service,store,calls,active,contexts,client


def session(service):
    snapshot=service.snapshot();choices=snapshot['native_choices']
    result=service.command('propose_skill',{'name':'native giver to receiver','object_description':'light block',
        'receiver_goal_description':'put into box','outcome_criteria':['block in box'],
        'giver_choice_id':choices['giver'][0]['choice_id'],'receiver_choice_id':choices['receiver'][0]['choice_id'],
        'giver_phase_map':{'PREPARE':choices['giver'][0]['phase_ids'][0]},
        'receiver_phase_map':{'PREPARE':choices['receiver'][0]['phase_ids'][0]}},authenticated_actor='trusted-owner')['result']
    spec=result['spec'];service.command('confirm_skill',{'skill_id':spec['skill_id'],'version':1,'spec_sha256':spec['spec_sha256']},authenticated_actor='trusted-owner')
    variants={role:next(item['variant_id'] for item in choices[role][0]['native_variants'] if item['split']=='train') for role in ('giver','receiver')}
    row=service.command('create_session',{'skill_id':spec['skill_id'],'version':1,'scene_id':'explicit-scene','object_instance_id':'original-block',
        'spec_sha256':spec['spec_sha256'],
        'split':'train','giver_input_mode':'keyboard','receiver_input_mode':'leader','native_variants':variants,
        'stationary_chassis_confirmed':True},authenticated_actor='trusted-owner')['result']
    return row,spec


def test_actual_confirmed_native_choice_ids_and_bootstrap_no_network_no_motor_no_model_import(tmp_path):
    service,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        state=service.snapshot();assert len(state['native_choices']['giver'])==len(state['native_choices']['receiver'])==1
        assert state['native_choices']['giver'][0]['reference']['action_contract']['dimension']==9
        assert state['native_choices']['receiver'][0]['reference']['action_contract']['dimension']==6
        assert not state['physical_execution_default'] and not any(state['model_adapters_bound'].values())
        assert state['missing_native_providers']==['cross_embodiment_transfer'] and calls==[]
        with pytest.raises(ValueError,match='current confirmed native'):
            service.command('propose_skill',{'giver_choice_id':'invented','receiver_choice_id':'invented'},authenticated_actor='owner')
    finally:service.close();client.close()


def test_real_public_source_contexts_and_authenticated_metadata_recording_preserve_parent_split_and_unknown_outcomes(tmp_path):
    service,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(service);base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
        # Explicit fake observed binding for this metadata-only source fixture.
        # No authorize/start/control endpoint is invoked by this test.
        row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
        service.workflow._save(row)
        result=service.command('record_start',base,authenticated_actor='trusted-owner')['result'];identity=result['recording_id']
        nano=contexts['nano.test']['skill_context'];pi=contexts['pi.test']
        assert nano['cooperative_parent_session_id']==row['session_id']==pi['context']['cooperative_parent_session_id']
        assert nano['cooperative_source_execution_domain']==pi['context']['cooperative_source_execution_domain']=='REAL'
        assert nano['split']['partition']==pi['split']=='train'
        assert nano['skill_id']==spec['subskills']['giver']['skill_id'] and pi['project_id']=='04'
        assert not result['native_source_errors'] and all(value for value in active.values())
        with pytest.raises(ValueError,match='recording changed'):
            service.command('record_finish',{**base,'recording_id':'stale'},authenticated_actor='trusted-owner')
        service.command('record_stage',{**base,'recording_id':identity,'phase_id':'PREPARE'},authenticated_actor='trusted-owner')
        service.command('record_finish',{**base,'recording_id':identity,'outcome':{'status':'success'}},authenticated_actor='trusted-owner')
        assert not any(active.values()) and service.recorder.done.wait(3)
        finishes=[call for call in calls if call[2].endswith('/finish')]
        assert finishes[0][3]['outcome_evidence']['operator_outcome']=='unknown'
        assert finishes[1][3]['outcome']=='unknown'  # whole task success didn't label either native role
        assert all('/record/' in path for method,_,path,_ in calls if method=='POST')
    finally:service.close();client.close()


def test_zone_originals_server_resolved_and_stale_epoch_cannot_stop_new_session(tmp_path):
    service,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(service);base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
        result=service.command('register_zone',{**base,'observing':True,'both_robot_workspaces_checked':True,
            'actor_id':'untrusted-client','source_files_sha256':{'fake':'bad'},'path':'/etc/passwd'},authenticated_actor='trusted-owner')['result']
        assert result['actor_id']=='trusted-owner' and not result['metric_registration_available']
        path,media=service.artifact('zone',result['zone_id'],'giver');assert media=='image/jpeg' and path.read_bytes().startswith(b'\xff\xd8')
        with pytest.raises(ValueError):service.artifact('zone','../../outside','giver')
        response=service.command('stop',base,authenticated_actor='trusted-owner')
        assert response['snapshot']['selected_session']['status']=='STOPPED'
        with pytest.raises(ValueError,match='epoch changed'):service.command('record_start',base,authenticated_actor='trusted-owner')
        assert not calls  # no owned native transport -> STOP has no unrelated side effect
    finally:service.close();client.close()


def test_closing_only_finishes_owned_metadata_never_starts_stops_resumes_or_releases_controls(tmp_path):
    service,store,calls,active,contexts,client=fixture(tmp_path)
    row,_=session(service);service.command('record_start',{'session_id':row['session_id'],'expected_epoch':row['epoch']},authenticated_actor='owner')
    service.close();client.close()
    assert not any(active.values())
    assert all('/record/' in path for method,_,path,_ in calls if method=='POST')
    with pytest.raises(ValueError):service.command('step',{},authenticated_actor='owner')


def test_real_durable_operator_inbox_confirmation_uses_server_actor_and_exact_payload(tmp_path):
    service,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,_=session(service)
        operation=service.command('step',{'session_id':row['session_id'],'expected_epoch':row['epoch'],'event':'giver_holding'},authenticated_actor='owner')['result']
        deadline=time.monotonic()+2
        while not service.inbox.pending() and time.monotonic()<deadline:time.sleep(.01)
        challenge=service.inbox.pending()[0]
        service.command('confirm_evidence',{'challenge_id':challenge['challenge_id'],'affirmed':True,'observing':True,
            'actor_id':'forged-client-identity'},authenticated_actor='trusted-owner')
        service.workflow.threads[operation['operation_id']].join(2)
        selected=service.snapshot(row['session_id'])['selected_session']
        assert selected['protocol_state']=='ROSMASTER_HOLDING' and 'offer_ready' in selected['legal_events']
        proof=selected['observation_receipts'][0]['source_receipt']['confirmation']
        assert proof['authenticated_actor']=='trusted-owner' and proof['state']=='consumed'
        assert calls==[]
    finally:service.close();client.close()


def test_broken_checkpoint_candidate_does_not_block_manual_skill_choices_or_create_a_model(tmp_path):
    service,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        selected=mobile.list_skills(store)[0]
        job=store.create_job('rosmaster_skill_train','02',{'skill_id':selected['spec']['skill_id'],'skill_version':1,'spec_sha256':selected['spec_sha256']})
        store.complete_job(job['id'],{'trained':True,'weights_changed':True,'save_reload_passed':True,'optimizer_steps':8,
            'checkpoint_sha256':{'missing':'f'*64},'checkpoint_dir':str(store.root/'absent')})
        state=service.snapshot()
        assert state['native_choices']['giver'][0]['models']==[] and job['id'] in state['model_source_errors']
        assert not calls
    finally:service.close();client.close()


def test_training_requires_real_paired_source_admission_and_never_creates_orphan_role_jobs(tmp_path):
    service,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(service)
        with pytest.raises(ValueError):service.train(spec['skill_id'],1,{'receiver':{'backend':'unknown'}})
        with pytest.raises(ValueError,match='train and development'):service.train(spec['skill_id'],1)
        assert store.jobs()==[] and calls==[]
    finally:service.close();client.close()


def test_partial_native_queue_reservation_recovers_committed_job_after_lost_return_without_duplicate(tmp_path,monkeypatch):
    """Only queue orchestration is mocked, never source/model qualification."""
    service,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(service);native_config={'skill_workflow':{'skill_id':spec['subskills']['giver']['skill_id'],
            'skill_version':1,'spec_sha256':spec['subskills']['giver']['spec_sha256']},'native_dimension':9}
        monkeypatch.setattr(service.workflow,'build_aggregate',lambda *args:{'aggregate_manifest_sha256':'c'*64})
        monkeypatch.setattr(service.workflow,'training_requests',lambda *args:{'ready':True,'blockers':[],
            'requests':{'giver':{'kind':'rosmaster_skill_train','project_id':'02','config':native_config},'receiver':{'operation':'native_owner'}}})
        attempts=[]
        def lost_after_commit(*args,**kwargs):
            attempts.append('giver');store.create_job('rosmaster_skill_train','02',native_config)
            raise OSError('Return lost AFTER original owner transaction committed')
        def receiver_queue(*args,**kwargs):
            attempts.append('receiver');return store.create_job('so101_skill_train','04',{'native_dimension':6,'request_id':args[3]['request_id']})
        monkeypatch.setattr(mobile,'enqueue_training',lost_after_commit);monkeypatch.setattr(lab,'enqueue_training',receiver_queue)
        first=service.train(spec['skill_id'],1)
        assert not first['complete_native_reservation'] and set(first['jobs'])=={'receiver'}
        second=service.train(spec['skill_id'],1)
        assert second['complete_native_reservation'] and set(second['jobs'])=={'giver','receiver'}
        assert attempts==['giver','receiver'] and len(store.jobs())==2
        assert not second['action_spaces_merged'] and not second['coordinator_trained'] and calls==[]
    finally:service.close();client.close()


def test_global_stop_uses_pre_cancel_native_scope_and_invalidates_receiver_setup(tmp_path,monkeypatch):
    service,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(service);seen=[]
        def cleanup(previous,*,actor,reason='EXPLICIT_COOPERATIVE_STOP'):
            assert service.workflow.snapshot(previous['session_id'])['status']=='STOPPED'
            assert previous['epoch']==row['epoch'] and actor=='trusted-owner'
            seen.append(previous);return {'roles':{},'dual_hold_interlocks_unarmed':False}
        monkeypatch.setattr(service.native_controls,'cancel_owned_sessions',cleanup)
        result=service.command('stop',{'session_id':row['session_id'],'expected_epoch':row['epoch']},authenticated_actor='trusted-owner')['result']
        assert len(seen)==1 and result['native_owned_session_cleanup']['dual_hold_interlocks_unarmed'] is False
        assert service.receiver_setup.generations[row['session_id']]==1
        assert all('/record/' in path for method,host,path,body in calls if method=='POST')
    finally:service.close();client.close()
