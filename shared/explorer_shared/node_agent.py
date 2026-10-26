"""ENGINEERING_GLUE: Explorer cameras, durable observations and explicitly requested station control.

Pi owns one camera capture. Nano observes the existing controller's files;
it neither imports ROS nor opens its cameras or serial ports. Pi optionally owns
the original calibrated SO-101 buses; no calibration or motor settings are written.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import tarfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
import uuid


MAX_JPEG_BYTES = 10 * 1024 * 1024
MAX_BODY_BYTES = 16384
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


def wall_utc(timestamp=None):
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp,
                                  timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as output:
        output.write(canonical_bytes(value))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def file_sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@dataclass(frozen=True)
class Frame:
    frame_id: str
    jpeg: bytes
    wall_utc: str
    monotonic_ns: int
    width: int
    height: int
    timestamp_basis: str = "host_receive_estimate"
    source_wall_utc: str | None = None
    bgr: object = field(default=None, repr=False, compare=False)

    def metadata(self):
        return {"frame_id": self.frame_id, "wall_utc": self.wall_utc,
                "monotonic_ns": self.monotonic_ns, "width": self.width,
                "height": self.height, "timestamp_basis": self.timestamp_basis,
                "source_wall_utc": self.source_wall_utc,
                "exposure_timestamp_available": False}


class CameraSource:
    def __init__(self, camera_id, label, stale_s=2.0):
        self.camera_id, self.label, self.stale_s = camera_id, label, stale_s
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.frame = None
        self.error = None
        self.thread = None
        self.counter = 0
        self.session_id = uuid.uuid4().hex[:12]
        self._history = deque()
        self._history_bytes = 0
        self._history_limit_frames = 64
        self._history_limit_bytes = 32 * 1024**2

    @staticmethod
    def _frame_bytes(frame):
        return len(frame.jpeg) + int(getattr(frame.bgr, 'nbytes', 0))

    def frame_by_id(self, frame_id):
        """Exact bounded retained bytes; age still needs checking by the caller."""
        with self.lock:
            return next((frame for frame in reversed(self._history) if frame.frame_id == frame_id), None)

    def before(self, monotonic_ns, max_age_ns=None):
        """Same-kernel-clock frame preceding a recorded dispatch, no new capture.

        Caller must verify original domain/boot identity before supplying ticks.
        This historical lookup is not current physical-observation permission.
        """
        if type(monotonic_ns) is not int or not 0 <= monotonic_ns <= time.monotonic_ns():
            return None
        max_age_ns = int(self.stale_s * 1e9) if max_age_ns is None else max_age_ns
        if type(max_age_ns) is not int or max_age_ns < 0:
            raise ValueError('Explicit nonnegative historical camera age required')
        with self.lock:
            return next((frame for frame in reversed(self._history)
                         if 0 <= monotonic_ns - frame.monotonic_ns <= max_age_ns), None)

    def latest(self):
        with self.lock:
            frame = self.frame
        return frame if frame and 0 <= (time.monotonic_ns() - frame.monotonic_ns) / 1e9 <= self.stale_s else None

    def publish(self, jpeg, width, height, *, bgr=None, source_wall_utc=None,
                monotonic_ns=None, timestamp_basis="host_receive_estimate"):
        if len(jpeg) > MAX_JPEG_BYTES or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("Invalid or oversized JPEG")
        self.counter += 1
        frame = Frame(f"{self.camera_id}:{self.session_id}:{self.counter}", jpeg, wall_utc(),
                      time.monotonic_ns() if monotonic_ns is None else monotonic_ns,
                      width, height, timestamp_basis, source_wall_utc, bgr)
        with self.lock:
            self.frame, self.error = frame, None
            self._history.append(frame)
            self._history_bytes += self._frame_bytes(frame)
            while (len(self._history) > self._history_limit_frames or
                   self._history_bytes > self._history_limit_bytes):
                self._history_bytes -= self._frame_bytes(self._history.popleft())

    def metadata(self):
        frame = self.latest()
        return {"id": self.camera_id, "label": self.label, "available": frame is not None,
                "frame": frame.metadata() if frame else None,
                "error": self.error if self.error else (None if frame else "no_fresh_frame"),
                "stale_after_s": self.stale_s, "physical_motion": False}

    def start(self):
        self.thread = threading.Thread(target=self._capture, name=self.camera_id, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)


class OpenCVCamera(CameraSource):
    def __init__(self, device, lock_path, width=640, height=480, fps=15):
        super().__init__("pi-overview", "Общий вид стола · Raspberry Pi")
        self.device, self.lock_path = str(device), Path(lock_path)
        self.width, self.height, self.fps = width, height, min(30, max(1, fps))

    def _capture(self):
        cap, owner = None, None
        try:
            import cv2
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            owner = self.lock_path.open("a")
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            while not self.stop_event.is_set():
                try:
                    if cap is None:
                        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
                        if not cap.isOpened():
                            raise RuntimeError("Camera could not be opened")
                        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                        cap.set(cv2.CAP_PROP_FPS, self.fps)
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    ok, image = cap.read()
                    received_ns = time.monotonic_ns()
                    if not ok or image is None:
                        raise RuntimeError("Camera returned no frame")
                    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if not ok:
                        raise RuntimeError("JPEG encoding failed")
                    self.publish(jpeg.tobytes(), image.shape[1], image.shape[0],
                                 bgr=image, monotonic_ns=received_ns)
                except Exception as exc:
                    self.error = type(exc).__name__
                    if cap is not None:
                        cap.release()
                        cap = None
                    self.stop_event.wait(2)
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            if cap is not None:
                cap.release()
            if owner:
                owner.close()


class LegacyFileCamera(CameraSource):
    """Reads an existing controller artifact; never owns a V4L2 device."""
    def __init__(self, frame_path, status_path, stale_s=2.0):
        super().__init__("nano-rgbd", "Камера действующего Explorer · Jetson Nano", stale_s)
        self.frame_path, self.status_path = Path(frame_path), Path(status_path)
        self.status_available = False
        self.status_age_s = None

    def _capture(self):
        previous = None
        while not self.stop_event.is_set():
            try:
                before = self.frame_path.stat()
                stamp = (before.st_mtime_ns, before.st_size)
                if before.st_size > MAX_JPEG_BYTES:
                    raise ValueError("Oversized legacy frame")
                age_s = time.time() - before.st_mtime
                if age_s < -5:
                    raise ValueError("Legacy frame has a future timestamp")
                age_s = max(0.0, age_s)
                if stamp != previous and age_s <= self.stale_s:
                    jpeg = self.frame_path.read_bytes()
                    after = self.frame_path.stat()
                    if (after.st_mtime_ns, after.st_size) == stamp:
                        from io import BytesIO
                        from PIL import Image
                        with Image.open(BytesIO(jpeg)) as image:
                            width, height = image.size
                        self.publish(jpeg, width, height,
                                     monotonic_ns=time.monotonic_ns() - int(age_s * 1e9),
                                     source_wall_utc=wall_utc(before.st_mtime),
                                     timestamp_basis="legacy_file_mtime_estimate")
                        previous = stamp
                if age_s > self.stale_s:
                    self.error = "legacy_frame_stale"
                self.status_available = False
                if self.status_path.exists() and self.status_path.stat().st_size < 1024 * 1024:
                    status = json.loads(self.status_path.read_text())
                    self.status_age_s = max(0.0, time.time() - self.status_path.stat().st_mtime)
                    self.status_available = isinstance(status, dict) and self.status_age_s <= 5
            except Exception as exc:
                self.error = type(exc).__name__
            self.stop_event.wait(0.05)

    def metadata(self):
        return {**super().metadata(), "source": "existing_controller_file",
                "legacy_status_available": self.status_available,
                "legacy_status_age_s": self.status_age_s,
                "camera_identity_verified": False}


def discover_serial(root=Path("/dev/serial/by-id")):
    """Only directory/symlink metadata. Never imports pyserial or opens a port."""
    devices = []
    for path in sorted(Path(root).glob("*")):
        devices.append({"id": path.name, "path": str(path), "device": str(path.resolve()),
                        "role": "unassigned", "servo_bus_opened": False,
                        "servo_model_verified": False, "positions_available": False})
    return devices


def discover_calibrations(roots):
    result = []
    for root in roots:
        for path in sorted(Path(root).glob("**/*.json")):
            try:
                if path.is_file() and path.stat().st_size <= 1024 * 1024:
                    data = json.loads(path.read_text())
                    if isinstance(data, dict):
                        result.append({"id": path.stem, "path": str(path), "sha256": file_sha256(path),
                                       "format": "unverified_json", "motor_names": sorted(data)[:32],
                                       "verified_on_connected_hardware": False})
            except (OSError, ValueError):
                pass
    return result[:100]


class EventDelivery:
    def __init__(self, node_id, state_dir, central_url=None, ingest_token=None, outbox=None):
        if outbox is None:
            from .outbox import EventOutbox
            outbox = EventOutbox(Path(state_dir) / "outbox.sqlite3")
        self.outbox, self.node_id = outbox, node_id
        self.central_url, self.ingest_token = central_url, ingest_token
        self.clock_domain = f"{node_id}:{Path('/proc/sys/kernel/random/boot_id').read_text().strip()}" if Path('/proc/sys/kernel/random/boot_id').exists() else f"{node_id}:{uuid.uuid4()}"
        self.stop_event = threading.Event()
        self.thread = None
        self.error = None

    def emit(self, payload, *, run_id=None, project_id=None):
        event_id = str(uuid.uuid4())
        event = {"event_id": event_id, "node_id": self.node_id, "run_id": run_id,
                 "project_id": project_id, "schema_version": 1, "wall_utc": wall_utc(),
                 "monotonic_ns": time.monotonic_ns(), "clock_domain": self.clock_domain,
                 "payload": payload, "sha256": hashlib.sha256(canonical_bytes(payload)).hexdigest()}
        self.outbox.append(event_id, event)
        return event_id

    def start(self):
        if self.central_url and self.ingest_token:
            self.thread = threading.Thread(target=self._work, name="event-outbox", daemon=True)
            self.thread.start()

    def deliver_once(self):
        if not self.central_url or not self.ingest_token:
            return 0
        delivered = 0
        for item in self.outbox.pending(limit=25):
            if self.stop_event.is_set():
                break
            request = Request(self.central_url.rstrip("/") + "/api/events", data=canonical_bytes(item["payload"]),
                              headers={"Authorization": "Bearer " + self.ingest_token,
                                       "Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(request, timeout=5) as response:
                    if 200 <= response.status < 300:
                        self.outbox.acknowledge(item["event_id"])
                        delivered += 1
                    else:
                        raise RuntimeError("NX rejected an event")
            except HTTPError as exc:
                exc.close()
                raise
        return delivered

    def _work(self):
        delay = 1
        while not self.stop_event.is_set():
            try:
                count = self.deliver_once()
                self.error, delay = None, 1
                self.stop_event.wait(0.1 if count else 2)
            except (OSError, HTTPError, URLError, RuntimeError):
                self.error = "delivery_pending"
                self.stop_event.wait(delay)
                delay = min(60, delay * 2)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=6)


class RecordingManager:
    def __init__(self, root, sources, emit, max_duration_s=300, sample_hz=5,
                 max_bytes=1024 * 1024 * 1024):
        self.root, self.sources, self.emit = Path(root), sources, emit
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_duration_s, self.sample_hz = max_duration_s, min(10, max(1, sample_hz))
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.active = None
        self.stop_event = threading.Event()
        self.thread = None
        self._recover()

    def _recover(self):
        for path in self.root.glob("*/manifest.json"):
            try:
                manifest = json.loads(path.read_text())
                if manifest.get("state") == "recording":
                    manifest.update(state="interrupted", error="agent_restarted", completed_wall_utc=wall_utc())
                    write_json(path, manifest)
                    self._archive_and_emit(path.parent, manifest)
            except (OSError, ValueError, KeyError):
                pass

    def _archive_and_emit(self, directory, manifest):
        archive = directory / "recording.tar"
        with tarfile.open(archive.with_suffix(".tmp"), "w") as bundle:
            bundle.add(directory / "manifest.json", arcname="manifest.json")
            for item in manifest["frames"]:
                bundle.add(directory / item["file"], arcname=item["file"])
        archive.with_suffix(".tmp").replace(archive)
        self.emit({"kind": "recording.finished", "recording_id": manifest["recording_id"],
                   "camera_id": manifest["camera_id"], "state": manifest["state"],
                   "frame_count": len(manifest["frames"]), "manifest_sha256": file_sha256(directory / "manifest.json"),
                   "archive_sha256": file_sha256(archive), "archive_bytes": archive.stat().st_size,
                   "artifact_location": "node_local", "camera_only": True},
                  run_id=manifest["recording_id"], project_id=manifest.get("project_id"))

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def start(self, camera_id, duration_s, recording_id, project_id=None):
        if not isinstance(recording_id, str) or not ID_PATTERN.fullmatch(recording_id):
            raise ValueError("Invalid recording_id")
        if isinstance(duration_s, bool) or not isinstance(duration_s, (int, float)) or not 0 < duration_s <= self.max_duration_s:
            raise ValueError("duration_s is outside the allowed range")
        if camera_id not in self.sources or self.sources[camera_id].latest() is None:
            raise LookupError("Camera has no fresh frame")
        with self.lock:
            directory = self.root / recording_id
            if directory.exists():
                return self.get(recording_id)
            if self.active:
                raise RuntimeError("A camera recording is already active")
            if shutil.disk_usage(self.root).free < 256 * 1024 * 1024:
                raise RuntimeError("Insufficient local storage")
            directory.mkdir(mode=0o700)
            manifest = {"schema_version": 1, "recording_id": recording_id, "camera_id": camera_id,
                        "project_id": project_id, "state": "recording", "created_wall_utc": wall_utc(),
                        "requested_duration_s": duration_s, "sample_hz": self.sample_hz,
                        "camera_only": True, "motion_commands": 0, "joint_measurements_available": False,
                        "calibration_verified": False, "format": "jpeg_sequence", "frames": [],
                        "timestamp_basis": "host_receive_estimate", "exposure_timestamp_available": False}
            write_json(directory / "manifest.json", manifest)
            self.active = recording_id
            self.thread = threading.Thread(target=self._record, args=(directory, manifest, duration_s),
                                           name="camera-recording", daemon=True)
            self.thread.start()
        return manifest

    def _record(self, directory, manifest, duration_s):
        source = self.sources[manifest["camera_id"]]
        begin, previous, total, gaps = time.monotonic(), None, 0, 0
        try:
            while time.monotonic() - begin < duration_s and not self.stop_event.is_set():
                frame = source.latest()
                if frame and frame.frame_id != previous:
                    if shutil.disk_usage(self.root).free < 256 * 1024 * 1024 + len(frame.jpeg) * 2:
                        raise RuntimeError("Local storage reserve reached")
                    total += len(frame.jpeg)
                    if total > self.max_bytes:
                        raise RuntimeError("Recording size limit reached")
                    name = f"{len(manifest['frames']):06d}.jpg"
                    target = directory / name
                    with target.open("wb") as output:
                        output.write(frame.jpeg)
                        output.flush()
                        os.fsync(output.fileno())
                    manifest["frames"].append({**frame.metadata(), "file": name,
                                               "sha256": hashlib.sha256(frame.jpeg).hexdigest(), "bytes": len(frame.jpeg)})
                    manifest["timestamp_basis"] = frame.timestamp_basis
                    previous = frame.frame_id
                    write_json(directory / "manifest.json", manifest)
                elif frame is None:
                    gaps += 1
                self.stop_event.wait(1 / self.sample_hz)
            state = "completed" if manifest["frames"] and gaps == 0 else "partial"
            manifest.update(state="interrupted" if self.stop_event.is_set() else state,
                            elapsed_s=time.monotonic() - begin, camera_gap_samples=gaps,
                            total_frame_bytes=total, completed_wall_utc=wall_utc())
        except Exception as exc:
            manifest.update(state="failed", error=type(exc).__name__, completed_wall_utc=wall_utc())
        finally:
            write_json(directory / "manifest.json", manifest)
            try:
                self._archive_and_emit(directory, manifest)
            finally:
                with self.lock:
                    self.active = None

    def get(self, recording_id):
        if not ID_PATTERN.fullmatch(recording_id):
            raise ValueError("Invalid recording_id")
        path = self.root / recording_id / "manifest.json"
        if not path.is_file():
            raise LookupError("Recording not found")
        return json.loads(path.read_text())

    def download_path(self, recording_id):
        self.get(recording_id)
        path = self.root / recording_id / "recording.tar"
        if not path.is_file():
            raise LookupError("Recording archive is not ready")
        return path


class NodeAgent:
    def __init__(self, node_id, mode, state_dir, token, *, sources=None, delivery=None,
                 central_url=None, ingest_token=None, calibration_dirs=(), camera_device=None,
                 legacy_dir=Path("/home/vlad/Explorer/data"), hailo_model=None, retarget_config_dir=None):
        if mode not in ("pi", "nano"):
            raise ValueError("mode must be pi or nano")
        if not token or len(token) < 16:
            raise ValueError("EXPLORER_NODE_TOKEN must contain at least 16 characters")
        self.node_id, self.mode, self.token = node_id, mode, token
        self.legacy_bridge = None
        if mode == "nano":
            from .legacy_bridge import LegacyBridge
            self.legacy_bridge = LegacyBridge(Path(legacy_dir).parent)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.delivery = delivery or EventDelivery(node_id, self.state_dir, central_url, ingest_token)
        self.sources = sources if sources is not None else ({"pi-overview": OpenCVCamera(
            camera_device or "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_D4EA612F-video-index0",
            self.state_dir / "camera-capture.lock")} if mode == "pi" else {"nano-rgbd": LegacyFileCamera(
                Path(legacy_dir) / "frame-raw.jpg", Path(legacy_dir) / "status.json")})
        self.calibration_dirs = calibration_dirs or ([Path.home()/'.cache/huggingface/lerobot/calibration'] if mode=='pi' else [])
        self.recordings = RecordingManager(self.state_dir / "recordings", self.sources, self.delivery.emit)
        self.mobile_records=None
        self.mobile_capture=None
        if mode=='nano':
            from .mobile_records import MobileRecords
            from .mobile_capture import MobileCaptureRecorder
            self.mobile_records=MobileRecords(Path(legacy_dir)/'mobile-demonstrations',self.state_dir/'mobile-archives')
            self.mobile_capture=MobileCaptureRecorder(legacy_dir,self.state_dir)
        self.stream_slots = threading.BoundedSemaphore(3)
        self.observer = None
        self.station = None
        arms_configuration = Path.home() / '.config/explorer/arms.json'
        if mode == "pi" and arms_configuration.exists():
            from .so101_station import SO101Station
            self.station = SO101Station(arms_configuration,self.state_dir,self.sources['pi-overview'])
        if mode == "pi" and hailo_model and "pi-overview" in self.sources:
            from .hailo_observer import HailoObserver
            self.observer = HailoObserver(self.sources["pi-overview"], hailo_model, self.delivery.emit)
        self.lab_vision=None
        if mode=='pi' and self.observer is not None:
            from .lab_vision_service import LabVisionService
            self.lab_vision=LabVisionService(self.sources['pi-overview'],self.observer,
                clock_domain=self.delivery.clock_domain,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                config_path=Path.home()/'.config/explorer/lab-vision.json',emit=self.delivery.emit)
        from .retarget_service import registered_geometry, PiRetargetService, nano_gateway
        retarget_config_dir = Path(retarget_config_dir or Path.home()/'.config/explorer')
        geometry = registered_geometry(retarget_config_dir)
        if mode == 'nano':
            self.arm_retarget = nano_gateway(geometry, self.legacy_bridge)
        else:
            try:
                self.arm_retarget = PiRetargetService.from_config(
                    geometry, self.station, self.sources.get('pi-overview'), self.state_dir, retarget_config_dir)
            except (OSError, ValueError, TypeError, KeyError) as error:
                self.arm_retarget = PiRetargetService(geometry, self.station, self.sources.get('pi-overview'), self.state_dir)
                self.arm_retarget.error = 'Private direct-arm peer configuration is invalid: '+type(error).__name__
            from .retarget_recording import RetargetRecorder
            self.arm_retarget.recorder = RetargetRecorder(self.state_dir/'retarget-recordings',self.sources.get('pi-overview'),
                self.delivery.emit,clock_domain=self.delivery.clock_domain,target_camera=self.arm_retarget.target_camera)

    def start(self):
        for source in self.sources.values():
            source.start()
        self.delivery.start()
        if self.observer:
            self.observer.start()
        if self.station:
            self.station.start()
        if self.lab_vision:
            self.lab_vision.start()
        if self.mode == 'pi':
            self.arm_retarget.start_worker()

    def close(self):
        retarget_error = None
        if self.lab_vision:
            self.lab_vision.close()
        if self.mode == 'pi':
            try:
                self.arm_retarget.close()
            except Exception as error:
                retarget_error = error
        if self.mobile_capture:
            self.mobile_capture.close()
        if self.station:
            self.station.close()
        self.recordings.close()
        if self.observer:
            self.observer.close()
        for source in self.sources.values():
            source.close()
        self.delivery.close()
        if retarget_error is not None:
            raise retarget_error

    def retarget_status(self):
        if self.mode == 'nano':
            # Absent physical registration stays useful even when the old API is offline.
            registration = self.arm_retarget.registration
            return {**self.arm_retarget.status(refresh=registration.get('state') == 'REGISTERED_DECLARATION'),
                    **({'state':'UNREGISTERED'} if registration.get('state') == 'UNREGISTERED' else {})}
        return self.arm_retarget.status()

    def retarget_command(self, operation, payload):
        if self.mode == 'pi':
            return self.arm_retarget.command(operation, payload)
        method = {'start':self.arm_retarget.start, 'action':self.arm_retarget.action,
                  'lease':self.arm_retarget.heartbeat, 'stop':self.arm_retarget.stop}.get(operation)
        if operation == 'cancel':
            return self.arm_retarget.cancel_all()
        if method is None:
            raise ValueError('Unknown direct arm endpoint')
        return method(payload)

    def legacy_request(self, path, payload=None, *, query=None):
        global_stop = payload is not None and ((path == 'control' and payload.get('op') == 'stop') or path in ('teleop/stop','teleop/disconnect','arm/trajectory/stop'))
        direct = self.arm_retarget.status()
        if global_stop:
            cancellation = None
            try:
                cancellation = self.arm_retarget.cancel_all('Existing manual/global STOP')
            except Exception as error:
                cancellation = {'state':'UNKNOWN_OUTCOME', 'error_type':type(error).__name__}
            code, result = self.legacy_bridge.request(path, payload)
            return code, {**result, 'direct_retarget_cancel':cancellation}
        competing = path in ('arm/jog','arm/finite','arm/trajectory/plan','arm/trajectory/start','arm/trajectory/lease',
                             'teleop/input','teleop/select','teleop/mode','teleop/resume')
        if payload is not None and competing and (direct['state'] == 'ACTIVE' or direct['inflight'] or direct['pending_stops'] or direct['stop_delivery_uncertain']):
            return 409, {'detail':'Direct arm route owns this Explorer adapter; STOP it before manual commands'}
        if query is not None:
            return self.legacy_bridge.request(path, payload, query=query)
        return self.legacy_bridge.request(path, payload)

    def native_teaching_request(self,path,payload=None):
        try:
            return self.legacy_bridge.request(path,payload)
        except (OSError,ValueError):
            return 503,{'error':'Native recording interface unavailable; existing cache remains readable'}

    def teaching_status(self):
        observed=self.mobile_capture.status() if self.mobile_capture else {}
        if observed.get('active'):
            return 200,observed
        code,native=self.native_teaching_request('teaching/mobile')
        if native.get('active'):
            return code,native
        latest=observed.get('last') or {}
        original=native.get('last') or {}
        if latest and (not original or latest.get('started_at',0)>=original.get('started',0)):
            return 200,observed
        return code,native

    def teaching_command(self,operation,payload):
        active=self.mobile_capture.status().get('active') if self.mobile_capture else None
        expected=payload.get('expected_id',payload.get('session'))
        if active and operation in ('finish','stage') and expected is not None and expected!=active['id']:
            return 409,{'detail':'Recording session changed; data labels were not applied'}
        if not active and operation=='finish' and self.mobile_capture:
            code,current=self.teaching_status()
            last=current.get('last') or {}
            if last.get('format')=='explorer_mobile_observation_episode_v1' and expected in (None,last['id']):
                return 200,current
        if active and operation=='start':
            return 409,{'detail':'Запись данных уже выполняется'}
        if active and operation!='start':
            methods={'lease':lambda:self.mobile_capture.heartbeat(payload['session'],payload.get('observing',False)),
                     'stage':lambda:self.mobile_capture.stage(payload['stage']),
                     'finish':lambda:self.mobile_capture.finish(payload.get('outcome','unknown'),active['id'])}
            if operation not in methods:
                raise ValueError('Unknown data recording operation')
            methods[operation]()
            return 200,self.mobile_capture.status()
        code,result=self.native_teaching_request('teaching/mobile/'+operation,payload)
        detail=str(result.get('detail',result.get('error','')))
        unavailable=code>=500 or any(text in detail for text in ('из-за питания','истории команд руки','свежего состояния','свежего кадра','требует наблюдателя'))
        if operation=='start' and code>=400 and unavailable and self.mobile_capture:
            try:
                self.mobile_capture.start(**payload)
            except RuntimeError as error:
                return 409,{'detail':str(error)}
            return 200,self.mobile_capture.status()
        return code,result

    def mobile_archive(self,ident):
        if self.mobile_capture and isinstance(ident,str) and re.fullmatch('[0-9a-f]{32}',ident):
            try:
                self.mobile_capture.get(ident)
            except FileNotFoundError:
                pass
            else:
                return self.mobile_capture.archive(ident)
        return self.mobile_records.archive(ident)

    def cameras(self):
        cameras = [source.metadata() for source in self.sources.values()]
        if self.mode == "nano":
            cameras.append({"id": "nano-logitech", "label": "Logitech · Jetson Nano",
                            "available": False, "error": "not_found_in_audited_live_sources"})
        return cameras

    def inventory(self):
        serial = discover_serial()
        calibrations = discover_calibrations(self.calibration_dirs)
        station = self.station.status() if self.station else None
        mobile=self.mobile_capture.status() if self.mobile_capture else None
        if mobile:
            last=mobile.get('last') or {}
            mobile={key:mobile[key] for key in ('active','last','format','capture_power_gate','actuator_commands')}
            mobile['archive_ready']=bool(last and (self.mobile_capture.root/(last['id']+'.archive.json')).is_file())
            mobile['ready_recording_ids']=[r['id'] for r in self.mobile_capture.recordings()
                if r['state'] in ('complete','interrupted') and (self.mobile_capture.root/(r['id']+'.archive.json')).is_file()]
        if station:
            for item in serial:
                for role,profile in station['roles'].items():
                    if item['path']==profile['port']:
                        item.update(role=role,servo_bus_opened=station['available'],servo_model_verified=True,
                                    positions_available=bool(station['measurements']))
            for item in calibrations:
                item['verified_on_connected_hardware']=any(profile['calibration_sha256']==item['sha256'] for profile in station['roles'].values())
        observer = self.observer.snapshot() if self.observer else {"available": False, "state": "not_configured"}
        cameras = self.cameras()
        return {"schema_version": 1, "node_id": self.node_id, "hostname": socket.gethostname(),
                "wall_utc": wall_utc(), "monotonic_ns": time.monotonic_ns(), "clock_domain": self.delivery.clock_domain,
                "hardware": {"architecture": os.uname().machine, "hailo_device_present": Path("/dev/hailo0").exists()},
                "cameras": cameras, "serial_devices": serial, "calibrations": calibrations,
                "perception": observer, "station": station,"mobile_observation":mobile,
                "lab_vision":self.lab_vision.snapshot() if self.lab_vision else None,
                "capabilities": {"physical_motion": bool(station and station['available']) or self.mode=='nano', "servo_bus_access": bool(station and station['available']),
                                 "serial_discovery": True, "joint_measurements": bool(station and station['measurements']),
                                 "calibration_verified": bool(station and len(station['roles'])==2), "arm_roles_assigned": bool(station and len(station['roles'])==2),
                                 "camera_preview": any(camera["available"] for camera in cameras),
                                 "camera_only_recording": True, "hailo_detection": observer["available"],
                                 "power_independent_observation_capture": bool(self.mobile_capture),
                                 "durable_event_outbox": True, "vlm": False},
                "errors": (["so101_roles_unassigned", "so101_calibration_unverified"] if self.mode == "pi" else [])
                          + ([self.delivery.error] if self.delivery.error else [])}


def make_handler(agent):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _json(self, code, payload):
            body = canonical_bytes(payload)
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authenticated(self):
            expected = "Bearer " + agent.token
            provided = self.headers.get("Authorization", "")
            return secrets.compare_digest(provided.encode(), expected.encode())

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path == "/health":
                self._json(200, {"ok": True, "schema_version": 1, "node_id": agent.node_id,
                                 "mode": agent.mode, "physical_motion": agent.mode == "nano",
                                 "controller_settings_changed": False})
            elif not self._authenticated():
                self._json(401, {"error": "unauthorized"})
            else:
                try:
                    self._get(path)
                except (ValueError, LookupError) as exc:
                    self._json(404, {"error": str(exc)})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except FileNotFoundError:
                    self._json(404,{'error':'Requested source file does not exist'})
                except OSError as exc:
                    self._json(503,{'error':str(exc)})

        def _get(self, path):
            parts = path.strip("/").split("/")
            if path=='/lab/vision/status' and agent.lab_vision is not None:
                self._json(200,agent.lab_vision.snapshot())
            elif path=='/lab/vision/freeze' and agent.lab_vision is not None:
                frame,observation=agent.lab_vision.frozen()
                self._json(200,dict(frame=frame.metadata(),observation=observation))
            elif len(parts)==4 and parts[:3]==['lab','vision','frames'] and agent.lab_vision is not None:
                frame,_=agent.lab_vision.frozen(parts[3])
                self.send_response(200)
                for key,value in {'Content-Type':'image/jpeg','Content-Length':len(frame.jpeg),'Cache-Control':'no-store',
                    'X-Frame-ID':frame.frame_id,'X-Frame-SHA256':frame.sha256,'X-Clock-Domain':frame.clock_domain,
                    'X-Monotonic-NS':frame.monotonic_ns,'X-Boot-ID':frame.boot_id,'X-Timestamp-Basis':frame.timestamp_basis}.items():
                    self.send_header(key,str(value))
                self.end_headers();self.wfile.write(frame.jpeg)
            elif path == '/arm-retarget/status':
                self._json(200, agent.retarget_status())
            elif agent.mode == 'pi' and path == '/arm-retarget/recordings':
                self._json(200, {'recordings':agent.arm_retarget.recorder.recordings()})
            elif agent.mode == 'pi' and len(parts)==4 and parts[:2]==['arm-retarget','recordings']:
                if parts[3]=='download':self._download(agent.arm_retarget.recorder.archive(parts[2]),parts[2])
                elif parts[3]=='manifest':self._json(200, agent.arm_retarget.recorder.manifest(parts[2]))
                else:raise LookupError('Unknown direct recording endpoint')
            elif agent.mobile_records is not None and path=='/mobile/recordings':
                self._json(200,{'recordings':agent.mobile_records.recordings()+(agent.mobile_capture.recordings() if agent.mobile_capture else [])})
            elif agent.mobile_records is not None and len(parts)==4 and parts[:2]==['mobile','recordings'] and parts[3]=='download':
                self._download(agent.mobile_archive(parts[2]),parts[2])
            elif path=='/legacy/teaching/mobile' and agent.mobile_capture is not None:
                code,payload=agent.teaching_status()
                self._json(code,payload)
            elif path.startswith('/station/') and agent.station is not None:
                if path=='/station/status':
                    self._json(200,agent.station.status())
                elif path=='/station/recordings':
                    self._json(200,{'recordings':agent.station.recordings()})
                elif len(parts)==4 and parts[1]=='recordings' and parts[3]=='download':
                    self._download(agent.station.archive(parts[2]),parts[2])
                elif len(parts)==4 and parts[1]=='recordings' and parts[3]=='manifest':
                    self._json(200,agent.station.manifest(parts[2]))
                else:
                    raise LookupError('Unknown station endpoint')
            elif path.startswith("/legacy/") and agent.legacy_bridge is not None:
                native_path=path.removeprefix('/legacy/')
                if native_path=='explorer/retarget/dispatch':
                    from .legacy_bridge import parse_dispatch_query
                    query=parse_dispatch_query(urlsplit(self.path).query)
                    code,payload=agent.legacy_request(native_path,query=query)
                else:
                    code,payload=agent.legacy_request(native_path)
                self._json(code, payload)
            elif path == "/inventory":
                self._json(200, agent.inventory())
            elif path == "/cameras":
                self._json(200, {"cameras": agent.cameras()})
            elif len(parts) == 3 and parts[0] == "cameras" and parts[2] in ("snapshot", "stream"):
                source = agent.sources.get(parts[1])
                if not source:
                    raise LookupError("Camera not found")
                if parts[2] == "stream":
                    self._stream(source)
                else:
                    frame = source.latest()
                    if frame is None:
                        self._json(503, {"error": "no_fresh_frame"})
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(frame.jpeg)))
                        self.send_header("Cache-Control", "no-store")
                        self._frame_headers(frame)
                        self.end_headers()
                        self.wfile.write(frame.jpeg)
            elif len(parts) in (2, 3) and parts[0] == "recordings":
                if len(parts) == 2 or parts[2] == "manifest":
                    self._json(200, agent.recordings.get(parts[1]))
                elif parts[2] == "download":
                    self._download(agent.recordings.download_path(parts[1]), parts[1])
                else:
                    raise LookupError("Endpoint not found")
            else:
                raise LookupError("Endpoint not found")

        def _frame_headers(self, frame):
            self.send_header("X-Frame-ID", frame.frame_id)
            self.send_header("X-Wall-UTC", frame.wall_utc)
            self.send_header("X-Monotonic-NS", str(frame.monotonic_ns))
            self.send_header("X-Timestamp-Basis", frame.timestamp_basis)
            self.send_header("X-Clock-Domain", agent.delivery.clock_domain)
            if frame.source_wall_utc is not None:
                self.send_header("X-Source-Wall-UTC", frame.source_wall_utc)

        def _stream(self, source):
            if not agent.stream_slots.acquire(blocking=False):
                self._json(429, {"error": "preview_client_limit"})
                return
            try:
                if source.latest() is None:
                    self._json(503, {"error": "no_fresh_frame"})
                    return
                self.connection.settimeout(5)
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                until, previous = time.monotonic() + 300, None
                while time.monotonic() < until and not source.stop_event.is_set():
                    frame = source.latest()
                    if frame is None:
                        break
                    if frame.frame_id != previous:
                        header = (f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame.jpeg)}\r\n"
                                  f"X-Frame-ID: {frame.frame_id}\r\nX-Wall-UTC: {frame.wall_utc}\r\n"
                                  f"X-Monotonic-NS: {frame.monotonic_ns}\r\n"
                                  f"X-Timestamp-Basis: {frame.timestamp_basis}\r\n\r\n").encode()
                        self.wfile.write(header + frame.jpeg + b"\r\n")
                        self.wfile.flush()
                        previous = frame.frame_id
                    source.stop_event.wait(0.1)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            finally:
                self.close_connection = True
                agent.stream_slots.release()

        def _download(self, path, recording_id):
            self.connection.settimeout(10)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-tar")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{recording_id}.tar"')
            self.send_header("X-Content-SHA256", file_sha256(path))
            self.end_headers()
            with path.open("rb") as source:
                shutil.copyfileobj(source, self.wfile, length=128 * 1024)

        def do_POST(self):
            if not self._authenticated():
                self.close_connection = True
                self._json(401, {"error": "unauthorized"})
                return
            path = unquote(urlsplit(self.path).path)
            legacy = path.startswith("/legacy/") and agent.legacy_bridge is not None
            station = path.startswith('/station/') and agent.station is not None
            retarget = path.startswith('/arm-retarget/')
            lab=path.startswith('/lab/vision/') and agent.lab_vision is not None
            if path != "/recordings" and not legacy and not station and not retarget and not lab:
                self.close_connection = True
                self._json(404, {"error": "Endpoint not found; no physical API is provided"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY_BYTES:
                    raise ValueError("Invalid request body length")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object")
                if lab:
                    self._json(200,agent.lab_vision.command(path.removeprefix('/lab/vision/'),payload))
                elif retarget:
                    self._json(200, agent.retarget_command(path.removeprefix('/arm-retarget/'), payload))
                elif station:
                    if path == '/station/stop' and agent.mode == 'pi':
                        agent.arm_retarget.stop()
                    direct = agent.arm_retarget.status() if agent.mode == 'pi' else {}
                    if path in ('/station/teleop/start','/station/policy/start','/station/teleop/recenter','/station/action') and (
                            direct.get('state') == 'ACTIVE' or direct.get('lifecycle_inflight')):
                        raise RuntimeError('Direct ROSMASTER teaching uses the idle measured source cache; stop that route first')
                    self._json(200,agent.station.command(path.removeprefix('/station/'),payload))
                elif legacy:
                    if path.startswith('/legacy/teaching/mobile/') and agent.mobile_capture is not None:
                        code,result=agent.teaching_command(path.rsplit('/',1)[1],payload)
                    else:
                        code, result = agent.legacy_request(path.removeprefix("/legacy/"), payload)
                    self._json(code, result)
                else:
                    manifest = agent.recordings.start(payload.get("camera_id"), payload.get("duration_s"),
                                                      payload.get("recording_id"), payload.get("project_id"))
                    self._json(202, manifest)
            except (ValueError, TypeError) as exc:
                self.close_connection = True
                self._json(400, {"error": str(exc)})
            except LookupError as exc:
                self._json(503, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
            except OSError as exc:
                self._json(503, {"error": str(exc)})

    return Handler


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self.client_slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if self.client_slots.acquire(blocking=False):
            try:
                super().process_request(request, client_address)
            except Exception:
                self.client_slots.release()
                raise
        else:
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.client_slots.release()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pi", "nano"), required=True)
    parser.add_argument("--node-id")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/share/explorer-node")
    parser.add_argument("--legacy-dir", type=Path, default=Path("/home/vlad/Explorer/data"))
    parser.add_argument("--camera-device")
    parser.add_argument("--calibration-dir", action="append", type=Path, default=[])
    parser.add_argument("--central-url", default=os.environ.get("EXPLORER_CENTRAL_URL"))
    parser.add_argument("--hailo-model", default=os.environ.get('EXPLORER_HAILO_MODEL',"/usr/share/hailo-models/yolov8m_h10.hef"))
    parser.add_argument("--no-hailo", action="store_true")
    args = parser.parse_args(argv)
    agent = NodeAgent(args.node_id or args.mode, args.mode, args.state_dir,
                      os.environ.get("EXPLORER_NODE_TOKEN"), central_url=args.central_url,
                      ingest_token=os.environ.get("EXPLORER_INGEST_TOKEN"),
                      calibration_dirs=args.calibration_dir, camera_device=args.camera_device,
                      legacy_dir=args.legacy_dir, hailo_model=None if args.no_hailo else args.hailo_model)
    server = AgentHTTPServer((args.bind, args.port or (8091 if args.mode == "pi" else 8092)), make_handler(agent))
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    agent.start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        agent.close()


if __name__ == "__main__":
    main()
