"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {createTransferGeometryClient,transferGeometryPanel}=require("../00-control-center/src/explorer/static/transfer-geometry.js");
const choice=(id="a")=>({job_id:id.repeat(32),spec_sha256:"f".repeat(64),skill_version:1,technical_source_ready:true,source_calibration_sha256:"c".repeat(64),checkpoint_content_sha256:"d".repeat(64)});
const binding=row=>({model_job_id:row.job_id,spec_sha256:row.spec_sha256,source_calibration_sha256:row.source_calibration_sha256,checkpoint_content_sha256:row.checkpoint_content_sha256});
const response=row=>({source_model_binding:binding(row),geometry_preview_ready:true,audit:{B:[{id:"source_and_target_metric_gripper_aperture"}],C:[{id:"qualified06_object_skill_source"}]}});
const wizard=row=>({wizard_id:"11111111-1111-4111-8111-111111111111",source_model_binding:binding(row),domain:"simulation",source_version:1,target_version:2,steps:[]});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture(extra={}){const calls=[],cache=new Map();const row=choice();const client=createTransferGeometryClient({get:async path=>{calls.push(["GET",path]);return response(row);},post:async(path,body)=>{calls.push(["POST",path,body]);return wizard(row);},randomUUID:()=>"22222222-2222-4222-8222-222222222222",storage:{getItem:key=>cache.get(key),setItem:(key,value)=>cache.set(key,value)},...extra});return {client,calls,cache,row};}

test("geometry stays available for technically ready source while object mastery remains separate",async()=>{
  const {client,calls,row}=fixture();client.select({...row,eligible_for_transfer:false});await client.preview();
  const view=client.snapshot();const html=transferGeometryPanel(view);assert.match(html,/Нужна независимая полная предметная попытка/);assert.match(html,/Нужно измерить апертуру/);
  assert.equal(calls.length,1);assert.equal(calls[0][0],"GET");assert.ok(!html.includes('data-native'));
  client.setTargetVersion("5");assert.match(transferGeometryPanel(client.snapshot()),/value="5"/);
});

test("explicit wizard create posts only model, advancing version and stable data request ID",async()=>{
  const {client,calls,row,cache}=fixture();client.select(row);await client.preview();const result=await client.create(2);await client.create(2);
  const posts=calls.filter(call=>call[0]==="POST");assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
  assert.deepEqual(Object.keys(posts[0][2]).sort(),["model_job_id","request_id","target_version"]);assert.equal(result.wizard_id,client.snapshot().savedWizardId);assert.equal(cache.size,1);
  assert.ok(calls.every(call=>call[1].startsWith('/api/transfer/')));
});

test("late preview cannot replace another selected model even after switching back",async()=>{
  const wait=deferred(),{client,row}=fixture({get:()=>wait.promise});client.select(row);const work=client.preview();client.select(choice("b"));client.select(row);wait.resolve(response(row));await work;
  assert.equal(client.snapshot().geometry,null);assert.equal(client.snapshot().busy,false);
});

test("late wizard cannot attach old metadata to another selected source",async()=>{
  const wait=deferred(),{client,row}=fixture({post:()=>wait.promise});client.select(row);await client.preview();const work=client.create(2);client.select(choice("b"));wait.resolve(wizard(row));await work;
  assert.equal(client.snapshot().wizard,null);assert.equal(client.snapshot().savedWizardId,"");
});

test("wrong scope/calibration reply and changed catalog never enable stale metadata",async()=>{
  const row=choice(),{client}=fixture({get:async()=>({...response(row),source_model_binding:{...binding(row),source_calibration_sha256:"e".repeat(64)}})});
  client.select(row);await assert.rejects(client.preview(),/другой исходной версии/);assert.equal(client.snapshot().geometry,null);
  client.refreshChoice({...row,technical_source_ready:false});await assert.rejects(client.preview(),/checkpoint/);
});

test("lost data reply preserves request identity and retained wizard can be restored without mutation",async()=>{
  const row=choice(),calls=[];let first=true;
  const {client}=fixture({post:async(path,body)=>{calls.push(body);if(first){first=false;throw new Error("lost reply");}return wizard(row);},get:async path=>path.includes('/calibration/')?{...wizard(row),current_geometry:response(row)}:response(row)});
  client.select(row);await client.preview();await assert.rejects(client.create(2),/lost reply/);await client.create(2);assert.deepEqual(calls[0],calls[1]);
  await client.restore();assert.equal(client.snapshot().wizard.wizard_id,wizard(row).wizard_id);
});


test("restored target version remains the saved version and mismatched version replies are rejected",async()=>{
  const row=choice(),saved={...wizard(row),target_version:4};
  const {client}=fixture({post:async()=>saved,get:async path=>path.includes('/calibration/')?{...saved,current_geometry:response(row)}:response(row)});
  client.select(row);await client.preview();await client.create(4);client.select(row);await client.restore();
  assert.equal(client.snapshot().targetVersion,"4");assert.match(transferGeometryPanel(client.snapshot()),/value="4"/);
  const wrong=fixture({post:async()=>saved}).client;wrong.select(row);await wrong.preview();await assert.rejects(wrong.create(2),/целевой версии/);
});
