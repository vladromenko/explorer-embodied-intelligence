"use strict";
// Local transport/DOM fixtures only; no real native HTTP, permission or motion.
const test=require("node:test"),assert=require("node:assert/strict"),{createHash,webcrypto}=require("node:crypto");
const {createCooperativeObservedClient,cooperativeObservedPanel,bindCooperativeObserved}=require("../00-control-center/src/explorer/static/cooperative-observed.js");
const copy=value=>JSON.parse(JSON.stringify(value)),id=letter=>letter.repeat(32),digest=letter=>letter.repeat(64);
const jpeg=new Uint8Array([255,216,83,79,70,84,87,65,82,69,255,217]),checksum=createHash("sha256").update(jpeg).digest("hex");
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function session(changes={}) {return {session_id:id("a"),epoch:"coordinator-epoch-1",process_session:"actual-process-1",status:"ACTIVE",skill_id:"whole-cooperative-skill",skill_version:2,spec_sha256:digest("b"),giver_input_mode:"keyboard",receiver_input_mode:"leader",execution_domain:"observation_only",...changes};}
function setup() {return {schema:"explorer_cooperative_observed_setup_v1",setup_choices:{giver:{choice_id:digest("c"),challenge_supported:true},receiver:{choice_id:digest("d"),challenge_supported:true}},permission_choices:{},blockers:["giver:EXACT_CURRENT_USER_OBSERVED_INPUT_PERMISSION_REQUIRED","receiver:EXACT_CURRENT_COOPERATIVE_PI_USER_GRANT_REQUIRED"],bind_choice_id:null,bind_observed_test_available:false};}
const metadata=role=>({frame_id:"native-"+role+"-frame",jpeg_sha256:checksum,boot_id:"native-"+role+"-boot"});
function challenge(role) {return {role,challenge_choice_id:id(role==="giver"?"1":"2"),frame:metadata(role),frame_url:"/api/cooperative/observed-frame/"+id(role==="giver"?"1":"2")};}
function nativeReceipt(role) {return {role,native_permission_receipt:{[role==="giver"?"authorization_id":"grant_id"]:id(role==="giver"?"3":"4"),execution_permission:true,control_epoch:"native-owned-epoch"},native_command_sent:false,transport_attached:false};}
async function rig(overrides={}) {
  const row=session(),view={selectedSession:row.session_id,stops:{},data:{workbench:{sessions:[row]},selected_session:row,observed_setup:setup()}},calls=[],clock={value:100};
  const options={view:()=>view,crypto:webcrypto,now:()=>clock.value,onChange:()=>{},invoke:async(op,fields)=>{
    calls.push([op,copy(fields)]);
    if(op==="observed_refresh")return copy(view.data.observed_setup);
    if(op==="observed_challenge")return challenge(fields.source_setup_choice_id===digest("c")?"giver":"receiver");
    if(op==="observed_authorize") {const role=fields.challenge_choice_id===id("1")?"giver":"receiver";view.data.observed_setup.permission_choices[role]={permission_choice_id:digest(role==="giver"?"e":"f"),display_observation_supported:role==="receiver",metadata_revoke_supported:true};return nativeReceipt(role);}
    if(op==="bind_observed_test") {row.execution_domain="user_observed_physical_test";return copy(row);}
    if(op==="observed_display")return {role:"receiver",observation_choice_id:id("5"),frame_url:"/api/cooperative/observed-frame/"+id("5"),frame:{frame_id:"native-receiver-new-frame",frame_sha256:checksum,frame_boot_id:"native-receiver-boot"}};
    if(op==="observed_observe")return nativeReceipt("receiver");
    if(op==="observed_revoke")return {revoked:true,native_command_called:false};
    throw new Error("Forbidden operation: "+op);
  },loadFrame:async path=>{calls.push(["FRAME",path]);const giver=path.endsWith(id("1")),observation=path.endsWith(id("5")),role=giver?"giver":"receiver";return {bytes:jpeg,headers:{"x-jpeg-sha256":checksum,"x-frame-id":observation?"native-receiver-new-frame":metadata(role).frame_id,"x-boot-id":metadata(role).boot_id}};},makeObjectURL:()=>"blob:verified-local-fixture",revokeObjectURL:()=>{},...overrides};
  const client=createCooperativeObservedClient(options);client.sync();return {client,view,row,calls,options,clock};
}
async function ready(client,role) {for(const key of role==="giver"?["observing","neutral","space_clear","user_observed_test_confirmed"]:["observing","robot_workspace_checked","user_observed_test_confirmed"])client.affirm(role,key,true);await client.challenge(role);await client.displayed(role,client.snapshot().frames[role].token);}
async function authorize(client,role="receiver") {await ready(client,role);await client.authorize(role);}
function events() {const handlers={};return {handlers,addEventListener:(key,fn)=>handlers[key]=fn,removeEventListener:key=>delete handlers[key]};}

