"""Root projection IO is not a prerequisite for exact Source STOP or revocation."""
from copy import deepcopy
import json
import threading
import time

import pytest

from explorer_shared import cooperative_workflow05 as module
from explorer_shared.cooperative_workflow05 import CooperativeWorkflow
from test_cooperative_native_sessions import configured,choice,execute
from test_cooperative_unattached_stop import close


def test_blocked_stop_projection_never_holds_workflow_lock_or_delays_actual_scoped_stop(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=configured(tmp_path);entered,release=threading.Event(),threading.Event();results=[];worker=None
    writer=module.write_projection
    try:
        started=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))['native_receipt']['grant']
        def blocked(path,value):
            if path.name.startswith('.session-stop-'):
                entered.set();assert release.wait(4)
                raise OSError('fixture: stop projection directory fsync unavailable')
            return writer(path,value)
        monkeypatch.setattr(module,'write_projection',blocked)
        worker=threading.Thread(target=lambda:results.append(service.workflow.cancel_session(row['session_id'],'WRITE_FAILURE')))
        worker.start();assert entered.wait(2)
        end=time.monotonic()+2
        while pair.station.mode!='idle' and time.monotonic()<end:time.sleep(.005)
        assert pair.station.mode=='idle' and worker.is_alive()
        stops=[body for operation,body in pair.station.calls if operation=='stop']
        assert len(stops)==1 and stops[0]['expected_control_epoch']==started['control_epoch']
        assert service.workflow.lock.acquire(blocking=False)
        try:
            stopped=service.workflow.snapshot(row['session_id'])
            assert stopped['status']=='STOPPED' and stopped['epoch']!=row['epoch']
            assert stopped['coordinator_cancellation_projection_persisted'] is False
        finally:service.workflow.lock.release()
        with pytest.raises(ValueError):service.native_controls._fresh(row,spec,'trusted-owner')
        with pytest.raises(ValueError):service.workflow.request_step(row['session_id'],'giver_holding',expected_epoch=row['epoch'])
        with pytest.raises(ValueError,match='revoked session'):service.workflow._save(row)
        release.set();worker.join(4);assert not worker.is_alive()
        result=results[0];assert result['session']['status']=='STOPPED' and not result['session']['coordinator_cancellation_projection_persisted']
        assert len(result['session']['coordinator_cancellation_projection_errors'])==2
        assert result['native_owned_session_cleanup']['roles']['receiver']['known_owned_native_session']
        assert json.loads(service.workflow._path(row['session_id']).read_bytes())['status']!='STOPPED'  # Raw old disk is retained honestly.
        assert service.workflow.snapshot()['sessions'][0]['status']=='STOPPED'
    finally:
        release.set()
        if worker is not None:worker.join(4)
        close(service,pair,client)


def test_final_projection_failure_after_atomic_replace_retains_stop_without_restoring_permission(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=configured(tmp_path);replace=module.os.replace;fsync=module.os.fsync;replaced=[];failures=[]
    try:
        execute(service,'native_begin',choice(service,row,'receiver','native_begin'))
        def tracked_replace(source,target):
            replace(source,target)
            if source.name.startswith('.session-stop-') and target.name=='session.json':replaced.append(target)
        def failed_fsync(descriptor):
            directory=service.workflow._path(row['session_id']).parent
            if replaced and module.os.readlink('/proc/self/fd/'+str(descriptor))==str(directory):
                failures.append(descriptor)
                raise OSError('fixture: final directory durability unknown after replace')
            return fsync(descriptor)
        monkeypatch.setattr(module.os,'replace',tracked_replace)
        monkeypatch.setattr(module.os,'fsync',failed_fsync)
        result=service.workflow.cancel_session(row['session_id'],'FINAL_FSYNC_FAILURE')
        assert replaced and failures
        assert result['session']['status']=='STOPPED' and result['session']['coordinator_cancellation_projection_persisted'] is False
        assert pair.station.mode=='idle' and not pair.provider.status()['execution_permission']
        assert json.loads(service.workflow._path(row['session_id']).read_bytes())['status']=='STOPPED'
        with pytest.raises(ValueError):service.native_controls._fresh(row,spec,'trusted-owner')
    finally:close(service,pair,client)


def test_restart_after_failed_stop_projection_interrupts_old_disk_session_without_source_replay(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=configured(tmp_path);writer=module.write_projection;restarted=None
    try:
        execute(service,'native_begin',choice(service,row,'receiver','native_begin'))
        def failed(path,value):
            if path.name.startswith('.session-stop-'):raise OSError('fixture: no projection storage')
            return writer(path,value)
        monkeypatch.setattr(module,'write_projection',failed)
        service.workflow.cancel_session(row['session_id']);before=deepcopy(pair.station.calls)
        restarted=CooperativeWorkflow(service.workflow.root,service.registry,evidence_inbox=None,
            clock_domain=service.workflow.domain,managed_root=store.root)
        actual=restarted.snapshot(row['session_id'])
        assert actual['status']=='INTERRUPTED' and actual['epoch']!=row['epoch']
        assert pair.station.calls==before and restarted.revoked_sessions=={}
    finally:
        if restarted is not None:restarted.close()
        close(service,pair,client)


def test_missing_spec_does_not_skip_known_returned_source_scope_cleanup(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        execute(service,'native_begin',choice(service,row,'receiver','native_begin'))
        def unreadable(current):raise OSError('fixture: pinned Spec file temporarily unreadable')
        monkeypatch.setattr(service.workflow,'_spec_for',unreadable)
        result=service.workflow.cancel_session(row['session_id'],'SPEC_IO_FAILURE')
        assert result['session']['status']=='STOPPED' and pair.station.mode=='idle'
        assert result['native_owned_session_cleanup']['roles']['receiver']['known_owned_native_session']
        assert all(value['delivery_unknown'] for value in result['stop_receipts'].values())
    finally:close(service,pair,client)
