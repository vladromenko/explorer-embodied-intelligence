import numpy as np

from explorer_shared.node_agent import CameraSource


def test_exact_history_before_dispatch_never_uses_newer_cached_image(monkeypatch):
    monkeypatch.setattr('explorer_shared.node_agent.time.monotonic_ns',lambda:1000)
    source=CameraSource('test','mock',stale_s=2.)
    first=b'\xff\xd8original-one\xff\xd9'; second=b'\xff\xd8original-two\xff\xd9'
    source.publish(first,2,2,monotonic_ns=100)
    first_id=source.latest().frame_id
    source.publish(second,2,2,monotonic_ns=300)
    assert source.latest().jpeg == second
    assert source.before(200).jpeg == first
    assert source.frame_by_id(first_id).jpeg == first
    assert source.before(200,max_age_ns=99) is None
    assert source.before(99) is None and source.before(1001) is None


def test_bounded_history_limits_bytes_including_bgr_and_reports_retention_gaps(monkeypatch):
    monkeypatch.setattr('explorer_shared.node_agent.time.monotonic_ns',lambda:1000)
    source=CameraSource('test','mock')
    source._history_limit_bytes=100
    image=np.zeros((4,4,3),dtype=np.uint8)
    source.publish(b'\xff\xd8one\xff\xd9',4,4,bgr=image,monotonic_ns=100)
    first_id=source.latest().frame_id
    source.publish(b'\xff\xd8two\xff\xd9',4,4,bgr=image.copy(),monotonic_ns=200)
    assert source._history_bytes<=100
    assert source.frame_by_id(first_id) is None and source.before(150) is None
    assert source.latest().monotonic_ns == 200
