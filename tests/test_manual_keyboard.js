"use strict";
// Only in-memory transport mocks. No hardware or network requests.
const test = require("node:test");
const assert = require("node:assert/strict");
const { ManualKeyboard } = require("../00-control-center/src/explorer/static/manual-keyboard.js");
const flush = async () => { for (let n = 0; n < 20; n++) await Promise.resolve(); };
function rig() {
  const state = { ready: true, baseForbidden: false }, calls = [];
  const keyboard = new ManualKeyboard({ ready: () => state.ready,
    filterKeys: keys => state.baseForbidden ? keys.filter(key => !["KeyW", "KeyS", "KeyA", "KeyD", "KeyQ", "KeyE"].includes(key)) : keys,
    post: async (url, body) => { calls.push({ operation: url.split("/").at(-1), body: structuredClone(body) }); return {}; } });
  return { keyboard, state, calls };
}
test("neutral input precedes native resume; one packet retains simultaneous base and arm", async () => {
  const r = rig(); await r.keyboard.start();
  assert.deepEqual(r.calls.map(call => call.operation), ["select", "input", "resume"]);
  assert.deepEqual(r.calls[1].body.keys, []);
  r.keyboard.keys = new Set(["KeyW", "KeyI", "KeyM"]); await r.keyboard.send();
  assert.deepEqual(r.calls.at(-1).body, { source: "keyboard", keys: ["KeyI", "KeyM", "KeyW"], observing: true, arm_mode: "cartesian" });
  r.state.baseForbidden = true; await r.keyboard.send();
  assert.deepEqual(r.calls.at(-1).body.keys, ["KeyI", "KeyM"]);
});
test("camera loss sends neutral and STOP and disconnect, recovery cannot resume", async () => {
  const r = rig(); await r.keyboard.start(); r.calls.length = 0;
  r.state.ready = false; r.keyboard.keys.add("KeyI"); await r.keyboard.send();
  assert.deepEqual(r.calls.map(call => call.operation), ["input", "stop", "disconnect"]);
  assert.deepEqual(r.calls[0].body.keys, []); assert.equal(r.keyboard.active, false);
  r.state.ready = true; r.calls.length = 0; await r.keyboard.send(); assert.equal(r.calls.length, 0);
});
test("a delayed select cannot resume after blur and cleanup is repeated after its completion", async () => {
  const r = rig(); let finish;
  const post = r.keyboard.options.post;
  r.keyboard.options.post = async (url, body) => { await post(url, body); if (url.endsWith("/select")) await new Promise(resolve => { finish = resolve; }); return {}; };
  const started = r.keyboard.start(); const rejected = assert.rejects(started, /прервано/); await flush();
  const stopped = r.keyboard.stop(true); await flush();
  assert.equal(r.calls.filter(call => call.operation === "stop").length, 1);
  await assert.rejects(r.keyboard.start(), /завершается/);
  finish(); await stopped; await rejected;
  assert.equal(r.calls.some(call => call.operation === "resume"), false);
  assert.ok(r.calls.filter(call => call.operation === "stop").length >= 2);
  assert.equal(r.keyboard.active, false);
});
test("a late resume response is followed by STOP, cannot reactivate the browser", async () => {
  const r = rig(); let finish; const post = r.keyboard.options.post;
  r.keyboard.options.post = async (url, body) => { await post(url, body); if (url.endsWith("/resume")) await new Promise(resolve => { finish = resolve; }); return {}; };
  const started = r.keyboard.start(); const rejected = assert.rejects(started, /прервано/); await flush();
  const stopped = r.keyboard.stop(true); await flush(); finish(); await stopped; await rejected;
  assert.equal(r.keyboard.active, false); assert.ok(r.calls.findLastIndex(call => call.operation === "stop") > r.calls.findIndex(call => call.operation === "resume"));
});
test("held movement blocks initial acquisition and explicit resume after STOP", async () => {
  const r = rig(); r.keyboard.heldKeys.add("KeyI"); await assert.rejects(r.keyboard.start(), /отпустите/);
  assert.equal(r.calls.some(call => call.operation === "select"), false);
  r.keyboard.heldKeys.clear(); await r.keyboard.start(); await r.keyboard.stop(false);
  r.keyboard.heldKeys.add("KeyW"); await assert.rejects(r.keyboard.resume(), /Отпустите/);
  r.keyboard.heldKeys.clear(); await r.keyboard.resume(); assert.equal(r.keyboard.paused, false);
});
test("mode and speed use neutral input and the native executor without controller settings", async () => {
  const r = rig(); await r.keyboard.start(); r.calls.length = 0; r.keyboard.keys.add("KeyI");
  await r.keyboard.configure("mode", "joint"); await r.keyboard.configure("speed", "fast");
  assert.deepEqual(r.calls.map(call => call.operation), ["input", "mode", "input", "speed"]);
  assert.deepEqual(r.calls[3].body, { source: "keyboard", speed: "fast" });
  assert.deepEqual(r.calls[0].body.keys, []);
});
test("an uncertain STOP blocks acquisition until an explicit successful cleanup", async () => {
  const r = rig(); await r.keyboard.start(); const post = r.keyboard.options.post;
  r.keyboard.options.post = async (url, body) => { if (url.endsWith("/stop")) throw new Error("mock unavailable"); return post(url, body); };
  await r.keyboard.stop(true); await assert.rejects(r.keyboard.start(), /не подтверждено/);
  r.keyboard.options.post = post; await r.keyboard.start(); assert.equal(r.keyboard.active, true);
});
