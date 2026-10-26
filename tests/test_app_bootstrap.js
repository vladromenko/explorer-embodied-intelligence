"use strict";
// All DOM, clocks and requests are local software fakes. No URL is fetched.
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const STATIC = path.resolve(__dirname, "../00-control-center/src/explorer/static");
const controls = require("./fixtures/explorer-manual-v2-controls.json");
const flush = async () => { for (let n = 0; n < 100; n++) await Promise.resolve(); };
class Element {
  constructor(tag = "DIV", document = null) {
    this.tagName = tag.toUpperCase(); this.document = document; this.parentNode = null; this.nodes = new Map();
    this.listeners = new Map(); this.dataset = {}; this.attributes = {}; this.hidden = false; this.open = false;
    this.style = {}; this.classList = { add() {}, remove() {}, toggle() {} };
  }
  set innerHTML(markup) {
    this.html = markup; this.nodes.clear();
    for (const match of markup.matchAll(/<([a-z][\w-]*)[^>]*\bid="([^"]+)"[^>]*>/gi)) {
      const child = new Element(match[1], this.document); child.id = match[2]; child.parentNode = this;
      if (/\bhidden\b/.test(match[0])) child.hidden = true;
      for (const data of match[0].matchAll(/data-([\w-]+)="([^"]*)"/g)) child.dataset[data[1].replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = data[2];
      this.nodes.set("#" + child.id, child);
    }
  }
  get innerHTML() { return this.html || ""; }
  get isConnected() { return this.connected || !!this.parentNode?.isConnected; }
  querySelector(selector) { return this.nodes.get(selector) || null; }
  querySelectorAll() { return []; }
  append(child) { child.remove(); child.parentNode = this; this.nodes.set("#" + (child.id || "anonymous"), child); }
  remove() { if (this.parentNode) for (const [key, value] of this.parentNode.nodes) if (value === this) this.parentNode.nodes.delete(key); this.parentNode = null; }
  contains(target) { for (let item = target; item; item = item.parentNode) if (item === this) return true; return false; }
  addEventListener(name, callback) { const list = this.listeners.get(name) || []; list.push(callback); this.listeners.set(name, list); }
  removeEventListener(name, callback) { this.listeners.set(name,(this.listeners.get(name) || []).filter(item => item !== callback)); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return name === "src" ? this.src || null : this.attributes[name] || null; }
  removeAttribute(name) { delete this.attributes[name]; if (name === "src") delete this.src; }
  focus() { if (this.document) this.document.activeElement = this; }
  closest() { return null; }
  matches() { return false; }
  decode() { return Promise.resolve(); }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
function bootstrap(route) {
  const index = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
  const doc = new Element("DOCUMENT"); doc.document = doc; doc.connected = true; doc.cookie = "explorer_csrf=software-fixture-token";
  doc.hidden = false; doc.visibilityState = "visible"; doc.hasFocus = () => true; doc.createElement = tag => new Element(tag, doc);
  doc.innerHTML = index; doc.activeElement = null;
  const requests = [], events = new Map(), intervals = [], timeouts = [], frames = [], assignments = [];
  const state = { nodes: [], cameras: [], jobs: [], skills: [], experiments: [], research: {}, admission: {} };
  const device = { index: 0, id: "IN-MEMORY STANDARD GAMEPAD", connected: true, mapping: "standard", axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ value: 0, pressed: false })) };
  const sandbox = { document: doc, navigator: { getGamepads: () => [device] }, isSecureContext: true,
    location: { pathname: route, assign(url) { assignments.push(url); } }, history: { pushState(_state, _title, url) { sandbox.location.pathname = url; } },
    performance: { now: () => 1000 }, queueMicrotask, AbortController, Blob, URL: { createObjectURL: () => "blob:software-fixture", revokeObjectURL() {} },
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; }, clearInterval() {},
    setTimeout: (fn, ms) => { timeouts.push({ fn, ms }); return timeouts.length; }, clearTimeout() {},
    requestAnimationFrame: fn => frames.push(fn), scrollTo() {},
    addEventListener(name, fn) { const list = events.get(name) || []; list.push(fn); events.set(name, list); },
    fetch: async (url, options = {}) => {
      requests.push({ url, method: options.method || "GET", options });
      if (options.method && options.method !== "GET") throw new Error("No real or implicit POST permitted in bootstrap qualification");
      const wizard = { sources: [], reviews: [], tasks: [], task: { setup: "Software empty-source fixture", phases: [] },
        counts: { original_episodes: 0, approved_demonstrations: 0, reviewed_failures: 0, reviewed_unknown: 0, unreviewed_sources: 0 },
        recommendation: { reason: "No actual teaching sources in fixture" }, coverage: { scenes: 0, sessions: 0, missing: [] },
        evaluation_trend: { successes: 0, attempts: 0 }, controls: {}, invalid_sources: [], stale_reviews: [] };
      const data = url === "/api/state" ? state : url.startsWith("/api/demonstration-wizard?") ? wizard : url.endsWith("/controls") ? controls : url.endsWith("/teleop") ? { source: "none" } : {};
      if (url.startsWith("/api/cameras/")) return { ok: false, status: 503, headers: { get: () => null } };
      return { ok: true, status: 200, json: async () => structuredClone(data) };
    }
  };
  sandbox.window = sandbox; sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  const scripts = [...index.matchAll(/<script\s+src="\/static\/([^"]+)"\s+defer><\/script>/g)].map(match => match[1]);
  for (const script of scripts) vm.runInContext(fs.readFileSync(path.join(STATIC, script), "utf8"), context, { filename: script });
  return { context, scripts, requests, document: doc, events, intervals, timeouts, frames, assignments,
    run: source => vm.runInContext(source, context) };
}

