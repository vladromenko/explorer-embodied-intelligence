"""Custody, corruption and STOP races with real Root/native software owners."""
from copy import deepcopy
import hashlib
import json
import threading

import pytest

from test_cooperative_native_sessions import configured,choice,execute


def test_actual_native_reply_download_is_exact_and_survives_cooperative_stop(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        result=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))
        pointer=result['control_receipt'];path,mime=service.artifact('native-control',pointer['receipt_id'])
        document=json.loads(path.read_bytes())
        assert mime=='application/json' and hashlib.sha256(path.read_bytes()).hexdigest()==pointer['sha256']
        assert document['source_response']==result['native_receipt'] and document['previous'] is None
        assert document['context']['session_id']==row['session_id'] and document['role']=='receiver'
        assert document['physical_attainment_claimed'] is False and document['automatic_native_command_replay_allowed'] is False
        saved=path.read_bytes();service.command('stop',{'session_id':row['session_id'],'expected_epoch':row['epoch']},authenticated_actor='trusted-owner')
        assert service.artifact('native-control',pointer['receipt_id'])[0].read_bytes()==saved
        path.write_bytes(saved+b' ')
        with pytest.raises(ValueError,match='receipt changed'):service.artifact('native-control',pointer['receipt_id'])
    finally:service.close();client.close()


def test_projection_rollover_keeps_all_immutable_predecessors_and_original_replies(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        before=deepcopy(pair.calls);pointers=[]
        for index in range(66):
            pointers.append(service.native_controls._save(row,spec,'giver','fixture_custody_only',{'original_sequence':index},'trusted-owner'))
        current=service.workflow.snapshot(row['session_id'])
        assert len(current['native_user_control_receipts'])==64 and len(list((service.root/'native-control').iterdir()))==66
        head=current['native_control_ledger']['giver'];assert head['ordinal']==66
        recovered=[]
        while head:
            path,_=service.artifact('native-control',head['receipt_id']);assert hashlib.sha256(path.read_bytes()).hexdigest()==head['sha256']
            document=json.loads(path.read_bytes());recovered.append(document['source_response']['original_sequence']);head=document['previous']
        assert recovered==list(reversed(range(66))) and pair.calls==before
    finally:service.close();client.close()


def test_delayed_artifact_fsync_does_not_hold_stop_or_publish_late_authority(tmp_path,monkeypatch):
    import explorer.cooperative_native_receipts as module
    service,store,row,spec,pair,client=configured(tmp_path)
    entered,release,stopped=threading.Event(),threading.Event(),threading.Event();errors=[];stop_errors=[]
    write=module.write_original
    def blocked(path,raw):
        entered.set();assert release.wait(4);return write(path,raw)
    monkeypatch.setattr(module,'write_original',blocked)
    def save():
        try:service.native_controls._save(row,spec,'giver','fixture_custody_only',{'parsed_reply':True},'trusted-owner')
        except Exception as error:errors.append(error)
    def stop():
        try:service.command('stop',{'session_id':row['session_id'],'expected_epoch':row['epoch']},authenticated_actor='trusted-owner')
        except Exception as error:stop_errors.append(error)
        finally:stopped.set()
    writer=threading.Thread(target=save);stopper=threading.Thread(target=stop)
    try:
        writer.start();assert entered.wait(2);stopper.start();assert stopped.wait(2)
        assert not stop_errors and service.workflow.snapshot(row['session_id'])['status']=='STOPPED'
        release.set();writer.join(3);stopper.join(3)
        assert not writer.is_alive() and errors and isinstance(errors[0],ValueError)
        current=service.workflow.snapshot(row['session_id']);assert not current.get('native_control_ledger') and not current.get('native_user_control_receipts')
        originals=list((service.root/'native-control').glob('*/receipt.json'));assert len(originals)==1
        document=json.loads(originals[0].read_bytes());assert document['source_response']=={'parsed_reply':True}
        assert service.artifact('native-control',document['receipt_id'])[0]==originals[0]
    finally:release.set();writer.join(3);service.close();client.close()


def test_parallel_role_chains_are_independent_and_same_role_stale_predecessor_cannot_overwrite(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        ledger=service.native_receipts
        a=ledger.archive(row,spec,'giver','fixture',{'sequence':0},'trusted-owner')
        stale=ledger.archive(row,spec,'giver','fixture',{'sequence':1},'trusted-owner')
        b=ledger.archive(row,spec,'receiver','fixture',{'sequence':2},'trusted-owner')
        ledger.publish(row,spec,a,'trusted-owner');ledger.publish(row,spec,b,'trusted-owner')
        with pytest.raises(ValueError,match='predecessor'):ledger.publish(row,spec,stale,'trusted-owner')
        assert service.workflow.snapshot(row['session_id'])['native_control_ledger']['giver']['receipt_id']==a['receipt_id']
        assert service.artifact('native-control',stale['receipt_id'])[0].is_file()
    finally:service.close();client.close()


def test_unpublished_source_reply_requires_immutable_origin_and_never_publishes_authority(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        ledger=service.native_receipts
        pointer=ledger.archive(row,spec,'giver','fixture_decoded_late_reply',{'actual_parsed_reply':True},'trusted-owner',allow_unpublished=True)
        document=json.loads(service.artifact('native-control',pointer['receipt_id'])[0].read_bytes())
        assert document['authority_publication_allowed'] is False
        with pytest.raises(ValueError,match='cannot restore authority'):ledger.publish(row,spec,pointer,'trusted-owner')
        with pytest.raises(ValueError,match='immutable original process/actor/context'):
            ledger.archive(row,spec,'giver','fixture',{'actual_parsed_reply':True},'foreign-owner',allow_unpublished=True)
        changed=deepcopy(row);changed['observed_test_binding']['actor_id']='foreign-owner'
        with pytest.raises(ValueError,match='immutable original process/actor/context'):
            ledger.archive(changed,spec,'giver','fixture',{'actual_parsed_reply':True},'foreign-owner',allow_unpublished=True)
        assert not service.workflow.snapshot(row['session_id']).get('native_control_ledger')
    finally:service.close();pair.close();client.close()
