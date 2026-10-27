"""ENGINEERING_GLUE: source-clock pause boundaries shared by capture adapters.

No sampling, hardware, clock conversion, interpolation or action modification.
Intervals use [pause, resume); an open pause extends through the seal boundary.
"""
from copy import deepcopy


def _ns(value):
    if type(value) is not int or value < 0:
        raise ValueError('Nonnegative integer source monotonic timestamp required')
    return value


def validate_intervals(intervals, *, started_ns=0, ended_ns=None):
    previous=_ns(started_ns)
    if ended_ns is not None and _ns(ended_ns)<previous:
        raise ValueError('Capture ends before its source start')
    if not isinstance(intervals,list) or len(intervals)>512:
        raise ValueError('Bounded ordered source pause intervals required')
    for index,part in enumerate(intervals):
        if not isinstance(part,dict) or set(part)!={'start_monotonic_ns','end_monotonic_ns'}:
            raise ValueError('Exact source pause interval required')
        start=_ns(part['start_monotonic_ns']);end=part['end_monotonic_ns']
        if start<previous or ended_ns is not None and start>ended_ns:
            raise ValueError('Source pause intervals overlap or exceed capture')
        if end is None:
            if index!=len(intervals)-1:raise ValueError('Only the final source pause may remain open')
        else:
            if _ns(end)<start or ended_ns is not None and end>ended_ns:
                raise ValueError('Source resume precedes pause or exceeds capture')
            previous=end
    return deepcopy(intervals)


def pause_intervals(intervals,at_ns,*,started_ns=0):
    result=validate_intervals(intervals,started_ns=started_ns)
    at_ns=_ns(at_ns)
    if result and result[-1]['end_monotonic_ns'] is None:
        raise ValueError('Source capture is already paused')
    result.append({'start_monotonic_ns':at_ns,'end_monotonic_ns':None})
    return validate_intervals(result,started_ns=started_ns)


def resume_intervals(intervals,at_ns,*,started_ns=0):
    result=validate_intervals(intervals,started_ns=started_ns)
    if not result or result[-1]['end_monotonic_ns'] is not None:
        raise ValueError('Source capture is not paused')
    result[-1]['end_monotonic_ns']=_ns(at_ns)
    return validate_intervals(result,started_ns=started_ns)


def source_segment(at_ns,intervals,*,started_ns=0,ended_ns=None):
    """Return a retained segment index or None; zero duration pauses still cut."""
    at_ns=_ns(at_ns)
    validate_intervals(intervals,started_ns=started_ns,ended_ns=ended_ns)
    return _segment(at_ns,intervals,started_ns,ended_ns)


def _segment(at_ns,intervals,started_ns,ended_ns):
    if at_ns<started_ns or ended_ns is not None and at_ns>=ended_ns:return None
    segment=0
    for part in intervals:
        start,end=part['start_monotonic_ns'],part['end_monotonic_ns']
        if at_ns<start:return segment
        if end is None or at_ns<end:return None
        segment+=1
    return segment


def select_source_samples(samples,intervals,*,started_ns,ended_ns,clock_domain,boot_id):
    """One canonical state/action selection, shared by seal and data consumers."""
    if not isinstance(samples,list) or len(samples)>10000:
        raise ValueError('Bounded original source sample list required')
    if any(not isinstance(value,str) or not value for value in (clock_domain,boot_id)):
        raise ValueError('Original source clock and boot identity required')
    _ns(ended_ns);validate_intervals(intervals,started_ns=started_ns,ended_ns=ended_ns)
    selection=[]
    for index,sample in enumerate(samples):
        if not isinstance(sample,dict):raise ValueError('Original source sample object required')
        if sample.get('clock_domain')!=clock_domain or sample.get('boot_id')!=boot_id:
            raise ValueError('Original sample source clock/boot changed')
        action=sample.get('action_monotonic_ns');state=sample.get('state_monotonic_ns')
        segment=_segment(_ns(action),intervals,started_ns,ended_ns)
        state_segment=_segment(_ns(state),intervals,started_ns,ended_ns)
        selected=segment is not None and state_segment==segment
        selection.append({'source_sample_index':index,'action_monotonic_ns':action,'state_monotonic_ns':state,
            'recording_segment_id':segment if selected else None,'included_after_pause_filter':selected,
            'selection_basis':'source_state_and_action_in_same_unpaused_segment',
            'exclusion_reason':None if selected else 'ACTION_OUTSIDE_UNPAUSED_SEGMENT' if segment is None else 'STATE_CROSSES_LIFECYCLE_BOUNDARY'})
    return selection
