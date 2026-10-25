"""Behavioral tests run without camera, Hailo, ROS or serial dependencies."""
import ast
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socket
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from explorer_shared import node_agent as node
from explorer_shared.hailo_observer import Letterbox, decode_hailo, IoUTracker


JPEG = b"\xff\xd8test-fixture\xff\xd9"
TOKEN = "test-only-node-token-00000000"


class MemoryOutbox:
    def __init__(self):
        self.items = {}

    def append(self, event_id, payload):
        added = event_id not in self.items
        self.items.setdefault(event_id, payload)
        return added

    def pending(self, limit=100):
        return [{"event_id": key, "payload": value} for key, value in list(self.items.items())[:limit]]

    def acknowledge(self, event_id):
        self.items.pop(event_id, None)


class FakeCamera(node.CameraSource):
    def __init__(self):
        super().__init__("pi-overview", "Test camera")

    def start(self):
        pass


class GeometryTests(unittest.TestCase):
    def test_letterbox_restores_original_coordinates_and_drops_padding(self):
        transform = Letterbox(640, 480, 640, 640)
        classes = [[] for _ in range(80)]
        classes[39] = [[0.125, 0.25, 0.875, 0.75, 0.9], [0, 0, 0.1, 1, 0.9],
                       [0.125, 0.25, 0.875, 0.75, 0.1]]
        result = decode_hailo(classes, transform)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "bottle")
        self.assertEqual(result[0]["box"], [0.25, 0, 0.75, 1])
        classes[39][0][4] = float("nan")
        with self.assertRaises(ValueError):
            decode_hailo(classes, transform)

    def test_tracker_one_to_one_class_gating_and_expiry(self):
        tracker = IoUTracker(max_age=1)
        a = {"class_id": 1, "box": [0, 0, 0.5, 0.5]}
        first = tracker.update([a], 10)[0]["track_id"]
        result = tracker.update([a, a], 10.1)
        self.assertEqual(result[0]["track_id"], first)
        self.assertNotEqual(result[0]["track_id"], result[1]["track_id"])
        self.assertNotEqual(tracker.update([a], 12)[0]["track_id"], first)