test("construct/sync/render/bind are inert; only explicit refresh invokes metadata source reads",async()=>{
  const {client,calls}=await rig(),root={...events(),contains:()=>true};const cleanup=bindCooperativeObserved(root,client,()=>{}, {document:events(),window:events()});cooperativeObservedPanel(client.snapshot());cleanup();assert.deepEqual(calls,[]);await client.refresh();assert.deepEqual(calls,[["observed_refresh",{}]]);
});
test("challenge and confirmations pin exact actual role choice, displayed SHA and all role-specific flags",async()=>{
  const {client,calls}=await rig();await ready(client,"giver");await client.authorize("giver");assert.deepEqual(calls.find(row=>row[0]==="observed_authorize"),["observed_authorize",{challenge_choice_id:id("1"),displayed_jpeg_sha256:checksum,observing:true,neutral:true,space_clear:true,user_observed_test_confirmed:true}]);assert.equal(client.snapshot().watching,false);assert.ok(!JSON.stringify(calls).includes("owner_chassis_test_authorized"));
});
test("receiver explicit authorization enables watch but performs no begin/resume/action/lease or model input calls",async()=>{
  const {client,calls}=await rig();await authorize(client);assert.equal(client.snapshot().watching,true);assert.ok(calls.every(([op])=>["observed_challenge","observed_authorize","FRAME"].includes(op)));
});
test("flags alone and decoded bytes not actually displayed cannot authorize",async()=>{
  const {client,calls}=await rig();await client.challenge("receiver");for(const key of ["observing","robot_workspace_checked","user_observed_test_confirmed"])client.affirm("receiver",key,true);await assert.rejects(client.authorize("receiver"),/Покажите свежий/);assert.equal(calls.some(([op])=>op==="observed_authorize"),false);
});
test("wrong source role, SHA/header/boot and external image URL never become displayed permission proof",async()=>{
  const wrong=await rig({loadFrame:async()=>({bytes:jpeg,headers:{"x-jpeg-sha256":checksum,"x-frame-id":"wrong-frame","x-boot-id":"native-receiver-boot"}})});await assert.rejects(wrong.client.challenge("receiver"),/не совпадают/);assert.equal(wrong.client.snapshot().challenges.receiver,undefined);
  const {client,options}=await rig();options.invoke=async()=>({...challenge("receiver"),frame_url:"https://other.example/frame.jpg"});await assert.rejects(client.challenge("receiver"),/фиксированный/);assert.equal(client.snapshot().frames.receiver,undefined);
});
test("old displayed challenge cannot be made fresh by another render or display event",async()=>{
  const {client,clock}=await rig();await ready(client,"receiver");clock.value+=1501;await client.displayed("receiver",client.snapshot().frames.receiver.token);await assert.rejects(client.authorize("receiver"),/Покажите свежий/);
});
test("both source grants are metadata-bound only by exact server-advertised ready choice",async()=>{
  const {client,view,calls}=await rig();await assert.rejects(client.bind(),/оба текущих/);await authorize(client,"giver");await authorize(client,"receiver");view.data.observed_setup.bind_observed_test_available=true;view.data.observed_setup.bind_choice_id=digest("9");client.sync();await client.bind();assert.deepEqual(calls.find(([op])=>op==="bind_observed_test"),["bind_observed_test",{observed_choice_id:digest("9")}]);assert.ok(calls.every(([op])=>!["native_begin","native_resume","action","lease"].includes(op)));
});
test("watch refreshes current source choices then uses exact new displayed observation; duplicate load cannot renew twice",async()=>{
  const {client,calls}=await rig();await authorize(client);await client.watchOnce();const before=calls.filter(([op])=>op==="observed_observe").length;assert.equal(before,0);await client.displayed("receiver",client.snapshot().frames.receiver.token);await client.displayed("receiver",client.snapshot().frames.receiver.token);assert.deepEqual(calls.filter(([op])=>op==="observed_observe"),[["observed_observe",{observation_choice_id:id("5"),displayed_jpeg_sha256:checksum,observing:true}]]);
});
test("watch waits for actual display instead of replacing an unshown observation",async()=>{
  const {client,calls}=await rig();await authorize(client);await client.watchOnce();const before=calls.length;assert.deepEqual(await client.watchOnce(),{waiting:true});assert.equal(calls.length,before);
});
test("selected-session/top-server-context mismatch and STOP prevent stale invoke",async()=>{
  const {client,view,row,calls}=await rig();view.data.selected_session={...row,session_id:id("9")};client.sync();await assert.rejects(client.refresh(),/Выберите текущую/);assert.equal(calls.length,0);view.data.selected_session=row;view.stops[row.session_id]={originalEpoch:row.epoch};client.sync();await assert.rejects(client.refresh(),/Выберите текущую/);
});
test("a late authorize reply cannot revive state after same-session coordinator STOP",async()=>{
  const pending=deferred(),{client,options,row,view}=await rig();const invoke=options.invoke;options.invoke=(op,fields)=>op==="observed_authorize"?pending.promise:invoke(op,fields);await ready(client,"receiver");const authorizing=client.authorize("receiver");row.status="STOPPED";row.epoch="stopped-new-epoch";view.stops[row.session_id]={originalEpoch:"coordinator-epoch-1"};client.sync();pending.resolve(nativeReceipt("receiver"));assert.deepEqual(await authorizing,{ignored:true});assert.equal(client.snapshot().watching,false);assert.deepEqual(client.snapshot().receipts,{});
});
test("new selected session/native input/spec/process changes fence all prior frame and permission evidence",async()=>{
  for(const field of ["epoch","process_session","spec_sha256","giver_input_mode","receiver_input_mode"]) {const {client,row}=await rig();await authorize(client);row[field]=field==="spec_sha256"?digest("8"):"changed";client.sync();assert.equal(client.snapshot().watching,false);assert.deepEqual(client.snapshot().frames,{});assert.deepEqual(client.snapshot().receipts,{});}
});
test("page hide/blur and route interrupt fence late renewals; becoming visible never enables watch",async()=>{
  const pending=deferred(),{client,options}=await rig();await authorize(client);await client.watchOnce();const invoke=options.invoke;options.invoke=(op,fields)=>op==="observed_observe"?pending.promise:invoke(op,fields);const observing=client.displayed("receiver",client.snapshot().frames.receiver.token);client.setVisible(false);client.setVisible(true);pending.resolve(nativeReceipt("receiver"));assert.deepEqual(await observing,{ignored:true});assert.equal(client.snapshot().watching,false);assert.deepEqual(client.snapshot().frames,{});
});
test("source revoke bypasses busy and fences late same-role response without touching other role's choice",async()=>{
  const pending=deferred(),{client,options,view,calls}=await rig();await authorize(client,"giver");await client.challenge("giver");for(const key of ["observing","neutral","space_clear","user_observed_test_confirmed"])client.affirm("giver",key,true);await client.displayed("giver",client.snapshot().frames.giver.token);
  const invoke=options.invoke;options.invoke=(op,fields)=>op==="observed_authorize"?pending.promise:invoke(op,fields);const authorizing=client.authorize("giver");await client.revoke("giver");pending.resolve(nativeReceipt("giver"));await authorizing;assert.equal(client.snapshot().receipts.giver,undefined);assert.deepEqual(calls.filter(([op])=>op==="observed_revoke"),[["observed_revoke",{permission_choice_id:view.data.observed_setup.permission_choices.giver.permission_choice_id}]]);
});
test("uncertain authorization has no automatic retry and does not block a genuinely new session after STOP",async()=>{
  const {client,options,row}=await rig();const invoke=options.invoke;options.invoke=(op,fields)=>op==="observed_authorize"?Promise.reject(new Error("lost response")):invoke(op,fields);await ready(client,"receiver");await assert.rejects(client.authorize("receiver"),/lost/);await assert.rejects(client.challenge("receiver"),/неопределённое/);row.epoch="new-intent-epoch";client.sync();assert.deepEqual(client.snapshot().uncertain,[]);
});
test("ordinary binding cleanup preserves current watch; actual lifecycle visibility fences it",async()=>{
  const {client}=await rig();await authorize(client);const root={...events(),contains:()=>true},doc={...events(),visibilityState:"visible"},win=events(),timers=new Set(),env={document:doc,window:win,setTimeout:fn=>{timers.add(fn);return fn;},clearTimeout:fn=>timers.delete(fn)};const cleanup=bindCooperativeObserved(root,client,()=>{},env);cleanup();assert.equal(client.snapshot().watching,true);assert.equal(timers.size,0);const cleanupAgain=bindCooperativeObserved(root,client,()=>{},env);win.handlers.blur();assert.equal(client.snapshot().watching,false);win.handlers.focus();assert.equal(client.snapshot().watching,false);cleanupAgain();
});
test("LAN SHA fallback and optional attached-display callback share the real-byte identity path",async()=>{
  const seen=[],{client}=await rig({crypto:{},displayFrame:async(url,identity)=>{seen.push([url,identity]);return true;}});await client.challenge("giver");assert.equal(client.snapshot().frames.giver.displayed,true);assert.equal(seen[0][1].jpeg_sha256,checksum);assert.equal(seen[0][1].role,"giver");
});
test("render escapes backend/context strings and does not invent chassis or native-control inputs",async()=>{
  const {client,view}=await rig();view.data.observed_setup.blockers=['<img src=x onerror="bad()">'];client.sync();const html=cooperativeObservedPanel(client.snapshot());assert.ok(html.includes("&lt;img"));assert.ok(html.includes("Шасси остаётся неподвижным"));assert.ok(!html.includes("textarea"));assert.ok(!html.includes('data-coop-observed-action="native_begin"'));assert.ok(!html.includes("JSON"));assert.ok(html.includes("Experiment 06"));
});
test("another role's in-flight metadata read defers real displayed renewal without discarding observation",async()=>{
  const pending=deferred(),{client,options,calls}=await rig();await authorize(client);await client.watchOnce();const token=client.snapshot().frames.receiver.token,invoke=options.invoke;options.invoke=(op,fields)=>op==="observed_challenge"?pending.promise:invoke(op,fields);
  const giverRead=client.challenge("giver");assert.deepEqual(await client.displayed("receiver",token),{waiting:true});assert.equal(client.snapshot().watching,true);assert.equal(calls.some(([op])=>op==="observed_observe"),false);pending.resolve(challenge("giver"));await giverRead;await client.watchOnce();assert.equal(calls.filter(([op])=>op==="observed_observe").length,1);
});
test("locally blocked unsent authorize is not mislabeled as uncertain remote authorization",async()=>{
  const pending=deferred(),{client,options}=await rig();await ready(client,"giver");const invoke=options.invoke;options.invoke=(op,fields)=>op==="observed_refresh"?pending.promise:invoke(op,fields);const refreshing=client.refresh();await assert.rejects(client.authorize("giver"),/Дождитесь/);assert.deepEqual(client.snapshot().uncertain,[]);pending.resolve(setup());await refreshing;
});

