"""ENGINEERING_GLUE: explicit action and observation provenance contracts v1."""
import hashlib
import json
import math
import time
import uuid

ACTION_NAMES = ('base', 'shoulder', 'elbow', 'wrist_pitch', 'wrist_roll', 'gripper', 'vx', 'vy', 'wz')
ACTION_UNITS = ('deg', 'deg', 'deg', 'deg', 'deg', 'deg', 'm/s', 'm/s', 'rad/s')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def action_vector(values):
    result = [float(value) for value in values]
    if len(result) != 9 or not all(math.isfinite(value) for value in result):
        raise ValueError('Nine finite action coordinates are required')
    return result


def check_sample(sample):
    """Reject post-action/future observations; monotonic domains remain distinct."""
    required = ('clock_domain', 'observation_monotonic_ns', 'dispatch_monotonic_ns', 'state_source')
    if any(key not in sample for key in required):
        raise ValueError('Incomplete causal provenance')
    if sample['observation_monotonic_ns'] > sample['dispatch_monotonic_ns']:
        raise ValueError('Observation is later than the action it supervises')
    if sample['state_source'] not in ('measured_state', 'command_estimate', 'vision_estimate', 'simulated_state'):
        raise ValueError('Explicit state provenance required')
    if sample.get('dispatch_clock_domain', sample['clock_domain']) != sample['clock_domain']:
        raise ValueError('Cross-domain timestamps need explicit synchronization conversion')
    return sample


def event(node_id, project_id, payload, run_id=None):
    return dict(event_id=uuid.uuid4().hex, node_id=node_id, project_id=project_id,
                run_id=run_id, schema_version=1, wall_utc=time.time(),
                monotonic_ns=time.monotonic_ns(), clock_domain=node_id + ':' + _boot_id(),
                payload=payload, sha256=digest(payload))


def _boot_id():
    try:
        from pathlib import Path
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return 'local-process'

