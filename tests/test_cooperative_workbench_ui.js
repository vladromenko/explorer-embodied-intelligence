"use strict";
// Pure HTTP/DOM fixtures only. No robot connection, camera owner or physical IO.
const test = require("node:test"), assert = require("node:assert/strict");
const {createCooperativeWorkbenchClient,cooperativeWorkbenchPanel,bindCooperativeWorkbench} = require("../00-control-center/src/explorer/static/cooperative-workbench.js");
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => {let resolve,reject;const promise = new Promise((yes,no) => {resolve=yes;reject=no;});return {promise,resolve,reject};};
function fixture() {
  const giver = {choice_id:"server-giver",name:"Предмет в общую зону",confirmed:true,reference:{skill_id:"giver-skill",skill_version:2,spec_sha256:"a".repeat(64),experiment_slug:"rosmaster_leader_learning"},phase_ids:["approach","grasp"],native_variants:[{variant_id:"giver-variant",title:"Сцена1"}],models:[{model_id:"giver-model",status:"shadow_only"}]};
  const receiver = {choice_id:"server-receiver",name:"Принять и положить",confirmed:true,reference:{skill_id:"receiver-skill",skill_version:3,spec_sha256:"b".repeat(64),experiment_slug:"so101_source_learning"},phase_ids:["receive","place"],native_variants:[{variant_id:"receiver-variant"}],models:[]};
  const spec = {skill_id:"cooperative-skill",version:1,spec_sha256:"c".repeat(64),name:"Передать конфету",object_description:"Конфета1",receiver_goal_description:"В коробку",outcome_criteria:["Передана без падения"],mode:"both_manual",subskills:{giver:giver.reference,receiver:receiver.reference}};
  const session = {session_id:"server-session",skill_id:spec.skill_id,skill_version:spec.version,spec_sha256:spec.spec_sha256,epoch:"epoch-one",scene_id:"Стол1",object_instance_id:"Конфета1",split:"development",execution_domain:"observation_only",mode:"both_manual",status:"READY_FOR_OBSERVED_STEPS",protocol_state:"PREPARE",legal_events:["giver_holding"],recording_id:null,last_recording_id:null,readiness:{metadata_and_recording_blockers:[],observed_execution_blockers:["NO_CURRENT_OBSERVED_AUTHORITY"],raw_two_camera_recording_available:true}};
  return {workbench:{skills:[{spec,confirmed:true}],sessions:[session]},catalogue:{phases:["PREPARE","SO101_FINISH"]},native_choices:{giver:[giver],receiver:[receiver]},pending_evidence:[],cameras:{giver:{preview_url:"/api/cameras/nano-rgbd/snapshot",available:true},receiver:{preview_url:"/api/cameras/pi-overview/snapshot",available:true}},native_episode_choices:{"server-session":{giver:[{choice_id:"original-giver",name:"Исходный ROSMASTER"}],receiver:[{choice_id:"original-receiver",name:"Исходный SO101"}]}}};
}
async function rig(overrides = {}) {
  const calls=[], state=fixture();const client=createCooperativeWorkbenchClient({get:async path => {calls.push(["GET",path]);return clone(state);},post:async (path,body) => {calls.push(["POST",path,body]);return {result:{ready:false,blockers:["ACTUAL_SOURCE_MISSING"]},snapshot:clone(state)};},...overrides});
  await client.refresh();client.chooseSession("server-session");return {client,calls,state};
}
test("construct/render/bind perform no requests; explicit refresh is GET only",async () => {
  const calls=[];const client=createCooperativeWorkbenchClient({get:async path => {calls.push(path);return fixture();},post:async () => {throw new Error("implicit POST forbidden");}});
  const handlers=new Map(),root={addEventListener:(id,fn)=>handlers.set(id,fn),removeEventListener:id=>handlers.delete(id),contains:()=>true};
  const unbind=bindCooperativeWorkbench(root,client);cooperativeWorkbenchPanel(client.snapshot());assert.equal(calls.length,0);unbind();assert.equal(handlers.size,0);
  await client.refresh();assert.deepEqual(calls,["/api/cooperative"]);
});
test("proposals resolve only actual server choices/models/phases and are never auto-confirmed",async () => {
  const {client,calls}=await rig();client.edit("object_description","Синяя конфета");client.edit("receiver_goal_description","Положить в коробку");client.edit("outcome_criteria","Нет падения\nПредмет в коробке");
  assert.throws(()=>client.propose(),/обе реальные/);client.edit("giver_choice_id","server-giver");client.edit("receiver_choice_id","server-receiver");client.edit("phase_maps.giver.PREPARE","approach");client.edit("giver_model_id","giver-model");await client.propose();
  const post=calls.at(-1);assert.equal(post[1],"/api/cooperative/propose_skill");assert.equal(post[2].giver_choice_id,"server-giver");assert.deepEqual(post[2].outcome_criteria,["Нет падения","Предмет в коробке"]);assert.deepEqual(post[2].giver_phase_map,{PREPARE:"approach"});assert.equal(post[2].giver_model_id,"giver-model");assert.ok(!("subskills" in post[2]));assert.ok(!calls.some(row=>row[1].includes("confirm_skill")));
  client.edit("phase_maps.giver.PREPARE","invented");assert.throws(()=>client.propose(),/реальные исходные фазы/);
});
test("confirm pins exact current immutable spec SHA",async () => {
  const {client,calls}=await rig();await client.confirm();assert.deepEqual(calls.at(-1)[2],{skill_id:"cooperative-skill",version:1,spec_sha256:"c".repeat(64)});
});
test("drafts survive refresh; source choice change clears only its own model/phase binding",async () => {
  const {client}=await rig();client.edit("object_description","Свой сохранённый черновик");client.edit("phase_maps.receiver.PREPARE","receive");client.edit("phase_maps.giver.PREPARE","grasp");client.edit("giver_model_id","giver-model");client.edit("giver_choice_id","server-giver");await client.refresh();
  const draft=client.snapshot().draft;assert.equal(draft.object_description,"Свой сохранённый черновик");assert.deepEqual(draft.phase_maps.giver,{});assert.equal(draft.giver_model_id,"");assert.equal(draft.phase_maps.receiver.PREPARE,"receive");
});
test("session requires actual inputs and stationary confirmation; sealed partition remains sealed",async () => {
  const {client,calls}=await rig();client.edit("scene","Стол за ширмой");client.edit("object_instance","Зелёный предмет2");assert.throws(()=>client.createSession(),/неподвижность/);client.edit("stationary_chassis_confirmed",true);client.edit("split","sealed_test");await client.createSession();const body=calls.at(-1)[2];assert.equal(body.split,"sealed_test");assert.equal(body.execution_domain,"observation_only");assert.equal(body.stationary_chassis_confirmed,true);
  client.edit("execution_domain","user_observed_physical_test");assert.throws(()=>client.createSession(),/не разрешён сервером/);client.edit("execution_domain","shadow");client.edit("giver_input_mode","policy");assert.throws(()=>client.createSession(),/подтверждённого навыка/);
});
test("FSM buttons and requests use server legal event list and exact current epoch",async () => {
  const {client,calls}=await rig();assert.throws(()=>client.step("release_requested"),/недоступен/);await client.step("giver_holding");assert.deepEqual(calls.at(-1)[2],{session_id:"server-session",event:"giver_holding",expected_epoch:"epoch-one"});
  const html=cooperativeWorkbenchPanel(client.snapshot());assert.ok(html.includes('data-coop-event="giver_holding"'));assert.ok(!html.includes('data-coop-event="release_requested"'));
});
test("STOP bypasses in-flight step; late step cannot revive old epoch",async () => {
  const pending=deferred(),state=fixture(),calls=[];
  const client=createCooperativeWorkbenchClient({get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);if(path.endsWith("/step"))return pending.promise;const stopped=clone(state);stopped.workbench.sessions[0].status="STOPPED";stopped.workbench.sessions[0].epoch="epoch-two";return {result:{stopped:true},snapshot:stopped};}});
  await client.refresh();client.chooseSession("server-session");const step=client.step("giver_holding");assert.equal(client.snapshot().busy,true);await client.stop();pending.resolve({result:{accepted:true},snapshot:clone(state)});assert.deepEqual(await step,{ignored:true});
  assert.equal(client.snapshot().data.workbench.sessions[0].status,"STOPPED");assert.equal(client.snapshot().stops["server-session"].acknowledged,true);assert.throws(()=>client.step("giver_holding"),/После STOP/);assert.equal(calls.length,2);
});
test("late GET and failed STOP cannot restore enabled controls",async () => {
  const waiting=deferred(),state=fixture();let gets=0;
  const client=createCooperativeWorkbenchClient({get:async()=>++gets===1?clone(state):waiting.promise,post:async()=>{throw new Error("network unknown");}});
  await client.refresh();client.chooseSession("server-session");const refresh=client.refresh();await assert.rejects(client.stop(),/network unknown/);waiting.resolve(clone(state));await refresh;
  assert.ok(client.snapshot().stops["server-session"]);assert.equal(client.snapshot().stops["server-session"].acknowledged,false);assert.throws(()=>client.step("giver_holding"),/После STOP/);assert.match(cooperativeWorkbenchPanel(client.snapshot()),/ожидается подтверждение сервера/);
});
test("operator evidence requires current challenge/run and two explicit booleans, actor is never client input",async () => {
  const state=fixture();state.pending_evidence=[{challenge_id:"challenge",run_id:"server-session",state:"pending",question:"Получатель удерживает предмет?",deadline_ns:1}];const posts=[];
  const {client}=await rig({get:async()=>clone(state),post:async(path,body)=>{posts.push([path,body]);return {result:{},snapshot:clone(state)};}});
  assert.throws(()=>client.confirmEvidence("challenge"),/конкретный текущий/);client.edit("evidence.challenge.observing",true);client.edit("evidence.challenge.affirmed",true);await client.confirmEvidence("challenge");assert.deepEqual(posts[0][1],{challenge_id:"challenge",observing:true,affirmed:true,note:""});assert.ok(!("actor" in posts[0][1]));assert.throws(()=>client.confirmEvidence("foreign-challenge"));
});
test("recording unknown outcome is retained; success cannot be inferred from FSM or closed gripper",async () => {
  const state=fixture();state.workbench.sessions[0].recording_id="active-original";state.workbench.sessions[0].protocol_completed=true;const posts=[];
  const {client}=await rig({get:async()=>clone(state),post:async(path,body)=>{posts.push([path,body]);return {result:{},snapshot:clone(state)};}});
  await client.recordFinish();assert.equal(posts[0][1].outcome.status,"unknown");assert.equal(posts[0][1].outcome.native_roles.giver.status,"unknown");assert.equal(posts[0][1].outcome.native_roles.receiver.independent_of_action_commands,false);
  client.edit("finish.status","success");assert.throws(()=>client.recordFinish(),/независимого наблюдения/);
});
test("review uses only server choices, explicit per-phase outcomes, and preserves unknown",async () => {
  const state=fixture();state.workbench.sessions[0].last_recording_id="saved-original";const posts=[];
  const {client}=await rig({get:async()=>clone(state),post:async(path,body)=>{posts.push([path,body]);return {result:{},snapshot:clone(state)};}});
  client.edit("review.whole_episode_seen",true);client.edit("review.phase_outcomes.PREPARE","success");client.edit("review.phase_outcomes.SO101_FINISH","unknown");client.edit("review.native_episode_choice_ids.giver","invented-ID");client.edit("review.native_episode_choice_ids.receiver","original-receiver");assert.throws(()=>client.review(),/обе реальные/);client.edit("review.native_episode_choice_ids.giver","original-giver");await client.review();
  assert.equal(posts[0][1].phase_outcomes.SO101_FINISH,"unknown");assert.equal(posts[0][1].outcome,"unknown");assert.deepEqual(posts[0][1].native_episode_choice_ids,{giver:"original-giver",receiver:"original-receiver"});
});
test("aggregate/training plan/shadow are separate explicit calls, without browser RGB/state or dispatch",async () => {
  const state=fixture();state.queued_native_shadow_supported=true;state.workbench.skills[0].spec.subskills.giver.model_id="giver-model";state.workbench.skills[0].spec.subskills.receiver.model_id="receiver-model";const calls=[];const {client}=await rig({uuid:()=>"11111111-1111-4111-8111-111111111111",get:async()=>clone(state),post:async(path,body)=>{calls.push(["POST",path,body]);return {result:{ready:false},snapshot:clone(state)};}});await client.aggregate();await client.trainingPlan();await client.shadow();const posts=calls.filter(row=>row[0]==="POST");assert.deepEqual(posts.map(row=>row[1]),["/api/cooperative/aggregate","/api/cooperative/training_plan","/api/cooperative/shadow"]);assert.deepEqual(posts[2][2],{session_id:"server-session",expected_epoch:"epoch-one",request_id:"11111111-1111-4111-8111-111111111111"});assert.ok(posts.every(row=>!JSON.stringify(row[2]).includes("action9")));
});
test("server immutable-version drift cannot silently re-pin confirm",async () => {
  const state=fixture();const {client}=await rig({get:async()=>clone(state)});state.workbench.skills[0].spec.spec_sha256="d".repeat(64);await client.refresh();assert.throws(()=>client.confirm(),/точную версию/);
});
test("queued shadow retries retain request ID across reload; a new successful click gets a new ID",async () => {
  const state=fixture(), posts=[], values=new Map();let ids=0,fail=true;
  state.queued_native_shadow_supported=true;state.model_adapters_bound={giver:false,receiver:false};
  state.workbench.skills[0].spec.subskills.giver.model_id="native9-model";
  state.workbench.skills[0].spec.subskills.receiver.model_id="native6-model";
  const options={storage:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)},
    uuid:()=>`${String(++ids).padStart(8,"0")}-1111-4111-8111-111111111111`,get:async()=>clone(state),
    post:async(path,body)=>{posts.push([path,body]);if(fail)throw new Error("reply lost after possible commit");return {result:{complete_native_reservation:true},snapshot:clone(state)};}};
  const first=createCooperativeWorkbenchClient(options);await first.refresh();first.chooseSession("server-session");
  await assert.rejects(first.shadow(),/reply lost/);assert.equal(posts.length,1);assert.equal(ids,1);
  const second=createCooperativeWorkbenchClient(options);await second.refresh();assert.equal(posts.length,1);
  fail=false;await second.shadow();assert.equal(posts[0][1].request_id,posts[1][1].request_id);assert.equal(ids,1);
  await second.shadow();assert.notEqual(posts[2][1].request_id,posts[1][1].request_id);assert.equal(ids,2);
  assert.ok(posts.every(([path,body])=>path.endsWith("/shadow") && !body.action9 && !body.authorization_id));
});
test("queued shadow status is visible without claiming synchronized or physical execution",async () => {
  const {client}=await rig();const view=client.snapshot();view.data.queued_native_shadows=[{
    current_session_epoch:true,native_heads:{giver:{job_id:"abc12345",status:"completed",verified_fresh_shadow_at_worker_time:true},
      receiver:{job_id:"def12345",status:"failed",evidence_error:"Source <changed>"}},jobs:{},errors:{}}];
  const html=cooperativeWorkbenchPanel(view);assert.match(html,/abc12345/);assert.match(html,/failed/);
  assert.match(html,/Source &lt;changed&gt;/);assert.match(html,/часы роботов не синхронизированы/);
  assert.match(html,/физическое исполнение этим не подтверждаются/);
});
test("renderer escapes user/backend text, blocks external/script image URLs and has no raw ID/JSON editor",async () => {
  const {client}=await rig();const view=client.snapshot();view.error='<script>move()</script>';view.data.cameras.giver.preview_url="javascript:move()";view.data.workbench.skills[0].spec.name='"><img src=x onerror=move()>';
  const html=cooperativeWorkbenchPanel(view);assert.ok(!html.includes("<script>"));assert.ok(html.includes("&lt;script&gt;"));assert.ok(!html.includes('src="javascript:'));assert.ok(html.includes("Источник камеры не предоставлен сервером"));assert.ok(!html.includes('name="session_id"'));assert.ok(!html.includes("<pre>"));assert.ok(html.includes("ROSMASTER9D"));assert.ok(html.includes("SO1016D"));assert.ok(html.includes('data-coop-action="stop"'));
});
test("DOM teardown and disabled or outside buttons never emit requests",async () => {
  const {client,calls}=await rig();const listeners={};const root={addEventListener:(name,handler)=>listeners[name]=handler,removeEventListener:name=>delete listeners[name],contains:target=>target.inside===true};let errors=0;
  const teardown=bindCooperativeWorkbench(root,client,()=>errors++),before=calls.length;
  listeners.click({target:{closest:()=>({inside:true,disabled:true,dataset:{coopAction:"step",coopEvent:"giver_holding"}})}});listeners.click({target:{closest:()=>({inside:false,dataset:{coopAction:"stop"}})}});assert.equal(calls.length,before);teardown();assert.deepEqual(listeners,{});assert.equal(errors,0);
});
test("recording boundaries pin original recording ID, not only session epoch",async () => {
  const state=fixture();state.workbench.sessions[0].recording_id="recording-before-tab-change";const calls=[];
  const {client}=await rig({get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);return {result:{},snapshot:clone(state)};}});
  client.edit("record_phase","PREPARE");await client.recordStage();await client.recordFinish();assert.ok(calls.every(([,body])=>body.recording_id==="recording-before-tab-change" && body.expected_epoch==="epoch-one"));
});
test("a GET started during STOP cannot overwrite its later acknowledged response",async () => {
  const stop=deferred(),read=deferred(),state=fixture();let gets=0;
  const client=createCooperativeWorkbenchClient({get:async()=>++gets===1?clone(state):read.promise,post:async()=>stop.promise});
  await client.refresh();client.chooseSession("server-session");const stopping=client.stop();const fetching=client.refresh();const stopped=clone(state);stopped.workbench.sessions[0].status="STOPPED";stopped.workbench.sessions[0].epoch="next-epoch";stop.resolve({result:{},snapshot:stopped});await stopping;read.resolve(clone(state));await fetching;assert.equal(client.snapshot().data.workbench.sessions[0].status,"STOPPED");
});
test("server-persisted STOPPED sessions stay stopped after client/page recreation",async () => {
  const state=fixture();state.workbench.sessions[0].status="STOPPED";const {client}=await rig({get:async()=>clone(state)});assert.throws(()=>client.step("giver_holding"),/После STOP/);assert.throws(()=>client.recordStart(),/После STOP/);assert.match(cooperativeWorkbenchPanel(client.snapshot()),/старая сессия не возобновляется/);
});
test("page recreation preserves text drafts and failed-STOP latch but never old observation affirmations",async () => {
  const state=fixture(),values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
  const options={storage,get:async()=>clone(state),post:async()=>{throw new Error("unknown network outcome");}};
  const first=createCooperativeWorkbenchClient(options);await first.refresh();first.chooseSession("server-session");first.edit("object_description","Сохранённый предмет");first.edit("zone_observing",true);first.edit("finish.observed",true);first.edit("evidence.old.affirmed",true);await assert.rejects(first.stop());
  const reloaded=createCooperativeWorkbenchClient(options);await reloaded.refresh();const view=reloaded.snapshot();assert.equal(view.draft.object_description,"Сохранённый предмет");assert.equal(view.draft.zone_observing,false);assert.equal(view.draft.finish.observed,false);assert.deepEqual(view.draft.evidence,{});assert.throws(()=>reloaded.step("giver_holding"),/После STOP/);
});
test("new recording and source original reset prior success labels rather than count them again",async () => {
  const state=fixture();state.workbench.sessions[0].recording_id="first-recording";state.workbench.sessions[0].last_recording_id="prior-original";const {client}=await rig({get:async()=>clone(state)});
  client.edit("finish.status","success");client.edit("finish.observed",true);client.edit("review.status","success");client.edit("review.whole_episode_seen",true);client.edit("review.phase_outcomes.PREPARE","success");
  await client.refresh();assert.equal(client.snapshot().draft.finish.status,"success");assert.equal(client.snapshot().draft.review.status,"success");
  state.workbench.sessions[0].recording_id="second-recording";state.workbench.sessions[0].last_recording_id="new-original";await client.refresh();assert.equal(client.snapshot().draft.finish.status,"unknown");assert.equal(client.snapshot().draft.finish.observed,false);assert.equal(client.snapshot().draft.review.status,"unknown");assert.deepEqual(client.snapshot().draft.review.phase_outcomes,{});
});
test("native giver success requires explicit exact-source phases and separate visible/full-task review",async () => {
  const state=fixture();state.workbench.sessions[0].recording_id="native-review-original";const calls=[];const {client}=await rig({uuid:()=>"11111111-1111-4111-8111-111111111111",get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);return {result:{},snapshot:clone(state)};}});
  client.edit("finish.observed",true);client.edit("finish.giver","success");assert.throws(()=>client.recordFinish(),/исходного навыка/);
  client.edit("finish.giver_visible",true);client.edit("finish.giver_full_task",true);client.edit("finish.giver_phases.approach",true);client.edit("finish.giver_phases.grasp",true);await client.recordFinish();assert.deepEqual(calls[0][1].outcome.native_roles.giver.review,{completed_stages:["approach","grasp"],object_and_goal_visible:true,full_task_confirmed:true});
  client.edit("finish.giver","failure");assert.deepEqual(client.snapshot().draft.finish.giver_phases,{});assert.equal(client.snapshot().draft.finish.giver_full_task,false);
});
test("missing model or observation provider blocks shadow without inventing browser inputs",async () => {
  const {client,calls}=await rig();const before=calls.length;assert.throws(()=>client.shadow(),/серверная общая очередь/);assert.equal(calls.length,before);assert.match(cooperativeWorkbenchPanel(client.snapshot()),/Shadow заблокирован/);
});
test("malformed optional local draft storage cannot replace typed form structures",async () => {
  const storage={getItem:()=>JSON.stringify({schema:1,draft:{finish:null,phase_maps:{giver:null,receiver:false},review:{phase_outcomes:null},scene:{evil:true}}}),setItem:()=>{}};const client=createCooperativeWorkbenchClient({storage,get:async()=>fixture(),post:async()=>{throw new Error("no POST");}});await client.refresh();assert.equal(client.snapshot().draft.finish.status,"unknown");assert.deepEqual(client.snapshot().draft.phase_maps.giver,{});assert.equal(client.snapshot().draft.scene,"");assert.doesNotThrow(()=>cooperativeWorkbenchPanel(client.snapshot()));
});
test("unreviewed failure stays reported analysis data and still closes native recordings as unknown",async () => {
  const state=fixture();state.workbench.sessions[0].recording_id="partial-failure";const calls=[];const {client}=await rig({uuid:()=>"11111111-1111-4111-8111-111111111111",get:async()=>clone(state),post:async(path,body)=>{calls.push(body);return {result:{},snapshot:clone(state)};}});
  client.edit("finish.status","failure");client.edit("finish.giver","failure");await client.recordFinish();assert.equal(calls[0].outcome.status,"failure");assert.equal(calls[0].outcome.native_roles.giver.operator_reported_status,"failure");assert.equal(calls[0].outcome.native_roles.giver.status,"unknown");assert.equal(calls[0].outcome.native_roles.giver.method,"unknown");
});
test("real enqueue is separate explicit action gated on accepted aggregate and ready plan for this exact spec",async () => {
  const state=fixture(),calls=[];const {client}=await rig({get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);const result=path.endsWith("/aggregate")?{aggregate_manifest_sha256:"e".repeat(64)}:path.endsWith("/training_plan")?{ready:true,requests:{giver:{},receiver:{}}}:{jobs:{giver:{id:"actual-giver-job",status:"queued"}},errors:{receiver:"Source not admitted"},complete_native_reservation:false};return {result,snapshot:clone(state)};}});
  assert.throws(()=>client.train(),/Сначала соберите/);await client.aggregate();assert.throws(()=>client.train(),/Сначала соберите/);await client.trainingPlan();assert.equal(calls.filter(([path])=>path.endsWith("/train")).length,0);await client.train();assert.deepEqual(calls.at(-1),["/api/cooperative/train",{skill_id:"cooperative-skill",version:1,settings:{}}]);const html=cooperativeWorkbenchPanel(client.snapshot());assert.ok(html.includes("actual-g"));assert.ok(html.includes("Source not admitted"));assert.ok(html.includes("Частичный результат сохраняется сервером"));
});
test("a plan from another immutable skill cannot enable enqueue and changed source review invalidates preflight",async () => {
  const state=fixture();state.workbench.sessions[0].recording_id="finishing-source";const {client}=await rig({get:async()=>clone(state),post:async(path)=>({result:path.endsWith("/aggregate")?{aggregate_manifest_sha256:"e".repeat(64)}:{ready:true},snapshot:clone(state)})});await client.aggregate();await client.trainingPlan();await client.recordFinish();assert.throws(()=>client.train(),/Сначала соберите/);
  const view=client.snapshot();view.aggregateReceipt={skillKey:"foreign",result:{aggregate_manifest_sha256:"e".repeat(64)}};view.trainingPlanReceipt={skillKey:"foreign",result:{ready:true}};assert.match(cooperativeWorkbenchPanel(view),/Для enqueue нужны допущенный/);
});