async function nativeRig() {
  const r=await rig(),nativeCalls=[];
  const operations=()=>Object.fromEntries(['native_begin','native_resume','native_recenter','native_attach','native_lease','native_stop','native_reset_safe','native_phase_activate'].map(op=>[op,{implemented:true,available:true,choice_id:digest('a')}]));
  r.controls={schema:'explorer_cooperative_native_controls_v1',roles:{giver:{operations:operations()},receiver:{operations:operations()}}};
  for(const op of ['native_resume','native_recenter','native_lease'])r.controls.roles.receiver.operations[op]={implemented:false,available:false,choice_id:null};
  r.options.invokeNative=async(op,fields)=>{nativeCalls.push([op,copy(fields)]);return op==='native_refresh'?copy(r.controls):{role:fields.role,operation:op,native_controls:copy(r.controls),API_result_claims_hardware_attainment:false};};
  return {...r,nativeCalls};
}
function nativeAffirm(client,role='giver') {for(const key of ['observing','user_observed_test_confirmed','neutral'])client.nativeAffirm(role,key,true);}

test('native rendering/observed watch never refreshes or starts control; separate native refresh is read only',async()=>{
  const {client,nativeCalls,calls}=await nativeRig();cooperativeObservedPanel(client.snapshot());client.sync();assert.deepEqual(nativeCalls,[]);await client.refresh();assert.deepEqual(nativeCalls,[]);await client.nativeRefresh();assert.deepEqual(nativeCalls,[['native_refresh',{}]]);assert.deepEqual(calls,[['observed_refresh',{}]]);
});
test('native actions consume one server choice and separate confirmations, then reset flags without auto leases',async()=>{
  const {client,nativeCalls}=await nativeRig();await client.nativeRefresh();await assert.rejects(client.nativeCommand('giver','native_begin'),/Лично подтвердите/);nativeAffirm(client);await client.nativeCommand('giver','native_begin');assert.deepEqual(nativeCalls.at(-1),['native_begin',{role:'giver',native_control_choice_id:digest('a'),observing:true,user_observed_test_confirmed:true,neutral:true}]);assert.deepEqual(client.snapshot().nativeFlags.giver,{observing:false,user_observed_test_confirmed:false,neutral:false,object_safe_no_dual_hold:false});assert.equal(nativeCalls.length,2);await assert.rejects(client.nativeCommand('giver','native_resume'),/Лично подтвердите/);
});
test('unimplemented Pi operations, unadvertised action and stale native choices cannot issue requests',async()=>{
  const {client,nativeCalls,clock}=await nativeRig();await client.nativeRefresh();nativeAffirm(client,'receiver');await assert.rejects(client.nativeCommand('receiver','native_resume'),/недоступно/);await assert.rejects(client.nativeCommand('receiver','action'),/реализованное/);clock.value+=1501;await assert.rejects(client.nativeCommand('receiver','native_begin'),/недоступно/);assert.deepEqual(nativeCalls,[['native_refresh',{}]]);
});
test('native recenter requires neutral again, success does not automatically resume',async()=>{
  const {client,nativeCalls}=await nativeRig();await client.nativeRefresh();client.nativeAffirm('giver','observing',true);client.nativeAffirm('giver','user_observed_test_confirmed',true);await assert.rejects(client.nativeCommand('giver','native_recenter'),/нейтральный/);client.nativeAffirm('giver','neutral',true);await client.nativeCommand('giver','native_recenter');assert.equal(nativeCalls.length,2);assert.equal(nativeCalls.at(-1)[0],'native_recenter');assert.equal(nativeCalls.some(([op])=>op==='native_resume'),false);
});
test('unknown native reply is retained as uncertain; refresh never retries and only explicit exact stop can resolve it',async()=>{
  const r=await nativeRig();await r.client.nativeRefresh();nativeAffirm(r.client);const invoke=r.options.invokeNative;r.options.invokeNative=async(op,fields)=>{if(op==='native_begin'){r.nativeCalls.push([op,fields]);throw new Error('unknown native return');}return invoke(op,fields);};await assert.rejects(r.client.nativeCommand('giver','native_begin'),/unknown/);assert.deepEqual(r.client.snapshot().nativeUncertain,['giver']);await r.client.nativeRefresh();nativeAffirm(r.client);await assert.rejects(r.client.nativeCommand('giver','native_resume'),/не подтверждён/);assert.equal(r.nativeCalls.filter(([op])=>op==='native_begin').length,1);await r.client.nativeCommand('giver','native_stop');assert.deepEqual(r.client.snapshot().nativeUncertain,[]);
});
test('blur while native request is pending drops its late controls and never reuses old flags on focus',async()=>{
  const r=await nativeRig(),pending=deferred();await r.client.nativeRefresh();nativeAffirm(r.client);r.options.invokeNative=async()=>pending.promise;const operation=r.client.nativeCommand('giver','native_begin');r.client.setVisible(false);pending.resolve({role:'giver',operation:'native_begin',native_controls:r.controls});assert.deepEqual(await operation,{ignored:true});r.client.setVisible(true);assert.equal(r.client.snapshot().nativeControls,null);assert.equal(r.client.snapshot().nativeFlags.giver.observing,false);await assert.rejects(r.client.nativeCommand('giver','native_begin'),/недоступно/);
});


