"""Operator DATA path with shared real file writer and isolated fake source tap."""
from contextlib import contextmanager
import importlib
import json
import time

from fastapi.testclient import TestClient
import pytest

from explorer import transfer_capture as capture
from explorer_shared.native_capture import NativeCaptureWriter
from explorer_shared.native_capture_service import NativeCaptureService
from test_transfer_target_native_capture import context_fixture
from test_native_capture import setup,publication
from test_transfer_target_original_cohort import original_target_cohort


def test_operator_start_pause_stage_finish_separate_review_and_import_queue(tmp_path,monkeypatch):
    store,wizard,metadata,context=context_fixture(tmp_path,monkeypatch)
    actor='OWN_TEST_ACTOR';old,tap,camera,clock,_,_=setup(tmp_path/'tap');old.close()
    writer=NativeCaptureWriter(tmp_path/'writer',camera,clock_domain='nano',boot_id='boot',clock=clock)
    class Nano:
        def __init__(self):self.calls=[]
        def status(self):return service.status()
        def reference(self):return {}
        def start_recording(self,ctx):self.calls.append('start');return service.start(ctx)
        def record_operation(self,operation,identity,body):
            self.calls.append(operation)
            if operation=='stage':return service.stage(identity,body['phase_id'])
            if operation=='finish':return service.finish(identity,body['outcome_evidence'])
            if operation=='review':return service.review(identity,body['decision'],body['actor_id'],body['expected_archive_sha256'])
            return getattr(service,operation)(identity)
        def manifest(self,identity):return json.loads((writer.folder/'manifest.json').read_bytes())
        def review_state(self,identity):return writer.recording_review(identity)
    class TapReader:
        def status(self):return dict(schema='explorer_native_retarget_mux_v1')
        def reference(self):return {}
        def dispatch_source_packet(self,cursor):
            page=tap.since(cursor);return dict(source_bytes=json.dumps(page).encode(),payload=page)
    service=NativeCaptureService(writer,TapReader(),poll_s=.01,clock=clock);nano=Nano()
    try:
        row=capture.start(store,wizard['wizard_id'],metadata,actor,nano)
        assert capture.start(store,wizard['wizard_id'],metadata,actor,nano)['recording_id']==row['recording_id']
        ident=row['capture_id'];operation=lambda body:capture.operation(store,wizard['wizard_id'],ident,body,actor,nano)
        assert operation({'operation':'pause'})['state']=='PAUSED'
        assert operation({'operation':'resume'})['state']=='RECORDING'
        assert operation({'operation':'pause'})['state']=='PAUSED'
        assert capture.capture_list(store,wizard['wizard_id'],actor)['captures'][0]['state']=='PAUSED'
        operation({'operation':'resume'})
        publication(tap,camera,clock,mode='keyboard')
        deadline=time.monotonic()+3
        while writer.status()['samples_written']==0:
            assert time.monotonic()<deadline,service.status()
            service.wake.set();time.sleep(.005)
        for phase in context['target_phase_ids']:operation({'operation':'stage','phase_id':phase})
        phases={phase:'unknown' for phase in context['target_phase_ids']}
        assert operation({'operation':'finish','operator_outcome':'unknown','per_phase':phases})['state']=='FINISH_PENDING'
        assert writer.done.wait(3)
        sealed=operation({'operation':'refresh'});assert sealed['state']=='SEALED'
        assert not sealed['admission']['eligible_as_successful_bc_expert']
        operation({'operation':'review','decision':'approve'})
        job=operation({'operation':'import'});assert job['kind']=='transfer_target_import'
        assert operation({'operation':'import'})['id']==job['id']
        assert nano.calls.count('start')==nano.calls.count('finish')==1
        assert not capture.capture_list(store,wizard['wizard_id'],actor)['physical_execution_authorized']
        with pytest.raises(ValueError):capture.operation(store,wizard['wizard_id'],ident,{'operation':'stop'},actor,nano)
    finally:service.close()


def test_target_dataset_assembly_is_managed_completed_job_and_preserves_originals(tmp_path,monkeypatch):
    store,wizard,path,imports,evidence=original_target_cohort(tmp_path,monkeypatch)
    job=capture.aggregate(store,wizard['wizard_id'],imports,wizard['created_by_actor'])
    assert job['status']=='completed' and job['kind']=='transfer_target_dataset'
    assert capture.aggregate(store,wizard['wizard_id'],imports,wizard['created_by_actor'])['id']==job['id']
    assert len(job['result']['train_original_episode_ids'])==3 and len(job['result']['sealed_original_episode_ids'])==1
    from explorer_shared.learning.artifacts import sha
    assert all(sha(path)==checksum for path,checksum in evidence['originals_sha256'].items())


def test_capture_routes_require_authenticated_csrf_and_reuse_fixed_data_transport(tmp_path,monkeypatch):
    store,wizard,metadata,_=context_fixture(tmp_path,monkeypatch);app=importlib.import_module('explorer.app')
    client=TestClient(app.app);base='/api/transfer/calibration/'+wizard['wizard_id']
    assert client.get(base+'/captures').status_code==401
    client.post('/api/login',json={'password':'fixture'});headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
    assert client.post(base+'/captures',json=metadata).status_code==403
    import explorer.transfer_geometry_api as api
    @contextmanager
    def forbidden(config):raise ValueError('isolated missing Nano; no network');yield
    monkeypatch.setattr(api,'nano_capture_connection',forbidden)
    assert client.get(base+'/captures').status_code==200
    response=client.post(base+'/captures',json=metadata,headers=headers)
    assert response.status_code==409 and 'isolated missing Nano' in response.text
