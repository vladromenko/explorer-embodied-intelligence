"""Root known DATA intent / lost HTTP ACK / restart; fake node API only."""
from copy import deepcopy
import json

import httpx
import pytest

from explorer.cooperative_service import CooperativeService
from test_cooperative_service import fixture,session


def reconcile_body(root,base):
    row=root.workflow.snapshot(base['session_id'])
    return {**base,'capture_attempt_id':row['native_capture_start_intents']['receiver']['attempt_id']}


def lost_ack(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client=fixture(tmp_path)
    row,spec=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
    root.workflow._save(row);original_request=root._request
    def factory(role):
        send=original_request(role)
        def request(method,path,body):
            returned=send(method,path,body)
            if path=='/so101-capture/record/start':raise httpx.ReadTimeout('FAKE lost ACK AFTER original data writer started')
            return returned
        return request
    monkeypatch.setattr(root,'_request',factory)
    root.producers['receiver'].request=factory('receiver')
    base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
    result=root.command('record_start',base,authenticated_actor='trusted-owner')['result']
    return root,store,calls,active,contexts,client,base,result,original_request


def test_lost_start_reply_is_durable_no_retry_and_explicit_reconcile_appends_sidecar(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client,base,result,original=lost_ack(tmp_path,monkeypatch)
    try:
        assert active['pi.test'] and root.producers['receiver'].active is None and 'receiver' in result['native_source_errors']
        row=root.workflow.snapshot(base['session_id']);pointer=row['native_capture_start_intents']['receiver']
        folder,intent=root.capture_starts._load(pointer)
        assert intent['record_start_body']['context']['source_capture_attempt_id']==pointer['attempt_id']
        assert 'leader_calibration_sha256' not in intent['original_grant_literal']
        assert set(intent['source_identity']['calibration_sha256'])=={'leader','follower'}
        assert root.capture_starts.projection(row)['state']=='PENDING_DATA_RECONCILIATION'
        count=len(calls)
        with pytest.raises(ValueError,match='pending'):root.command('record_start',base,authenticated_actor='trusted-owner')
        assert len(calls)==count  # no second role starts before pending preflight
        original_bytes=(root.recorder.folder/'sources/native-producer-start.json').read_bytes()
        with pytest.raises(ValueError,match='source recording|known receiver|already user-observed'):
            root.producers['receiver'].record_start(deepcopy(row['proposed_native_source_contexts']['receiver']))
        before=len(calls)
        with pytest.raises(ValueError,match='original authenticated DATA'):root.command('record_reconcile',reconcile_body(root,base),authenticated_actor='foreign')
        assert len(calls)==before
        reply=root.command('record_reconcile',reconcile_body(root,base),authenticated_actor='trusted-owner')['result']
        assert reply['recording_metadata_attached'] and not reply['control_authority_adopted'] and not reply['native_commands_dispatched']
        assert (root.recorder.folder/'sources/native-producer-start.json').read_bytes()==original_bytes
        assert (root.recorder.folder/'sources/native-producer-reconciled-receiver.json').is_file()
        assert root.workflow.snapshot(base['session_id'])['recording_source_refs']['receiver']['recording_id']==active['pi.test']
        assert root.capture_starts.projection(root.workflow.snapshot(base['session_id']))['state']=='ACKNOWLEDGED'
        assert len([c for c in calls if c[0]=='POST' and c[2]=='/so101-capture/record/start'])==1
        assert all('/record/' in path for method,_,path,_ in calls if method=='POST')
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
        assert active['pi.test'] is None
    finally:root.close();client.close()


def test_restart_reconciles_only_old_data_and_finishes_unknown_without_control_resume(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client,base,result,original=lost_ack(tmp_path,monkeypatch)
    config=deepcopy(root.config);root.close();assert active['pi.test']
    recovered=CooperativeService(config,store,client=client)
    try:
        row=recovered.workflow.snapshot(base['session_id']);assert row['status'] in ('STOPPED','INTERRUPTED')
        body={'session_id':row['session_id'],'expected_epoch':row['epoch']};before=len(calls)
        assert recovered.capture_starts.projection(row)['state']=='PENDING_DATA_RECONCILIATION'
        assert len(calls)==before  # read-only projection never adopts a source
        reply=recovered.command('record_reconcile',reconcile_body(recovered,body),authenticated_actor='trusted-owner')['result']
        assert not reply['recording_metadata_attached'] and reply['own_source_finished_unknown']
        assert active['pi.test'] is None and recovered.workflow.snapshot(row['session_id'])['status'] in ('STOPPED','INTERRUPTED')
        mutations=[(path,body) for method,_,path,body in calls[before:] if method=='POST']
        assert len(mutations)==1 and mutations[0][0]=='/so101-capture/record/finish' and mutations[0][1]['outcome']=='unknown'
    finally:recovered.close();client.close()


def test_changed_intent_never_adopts_source_and_another_root_writer_cannot_mutate_session(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client,base,result,original=lost_ack(tmp_path,monkeypatch)
    try:
        before=root.workflow.snapshot(base['session_id'])
        with pytest.raises(BlockingIOError):CooperativeService(root.config,store,client=client)
        assert root.workflow.snapshot(base['session_id'])==before
        pointer=before['native_capture_start_intents']['receiver'];folder,intent=root.capture_starts._load(pointer)
        path=folder/'intent.json';raw=path.read_bytes();intent['actor_id']='foreign';path.write_text(json.dumps(intent))
        count=len(calls)
        with pytest.raises(ValueError,match='intent bytes changed'):root.command('record_reconcile',reconcile_body(root,base),authenticated_actor='trusted-owner')
        assert len(calls)==count and root.capture_starts.projection(before)['state']=='UNKNOWN_METADATA'
        path.write_bytes(raw)
    finally:root.close();client.close()


def test_ack_checksum_failure_never_publishes_ready_and_original_known_data_can_finish(tmp_path,monkeypatch):
    import explorer.cooperative_capture_starts as implementation
    root,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
        root.workflow._save(row);base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
        original=implementation.write_original
        def fail_checksum(path,raw):
            if path.name=='ack.sha256':raise OSError('FAKE fsync failure before DATA ACK publication')
            return original(path,raw)
        monkeypatch.setattr(implementation,'write_original',fail_checksum)
        result=root.command('record_start',base,authenticated_actor='trusted-owner')['result']
        assert 'receiver' in result['native_source_errors'] and active['pi.test']
        current=root.workflow.snapshot(row['session_id']);assert root.capture_starts.projection(current)['state']=='UNKNOWN_METADATA'
        count=len(calls)
        with pytest.raises(ValueError,match='incomplete or changed'):root.command('record_start',base,authenticated_actor='trusted-owner')
        assert len(calls)==count
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
        assert active['pi.test'] is None
        assert all('/record/' in path for method,_,path,_ in calls if method=='POST')
    finally:root.close();client.close()


def test_session_save_failure_after_intent_persistence_is_known_pre_rpc_abort(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
        root.workflow._save(row);base={'session_id':row['session_id'],'expected_epoch':row['epoch']};save=root.workflow._save;failed=[]
        def fail_once(value):
            save(value)
            if value.get('native_capture_start_intents') and not failed:
                failed.append(True);raise OSError('FAKE directory fsync failure AFTER row replace BEFORE source RPC')
        monkeypatch.setattr(root.workflow,'_save',fail_once)
        result=root.command('record_start',base,authenticated_actor='trusted-owner')['result']
        assert 'receiver' in result['native_source_errors'] and active['pi.test'] is None
        assert not any(path=='/so101-capture/record/start' for method,_,path,_ in calls if method=='POST')
        current=root.workflow.snapshot(row['session_id']);assert root.capture_starts.projection(current)['state']=='ABORTED_BEFORE_RPC'
        root.capture_starts.require_no_pending()
    finally:root.close();client.close()


def test_foreign_authenticated_caller_cannot_borrow_bound_actor_for_start_stage_or_finish(tmp_path):
    root,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
        root.workflow._save(row);base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
        with pytest.raises(ValueError,match='recording actor'):root.command('record_start',base,authenticated_actor='foreign')
        assert calls==[] and not any(active.values())
        result=root.command('record_start',base,authenticated_actor='trusted-owner')['result']
        count=len(calls);body={**base,'recording_id':result['recording_id']}
        for operation,extra in [('record_stage',{'phase_id':'PREPARE'}),('record_finish',{'outcome':{'status':'unknown'}})]:
            with pytest.raises(ValueError,match='recording actor'):root.command(operation,{**body,**extra},authenticated_actor='foreign')
        assert len(calls)==count and all(active.values())
        root.command('record_finish',{**body,'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
    finally:root.close();client.close()


@pytest.mark.parametrize('operation',['record_stage','record_finish'])
@pytest.mark.parametrize('fault',['recording','actor'])
def test_recording_or_actor_changed_after_outer_api_check_is_fenced_inside_record_io(tmp_path,monkeypatch,operation,fault):
    root,store,calls,active,contexts,client=fixture(tmp_path);saved=None
    try:
        row,spec=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
        root.workflow._save(row);base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
        result=root.command('record_start',base,authenticated_actor='trusted-owner')['result']
        saved=root.workflow.snapshot(row['session_id']);original=getattr(root.workflow,'_'+operation);count=len(calls)
        def changed(*args,**kwargs):
            current=root.workflow.snapshot(row['session_id'])
            if fault=='recording':current['recording_id']='f'*32
            else:current['recording_actor_id']='foreign'
            root.workflow._save(current);return original(*args,**kwargs)
        monkeypatch.setattr(root.workflow,'_'+operation,changed)
        payload={**base,'recording_id':result['recording_id'],**({'phase_id':'PREPARE'} if operation=='record_stage' else {'outcome':{'status':'unknown'}})}
        with pytest.raises(ValueError,match='recording changed|recording actor'):root.command(operation,payload,authenticated_actor='trusted-owner')
        assert len(calls)==count and all(active.values())
        root.workflow._save(saved);monkeypatch.setattr(root.workflow,'_'+operation,original)
    finally:
        if saved:root.workflow._save(saved)
        root.close();client.close()


def test_concurrent_failed_record_start_cannot_replace_first_authenticated_actor(tmp_path,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    root,store,calls,active,contexts,client=fixture(tmp_path);entered=threading.Event();release=threading.Event()
    try:
        row,spec=session(root);base={'session_id':row['session_id'],'expected_epoch':row['epoch']};prepare=root._prepare_context
        def paused(role,context):
            if role=='giver':entered.set();assert release.wait(4)
            return prepare(role,context)
        monkeypatch.setattr(root,'_prepare_context',paused)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(root.command,'record_start',base,authenticated_actor='first-data-owner')
            assert entered.wait(4)
            with pytest.raises(ValueError,match='metadata operation'):root.command('record_start',base,authenticated_actor='other-data-owner')
            assert root.workflow.snapshot(row['session_id'])['recording_actor_id']=='first-data-owner'
            assert all(method=='GET' for method,_,_,_ in calls)
            release.set();result=first.result(timeout=5)['result']
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='first-data-owner')
    finally:release.set();root.close();client.close()


def ready_capture(tmp_path):
    root,store,calls,active,contexts,client=fixture(tmp_path)
    row,spec=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
    root.workflow._save(row);base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
    result=root.command('record_start',base,authenticated_actor='trusted-owner')['result']
    return root,calls,active,client,base,result


def test_lost_finish_ack_after_source_sealed_reads_original_once_without_second_finish(tmp_path):
    root,calls,active,client,base,result=ready_capture(tmp_path);send=root.producers['receiver'].request
    try:
        def lost(method,path,body):
            reply=send(method,path,body)
            if path=='/so101-capture/record/finish':raise httpx.ReadTimeout('FAKE lost reply AFTER original seal')
            return reply
        root.producers['receiver'].request=lost
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
        assert active['pi.test'] is None and root.producers['receiver'].active is None
        current=root.workflow.snapshot(base['session_id']);assert root.capture_starts.projection(current)['state']=='DATA_FINISHED'
        assert len([c for c in calls if c[0]=='POST' and c[2]=='/so101-capture/record/finish'])==1
        folder,_=root.capture_starts._load(current['native_capture_start_intents']['receiver'])
        assert (folder/'finish-rpc-failure.json').is_file() and (folder/'finish-ack.json').is_file()
        assert all('/record/' in path for method,_,path,_ in calls if method=='POST')
    finally:root.close();client.close()


def test_finish_not_issued_stays_pending_until_explicit_actor_attempt_bound_data_cleanup(tmp_path):
    root,calls,active,client,base,result=ready_capture(tmp_path);send=root.producers['receiver'].request
    try:
        def lost(method,path,body):
            if path=='/so101-capture/record/finish':raise httpx.ReadTimeout('FAKE request lost BEFORE original writer finish')
            return send(method,path,body)
        root.producers['receiver'].request=lost
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
        row=root.workflow.snapshot(base['session_id']);assert root.capture_starts.projection(row)['state']=='PENDING_DATA_FINISH_RECONCILIATION' and active['pi.test']
        count=len(calls)
        with pytest.raises(ValueError,match='finish is pending'):root.command('record_start',base,authenticated_actor='trusted-owner')
        with pytest.raises(ValueError,match='attempt changed'):root.command('record_reconcile',{**base,'capture_attempt_id':'f'*32},authenticated_actor='trusted-owner')
        with pytest.raises(ValueError,match='original DATA actor'):root.command('record_reconcile',reconcile_body(root,base),authenticated_actor='foreign')
        assert len(calls)==count
        root.producers['receiver'].request=send
        reply=root.command('record_reconcile',reconcile_body(root,base),authenticated_actor='trusted-owner')['result']
        assert reply['own_source_finished_unknown'] and not reply['control_authority_adopted'] and active['pi.test'] is None
    finally:root.close();client.close()


def test_changed_sealed_reply_is_retained_and_never_releases_pending_data_slot(tmp_path,monkeypatch):
    root,calls,active,client,base,result=ready_capture(tmp_path);send=root.producers['receiver'].request;original=root._request
    try:
        def lost(method,path,body):
            reply=send(method,path,body)
            if path=='/so101-capture/record/finish':raise httpx.ReadTimeout('FAKE finish ACK lost')
            return reply
        root.producers['receiver'].request=lost
        def factory(role):
            request=original(role)
            def changed(method,path,body):
                reply=request(method,path,body)
                if path.startswith('/so101-capture/recordings/') and path.endswith('/manifest'):
                    reply['source_context']['scene_id']='FOREIGN_SEALED_CONTEXT'
                return reply
            return changed
        monkeypatch.setattr(root,'_request',factory)
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
        row=root.workflow.snapshot(base['session_id']);assert root.capture_starts.projection(row)['state']=='PENDING_DATA_FINISH_RECONCILIATION'
        folder,_=root.capture_starts._load(row['native_capture_start_intents']['receiver'])
        failures=list(folder.glob('finish-ack-failure-*.json'));assert failures and 'FOREIGN_SEALED_CONTEXT' in failures[0].read_text()
        assert not (folder/'finish-ack.json').exists() and root.producers['receiver'].active
        monkeypatch.setattr(root,'_request',original);root.producers['receiver'].request=send
    finally:root.close();client.close()


def test_reconciliation_is_serialized_and_old_reference_cannot_attach_to_next_same_epoch_attempt(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client,base,result,original=lost_ack(tmp_path,monkeypatch)
    try:
        before=len(calls);root.workflow.record_io.acquire()
        try:
            with pytest.raises(ValueError,match='metadata operation'):root.command('record_reconcile',reconcile_body(root,base),authenticated_actor='trusted-owner')
            assert len(calls)==before
        finally:root.workflow.record_io.release()
        row=root.workflow.snapshot(base['session_id']);pointer=row['native_capture_start_intents']['receiver']
        ack=root.capture_starts.acknowledge({'capture_intent':pointer})
        old=(root.recorder.folder/'sources/native-producer-start.json').read_bytes()
        saved=deepcopy(root.recorder.context['native_capture_start_intents'])
        root.recorder.context['native_capture_start_intents']['receiver']['attempt_id']='f'*32
        with pytest.raises(ValueError,match='known DATA'):root.recorder.acknowledge_native_source(result['recording_id'],'receiver',ack['reference'])
        assert 'receiver' not in root.recorder.native_sources
        assert (root.recorder.folder/'sources/native-producer-start.json').read_bytes()==old
        root.recorder.context['native_capture_start_intents']=saved
        state=root.capture_starts.projection(row);assert state['attachment_required']
        reply=root.command('record_reconcile',reconcile_body(root,base),authenticated_actor='trusted-owner')['result']
        assert reply['recording_metadata_attached']
    finally:root.close();client.close()


def test_invalid_receiver_role_label_rejects_before_either_data_producer_finishes(tmp_path):
    root,calls,active,client,base,result=ready_capture(tmp_path)
    try:
        count=len(calls)
        with pytest.raises(ValueError,match='operator outcome'):root.command('record_finish',
            {**base,'recording_id':result['recording_id'],'outcome':{'native_roles':{'receiver':{'status':'success'}}}},authenticated_actor='trusted-owner')
        assert len(calls)==count and all(active.values())
        assert root.workflow.snapshot(base['session_id'])['recording_id']==result['recording_id']
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
    finally:root.close();client.close()


@pytest.mark.parametrize('native_roles',[{'giver':'success'},{'giver':{'status':'INVALID'}},{'unknown-role':{'status':'unknown'}}])
def test_malformed_native_role_outcomes_never_partially_finish_other_source(tmp_path,native_roles):
    root,calls,active,client,base,result=ready_capture(tmp_path)
    try:
        count=len(calls)
        with pytest.raises(ValueError,match='outcome objects'):root.command('record_finish',
            {**base,'recording_id':result['recording_id'],'outcome':{'native_roles':native_roles}},authenticated_actor='trusted-owner')
        assert len(calls)==count and all(active.values())
    finally:root.close();client.close()


def test_explicit_restart_recovery_retires_stale_camera_pointer_without_rewriting_raw_manifest(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client,base,result,original=lost_ack(tmp_path,monkeypatch)
    path=root.workflow._path(base['session_id']);persisted=path.read_bytes();config=deepcopy(root.config)
    root.close();assert active['pi.test']
    # Simulate persisted active session metadata after process loss before its
    # camera pointer was retired. This is a DATA recovery fixture, not SIGKILL evidence.
    path.write_bytes(persisted)
    camera=root.root/'recordings'/result['recording_id']/'manifest.json';raw=camera.read_bytes()
    recovered=CooperativeService(config,store,client=client)
    try:
        row=recovered.workflow.snapshot(base['session_id']);assert row['status']=='INTERRUPTED' and row['recording_id']==result['recording_id']
        body=reconcile_body(recovered,{'session_id':row['session_id'],'expected_epoch':row['epoch']})
        reply=recovered.command('record_reconcile',body,authenticated_actor='trusted-owner')['result']
        assert reply['interrupted_camera_pointer_retired'] and not reply['control_authority_adopted']
        current=recovered.workflow.snapshot(row['session_id']);assert current['recording_id'] is None
        assert current['last_interrupted_recording_id']==result['recording_id'] and current['status']=='INTERRUPTED'
        assert camera.read_bytes()==raw and active['pi.test'] is None
        assert current['interrupted_recording_recovery']['camera_capture_resumed'] is False
        assert all('/record/' in p for method,_,p,_ in calls if method=='POST')
    finally:recovered.close();client.close()