async function receiverRig() {
  const r=await rig(),receiverCalls=[];
  r.setup={schema:'explorer_cooperative_receiver_setup_v1',operations:Object.fromEntries(['bind_guard','capture_empty_open','capture_empty_closed','reset_safe','adopt_hold'].map(op=>[op,{implemented:true,available:true,choices:[{choice_id:digest('a'),label:op}]}]))};
  r.options.invokeReceiverSetup=async(op,fields)=>{receiverCalls.push([op,copy(fields)]);return op==='receiver_setup_refresh'?copy(r.setup):{operation:op.replace('receiver_setup_',''),native_command_sent:false,native_lease_or_user_observation_renewed:false,physical_execution_authorized:false};};
  return {...r,receiverCalls};
}
function receiverAffirm(client,...keys) {for(const key of ['observing','user_observed_test_confirmed',...keys])client.nativeAffirm('receiver',key,true);}
test('receiver setup is inert on render and explicit metadata choices consume separate confirmations',async()=>{
  const r=await receiverRig();cooperativeObservedPanel(r.client.snapshot());r.client.sync();assert.deepEqual(r.receiverCalls,[]);
  await r.client.receiverRefresh();await assert.rejects(r.client.receiverCommand('bind_guard',digest('a')),/Лично подтвердите/);
  receiverAffirm(r.client);await r.client.receiverCommand('bind_guard',digest('a'));
  assert.deepEqual(r.receiverCalls,[['receiver_setup_refresh',{}],['receiver_setup_bind_guard',{receiver_setup_choice_id:digest('a'),observing:true,user_observed_test_confirmed:true}]]);
  assert.equal(r.client.snapshot().receiverSetup,null);assert.equal(r.client.snapshot().nativeFlags.receiver.observing,false);
});
test('empty endpoints require actual newly displayed observation SHA and never accept just decoded image',async()=>{
  const r=await receiverRig();await authorize(r.client);await r.client.displayObservation();await r.client.receiverRefresh();receiverAffirm(r.client,'empty_gripper_confirmed');
  await assert.rejects(r.client.receiverCommand('capture_empty_open',digest('a')),/покажите свежий/);
  await r.client.displayed('receiver',r.client.snapshot().frames.receiver.token);await r.client.receiverCommand('capture_empty_open',digest('a'));
  assert.deepEqual(r.receiverCalls.at(-1),['receiver_setup_capture_empty_open',{receiver_setup_choice_id:digest('a'),observing:true,user_observed_test_confirmed:true,empty_gripper_confirmed:true,observation_choice_id:id('5'),displayed_jpeg_sha256:checksum}]);
  assert.equal(r.receiverCalls.length,2);
});
test('receiver setup rejects stale choices, unknown operations, missing safe-object affirmation and late blur reply',async()=>{
  const r=await receiverRig();await r.client.receiverRefresh();receiverAffirm(r.client);await assert.rejects(r.client.receiverCommand('reset_safe',digest('a')),/состояние предмета/);
  receiverAffirm(r.client,'object_safe_no_dual_hold');r.clock.value+=1501;await assert.rejects(r.client.receiverCommand('reset_safe',digest('a')),/Обновите/);
  await r.client.receiverRefresh();await assert.rejects(r.client.receiverCommand('native_begin',digest('a')),/Обновите/);
  const pending=deferred();r.options.invokeReceiverSetup=async()=>pending.promise;const reading=r.client.receiverRefresh();r.client.setVisible(false);pending.resolve(r.setup);assert.deepEqual(await reading,{ignored:true});r.client.setVisible(true);assert.equal(r.client.snapshot().receiverSetup,null);
  assert.equal(r.receiverCalls.length,2);
});


