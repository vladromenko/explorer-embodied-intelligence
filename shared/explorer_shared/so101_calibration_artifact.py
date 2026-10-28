"""Export only the existing configured calibration file; never open a device."""
import base64
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from .transfer_mapping import SourceCoordinates

HASH=re.compile('[0-9a-f]{64}')


def configured_calibration_artifact(station,role,expected_sha256):
    if role not in ('leader','follower') or not isinstance(expected_sha256,str) or not HASH.fullmatch(expected_sha256):
        raise ValueError('Explicit configured role and expected original calibration SHA required')
    with station.lock:
        configured=deepcopy(station.configuration['roles'][role]);profile=deepcopy(station.profiles.get(role) or {})
    path=Path(configured['calibration'])
    if path.stat().st_size>128*1024:raise ValueError('Existing calibration metadata exceeds source bound')
    raw=path.read_bytes();coordinates=SourceCoordinates(raw,expected_sha256)
    with station.lock:
        if station.configuration['roles'][role]!=configured:
            raise ValueError('Configured source calibration changed while reading')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=coordinates.sha256:
        raise ValueError('Original calibration changed while reading')
    return dict(schema='explorer_so101_original_calibration_artifact_v1',role=role,sha256=coordinates.sha256,
        raw_base64=base64.b64encode(raw).decode('ascii'),bytes=len(raw),
        basis='configured_original_file_only',historically_verified_on_connected_hardware=
            profile.get('calibration_sha256')==coordinates.sha256 and profile.get('verified_on_connected_hardware') is True,
        connected_device_reverified_by_this_read=False,measured_state_provided=False,
        device_IO_performed=False,controller_settings_written=False,physical_execution_authorized=False)
