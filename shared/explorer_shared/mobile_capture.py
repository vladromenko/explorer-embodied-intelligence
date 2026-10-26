"""ENGINEERING_GLUE: observation-only mobile recorder reading existing cache files.

LOW_POWER, unknown arm pose and observer presence are recorded information,
never capture admission. No ROS/controller imports, device opens or commands.
This module never emits the action-labelled native explorer_mobile_episode_v3.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import tarfile
import threading
import time
import uuid

FORMAT = "explorer_mobile_observation_episode_v1"
ROW_SCHEMA = "explorer_mobile_observation_sample_v1"
AXES = ["base", "shoulder", "elbow", "wrist_pitch", "wrist_roll", "gripper", "vx", "vy", "wz"]
SOURCE_LIMITS = {"status.json": 2 * 1024**2, "camera-frame.json": 256 * 1024,
                 "frame-raw.jpg": 16 * 1024**2, "rgbd-cache-metadata.json": 256 * 1024,
                 "rgbd-snapshot.npz": 64 * 1024**2, "arm-telemetry-fault.json": 256 * 1024}
IDENTITY = re.compile(r"[0-9a-f]{32}")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_bytes(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def save(path, value):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        write_bytes(temporary, encoded(value) + b"\n")
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def stable_read(path, limit):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("source_not_regular_or_oversized")
        raw = stream.read(limit + 1)
        after, current = os.fstat(stream.fileno()), path.lstat()
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after) or identity(before) != identity(current):
            raise ValueError("source_changed_during_read")
    return raw, {"sha256": sha(raw), "bytes": len(raw), "source_mtime_ns": before.st_mtime_ns,
                 "source_device": before.st_dev, "source_inode": before.st_ino}


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def strict_object(raw):
    def invalid(value):
        raise ValueError("non_finite_source_json")
    value = json.loads(raw, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("source_json_not_object")
    encoded(value)  # Also rejects finite-looking exponent syntax that decodes to inf.
    return value


def vector(value, length):
    source = value if isinstance(value, list) and len(value) == length else [None] * length
    values = [item if type(item) in (int, float) and math.isfinite(item) else None for item in source]
    return values, [item is not None for item in values]


def projection(status):
    """Actual status.velocity is gate.output; odom_velocity is a separate estimate.

