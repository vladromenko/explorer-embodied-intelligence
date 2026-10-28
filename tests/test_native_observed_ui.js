"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {createObservedNativeClient,observedNativePanel}=require("../00-control-center/src/explorer/static/native-observed.js");
function rig(){
  const calls=[],revoked=[];let tick=0,hook=null;
  const challenge={challenge_id:"a".repeat(32),frame:{jpeg_sha256:"b".repeat(64),frame_id:"native-frame",boot_id:"native-boot"}};
  const options={api:async(path,opts)=>{calls.push({path,...opts});if(hook)return hook(path,opts);return path.endsWith("challenge")?challenge:path.endsWith("authorize")?{execution_permission:true,authorization_id:"explicit-test-only",input_mode:"so101_leader"}:{};},
    now:()=>tick,frame:async()=>({sha256:challenge.frame.jpeg_sha256,frame_id:"native-frame",boot_id:"native-boot",blob:{localMock:true}}),createURL:()=>"blob:mock",revokeURL:url=>revoked.push(url),decode:async()=>{}};
  const client=createObservedNativeClient(options);
  return {client,calls,revoked,options,challenge,setTick:value=>tick=value,setHook:value=>hook=value,confirm:()=>client.configure({confirmations:{observing:true,neutral:true,space_clear:true,user_observed_test_confirmed:true}})};
}
test("metadata refresh and panel rendering never authorize HOME or start motion",async()=>{
  const r=rig();await r.client.refresh();const html=observedNativePanel(r.client.snapshot());
  assert.ok(r.calls.every(row=>!row.method));assert.match(html,/HOME действительно двигает руку/);assert.match(html,/шасси заблокировано/);
  assert.equal(r.client.chassisAllowed(),false);
});
test("exact displayed frame plus all explicit confirmations bind an authorization, no HOME follows",async()=>{
  const r=rig();await r.client.freeze();await assert.rejects(()=>r.client.authorize(),/подтверждения/);
  r.confirm();await r.client.authorize();const sent=r.calls.find(row=>row.path.endsWith("authorize"));
  const body=JSON.parse(sent.body);assert.equal(body.displayed_jpeg_sha256,r.challenge.frame.jpeg_sha256);assert.equal(body.challenge_id,r.challenge.challenge_id);
  assert.equal(body.owner_chassis_test_authorized,false);assert.ok(!("actor_id" in body));assert.ok(!r.calls.some(row=>row.path.endsWith("prepare")));
});
test("wrong boot/frame/SHA and expired frame cannot authorize",async()=>{
  const r=rig();r.options.frame=async()=>({sha256:"c".repeat(64),frame_id:"native-frame",boot_id:"native-boot"});
  await assert.rejects(()=>r.client.freeze(),/отличается/);assert.equal(r.client.snapshot().displayed,false);
  const q=rig();q.confirm();await q.client.freeze();q.setTick(2001);await assert.rejects(()=>q.client.authorize(),/новый native/);
  assert.ok(!q.calls.some(row=>row.path.endsWith("authorize")));
});
test("navigation during late decode or authorization cannot restore a permit",async()=>{
  const r=rig();let decode;r.options.decode=()=>new Promise(resolve=>{decode=resolve;});
  const frozen=r.client.freeze();for(let i=0;i<8;i++)await Promise.resolve();r.client.cancelPending();decode();await assert.rejects(()=>frozen,/прерван/);
  assert.equal(r.client.snapshot().displayed,false);assert.deepEqual(r.revoked,["blob:mock"]);
  const q=rig();q.confirm();await q.client.freeze();let authorize;
  q.setHook(path=>path.endsWith("authorize")?new Promise(resolve=>{authorize=resolve;}):{});
  const pending=q.client.authorize();q.client.cancelPending();authorize({authorization_id:"late-only"});
  await assert.rejects(()=>pending,/отозвано/);assert.equal(q.client.snapshot().activation,null);
  assert.deepEqual(JSON.parse(q.calls.at(-1).body),{expected_authorization_id:"late-only"});
});
test("late permission status after interruption is ignored before native acquisition",async()=>{
  const r=rig();let answer;r.setHook(()=>new Promise(resolve=>{answer=resolve;}));
  const pending=r.client.requirePermission("keyboard");r.client.cancelPending();answer({execution_permission:true,authorization_id:"late",input_mode:"keyboard",chassis_motion_allowed:true});
  await assert.rejects(()=>pending,/прервана/);assert.equal(r.client.chassisAllowed(),false);
});
test("one explicit control click displays its frame and obtains the existing manual permission",async()=>{
 const r=rig();let shown=false;r.options.displayFrame=async url=>{assert.equal(url,'blob:mock');shown=true;};
 await r.client.connect('keyboard');assert.ok(shown);
 const request=r.calls.find(row=>row.path.endsWith('/authorize'));assert.ok(request);
 const body=JSON.parse(request.body);assert.equal(body.input_mode,'keyboard');assert.equal(body.owner_chassis_test_authorized,true);
 assert.ok(!r.calls.some(row=>row.path.endsWith('/prepare')||row.path.endsWith('/begin')));
});
test("explicit first-connect initialization reuses original HOME and then obtains a fresh manual permission",async()=>{
 const r=rig();await r.client.connect('keyboard',{initializeArm:true});
 assert.equal(r.calls.filter(row=>row.path.endsWith('/arm/prepare')).length,1);
 assert.equal(r.calls.filter(row=>row.path.endsWith('/authorize')).length,2);
 const body=JSON.parse(r.calls.find(row=>row.path.endsWith('/arm/prepare')).body);assert.equal(body.acknowledge_factory_home_motion,true);
});
