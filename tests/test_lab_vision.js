"use strict";
// All frames and HTTP requests are memory-only software fixtures.
const test = require("node:test"), assert = require("node:assert/strict");
const {createLabVisionClient,labVisionPanel,maskPixels,sourcePixel} = require("../00-control-center/src/explorer/static/lab-vision.js");
const pair = () => ({frame:{camera_id:"pi-overview",frame_id:"fixture:1",frame_sha256:"a".repeat(64),width:640,height:480},observation:{camera_id:"pi-overview",frame_id:"fixture:1",frame_sha256:"a".repeat(64)}});
test("freeze binds exact camera/frame/SHA and a click forwards that identity only", async () => {
  const calls=[];const client=createLabVisionClient({get:async path => {calls.push([path]);return pair();},post:async (path,body) => {calls.push([path,body]);return {selection_id:"s",pixel:body.pixel,purpose:body.purpose};}});
  assert.equal(calls.length,0);await client.freeze();await client.select([20,30],"object");
  assert.deepEqual(calls[1],["/api/lab/vision/select",{frame_id:"fixture:1",frame_sha256:"a".repeat(64),pixel:[20,30],purpose:"object"}]);
  await client.semantic();assert.deepEqual(calls[2],["/api/lab/vision/semantic",{trigger:"user_selection",selection_id:"s"}]);
  assert.ok(calls.every(([path]) => path.startsWith("/api/lab/vision/")));
});
test("mismatched frame/mask identity is rejected before any selection POST",async () => {
  for (const key of ["camera_id","frame_id","frame_sha256"]) {
    const bad=pair();bad.observation[key]="different";let posts=0;
    const client=createLabVisionClient({get:async () => bad,post:async () => {posts++;}});
    await assert.rejects(client.freeze(),/разным кадрам/);assert.equal(client.snapshot().frozen,null);
    await assert.rejects(client.select([1,1],"object"),/Сначала/);assert.equal(posts,0);
  }
});
test("an in-flight freeze cleared by navigation cannot restore an obsolete selected frame",async () => {
  let resolve;const client=createLabVisionClient({get:() => new Promise(done => {resolve=done;})});
  const request=client.freeze();client.clear();resolve(pair());await request;assert.equal(client.snapshot().frozen,null);
});
test("metric grid does not submit until current measured calibration is valid",async () => {
  let posts=0;const client=createLabVisionClient({get:async () => ({calibration:{valid:false}}),post:async () => {posts++;}});
  await client.refresh(true);await assert.rejects(client.metricGrid(.03),/калибровка/);assert.equal(posts,0);
});
test("source-resolution masks and scaled clicks preserve the source image convention",() => {
  const rle={encoding:"row_major_runs_v1",starts_with:0,shape:[2,3],counts:[1,2,2,1]};
  assert.deepEqual([...maskPixels(rle,3,2)],[0,1,1,0,0,1]);
  assert.throws(() => maskPixels({...rle,counts:[1,2,3,1]},3,2));
  assert.deepEqual(sourcePixel({clientX:180,clientY:130},{left:20,top:10,width:320,height:240},{width:640,height:480}),[320,240]);
  assert.throws(() => sourcePixel({clientX:340,clientY:250},{left:20,top:10,width:320,height:240},{width:640,height:480}));
});
test("VLM text and selection metadata are escaped and never render motor execution controls",() => {
  const html=labVisionPanel({status:{vlm:{worker:{receipts:[{result:{semantic_text:'<img src=x onerror="move()">'}}]}}}});
  assert.ok(html.includes("&lt;img"));assert.ok(!html.includes("<img src=x"));
  assert.ok(!html.includes("data-action=\"execute"));assert.ok(html.includes("не подтверждает координаты"));
});