test("warm model preparation is explicit compute-only, pinned to epoch and never revives after STOP",async()=>{
  const state=fixture(),calls=[];state.queued_native_shadow_supported=true;state.warm_compute_sessions=[];
  state.required_policy_roles=["giver","receiver"];state.workbench.sessions[0].giver_input_mode="policy";state.workbench.sessions[0].receiver_input_mode="policy";
  state.workbench.skills[0].spec.subskills.giver.model_id="native9-model";state.workbench.skills[0].spec.subskills.receiver.model_id="native6-model";
  const {client}=await rig({uuid:()=>"11111111-1111-4111-8111-111111111111",get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);return {result:{compute_ready:false},snapshot:clone(state)};}});
  assert.equal(calls.length,0);await client.warmModels();assert.deepEqual(calls[0],["/api/cooperative/warm_models",{session_id:"server-session",expected_epoch:"epoch-one",request_id:"11111111-1111-4111-8111-111111111111",compute_ttl_s:120}]);
  const view=client.snapshot();view.data.warm_compute_sessions=[{job_id:"abc12345",status:"running",compute_ready:true,compute_ttl_s:120,paired_runtime_benchmark_qualified:false,blockers:[]}];
  const html=cooperativeWorkbenchPanel(view);assert.match(html,/Требуемые модели загружены/);assert.match(html,/native lease не разрешаются/);assert.match(html,/измерение пары моделей ещё не подтверждено/);
  await client.stop();assert.throws(()=>client.warmModels(),/После STOP/);assert.ok(calls.every(([path])=>path.endsWith('/warm_models') || path.endsWith('/stop')));
});

