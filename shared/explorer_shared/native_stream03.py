"""ENGINEERING_GLUE: Startup-mounted adapter for the EXISTING factory ManualArm/ManualTeleop.

No ROS/serial/device creation, finite trajectory queue, readback or settings
writes. Install only in the original owner process after reviewing its startup.
Host publication receipts never establish MCU dispatch, attained q or contact.
"""
from collections import deque
from copy import deepcopy
import hashlib
import json
import math
import threading
import time
import uuid

import numpy as np

from .kinematics import ObjectRegistration

BASE_ACTIONS = ('forward', 'backward', 'left', 'right', 'turn_left', 'turn_right')
AUDITED_FACTORY_SHA256 = {
    'src/manual_arm.py': '8ddadb774fb166878d8856974bcea3c075ad8014af7f3c28247cea0e0b8ecdb8',
    'src/manual_teleop.py': '809c465dae4d919f71e5e83e6bd111480bdb697f69552104a488519f85873ac1',
    'src/servo_coordinates.py': '6dbfbcaa238b04e2ddb61e08d43f1ebee250e8bfd8ca273d83ff2e1008df6b2f',
    'src/arm_velocity.py': 'ee5300c7731b47806201e5509e8cad69e275f4e87812fb79d91964638159fd7c',
    'config/arm-motion.json': '8ab3bc35a2d88f78050c720d1af8c97dcfffb500f20fcc04fee6a95a8dd4ca3a',
}


def audit_factory_binding(source_bytes):
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in source_bytes.items()}
    exact = all(hashes.get(name) == digest for name, digest in AUDITED_FACTORY_SHA256.items())
    motion = json.loads(source_bytes.get('config/arm-motion.json', b'{}'))
    stream = motion.get('streaming', {})
    return {'schema': 'explorer_factory_stream_binding_v1', 'exact_audited_source': exact,
            'source_sha256': hashes, 'native_backend': 'factory_micro_ros_ruckig_velocity',
            'original_stream_config': deepcopy(stream), 'settings_written': False,
            'servo_mapping_basis': 'original_servo_coordinates.to_radians/to_servo',
            'physical_qualification': False}


def _vector(value, length):
    array = np.asarray(value, dtype=float)
    if array.shape != (length,) or not np.isfinite(array).all():
        raise ValueError('Finite vector of the exact native shape required')
    return array


