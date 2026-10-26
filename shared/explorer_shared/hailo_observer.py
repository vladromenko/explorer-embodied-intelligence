"""ENGINEERING_GLUE: Hailo-10H object estimates; no robot, audio, identity or actuator imports.

The hardware adapter derives from the owner's audited MILO detector. See
shared/perception/PROVENANCE.md. Importing this module never opens a device.
"""
from __future__ import annotations

from contextlib import ExitStack
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import subprocess
import threading
import time


COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)


def _clip(value):
    return min(1.0, max(0.0, float(value)))


@dataclass(frozen=True)
class Letterbox:
    width: int
    height: int
    target_width: int
    target_height: int

    def __post_init__(self):
        if min(self.width, self.height, self.target_width, self.target_height) <= 0:
            raise ValueError("Image dimensions must be positive")

    @property
    def resized(self):
        scale = min(self.target_width / self.width, self.target_height / self.height)
        return max(1, round(self.width * scale)), max(1, round(self.height * scale))

    @property
    def padding(self):
        width, height = self.resized
        return (self.target_width - width) // 2, (self.target_height - height) // 2

    def restore(self, yxyx):
        width, height = self.resized
        left, top = self.padding
        y1, x1, y2, x2 = yxyx
        return [_clip((x1 * self.target_width - left) / width),
                _clip((y1 * self.target_height - top) / height),
                _clip((x2 * self.target_width - left) / width),
                _clip((y2 * self.target_height - top) / height)]


def decode_hailo(classes, transform, threshold=0.35):
    if len(classes) != 80:
        raise ValueError("Expected an 80-class COCO NMS model")
    detections = []
    for class_id, rows in enumerate(classes):
        for row in rows:
            if len(row) != 5 or not all(math.isfinite(float(value)) for value in row):
                raise ValueError("Invalid Hailo NMS row")
            if float(row[4]) >= threshold:
                box = transform.restore(row[:4])
                if box[2] > box[0] and box[3] > box[1]:
                    detections.append({"class_id": class_id, "label": COCO_CLASSES[class_id],
                                       "box": box, "score": float(row[4])})
    return sorted(detections, key=lambda item: item["score"], reverse=True)[:100]


class HailoDetector:
    def __init__(self, path, threshold=0.35):
        import cv2
        import numpy as np
        import hailo_platform as hailo

        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError("Hailo model does not exist")
        parsed = subprocess.run(["hailortcli", "parse-hef", str(path)], check=True,
                                capture_output=True, text=True, timeout=10)
        compatible = next((line for line in parsed.stdout.splitlines()
                           if "HEF Compatible for:" in line), "")
        if "HAILO10H" not in compatible:
            raise ValueError("A HAILO10H HEF is required")
        with path.open("rb") as model_file:
            model_hash = hashlib.file_digest(model_file, "sha256").hexdigest()
        self.model_info = {"name": path.name, "sha256": model_hash,
                           "architecture": "HAILO10H", "runtime_api": "HailoRT 5 InferModel"}
        self.cv2, self.np, self.threshold = cv2, np, threshold
        self._resources = ExitStack()
        try:
            params = hailo.VDevice.create_params()
            params.scheduling_algorithm = hailo.HailoSchedulingAlgorithm.ROUND_ROBIN
            self.device = self._resources.enter_context(hailo.VDevice(params))
            self.model = self.device.create_infer_model(str(path))
            self.model.set_batch_size(1)
            if len(self.model.inputs) != 1 or len(self.model.outputs) != 1:
                raise ValueError("Expected one RGB input and one COCO NMS output")
            self.height, self.width, channels = self.model.input().shape
            output = self.model.output()
            if channels != 3 or output.format.order != hailo.FormatOrder.HAILO_NMS_BY_CLASS:
                raise ValueError("Expected RGB input and HAILO_NMS_BY_CLASS output")
            if self.model.hef.get_output_vstream_infos()[0].nms_shape.number_of_classes != 80:
                raise ValueError("Expected 80 COCO classes")
            self.model.input().set_format_type(hailo.FormatType.UINT8)
            output.set_format_type(hailo.FormatType.FLOAT32)
            self.configured = self._resources.enter_context(self.model.configure())
            self.bindings = self.configured.create_bindings(output_buffers={
                output.name: np.empty(output.shape, dtype=np.float32)})
        except Exception:
            self._resources.close()
            raise

    def detect(self, frame):
        cv2 = self.cv2
        geometry = Letterbox(frame.shape[1], frame.shape[0], self.width, self.height)
        resized = cv2.resize(frame, geometry.resized)
        left, top = geometry.padding
        width, height = geometry.resized
        padded = cv2.copyMakeBorder(resized, top, self.height - height - top,
                                   left, self.width - width - left,
                                   cv2.BORDER_CONSTANT, value=(114, 114, 114))
        self.bindings.input().set_buffer(cv2.cvtColor(padded, cv2.COLOR_BGR2RGB))
        begin = time.monotonic()
        self.configured.run([self.bindings], 2000)
        elapsed_ms = (time.monotonic() - begin) * 1000
        return decode_hailo(self.bindings.output().get_buffer(), geometry, self.threshold), elapsed_ms

    def close(self):
        self._resources.close()


