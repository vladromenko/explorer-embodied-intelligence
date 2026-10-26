// Every request below uses an in-memory mock. No URL is ever fetched.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const api = require('../../00-control-center/src/explorer/static/gamepad.js');
const controlsFixture = require('../fixtures/explorer-manual-v2-controls.json');
const tests = [];
const test = (name, callback) => tests.push({ name, callback });
function pad(index = 0, id = 'SOFTWARE STANDARD GAMEPAD FIXTURE') {
  return { index, id, mapping: 'standard', connected: true, axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ value: 0, pressed: false })) };
}
function press(pad, index, value = 1) { pad.buttons[index] = { value, pressed: value > .5 }; }
function fixture() {
  const device = pad(), state = { pads: [device], visible: true, focus: true, camera: true, route: true, now: 0 }, calls = [];
  const options = { secure: true, getGamepads: () => state.pads, visible: () => state.visible, focused: () => state.focus,
    routeActive: () => state.route, cameraReady: () => state.camera, now: () => state.now,
    controls: async () => structuredClone(controlsFixture),
    nativeState: () => ({ arm_mode: 'cartesian', arm_speed_selected: 'normal' }),
    post: async (url, body) => { calls.push({ url, body: structuredClone(body) }); return {}; } };
  return { device, state, calls, options, controller: new api.Controller(options) };
}
async function connected(value) { await value.controller.select(0); await value.controller.connect(); value.calls.length = 0; }
function operations(calls) { return calls.map((call) => call.url.split('/').at(-1)); }

test('standard controls map only into the audited native key directions', () => {
  const device = pad(); device.axes = [-.8, -.8, .7, -.6];
  for (const index of [3, 4, 6, 2, 12, 15]) press(device, index);
  const frame = api.snapshot(device);
  assert.deepEqual(api.directionKeys(frame, 'cartesian'), ['KeyA', 'KeyI', 'KeyL', 'KeyN', 'KeyQ', 'KeyU', 'KeyW', 'ShiftLeft'].sort());
  assert.deepEqual(api.directionKeys(frame, 'joint'), ['KeyA', 'KeyI', 'KeyL', 'KeyN', 'KeyQ', 'KeyU', 'KeyW', 'KeyY', 'Period', 'ShiftLeft'].sort());
  press(device, 7); press(device, 5); press(device, 0);
  const both = api.directionKeys(api.snapshot(device), 'joint');
  assert(!both.includes('KeyN') && !both.includes('KeyM') && !both.includes('KeyQ') && !both.includes('KeyE') && !both.includes('KeyU') && !both.includes('KeyO'));
});

test('connect sends real neutral before resume and uses no guessed gamepad wire schema', async () => {
  const f = fixture(); await f.controller.select(0); await f.controller.connect();
  assert.deepEqual(operations(f.calls), ['select', 'input', 'resume']);
  assert.deepEqual(f.calls[1].body, { source: 'keyboard', keys: [], observing: true, arm_mode: 'cartesian' });
  assert(f.calls.every((call) => call.body.source === 'keyboard'));
  assert.equal(f.controller.inputProvenance().axes_encoding, 'digitized_directions_native_speed_and_smoothing');
});

test('held axes or malformed/unmapped controllers cannot acquire ownership', async () => {
  const f = fixture(); f.device.axes[0] = .7; await f.controller.select(0);
  await assert.rejects(f.controller.connect(), /отпустите/); assert.equal(f.calls.length, 0);
  f.device.axes[0] = .1; assert(api.snapshot(f.device).neutral);
  f.device.mapping = ''; await f.controller.select(0);
  await assert.rejects(f.controller.connect(), /стандартной/); assert.equal(f.calls.length, 0);
});

test('faults always neutral STOP disconnect and never resume on recovery', async () => {
  for (const kind of ['camera', 'focus', 'visible', 'route', 'device']) {
    const f = fixture(); await connected(f);
    if (kind === 'device') f.state.pads = []; else f.state[kind] = false;
    await f.controller.poll();
    assert.deepEqual(operations(f.calls), ['input', 'stop', 'disconnect'], kind);
    assert.deepEqual(f.calls[0].body.keys, []); assert.equal(f.calls[0].body.observing, false);
    f.state.camera = f.state.focus = f.state.visible = f.state.route = true; f.state.pads = [f.device]; f.calls.length = 0;
    press(f.device, 9); await f.controller.poll(); assert.equal(f.calls.length, 0, kind);
    assert.equal(f.controller.state, 'disconnected');
  }
});

