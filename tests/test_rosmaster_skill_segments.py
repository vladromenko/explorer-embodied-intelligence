"""Pause boundaries are original Source facts, never mutable manifest scalars."""
from copy import deepcopy

import pytest

from explorer_shared.learning.rosmaster_skill_segments import verified_recording_segments,chunk_slots
from explorer_shared.learning.rosmaster_skill_features import PipelineBlocked
from test_embodied_episode import source_facts,package


def segmented():
    facts,assets=source_facts();template=deepcopy(facts['samples'][0])
    facts['samples']=[deepcopy(template) for _ in range(4)]
    facts['recording']['capture_intervals']={'started_monotonic_ns':1,'pause_intervals':[{'start_monotonic_ns':30,'end_monotonic_ns':40}]}
    for index,sample in enumerate(facts['samples']):sample.update(recording_segment_id=index//2,recording_segment={'index':index//2})
    envelope,payloads=package(facts,assets)
    for index,sample in enumerate(envelope['samples']):sample['recording_segment']['provenance']={
        'source_file':'facts.json','sha256':envelope['source_files_sha256']['facts.json'],'json_pointer':'/samples/'+str(index)+'/recording_segment'}
    return envelope,payloads


def test_source_provenance_and_chunk_masks_preserve_all_target9_values_without_crossing_pause():
    envelope,payloads=segmented();segments=verified_recording_segments(envelope,payloads);assert segments==[0,0,1,1]
    frames=[{'original_sample_index':index,'recording_segment_id':segment,'action':[float(index)]*9} for index,segment in enumerate(segments)]
    slots,pad=chunk_slots(frames,1,3)
    assert slots==[1,1,1] and pad==[False,True,True]
    assert all(len(frames[slot]['action'])==9 for slot in slots)
    assert chunk_slots(frames,2,3)==([2,3,3],[False,False,True])
    frames[3]['original_sample_index']=9;assert chunk_slots(frames,2,3)==([2,2,2],[False,True,True])


@pytest.mark.parametrize('change',['scalar','source_record','missing_proof','missing_segment','order'])
def test_mutable_scalar_or_unbound_record_never_controls_chunk_boundaries(change):
    envelope,payloads=segmented()
    if change=='scalar':envelope['samples'][1]['recording_segment_id']=9
    elif change=='source_record':envelope['samples'][1]['recording_segment']['index']=9;envelope['samples'][1]['recording_segment_id']=9
    elif change=='missing_proof':envelope['samples'][1]['recording_segment'].pop('provenance')
    elif change=='missing_segment':
        for sample in envelope['samples']:sample.pop('recording_segment')
    else:envelope['samples'].reverse()
    with pytest.raises(PipelineBlocked):verified_recording_segments(envelope,payloads)


def test_old_unpaused_source_is_one_segment_but_pause_cannot_be_hidden_by_dropping_bound_record():
    envelope,payloads=package();assert verified_recording_segments(envelope,payloads)==[0]*len(envelope['samples'])
    envelope,payloads=segmented()
    for sample in envelope['samples']:sample.pop('recording_segment')
    with pytest.raises(PipelineBlocked,match='PROOF_REQUIRED'):verified_recording_segments(envelope,payloads)
