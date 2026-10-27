"""ENGINEERING_GLUE: source-bound recording pause segments for native9 chunks.

No interpolation, relabelling, target construction or new demonstration IDs.
Only the original source JSON record can establish a segment boundary.
"""
from explorer_shared.embodied_episode import _Evidence
from .rosmaster_skill_features import PipelineBlocked


def verified_recording_segments(envelope,payloads):
    samples=envelope.get('samples') or [];recording=envelope.get('recording') or {};missing=[]
    evidence=_Evidence(envelope,payloads,missing)
    if not evidence.record(recording,'recording'):raise PipelineBlocked('RECORDING_SEGMENT_SOURCE_PROVENANCE_MISSING')
    intervals=(recording.get('capture_intervals') or {}).get('pause_intervals') or []
    marked=any(sample.get('recording_segment') is not None for sample in samples)
    if not marked:
        if intervals:raise PipelineBlocked('PAUSED_RECORDING_SEGMENT_SOURCE_PROOF_REQUIRED')
        # Legacy producers had no pause operation. Their single segment is not
        # inferred from an unverified scalar supplied in a mutable envelope.
        return [0]*len(samples)
    result=[]
    for index,sample in enumerate(samples):
        record=sample.get('recording_segment') or {};segment=record.get('index')
        if (type(segment) is not int or segment<0 or type(sample.get('recording_segment_id')) is not int or
                sample['recording_segment_id']!=segment or not evidence.record(record,'samples.'+str(index)+'.recording_segment') or
                result and segment<result[-1]):
            raise PipelineBlocked('RECORDING_SEGMENT_SOURCE_PROOF_OR_ORDER_CHANGED',str(index))
        result.append(segment)
    return result


def chunk_slots(frames,position,chunk_size):
    """Pad at an actual source pause, filtered-sample gap or episode end."""
    frame=frames[position];slots=[];padded=[];ended=False;last=position
    for offset in range(chunk_size):
        selected=position+offset
        ended=bool(ended or selected>=len(frames) or
            frames[selected]['original_sample_index']!=frame['original_sample_index']+offset or
            frames[selected]['recording_segment_id']!=frame['recording_segment_id'])
        if not ended:last=selected
        slots.append(last);padded.append(ended)
    return slots,padded
