"""Root authenticated API: native9 known DATA recovery, no actual hardware."""
from copy import deepcopy
import httpx
import pytest
from explorer.cooperative_service import CooperativeService
from test_cooperative_service import fixture,session


def started(tmp_path,monkeypatch,lost=None):
    root,store,calls,active,contexts,client=fixture(tmp_path)
    row,_=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
    root.workflow._save(row);base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
    original=root.producers['giver'].request
    def send(method,path,body):
        if lost=='finish-before' and path=='/native-capture/record/finish':raise httpx.ReadTimeout('FAKE before writer receives finish')
        result=original(method,path,body)
        if path=={'start':'/native-capture/record/start','finish-after':'/native-capture/record/finish'}.get(lost):
            raise httpx.ReadTimeout('FAKE lost reply AFTER original writer')
        return result
    monkeypatch.setattr(root.producers['giver'],'request',send)
    result=root.command('record_start',base,authenticated_actor='trusted-owner')['result']
    return root,store,calls,active,contexts,client,base,result,original


def reconcile(root,base):
    row=root.workflow.snapshot(base['session_id'])
    return {**base,'role':'giver','capture_attempt_id':row['native_capture_start_intents']['giver']['attempt_id']}


def test_lost_giver_start_reply_recovers_only_exact_actor_attempt_and_appends_data(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client,base,result,original=started(tmp_path,monkeypatch,'start')
    try:
        row=root.workflow.snapshot(base['session_id']);assert active['nano.test'] and active['pi.test'] is None
        assert set(result['native_source_errors'])=={'giver','receiver'}
        pointer=row['native_capture_start_intents']['giver'];folder,intent=root.capture_starts._load(pointer)
        assert intent['record_start_body']['skill_context']['source_capture_attempt_id']==pointer['attempt_id']
        assert intent['actor_id']=='trusted-owner' and 'permission' not in intent
        raw=(root.recorder.folder/'sources/native-producer-start.json').read_bytes();count=len(calls)
        for body,actor in [(reconcile(root,base),'foreign'),({**reconcile(root,base),'capture_attempt_id':'e'*32},'trusted-owner')]:
            with pytest.raises(ValueError):root.command('record_reconcile',body,authenticated_actor=actor)
            assert len(calls)==count
        reply=root.command('record_reconcile',reconcile(root,base),authenticated_actor='trusted-owner')['result']
        assert reply['recording_metadata_attached'] and not reply['control_authority_adopted']
        assert raw==(root.recorder.folder/'sources/native-producer-start.json').read_bytes()
        assert (root.recorder.folder/'sources/native-producer-reconciled-giver.json').is_file()
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
        assert active['nano.test'] is None and root.capture_starts.projection(root.workflow.snapshot(base['session_id']),'giver')['state']=='DATA_FINISHED'
        assert len([c for c in calls if c[0]=='POST' and c[2]=='/native-capture/record/start'])==1
        assert all('/record/' in c[2] for c in calls if c[0]=='POST')
    finally:root.close();client.close()


@pytest.mark.parametrize('fault',['finish-before','finish-after'])
def test_giver_lost_finish_reconciles_sealed_original_without_duplicate_finish(tmp_path,monkeypatch,fault):
    root,store,calls,active,contexts,client,base,result,original=started(tmp_path,monkeypatch,fault)
    try:
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
        projection=root.capture_starts.projection(root.workflow.snapshot(base['session_id']),'giver')
        if fault=='finish-before':
            assert projection['state']=='PENDING_DATA_FINISH_RECONCILIATION' and active['nano.test']
            monkeypatch.setattr(root.producers['giver'],'request',original)
            reply=root.command('record_reconcile',reconcile(root,base),authenticated_actor='trusted-owner')['result']
            assert reply['own_source_finished_unknown'] and not reply['native_commands_dispatched']
        else:assert projection['state']=='DATA_FINISHED'
        assert active['nano.test'] is None
        assert len([c for c in calls if c[0]=='POST' and c[2]=='/native-capture/record/finish'])==1
    finally:root.close();client.close()


def test_restart_lost_giver_start_only_unknown_data_cleanup_no_grant_or_control_resume(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client,base,result,original=started(tmp_path,monkeypatch,'start')
    config=deepcopy(root.config);root.close();assert active['nano.test']
    recovered=CooperativeService(config,store,client=client)
    try:
        row=recovered.workflow.snapshot(base['session_id']);assert row['status'] in ('STOPPED','INTERRUPTED')
        body={'session_id':row['session_id'],'expected_epoch':row['epoch']};count=len(calls)
        assert recovered.capture_starts.projection(row,'giver')['state']=='PENDING_DATA_RECONCILIATION' and len(calls)==count
        reply=recovered.command('record_reconcile',reconcile(recovered,body),authenticated_actor='trusted-owner')['result']
        assert reply['own_source_finished_unknown'] and not reply['recording_metadata_attached'] and active['nano.test'] is None
        assert [c[2] for c in calls[count:] if c[0]=='POST']==['/native-capture/record/finish']
    finally:recovered.close();client.close()


def test_stale_real_reference_fails_before_known_intent_or_data_start(tmp_path,monkeypatch):
    root,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,_=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'});root.workflow._save(row)
        original=root._request
        def factory(role):
            send=original(role)
            def request(method,path,body):
                if role=='giver' and path=='/legacy/explorer/retarget/reference':raise ValueError('HTTP409 MCU telemetry stale')
                return send(method,path,body)
            return request
        monkeypatch.setattr(root,'_request',factory)
        result=root.command('record_start',{'session_id':row['session_id'],'expected_epoch':row['epoch']},authenticated_actor='trusted-owner')['result']
        assert 'MCU telemetry stale' in result['native_source_errors']['giver'] and active['nano.test'] is None
        assert root.capture_starts.projection(root.workflow.snapshot(row['session_id']),'giver') is None
        assert not any(c[0]=='POST' and c[2]=='/native-capture/record/start' for c in calls)
    finally:root.close();client.close()


def test_closed_service_accepts_only_fixed_known_manifest_get_and_rejects_native_control(tmp_path):
    root,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        root.closed=True;send=root._request('giver')
        for path in ('/legacy/explorer/retarget/begin','/native-capture/record/start','/native-capture/recordings/'+'a'*32+'/manifest?adopt=true',
                     '/native-capture/recordings/../manifest','/native-capture/recordings/'+'a'*32+'/download'):
            with pytest.raises(ValueError,match='closed'):send('GET',path,None)
        assert calls==[]
        with pytest.raises(ValueError,match='HTTP404'):send('GET','/native-capture/recordings/'+'a'*32+'/manifest',None)
        assert len(calls)==1 and calls[0][0]=='GET'
    finally:root.close();client.close()


def test_failed_giver_ack_fsync_stays_unknown_but_known_original_can_finish_unknown(tmp_path,monkeypatch):
    import json
    import explorer.cooperative_capture_starts as implementation
    root,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,_=session(root);row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'});root.workflow._save(row)
        original=implementation.write_original
        def write(path,raw):
            if path.name=='ack.sha256' and json.loads(path.with_name('intent.json').read_bytes())['role']=='giver':
                raise OSError('FAKE native9 DATA fsync failure')
            return original(path,raw)
        monkeypatch.setattr(implementation,'write_original',write)
        base={'session_id':row['session_id'],'expected_epoch':row['epoch']}
        result=root.command('record_start',base,authenticated_actor='trusted-owner')['result']
        assert active['nano.test'] and active['pi.test'] is None and 'giver' in result['native_source_errors']
        assert root.capture_starts.projection(root.workflow.snapshot(row['session_id']),'giver')['state']=='UNKNOWN_METADATA'
        root.command('record_finish',{**base,'recording_id':result['recording_id'],'outcome':{'status':'unknown'}},authenticated_actor='trusted-owner')
        assert active['nano.test'] is None
        assert root.capture_starts.projection(root.workflow.snapshot(row['session_id']),'giver')['state']=='DATA_FINISHED'
        pointer=root.workflow.snapshot(row['session_id'])['native_capture_start_intents']['giver']
        folder,_=root.capture_starts._load(pointer);assert (folder/'ack.json').is_file() and not (folder/'ack.sha256').exists()
        assert (folder/'finish-ack.json').is_file()  # original incomplete start evidence remains untouched
    finally:root.close();client.close()
