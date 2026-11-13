"""Register Wristlab processors and optional official-rollout telemetry."""

# Importing policies registers the serialized centered-letterbox processor.
from so101_lab import policies as _policies


def _install_rollout_trace():
    """Trace the existing LeRobot loop without changing its action path.

    The worker supplies ``SO101_ROLLOUT_TRACE`` only for physical ACT runs.
    Recording compact numeric telemetry here avoids opening either camera a
    second time and adds no processor or motor command to the rollout.
    """
    import atexit
    import json
    import os
    import time

    target = os.environ.get("SO101_ROLLOUT_TRACE")
    if not target:
        return

    import numpy as np
    from lerobot.rollout.strategies.core import RolloutStrategy

    output = open(target, "w", buffering=1)
    from pathlib import Path
    frames_dir = Path(target).with_suffix(".frames")
    frames_dir.mkdir(exist_ok=True)
    counter = {"n": 0}
    original = RolloutStrategy._log_telemetry

    def traced(observation, action, runtime):
        original(observation, action, runtime)
        image = observation.get("wrist") if isinstance(observation, dict) else None
        cube = None
        if image is not None:
            pixels = np.asarray(image)
            if pixels.ndim == 3 and pixels.shape[0] == 3 and pixels.shape[-1] != 3:
                pixels = np.moveaxis(pixels, 0, -1)
            if pixels.ndim == 3 and pixels.shape[-1] == 3:
                rgb = pixels.astype(np.float32, copy=False)
                if float(rgb.max()) <= 1.5:
                    rgb = rgb * 255.0
                mask = (rgb[..., 2] > 70) & (rgb[..., 2] > rgb[..., 0] * 1.25) & (rgb[..., 2] > rgb[..., 1] * 1.15)
                ys, xs = np.nonzero(mask)
                if xs.size >= 80:
                    cube = {"x": float(xs.mean()), "y": float(ys.mean()), "area": int(xs.size)}
        state = {}
        if isinstance(observation, dict):
            state = {key: float(value) for key, value in observation.items()
                     if key.endswith(".pos") and np.isscalar(value)}
        image_summary = None
        if image is not None:
            image_summary = {"shape": list(np.asarray(image).shape),
                             "min": float(np.asarray(image).min()), "max": float(np.asarray(image).max())}
        # Diagnostic: every 10th live wrist frame (exact camera input) for offline replay.
        if image is not None and counter["n"] % 10 == 0:
            try:
                from PIL import Image as _Image
                frame = np.asarray(image)
                if frame.ndim == 3 and frame.shape[0] == 3 and frame.shape[-1] != 3:
                    frame = np.moveaxis(frame, 0, -1)
                if frame.dtype != np.uint8:
                    frame = (np.clip(frame, 0, 1) * 255).astype(np.uint8) if float(frame.max()) <= 1.5 else frame.astype(np.uint8)
                _Image.fromarray(frame).save(frames_dir / f"{counter['n']:05d}.png")
            except Exception:
                pass
        counter["n"] += 1
        record = {"monotonic_ns": time.monotonic_ns(), "state": state,
                  "action": action, "blue_cube": cube, "image": image_summary}
        output.write(json.dumps(record, separators=(",", ":")) + "\n")

    RolloutStrategy._log_telemetry = staticmethod(traced)
    atexit.register(output.close)


_install_rollout_trace()