test("one real policy head can warm without a model for the manual role; both-manual needs no warm job",async()=>{
  const state=fixture(),calls=[];state.warm_compute_sessions=[];state.required_policy_roles=["giver"];
  state.workbench.sessions[0].giver_input_mode="policy";state.workbench.sessions[0].receiver_input_mode="leader";
  state.workbench.skills[0].spec.subskills.giver.model_id="native9-model";
  assert.equal(state.workbench.skills[0].spec.subskills.receiver.model_id,undefined);
  const {client}=await rig({uuid:()=>"11111111-1111-4111-8111-111111111111",get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);return {result:{},snapshot:clone(state)};}});
  await client.warmModels();assert.equal(calls.length,1);assert.equal(calls[0][0],"/api/cooperative/warm_models");
  assert.ok(!JSON.stringify(calls[0][1]).includes("receiver-model"));
  state.required_policy_roles=[];state.workbench.sessions[0].giver_input_mode="keyboard";await client.refresh();
  assert.throws(()=>client.warmModels(),/ручной ввод модели не требует/);assert.equal(calls.length,1);
});

test("observed wrapper pins current session and epoch and rejects unknown native operations or browser overrides",async()=>{
  const {client,calls}=await rig();await client.observedOperation("observed_refresh",{});
  assert.deepEqual(calls.at(-1),["POST","/api/cooperative/observed_refresh",{session_id:"server-session",expected_epoch:"epoch-one"}]);
  assert.throws(()=>client.observedOperation("observed_refresh",{session_id:"foreign"}),/без подмены/);
  assert.throws(()=>client.observedOperation("native_begin",{role:"giver"}),/typed операция/);
  await client.stop();assert.throws(()=>client.observedOperation("observed_refresh",{}),/После STOP/);
});

