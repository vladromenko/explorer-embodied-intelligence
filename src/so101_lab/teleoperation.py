"""Explicit operator-controlled leader/follower check without a dataset.

One HardwareSource owns the bus and cameras. Recording takes ownership only
after this session has stopped and cleanup has succeeded; no motion is retried.
"""
from __future__ import annotations

import io
import threading
import time
import traceback
from pathlib import Path

from .hardware import HardwareSource, hardware_paths, hardware_settings
from .jobs import atomic
from .resources import ROOT, assert_hardware_available


class TeleoperationManager:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.source = None
        self.images = {}
        self.current = {"state": "idle", "active": False, "hardware_open": False,
                        "recording": False, "samples": 0}
        self._update()

    def _update(self, **values):
        with self.lock:
            self.current.update(values, updated_at=time.time())
            target = self.root / "runtime/teleoperation.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic(target, self.current)

    def status(self):
        with self.lock:
            result = dict(self.current)
            if result.get("hardware_open") and self.source:
                result["cameras"] = {name: {"connected": not camera.error,
                    "error": camera.error, "observed_fps": camera.info.get("fps_observed_recent")}
                    for name, camera in self.source.cameras.items()}
            return result

    def start(self, *, explicit_user_start=False, settings=None):
        if not explicit_user_start:
            raise PermissionError("Телеоперация включается только кнопкой оператора в UI")
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("Телеоперация уже включена; сначала нажмите STOP")
            if self.current.get("hardware_stop_state") == "torque_disable_unconfirmed_operator_check_required":
                raise RuntimeError("Снятие torque не подтверждено. Проверьте аппаратный STOP перед новым запуском")
            cfg = hardware_settings(settings)
            assert_hardware_available(hardware_paths(cfg))
            source = HardwareSource(cfg, teleoperate=True)
            self.source = source
            self.stop_event.clear()
            self.images = {}
            self.current = {"state": "starting", "active": True, "hardware_open": False,
                            "recording": False, "samples": 0, "settings": cfg,
                            "message": "Подключаем руки. Запись датасета выключена"}
            self._update()
            self.thread = threading.Thread(target=self._run, args=(source, cfg),
                name="so101-ai-teleoperation", daemon=True)
            self.thread.start()
            return self.status()

    def stop(self):
        with self.lock:
            if self.current["active"]:
                self.stop_event.set()
                self._update(state="stopping", message="Останавливаем следование и отключаем torque")
            return self.status()

    def wait(self, timeout=15):
        thread = self.thread
        if thread:
            thread.join(timeout)
        return not thread or not thread.is_alive()

    def stop_for_recording(self):
        self.stop()
        if not self.wait():
            raise RuntimeError("Телеоперация ещё останавливается. Дождитесь STOP перед записью")
        current = self.status()
        if current.get("hardware_stop_state") == "torque_disable_unconfirmed_operator_check_required":
            raise RuntimeError("Снятие torque не подтверждено. Запись не запущена; проверьте аппаратный STOP")

    def latest_frame(self, camera):
        with self.lock:
            image = self.images.get(camera)
            active = self.current["active"]
        if not active or image is None:
            return None
        if time.monotonic_ns() - image["timestamp_ns"] > 1_000_000_000:
            raise RuntimeError("Нет свежего кадра телеоперации: " + camera)
        from PIL import Image
        output = io.BytesIO()
        Image.fromarray(image["image"]).save(output, format="JPEG", quality=78)
        return output.getvalue()

    def _run(self, source, cfg):
        samples = 0
        started = time.monotonic()
        last_persist = started
        state = "stopped"
        error = None
        trace = None
        stop_errors = []
        try:
            provenance = source.start(explicit_user_start=True, stop=self.stop_event.is_set)
            self._update(state="teleoperating", hardware_open=True, provenance=provenance,
                         message="Follower следует за leader. Данные не записываются")
            while not self.stop_event.is_set():
                before = time.monotonic()
                sample = source.sample(samples + 1, stop=self.stop_event.is_set)
                samples += 1
                with self.lock:
                    self.images = sample["images"]
                    self.current.update(samples=samples, measured_joints=sample["observation.state"],
                        sent_action=sample["action"], last_sample_ns=sample["timestamp_ns"])
                if time.monotonic() - last_persist >= 1:
                    self._update(observed_hz=samples / (time.monotonic() - started))
                    last_persist = time.monotonic()
                # No catch-up commands after a slow USB read.
                elapsed = time.monotonic() - before
                self.stop_event.wait(1 / cfg["fps"] - elapsed if elapsed < 1 / cfg["fps"] else 1 / cfg["fps"])
        except Exception as exc:
            if not (isinstance(exc, InterruptedError) and self.stop_event.is_set()):
                state, error = "failed", f"{type(exc).__name__}: {exc}"
                trace = traceback.format_exc()
        finally:
            try:
                stop_errors = source.close()
            except Exception as exc:
                stop_errors = [str(exc)]
            if stop_errors:
                state, error = "failed", "Снятие torque не подтверждено; проверьте аппаратный STOP. " + str(stop_errors)
            if trace:
                log = self.root / "logs/teleoperation-errors.log"
                try:
                    log.parent.mkdir(parents=True, exist_ok=True)
                    with log.open("a") as out:
                        out.write(trace + "\n")
                except OSError:
                    pass  # Cleanup has already run; retain the error in memory/status.
            with self.lock:
                self.images = {}
            self._update(state=state, active=False, hardware_open=False, error=error,
                samples=samples, stop_errors=stop_errors,
                hardware_stop_state="torque_disable_unconfirmed_operator_check_required" if stop_errors else "devices_closed",
                message="Телеоперация остановлена. Датасет не создавался" if not error else error)
