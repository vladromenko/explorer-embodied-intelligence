"""Admission must reject cached camera timestamps from the future or stale past."""
import time

from explorer_shared.node_agent import CameraSource


def test_existing_camera_freshness_rejects_future_without_changing_its_stale_setting():
    source=CameraSource('pi-overview','software fixture',stale_s=2)
    jpeg=b'\xff\xd8software-sensor-fixture\xff\xd9'
    source.publish(jpeg,640,480,monotonic_ns=time.monotonic_ns()+10_000_000_000)
    assert source.latest() is None and not source.metadata()['available']
    source.publish(jpeg,640,480,monotonic_ns=time.monotonic_ns()-3_000_000_000)
    assert source.latest() is None
    source.publish(jpeg,640,480)
    assert source.latest().jpeg==jpeg and source.stale_s==2