test('native wrapper pins server session/epoch and rejects targets, model IDs or browser ownership fields',async()=>{
  const {client,calls}=await rig();await client.nativeOperation('native_refresh',{});assert.deepEqual(calls.at(-1),['POST','/api/cooperative/native_refresh',{session_id:'server-session',expected_epoch:'epoch-one'}]);
  for(const fields of [{session_id:'other'},{expected_epoch:'other'},{targets:[1,2]},{model_id:'spoof'},{actor_id:'spoof'},{url:'http://foreign'}])assert.throws(()=>client.nativeOperation('native_begin',fields),/без подмены/);
  await client.nativeOperation('native_recenter',{role:'giver',native_control_choice_id:'a'.repeat(64),observing:true,user_observed_test_confirmed:true,neutral:true});assert.equal(calls.at(-1)[2].expected_epoch,'epoch-one');await client.stop();assert.throws(()=>client.nativeOperation('native_refresh',{}),/После STOP/);
});


test('receiver metadata wrapper pins epoch, uses operation-specific fields and fences STOP',async()=>{
  const {client,calls}=await rig();await client.receiverSetupOperation('receiver_setup_refresh',{});
  assert.deepEqual(calls.at(-1),['POST','/api/cooperative/receiver_setup_refresh',{session_id:'server-session',expected_epoch:'epoch-one'}]);
  for(const fields of [{session_id:'foreign'},{expected_epoch:'foreign'},{native_target:[1]},{frame_id:'spoof'},{actor_id:'spoof'},{direction_profile_id:'spoof'},{path:'/secret'}])assert.throws(()=>client.receiverSetupOperation('receiver_setup_bind_guard',fields),/без native чисел/);
  assert.throws(()=>client.receiverSetupOperation('receiver_setup_reset_safe',{empty_gripper_confirmed:true}),/без native чисел/);
  assert.throws(()=>client.receiverSetupOperation('native_begin',{}),/без native чисел/);
  await client.receiverSetupOperation('receiver_setup_capture_empty_open',{receiver_setup_choice_id:'a'.repeat(64),observation_choice_id:'b'.repeat(32),displayed_jpeg_sha256:'c'.repeat(64),observing:true,user_observed_test_confirmed:true,empty_gripper_confirmed:true});
  assert.equal(calls.at(-1)[2].expected_epoch,'epoch-one');await client.stop();assert.throws(()=>client.receiverSetupOperation('receiver_setup_refresh',{}),/После STOP/);
});