class NativeDispatchTap:
    """Bounded in-memory after-publication trace; no I/O in native servo locks."""
    def __init__(self, *, boot_id, clock_domain, capacity=4096, clock=time.monotonic_ns):
        if not boot_id or not clock_domain or type(capacity) is not int or capacity < 2:
            raise ValueError('Original boot/clock and bounded receipt capacity required')
        self.boot_id, self.clock_domain, self.clock = boot_id, clock_domain, clock
        self.lock = threading.RLock()
        self.rows, self.sequence = deque(maxlen=capacity), 0
        self.arm, self.base, self.arm_receipt, self.base_receipt = None, None, None, None
        self.reference = None
        self.capture_failures = 0
        self.intents = {}

    def bind_intent(self, generation, native_received_at, binding):
        if type(generation) is not int or not isinstance(native_received_at, (float,int)) or not math.isfinite(native_received_at):
            raise ValueError('Original native intent correlation fields required')
        with self.lock:
            self.intents[(generation, repr(native_received_at))] = deepcopy(binding)
            if len(self.intents) > self.rows.maxlen:
                self.intents.pop(next(iter(self.intents)))

    def observe(self, kind, payload, *, source_id, started_ns=None):
        try:
            return self.append(kind, payload, source_id=source_id, started_ns=started_ns)
        except Exception:
            # Optional recording failure cannot become a new native motor fault.
            # The counter prohibits a downstream lossless/expert claim.
            with self.lock:
                self.capture_failures += 1
            return None

    def reference_header(self, record, original_sha256):
        if (record.get('boot_id') != self.boot_id or len(original_sha256) != 64 or
                record.get('measured') is not False):
            raise ValueError('Original same-boot command reference required; never measured feedback')
        _vector(record.get('servo_deg'), 6)
        with self.lock:
            self.reference = {'original': deepcopy(record), 'source_sha256': original_sha256,
                              'reference_id': original_sha256, 'reference_receipt_id': 'factory-reference:'+original_sha256,
                              'state_source': 'command_estimate', 'attainment_measured': False}

    def append(self, kind, payload, *, source_id, started_ns=None):
        with self.lock:
            self.sequence += 1
            receipt_id = self.boot_id + ':native-host:' + str(self.sequence)
            prior = None
            if kind == 'arm_host_publish' and payload.get('q_estimated_deg') is not None and self.base is not None:
                prior = {'values': _vector(payload['q_estimated_deg'],6).tolist() + list(self.base),
                         'state_source': 'command_estimate',
                         'estimator_id': 'original-factory-ruckig:'+AUDITED_FACTORY_SHA256['src/arm_velocity.py'],
                         'reference_id': self.reference['reference_id'] if self.reference else None,
                         'last_dispatch_receipt_id': self.arm_receipt or (self.reference['reference_receipt_id'] if self.reference else None),
                         'base_component_receipt_id': self.base_receipt,
                         'timestamp_basis': 'native_host_before_publish_cache_observation'}
            if kind == 'arm_host_publish':
                self.arm = _vector(payload.get('q_commanded_deg'), 6).tolist()
                self.arm_receipt = receipt_id
            elif kind in ('base_drive_host_request', 'base_stop_host_request', 'base_release_host_request'):
                self.base = _vector(payload.get('velocity'), 3).tolist()
                self.base_receipt = receipt_id
            row = {'schema': 'explorer_native_host_dispatch_v1', 'sequence': self.sequence,
                   'receipt_id': receipt_id, 'kind': kind, 'source_id': source_id,
                   'stamp': {'clock_domain': self.clock_domain, 'boot_id': self.boot_id,
                             'monotonic_ns': self.clock()},
                   'call_started_monotonic_ns': started_ns, 'original_payload': deepcopy(payload),
                   'native_host_publication_returned': True, 'hardware_dispatch_verified': False,
                   'state_source': 'command_estimate', 'measured_q': None,
                   'measured_q_mask': [False] * 6, 'attainment_measured': False,
                   'contact_verified': False}
            row['preceding_command9'] = prior
            if self.arm is not None and self.base is not None:
                row.update(command9=list(self.arm) + list(self.base), command9_mask=[True] * 9,
                           component_receipt_ids=[self.arm_receipt] * 6 + [self.base_receipt] * 3,
                           command9_semantics='current_native_command_snapshot_after_this_publication',
                           components_published_in_this_event=list(range(6)) if kind == 'arm_host_publish' else [6,7,8])
            else:
                row.update(command9=(list(self.arm) if self.arm is not None else [None]*6) +
                                      (list(self.base) if self.base is not None else [None]*3),
                           command9_mask=[self.arm is not None]*6 + [self.base is not None]*3,
                           block_reasons=['ARM_AND_BASE_NATIVE_COMMAND_CONTEXT_NOT_BOTH_OBSERVED'])
            self.rows.append(row)
            return deepcopy(row)

    def since(self, after_sequence=0, limit=None):
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError('Nonnegative original receipt sequence required')
        if limit is not None and (type(limit) is not int or not 1<=limit<=1024):
            raise ValueError('Bounded native receipt page required')
        with self.lock:
            first = self.rows[0]['sequence'] if self.rows else self.sequence + 1
            selected=[row for row in self.rows if row['sequence']>after_sequence]
            selected=selected[:limit] if limit is not None else selected
            bindings=[self.intents.get((row['original_payload'].get('command_generation'),
                                        repr(row['original_payload'].get('input_received_at')))) for row in selected]
            metadata={'schema': 'explorer_native_dispatch_page_v1', 'boot_id': self.boot_id,
                    'clock_domain': self.clock_domain, 'after_sequence': after_sequence,
                    'next_sequence': selected[-1]['sequence'] if selected else after_sequence,
                    'available_latest_sequence':self.sequence,'gap': after_sequence < first - 1,
                    'capture_failures': self.capture_failures,
                    'first_available_sequence': first,
                    'reference': self.reference}
        # Copy/JSON work is outside the native publication lock. Objects stored
        # in the ring/binding table are immutable after insertion.
        rows=deepcopy(selected)
        for row,binding in zip(rows,bindings):
            row['source_intent_binding']=deepcopy(binding)
            row['source_intent_link_basis']='exact_native_generation_and_input_received_at_not_cross_device_clock_conversion'
        return {**metadata,'reference':deepcopy(metadata['reference']),'receipts':rows}


