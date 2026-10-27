"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLabSkillsClient, labSkillsPanel, installLabSkillsHandlers } = require("../00-control-center/src/explorer/static/lab-skills.js");

function fixture(id = "skill-a", version = 1) {
  return { spec: { skill_id: id, version, owner_experiment: "06", title: "Конфетка → коробка", object: { query: "Конфетка" }, goal: { query: "Коробка" },
    success: { description: "Предмет лежит в коробке" }, phase_ids: ["grasp", "place_release"], demo_plan: { variants: [{ variant_id: "variant-001", title: "Первый показ", split: "train" }] } },
    spec_sha256: "a".repeat(64), confirmed: true, assignments: [], sessions: [], training_jobs: [], data_ready: false, train_ready: false,
    plan: { covered_variants: 0, planned_variants: 5 }, counts: { accepted_originals: 0, assigned_originals: 0 }, next_action: { reason: "Нужен показ" } };
}
function harness(options = {}) {
  const calls = [], selected = fixture();
  const api = async (path, request = {}) => {
    calls.push({ path, request });
    assert.ok(path === "/api/lab/skills" || path.startsWith("/api/lab/skills/"));
    if (options.respond) { const result = options.respond(path, request); if (result !== undefined) return result; }
    if (path.endsWith("/catalogue")) return { templates: [{ template_id: "pick_object_to_container", title: "Предмет в коробку" }] };
    if (path.endsWith("/imports")) return { imports: [] };
    if (path === "/api/lab/skills" && !request.method) return { skills: [selected] };
    if (path.includes("/readiness?")) return { demos: { ready: false }, trained: { ready: false }, physical: { ready: false } };
    if (request.method === "POST") return path.endsWith("capture-context") ? { record_start_body: { skill_id: selected.spec.skill_id }, physical_commands_dispatched: false } : selected;
    return { ...fixture(decodeURIComponent(path.split("/")[4].split("?")[0])), spec_sha256: "a".repeat(64) };
  };
  return { client: createLabSkillsClient({ api, ...options.client }), calls };
}

test("initial load and rendering fetch only metadata; no automatic optimizer, camera or motor request", async () => {
  const { client, calls } = harness(); await client.refresh();
  assert.equal(client.snapshot().selected, "skill-a");
  assert.equal(calls.length, 5); assert.ok(calls.every(row => !row.request.method));
  const html = labSkillsPanel(client.snapshot());
  assert.ok(html.includes("Новая модель не двигает руки автоматически"));
  assert.match(html, /data-lab-form="train"[\s\S]*?button class="button primary" disabled/);
  assert.ok(!html.includes("/api/station") && !html.includes("/api/robot"));
});

test("late old skill state cannot replace newly selected version", async () => {
  let oldState, oldReadiness;
  const { client } = harness({ respond(path) {
    if (path === "/api/lab/skills/skill-a?version=1") return new Promise(resolve => { oldState = resolve; });
    if (path === "/api/lab/skills/skill-a/readiness?version=1") return new Promise(resolve => { oldReadiness = resolve; });
  } });
  const pending = client.refresh(); await new Promise(resolve => setImmediate(resolve));
  await client.select("skill-b", 2);
  oldState(fixture("skill-a")); oldReadiness({ trained: { ready: true } }); await pending;
  assert.equal(client.snapshot().selected, "skill-b"); assert.equal(client.snapshot().version, 2);
  assert.equal(client.snapshot().state.spec.skill_id, "skill-b"); assert.equal(client.snapshot().readiness.trained.ready, false);
});

test("capture-context emits metadata callback and makes exactly one explicit lab POST", async () => {
  const contexts = []; const { client, calls } = harness({ client: { onCaptureContext: value => contexts.push(value) } });
  await client.refresh(); await client.captureContext({ variant_id: "variant-001", session_id: "session", object_instance_id: "object" });
  const posts = calls.filter(row => row.request.method === "POST");
  assert.equal(posts.length, 1); assert.equal(posts[0].path, "/api/lab/skills/skill-a/capture-context");
  assert.equal(JSON.parse(posts[0].request.body).version, 1); assert.equal(contexts.length, 1);
  assert.equal(contexts[0].physical_commands_dispatched, false);
});

