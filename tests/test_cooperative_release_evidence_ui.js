"use strict";
// Pure DOM/HTTP fixtures. No device, native start or real USER authorization.
const test=require("node:test"),assert=require("node:assert/strict");
const {createCooperativeWorkbenchClient,cooperativeWorkbenchPanel,bindCooperativeWorkbench}=require("../00-control-center/src/explorer/static/cooperative-workbench.js");
const clone=value=>JSON.parse(JSON.stringify(value));
const identity="a".repeat(32),nextID="b".repeat(32),statements=["receiver_holds_object","giver_stationary","receiver_stationary"];
function fixture() {
  const spec={skill_id:"skill",version:1,spec_sha256:"c".repeat(64),mode:"both_manual",subskills:{giver:{},receiver:{}}};
  const session={session_id:"run",skill_id:"skill",skill_version:1,epoch:"epoch",mode:"both_manual",status:"READY_FOR_OBSERVED_STEPS",execution_domain:"user_observed_physical_test",protocol_state:"RECEIVER_GRASP_CHECK",pending_operation:{event:"release_requested",operation_id:"operation"}};
  const bundle={kind:"parked_release_bundle",bundle_id:identity,challenge_id:identity,run_id:"run",session_id:"inbox-process",object_id:"object",state:"pending",source_challenge_sha256:"d".repeat(64),deadline_ns:300,questions:Object.fromEntries(statements.map(key=>[key,{question:key}]))};
  return {workbench:{skills:[{spec,confirmed:true}],sessions:[session]},pending_evidence:[bundle],catalogue:{phases:[]}};
}
async function rig(state=fixture(),extra={}) {
  const posts=[],stored=new Map();
  const client=createCooperativeWorkbenchClient({get:async()=>clone(state),post:async(path,body)=>{posts.push([path,body]);return {result:{},snapshot:clone(state)};},storage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)},...extra});
  await client.refresh();client.chooseSession("run");return {client,posts,state,stored};
}
function affirm(client,id=identity) {
  for (const key of statements) client.edit(`evidence.${id}.affirmations.${key}`,true);
  for (const key of ["observing","neutral","user_observed_test_confirmed"]) client.edit(`evidence.${id}.${key}`,true);
}
test("fresh release questions render without commands; complete separate USER event posts only server bundle and explicit flags",async()=>{
  const {client,posts,stored}=await rig();const html=cooperativeWorkbenchPanel(client.snapshot());assert.equal(posts.length,0);
  assert.match(html,/Подтвердить наблюдения и возобновить отпускание/);assert.match(html,/Органы ручного управления в нейтрали/);
  assert.throws(()=>client.resumeParkedRelease(identity),/три новых/);
  affirm(client);client.edit(`evidence.${identity}.actor_id`,"spoof");client.edit(`evidence.${identity}.control_epoch`,"spoof");
  assert.throws(()=>client.confirmEvidence(identity),/конкретный текущий/);
  await client.resumeParkedRelease(identity);
  assert.deepEqual(posts,[["/api/cooperative/native_parked_release_resume",{session_id:"run",expected_epoch:"epoch",release_bundle_id:identity,affirmations:Object.fromEntries(statements.map(key=>[key,true])),observing:true,neutral:true,user_observed_test_confirmed:true}]]);
  const disk=JSON.parse([...stored.values()][0]);assert.deepEqual(disk.draft.evidence,{});
});
test("every independently unchecked fact or control flag prevents POST",async()=>{
  for (const field of [...statements.map(key=>`affirmations.${key}`),"observing","neutral","user_observed_test_confirmed"]) {
    const {client,posts}=await rig();affirm(client);client.edit(`evidence.${identity}.${field}`,false);
    assert.throws(()=>client.resumeParkedRelease(identity),/три новых/);assert.equal(posts.length,0);
  }
});
test("new bundle, changed source hash and changed Root epoch clear old checks while unchanged snapshots retain them",async()=>{
  const {client,state,posts}=await rig();affirm(client);await client.refresh();assert.equal(client.snapshot().draft.evidence[identity].observing,true);
  state.pending_evidence[0].source_challenge_sha256="e".repeat(64);await client.refresh();assert.equal(client.snapshot().draft.evidence[identity],undefined);
  affirm(client);state.workbench.sessions[0].epoch="new-epoch";await client.refresh();assert.equal(client.snapshot().draft.evidence[identity],undefined);
  client.edit(`evidence.${nextID}.observing`,true);state.pending_evidence[0].bundle_id=nextID;state.pending_evidence[0].challenge_id=nextID;
  await client.refresh();assert.equal(client.snapshot().draft.evidence[nextID],undefined);assert.equal(client.snapshot().draft.evidence[identity],undefined);
  assert.throws(()=>client.resumeParkedRelease(nextID));assert.equal(posts.length,0);
});
test("reload never restores three old observations and an absent bundle cannot be submitted",async()=>{
  const {client,state,stored}=await rig();affirm(client);
  const loaded=createCooperativeWorkbenchClient({get:async()=>clone(state),post:async()=>{throw Error("unexpected POST");},storage:{getItem:key=>stored.get(key),setItem:()=>{}}});
  await loaded.refresh();assert.deepEqual(loaded.snapshot().draft.evidence,{});assert.throws(()=>loaded.resumeParkedRelease(identity));
  state.pending_evidence=[];await client.refresh();assert.deepEqual(client.snapshot().draft.evidence,{});assert.throws(()=>client.resumeParkedRelease(identity));
});
test("observation-only mode, foreign run, wrong state and ordinary question cannot authorize native release",async()=>{
  for (const change of [s=>s.workbench.sessions[0].execution_domain="observation_only",s=>s.workbench.sessions[0].protocol_state="OFFER",s=>s.workbench.sessions[0].pending_operation=null,s=>s.pending_evidence[0].run_id="other",s=>s.pending_evidence[0].kind="ordinary"]) {
    const state=fixture();change(state);const {client,posts}=await rig(state);affirm(client);assert.throws(()=>client.resumeParkedRelease(identity));assert.equal(posts.length,0);
  }
});
test("late release reply cannot undo STOP or restore an enabled release button",async()=>{
  const state=fixture();let resolve;const reply=new Promise(yes=>{resolve=yes;});const posts=[];
  const {client}=await rig(state,{post:async(path,body)=>{posts.push([path,body]);if(path.endsWith("/native_parked_release_resume"))return reply;return {result:{},snapshot:clone(state)};}});
  affirm(client);const pending=client.resumeParkedRelease(identity);await client.stop();resolve({result:{resumed:true},snapshot:clone(state)});
  assert.deepEqual(await pending,{ignored:true});assert.throws(()=>client.resumeParkedRelease(identity),/После STOP/);assert.equal(posts.length,2);
});
test("actual click binding invokes only the explicit control and renderer escapes source-provided questions",async()=>{
  const {client,state,posts}=await rig();state.pending_evidence[0].questions.giver_stationary.question='<img src=x onerror="bad()">';await client.refresh();affirm(client);
  const html=cooperativeWorkbenchPanel(client.snapshot());assert.ok(!html.includes('<img src=x'));assert.ok(html.includes('&lt;img'));
  const handlers={};const root={contains:()=>true,addEventListener:(name,fn)=>{handlers[name]=fn;},removeEventListener:()=>{}};
  const unbind=bindCooperativeWorkbench(root,client);handlers.click({target:{closest:()=>({disabled:false,dataset:{coopAction:"release-resume",coopBundle:identity}})}});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(posts.length,1);assert.equal(posts[0][0],"/api/cooperative/native_parked_release_resume");unbind();
});
test("hidden page and focus loss clear observations without any native command and teardown removes listeners",async()=>{
  const {client,posts}=await rig(),events=new Map(),windowEvents=new Map();
  const win={addEventListener:(key,fn)=>windowEvents.set(key,fn),removeEventListener:key=>windowEvents.delete(key)};
  const doc={defaultView:win,visibilityState:"visible",addEventListener:(key,fn)=>events.set(key,fn),removeEventListener:key=>events.delete(key)};
  const root={ownerDocument:doc,addEventListener:()=>{},removeEventListener:()=>{}};
  const unbind=bindCooperativeWorkbench(root,client);affirm(client);windowEvents.get("blur")();assert.deepEqual(client.snapshot().draft.evidence,{});
  affirm(client);doc.visibilityState="hidden";events.get("visibilitychange")();assert.deepEqual(client.snapshot().draft.evidence,{});
  assert.throws(()=>client.resumeParkedRelease(identity));assert.equal(posts.length,0);unbind();assert.equal(events.size,0);assert.equal(windowEvents.size,0);
});