test('a device replacing the selected index cannot silently inherit a lease', async () => {
  const f = fixture(); await connected(f); f.state.pads = [pad(0, 'DIFFERENT SOFTWARE FIXTURE')];
  await f.controller.poll(); assert.deepEqual(operations(f.calls), ['input', 'stop', 'disconnect']);
  assert.equal(f.controller.state, 'disconnected');
});

test('B stops and only a later explicit Start edge can resume after neutral input', async () => {
  const f = fixture(); await connected(f); press(f.device, 1); await f.controller.poll();
  assert.equal(f.controller.state, 'paused'); assert.deepEqual(operations(f.calls), ['input', 'stop', 'disconnect']);
  press(f.device, 1, 0); await f.controller.poll(); f.calls.length = 0;
  press(f.device, 9); await f.controller.poll();
  assert.deepEqual(operations(f.calls), ['select', 'input', 'resume']); assert.deepEqual(f.calls[1].body.keys, []);
  assert.equal(f.controller.state, 'active');
});

test('Select and right-stick click are edge-triggered existing mode and speed operations', async () => {
  const f = fixture(); await connected(f);
  press(f.device, 8); await f.controller.poll();
  assert.equal(f.controller.mode, 'joint'); assert.deepEqual(operations(f.calls), ['input', 'mode']);
  await f.controller.poll(); assert.equal(f.calls.filter((call) => call.url.endsWith('/mode')).length, 1);
  press(f.device, 8, 0); f.state.now += 150; await f.controller.poll();
  f.calls.length = 0; press(f.device, 11); await f.controller.poll();
  assert.deepEqual(operations(f.calls), ['input', 'speed']); assert.equal(f.calls[1].body.speed, 'fast');
  assert(f.calls.every((call) => !call.body.keys || call.body.keys.length === 0));
});

test('all shifted semantics stay neutral until sticks and buttons are released', async () => {
  const f = fixture(); await connected(f); f.device.axes[2] = .9; press(f.device, 8);
  await f.controller.poll(); f.calls.length = 0; f.state.now += 150; await f.controller.poll();
  assert(f.calls.every((call) => !call.body.keys || call.body.keys.length === 0));
  f.device.axes[2] = 0; press(f.device, 8, 0); f.state.now += 150; await f.controller.poll();
  assert.equal(f.controller.neutralRequired, false);
});

test('preview reads mock non-neutral inputs without sending any request', async () => {
  const f = fixture(); await f.controller.select(0); await f.controller.setPreview(true);
  f.device.axes = [.7, -.7, .6, -.5]; press(f.device, 6); await f.controller.poll();
  assert(f.controller.lastKeys.length > 0); assert.equal(f.calls.length, 0); assert(f.controller.preview);
});

test('an error from input still sends all three cleanup requests even if neutral fails synchronously', async () => {
  const f = fixture(); await connected(f); const invoked = [];
  f.options.post = (url, body) => { invoked.push({ url, body }); if (url.endsWith('/input')) throw new Error('software network fault'); return Promise.resolve({}); };
  f.state.now = 1000; await f.controller.poll();
  assert.deepEqual(operations(invoked), ['input', 'input', 'stop', 'disconnect']);
  assert.equal(f.controller.state, 'disconnected');
});

test('STOP during a slow controls GET cannot be undone by the old connect continuation', async () => {
  const f = fixture(); let release; f.options.controls = () => new Promise((resolve) => { release = resolve; });
  await f.controller.select(0); const pending = f.controller.connect();
  assert.equal(f.controller.state, 'connecting'); await f.controller.disconnect('software STOP');
  release(structuredClone(controlsFixture)); await assert.rejects(pending, /завершён/);
  assert(!operations(f.calls).includes('resume')); assert.equal(f.controller.state, 'disconnected');
});