class NativeTeleopMux:
    """SO101 arm intent and keyboard base share ONE native keyboard session.

    The original ManualTeleop continues to own its lease/tick/base transform and
    the original ManualArm owns Ruckig, limits, collision checks and publisher.
    Leader packets never refresh the user-observation lease. Base-only keyboard
    packets never replace the arm intent. No second servo worker is created.
    """
    def __init__(self, teleop, manual_arm, tap, *, chassis_motion_allowed=False, clock=time.monotonic):
        if teleop.arm is not manual_arm or getattr(manual_arm, 'supports_velocity_stream', False) is not True:
            raise ValueError('Bind the original existing shared native arm instance')
        self.teleop, self.arm, self.tap, self.clock = teleop, manual_arm, tap, clock
        self.chassis_allowed = chassis_motion_allowed
        self.active, self.installed, self.state = False, False, 'NOT_INSTALLED'
        self.epoch, self.generation, self.sequence = None, None, -1
        self.latest, self.intent_deadline, self.dropped = [0.] * 6, 0., 0
        self.latest_source = None
        self.original = {}

    def install(self):
        """Call once at original-owner startup; this does not select or resume."""
        with self.teleop.lock:
            if self.installed:
                raise RuntimeError('Mux already installed')
            self.original = {'arm_update': self.teleop._update_arm_stream_locked,
                             'arm_publish': self.arm._publish_command,
                             'base_drive': self.teleop.drive, 'base_release': self.teleop.release,
                             'stop_all': self.teleop.stop_all}
            self.teleop._update_arm_stream_locked = self._arm_update
            self.arm._publish_command = self._arm_publish
            self.teleop.drive = self._base_drive
            self.teleop.release = self._base_release
            self.teleop.stop_all = self._stop_all
            self.installed, self.state = True, 'IDLE'

    def _arm_publish(self, pose, runtime_ms, record, phase='command_in_progress', revision=None):
        started = self.tap.clock()
        result = self.original['arm_publish'](pose, runtime_ms, record, phase=phase, revision=revision)
        self.tap.observe('arm_host_publish', record, source_id='factory_manual_arm._publish_command', started_ns=started)
        return result

    def _base_drive(self, velocity):
        values = _vector(velocity, 3).tolist()
        if not self.chassis_allowed and any(abs(value) > 1e-12 for value in values):
            raise PermissionError('PHYSICAL_CHASSIS_PROHIBITED_BY_OWNER')
        started = self.tap.clock()
        result = self.original['base_drive'](values)
        self.tap.observe('base_drive_host_request', {'velocity': values, 'native_return': result},
                        source_id='original_manual_teleop.drive', started_ns=started)
        return result

    def _base_release(self):
        started = self.tap.clock()
        result = self.original['base_release']()
        self.tap.observe('base_release_host_request', {'velocity': [0.,0.,0.], 'native_return': result},
                        source_id='original_manual_teleop.release', started_ns=started)
        return result

    def _stop_all(self):
        self.active, self.state, self.latest = False, 'STOPPED', [0.] * 6
        started = self.tap.clock()
        result = self.original['stop_all']()
        self.tap.observe('base_stop_host_request', {'velocity': [0.,0.,0.], 'native_return': result},
                        source_id='original_global_stop', started_ns=started)
        return result

    def begin(self, *, observing, neutral):
        with self.teleop.lock:
            if (not self.installed or self.active or observing is not True or neutral is not True or
                    self.teleop.owner != 'keyboard' or not self.teleop.stop_latched or
                    any(abs(value) > 1e-8 for value in self.teleop.inputs.values())):
                raise ValueError('Explicit selected keyboard owner, STOP and observed neutral required')
            self.epoch = uuid.uuid4().hex
            self.generation = self.teleop.generation
            self.active, self.state, self.sequence = True, 'CLUTCHED', -1
            self.latest, self.intent_deadline = [0.] * 6, self.teleop.lease
            return self.status()

    def recenter(self, epoch):
        with self.teleop.lock:
            self._check(epoch)
            self.latest, self.intent_deadline, self.state = [0.] * 6, self.teleop.lease, 'CLUTCHED'
            return self.status()

    def resume(self, epoch, *, observing):
        with self.teleop.lock:
            self._check(epoch)
            if self.state != 'CLUTCHED' or any(abs(value) > 1e-8 for value in self.latest):
                raise ValueError('Explicit recenter and neutral required before resume')
            result = self.teleop.resume('keyboard', observing)
            self.state, self.intent_deadline = 'ACTIVE', self.teleop.lease
            return {'native': result, **self.status()}

    def _check(self, epoch):
        if (not self.active or epoch != self.epoch or self.teleop.owner != 'keyboard' or
                self.generation != self.teleop.generation):
            raise ValueError('Old, stopped or preempted control epoch')

    def leader_intent(self, epoch, source_sequence, joint_velocity_deg_s, source_evidence):
        velocities = _vector(joint_velocity_deg_s, 6).tolist()
        with self.teleop.lock:
            self._check(epoch)
            if type(source_sequence) is not int or source_sequence <= self.sequence:
                self.dropped += 1
                raise ValueError('Out-of-order leader sample dropped')
            if self.state != 'ACTIVE' or self.clock() >= self.teleop.lease:
                raise ValueError('Observed native user lease/recenter is not active')
            if not isinstance(source_evidence, dict) or not all(source_evidence.get(key) for key in
                    ('clock_domain', 'boot_id', 'sample_id', 'calibration_sha256')):
                raise ValueError('Original measured leader source identity required')
            if source_evidence.get('state_source') != 'measured_leader':
                raise ValueError('Command echo is not a measured leader sample')
            self.latest, self.sequence = velocities, source_sequence
            self.latest_source = {'control_epoch': self.epoch, 'source_sequence': source_sequence,
                                  'source_evidence': deepcopy(source_evidence),
                                  'received_target_monotonic_ns': self.tap.clock(),
                                  'target_clock_domain': self.tap.clock_domain, 'target_boot_id': self.tap.boot_id,
                                  'input_adapter': {'id':'so101_leader','version':'1','input_mode':'so101_leader'},
                                  'cross_device_clock_mapping_assumed': False}
            # Snapshot the existing native lease; keyboard refresh cannot keep a
            # lost leader's last velocity alive indefinitely.
            self.intent_deadline = self.teleop.lease
            return {'accepted': True, 'latest_intent': True, 'source_sequence': source_sequence,
                    'native_dispatched': False, 'hardware_dispatch_verified': False,
                    'source_evidence': deepcopy(source_evidence), **self.status()}

    def keyboard_base_input(self, epoch, actions, *, observing):
        with self.teleop.lock:
            self._check(epoch)
            if any(key not in BASE_ACTIONS for key in actions):
                raise ValueError('Arm input belongs to the measured leader in this session')
            clean = {key: float(value) for key, value in actions.items()}
            if not self.chassis_allowed and any(abs(value) > 1e-12 for value in clean.values()):
                raise PermissionError('PHYSICAL_CHASSIS_PROHIBITED_BY_OWNER')
            return self.teleop.update('keyboard', clean, observing, False)

    def _arm_update(self, values, precision):
        if not self.active:
            return self.original['arm_update'](values, precision)
        if self.teleop.owner != 'keyboard' or self.teleop.generation != self.generation:
            self.active, self.state, self.latest = False, 'PREEMPTED', [0.] * 6
            return self.original['arm_update'](values, precision)
        if self.clock() >= self.intent_deadline and any(abs(value) > 1e-8 for value in self.latest):
            self.latest, self.state = [0.] * 6, 'SOURCE_EXPIRED_RECENTER_REQUIRED'
        velocity = list(self.latest) if self.state == 'ACTIVE' else [0.] * 6
        generation, epoch = self.generation, self.epoch
        permit = lambda: (self.active and self.epoch == epoch and self.teleop._stream_permitted(generation))
        result = self.arm.stream_velocity(velocity, [0.,0.,0.], generation, self.teleop.lease,
                                          permit, owner='keyboard', coordinated=True)
        if result.get('accepted') is True:
            with self.arm.stream_lock:
                intent = dict(self.arm.stream_intent or {})
            if intent.get('owner') == 'keyboard' and intent.get('generation') == generation:
                binding = {'source': deepcopy(self.latest_source), 'mux_state': self.state,
                           'requested_velocity_deg_s': velocity, 'native_valid_until': intent.get('valid_until'),
                           'accepted_target_monotonic_ns': self.tap.clock(),
                           'native_api_acceptance_is_host_publication': False}
                self.tap.bind_intent(generation,intent['received_at'],binding)
        self.teleop.arm_busy = self.arm.streaming_active
        return result

    def stop(self, epoch):
        with self.teleop.lock:
            self._check(epoch)
            self.active, self.state, self.latest = False, 'STOPPED', [0.] * 6
            return self.teleop.stop()

    def status(self):
        with self.teleop.lock:
            return {'schema': 'explorer_native_retarget_mux_v1', 'state': self.state, 'active': self.active,
                    'control_epoch': self.epoch, 'native_generation': self.generation,
                    'latest_source_sequence': self.sequence, 'out_of_order_dropped': self.dropped,
                    'native_owner': self.teleop.owner, 'native_lease_remaining_s': max(0., self.teleop.lease-self.clock()),
                    'arm_backend': 'existing_factory_ruckig_velocity', 'native_queue_length': 0,
                    'chassis_motion_allowed': self.chassis_allowed, 'measured_joint_feedback': False}


