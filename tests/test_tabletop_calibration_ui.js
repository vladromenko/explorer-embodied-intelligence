"use strict";
// Software HTTP/DOM fixtures only; no camera or actuator.
const test = require("node:test"), assert = require("node:assert/strict");
globalThis.crypto ||= require("node:crypto").webcrypto;
const {createTabletopCalibrationClient,tabletopCalibrationPanel} = require("../00-control-center/src/explorer/static/tabletop-calibration.js");
const fixture = () => ({current_frame:{frame_id:"pi-overview:fixture:1",frame_sha256:"a".repeat(64)},session:null});
test("reading status never captures and only the exact displayed frame can be saved",async () => {
  const calls=[], state=fixture();
  const client=createTabletopCalibrationClient({get:async path => {calls.push(["GET",path]);return state;},post:async (path,body) => {calls.push(["POST",path,body]);return {...state,session:{session_id:"series",revision:"r",captures:[]}};}});
  assert.equal(calls.length,0);await client.refresh(true);assert.equal(calls.length,1);
  await assert.rejects(client.capture("intrinsics"),/Обновите/);assert.equal(calls.length,1);
  client.displayedFrame("wrong");await assert.rejects(client.capture("intrinsics"));
  client.displayedFrame(state.current_frame.frame_id);await client.capture("intrinsics");
  assert.equal(calls[1][2].frame_sha256,"a".repeat(64));assert.equal(calls[1][2].new_session,true);
  assert.ok(calls.every(row => row[1].startsWith("/api/lab/vision/calibration/")));
});
test("late status cannot replace a saved series and drafts survive status refresh",async () => {
  let resolve, counter=0;const state=fixture();
  const client=createTabletopCalibrationClient({get:async () => ++counter===1 ? state : new Promise(done => {resolve=done;}),post:async () => ({...state,session:{session_id:"saved",revision:"r"}})});
  await client.refresh(true);client.edit("corner_0_x","12.5");const late=client.refresh(true);
  client.displayedFrame(state.current_frame.frame_id);await client.capture("reference");resolve(state);await late;
  assert.equal(client.snapshot().status.session.session_id,"saved");assert.equal(client.snapshot().draft.corner_0_x,"12.5");
});
test("solution acceptance requires shown overlay and explicit confirmations",async () => {
  const state={...fixture(),session:{session_id:"s",revision:"r",solutions:[{solution_id:"solution",artifact_sha256:"b".repeat(64)}]},expected_active_calibration_sha256:null};let posts=0;
  const client=createTabletopCalibrationClient({get:async () => state,post:async (path,body) => {posts++;assert.equal(body.solution_sha256,"b".repeat(64));assert.equal(body.expected_revision,"r");return state;}});
  await client.refresh(true);client.edit("confirm_axes_overlay",true);client.edit("confirm_mount_unchanged",true);
  await assert.rejects(client.accept(),/наложение/);assert.equal(posts,0);
  client.solutionDisplayed("solution");await client.accept();assert.equal(posts,1);
  const html=tabletopCalibrationPanel({status:state,error:'<script>move()</script>'});
  assert.ok(html.includes("&lt;script&gt;"));assert.ok(!html.includes("<script>"));assert.ok(html.includes('data-calibration-field="corner_3_z"'));
});