test('STOP during a slow resume produces cleanup after reply and no late motion frame', async () => {
  const f = fixture(); let release; const ordinary = f.options.post;
  f.options.post = async (url, body) => { if (url.endsWith('/resume')) { f.calls.push({ url, body }); return new Promise((resolve) => { release = resolve; }); } return ordinary(url, body); };
  await f.controller.select(0); const pending = f.controller.connect();
  for (let i = 0; i < 20 && !release; ++i) await Promise.resolve();
  assert(release); await f.controller.disconnect('software pagehide'); release({});
  await assert.rejects(pending); assert.equal(f.controller.state, 'disconnected');
  assert(f.calls.filter((call) => call.url.endsWith('/input')).every((call) => call.body.keys.length === 0));
  assert.equal(operations(f.calls).at(-1), 'disconnect');
});

test('HTTP/insecure contexts report capability honestly and do not acquire a lease', async () => {
  const f = fixture(); f.options.secure = false;
  assert.equal(api.capability(f.options).supported, false);
  await assert.rejects(f.controller.connect(), /HTTPS/); assert.equal(f.calls.length, 0);
  assert.match(api.panel(f.controller.status()), /HTTPS/);
});

test('persistent keyboard instructions include both modes and source mapping rather than a collapsed help', () => {
  const html = api.keyboardGuide({ help: { keyboard_joint: 'audited fixture joint mapping' } });
  assert.match(html, /Клавиатура/); assert.match(html, /Захват XYZ/); assert.match(html, /audited fixture joint mapping/);
  assert.match(html, /Space/); assert.match(html, /Enter/); assert(!html.includes('<details'));
});

test('native scheme semantic changes are rejected before selecting ownership', async () => {
  const f = fixture(); f.options.controls = async () => {
    const changed = structuredClone(controlsFixture); changed.keyboard.common.KeyW = 'different_unqualified_direction'; return changed;
  };
  await f.controller.select(0); await assert.rejects(f.controller.connect(), /изменилась/);
  assert(!operations(f.calls).includes('select') && !operations(f.calls).includes('resume'));
});

test('keyboard ownership is released before the browser gamepad selects the same native source', async () => {
  const f = fixture(), order = [];
  f.options.beforeConnect = async () => { order.push('release-real-keyboard'); };
  const ordinary = f.options.post;
  f.options.post = async (url, body) => { order.push(url.split('/').at(-1)); return ordinary(url, body); };
  await f.controller.select(0); await f.controller.connect();
  assert.deepEqual(order, ['release-real-keyboard', 'select', 'input', 'resume']);
});

function surface() {
  const handlers = new Map();
  return { hidden: false, addEventListener(name, callback) { if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(callback); },
    querySelector() { return null; }, requestAnimationFrame() {},
    async fire(name, event = {}) { for (const handler of handlers.get(name) || []) await handler(event); } };
}

test('installed blur/pagehide/device-disconnect handlers release the native source without automatic continuation', async () => {
  for (const signal of ['blur', 'pagehide', 'gamepaddisconnected']) {
    const f = fixture(), win = surface(), doc = surface();
    const controller = api.install({ ...f.options, window: win, document: doc });
    await controller.select(0); await controller.connect(); f.calls.length = 0;
    await win.fire(signal, { gamepad: f.device });
    for (let i = 0; i < 20; ++i) await Promise.resolve();
    assert.deepEqual(operations(f.calls), ['input', 'stop', 'disconnect'], signal);
    f.calls.length = 0; await controller.poll(); assert.equal(f.calls.length, 0, signal);
    assert.equal(controller.state, 'disconnected');
  }
});

test('the current owner chassis ban removes only chassis keys from mocked wire packets', async () => {
  const f = fixture();
  f.options.chassisProhibited = () => true;
  f.options.filterKeys = (keys) => keys.filter((key) => !['KeyW','KeyS','KeyA','KeyD','KeyQ','KeyE'].includes(key));
  await connected(f); f.device.axes = [.8, -.8, .8, -.8]; press(f.device, 4); press(f.device, 6);
  f.state.now = 1000; await f.controller.poll();
  assert.deepEqual(f.calls[0].body.keys, ['KeyI','KeyL','KeyN']);
  assert.match(api.panel(f.controller.status()), /Движение шасси сейчас запрещено/);
});

(async () => {
  let passed = 0;
  for (const entry of tests) { await entry.callback(); ++passed; process.stdout.write(`PASS ${entry.name}\n`); }
  process.stdout.write(JSON.stringify({ passed, actualHardwareRequests: 0, actualChassisMotion: 0, transport: 'in-memory mock only' }) + '\n');
})().catch((error) => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
