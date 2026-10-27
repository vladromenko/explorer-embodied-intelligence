"""Reuse original Station recorder/converter; short source pause must cut chunks."""
import hashlib
import io
import json

import numpy as np
from PIL import Image
import pytest

from explorer_sim.station_adapter import convert_station_episode
from test_so101_capture_adapter import capture, context


def seal_real_RGB(capture,*,phase_annotation=False):
    adapter,source,clock,_=capture;start=clock();original_sample=source.station._sample;counter=[0]
    def sample(frame,*args):
        counter[0]+=1;image=Image.new('RGB',(4,3),(counter[0]*30,40,50));buffer=io.BytesIO();image.save(buffer,format='JPEG')
        frame.jpeg=buffer.getvalue();return original_sample(frame,*args)
    source.station._sample=sample
    ident=adapter.start(context(),'fixture-owner')['recording_id']
    source.sample(start+110_000_000);clock.value=start+120_000_000;adapter.pause(ident)
    source.sample(start+220_000_000);clock.value=start+230_000_000;adapter.resume(ident)
    if phase_annotation:
        source.station.record_stage({'recording_id':ident,'phase_id':'grasp','request_id':'software-phase-proof'})
    source.sample(start+330_000_000);clock.value=start+350_000_000;envelope=adapter.finish(ident)
    original_path=source.station.root/'recordings'/ident/'manifest.json';capture_path=adapter.root/ident/'manifest.json'
    digest=hashlib.sha256(capture_path.read_bytes()).hexdigest()
    return ident,original_path,capture_path,digest,envelope


def test_source_pause_shorter_than_old_gap_threshold_cuts_real_RGB_Dataset_view_without_rewriting_raw(capture,tmp_path):
    ident,original,envelope,checksum,sealed=seal_real_RGB(capture,phase_annotation=True)
    raw=original.read_bytes();before=envelope.read_bytes();target=tmp_path/'converted'
    result=convert_station_episode(original,target,capture_manifest_path=envelope,expected_capture_sha256=checksum)
    assert result['source_pause_selection_verified'] and result['conversion_version']==3
    assert result['resampling']['excluded_source_capture_indices']==[1]
    assert len(result['episodes'])==2 and [row['source_indices'] for row in result['episodes']]==[[0],[2]]
    assert [row['recording_segment_id'] for row in result['episodes']]==[0,1]
    with np.load(target/'source_originals/native_samples.npz',allow_pickle=False) as all_raw:
        assert len(all_raw['state_native'])==3 and len(all_raw['action_native'])==3
    assert original.read_bytes()==raw and envelope.read_bytes()==before
    assert (target/'source_originals/manifest.json').read_bytes()==raw
    assert (target/'source_originals/capture-envelope.json').read_bytes()==before
    for episode,index in zip(result['episodes'],[0,2]):
        with np.load(target/episode['file'],allow_pickle=False) as data:
            assert data['source_sample_index'].tolist()==[index]
            assert data['action_native'].tolist()==[[100.,20.,30.,40.,50.,60.]]
    again=convert_station_episode(original,target,capture_manifest_path=envelope,expected_capture_sha256=checksum)
    assert again['source_capture_manifest_sha256']==checksum
    with pytest.raises(ValueError,match='different capture timeline'):convert_station_episode(original,target)


def test_forged_selection_cannot_restore_a_paused_label_even_with_new_outer_SHA(capture,tmp_path):
    ident,original,envelope,checksum,sealed=seal_real_RGB(capture)
    raw=envelope.read_bytes();forged=json.loads(raw)
    forged['sample_selection'][1].update(included_after_pause_filter=True,recording_segment_id=0,exclusion_reason=None)
    view=tmp_path/'forged.json';view.write_text(json.dumps(forged));new_sha=hashlib.sha256(view.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='sample selection changed'):
        convert_station_episode(original,tmp_path/'rejected',capture_manifest_path=view,expected_capture_sha256=new_sha)
    assert not (tmp_path/'rejected').exists() and envelope.read_bytes()==raw


def test_missing_or_wrong_timeline_SHA_is_not_an_implicit_legacy_fallback(capture,tmp_path):
    ident,original,envelope,checksum,sealed=seal_real_RGB(capture)
    with pytest.raises(ValueError,match='envelope SHA'):
        convert_station_episode(original,tmp_path/'wrong',capture_manifest_path=envelope,expected_capture_sha256='0'*64)
    with pytest.raises(ValueError,match='requires its original envelope'):
        convert_station_episode(original,tmp_path/'missing',expected_capture_sha256=checksum)
