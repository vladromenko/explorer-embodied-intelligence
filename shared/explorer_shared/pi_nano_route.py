"""ENGINEERING_GLUE: direct Pi/Nano arm-only transactions using the old executor.

Original plan/start/lease/stop preserves its stop revision and .6s operator lease.
No chassis request, source actuator write, NX scheduler, controller-setting write
or hardware import. Network calls happen only through explicitly invoked methods.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from urllib.request import Request, urlopen
import uuid

from .arm_only_retarget import finite_vector, six_servo_goal

OPERATIONS = {"arm/manual", "arm/trajectory", "arm/trajectory/plan", "arm/trajectory/start",
              "arm/trajectory/lease", "arm/trajectory/stop"}


class OriginalEndpointRejected(RuntimeError):
    """An explicit non-2xx reply is distinct from an ambiguous/lost start reply."""


class NanoArmGateway:
    """Node integration injects LegacyBridge.request; constructors perform no IO."""
    def __init__(self, legacy_request, registration):
        self.legacy_request, self.registration = legacy_request, dict(registration)
        self.lock = threading.RLock()
        self.epoch, self.owner, self.mode = uuid.uuid4().hex, None, "IDLE"
        self.sequence, self.target_session, self.inflight = -1, None, False
        self.last, self.reason = None, None
        self.pending_stops, self.stop_uncertain = 0, False

    def _request(self, operation, body=None):
        if operation not in OPERATIONS:
            raise ValueError("Operation outside the arm-only executor")
        code, payload = self.legacy_request(operation, body)
        if not isinstance(payload, dict) or not isinstance(code, int):
            raise ValueError("Original endpoint returned an invalid receipt")
        if not 200 <= code < 300:
            raise OriginalEndpointRejected("Original executor rejected " + operation + ": " + str(payload.get("detail", payload.get("error", code))))
        return payload

    def _invalidate(self, reason, mode="STOPPED"):
        self.epoch, self.owner, self.mode = uuid.uuid4().hex, None, mode
        self.reason = reason

    def _match(self, body):
        if self.mode != "ACTIVE" or body.get("control_epoch") != self.epoch or body.get("owner_id") != self.owner:
            raise ValueError("Ended, superseded or foreign direct-arm transaction")

    def _observe(self, state):
        with self.lock:
            if self.mode == "ACTIVE" and self.target_session:
                if state.get("phase") in {"stopped", "stopping", "failed", "unconfirmed"}:
                    self._invalidate("Original executor ended or stopped its session")
                elif not self.inflight and state.get("session") != self.target_session and (state.get("busy") or state.get("session")):
                    self._invalidate("Original executor ownership changed")

    def status(self, *, refresh=False):
        target, reference = None, None
        if refresh:
            target = self._request("arm/trajectory")
            self._observe(target)
            if not target.get("busy"):
                manual = self._request("arm/manual")
                if manual.get("blocked_by") is None and isinstance(manual.get("servo_deg"), list):
                    try:
                        finite_vector(manual["servo_deg"], 6)
                        reference = {"servo_deg": list(manual["servo_deg"]), "basis": "original_reported_command_estimate_not_measured"}
                    except ValueError:
                        reference = None
        with self.lock:
            return {"state": self.mode, "control_epoch": self.epoch, "owner_id": self.owner,
                    "registration": self.registration, "target_session": self.target_session,
                    "last_sequence": self.sequence, "inflight": self.inflight, "reason": self.reason,
                    "pending_stops": self.pending_stops, "stop_delivery_uncertain": self.stop_uncertain,
                    "target": target, "target_reference": reference, "last_receipt": copy.deepcopy(self.last),
                    "chassis_requests": 0, "dispatch_confirmed": False, "physical_stop_confirmed": False}

    def start(self, body):
        if self.registration.get("state") != "REGISTERED_DECLARATION":
            return {**self.status(), "state": "UNREGISTERED"}
        if body.get("registration_id") != self.registration.get("registration_id") or body.get("registration_sha256") != self.registration.get("registration_sha256"):
            raise ValueError("Pi/Nano registration identities differ")
        if body.get("observing") is not True or not isinstance(body.get("owner_id"), str) or not body["owner_id"]:
            raise ValueError("An explicit observed owner start is required")
        with self.lock:
            expected = self.epoch
            if body.get("expected_control_epoch") != expected or self.mode == "ACTIVE" or self.inflight or self.pending_stops or self.stop_uncertain:
                raise ValueError("Refresh the original direct-arm session before starting")
        current = self.status(refresh=True)
        if current["target"].get("busy"):
            return {**current, "state": "TARGET_BUSY"}
        if current["target_reference"] is None:
            return {**current, "state": "TARGET_REFERENCE_UNKNOWN"}
        with self.lock:
            if self.epoch != expected or self.mode == "ACTIVE" or self.inflight or self.pending_stops or self.stop_uncertain:
                raise ValueError("Direct session changed during start")
            self.epoch, self.owner, self.mode = uuid.uuid4().hex, body["owner_id"], "ACTIVE"
            self.sequence, self.target_session, self.reason = -1, None, None
            return {**self.status(), "target_reference": current["target_reference"], "motion_requested": False}

    def action(self, body):
        goal = six_servo_goal(body.get("goal"))
        if set(body) - {"goal", "control_epoch", "owner_id", "sequence", "source_measurement"}:
            raise ValueError("Unknown fields are outside the six-servo arm-only ABI")
        sequence = body.get("sequence")
        with self.lock:
            self._match(body)
            if type(sequence) is not int or sequence <= self.sequence:
                raise ValueError("Non-increasing direct-arm sequence")
            self.sequence = sequence
            epoch = self.epoch
            if self.inflight:
                return {"state": "BUSY", "requested": goal, "accepted": None, "dispatched": None}
            self.inflight = True
        receipt = {"state": "REQUESTED", "control_epoch": epoch, "sequence": sequence, "requested": goal,
                   "accepted": None, "dispatched": None, "dispatch_confirmed": False,
                   "source_measurement": copy.deepcopy(body.get("source_measurement", {})),
                   "execution_api": "original_plan_start_operator_held", "chassis_requests": 0}
        start_requested = False
        try:
            target = self._request("arm/trajectory")
            self._observe(target)
            with self.lock:
                self._match(body)
            if target.get("busy"):
                receipt["state"] = "BUSY"
                return receipt
            plan = self._request("arm/trajectory/plan", {"goal_deg": goal})
            receipt["original_plan"] = plan
            if plan.get("phase") != "planned" or not isinstance(plan.get("plan_id"), str) or len(plan["plan_id"]) != 32 or plan.get("goal_deg") != goal:
                raise ValueError("Original plan identity or accepted six-servo endpoint differs")
            with self.lock:
                if self.epoch != epoch or self.mode != "ACTIVE":
                    receipt["state"] = "CANCELLED_BEFORE_START"
                    return receipt
            # STOP after the epoch check invalidates the original pending stop revision.
            start_requested = True
            started = self._request("arm/trajectory/start", {"plan_id": plan["plan_id"], "observing": True})
            receipt["original_start"] = started
            if started.get("goal_deg") != goal or not isinstance(started.get("session"), str) or started.get("phase") != "moving":
                raise ValueError("Original executor did not acknowledge the planned target/session")
            with self.lock:
                if self.epoch != epoch or self.mode != "ACTIVE":
                    receipt["state"] = "CANCELLED_DURING_START"
                    return receipt
                self.target_session = started["session"]
                receipt.update(state="API_ACCEPTED", accepted=list(started["goal_deg"]), target_session=self.target_session)
            return receipt
        except Exception as error:
            rejected = isinstance(error, OriginalEndpointRejected)
            with self.lock:
                if self.epoch == epoch:
                    self._invalidate(type(error).__name__ + ": " + str(error), "REJECTED" if rejected else "UNKNOWN_OUTCOME")
            receipt.update(state="REJECTED" if rejected else "UNKNOWN_OUTCOME", error_type=type(error).__name__, no_automatic_retry=True,
                           motion_start_requested=start_requested)
            # Never cancel a foreign/busy owner merely because our pure plan was rejected.
            if start_requested and not rejected:
                try:
                    receipt["original_cancel"] = self._request("arm/trajectory/stop", {})
                except Exception as cancel_error:
                    receipt["cancel_error"] = type(cancel_error).__name__
            return receipt
        finally:
            with self.lock:
                self.inflight = False
                self.last = copy.deepcopy(receipt)

    def heartbeat(self, body):
        with self.lock:
            self._match(body)
            target_session = self.target_session
        if body.get("held") is not True:
            return self.stop(body)
        if target_session is None:
            return self.status()
        state = self._request("arm/trajectory")
        self._observe(state)
        with self.lock:
            self._match(body)
        if state.get("busy"):
            original = self._request("arm/trajectory/lease", {"session": target_session, "held": True})
            return {**self.status(), "original_lease": original}
        return self.status()

    def stop(self, body):
        return self.cancel_all("Explicit direct-arm STOP", expected_owner=body)

    def cancel_all(self, reason="Operator STOP", *, expected_owner=None):
        """Authenticated global STOP integration; also usable after an ambiguous reply."""
        with self.lock:
            if expected_owner is not None:
                self._match(expected_owner)
            if not self.pending_stops:
                self.stop_uncertain = False
            self.pending_stops += 1
            self._invalidate(reason)
        try:
            original = self._request("arm/trajectory/stop", {})
        except Exception:
            with self.lock:
                self.stop_uncertain = True
                self.mode = "UNKNOWN_OUTCOME"
            raise
        finally:
            with self.lock:
                self.pending_stops -= 1
        return {**self.status(), "original_stop": original, "new_start_required": True}


class NanoArmHTTPClient:
    """Direct authenticated Pi -> Nano observer only; no NX intermediary or retries."""
    def __init__(self, base_url, token, timeout=8):
        self.base_url, self._token, self.timeout = base_url.rstrip("/"), token, timeout

    def _request(self, operation, body=None):
        if operation not in {"status", "start", "action", "lease", "stop"}:
            raise ValueError("Unknown direct-arm operation")
        request = Request(self.base_url + "/arm-retarget/" + operation,
                          data=json.dumps(body, allow_nan=False).encode() if body is not None else None,
                          headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:
            raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise ValueError("Nano receipt exceeds its bound")
            return json.loads(raw)

    def status(self):
        return self._request("status")

    def start(self, body):
        return self._request("start", body)

    def action(self, body):
        return self._request("action", body)

    def heartbeat(self, body):
        return self._request("lease", body)

    def stop(self, body):
        return self._request("stop", body)


class PiNanoRetargetSession:
    """Consumes cached measured leader snapshots; never opens the servo bus."""
    def __init__(self, geometry, client, owner_id="pi:so101-leader"):
        self.geometry, self.client, self.owner_id = geometry, client, owner_id
        self.epoch, self.source_epoch, self.source_clock, self.sequence = None, None, None, -1
        self.last_source_time, self.state, self.last = -1, "IDLE", None
        self.lock = threading.RLock()

    def _owner(self):
        if self.state != "ACTIVE" or self.epoch is None:
            raise ValueError("A fresh explicit retarget start is required")
        return {"control_epoch": self.epoch, "owner_id": self.owner_id}

    def start(self, measurement, *, observing):
        registration = self.geometry.status()
        if registration["state"] == "UNREGISTERED":
            self.state = "UNREGISTERED"
            return registration
        self.geometry.source_positions(measurement)
        remote = self.client.status()
        response = self.client.start({"owner_id": self.owner_id, "expected_control_epoch": remote["control_epoch"],
                                      "registration_id": registration["registration_id"], "registration_sha256": registration["registration_sha256"],
                                      "observing": observing})
        if response.get("state") != "ACTIVE":
            self.state = response.get("state", "UNKNOWN_OUTCOME")
            return response
        try:
            self.geometry.recenter(measurement, response["target_reference"]["servo_deg"])
        except Exception:
            self.client.stop({"owner_id": self.owner_id, "control_epoch": response["control_epoch"]})
            raise
        with self.lock:
            self.epoch, self.state, self.sequence = response["control_epoch"], "ACTIVE", -1
            self.source_epoch, self.source_clock = measurement.get("control_epoch"), measurement.get("clock_domain")
            self.last_source_time = measurement.get("monotonic_ns", -1)
        return {"state": self.state, "control_epoch": self.epoch, "recentered": True, "motion_requested": False}

    def recenter(self, measurement):
        self._owner()
        if measurement.get("control_epoch") != self.source_epoch or measurement.get("clock_domain") != self.source_clock:
            raise ValueError("Clutch belongs to the current measured source session")
        remote = self.client.status()
        if remote.get("control_epoch") != self.epoch or remote.get("target", {}).get("busy") or remote.get("target_reference") is None:
            raise ValueError("Recenter needs the same idle target reference")
        self.stop()
        # New remote epoch rejects queued actions/leases computed from the former anchor.
        return self.start(measurement, observing=True)

    def step(self, measurement):
        owner = self._owner()
        stamp = measurement.get("monotonic_ns")
        if type(stamp) is not int or stamp < self.last_source_time or measurement.get("control_epoch") != self.source_epoch or measurement.get("clock_domain") != self.source_clock:
            raise ValueError("Leader sample is old or belongs to another source session/clock")
        if stamp == self.last_source_time:
            return {"state": "SOURCE_UNCHANGED", "motion_requested": False}
        self.last_source_time = stamp
        remote = self.client.status()
        if remote.get("state") != "ACTIVE" or remote.get("control_epoch") != self.epoch:
            self.state, self.epoch = "STOPPED", None
            return {"state": "STOPPED", "new_start_required": True}
        if remote.get("target", {}).get("busy"):
            return {"state": "BUSY", "latest_sample_dropped_without_queue": True}
        if remote.get("target_reference") is None:
            return {"state": "TARGET_REFERENCE_UNKNOWN", "motion_requested": False}
        plan = self.geometry.plan(measurement, remote["target_reference"]["servo_deg"])
        if plan.get("requested_goal") is None:
            return plan
        if plan["requested_goal"] == remote["target_reference"]["servo_deg"]:
            return {**plan, "state": "UNCHANGED", "motion_requested": False}
        self.sequence += 1
        receipt = self.client.action({**owner, "sequence": self.sequence, "goal": plan["requested_goal"], "source_measurement": plan["source_measurement"]})
        self.last = {"geometry": plan, "target_receipt": receipt}
        if receipt.get("state") not in {"API_ACCEPTED", "BUSY"}:
            self.state, self.epoch = "STOPPED", None
        return self.last

    def heartbeat(self, *, held=True):
        return self.client.heartbeat({**self._owner(), "held": held})

    def stop(self):
        owner = self._owner()
        self.state, self.epoch = "STOPPED", None
        return self.client.stop(owner)

    def run(self, read_cached_measurement, stop_event):
        """Integrator runs this on Pi, outside station bus locks; no lease is invented."""
        while not stop_event.is_set() and self.state == "ACTIVE":
            try:
                self.step(read_cached_measurement())
            except Exception as error:
                self.last = {"state": "INTERRUPTED", "error_type": type(error).__name__}
                try:
                    self.stop()
                except Exception:
                    self.state, self.epoch = "UNKNOWN_OUTCOME", None
            stop_event.wait(1 / 30)