test('source metadata artifacts survive a stopped session and page recreation with fixed same-origin links',async()=>{
 const state=fixture(),receipt={receipt_id:'a'.repeat(32),sha256:'b'.repeat(64),operation:'adopt_hold',url:'/api/cooperative/artifacts/receiver-metadata/'+'a'.repeat(32)};
 state.workbench.sessions[0].status='STOPPED';state.workbench.sessions[0].receiver_metadata_receipts=[receipt,{...receipt,url:'javascript:move()'}];
 const r=await rig({get:async()=>clone(state)}),before=r.calls.length,html=cooperativeWorkbenchPanel(r.client.snapshot());
 assert.match(html,/Сохранённые проверки SO101/);assert.ok(html.includes(receipt.url));assert.ok(!html.includes('javascript:'));
 assert.match(html,/Сохранённая запись недоступна/);assert.equal(r.calls.length,before);
});


test('giver reset wrapper accepts only explicit safe-object flag and opaque choice; source CAS remains server-owned',async()=>{
 const r=await rig();await r.client.nativeOperation('native_reset_safe',{role:'giver',native_control_choice_id:'a'.repeat(64),observing:true,user_observed_test_confirmed:true,object_safe_no_dual_hold:true});
 assert.deepEqual(r.calls.at(-1)[2],{role:'giver',native_control_choice_id:'a'.repeat(64),observing:true,user_observed_test_confirmed:true,object_safe_no_dual_hold:true,session_id:'server-session',expected_epoch:'epoch-one'});
 for(const fields of [{expected_state_id:'spoof'},{expected_revision:123},{targets:[90,90]}])assert.throws(()=>r.client.nativeOperation('native_reset_safe',fields),/без подмены/);
 assert.throws(()=>r.client.nativeOperation('native_begin',{object_safe_no_dual_hold:true}),/без подмены/);
});


