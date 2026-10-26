"use strict";
// ENGINEERING_GLUE: bounded actual JPEG preview; no control or actuator requests.
(function (surface) {
  function createManualCameraPreview(options) {
    const cameraId = options.cameraId || "nano-rgbd";
    if (cameraId !== "nano-rgbd") throw new Error("Manual ROSMASTER preview requires nano-rgbd.");
    const fetcher = options.fetcher || surface.fetch.bind(surface);
    const clock = options.clock || (() => surface.performance.now());
    const timers = options.timers || surface;
    const doc = options.document || surface.document;
    const win = options.window || surface;
    const periodMs = 200, staleMs = 2000, timeoutMs = 1200;
    let active = false, epoch = 0, timer = null, inflight = null;
    let lastAttempt = -Infinity, lastUniqueRx = null, lastFrameId = null;
    let unavailableNotified = false, reason = "not_started", displayedUrl = null;
    const urls = options.urls || surface.URL;
    const onUnavailable = options.onUnavailable || (() => {});
    const onFrame = options.onFrame || (() => {});
    const showFrame = options.showFrame || (async (blob, metadata) => {
      if (!options.image) throw new Error("A preview image or frame renderer is required.");
      const url = urls.createObjectURL(blob);
      metadata.objectUrl = url;
      options.image.src = url;
      await options.image.decode();
    });
    function visible() {
      if (options.isVisible) return options.isVisible() === true;
      if (!options.image) return true;
      return options.image.isConnected !== false && options.image.hidden !== true;
    }
    function ready() {
      return visible() && active && lastUniqueRx !== null && clock() - lastUniqueRx >= 0 && clock() - lastUniqueRx <= staleMs;
    }
    function state() {
      return { camera_id: cameraId, active, request_inflight: inflight !== null, ready: ready(),
        frame_id: lastFrameId, last_unique_received_browser_monotonic_ms: lastUniqueRx,
        age_ms: lastUniqueRx === null ? null : Math.max(0, clock() - lastUniqueRx),
        freshness_clock: "browser_receive_monotonic_only", reason, max_stale_ms: staleMs, preview_period_ms: periodMs };
    }
    function unavailable(why, cancel = true) {
      reason = why; lastUniqueRx = null;
      if (cancel && inflight) { inflight.cancelled = true; inflight.abort.abort(); inflight.finish(false); }
      if (!unavailableNotified) {
        unavailableNotified = true;
        onUnavailable({ reason: why, camera_id: cameraId, state: state() });
      }
    }
    function valid(entry) { return active && entry.epoch === epoch && !entry.cancelled; }
    function requestFrame() {
      if (!active) return Promise.resolve(false);
      if (inflight) return inflight.result;
      lastAttempt = clock();
      let settle;
      const entry = { epoch, abort: new AbortController(), cancelled: false, result: new Promise(resolve => { settle = resolve; }) };
      let settled = false;
      entry.finish = value => { if (!settled) { settled = true; settle(value); } };
      inflight = entry;
      const timeout = timers.setTimeout(() => {
        if (inflight === entry && valid(entry)) {
          entry.cancelled = true; entry.abort.abort(); unavailable("snapshot_timeout", false); entry.finish(false);
        }
      }, timeoutMs);
      (async () => {
        let frameUrl = null;
        try {
          const response = await fetcher(`/api/cameras/${encodeURIComponent(cameraId)}/snapshot?preview_epoch=${entry.epoch}&rx=${encodeURIComponent(String(lastAttempt))}`, {
            method: "GET", credentials: "same-origin", cache: "no-store", signal: entry.abort.signal
          });
          if (!valid(entry)) return;
          if (!response.ok) throw new Error(response.status === 401 ? "unauthorized" : "snapshot_http_" + response.status);
          if (!String(response.headers.get("content-type") || "").toLowerCase().startsWith("image/jpeg")) throw new Error("snapshot_not_jpeg");
          const frameId = response.headers.get("x-frame-id");
          if (!frameId) throw new Error("snapshot_frame_identity_missing");
          const blob = await response.blob();
          const received = clock();
          if (!valid(entry)) return;
          if (!blob || blob.size < 4 || blob.size > 10 * 1024 * 1024) throw new Error("snapshot_size_invalid");
          const metadata = { camera_id: cameraId, frame_id: frameId, received_browser_monotonic_ms: received,
            freshness_clock: "browser_receive_monotonic_only", new_source_frame: frameId !== lastFrameId };
          await showFrame(blob, metadata);
          frameUrl = metadata.objectUrl || null;
          if (!valid(entry)) return;
          if (frameUrl) { if (displayedUrl && displayedUrl !== frameUrl) urls.revokeObjectURL(displayedUrl); displayedUrl = frameUrl; frameUrl = null; }
          if (options.image) options.image.hidden = false;
          if (metadata.new_source_frame) { lastFrameId = frameId; lastUniqueRx = received; }
          if (!ready()) { unavailable("source_frame_not_advancing", false); entry.finish(false); return; }
          reason = null; unavailableNotified = false;
          if (options.image) options.image.hidden = false;
          onFrame({ ...metadata, ready: true, age_ms: Math.max(0, clock() - lastUniqueRx), preview_label: "JPEG кадры ROSMASTER · до 5 кадров/с" });
          entry.finish(true);
        } catch (error) {
          if (valid(entry)) unavailable(error.message || "snapshot_unavailable", false);
        } finally {
          timers.clearTimeout(timeout);
          if (frameUrl) urls.revokeObjectURL(frameUrl);
          entry.finish(false);
          // Keep an aborted underlying GET in this slot until it actually settles.
          // A late response cannot overlap a second request or restore freshness.
          if (inflight === entry) inflight = null;
        }
      })();
      return entry.result;
    }
    function tick() {
      if (!active) return;
      if (doc && doc.visibilityState === "hidden") { stop("page_hidden"); return; }
      if (lastUniqueRx !== null && !visible()) { unavailable("preview_not_visible"); return; }
      if (lastUniqueRx !== null && clock() - lastUniqueRx > staleMs) unavailable("preview_stale");
      if (!inflight && clock() - lastAttempt >= periodMs) requestFrame();
    }
    function start() {
      if (active) return requestFrame();
      if (doc && doc.visibilityState === "hidden") { unavailable("page_hidden"); return Promise.resolve(false); }
      active = true; epoch += 1; lastUniqueRx = null; unavailableNotified = false; reason = "waiting_for_frame";
      timer = timers.setInterval(tick, 50);
      return requestFrame();
    }
    function stop(why = "preview_stopped") {
      active = false; epoch += 1;
      if (timer !== null) timers.clearInterval(timer);
      timer = null; unavailable(why);
      if (displayedUrl) { urls.revokeObjectURL(displayedUrl); displayedUrl = null; }
    }
    async function ensureFresh() {
      if (ready()) return true;
      if (!active) await start();
      else await requestFrame();
      if (active && !ready() && !inflight) await requestFrame();
      return ready();
    }
    if (doc && doc.addEventListener) doc.addEventListener("visibilitychange", () => { if (doc.visibilityState === "hidden") stop("page_hidden"); });
    if (win && win.addEventListener) {
      win.addEventListener("pagehide", () => stop("page_hidden"));
      win.addEventListener("blur", () => stop("window_blur"));
    }
    return { start, stop, ready, ensureFresh, state };
  }
  surface.createManualCameraPreview = createManualCameraPreview;
  if (typeof module !== "undefined" && module.exports) module.exports = { createManualCameraPreview };
})(typeof window !== "undefined" ? window : globalThis);
