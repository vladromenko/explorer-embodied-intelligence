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
    if at_ns<started_ns or ended_ns is not None and at_ns>=ended_ns:return None
    segment=0
    for part in intervals:
        start,end=part['start_monotonic_ns'],part['end_monotonic_ns']
        if at_ns<start:return segment
        if end is None or at_ns<end:return None
        segment+=1
    return segment