No reconstruction of six arm coordinates is attempted from phase/goal/history.
The original arm state remains in the exact retained status bytes and raw fields.
"""
    command, command_valid = vector(status.get("velocity"), 3)
    odom, odom_valid = vector(status.get("odom_velocity"), 3)
    return {"axis_names": AXES, "units": ["deg"] * 6 + ["m/s", "m/s", "rad/s"],
            "reported_state": [None] * 6 + odom, "state_valid_mask": [False] * 6 + odom_valid,
            "state_axis_basis": ["unknown_arm_pose"] * 6 + ["legacy_status.odom_velocity_estimate"] * 3,
            "reported_command": [None] * 6 + command, "command_valid_mask": [False] * 6 + command_valid,
            "command_axis_basis": ["unverified_native_arm_contract"] * 6 + ["legacy_status.velocity_gate_output"] * 3,
            "arm_command_state_raw": status.get("arm_command_state"), "arm_state_raw": status.get("arm_state"),
            "measured_arm_feedback_claimed": False, "native_9d_contract_satisfied": False}


class MobileCaptureRecorder:
    def __init__(self, source_dir, state_dir, *, sample_hz=5, max_duration_s=300,
                 max_bytes=1024**3, retain_rgbd=True):
        if type(sample_hz) not in (int, float) or not math.isfinite(sample_hz) or not 0 < sample_hz <= 10:
            raise ValueError("sample_hz must be finite in (0,10]")
        if type(max_duration_s) not in (int, float) or not math.isfinite(max_duration_s) or not 0 < max_duration_s <= 3600:
            raise ValueError("max_duration_s must be finite in (0,3600]")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.source = Path(source_dir).resolve()
        self.root = (Path(state_dir).resolve() / "mobile-capture")
        if self.root == self.source or self.root in self.source.parents or self.source in self.root.parents:
            raise ValueError("Recorder state must be outside the original source tree")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("Recorder state cannot be a symlink")
        self.root.chmod(0o700)
        self.owner = os.open(self.root / ".recorder.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.owner)
            raise
        self.lock = threading.RLock()
        self.sample_hz, self.max_duration_s, self.max_bytes = sample_hz, max_duration_s, max_bytes
        self.retain_rgbd = bool(retain_rgbd)
        self.active, self.last, self.thread = None, None, None
        self.stop = threading.Event()
        self.closed = False
        try:
            self._recover()
        except BaseException:
            os.close(self.owner)
            raise

    def folder(self, ident):
        if not isinstance(ident, str) or not IDENTITY.fullmatch(ident):
            raise ValueError("Recording identity must be UUID32")
        path = self.root / ident
        if path.is_symlink():
            raise ValueError("Recording folder cannot be linked")
        return path

    def _persist(self, record):
        with self.lock:
            value = copy.deepcopy(record)
        save(self.folder(record["id"]) / "episode.json", value)

    def _recover(self):
        for folder in sorted(self.root.iterdir()):
            if IDENTITY.fullmatch(folder.name) and folder.is_dir() and not folder.is_symlink():
                try:
                    record = strict_object((folder / "episode.json").read_bytes())
                    if record.get("format") == FORMAT and record.get("state") in ("recording", "finishing"):
                        count, frames, note = self._journal_count(folder)
                        record.update(state="interrupted", outcome="unknown", samples=count, frames=frames,
                                      recovery_note=note, finished_at=time.time(), recovered_after_restart=True)
                        record["training_admission"]["operator_success_label"] = False
                        self._persist(record)
                        self._archive(record)
                    elif record.get("format") == FORMAT and record.get("state") in ("complete", "interrupted") and not (self.root / (record["id"] + ".archive.json")).exists():
                        self._archive(record)
                    if record.get("format") == FORMAT:
                        self.last = record["id"]
                except (OSError, ValueError, KeyError, tarfile.TarError) as error:
                    # Preserve all original bytes; an unreadable recovery is visible, never erased.
                    save(folder / "recovery-error.json", {"recovered": False, "error_type": type(error).__name__})

    @staticmethod
    def _journal_count(folder):
        count, frames, note = 0, 0, "All complete journal rows recovered; original journal bytes retained"
        journal = folder / "samples.jsonl"
        if journal.exists():
            with journal.open("rb") as stream:
                for line in stream:
                    try:
                        row = strict_object(line)
                        if row.get("schema") != ROW_SCHEMA:
                            raise ValueError("Unknown journal row")
                        for source in row.get("source_files", {}).values():
                            path = folder / source["file"]
                            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or file_sha(path) != source["sha256"]:
                                raise ValueError("Interrupted source checksum differs")
                        if row.get("image") is not None:
                            image = row.get("source_files", {}).get("frame-raw.jpg", {})
                            if row["image"] != image.get("file") or row.get("image_sha256") != image.get("sha256"):
                                raise ValueError("Interrupted image reference differs")
                            frames += 1
                        count += 1
                    except (OSError, ValueError, KeyError):
                        note = "Incomplete/invalid journal tail excluded from sample count; original bytes retained"
                        break
        return count, frames, note

    def start(self, name="Наблюдение ROSMASTER", observing=False, **options):
        if not isinstance(name, str) or len(name) > 200 or type(observing) is not bool:
            raise ValueError("Invalid recording name/observing annotation")
        duration = options.get("duration_s", self.max_duration_s)
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= self.max_duration_s:
            raise ValueError("Recording duration exceeds the configured bound")
        ident = options.get("recording_id") or uuid.uuid4().hex
        folder = self.folder(ident)
        annotations = {key: options[key] for key in ("object_label", "object_class", "size_class", "destination") if key in options}
        if any(not isinstance(value, str) or len(value) > 500 for value in annotations.values()):
            raise ValueError("Object annotations must be bounded strings")
        with self.lock:
            if self.closed:
                raise RuntimeError("Recorder closed")
            if self.active:
                raise RuntimeError("A mobile observation recording is already active")
            folder.mkdir(mode=0o700)
            (folder / "sources").mkdir(mode=0o700)
            write_bytes(folder / "samples.jsonl", b"")
            now = time.time()
            record = {"id": ident, "session": ident, "name": name, "format": FORMAT,
                      "source": "read_only_existing_mobile_cache", "source_type": "mobile_observation",
                      "state": "recording", "started_at": now, "started_wall_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                      "physical_sample_rate_hz": self.sample_hz, "requested_duration_s": duration,
                      "samples": 0, "frames": 0, "stored_bytes": 0, "outcome": "unknown",
                      "label_source": "operator", "observing": observing, "last_heartbeat_at": now,
                      "project_id": options.get("project_id", "02"), "native_9d_contract_satisfied": False,
                      "joint_state_source": "unknown_arm_pose_preserved", "motion_commands": 0,
                      "quality": {"usable": False, "analysis_usable": True, "reason": "observation_only_unverified_9d_arm_action_contract"},
                      "training_admission": {"source_quality_usable": False, "operator_success_label": False,
                                             "reason": "Observation-only cache capture; native causal 9D arm/state/action contract is unverified"},
                      "timestamp_policy": "Original source stamps retained verbatim; collector times separately labelled; no frame-stamp relabel",
                      "object_metadata": annotations,
                      "source_errors": {}, "source_available": {}, "polls": 0}
            record.update(stage="observation", stage_annotations=[])
            # Validate annotation serialization before starting a background writer.
            encoded(record)
            self._persist(record)
            self.stop.clear()
            self.active = record
            self.thread = threading.Thread(target=self._work, args=(record,), name="mobile-observation-writer", daemon=True)
            self.thread.start()
            return copy.deepcopy(record)

    def heartbeat(self, session, observing=False):
        if type(observing) is not bool:
            raise ValueError("observing must be boolean")
        with self.lock:
            if not self.active or session != self.active["session"]:
                raise ValueError("Unknown active recording session")
            if self.active["state"] == "recording":
                self.active.update(last_heartbeat_at=time.time(), observing=observing)
            return copy.deepcopy(self.active)

    def stage(self, stage):
        if not isinstance(stage, str) or not stage.strip() or len(stage) > 80:
            raise ValueError("Stage is an informational text label of at most 80 characters")
        with self.lock:
            if not self.active or self.active["state"] != "recording":
                raise ValueError("No active observation recording")
            if len(self.active["stage_annotations"]) >= 1000:
                raise ValueError("Stage annotation count bound reached")
            self.active["stage"] = stage
            self.active["stage_annotations"].append({"stage": stage, "collector_wall_unix": time.time()})
            return copy.deepcopy(self.active)

    def _sample(self, record, previous):
        files, objects, errors, available = {}, {}, {}, {}
        raw_items = {}
        for name, limit in SOURCE_LIMITS.items():
            if self.retain_rgbd or name != "rgbd-snapshot.npz":
                try:
                    raw, info = stable_read(self.source / name, limit)
                    available[name] = True
                    raw_items[name] = (raw, info)
                    if name.endswith(".json"):
                        try:
                            objects[name] = strict_object(raw)
                        except (ValueError, UnicodeError):
                            errors[name] = "source_json_parse_error_raw_bytes_retained"
                except (OSError, ValueError) as error:
                    available[name] = False
                    errors[name] = str(error) if isinstance(error, ValueError) else type(error).__name__
        with self.lock:
            annotations = {"stage": record["stage"], "observing": record["observing"]}
            record.update(source_available=available, source_errors=errors, polls=record["polls"] + 1)
        fingerprint = sha(encoded({"sources": {name: item[1]["sha256"] for name, item in raw_items.items()},
                                   "annotations": annotations}))
        if fingerprint == previous:
            return previous
        folder = self.folder(record["id"])
        for name, (raw, info) in raw_items.items():
            suffix = Path(name).suffix
            target = folder / "sources" / (info["sha256"] + suffix)
            if not target.exists():
                with self.lock:
                    if record["stored_bytes"] + len(raw) > self.max_bytes:
                        raise ValueError("capture_storage_byte_bound_reached")
                write_bytes(target, raw)
                with self.lock:
                    record["stored_bytes"] += len(raw)
            elif target.is_symlink() or file_sha(target) != info["sha256"]:
                raise ValueError("existing_retained_source_blob_checksum_differs")
            files[name] = {**info, "file": target.relative_to(folder).as_posix(), "source_name": name}
        sync_dir(folder / "sources")
        status, camera = objects.get("status.json", {}), objects.get("camera-frame.json", {})
        frame = files.get("frame-raw.jpg")
        bound = bool(frame and camera.get("jpeg") == "frame-raw.jpg" and camera.get("jpeg_sha256") == frame["sha256"])
        stamp_valid = type(camera.get("image_stamp")) in (int, float) and math.isfinite(camera["image_stamp"])
        now = time.time()
        row = {"schema": ROW_SCHEMA, "sequence": record["samples"], "collector_wall_unix": now,
               "collector_monotonic_ns": time.monotonic_ns(), "source_files": files,
               **annotations,
               "source_available": available, "source_errors": errors, "telemetry": status,
               "camera_metadata_raw": camera, "rgbd_metadata_raw": objects.get("rgbd-cache-metadata.json"),
               "image": frame["file"] if frame else None, "image_sha256": frame["sha256"] if frame else None,
               "image_stamp": camera.get("image_stamp") if bound and stamp_valid else None,
               "source_image_stamp_valid": stamp_valid,
               "image_stamp_source": "original_ROS_header_bound_by_JPEG_SHA256" if bound else "unbound_no_timestamp_substitution",
               "same_frame_hash_verified": bound, "exposure_time_verified": False,
               "frame_reused": bool(frame and frame["sha256"] == record.get("last_frame_sha256")),
               "clock_alignment_verified": False, "source_status_at": status.get("at"),
               "source_status_monotonic": status.get("monotonic"), "source_boot_id": status.get("boot_id"),
               "status_source_age_s": now - status["at"] if type(status.get("at")) in (int, float) else None,
               "image_source_age_s": now - camera["image_stamp"] if bound and stamp_valid else None,
               "rgbd_stamp_binding_verified": False, **projection(status)}
        raw = encoded(row) + b"\n"
        with self.lock:
            if record["stored_bytes"] + len(raw) > self.max_bytes:
                raise ValueError("capture_storage_byte_bound_reached")
        with (folder / "samples.jsonl").open("ab") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        with self.lock:
            record["samples"] += 1
            record["frames"] += int(frame is not None)
            record["stored_bytes"] += len(raw)
            record["last_source_status_at"] = status.get("at")
            record["last_frame_sha256"] = frame["sha256"] if frame else None
            record["quality"]["analysis_usable"] = record["quality"].get("captured_any_source", False) or bool(files)
            record["quality"]["captured_any_source"] = record["quality"]["analysis_usable"]
        self._persist(record)
        return fingerprint

    def _work(self, record):
        started, previous = time.monotonic(), None
        try:
            while not self.stop.is_set() and time.monotonic() - started < record["requested_duration_s"]:
                previous = self._sample(record, previous)
                self.stop.wait(1 / self.sample_hz)
            with self.lock:
                interrupted = record.get("interrupt_requested", False)
                record.update(state="interrupted" if interrupted else "complete", finished_at=time.time(),
                              elapsed_s=time.monotonic() - started)
        except Exception as error:
            with self.lock:
                record.update(state="interrupted", outcome="unknown", finished_at=time.time(), error_type=type(error).__name__,
                              error=str(error) if isinstance(error, ValueError) else "capture_file_operation_failed")
                record["training_admission"]["operator_success_label"] = False
        finally:
            try:
                self._persist(record)
                self._archive(record)
            except Exception as error:
                with self.lock:
                    record.update(archive_error=type(error).__name__)
                self._persist(record)
            finally:
                with self.lock:
                    self.last, self.active = record["id"], None

    def finish(self, outcome="unknown", expected_id=None):
        if outcome not in {"success", "failure", "unknown"}:
            raise ValueError("Unknown operator outcome")
        with self.lock:
            if self.active is None:
                if self.last and expected_id in (None, self.last):
                    return self.get(self.last)
                raise ValueError("No active observation recording")
            if expected_id is not None and expected_id != self.active["id"]:
                raise ValueError("Active recording identity differs")
            ident = self.active["id"]
            if self.active["state"] == "recording":
                self.active.update(outcome=outcome, state="finishing")
                self.active["training_admission"]["operator_success_label"] = outcome == "success"
            # Outcome is an operator annotation; observation-only training admission stays false.
            self.stop.set()
            thread = self.thread
        thread.join(timeout=20)
        if thread.is_alive():
            with self.lock:
                return {**copy.deepcopy(self.active), "state": "finishing", "archive_pending": True}
        return self.get(ident)

    def get(self, ident):
        with self.lock:
            if self.active and self.active["id"] == ident:
                return copy.deepcopy(self.active)
        return strict_object((self.folder(ident) / "episode.json").read_bytes())

    def recordings(self):
        return [self.get(path.name) for path in sorted(self.root.iterdir(), key=lambda path: path.name, reverse=True)
                if IDENTITY.fullmatch(path.name) and path.is_dir() and not path.is_symlink()]

    def status(self):
        with self.lock:
            active = copy.deepcopy(self.active)
            last = self.last
        recent = sorted(self.recordings(), key=lambda value: value.get("started_at", 0), reverse=True)[:20]
        return {"active": active, "last": self.get(last) if last else None, "recent": recent,
                "format": FORMAT, "capture_power_gate": False, "actuator_commands": 0}

    def _archive(self, record):
        folder = self.folder(record["id"])
        verified_rows, verified_frames, _ = self._journal_count(folder)
        if (verified_rows, verified_frames) != (record["samples"], record["frames"]):
            raise ValueError("Recorded source bytes differ from the durable journal")
        files = {}
        for path in sorted(folder.rglob("*")):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise ValueError("Archive source contains a linked/special member")
            if path.is_file():
                files[path.relative_to(folder).as_posix()] = file_sha(path)
        fingerprint = sha(encoded(files))
        archive = self.root / (record["id"] + "-" + fingerprint + ".tar")
        if archive.exists():
            archive_sha = self._verify_archive(archive, files)
            save(self.root / (record["id"] + ".archive.json"), {"path": archive.name, "sha256": archive_sha,
                 "bytes": archive.stat().st_size, "fingerprint": fingerprint, "source_files_sha256": files,
                 "original_retained": True, "format": FORMAT})
            return
        temporary = archive.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output, tarfile.open(fileobj=output, mode="w") as bundle:
                for name, expected in files.items():
                    path = folder / name
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    with os.fdopen(descriptor, "rb") as source:
                        before = os.fstat(source.fileno())
                        if not stat.S_ISREG(before.st_mode):
                            raise ValueError("Archive source is not a regular file")
                        info = tarfile.TarInfo(name)
                        info.size, info.mode = before.st_size, 0o600
                        bundle.addfile(info, source)
                        after = os.fstat(source.fileno())
                        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or file_sha(path) != expected:
                            raise ValueError("Recording source changed during archive")
                transport = encoded({"format": FORMAT, "source_files_sha256": files, "fingerprint": fingerprint,
                                     "original_retained": True, "training_admission": False})
                info = tarfile.TarInfo("transport.json")
                info.size, info.mode = len(transport), 0o600
                bundle.addfile(info, io.BytesIO(transport))
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            archive_sha = self._verify_archive(temporary, files)
            os.rename(temporary, archive)
            sync_dir(self.root)
            save(self.root / (record["id"] + ".archive.json"), {"path": archive.name, "sha256": archive_sha,
                 "bytes": archive.stat().st_size, "fingerprint": fingerprint, "source_files_sha256": files,
                 "original_retained": True, "format": FORMAT})
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _verify_archive(path, files):
        with tarfile.open(path) as bundle:
            members = bundle.getmembers()
            if len(members) != len(files) + 1 or {item.name for item in members} != {*files, "transport.json"} or not all(item.isfile() for item in members):
                raise ValueError("Archive members differ")
            for name, expected in files.items():
                with bundle.extractfile(name) as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                        raise ValueError("Archive source bytes differ")
            with bundle.extractfile("transport.json") as stream:
                if strict_object(stream.read())["source_files_sha256"] != files:
                    raise ValueError("Archive source manifest differs")
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def archive_info(self, ident):
        record = self.get(ident)
        if record.get("state") not in {"complete", "interrupted"}:
            raise ValueError("Recording archive is not finished")
        receipt = strict_object((self.root / (ident + ".archive.json")).read_bytes())
        path = self.root / receipt["path"]
        if path.parent != self.root or path.is_symlink() or self._verify_archive(path, receipt["source_files_sha256"]) != receipt["sha256"]:
            raise ValueError("Immutable archive checksum differs")
        return {**receipt, "path": str(path)}

    def archive(self, ident):
        return Path(self.archive_info(ident)["path"])

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.active and self.active["state"] in {"recording", "finishing"}:
                self.active.update(interrupt_requested=True, outcome="unknown")
                self.active["training_admission"]["operator_success_label"] = False
            self.stop.set()
            thread = self.thread
        if thread:
            thread.join(timeout=30)
            if thread.is_alive():
                raise RuntimeError("Recorder writer is still closing; lock retained")
        os.close(self.owner)


MobileCapture = MobileCaptureRecorder