for (const route of ["/", "/experiments/01", "/experiments/02", "/experiments/03", "/experiments/04", "/experiments/05", "/experiments/06", "/learning/so101", "/evening"]) {
  test(`actual deferred browser module graph bootstraps ${route} without implicit control requests`, async () => {
    const rig = bootstrap(route); await flush();
    assert.equal(rig.run("model.error"), null);
    assert.equal(rig.run("typeof ManualKeyboard"), "function");
    assert.equal(rig.run("typeof createManualCameraPreview"), "function");
    assert.equal(rig.run("typeof ExplorerBrowserGamepad.install"), "function");
    assert.ok(rig.scripts.indexOf("manual-camera.js") < rig.scripts.indexOf("app.js"));
    assert.ok(rig.scripts.indexOf("manual-keyboard.js") < rig.scripts.indexOf("app.js"));
    assert.ok(rig.requests.some(item => item.url === "/api/state"));
    assert.ok(rig.requests.every(item => item.method === "GET"));
    assert.ok(rig.document.querySelector("#main").innerHTML.length > 100);
    if (route === "/experiments/02") {
      assert.equal(rig.run("currentCamera"), "nano-rgbd");
      assert.equal(rig.run("rosmasterCameraReady()"), false);
      assert.equal(rig.run("keyboard.active"), false);
      assert.equal(rig.run("browserGamepad.state"), "disconnected");
      assert.ok(rig.requests.some(item => item.url.startsWith("/api/cameras/nano-rgbd/snapshot")));
      assert.ok(!rig.requests.some(item => item.url.includes("/stream")));
    }
  });
}
async function pump(rig, rounds = 40) {
  for (let round = 0; round < rounds; round++) {
    await flush();
    for (const timer of rig.timeouts) if (timer.ms === 10 && !timer.fired) { timer.fired = true; timer.fn(); }
  }
  await flush();
}
async function controlled() {
  const rig = bootstrap("/experiments/02"); await flush();
  rig.calls = []; rig.hooks = new Map();
  const original = rig.context.fetch;
  rig.context.fetch = async (url, options = {}) => {
    if(url==="/api/robot/explorer/retarget/activation/status")return {ok:true,status:200,json:async()=>({execution_permission:rig.permissionDenied!==true,authorization_id:"explicit-software-fixture-grant",input_mode:rig.run('manualInputOwner?.owner === "gamepad" ? "browser_gamepad" : "keyboard"'),chassis_motion_allowed:false})};
    if (options.method === "POST") {
      const call = { url, operation: url.split("/").at(-1), body: JSON.parse(options.body || "{}"), options };
      rig.calls.push(call);
      const hook = rig.hooks.get(url); if (hook) await hook(call);
      return { ok: true, status: 200, json: async () => ({}) };
    }
    const hook = rig.hooks.get(url); if (hook) await hook({ url, options });
    return original(url, options);
  };
  rig.run("manualPreview.ready=()=>true;manualPreview.ensureFresh=async()=>true");
  await rig.run("browserGamepad.select(0)");
  return rig;
}
test("missing observed permission blocks the actual browser graph before native select or resume",async()=>{
  const rig=await controlled();rig.permissionDenied=true;
  assert.equal((await started(rig,"keyboard")).status,"rejected");
  assert.ok(!rig.calls.some(row=>["select","resume"].includes(row.operation)));
});
function started(rig, name) {
  return rig.run(name === "keyboard" ? "keyboard.start()" : "browserGamepad.connect()").then(
    () => ({ status: "fulfilled" }), error => ({ status: "rejected", error }));
}
function active(rig) { return JSON.parse(rig.run('JSON.stringify({keyboard:keyboard.active,gamepad:browserGamepad.state,busy:keyboard.busy||browserGamepad.busy})')); }
for (const [first, winner] of [["keyboard", "gamepad"], ["gamepad", "keyboard"]]) {
  test(`concurrent ${first} then ${winner} acquisition leaves only the last source active`, async () => {
    const rig = await controlled(); const one = started(rig, first), two = started(rig, winner);
    await pump(rig); const results = await Promise.all([one, two]);
    assert.equal(results[0].status, "rejected"); assert.equal(results[1].status, "fulfilled");
    assert.deepEqual(active(rig), { keyboard: winner === "keyboard", gamepad: winner === "gamepad" ? "active" : "disconnected", busy: false });
    assert.equal(rig.calls.filter(call => call.operation === "resume").length, 1);
    assert.ok(rig.calls.every(call => call.options.headers["X-Explorer-CSRF"] === "software-fixture-token"));
    if (winner === "keyboard") await rig.run("keyboard.stop(true)"); else await rig.run("browserGamepad.disconnect('software cleanup')");
  });
}
for (const source of ["keyboard", "gamepad"]) for (const interruption of ["blur", "STOP"]) {
  test(`${interruption} during ${source} delayed native resume prevents late reactivation`, async () => {
    const rig = await controlled(); let release;
    rig.hooks.set("/api/robot/teleop/resume", () => new Promise(resolve => { release = resolve; }));
    const connect = started(rig, source); await pump(rig);
    assert.equal(typeof release, "function");
    let stopped;
    if (interruption === "blur") {
      rig.document.hasFocus = () => false;
      for (const listener of rig.events.get("blur") || []) listener();
      stopped = Promise.resolve();
    } else stopped = rig.document.querySelector("#stop-button").listeners.get("click")[0]();
    await pump(rig);
    assert.ok(rig.calls.some(call => call.operation === "stop"));
    release(); await pump(rig); await stopped;
    assert.equal((await connect).status, "rejected");
    assert.equal(active(rig).keyboard, false); assert.equal(active(rig).gamepad, "disconnected");
    const resume = rig.calls.findIndex(call => call.operation === "resume");
    assert.ok(rig.calls.findLastIndex(call => call.operation === "stop") > resume);
    rig.document.hasFocus = () => true; await pump(rig);
    assert.equal(rig.calls.filter(call => call.operation === "resume").length, 1, "focus recovery never resumes");
  });
}
test("logout drains late acquisition cleanup before deleting session and freezes new claims", async () => {
  const rig = await controlled(); let release;
  rig.hooks.set("/api/robot/teleop/select", () => new Promise(resolve => { release = resolve; }));
  const connect = started(rig, "keyboard"); await pump(rig); assert.equal(typeof release, "function");
  const logout = rig.document.querySelector("#logout-button").listeners.get("click")[0](); await pump(rig);
  assert.ok(!rig.calls.some(call => call.url === "/api/logout"));
  assert.equal((await started(rig, "gamepad")).status, "rejected");
  release(); await pump(rig); await logout;
  assert.equal((await connect).status, "rejected");
  assert.equal(rig.calls.at(-1).url, "/api/logout");
  assert.deepEqual(rig.assignments, ["/api/login"]);
  assert.ok(!rig.calls.some(call => call.operation === "resume"));
  assert.equal(active(rig).keyboard, false); assert.equal(active(rig).gamepad, "disconnected");
});
test("uncertain native STOP preserves the login instead of discarding cleanup credentials", async () => {
  const rig = await controlled(); assert.equal((await started(rig, "keyboard")).status, "fulfilled");
  rig.hooks.set("/api/robot/teleop/stop", () => { throw new Error("isolated mock STOP unavailable"); });
  await rig.document.querySelector("#logout-button").listeners.get("click")[0](); await pump(rig);
  assert.ok(!rig.calls.some(call => call.url === "/api/logout")); assert.deepEqual(rig.assignments, []);
  assert.equal(rig.run("keyboard.stopUnconfirmed"), true);
  assert.match(rig.document.querySelector("#toast").textContent, /STOP/);
});
module.exports = { bootstrap, flush, pump, controlled };

