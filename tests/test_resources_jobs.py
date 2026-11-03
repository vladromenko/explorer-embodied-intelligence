from pathlib import Path
import pytest
from so101_lab.resources import ResourceLease,ResourceBusy,assert_hardware_available,snapshot

def test_exclusive_lease():
    with ResourceLease('test-exclusive'):
        with pytest.raises(ResourceBusy):
            with ResourceLease('test-exclusive'):pass
    with ResourceLease('test-exclusive'):pass

def test_missing_device_fail_closed(tmp_path):
    with pytest.raises(ResourceBusy):assert_hardware_available([tmp_path/'missing-device'])

def test_resource_snapshot_no_hardware_open():
    s=snapshot();assert s['memory_available_bytes']>0;assert s['disk_free_bytes']>0