test('native reply history remains readable after STOP and never renders injected artifact URLs',async()=>{
 const state=fixture(),receipt={role:'giver',ordinal:66,receipt_id:'c'.repeat(32),sha256:'d'.repeat(64),url:'/api/cooperative/artifacts/native-control/'+'c'.repeat(32)};
 state.workbench.sessions[0].status='STOPPED';state.workbench.sessions[0].native_user_control_receipts=[receipt,{...receipt,url:'javascript:move()'}];
 const r=await rig({get:async()=>clone(state)}),before=r.calls.length,html=cooperativeWorkbenchPanel(r.client.snapshot());
 assert.match(html,/Сохранённые действия управления/);assert.ok(html.includes(receipt.url));assert.ok(!html.includes('javascript:'));
 assert.match(html,/Сохранённая запись недоступна/);assert.equal(r.calls.length,before);
});

test('giver phase wrappers preserve server session and reject browser source CAS/model/token values',async()=>{
 const r=await rig(),ops=['native_phase_park','native_phase_adopt','native_phase_begin','native_phase_resume'];
 for(const op of ops){
  const body={role:'giver',native_control_choice_id:'a'.repeat(64),observing:true,user_observed_test_confirmed:true,neutral:true};
  await r.client.nativeOperation(op,body);assert.deepEqual(r.calls.at(-1),['POST','/api/cooperative/'+op,{...body,session_id:'server-session',expected_epoch:'epoch-one'}]);
  for(const key of ['authorization_id','control_epoch','park_request_id','expected_revision','phase_id','actor_id','constraint','source_origin'])
   assert.throws(()=>r.client.nativeOperation(op,{...body,[key]:'browser-value'}),/без подмены/);
 }
 assert.equal(r.calls.filter(([method])=>method==='POST').length,ops.length);
});
test("explicit pending DATA reconciliation can finish old data after STOP without control fields",async()=>{
  const state=fixture(),calls=[];state.workbench.sessions[0].status="STOPPED";
  state.workbench.sessions[0].native_capture_recovery={state:"PENDING_DATA_RECONCILIATION",attempt_id:"server-opaque-attempt"};
  state.workbench.sessions[0].record_start_errors={receiver:"KNOWN_START_ACK_MISSING"};
  const {client}=await rig({get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);return {snapshot:clone(state),result:{control_authority_adopted:false}};}});
  const html=cooperativeWorkbenchPanel(client.snapshot());assert.match(html,/record-reconcile/);assert.match(html,/UNKNOWN/);assert.match(html,/KNOWN_START_ACK_MISSING/);
  await client.recordReconcile();assert.deepEqual(calls,[["/api/cooperative/record_reconcile",{session_id:"server-session",expected_epoch:"epoch-one",capture_attempt_id:"server-opaque-attempt"}]]);
  assert.equal(client.snapshot().data.workbench.sessions[0].status,"STOPPED");assert.throws(()=>client.step("giver_holding"),/После STOP/);
});
for(const recovery of [{state:"PENDING_DATA_FINISH_RECONCILIATION"},{state:"DATA_FINISHED",cleanup_required:true}]){
  test(`DATA recovery ${recovery.state} uses only server attempt pin after interruption`,async()=>{
    const state=fixture(),calls=[];state.workbench.sessions[0].status="INTERRUPTED";
    state.workbench.sessions[0].native_capture_recovery={...recovery,attempt_id:"current-server-attempt"};
    const {client}=await rig({get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);return {snapshot:clone(state),result:{control_authority_adopted:false}};}});
    await client.recordReconcile();assert.deepEqual(calls,[["/api/cooperative/record_reconcile",{session_id:"server-session",expected_epoch:"epoch-one",capture_attempt_id:"current-server-attempt"}]]);
    assert.equal(client.snapshot().data.workbench.sessions[0].status,"INTERRUPTED");
    assert.match(cooperativeWorkbenchPanel(client.snapshot()),/record-reconcile/);
  });
}
test("both DATA sources expose separate attempts and giver reconcile never carries authority",async()=>{
  const state=fixture(),calls=[];
  state.workbench.sessions[0].native_capture_recoveries={
    giver:{state:"PENDING_DATA_FINISH_RECONCILIATION",attempt_id:"giver-attempt"},
    receiver:{state:"PENDING_DATA_RECONCILIATION",attempt_id:"receiver-attempt"}};
  const {client}=await rig({get:async()=>clone(state),post:async(path,body)=>{calls.push([path,body]);return {snapshot:clone(state),result:{control_authority_adopted:false}};}});
  const html=cooperativeWorkbenchPanel(client.snapshot());assert.match(html,/data-coop-role="giver"/);assert.match(html,/data-coop-role="receiver"/);
  assert.throws(()=>client.recordReconcile("other"),/DATA поток/);
  await client.recordReconcile("giver");assert.deepEqual(calls,[["/api/cooperative/record_reconcile",{session_id:"server-session",expected_epoch:"epoch-one",capture_attempt_id:"giver-attempt",role:"giver"}]]);
});
for(const [status,message] of [["COMPLETED","Протокол завершён"],["INTERRUPTED","Сессия прервана"]]){
  test(`${status} never fabricates a confirmed STOP`,async()=>{
    const state=fixture();state.workbench.sessions[0].status=status;
    const {client}=await rig({get:async()=>clone(state)});
    const html=cooperativeWorkbenchPanel(client.snapshot());assert.match(html,new RegExp(message));assert.doesNotMatch(html,/STOP подтверждён сервером/);
  });
}