test("delayed actual native controls GET cannot let a superseded gamepad acquire after keyboard", async () => {
  const rig = await controlled(); let release, signal;
  rig.hooks.set("/api/robot/teleop/controls", call => { signal = call.options.signal; return new Promise(resolve => { release = resolve; }); });
  const old = started(rig, "gamepad"); await pump(rig); assert.equal(typeof release, "function");
  assert.equal(typeof signal?.addEventListener, "function", "actual preparation endpoint has bounded abort signal");
  const current = started(rig, "keyboard"); await pump(rig);
  assert.equal(rig.calls.filter(call => call.operation === "resume").length, 0);
  release(); await pump(rig);
  assert.equal((await old).status, "rejected"); assert.equal((await current).status, "fulfilled");
  assert.deepEqual(active(rig), { keyboard: true, gamepad: "disconnected", busy: false });
  assert.equal(rig.calls.filter(call => call.operation === "resume").length, 1);
  await rig.run("keyboard.stop(true)");
});

test("blur during actual controls GET rejects late preparation without select or resume", async () => {
  const rig = await controlled(); let release;
  rig.hooks.set("/api/robot/teleop/controls", () => new Promise(resolve => { release = resolve; }));
  const connecting = started(rig, "gamepad"); await pump(rig); assert.equal(typeof release, "function");
  rig.document.hasFocus = () => false; for (const listener of rig.events.get("blur") || []) listener();
  await pump(rig); release(); await pump(rig);
  assert.equal((await connecting).status, "rejected");
  assert.ok(!rig.calls.some(call => ["select", "resume"].includes(call.operation)));
  assert.equal(active(rig).gamepad, "disconnected");
});
