"""ENGINEERING_GLUE: model geometry + fake original HTTP ABI, never devices."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
from explorer_shared.arm_only_retarget import ArmOnlyGeometry, SOURCE_NAMES, SOURCE_UNITS, digest, six_servo_goal
from explorer_shared.pi_nano_route import NanoArmGateway, NanoArmHTTPClient, PiNanoRetargetSession

SOURCE = ROOT / "04-cross-embodiment-transfer/configs/so101/so101_leader_new_calib.urdf"
TARGET = ROOT / "01-autonomous-learning/src/legacy/config/explorer.urdf"
HOME = [90, 60, 145, 65, 90, 100]


def geometry(tmp_path):
    unregistered = ArmOnlyGeometry(SOURCE, TARGET)
    # Hypothetical registration data for software only. No real hardware is registered by this fixture.
    artifact = tmp_path / "software-fixture-evidence.json"
    artifact.write_text('{"fixture_only":true,"physical_validation":false}')
    calib = tmp_path / "original-calibration-fixture.json"
    calib.write_text('{"fixture_only":true,"original_retained":true}')
    p = {"registration_id": "software-fixture-not-hardware", "registration_state": "measured",
         "source_calibration": {"path": str(calib), "sha256": digest(calib)},
         "model_sha256": {"source": unregistered.source.source_sha256, "target": unregistered.target.source_sha256},
         "source_joint_points": {name: [[-100, -1.], [100, 1.]] for name in unregistered.source.names},
         "target_joint_points": {name: [[-1, 90 - (-1 if index == 0 else 1) * 180 / np.pi], [1, 90 + (-1 if index == 0 else 1) * 180 / np.pi]] for index, name in enumerate(unregistered.target.names)},
         "target_base_from_source_rotation": np.eye(3).tolist(), "orientation_mode": "position_only",
         "gripper": {"source_trigger_closed_open": [0, 100], "target_aperture_closed_open_m": [0., .05],
                     "target_aperture_m_to_servo_deg": [[0., 170.], [.05, 30.]]},
         "evidence": {name: {"path": str(artifact), "sha256": digest(artifact), "calibration_id": "software-fixture-" + name}
                      for name in ("source_joint_zero_and_direction", "target_servo_zero_and_direction", "frame_axes", "gripper_aperture")}}
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(p))
    return ArmOnlyGeometry(SOURCE, TARGET, path), path, p


def sample(g, stamp=1, positions=None):
    return {"role": "leader", "joint_names": list(SOURCE_NAMES), "units": SOURCE_UNITS,
            "positions": positions or [0., -60., 90., -30., 0., 50.], "measured": True,
            "calibration_sha256": g.profile["source_calibration"]["sha256"], "monotonic_ns": stamp,
            "clock_domain": "pi:software-fixture-boot", "control_epoch": "station-fixture-epoch"}


class OriginalHTTPFixture:
    def __init__(self):
        self.calls, self.pose, self.revision = [], list(HOME), 0
        self.state, self.pending = {"phase": "idle", "busy": False}, None
        self.on_plan, self.on_start, self.lose_start_reply = None, None, False

    def request(self, operation, body=None):
        self.calls.append((operation, copy.deepcopy(body)))
        if operation == "arm/manual":
            return 200, {"servo_deg": list(self.pose), "blocked_by": None, "estimated_only": True}
        if operation == "arm/trajectory":
            return 200, copy.deepcopy(self.state)
        if operation == "arm/trajectory/plan":
            assert set(body) == {"goal_deg"}
            six_servo_goal(body["goal_deg"])
            self.pending = {"goal": list(body["goal_deg"]), "revision": self.revision, "id": "a" * 32}
            self.state = {"phase": "planned", "busy": False, "plan_id": self.pending["id"], "goal_deg": list(body["goal_deg"])}
            result = copy.deepcopy(self.state)
            if self.on_plan:
                self.on_plan()
            return 200, result
        if operation == "arm/trajectory/start":
            assert set(body) == {"plan_id", "observing"} and body["observing"] is True
            if self.pending["revision"] != self.revision:
                return 409, {"detail": "STOP changed after plan"}
            self.state = {**self.state, "phase": "moving", "busy": True, "session": "b" * 32, "executed": False, "execution_mode": "operator_held"}
            if self.on_start:
                self.on_start()
            if self.lose_start_reply:
                raise TimeoutError("software fixture lost reply")
            return 200, copy.deepcopy(self.state)
        if operation == "arm/trajectory/lease":
            assert body == {"session": self.state["session"], "held": True}
            return 200, copy.deepcopy(self.state)
        if operation == "arm/trajectory/stop":
            self.revision += 1
            self.state.update(phase="stopped", busy=False)
            return 200, {"cancel_requested": True, "physical_stop_confirmed": False}
        raise AssertionError("Chassis/unknown request must not occur: " + operation)

    def complete(self):
        self.pose = list(self.state["goal_deg"])
        self.state.update(phase="command_completed", busy=False, executed=True, reached=False)


class DirectClientFixture:
    def __init__(self, gateway):
        self.gateway = gateway
    def status(self):
        return self.gateway.status(refresh=True)
    def start(self, body):
        return self.gateway.start(body)
    def action(self, body):
        return self.gateway.action(body)
    def heartbeat(self, body):
        return self.gateway.heartbeat(body)
    def stop(self, body):
        return self.gateway.stop(body)


def route(tmp_path):
    g, path, profile = geometry(tmp_path)
    original = OriginalHTTPFixture()
    gateway = NanoArmGateway(original.request, g.status())
    session = PiNanoRetargetSession(g, DirectClientFixture(gateway))
    return g, original, gateway, session


def test_missing_actual_registration_is_explicit_and_makes_no_target_calls():
    g = ArmOnlyGeometry(SOURCE, TARGET)
    calls = []
    gateway = NanoArmGateway(lambda *args: calls.append(args), g.status())
    session = PiNanoRetargetSession(g, DirectClientFixture(gateway))
    status = session.start({}, observing=True)
    assert status["state"] == "UNREGISTERED" and "source_joint_points" in status["missing"]
    assert not status["physical_acceptance_confirmed"] and calls == []


def test_full_source_fk_clutch_m3_ik_and_separate_gripper_to_original_abi(tmp_path):
    g, original, gateway, session = route(tmp_path)
    first = sample(g)
    started = session.start(first, observing=True)
    assert started["recentered"] and not started["motion_requested"]
    assert not any(operation.endswith(("plan", "start")) for operation, _ in original.calls)
    changed = sample(g, 2, [3., -60., 90., -30., 0., 75.])
    result = session.step(changed)
    proposal, receipt = result["geometry"], result["target_receipt"]
    assert proposal["state"] == "GEOMETRIC_PROPOSAL" and proposal["ik"]["reached"]
    assert proposal["requested_goal"] != changed["positions"]
    assert proposal["gripper"]["target_servo_deg"] == 65
    assert proposal["requested_goal"][4] == HOME[4]  # Position-only mode retains target wrist roll.
    assert len(proposal["ik"]["positions_rad"]) == 5
    assert receipt["state"] == "API_ACCEPTED" and receipt["accepted"] == proposal["requested_goal"]
    assert receipt["dispatched"] is None and not receipt["dispatch_confirmed"]
    assert original.calls[-2][0] == "arm/trajectory/plan" and original.calls[-1][0] == "arm/trajectory/start"
    session.heartbeat(held=True)
    assert original.calls[-1][0] == "arm/trajectory/lease"
    assert session.step(sample(g, 3))["state"] == "BUSY"
    original.complete()
    recentered = session.recenter(sample(g, 4, changed["positions"]))
    assert not recentered["motion_requested"]
    assert session.step(sample(g, 5, changed["positions"]))["state"] == "UNCHANGED"
    stopped = session.stop()
    assert stopped["state"] == "STOPPED" and not stopped["physical_stop_confirmed"]
    assert original.calls[-1] == ("arm/trajectory/stop", {})
    assert all(operation.startswith("arm/") for operation, _ in original.calls)


def test_stop_during_plan_prevents_late_start_and_old_epoch_cannot_resume(tmp_path):
    g, original, gateway, session = route(tmp_path)
    session.start(sample(g), observing=True)
    owner = {"owner_id": session.owner_id, "control_epoch": session.epoch}
    original.on_plan = lambda: gateway.stop(owner)
    result = session.step(sample(g, 2, [3., -60., 90., -30., 0., 75.]))
    assert result["target_receipt"]["state"] == "CANCELLED_BEFORE_START"
    assert not any(operation == "arm/trajectory/start" for operation, _ in original.calls)
    assert gateway.status()["state"] == "STOPPED"
    with pytest.raises(ValueError, match="Ended"):
        gateway.action({**owner, "sequence": 2, "goal": list(HOME)})


def test_lost_start_reply_cancels_without_retry_or_dispatch_claim(tmp_path):
    g, original, gateway, session = route(tmp_path)
    session.start(sample(g), observing=True)
    original.lose_start_reply = True
    result = session.step(sample(g, 2, [3., -60., 90., -30., 0., 75.]))["target_receipt"]
    assert result["state"] == "UNKNOWN_OUTCOME" and result["no_automatic_retry"]
    assert result["accepted"] is None and result["dispatched"] is None
    assert original.calls[-1][0] == "arm/trajectory/stop"
    assert sum(operation == "arm/trajectory/start" for operation, _ in original.calls) == 1


@pytest.mark.parametrize("bad", [[90] * 5, [90.] * 6, [True] * 6, [90] * 7])
def test_strict_six_integer_servo_abi(bad):
    with pytest.raises(ValueError, match="six integers"):
        six_servo_goal(bad)


def test_no_assumed_source_calibration_clock_or_zero_mapping(tmp_path):
    g, path, p = geometry(tmp_path)
    leader = sample(g)
    leader["calibration_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="calibration SHA"):
        g.source_positions(leader)
    leader = sample(g)
    leader["clock_domain"] = None
    with pytest.raises(ValueError, match="clock"):
        g.source_positions(leader)
    p["evidence"]["frame_axes"]["sha256"] = "0" * 64
    path.write_text(json.dumps(p))
    assert "evidence.frame_axes" in ArmOnlyGeometry(SOURCE, TARGET, path).status()["missing"]


def test_external_original_stop_and_source_epoch_change_cannot_silently_resume(tmp_path):
    g, original, gateway, session = route(tmp_path)
    session.start(sample(g), observing=True)
    session.step(sample(g, 2, [3., -60., 90., -30., 0., 75.]))
    original.request("arm/trajectory/stop", {})
    assert session.step(sample(g, 3))["state"] == "STOPPED"
    with pytest.raises(ValueError, match="fresh explicit"):
        session.step(sample(g, 4))
    session.start(sample(g, 5), observing=True)
    changed = sample(g, 6)
    changed["control_epoch"] = "a-new-station-session"
    with pytest.raises(ValueError, match="another source session"):
        session.step(changed)


def test_recenter_rotates_epoch_and_duplicate_cached_measurement_does_not_command(tmp_path):
    g, original, gateway, session = route(tmp_path)
    session.start(sample(g), observing=True)
    old = {"owner_id": session.owner_id, "control_epoch": session.epoch}
    before = len(original.calls)
    assert session.step(sample(g))["state"] == "SOURCE_UNCHANGED"
    assert len(original.calls) == before
    result = session.recenter(sample(g, 2))
    assert result["control_epoch"] != old["control_epoch"] and not result["motion_requested"]
    with pytest.raises(ValueError, match="Ended"):
        gateway.action({**old, "sequence": 1, "goal": list(HOME)})


def test_poll_during_own_planning_does_not_mislabel_it_as_foreign_owner(tmp_path):
    g, original, gateway, session = route(tmp_path)
    session.start(sample(g), observing=True)
    session.step(sample(g, 2, [3., -60., 90., -30., 0., 75.]))
    original.complete()
    original.on_plan = lambda: gateway.status(refresh=True)
    result = session.step(sample(g, 3, [4., -60., 90., -30., 0., 80.]))
    assert result["target_receipt"]["state"] == "API_ACCEPTED" and gateway.status()["state"] == "ACTIVE"


def test_direct_http_serialization_reaches_only_nano_arm_gateway(tmp_path, monkeypatch):
    import explorer_shared.pi_nano_route as module
    g, original, gateway, unused = route(tmp_path)
    urls = []

    class Response:
        def __init__(self, value): self.raw = json.dumps(value).encode()
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read(self, size): return self.raw[:size]

    def local_fixture(request, timeout):
        assert request.get_header("Authorization") == "Bearer private-software-fixture-token"
        urls.append(request.full_url)
        assert request.full_url.startswith("http://nano-software-fixture:8092/arm-retarget/")
        operation = request.full_url.rsplit("/", 1)[-1]
        body = json.loads(request.data) if request.data is not None else None
        if operation == "status": return Response(gateway.status(refresh=True))
        method = {"start": gateway.start, "action": gateway.action, "lease": gateway.heartbeat, "stop": gateway.stop}[operation]
        return Response(method(body))

    monkeypatch.setattr(module, "urlopen", local_fixture)
    client = NanoArmHTTPClient("http://nano-software-fixture:8092", "private-software-fixture-token")
    session = PiNanoRetargetSession(g, client)
    session.start(sample(g), observing=True)
    result = session.step(sample(g, 2, [3., -60., 90., -30., 0., 75.]))
    assert result["target_receipt"]["state"] == "API_ACCEPTED"
    session.heartbeat(held=True)
    session.stop()
    assert {url.rsplit("/", 1)[-1] for url in urls} == {"status", "start", "action", "lease", "stop"}


def test_explicit_plan_rejection_does_not_cancel_a_foreign_original_owner(tmp_path):
    g, original, unused, unused_session = route(tmp_path)
    def request(operation, body=None):
        if operation == "arm/trajectory/plan":
            original.calls.append((operation, copy.deepcopy(body)))
            return 409, {"detail": "A different original owner became busy"}
        return original.request(operation, body)
    gateway = NanoArmGateway(request, g.status())
    session = PiNanoRetargetSession(g, DirectClientFixture(gateway))
    session.start(sample(g), observing=True)
    result = session.step(sample(g, 2, [3., -60., 90., -30., 0., 75.]))["target_receipt"]
    assert result["state"] == "REJECTED" and not result["motion_start_requested"]
    assert not any(operation.endswith(("stop", "start")) for operation, _ in original.calls)
