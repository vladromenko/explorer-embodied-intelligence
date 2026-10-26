"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), vm = require("node:vm"), fs = require("node:fs");
const root = "00-control-center/src/explorer/static/";
function context() {
  const ctx = vm.createContext({ window: { location: { pathname: "/experiments/03" }, addEventListener() {} }, document: { addEventListener() {} }, setInterval() {},
    PROJECTS: [{ id: "03", title: "03" }, { id: "04", title: "04" }], model: { jobs: [], skills: [] },
    array: value => Array.isArray(value) ? value : [], number: value => typeof value === "number" ? value : null,
    text: (value, fallback = "") => value ?? fallback, esc: value => String(value ?? ""), count: String,
    icon: () => "", heading: (...args) => args.join(" "), notice: (...args) => args.join(" "), kv: () => "",
    demonstrationWizardPanel: value => `WIZARD-${value}`, jobsPanel: () => "JOBS", policyTransferPanel: () => "VERIFIED-EXISTING-MODEL-TRANSFER" });
  vm.runInContext(fs.readFileSync(root + "station.js", "utf8"), ctx);
  vm.runInContext(fs.readFileSync(root + "direct03.js", "utf8"), ctx);
  return ctx;
}
test("03 immediately offers leader to ROSMASTER manual controls without follower training", () => {
  const ctx = context();
  vm.runInContext('stationControlPanel = stationTrainingPanel = stationEvaluationPanel = () => { throw new Error("wrong follower workflow"); }', ctx);
  const html = vm.runInContext('stationPage("03")', ctx);
  assert.match(html, /direct03-start/); assert.match(html, /direct03-recenter/); assert.match(html, /SO-101 leader/);
  assert.match(html, /не нужны запись или обучение/); assert.match(html, /камер.*ROSMASTER/i);
  assert.doesNotMatch(html, /data-station-joint="follower-/); assert.doesNotMatch(html, /station-train/);
});
test("04 consumes a learned source and never renders follower teleop or source training", () => {
  const ctx = context();
  vm.runInContext('stationControlPanel = stationTrainingPanel = stationEvaluationPanel = () => { throw new Error("wrong follower workflow"); }', ctx);
  const html = vm.runInContext('stationPage("04")', ctx);
  assert.match(html, /VERIFIED-EXISTING-MODEL-TRANSFER/); assert.match(html, /уже обученную/);
  assert.doesNotMatch(html, /station-teleop|station-train|direct03-start/);
});
test("03 selects Nano on entry, lab selects Pi; Nano outage never silently substitutes tabletop", () => {
  const source = fs.readFileSync(root + "app.js", "utf8");
  const fn = source.slice(source.indexOf("function syncCamera()"), source.indexOf("function filterManualKeysForCurrentOwnerConstraints"));
  const ctx = vm.createContext({ currentCamera: "pi-overview", cameraSignature: "", cameraMode: "stream", cameraImage: { getAttribute: () => "some-src" },
    cameraSelect: {}, available: [{ id: "nano-rgbd", available: true }, { id: "pi-overview", available: true }],
    route: "/experiments/03", manualPreview: { state: () => ({ active: false }), start() {}, stop() {} }, cameras: () => ctx.available,
    rosmasterDemonstrationRoute: () => ["02", "03"].includes(ctx.route.split("/").at(-1)),
    path: () => ctx.route, activeProject: () => ({ id: ctx.route.split("/").at(-1) }), cameraLabel: camera => camera.id,
    startCamera: () => {}, stationRoute: () => true, esc: String });
  vm.runInContext(fn, ctx); vm.runInContext("syncCamera()", ctx); assert.equal(ctx.currentCamera, "nano-rgbd");
  ctx.available[0].available = false; vm.runInContext("syncCamera()", ctx); assert.equal(ctx.currentCamera, "nano-rgbd");
  ctx.route = "/learning/so101"; vm.runInContext("syncCamera()", ctx); assert.equal(ctx.currentCamera, "pi-overview");
});