class SourceTests(unittest.TestCase):
    def test_stale_frame_is_unavailable_and_timestamps_honest(self):
        camera = FakeCamera()
        camera.publish(JPEG, 640, 480, monotonic_ns=time.monotonic_ns() - 3_000_000_000)
        self.assertIsNone(camera.latest())
        camera.publish(JPEG, 640, 480)
        self.assertFalse(camera.latest().metadata()["exposure_timestamp_available"])
        self.assertEqual(camera.latest().timestamp_basis, "host_receive_estimate")

    def test_serial_discovery_only_reads_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usb-controller-a"
            path.symlink_to("/dev/ttyACM9")
            devices = node.discover_serial(directory)
            self.assertEqual(devices[0]["role"], "unassigned")
            self.assertFalse(devices[0]["servo_bus_opened"])
            self.assertFalse(devices[0]["positions_available"])

    def test_legacy_stale_artifact_is_not_served(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / "frame-raw.jpg"
            frame.write_bytes(JPEG)
            os.utime(frame, (time.time() - 10, time.time() - 10))
            source = node.LegacyFileCamera(frame, Path(directory) / "status.json", stale_s=0.1)
            source.start()
            time.sleep(0.12)
            source.close()
            self.assertIsNone(source.latest())
            self.assertEqual(source.error, "legacy_frame_stale")


class RecordingTests(unittest.TestCase):
    def test_camera_only_recording_hashes_and_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            camera, events = FakeCamera(), []
            camera.publish(JPEG, 640, 480)
            manager = node.RecordingManager(directory, {camera.camera_id: camera},
                                            lambda payload, **kw: events.append(payload), sample_hz=10)
            manager.start(camera.camera_id, 0.12, "record-1", "personal-lab")
            manager.thread.join(timeout=2)
            result = manager.get("record-1")
            self.assertEqual(result["state"], "completed")
            self.assertEqual(result["motion_commands"], 0)
            self.assertFalse(result["joint_measurements_available"])
            self.assertEqual(result["frames"][0]["sha256"], hashlib.sha256(JPEG).hexdigest())
            self.assertEqual(events[0]["archive_sha256"], node.file_sha256(manager.download_path("record-1")))
            with tarfile.open(manager.download_path("record-1")) as bundle:
                self.assertIn("manifest.json", bundle.getnames())
                self.assertEqual(bundle.extractfile("000000.jpg").read(), JPEG)
            with self.assertRaises(ValueError):
                manager.get("../outside")
            manager.close()

    def test_restart_seals_interrupted_recording_and_preserves_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "record-2"
            target.mkdir()
            (target / "000000.jpg").write_bytes(JPEG)
            node.write_json(target / "manifest.json", {"state": "recording", "recording_id": "record-2",
                            "camera_id": "pi-overview", "frames": [{"file": "000000.jpg"}]})
            events = []
            manager = node.RecordingManager(directory, {}, lambda payload, **kw: events.append(payload))
            self.assertEqual(manager.get("record-2")["state"], "interrupted")
            self.assertEqual(events[0]["state"], "interrupted")
            self.assertEqual((target / "000000.jpg").read_bytes(), JPEG)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.camera = FakeCamera()
        self.camera.publish(JPEG, 640, 480)
        delivery = node.EventDelivery("pi", self.directory.name, outbox=MemoryOutbox())
        self.agent = node.NodeAgent("pi", "pi", self.directory.name, TOKEN,
                                    sources={self.camera.camera_id: self.camera}, delivery=delivery)
        self.server = node.AgentHTTPServer(("127.0.0.1", 0), node.make_handler(self.agent))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.agent.close()
        self.directory.cleanup()

    def request(self, path, auth=True, data=None):
        headers = {"Authorization": "Bearer " + TOKEN} if auth else {}
        try:
            return urlopen(Request(self.base + path, data=data, headers=headers), timeout=2)
        except HTTPError as exc:
            exc.close()
            raise

    def test_authentication_and_no_motion_endpoints(self):
        with self.request("/health", auth=False) as response:
            self.assertFalse(json.load(response)["physical_motion"])
        with self.assertRaises(HTTPError) as error:
            self.request("/inventory", auth=False)
        self.assertEqual(error.exception.code, 401)
        with self.request("/inventory") as response:
            inventory = json.load(response)
            self.assertFalse(inventory["capabilities"]["servo_bus_access"])
        with self.assertRaises(HTTPError) as error:
            self.request("/arm", data=b"{}")
        self.assertEqual(error.exception.code, 404)

    def test_snapshot_freshness_and_bounded_stream_clients(self):
        with self.request("/cameras/pi-overview/snapshot") as response:
            self.assertEqual(response.read(), JPEG)
            self.assertEqual(response.headers["X-Timestamp-Basis"], "host_receive_estimate")
        for _ in range(3):
            self.assertTrue(self.agent.stream_slots.acquire(blocking=False))
        try:
            with self.assertRaises(HTTPError) as error:
                self.request("/cameras/pi-overview/stream")
            self.assertEqual(error.exception.code, 429)
        finally:
            for _ in range(3):
                self.agent.stream_slots.release()
        self.camera.publish(JPEG, 640, 480, monotonic_ns=time.monotonic_ns() - 3_000_000_000)
        with self.assertRaises(HTTPError) as error:
            self.request("/cameras/pi-overview/snapshot")
        self.assertEqual(error.exception.code, 503)


class DeliveryTests(unittest.TestCase):
    def test_failed_delivery_is_retained_and_retry_reuses_event_id(self):
        received, accepted = [], [False]

        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(201 if accepted[0] else 503)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = node.AgentHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            outbox = MemoryOutbox()
            delivery = node.EventDelivery("pi", ".", "http://127.0.0.1:" + str(server.server_port), TOKEN, outbox)
            event_id = delivery.emit({"kind": "test", "data": "кириллица"})
            with self.assertRaises(HTTPError):
                delivery.deliver_once()
            self.assertEqual(outbox.pending()[0]["event_id"], event_id)
            accepted[0] = True
            self.assertEqual(delivery.deliver_once(), 1)
            self.assertEqual(outbox.pending(), [])
            self.assertEqual(received[0]["event_id"], received[1]["event_id"])
            self.assertEqual(received[0]["sha256"], hashlib.sha256(node.canonical_bytes(received[0]["payload"])).hexdigest())
        finally:
            server.shutdown()
            server.server_close()

    def test_no_forbidden_control_flow_or_device_imports(self):
        source_path = Path(node.__file__)
        for path in (source_path, source_path.with_name("hailo_observer.py")):
            tree = ast.parse(path.read_text())
            self.assertFalse(any(isinstance(item, ast.Continue) for item in ast.walk(tree)))
        imports = [item for item in ast.walk(ast.parse(source_path.read_text())) if isinstance(item, ast.Import)]
        self.assertFalse(any(alias.name in ("serial", "rclpy") for item in imports for alias in item.names))


if __name__ == "__main__":
    unittest.main()
