"""ENGINEERING_GLUE: authenticated node binding for direct Pi/Nano ARM_ONLY.

The Pi consumes its existing measured cache and operator-held lease events. It
never owns a servo bus or renews the native motor lease on its own. Registration
is an independent measured artifact; missing registration never opens a route.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlsplit

from .arm_only_retarget import ArmOnlyGeometry, SOURCE_NAMES, SOURCE_UNITS
from .pi_nano_route import NanoArmGateway, NanoArmHTTPClient, PiNanoRetargetSession


def registered_geometry(config_dir, root=None):
    root = Path(root or Path(__file__).resolve().parents[2])
    profile = Path(config_dir) / 'arm-retarget-registration.json'
    try:
        return ArmOnlyGeometry(root/'04-cross-embodiment-transfer/configs/so101/so101_leader_new_calib.urdf',
            root/'01-autonomous-learning/src/legacy/config/explorer.urdf', profile)
    except (OSError, ValueError, TypeError, KeyError) as error:
        error_type = type(error).__name__
        class UnavailableGeometry:
            def status(self):
                return dict(state='UNREGISTERED', registration_id=None, registration_sha256=None,
                    missing=['invalid_registration:'+error_type], physical_acceptance_confirmed=False,
                    controller_configuration_writes=0, chassis_requests=0)
        return UnavailableGeometry()


class PiRetargetService:
    """One coalesced cached sample per explicit operator heartbeat, no auto-lease.

    The worker stays on Pi; every action travels Pi -> Nano. Idle hardware source
    reads remain owned by SO101Station. Local epochs discard queued UI events and
    a concurrent STOP cannot be undone by an old start/recenter response.
    """
    def __init__(self, geometry, station, camera, state_dir, client=None):
        self.geometry, self.station, self.camera, self.client = geometry, station, camera, client
        self.lock = threading.RLock()
        self.epoch, self.mode, self.error = uuid.uuid4().hex, 'IDLE', None
        self.lifecycle_inflight, self.step_inflight, self.last_lease_sequence = False, False, -1
        self.session = PiNanoRetargetSession(geometry, client) if client is not None else None
        self.pending, self.shutdown = threading.Event(), threading.Event()
        self.thread = None
        self.last_result, self.run_dir, self.sample_count = None, None, 0
        self.remote_cancel_required = False
        self.state_dir = Path(state_dir)
        self.journal_lock = threading.Lock()

    @classmethod
    def from_config(cls, geometry, station, camera, state_dir, config_dir):
        settings = Path(config_dir)/'arm-retarget-peer.json'
        client = None
        if settings.is_file():
            if settings.stat().st_size > 16384:
                raise ValueError('Direct-arm peer metadata exceeds its bound')
            body = json.loads(settings.read_text())
            target = urlsplit(body.get('nano_url', ''))
            if target.scheme not in ('http', 'https') or not target.hostname or target.username or target.password or target.query or target.fragment or target.path not in ('', '/'):
                raise ValueError('Direct-arm peer must be an explicit Nano observer origin')
            token = body.get('nano_node_token')
            if not isinstance(token, str) or len(token) < 16:
                raise ValueError('The existing private Nano observer token is required')
            client = NanoArmHTTPClient(body['nano_url'], token)
        return cls(geometry, station, camera, state_dir, client)

    def start_worker(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name='pi-nano-retarget', daemon=True)
            self.thread.start()

    def _journal(self, event, **payload):
        with self.journal_lock:
            run = self.run_dir
            if run is not None:
                row = dict(event=event, received_monotonic_ns=time.monotonic_ns(), clock_domain='pi-retarget:'+str(os.getpid()),
                           physical_success_inferred=False, chassis_requests=0, **payload)
                with (run/'events.jsonl').open('a') as stream:
                    stream.write(json.dumps(row, allow_nan=False, sort_keys=True)+'\n')
                    stream.flush();os.fsync(stream.fileno())

    def _measurement(self):
        if self.station is None:
            raise ValueError('Measured SO-101 station cache is unavailable')
        status = self.station.status()
        if status.get('available') is not True or status.get('mode') != 'idle':
            raise ValueError('Direct teaching requires the existing source station in idle read-only mode')
        measured = status.get('measurements') or {}
        stamp = measured.get('monotonic_ns')
        # Reuse the source camera's existing freshness contract, not a motor setting.
        freshness = self.camera.stale_s if self.camera is not None else 2.
        if type(stamp) is not int or not 0 <= time.monotonic_ns()-stamp <= freshness*1e9:
            raise ValueError('Measured leader cache is missing, stale or future-dated')
        return dict(role='leader', joint_names=list(SOURCE_NAMES), units=SOURCE_UNITS,
            positions=copy.deepcopy(measured.get('leader')), measured=True,
            calibration_sha256=(status.get('roles', {}).get('leader') or {}).get('calibration_sha256'),
            monotonic_ns=stamp, clock_domain=measured.get('clock_domain'), control_epoch=status.get('control_epoch'))

    def status(self):
        registration = self.geometry.status()
        with self.lock:
            mode = 'UNREGISTERED' if registration['state'] == 'UNREGISTERED' else self.mode
            if mode == 'IDLE' and self.client is None:
                mode = 'PEER_NOT_CONFIGURED'
            return dict(state=mode, control_epoch=self.epoch, registration=registration,
                peer_configured=self.client is not None, lifecycle_inflight=self.lifecycle_inflight,
                stop_delivery_uncertain=self.remote_cancel_required,
                source='existing_SO101Station_measured_cache', target='ROSMASTER_ARM_ONLY',
                source_station_mode=self.station.status().get('mode') if self.station is not None else None,
                last_result=copy.deepcopy(self.last_result), error=self.error, cached_samples_consumed=self.sample_count,
                run_id=self.run_dir.name if self.run_dir else None, operator_lease='unchanged_original_operator_held',
                loop='coalesced_one_cached_sample_per_explicit_held_event', controller_configuration_writes=0,
                physical_acceptance_confirmed=False, chassis_requests=0)

    def _observing(self, body):
        if body.get('observing') is not True or not ((self.camera is not None and self.camera.latest() is not None) or body.get('operator_present') is True):
            raise ValueError('A current scene observation or explicit operator presence is required')

    def _match(self, body):
        if self.mode != 'ACTIVE' or body.get('control_epoch') != self.epoch:
            raise ValueError('Direct teaching session changed; a fresh explicit start is required')

    def _begin(self, body, recenter=False):
        registration = self.geometry.status()
        if registration['state'] == 'UNREGISTERED':
            return self.status()
        if self.session is None:
            return self.status()
        self._observing(body)
        with self.lock:
            if self.remote_cancel_required:
                raise ValueError('Explicit STOP recovery is required before another start')
            if body.get('expected_control_epoch') != self.epoch or self.lifecycle_inflight or self.step_inflight or (self.mode == 'ACTIVE' and not recenter):
                raise ValueError('Refresh the direct teaching session before starting')
            if recenter and self.mode != 'ACTIVE':
                raise ValueError('Recenter requires an active direct teaching session')
            expected = self.epoch
            self.lifecycle_inflight = True
            self.pending.clear()
        try:
            measured = self._measurement()
            result = self.session.recenter(measured) if recenter else self.session.start(measured, observing=True)
            with self.lock:
                superseded = expected != self.epoch
                if not superseded:
                    self.mode = result.get('state', 'UNKNOWN_OUTCOME')
                    self.epoch, self.last_lease_sequence = uuid.uuid4().hex, -1
                    self.error = None
                    if self.mode == 'ACTIVE' and not recenter:
                        self.run_dir = self.state_dir/'direct-teaching'/uuid.uuid4().hex
                        self.run_dir.mkdir(parents=True, exist_ok=False)
                        self.sample_count = 0
            if superseded:
                if self.session.state == 'ACTIVE':
                    self.session.stop()
                raise ValueError('Start/recenter was superseded by STOP; old response discarded')
            self._journal('recenter' if recenter else 'start', source_measurement=measured, result=result,
                          registration=registration)
            return self.status()
        except Exception as error:
            with self.lock:
                self.error = type(error).__name__+': '+str(error)
                self.remote_cancel_required |= bool(self.session and self.session.state == 'UNKNOWN_OUTCOME')
                if self.epoch == expected:
                    self.mode, self.epoch = 'INTERRUPTED', uuid.uuid4().hex
            raise
        finally:
            with self.lock:
                self.lifecycle_inflight = False

    def command(self, operation, body):
        if operation == 'start':
            return self._begin(body)
        if operation == 'recenter':
            return self._begin(body, recenter=True)
        if operation == 'stop':
            return self.stop(body.get('expected_control_epoch'))
        if operation != 'lease':
            raise ValueError('Unknown direct teaching operation')
        self._observing(body)
        with self.lock:
            self._match(body)
            if self.lifecycle_inflight:
                raise ValueError('A new anchor is still being established')
            sequence = body.get('sequence')
            if type(sequence) is not int or sequence <= self.last_lease_sequence:
                raise ValueError('Non-increasing operator-held event')
            self.last_lease_sequence = sequence
            expected = self.epoch
        if body.get('held') is not True:
            return self.stop(expected)
        try:
            result = self.session.heartbeat(held=True)
            with self.lock:
                if self.epoch != expected or self.mode != 'ACTIVE':
                    raise ValueError('Lease reply belongs to an ended teaching session')
                self.pending.set()
            return self.status()
        except Exception:
            self.stop(expected)
            raise

    def stop(self, expected=None):
        with self.lock:
            if expected is not None and expected != self.epoch:
                raise ValueError('STOP belongs to an ended direct teaching session')
            self.epoch, self.mode = uuid.uuid4().hex, 'STOPPED'
            self.pending.clear()
        result = {'motion_requested': False}
        try:
            if self.session is not None and self.session.state == 'ACTIVE':
                result = self.session.stop()
            elif self.remote_cancel_required and self.client is not None:
                result = self.client.cancel()
            self.remote_cancel_required = False
            self._journal('stop', result=result)
        except Exception as error:
            with self.lock:
                self.mode, self.error = 'UNKNOWN_OUTCOME', type(error).__name__+': '+str(error)
                self.remote_cancel_required = True
            raise
        return {**self.status(), 'target_stop': result, 'physical_stop_confirmed': False}

    def _work(self):
        while not self.shutdown.is_set():
            if self.pending.wait(.1) and not self.shutdown.is_set():
                self.pending.clear()
                with self.lock:
                    expected, active = self.epoch, self.mode == 'ACTIVE' and not self.lifecycle_inflight
                    self.step_inflight = active
                if active:
                    try:
                        measured = self._measurement()
                        result = self.session.step(measured)
                        with self.lock:
                            if self.epoch == expected:
                                self.last_result = result
                                self.remote_cancel_required |= bool(result.get('target_receipt', {}).get('cancel_error'))
                                self.sample_count += 1
                                if self.session.state != 'ACTIVE':
                                    self.mode, self.epoch = self.session.state, uuid.uuid4().hex
                        self._journal('cached_source_step', source_measurement=measured, result=result)
                    except Exception as error:
                        with self.lock:
                            self.error = type(error).__name__+': '+str(error)
                        try:
                            self.stop(expected)
                        except Exception:
                            pass
                    finally:
                        with self.lock:
                            self.step_inflight = False

    def close(self):
        self.shutdown.set();self.pending.set()
        if self.thread:
            self.thread.join(timeout=10)
        self.stop()


def nano_gateway(geometry, legacy_bridge):
    return NanoArmGateway(legacy_bridge.request, geometry.status())
