"""Pause boundaries and source approval use fake clocks/receipts; no devices."""
import hashlib
import json

import pytest

from test_native_capture import setup,publication,outcomes,sealed


def test_pause_excludes_actions_but_retains_raw_pages_and_separates_resumed_segments(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path,project='02',mode='keyboard')
    try:
        first,_=publication(tap,camera,clock,stamp=2250,mode='keyboard');assert writer.submit(ident,first)
        clock.value=2300;writer.pause(ident)
        paused,_=publication(tap,camera,clock,stamp=2450,mode='keyboard');assert writer.submit(ident,paused)
        clock.value=2600;writer.resume(ident)
        resumed,_=publication(tap,camera,clock,stamp=2750,mode='keyboard');assert writer.submit(ident,resumed)
        clock.value=2800;result=sealed(writer,ident,outcomes(ident))
        episode=result['embodied_episode'];assert len(episode['samples'])==2
        assert [sample['recording_segment_id'] for sample in episode['samples']]==[0,1]
        assert episode['recording']['paused_native_receipts_retained_not_training_samples']==1
        assert episode['recording']['capture_intervals']['pause_intervals']==[{'start_monotonic_ns':2300,'end_monotonic_ns':2600}]
        pages=[path.read_bytes() for path in (writer.folder/'sources').glob('native-page-*.json') if not path.name.endswith('-camera.json')]
        assert first in pages and paused in pages and resumed in pages
        assert not writer.ready_recordings()[0]['transfer_ready']
    finally:writer.close()


def test_source_approval_binds_exact_archive_and_preserves_both_decisions_and_originals(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path,project='02',mode='keyboard')
    try:
        packet,_=publication(tap,camera,clock,mode='keyboard');writer.submit(ident,packet)
        sealed(writer,ident,outcomes(ident));folder=writer.folder
        manifest=(folder/'manifest.json').read_bytes();archive=(folder/'recording.tar').read_bytes()
        digest=hashlib.sha256(archive).hexdigest()
        assert writer.recording_review(ident)['source_review'] is None
        with pytest.raises(ValueError,match='exact unchanged'):writer.review(ident,'approve','fixture-owner','0'*64)
        approved=writer.review(ident,'approve','fixture-owner',digest)
        assert approved['source_review']['archive_sha256']==digest and writer.ready_recordings()[0]['transfer_ready']
        rejected=writer.review(ident,'reject','fixture-owner',digest)
        assert rejected['source_review']['previous_review_sha256']==approved['review_sha256']
        assert not writer.ready_recordings()[0]['transfer_ready']
        assert len(list((folder/'reviews').glob('*.json')))==2
        assert (folder/'manifest.json').read_bytes()==manifest and (folder/'recording.tar').read_bytes()==archive
        (folder/'recording.tar').write_bytes(archive+b'fixture-corruption')
        with pytest.raises(ValueError,match='exact unchanged'):writer.review(ident,'approve','fixture-owner',digest)
    finally:writer.close()


def test_cancelled_paused_record_is_preserved_downloadable_and_never_approved(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path,project='02',mode='keyboard')
    try:
        packet,_=publication(tap,camera,clock,mode='keyboard');writer.submit(ident,packet)
        clock.value=2300;writer.pause(ident)
        writer.finish(ident,json.dumps({'recording_id':ident,'operator_outcome':'rejected','recording_cancelled':True}).encode())
        assert writer.done.wait(3);result=json.loads((writer.folder/'manifest.json').read_bytes())
        assert result['state']=='cancelled' and result['analysis_only']
        assert writer.archive(ident).is_file() and not writer.ready_recordings()[0]['transfer_ready']
        digest=writer.ready_recordings()[0]['archive_sha256']
        with pytest.raises(ValueError,match='Cancelled'):writer.review(ident,'approve','fixture-owner',digest)
        assert writer.review(ident,'reject','fixture-owner',digest)['source_review']['decision']=='reject'
        with pytest.raises(ValueError):writer.resume(ident)
    finally:writer.close()


def test_operator_requests_and_native_responses_are_retained_without_fabricating_dispatch_or_feedback(tmp_path):
    from explorer_shared.native_capture_service import NativeCaptureService
    writer,tap,camera,clock,ident,_=setup(tmp_path,project='02',mode='keyboard')
    service=NativeCaptureService(writer,None,clock=clock);service.active_id=ident
    try:
        request={'source':'keyboard','keys':['KeyI'],'observing':True}
        response={'state':'ACTIVE','arm_mode':'cartesian'};clock.value=2300
        service.observe_operator_input(request,response,200,2200,{'input_origin':'Browser Gamepad API','raw_axes':[.5,0,0,0]})
        service.finish(ident,{'recording_id':ident,'operator_outcome':'unknown'});assert writer.done.wait(3)
        manifest=json.loads((writer.folder/'manifest.json').read_bytes());trace=manifest['embodied_episode']['operator_input_trace']
        assert len(trace)==1 and trace[0]['request_started_monotonic_ns']==2200 and trace[0]['response_returned_monotonic_ns']==2300
        raw=(writer.folder/trace[0]['source_file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==trace[0]['sha256']
        event=json.loads(raw);assert event['operator_input']==request and event['native_response']==response
        assert event['accepted_action'] is None and not event['physical_attainment_inferred']
        assert event['client_declared_metadata']['raw_axes']==[.5,0,0,0]
        assert not manifest['embodied_episode']['samples'] and manifest['analysis_only']
    finally:service.close()


def test_blocked_recording_storage_never_blocks_operator_input_receipt_callback(tmp_path):
    import threading,time
    from explorer_shared.native_capture_service import NativeCaptureService
    writer,tap,camera,clock,ident,_=setup(tmp_path,project='02',mode='keyboard')
    service=NativeCaptureService(writer,None,clock=clock);service.active_id=ident
    entered,release=threading.Event(),threading.Event()
    def blocked():
        with writer.lock:entered.set();assert release.wait(2)
    worker=threading.Thread(target=blocked);worker.start();assert entered.wait(1)
    try:
        before=time.monotonic();service.observe_operator_input({'keys':[]},{},200,2000)
        assert time.monotonic()-before<.1 and writer.operator_evidence_gap
        release.set();worker.join(1)
        service.finish(ident,{'recording_id':ident,'operator_outcome':'unknown'});assert writer.done.wait(3)
        manifest=json.loads((writer.folder/'manifest.json').read_bytes())
        assert 'OPERATOR_INPUT_EVIDENCE_GAP' in manifest['embodied_episode']['recording']['capture_blockers']
        assert manifest['analysis_only']
    finally:release.set();worker.join(1);service.close()
