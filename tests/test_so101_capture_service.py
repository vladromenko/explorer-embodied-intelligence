"""Source04 localhost data plane; original file writer and synthetic cached inputs."""
from copy import deepcopy
import hashlib
from http.server import ThreadingHTTPServer
import json
from types import SimpleNamespace
import threading
import uuid

import httpx
import pytest

from explorer_so101.capture_service import StationCaptureService
from explorer_shared.node_agent import make_handler
from explorer.native_capture_transfer import transfer_native_capture
from test_so101_capture_adapter import FakeSource, Clock, context


class Permission:
    """Native provider integration is separately exercised with real COMMON code."""
    def __init__(self,selected,station):self.selected,self.station=selected,station;self.calls=0;self.deny_at=None
    def check(self,grant,selected,epoch):
        self.calls+=1
        if (selected!=self.selected or grant!='fixture-owned-grant' or epoch!=self.station.control_epoch or
                self.calls==self.deny_at):raise ValueError('Exact acknowledged demo permission required')
        return {'actor_id':'fixture-owner','binding':deepcopy(selected)}


@pytest.fixture
def service(tmp_path):
    source=FakeSource(tmp_path);clock=Clock();body=context();body['session_id']=str(uuid.uuid4())
    selected=dict(purpose='source04_demo',input_mode='leader',source_domain='PHYSICAL',skill_id=body['skill_id'],
        skill_version=body['skill_version'],spec_sha256=body['skill_spec_sha256'],action_contract_id='so101_follower_calibrated_position6_v1',
        source_session_id=uuid.UUID(body['session_id']).hex,demonstration_id=uuid.uuid4().hex)
    permission=Permission(selected,source.station)
    capture=StationCaptureService(source.station,permission,tmp_path/'metadata',clock=clock)
    request={'record_start_body':body,'permission':{'binding':selected,'grant_id':'fixture-owned-grant','control_epoch':source.station.control_epoch}}
    yield capture,source,clock,request,permission
    capture.close();source.close_writer()


def test_known_denied_admission_before_writer_can_retry_and_does_not_require_unknown_recovery(service):
    capture,source,clock,body,permission=service
    bad=deepcopy(body);bad['permission']['binding']['demonstration_id']=uuid.uuid4().hex
    with pytest.raises(ValueError):capture.start(bad,'fixture-owner')
    assert capture.status()['capture'] is None and not source.station.record
    # Admission can expire between preflight and the callback. That exact
    # pre-writer denial is journaled separately from an unknown writer return.
    permission.deny_at=permission.calls+2
    with pytest.raises(ValueError):capture.start(body,'fixture-owner')
    assert capture.status()['recovery_required'] is False and not source.station.record
    permission.deny_at=None
    assert capture.start(body,'fixture-owner')['recording_id']


def test_capture_context_actor_and_native_epoch_cannot_borrow_a_demo_grant(service):
    capture,source,clock,body,_=service
    for key,value in [('skill_version',2),('session_id',str(uuid.uuid4()))]:
        bad=deepcopy(body);bad['record_start_body'][key]=value
        with pytest.raises(ValueError):capture.start(bad,'fixture-owner')
    with pytest.raises(ValueError):capture.start(body,'foreign-tab')
    assert not source.station.record and capture.status()['capture'] is None


def test_actual_localhost_capture_review_preview_and_common_resumable_transfer(service,tmp_path):
    capture,source,clock,body,_=service;token='local-test-source04-data-token'
    agent=SimpleNamespace(token=token,mode='pi',station_capture=capture,station=None,legacy_bridge=None)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
    try:
        with httpx.Client(base_url=base,timeout=3,headers={'Authorization':'Bearer '+token},trust_env=False) as client:
            def post(operation,body,actor='fixture-owner'):
                response=client.post('/so101-capture/'+operation,json=dict(body,actor_id=actor));response.raise_for_status();return response.json()
            assert client.get('/so101-capture/capabilities').json()['action_dimension']==6
            start=clock();ident=post('record/start',body)['recording_id']
            source.sample(start+110_000_000);clock.value=start+120_000_000;post('record/pause',{'recording_id':ident})
            denied=client.post('/so101-capture/record/resume',json={'recording_id':ident,'actor_id':'foreign-tab'});assert denied.status_code==400
            clock.value=start+130_000_000;post('record/resume',{'recording_id':ident})
            source.sample(start+230_000_000);clock.value=start+250_000_000
            sealed=post('record/finish',{'recording_id':ident,'outcome':'unknown'})
            listing=client.get('/so101-capture/recordings').json();row=listing['ready_recordings'][0];checksum=row['archive_sha256']
            assert row['transfer_ready'] is False and sealed['sample_selection'][1]['recording_segment_id']==1
            jpeg_sha=next(iter(sealed['original_jpegs_sha256'].values()))
            preview=client.get('/so101-capture/recordings/'+ident+'/frame/'+jpeg_sha)
            assert preview.status_code==200 and hashlib.sha256(preview.content).hexdigest()==jpeg_sha
            approval=post('record/review',{'recording_id':ident,'decision':'approve','expected_archive_sha256':checksum})
            # The SAME transfer implementation resumes a real HTTP byte range.
            original=capture.archive(ident).read_bytes();artifact_root=tmp_path/'transferred';folder=artifact_root/checksum[:2];folder.mkdir(parents=True)
            (folder/(checksum+'.part')).write_bytes(original[:4096])
            artifact,verified,evidence=transfer_native_capture(client,base,ident,checksum,approval['review_sha256'],artifact_root,source_prefix='/so101-capture')
            assert artifact.read_bytes()==original and evidence['resumed_bytes']==4096 and verified==approval
            assert client.get('/so101-capture/recordings').json()['ready_recordings'][0]['transfer_ready']
            assert client.get('/so101-capture/recordings/'+ident+'/manifest').json()==sealed
            assert source.station.mode=='teleoperation'  # finish is data-only.
            assert source.station.last_command['requested']==[110,20,30,40,50,60]
            post('record/review',{'recording_id':ident,'decision':'reject','expected_archive_sha256':checksum})
            with pytest.raises(ValueError,match='approval|review|approve'):
                transfer_native_capture(client,base,ident,checksum,approval['review_sha256'],artifact_root,source_prefix='/so101-capture')
    finally:server.shutdown();server.server_close();thread.join(2)