def box_iou(a, b):
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda box: max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    union = area(a) + area(b) - intersection
    return intersection / union if union > 0 else 0.0


class IoUTracker:
    """Session-local associations; IDs are neither identities nor grasp evidence."""
    def __init__(self, max_age=1.2):
        self.max_age, self.next_id, self.tracks = max_age, 1, {}

    def update(self, detections, now):
        self.tracks = {key: value for key, value in self.tracks.items() if now - value[1] <= self.max_age}
        used, result = set(), []
        for detection in detections:
            candidates = [(box_iou(previous["box"], detection["box"]), key)
                          for key, (previous, _) in self.tracks.items()
                          if key not in used and previous["class_id"] == detection["class_id"]]
            overlap, track_id = max(candidates, default=(0, 0))
            if overlap < 0.25:
                track_id = self.next_id
                self.next_id += 1
            used.add(track_id)
            item = dict(detection, track_id=track_id)
            self.tracks[track_id] = (item, now)
            result.append(item)
        return result


class HailoObserver:
    """Independent bounded worker. Camera capture does not wait for inference."""
    def __init__(self, source, model_path, emit, detector_factory=HailoDetector,
                 inference_hz=2.0, event_interval_s=5.0):
        self.source, self.model_path, self.emit = source, model_path, emit
        self.detector_factory = detector_factory
        self.period = 1 / min(5.0, max(0.2, inference_hz))
        self.event_interval = max(5.0, event_interval_s)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.state = {"available": False, "state": "not_started", "detections": [],
                      "evidence_type": "vision_estimate", "contact_verified": False}
        self.thread = None
        self._semantic_owner = threading.Lock()
        self._pause_requested = threading.Event()
        self._paused = threading.Event()
        self._wake = threading.Event()
        self._semantic_release_unknown = False

    def start(self):
        self.thread = threading.Thread(target=self._work, name="hailo-observer", daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
        if result["available"] and self.source.latest() is None:
            result.update(available=False, state="camera_stale")
        return result

    def close(self):
        self.stop_event.set()
        self._wake.set()
        if self.thread:
            self.thread.join(timeout=5)

    @contextmanager
    def semantic_slot(self, timeout_s=10.):
        """Release only the CV accelerator context, preserving camera/servo owners.

        The slot's release proof is set by the semantic worker ONLY after its
        own backend closes successfully. Unknown release keeps CV paused.
        """
        if not self._semantic_owner.acquire(timeout=timeout_s):
            raise TimeoutError('Another semantic owner holds the accelerator slot')
        slot = {'backend_released': False, 'backend_created': False}
        try:
            if (self._semantic_release_unknown or not self.thread or not self.thread.is_alive() or
                    self.stop_event.is_set()):
                raise RuntimeError('CV observer unavailable or previous semantic release unknown')
            self._pause_requested.set()
            self._wake.set()
            if not self._paused.wait(timeout_s):
                self._pause_requested.clear()
                self._wake.set()
                raise TimeoutError('CV context did not confirm release')
            yield slot
        finally:
            if slot['backend_created'] and not slot['backend_released']:
                self._semantic_release_unknown = True
                with self.lock:
                    self.state.update(available=False, state='semantic_release_unknown')
            elif not self._semantic_release_unknown:
                self._pause_requested.clear()
                self._wake.set()
            self._semantic_owner.release()

    def _work(self):
        detector = None
        try:
            detector = self.detector_factory(self.model_path)
            tracker = IoUTracker()
            last_frame, last_event, signature = None, 0.0, None
            while not self.stop_event.is_set():
                if self._pause_requested.is_set():
                    if detector is not None:
                        detector.close()
                        detector = None
                    with self.lock:
                        self.state.update(available=False, state=('semantic_release_unknown' if self._semantic_release_unknown
                                                                else 'cv_released_for_semantic_request'))
                    self._paused.set()
                else:
                    self._paused.clear()
                    if detector is None:
                        detector = self.detector_factory(self.model_path)
                        last_frame = None
                frame = self.source.latest() if detector is not None else None
                if detector is not None and frame and frame.frame_id != last_frame and frame.bgr is not None:
                    detections, elapsed = detector.detect(frame.bgr)
                    now = time.monotonic()
                    detections = tracker.update(detections, now)
                    current = tuple(sorted(item["class_id"] for item in detections))
                    observation = {"available": True, "state": "running", "camera_id": self.source.camera_id,
                                   "frame_id": frame.frame_id, "frame_wall_utc": frame.wall_utc,
                                   "frame_monotonic_ns": frame.monotonic_ns,
                                   "timestamp_basis": frame.timestamp_basis, "model": detector.model_info,
                                   "inference_ms": elapsed, "detections": detections,
                                   "evidence_type": "vision_estimate", "contact_verified": False}
                    with self.lock:
                        self.state = observation
                    if now - last_event >= self.event_interval and (current != signature or now - last_event >= 30):
                        self.emit({"kind": "perception.observation", **observation})
                        last_event, signature = now, current
                    last_frame = frame.frame_id
                self._wake.wait(self.period)
                self._wake.clear()
        except Exception as exc:
            with self.lock:
                self.state.update(available=False, state="unavailable", error=type(exc).__name__)
        finally:
            if detector:
                detector.close()
            self._paused.clear()