test('receiver begin discards a pending previous-epoch frame while continuous USER watching requires a fresh display',async()=>{
  const r=await nativeRig();await authorize(r.client);await r.client.displayObservation();const oldToken=r.client.snapshot().frames.receiver.token;
  await r.client.nativeRefresh();nativeAffirm(r.client,'receiver');await r.client.nativeCommand('receiver','native_begin');
  assert.equal(r.client.snapshot().watching,true);assert.equal(r.client.snapshot().frames.receiver,undefined);
  assert.deepEqual(await r.client.displayed('receiver',oldToken),{ignored:true});
  assert.equal(r.calls.some(([op])=>op==='observed_observe'),false);
  await r.client.watchOnce();assert.notEqual(r.client.snapshot().frames.receiver.token,oldToken);
  await r.client.displayed('receiver',r.client.snapshot().frames.receiver.token);
  assert.equal(r.calls.filter(([op])=>op==='observed_observe').length,1);
  assert.deepEqual(r.nativeCalls.map(([op])=>op),['native_refresh','native_begin']);
  await r.client.nativeRefresh();nativeAffirm(r.client,'receiver');await r.client.nativeCommand('receiver','native_stop');
  assert.equal(r.client.snapshot().watching,false);assert.equal(r.client.snapshot().frames.receiver,undefined);
});
test('uncertain receiver start cannot keep renewing observation or reuse a pending old image',async()=>{
  const r=await nativeRig();await authorize(r.client);await r.client.displayObservation();const token=r.client.snapshot().frames.receiver.token;
  await r.client.nativeRefresh();nativeAffirm(r.client,'receiver');r.options.invokeNative=async()=>{throw new Error('unknown receiver start reply');};
  await assert.rejects(r.client.nativeCommand('receiver','native_begin'),/unknown receiver/);
  assert.equal(r.client.snapshot().watching,false);assert.equal(r.client.snapshot().frames.receiver,undefined);
  assert.deepEqual(r.client.snapshot().nativeUncertain,['receiver']);assert.deepEqual(await r.client.displayed('receiver',token),{ignored:true});
  assert.deepEqual(await r.client.watchOnce(),{ignored:true});assert.equal(r.calls.some(([op])=>op==='observed_observe'),false);
});


