"""Explicit04 runtime boundary; no peer config/client/leader worker is created."""
import uuid

from .pi_native_retarget import LegacyRawView


class SourceOnlyRetarget:
    def __init__(self, state_dir):
        from pathlib import Path
        self.epoch = uuid.uuid4().hex
        self.recorder = LegacyRawView(Path(state_dir) / 'retarget-recordings')

    def status(self):
        return {'schema': 'explorer_disabled_direct03_runtime_v1',
                'state': 'SOURCE04_ONLY', 'enabled': False,
                'reason': 'CONTINUITY_QUALIFICATION_REQUIRED',
                'runtime_profile': 'so101_source_only', 'control_epoch': self.epoch,
                'peer_configured': False, 'source_cache_claimed': False,
                'lifecycle_inflight': False, 'step_inflight': False, 'inflight': False,
                'pending_stops': 0, 'stop_delivery_uncertain': False,
                'target_control_epoch': None, 'calibration_wizard': None,
                'recording': self.recorder.status(), 'worker_started': False,
                'native_control_calls': 0, 'native_STOP_delivered': False,
                'controller_configuration_writes': 0, 'physical_acceptance_confirmed': False}

    def command(self, operation, body):
        raise RuntimeError('CONTINUITY_QUALIFICATION_REQUIRED: direct03 disabled by source04 runtime profile')

    def start_worker(self):
        pass

    def stop(self, *args, **kwargs):
        # Explicit global Station STOP is handled by the original Station owner.
        # There is no retained03 target epoch or client to stop in this profile.
        return self.status()

    def close(self):
        pass
