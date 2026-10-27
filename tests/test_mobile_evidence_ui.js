"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { mobileEvidencePanel, liveMobileEvidence, createMobileEvidenceClient } = require("../00-control-center/src/explorer/static/mobile-evidence.js");

test("inventory availability cannot masquerade as actual ROSMASTER JPEG freshness", () => {
  assert.equal(liveMobileEvidence({ camera_id: "nano-rgbd", available: true, online: true }).camera_fresh, false);
  assert.equal(liveMobileEvidence({ camera_id: "pi-overview", ready: true, freshness_clock: "browser_receive_monotonic_only" }).camera_fresh, false);
  assert.equal(liveMobileEvidence({ camera_id: "nano-rgbd", ready: true, frame_id: "actual-1", freshness_clock: "browser_receive_monotonic_only" }).camera_fresh, true);
});

test("three observation imports remain analysis-only with honest frame/ACT counts", () => {
  const imports = [9, 9, 18].map((frames, index) => ({ original_episode_id: String(index), raw_frames_recorded: frames,
    admitted_for_training: false, rgb_verified: false, action9_verified: false, causal_verified: false,
    reasons: ["observation_only_no_admitted_dataset", "unknown_or_unqualified_arm_history:UNKNOWN_AFTER_REBOOT"], visual_ACT: { trained_RGB_model_verified: false } }));
  const html = mobileEvidencePanel({ counts: { imported: 3, raw_frames_recorded: 36, admitted_for_training: 0, observation_or_rejected: 3 }, imports });
  assert.equal((html.match(/Остаётся для анализа/g) || []).length, 3);
  assert.equal((html.match(/Подтверждённого обучения ещё нет/g) || []).length, 3);
  assert.ok(!html.includes("Допущен к офлайн-обучению"));
});

test("recording status/outcome and labels are displayed without trusting source HTML", () => {
  const html = mobileEvidencePanel({ imports: [{ recording_name: '<script>bad()</script>', reasons: ['<img src=x>'] }] },
    { teaching: { active: { id: "session", name: "Показ", frames: 9, samples: 9 } } });
  assert.ok(html.includes("Идёт запись")); assert.ok(html.includes("&lt;script&gt;")); assert.ok(!html.includes("<script>"));
});

test("read-only refresh coalesces pending requests and preserves outage evidence", async () => {
  let finish, calls = 0;
  const client = createMobileEvidenceClient({ fetchEvidence: () => { calls++; return new Promise(resolve => { finish = resolve; }); } });
  const first = client.refresh(); await client.refresh(); assert.equal(calls, 1);
  finish({ counts: { admitted_for_training: 0 } }); await first;
  assert.equal(client.snapshot().counts.admitted_for_training, 0);
});

test("sensor recording, admission and actual model use remain separate; canonical needs no legacy view", () => {
  const html = mobileEvidencePanel({ imports: [{ admitted_for_training: true, verification_basis: "source_bound_unified_embodied_contract",
    legacy_RGB9D_view: { rgb_verified: false }, mobile_embodied: { admitted_for_training: true, channels: {
      camera_rgb: { recorded: true, admitted: true, model_used: false }, joint_feedback: { recorded: false, admitted: false, model_used: false }
    } }, reasons: [], visual_ACT: { trained_RGB_model_verified: false } }] });
  assert.ok(html.includes("Допущен к офлайн-обучению"));
  assert.ok(html.includes("<th scope=\"row\">RGB</th><td>да</td><td>да</td><td>нет</td>"));
  assert.ok(html.includes("Измеренные суставы</th><td>нет</td><td>нет</td><td>нет</td>"));
  assert.ok(html.includes("Настоящие нулевые команды остаются"));
  assert.ok(!html.includes("В старом представлении ACT положения"));
});

test('03 raw observation RGB remains recorded while admission and model use stay false',()=>{
 const html=mobileEvidencePanel({project_id:'03',counts:{imported:1,pending_imports:0,raw_frames_recorded:39,admitted_for_training:0},imports:[{
  import_status:'completed',raw_frames_recorded:39,admitted_for_training:false,rgb_verified:false,
  raw_observation_evidence:{available_source_channel_counts:{camera_rgb:39,imu:39}},
  mobile_embodied:{channels:{camera_rgb:{recorded:false,admitted:false,model_used:false}}},reasons:[],visual_ACT:{trained_RGB_model_verified:false}
 }]},{project_id:'03'});
 assert.match(html,/03 \/ От кадра к модели/);assert.match(html,/Сохранено кадров: 39/);
 assert.match(html,/<th scope="row">RGB<\/th><td>да<\/td><td>нет<\/td><td>нет<\/td>/);
 assert.match(html,/leader и записью/);assert.doesNotMatch(html,/Допущен к офлайн-обучению/);
});