test('held observation adoption uses only opaque choice and flags and never starts or renews native control',async()=>{
 const r=await receiverRig();await r.client.receiverRefresh();receiverAffirm(r.client);await r.client.receiverCommand('adopt_hold',digest('a'));
 assert.deepEqual(r.receiverCalls,[['receiver_setup_refresh',{}],['receiver_setup_adopt_hold',{receiver_setup_choice_id:digest('a'),observing:true,user_observed_test_confirmed:true}]]);
 assert.equal(r.client.snapshot().watching,false);assert.equal(r.client.snapshot().receiverSetup,null);
});


test('metadata receipt uses exact authenticated artifact link and SHA, fenced on context loss',async()=>{
 const r=await receiverRig(),invoke=r.options.invokeReceiverSetup,receipt={receipt_id:id('a'),sha256:digest('b'),url:'/api/cooperative/artifacts/receiver-metadata/'+id('a')};
 r.options.invokeReceiverSetup=async(op,fields)=>{const result=await invoke(op,fields);return op==='receiver_setup_refresh'?result:{...result,metadata_receipt:receipt};};
 await r.client.receiverRefresh();receiverAffirm(r.client);await r.client.receiverCommand('adopt_hold',digest('a'));
 assert.deepEqual(r.client.snapshot().receiverReceipt,receipt);assert.match(cooperativeObservedPanel(r.client.snapshot()),/Сохранённая проверка SO101/);
 const forged=r.client.snapshot();forged.receiverReceipt.url='javascript:move()';assert.ok(!cooperativeObservedPanel(forged).includes('javascript:'));
 r.client.setVisible(false);assert.equal(r.client.snapshot().receiverReceipt,null);
});