test("late capture context cannot seed a different selected skill or version", async () => {
  let finish; const contexts=[];
  const {client}=harness({respond(path,request){
    if(path.endsWith("capture-context") && request.method==="POST")return new Promise(resolve=>{finish=resolve;});
  },client:{onCaptureContext:value=>contexts.push(value)}});
  await client.refresh();const pending=client.captureContext({variant_id:"variant-001",session_id:"session-a",object_instance_id:"object-a"});
  await client.select("skill-b",2);
  finish({record_start_body:{skill_id:"skill-a",skill_version:1},physical_commands_dispatched:false});await pending;
  assert.equal(client.snapshot().selected,"skill-b");assert.equal(client.snapshot().version,2);
  assert.equal(client.snapshot().captureContext,null);assert.deepEqual(contexts,[]);
});

test("explicit mutations are serialized and disposal ignores late metadata", async () => {
  let finish; const { client, calls } = harness({ respond(path, request) {
    if (path.endsWith("/train") && request.method === "POST") return new Promise(resolve => { finish = resolve; });
  } });
  await client.refresh(); const pending = client.train({ request_id: "one", act_steps: 8 });
  await assert.rejects(client.train({ request_id: "two", act_steps: 8 }), /Предыдущее/);
  client.dispose(); finish({ id: "job" }); await pending;
  assert.equal(calls.filter(row => row.request.method === "POST").length, 1);
});

test("source review uses original indexed JPEG and keeps requested, accepted, dispatched, measured separate", async () => {
  const { client, calls } = harness({ respond(path) {
    if (path.includes("/imports/job/review?")) return { total_samples: 2, action_names: ["joint"], camera_ids: ["camera"], rows: [
      { source_sample_index: 0, leader_state: [1], requested_action: [2], accepted_action: [3], dispatched_action: [3], state: [0], frame_id: "frame-0", frame_sha256: "0".repeat(64) },
      { source_sample_index: 1, leader_state: [2], requested_action: [3], accepted_action: [4], dispatched_action: [4], state: [1], frame_id: "frame-1", frame_sha256: "1".repeat(64) }] };
  } });
  await client.refresh(); await client.loadReview("job"); client.setReviewFrame(1);
  const html = labSkillsPanel(client.snapshot());
  assert.ok(html.includes('src="/api/lab/skills/imports/job/frame/1"'));
  for (const label of ["Измерено у лидера", "Запрошено", "Принято", "Отправлено", "Измерено у последователя"]) assert.ok(html.includes(label));
  assert.ok(calls.every(row => !row.request.method)); assert.throws(() => client.setReviewFrame(99), /исходный кадр/);
});

test("source labels and errors cannot inject HTML, raw hardware defaults are never suggested", () => {
  const state = fixture(); state.spec.title = '<img src=x onerror="bad()">';
  state.assignments = [{ binding: { original_episode_id: "<script>bad()</script>", split: "train", import_job_id: "job" }, accepted: false, reasons: ["<svg onload=bad()>"] }];
  const html = labSkillsPanel({ state, skills: [state], selected: "skill-a", version: 1, error: "<script>error</script>" });
  assert.ok(html.includes("&lt;script&gt;")); assert.ok(!html.includes("<script>") && !html.includes("<svg onload"));
  assert.ok(!html.includes("use_degrees=True") && !html.includes("lerobot-replay"));
});

test("handler installation/removal is explicit and contains only metadata client dispatch", () => {
  const listeners = new Map(); const root = { addEventListener: (name, callback) => listeners.set(name, callback), removeEventListener: name => listeners.delete(name) };
  const cleanup = installLabSkillsHandlers(root, {}); assert.deepEqual([...listeners.keys()], ["click", "change", "submit"]);
  cleanup(); assert.equal(listeners.size, 0);
});