class RelativeLeaderGeometry:
    """Measured leader -> source FK -> clutch -> target IK -> latest velocity.

    The original factory servo-coordinate functions are passed in, not replaced.
    Source normalized-position physical mapping and gripper curves remain an
    explicit registration dependency; missing registration is UNREGISTERED.
    """
    def __init__(self, source_chain, target_chain, source_mapper, gripper_mapper,
                 servo_to_radians, radians_to_servo, *, registration=None):
        self.source, self.target = source_chain, target_chain
        self.source_mapper, self.gripper_mapper = source_mapper, gripper_mapper
        self.to_radians, self.to_servo = servo_to_radians, radians_to_servo
        self.registration, self.anchor = deepcopy(registration), None

    def status(self):
        valid = (isinstance(self.registration, dict) and self.registration.get('provenance') in ('measured', 'synthetic') and
                 isinstance(self.registration.get('sha256'), str) and len(self.registration['sha256']) == 64 and
                 self.registration.get('source_urdf_sha256') == self.source.source_sha256 and
                 self.registration.get('target_urdf_sha256') == self.target.source_sha256 and
                 all(callable(function) for function in (self.source_mapper, self.gripper_mapper,
                                                          self.to_radians, self.to_servo)))
        return {'state': 'REGISTERED_DECLARATION' if valid else 'UNREGISTERED',
                'registration': deepcopy(self.registration), 'physical_acceptance': False}

    def recenter(self, measured_leader, commanded_target_servo):
        if self.status()['state'] == 'UNREGISTERED':
            raise ValueError('Source normalized-to-physical axes/zero/scale and gripper registration missing')
        leader = _vector(measured_leader, 6)
        target = _vector(commanded_target_servo, 6)
        source_q = _vector(self.source_mapper(leader[:5]), len(self.source.names))
        target_q = _vector(self.to_radians(target[:5]), len(self.target.names))
        self.anchor = ObjectRegistration(self.source.forward(source_q), self.target.forward(target_q), 'virtual_declared')
        return {'recentered': True, 'target_basis': 'native_command_estimate_not_measured',
                'physical_commands': 0}

    def propose(self, measured_leader, commanded_target_servo, *, original_native_period_s):
        if self.anchor is None:
            raise ValueError('Explicit clutch/recenter required')
        if not isinstance(original_native_period_s, (int, float)) or not math.isfinite(original_native_period_s) or original_native_period_s <= 0:
            raise ValueError('Original native stream period must be supplied unchanged')
        leader, estimated = _vector(measured_leader, 6), _vector(commanded_target_servo, 6)
        source_q = _vector(self.source_mapper(leader[:5]), len(self.source.names))
        target_q = _vector(self.to_radians(estimated[:5]), len(self.target.names))
        result = self.target.inverse(self.anchor.transfer(self.source.forward(source_q)), target_q,
                                      orientation_weight=.03, restarts=0, seed=0)
        if not result.reached:
            return {'accepted': False, 'reason': 'IK_UNREACHABLE', 'ik': result.as_dict(),
                    'joint_velocity_deg_s': [0.] * 6, 'native_dispatched': False}
        goal = np.concatenate((_vector(self.to_servo(result.positions), 5),
                               _vector([self.gripper_mapper(float(leader[5]))], 1)))
        return {'accepted': True, 'ik': result.as_dict(), 'desired_servo_deg': goal.tolist(),
                'joint_velocity_deg_s': ((goal-estimated)/original_native_period_s).tolist(),
                'state_source': 'command_estimate', 'native_dispatched': False,
                'controller_limits_applied_by': 'unchanged_original_native_Ruckig_generator'}