test('giver safe reset consumes fresh explicit object-safe confirmation and never calls begin or resume',async()=>{
 const r=await nativeRig();await r.client.nativeRefresh();nativeAffirm(r.client);await assert.rejects(r.client.nativeCommand('giver','native_reset_safe'),/Лично подтвердите/);
 r.client.nativeAffirm('giver','object_safe_no_dual_hold',true);await r.client.nativeCommand('giver','native_reset_safe');
 assert.deepEqual(r.nativeCalls.at(-1),['native_reset_safe',{role:'giver',native_control_choice_id:digest('a'),observing:true,user_observed_test_confirmed:true,object_safe_no_dual_hold:true}]);
 assert.equal(r.client.snapshot().nativeFlags.giver.object_safe_no_dual_hold,false);assert.deepEqual(r.nativeCalls.map(([op])=>op),['native_refresh','native_reset_safe']);
});


test('receiver phase activation requires new neutral confirmation, uses only an opaque choice and fences old frames',async()=>{
 const r=await nativeRig();await authorize(r.client);await r.client.displayObservation();const oldToken=r.client.snapshot().frames.receiver.token;await r.client.nativeRefresh();r.client.nativeAffirm('receiver','observing',true);r.client.nativeAffirm('receiver','user_observed_test_confirmed',true);
 await assert.rejects(r.client.nativeCommand('receiver','native_phase_activate'),/Лично подтвердите/);
 assert.equal(r.nativeCalls.length,1);r.client.nativeAffirm('receiver','neutral',true);await r.client.nativeCommand('receiver','native_phase_activate');
 assert.deepEqual(r.nativeCalls.at(-1),['native_phase_activate',{role:'receiver',native_control_choice_id:digest('a'),observing:true,user_observed_test_confirmed:true,neutral:true}]);
 assert.equal(r.client.snapshot().frames.receiver,undefined);assert.deepEqual(await r.client.displayed('receiver',oldToken),{ignored:true});assert.equal(r.client.snapshot().nativeFlags.receiver.neutral,false);
 assert.equal(r.nativeCalls.length,2);assert.equal(r.calls.some(([op])=>op==='observed_observe'),false);
});
