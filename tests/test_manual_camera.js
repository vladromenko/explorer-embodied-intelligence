"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createManualCameraPreview } = require("../00-control-center/src/explorer/static/manual-camera.js");
const flush = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };
function rig(fetcher, extra = {}) {
  let now = 0, counter = 0; const pending = new Map(), frames = [], unavailable = [], handlers = {};
  const timers = {
    setTimeout(fn, delay) { const id = ++counter; pending.set(id, { fn, at: now + delay }); return id; },
    clearTimeout(id) { pending.delete(id); },
    setInterval(fn, delay) { const id = ++counter; pending.set(id, { fn, at: now + delay, repeat: delay }); return id; },
    clearInterval(id) { pending.delete(id); }
  };
  const document = { visibilityState: "visible", addEventListener(name, fn) { handlers[name] = fn; } };
  const window = { addEventListener(name, fn) { handlers[name] = fn; } };
  const preview = createManualCameraPreview({ cameraId: "nano-rgbd", fetcher, clock: () => now, timers, document, window,
    showFrame: async (blob, meta) => { assert.ok(blob.size >= 4); }, onFrame: frame => frames.push(frame), onUnavailable: value => unavailable.push(value), ...extra });
  return { preview, frames, unavailable, document, handlers, async advance(ms) {
    const target = now + ms;
    while (now < target) {
      now = Math.min(target, now + 50);
      for (const [id, entry] of [...pending]) if (entry.at <= now && pending.has(id)) {
        if (entry.repeat) entry.at += entry.repeat; else pending.delete(id);
        entry.fn();
      }
      await flush();
    }
  } };
}
function response(id = "nano-frame-1") { return { ok: true, status: 200, headers: { get(name) { return name.toLowerCase() === "content-type" ? "image/jpeg" : name.toLowerCase() === "x-frame-id" ? id : null; } }, async blob() { return new Blob([new Uint8Array([255,216,255,217])], { type: "image/jpeg" }); } }; }

test("authenticated bounded JPEG previews use browser RX time and one request at a time", async () => {
  let calls = 0, open = 0, maxOpen = 0;
  const r = rig(async (url, options) => { calls++; open++; maxOpen = Math.max(maxOpen, open); assert.match(url, /^\/api\/cameras\/nano-rgbd\/snapshot\?/); assert.equal(options.method, "GET"); assert.equal(options.credentials, "same-origin"); assert.equal(options.cache, "no-store"); open--; return response("frame-" + calls); });
  assert.equal(await r.preview.start(), true); await flush();
  await r.advance(1000);
  assert.ok(calls >= 5 && calls <= 6); assert.equal(maxOpen, 1);
  assert.equal(r.preview.ready(), true); assert.equal(r.frames.at(-1).freshness_clock, "browser_receive_monotonic_only");
  r.preview.stop(); assert.equal(r.preview.ready(), false);
});

test("duplicate source frame identities cannot renew the two-second freshness", async () => {
  const r = rig(async () => response("same-frozen-source-frame"));
  await r.preview.start(); await r.advance(2100);
  assert.equal(r.preview.ready(), false); assert.ok(r.unavailable.some(item => ["preview_stale", "source_frame_not_advancing"].includes(item.reason)));
  r.preview.stop();
});

test("timeout, stop and late GET cannot restore freshness or overlap another GET", async () => {
  let release, calls = 0;
  const r = rig(() => { calls++; return calls === 1 ? new Promise(resolve => { release = resolve; }) : Promise.resolve(response("new-frame")); });
  const initial = r.preview.start(); await r.advance(1250);
  assert.equal(await initial, false); assert.equal(r.preview.ready(), false);
  assert.equal(calls, 1); r.preview.stop("operator_left");
  const restarted = r.preview.start(); await r.advance(200); assert.equal(calls, 1);
  release(response("late-old-frame")); await flush(); await restarted;
  assert.equal(r.frames.length, 0); assert.equal(r.preview.ready(), false);
  await r.advance(50); assert.equal(calls, 2); assert.equal(r.preview.ready(), true);
  assert.equal(r.frames[0].frame_id, "new-frame"); r.preview.stop();
});

test("late decode after stop and backgrounding never restores ready state", async () => {
  let decoded; const r = rig(async () => response(), { showFrame: () => new Promise(resolve => { decoded = resolve; }) });
  const started = r.preview.start(); await flush();
  r.document.visibilityState = "hidden"; r.handlers.visibilitychange();
  decoded(); await flush(); await started;
  assert.equal(r.preview.ready(), false); assert.equal(r.frames.length, 0);
  assert.equal(r.unavailable[0].reason, "page_hidden");
});

test("HTTP/auth/decode errors and a lost visible preview notify unavailability", async () => {
  const failed = rig(async () => ({ ...response(), ok: false, status: 401 }));
  assert.equal(await failed.preview.start(), false); assert.equal(failed.unavailable[0].reason, "unauthorized"); failed.preview.stop();
  const broken = rig(async () => response(), { showFrame: async () => { throw new Error("jpeg_decode_failed"); } });
  assert.equal(await broken.preview.start(), false); assert.equal(broken.unavailable[0].reason, "jpeg_decode_failed"); broken.preview.stop();
  let visible = true; const hidden = rig(async () => response("unique"), { isVisible: () => visible });
  await hidden.preview.start(); visible = false; await hidden.advance(50);
  assert.equal(hidden.preview.ready(), false); assert.equal(hidden.unavailable[0].reason, "preview_not_visible"); hidden.preview.stop();
});
