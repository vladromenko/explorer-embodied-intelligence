"""The SAME source timeline/review core with existing native02 and native03."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from explorer_shared.capture_timeline import pause_intervals,resume_intervals,source_segment
from explorer_shared import sealed_capture_review as reviews
from explorer_shared.native_capture_approval import validate_source_approval
from test_native_capture import setup,publication,outcomes,sealed


@pytest.mark.parametrize('project,mode',[('02','keyboard'),('03','so101_leader')])
def test_native_pause_sample_bytes_and_source_review_receipt_abi_unchanged(tmp_path,monkeypatch,project,mode):
    writer,tap,camera,clock,ident,_=setup(tmp_path,project=project,mode=mode)
    try:
        first,_=publication(tap,camera,clock,stamp=2250,mode=mode);assert writer.submit(ident,first)
        clock.value=2300;writer.pause(ident)
        middle,_=publication(tap,camera,clock,stamp=2450,mode=mode);assert writer.submit(ident,middle)
        clock.value=2600;writer.resume(ident)
        last,_=publication(tap,camera,clock,stamp=2750,mode=mode);assert writer.submit(ident,last)
        clock.value=2800;manifest=sealed(writer,ident,outcomes(ident))
        assert [sample['recording_segment_id'] for sample in manifest['embodied_episode']['samples']]==[0,1]
        assert manifest['embodied_episode']['recording']['capture_intervals']['pause_intervals']==[
            {'start_monotonic_ns':2300,'end_monotonic_ns':2600}]
        source_pages=[path.read_bytes() for path in (writer.folder/'sources').glob('native-page-*.json')]
        assert all(page in source_pages for page in (first,middle,last))
        raw=(writer.folder/'manifest.json').read_bytes();archive=writer.archive(ident);before=hashlib.sha256(archive.read_bytes()).hexdigest()
        monkeypatch.setattr(reviews.uuid,'uuid4',lambda:SimpleNamespace(hex='e'*32))
        result=writer.review(ident,'approve','Владелец',before)
        receipt={'schema':'explorer_native_capture_review_v1','review_id':'e'*32,'recording_id':ident,
            'archive_sha256':before,'decision':'approve','actor_id':'Владелец','reviewed_native_ns':clock(),
            'source_boot_id':'boot','source_clock_domain':'nano','raw_archive_unchanged':True,'previous_review_sha256':None}
        literal=json.dumps(receipt,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        assert result=={'source_review':receipt,'review_sha256':hashlib.sha256(literal).hexdigest(),'transfer_ready':True}
        assert b'\\u0412' in (writer.folder/'review.json').read_bytes()
        assert validate_source_approval(result,ident,before)['verified']
        assert (writer.folder/'manifest.json').read_bytes()==raw and hashlib.sha256(archive.read_bytes()).hexdigest()==before
    finally:writer.close()


def test_exact_source_clock_boundaries_zero_pause_and_large_integer_stamps():
    start=2**53+31;pause=start+10;resume=pause+1;end=resume+10
    intervals=pause_intervals([],pause,started_ns=start)
    assert source_segment(pause-1,intervals,started_ns=start)==0
    assert source_segment(pause,intervals,started_ns=start) is None
    intervals=resume_intervals(intervals,resume,started_ns=start)
    assert source_segment(resume,intervals,started_ns=start,ended_ns=end)==1
    assert source_segment(end,intervals,started_ns=start,ended_ns=end) is None
    intervals=resume_intervals(pause_intervals([],pause,started_ns=start),pause,started_ns=start)
    assert source_segment(pause,intervals,started_ns=start)==1
    with pytest.raises(ValueError):pause_intervals(intervals,pause-1,started_ns=start)
    with pytest.raises(ValueError):source_segment(float(start),intervals,started_ns=start)
