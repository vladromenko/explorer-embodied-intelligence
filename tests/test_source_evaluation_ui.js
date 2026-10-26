"use strict";
// Temp programmatic transport/DOM evidence only, never real authorize/action HTTP.
const test = require("node:test"), assert = require("node:assert/strict"), {webcrypto,createHash} = require("node:crypto");
const {createSourceEvaluationClient,sourceEvaluationPanel,bindSourceEvaluation} = require("../00-control-center/src/explorer/static/source-evaluation.js");
const copy = value=>JSON.parse(JSON.stringify(value)), model="a".repeat(32), job="b".repeat(32), challengeID="c".repeat(32), grantID="d".repeat(32), observationID="e".repeat(32), outcomeID="f".repeat(32);
const base="/api/lab/source-evaluations", jpeg=new Uint8Array([255,216,83,79,70,84,87,65,82,69,255,217]), checksum=createHash("sha256").update(jpeg).digest("hex");
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {resolve,reject,promise};};
const frameMeta=(id="native-frame-one")=>({frame_id:id,jpeg_sha256:checksum,boot_id:"native-boot",camera_id:"actual-camera"});
function fixture() {return {physical_execution_authorized:false,models:[{id:model,title:"Конфета → коробка",backend:"act",eligible:true},{id:"0".repeat(32),title:"Исторический hold",backend:"act",eligible:false,reason:"Не предметный навык"}],attempts:[{id:job,status:"running",context:{object_instance_id:"Конфета A",scene_id:"Стол слева",goal_instance_id:"Коробка B"},result:{phase:"awaiting_source_observed_grant",model_ready:true},phase:{phase_id:"approach",revision:"1".repeat(64)},phases:["approach","grasp","place"],pending_outcomes:[],timeline:[]}]};}
function challenge() {return {challenge_id:challengeID,control_epoch:"native-epoch-1",frame:frameMeta(),execution_permission:false};}
function grant() {return {grant_id:grantID,execution_permission:true,native_mode:"policy",control_epoch:"native-epoch-1",boot_id:"native-boot",binding:{purpose:"source04_eval",model_job_id:model},displayed_frame:frameMeta()};}
async function rig(overrides={}) {
  const state=fixture(), calls=[], transport={grant:grant(),clock:100};
  const options={crypto:webcrypto,now:()=>transport.clock,get:async path=>{
    calls.push(["GET",path]);
    if(path===base)return copy(state);
    if(path.endsWith("/permission"))return {execution_permission:true,grant:copy(transport.grant)};
    if(path.endsWith("/observation"))return {packet:{observation:{observation_id:observationID,frame_id:"native-frame-two",frame_sha256:checksum,frame_boot_id:"native-boot"}},frame_url:`${base}/${job}/observation/${observationID}/frame`};
    throw new Error("Unexpected GET "+path);
  },post:async(path,body)=>{
    calls.push(["POST",path,copy(body)]);
    if(path===base)return {id:job,status:"queued"};
    if(path.endsWith("/challenge"))return challenge();
    if(path.endsWith("/authorize"))return copy(transport.grant);
    if(path.endsWith("/observe"))return copy(transport.grant);
    if(path.endsWith("/cancel")){state.attempts[0].status="cancel_requested";return {id:job,status:"cancel_requested"};}
    if(path.endsWith("/phase")){state.attempts[0].phase={phase_id:body.phase_id,revision:"2".repeat(64)};return copy(state.attempts[0].phase);}
    if(path.includes("/outcomes/")){state.attempts[0].pending_outcomes=[];return {status:body.status};}
    throw new Error("Unexpected POST "+path);
  },loadFrame:async path=>{calls.push(["FRAME",path]);return {bytes:jpeg,headers:{"x-jpeg-sha256":checksum,"x-frame-id":path.includes("/challenge/")?"native-frame-one":"native-frame-two","x-boot-id":"native-boot"}};},makeObjectURL:()=>"blob:software-verified-frame",revokeObjectURL:()=>{},...overrides};
  const client=createSourceEvaluationClient(options);await client.refresh();client.selectAttempt(job);
  return {client,calls,state,transport,options};
}
async function ready(client) {await client.requestChallenge();await client.displayed(client.snapshot().frame.token);for(const key of ["observing","robot_workspace_checked","user_observed_test_confirmed"])client.affirm(key,true);}
async function watching(client) {await ready(client);await client.authorize();}
function events() {const handlers={};return {handlers,addEventListener:(key,fn)=>handlers[key]=fn,removeEventListener:key=>delete handlers[key]};}

test("constructor, render and binding issue no HTTP mutation or implicit reads",()=>{
  const calls=[], client=createSourceEvaluationClient({get:async()=>calls.push("GET"),post:async()=>calls.push("POST")}),root={...events(),contains:()=>true};
  const cleanup=bindSourceEvaluation(root,client,()=>{}, {document:events(),window:events()});sourceEvaluationPanel(client.snapshot());cleanup();assert.deepEqual(calls,[]);
});
test("preparation uses one typed model and plain context only, never grants or native commands",async()=>{
  const {client,calls}=await rig();for(const [key,value] of Object.entries({model_job_id:model,object_instance_id:"Конфета A",scene_id:"Стол слева",goal_instance_id:"Коробка B",duration_s:"12",review_timeout_s:"90"}))client.edit(key,value);
  await client.prepare();const post=calls.find(row=>row[0]==="POST");assert.deepEqual(post,["POST",base,{model_job_id:model,duration_s:12,review_timeout_s:90,object_instance_id:"Конфета A",scene_id:"Стол слева",goal_instance_id:"Коробка B"}]);assert.equal(client.snapshot().watching,false);assert.equal(calls.filter(row=>row[0]==="POST").length,1);
});
test("unqualified model, empty context and bad bounds are rejected without mutation",async()=>{
  const {client,calls}=await rig();client.edit("model_job_id","0".repeat(32));await assert.rejects(client.prepare(),/допустимую/);client.edit("model_job_id",model);await assert.rejects(client.prepare(),/Опишите/);client.edit("duration_s","Infinity");await assert.rejects(client.prepare(),/Длительность/);assert.equal(calls.filter(row=>row[0]==="POST").length,0);
});
test("challenge displays exact verified JPEG; all flags alone cannot authorize before actual image display",async()=>{
  const {client,calls}=await rig();await client.requestChallenge();for(const key of ["observing","robot_workspace_checked","user_observed_test_confirmed"])client.affirm(key,true);
  await assert.rejects(client.authorize(),/Покажите свежий/);assert.equal(calls.some(row=>row[1].endsWith("/authorize")),false);
  await client.displayed(client.snapshot().frame.token);await client.authorize();const post=calls.find(row=>row[1].endsWith("/authorize"));assert.deepEqual(post[2],{challenge_id:challengeID,displayed_jpeg_sha256:checksum,expected_control_epoch:"native-epoch-1",observing:true,robot_workspace_checked:true,user_observed_test_confirmed:true});assert.equal(client.snapshot().watching,true);
});
test("wrong JPEG hash or frame/boot headers never produce a permission request",async()=>{
  for(const key of ["x-jpeg-sha256","x-frame-id","x-boot-id"]) {
    const headers={"x-jpeg-sha256":checksum,"x-frame-id":"native-frame-one","x-boot-id":"native-boot",[key]:"wrong"};
    const {client,calls}=await rig({loadFrame:async()=>({bytes:jpeg,headers})});await assert.rejects(client.requestChallenge(),/не совпадают/);assert.equal(client.snapshot().challenge,null);assert.equal(calls.filter(row=>row[1].endsWith("/authorize")).length,0);
  }
});
test("stale displayed frame is not renewed by another render or image load",async()=>{
  const {client,transport,calls}=await rig();await ready(client);transport.clock+=1501;await client.displayed(client.snapshot().frame.token);await assert.rejects(client.authorize(),/Покажите свежий/);assert.equal(calls.filter(row=>row[1].endsWith("/authorize")).length,0);
});
test("watch loop reads actual permission epoch and observation, posts only after actual new display",async()=>{
  const {client,transport,calls}=await rig();await watching(client);transport.grant.control_epoch="native-policy-epoch-2";
  await client.watchOnce();assert.equal(calls.filter(row=>row[1].endsWith("/observe")).length,0);const frame=client.snapshot().frame;assert.equal(frame.displayed,false);
  await client.displayed(frame.token);await client.displayed(frame.token);const posts=calls.filter(row=>row[1].endsWith("/observe"));assert.equal(posts.length,1);assert.deepEqual(posts[0][2],{grant_id:grantID,frame_id:"native-frame-two",displayed_jpeg_sha256:checksum,expected_control_epoch:"native-policy-epoch-2",observing:true});
});
test("unshown observation is not replaced repeatedly while awaiting real display",async()=>{
  const {client,calls}=await rig();await watching(client);await client.watchOnce();const count=calls.length;assert.deepEqual(await client.watchOnce(),{waitingForDisplay:true});assert.equal(calls.length,count);
});
test("fresh permission from a different grant/boot never borrows authority",async()=>{
  const {client,transport,calls}=await rig();await watching(client);transport.grant.grant_id="1".repeat(32);await assert.rejects(client.watchOnce(),/другой попытке/);assert.equal(client.snapshot().watching,false);assert.equal(calls.filter(row=>row[1].endsWith("/observe")).length,0);
});
test("hidden page fences late authorize and returning visible never revives watching",async()=>{
  const pending=deferred(),calls=[];const {client}=await rig({post:async(path,body)=>{calls.push([path,body]);return path.endsWith("/challenge")?challenge():pending.promise;}});await ready(client);const active=client.authorize();client.setVisible(false);client.setVisible(true);pending.resolve(grant());assert.deepEqual(await active,{ignored:true});assert.equal(client.snapshot().watching,false);assert.equal(client.snapshot().grant,null);assert.ok(client.snapshot().uncertain.includes(job));assert.equal(calls.filter(row=>row[0].endsWith("/authorize")).length,1);
});
test("STOP bypasses pending authorize and its late response cannot restore grant or selected control",async()=>{
  const pending=deferred();const {client,calls,state}=await rig({post:async(path)=>{calls.push(["POST",path]);if(path.endsWith("/challenge"))return challenge();if(path.endsWith("/authorize"))return pending.promise;if(path.endsWith("/cancel")){state.attempts[0].status="cancel_requested";return {status:"cancel_requested"};}throw new Error("unexpected");}});
  await ready(client);const authorizing=client.authorize();await client.cancel();pending.resolve(grant());await authorizing;assert.equal(client.snapshot().grant,null);assert.equal(client.snapshot().watching,false);assert.ok(client.snapshot().cancelled.includes(job));await assert.rejects(client.requestChallenge(),/остановлена/);
});
test("late observe cannot renew local watching after interrupt, even if backend response succeeds",async()=>{
  const pending=deferred();const {client,options}=await rig();const post=options.post;options.post=async(path,body)=>path.endsWith("/observe")?pending.promise:post(path,body);
  await watching(client);await client.watchOnce();const observing=client.displayed(client.snapshot().frame.token);client.interrupt("Глобальный STOP");pending.resolve(grant());assert.deepEqual(await observing,{ignored:true});assert.equal(client.snapshot().watching,false);assert.equal(client.snapshot().grant,null);
});
test("model selection fences late requests but keeps old attempt STOP available",async()=>{
  const pending=deferred();const {client,options}=await rig();const post=options.post;options.post=async(path,body)=>path.endsWith("/authorize")?pending.promise:post(path,body);
  await ready(client);const authorizing=client.authorize();client.edit("model_job_id","0".repeat(32));pending.resolve(grant());await authorizing;assert.equal(client.snapshot().selected,job);assert.equal(client.snapshot().grant,null);assert.equal(client.snapshot().watching,false);assert.match(sourceEvaluationPanel(client.snapshot()),/STOP · отменить эту попытку/);
});
test("failed prepare is not automatically retried across client/page recreation; only text drafts persist",async()=>{
  const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};const {client,options}=await rig({storage});client.edit("model_job_id",model);client.edit("object_instance_id","Предмет C");client.edit("scene_id","Сцена C");client.edit("goal_instance_id","Коробка C");options.post=async()=>{throw new Error("unknown network outcome");};await assert.rejects(client.prepare(),/unknown/);
  const again=createSourceEvaluationClient({...options,post:async()=>assert.fail("duplicate job")});await again.refresh();assert.equal(again.snapshot().draft.object_instance_id,"Предмет C");assert.equal(again.snapshot().prepareUncertain,true);assert.equal(again.snapshot().watching,false);assert.equal(again.snapshot().grant,null);await assert.rejects(again.prepare(),/Не повторяйте/);
});
test("phase advances only to actual next source phase with exact current revision and native epoch",async()=>{
  const {client,calls,transport}=await rig();await watching(client);transport.grant.control_epoch="native-policy-epoch-2";await client.watchOnce();await assert.rejects(client.advancePhase("place"),/следующий/);await client.advancePhase("grasp");assert.deepEqual(calls.find(row=>row[1].endsWith("/phase"))[2],{phase_id:"grasp",expected_revision:"1".repeat(64),expected_control_epoch:"native-policy-epoch-2"});
});
test("success/failure/unknown remain distinct explicit outcomes after actual stop, no default success",async()=>{
  for(const status of ["success","failure","unknown"]) {
    const {client,state,calls}=await rig();state.attempts[0].result.phase="source_outcome_review_after_native_stop";state.attempts[0].pending_outcomes=[{challenge_id:outcomeID,phase_id:"grasp",question:"Был ли предмет удержан?"}];await client.refresh();
    if(status!=="unknown"){await assert.rejects(client.outcome(outcomeID,status),/Лично просмотрите/);client.outcomeEdit(outcomeID,"observing",true);}await client.outcome(outcomeID,status);assert.equal(calls.find(row=>row[1].includes("/outcomes/"))[2].status,status);
  }
});
test("ordinary rebind cleanup retains active watch; lifecycle blur fences it and focus cannot resume",async()=>{
  const {client}=await rig();await watching(client);const root={...events(),contains:()=>true},doc={...events(),visibilityState:"visible"},win=events(),timers=new Set();
  const env={document:doc,window:win,setTimeout:fn=>{timers.add(fn);return fn;},clearTimeout:fn=>timers.delete(fn)};
  const cleanup=bindSourceEvaluation(root,client,()=>{},env);assert.equal(timers.size,1);cleanup();assert.equal(client.snapshot().watching,true);assert.equal(timers.size,0);
  const cleanupAgain=bindSourceEvaluation(root,client,()=>{},env);win.handlers.blur();assert.equal(client.snapshot().watching,false);win.handlers.focus();assert.equal(client.snapshot().watching,false);cleanupAgain();
});
test("hidden/offscreen/incomplete image cannot claim actual display through binding",async()=>{
  const {client}=await rig();await client.requestChallenge();const root={...events(),contains:()=>true},doc={...events(),visibilityState:"visible"},win={...events(),innerHeight:800,innerWidth:1000};const cleanup=bindSourceEvaluation(root,client,()=>{}, {document:doc,window:win});
  const image={dataset:{sourceEvalFrame:String(client.snapshot().frame.token)},complete:true,naturalWidth:320,getClientRects:()=>[{}],getBoundingClientRect:()=>({width:320,height:240,top:900,bottom:1140,left:0,right:320})};root.handlers.load({target:image});await Promise.resolve();assert.equal(client.snapshot().frame.displayed,false);cleanup();
});
test("panel escapes model/context and rejects untrusted timeline paths while showing separate04/06 scope",async()=>{
  const {client,state}=await rig();state.models[0].title='<img src=x onerror="alert(1)">';state.attempts[0].timeline=[{index:0,phase_id:"grasp",command_id:"native:1",frame_url:"javascript:alert(1)",frame_sha256:checksum}];await client.refresh();const html=sourceEvaluationPanel(client.snapshot());assert.ok(html.includes("&lt;img"));assert.ok(!html.includes('href="javascript:'));assert.ok(html.includes("Experiment 04"));assert.ok(html.includes("Experiment 06"));assert.ok(html.includes("Не предметный навык"));assert.ok(!html.includes("JSON"));
});
test("LAN HTTP without SubtleCrypto still verifies actual JPEG bytes with the shared SHA helper",async()=>{
  const {client}=await rig({crypto:{}});await ready(client);await client.authorize();assert.equal(client.snapshot().watching,true);
});
test("root displayFrame callback must confirm attached decode before display or observe",async()=>{
  const shown=deferred(),seen=[];const {client}=await rig({displayFrame:async(url,identity)=>{seen.push([url,identity]);return shown.promise;}});
  const loading=client.requestChallenge();while(!seen.length)await new Promise(resolve=>setImmediate(resolve));assert.equal(client.snapshot().frame.displayed,false);shown.resolve(true);await loading;assert.equal(client.snapshot().frame.displayed,true);assert.equal(seen[0][1].jpeg_sha256,checksum);assert.equal(seen[0][1].kind,"challenge");
});
test("explicit recovery of a found uncertain new preparation needs exact server context and sends no POST",async()=>{
  const {client,state,options,calls}=await rig();for(const [key,value] of Object.entries({model_job_id:model,object_instance_id:"Предмет C",scene_id:"Стол C",goal_instance_id:"Коробка C"}))client.edit(key,value);options.post=async()=>{throw new Error("lost reply");};await assert.rejects(client.prepare());assert.throws(()=>client.acknowledgePreparation(),/Выберите найденную/);
  const found={...copy(state.attempts[0]),id:"9".repeat(32),model_job_id:model,context:{object_instance_id:"Предмет C",scene_id:"Стол C",goal_instance_id:"Коробка C"}};state.attempts.push(found);await client.refresh();client.selectAttempt(found.id);const count=calls.length;assert.equal(client.snapshot().canResolvePreparation,true);client.acknowledgePreparation();assert.equal(client.snapshot().prepareUncertain,false);assert.equal(calls.length,count);
});
test("input events persist an unfinished text draft across ordinary poll/rerender",async()=>{
  const {client}=await rig(),root={...events(),contains:()=>true};const cleanup=bindSourceEvaluation(root,client,()=>{}, {document:events(),window:events()});root.handlers.input({target:{dataset:{sourceEvalField:"scene_id"},value:"Незаконченный текст",disabled:false}});await client.refresh();cleanup();assert.equal(client.snapshot().draft.scene_id,"Незаконченный текст");
});
test("first renewal waits for SAME grant idle→policy epoch rotation, never posts preceding idle epoch",async()=>{
  const {client,calls,transport}=await rig();transport.grant.native_mode="idle";await watching(client);const before=calls.length;
  assert.deepEqual(await client.watchOnce(),{waitingForNativeStart:true});assert.equal(client.snapshot().watching,true);assert.deepEqual(calls.slice(before),[["GET",`${base}/${job}/permission`]]);assert.equal(calls.filter(row=>row[1].endsWith("/observe")).length,0);
  transport.grant.native_mode="policy";transport.grant.control_epoch="actual-policy-start-epoch";await client.watchOnce();await client.displayed(client.snapshot().frame.token);const renew=calls.filter(row=>row[1].endsWith("/observe"));assert.equal(renew.length,1);assert.equal(renew[0][2].expected_control_epoch,"actual-policy-start-epoch");
});
test("unknown or teleoperation mode fails closed instead of waiting or renewing a source policy",async()=>{
  for(const mode of [undefined,"teleoperation","unknown"]) {const {client,calls,transport}=await rig();await watching(client);transport.grant.native_mode=mode;await assert.rejects(client.watchOnce(),/не подтвердил режим/);assert.equal(client.snapshot().watching,false);assert.equal(calls.filter(row=>row[1].endsWith("/observe")).length,0);}
});
const shadowID="7".repeat(32);
const shadowReply=(status="queued")=>({id:shadowID,kind:"so101_skill_shadow",status,config:{model_job_id:model,backend:"diffusion"}});
test("USER readonly shadow sends ONLY eligible server model ID and displays its separate actual queued job",async()=>{
  const {client,options,calls}=await rig();client.edit("model_job_id",model);client.edit("duration_s","Infinity");client.edit("object_instance_id","not shadow inputs");options.post=async(path,body)=>{calls.push(["POST",path,body]);return shadowReply();};
  await client.shadow();assert.deepEqual(calls.filter(row=>row[0]==="POST"),[["POST","/api/lab/source-shadow",{model_job_id:model}]]);const view=client.snapshot();assert.equal(view.shadowReceipt.id,shadowID);assert.equal(view.shadowReceipt.status,"queued");assert.equal(view.shadowReceipt.backend,"diffusion");assert.equal(view.selected,job);assert.equal(view.grant,null);assert.equal(view.watching,false);const html=sourceEvaluationPanel(view);assert.ok(html.includes(shadowID));assert.ok(html.includes('href="/queue"'));assert.ok(html.includes("Предметный исход проверяется отдельной наблюдаемой попыткой"));
});
test("readonly shadow cannot queue an unknown or ineligible model and does not grant permission",async()=>{
  const {client,calls}=await rig();await assert.rejects(client.shadow(),/допустимую/);client.edit("model_job_id","0".repeat(32));await assert.rejects(client.shadow(),/допустимую/);client.edit("model_job_id","1".repeat(32));await assert.rejects(client.shadow(),/допустимую/);assert.equal(calls.filter(row=>row[0]==="POST").length,0);assert.equal(client.snapshot().grant,null);
});
test("shadow enqueue preserves current physical attempt flags, displayed frame, phase and active watch",async()=>{
  const {client,options}=await rig();client.edit("model_job_id",model);await watching(client);client.affirm("observing",true);const before=client.snapshot();options.post=async()=>shadowReply();await client.shadow();const after=client.snapshot();assert.deepEqual(after.flags,before.flags);assert.deepEqual(after.grant,before.grant);assert.deepEqual(after.frame,before.frame);assert.equal(after.selected,before.selected);assert.equal(after.watching,true);assert.deepEqual(after.data.attempts[0].phase,before.data.attempts[0].phase);
});
test("uncertain shadow enqueue persists across refresh/recreation, no retry until explicit Queue acknowledgment",async()=>{
  const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};const {client,options}=await rig({storage});client.edit("model_job_id",model);let posts=0;options.post=async()=>{posts++;throw new Error("lost shadow reply");};await assert.rejects(client.shadow(),/lost/);await client.refresh();await assert.rejects(client.shadow(),/неопределённого/);assert.equal(posts,1);
  const again=createSourceEvaluationClient(options);await again.refresh();await assert.rejects(again.shadow(),/неопределённого/);assert.equal(again.snapshot().watching,false);again.acknowledgeShadow();assert.equal(posts,1);options.post=async()=>{posts++;return shadowReply();};await again.shadow();assert.equal(posts,2);assert.equal(again.snapshot().shadowUncertain,false);
});
test("malformed or wrong-kind shadow receipt is uncertain rather than an invented successful enqueue",async()=>{
  for(const reply of [{id:"bad",kind:"so101_skill_shadow",status:"queued"},{...shadowReply(),kind:"so101_skill_eval"},{...shadowReply(),config:{model_job_id:"0".repeat(32)}}]){const {client,options}=await rig();client.edit("model_job_id",model);options.post=async()=>reply;await assert.rejects(client.shadow(),/не подтвердил/);assert.equal(client.snapshot().shadowReceipt,null);assert.equal(client.snapshot().shadowUncertain,true);assert.equal(client.snapshot().grant,null);}
});
test("late shadow response after STOP cannot restore physical permission or falsely adopt a new attempt",async()=>{
  const pending=deferred(),{client,options,state}=await rig();client.edit("model_job_id",model);await watching(client);const post=options.post;options.post=(path,body)=>path==="/api/lab/source-shadow"?pending.promise:post(path,body);const enqueue=client.shadow();await client.cancel();pending.resolve(shadowReply());assert.deepEqual(await enqueue,{ignored:true});assert.equal(client.snapshot().watching,false);assert.equal(client.snapshot().grant,null);assert.equal(client.snapshot().selected,job);assert.equal(client.snapshot().shadowReceipt,null);assert.equal(client.snapshot().shadowUncertain,true);assert.equal(state.attempts[0].status,"cancel_requested");
});
test("shadow status uses existing jobs-list GET only, never nonexistent per-job route or result success inference",async()=>{
  const {client,options,calls}=await rig();client.edit("model_job_id",model);options.post=async()=>shadowReply();await client.shadow();const get=options.get;options.get=async path=>{calls.push(["STATUS_GET",path]);return path==="/api/jobs"?{jobs:[{...shadowReply("completed"),result:{independent_full_task_success:true}}]}:get(path);};await client.refreshShadow();assert.equal(client.snapshot().shadowReceipt.status,"completed");assert.equal(client.snapshot().shadowReceipt.independent_full_task_success,undefined);assert.equal(client.snapshot().data.attempts[0].result.phase,"awaiting_source_observed_grant");assert.deepEqual(calls.filter(row=>row[0]==="STATUS_GET"),[["STATUS_GET","/api/jobs"]]);
});
test("an old jobs-list read cannot overwrite a newer confirmed shadow job",async()=>{
  const pending=deferred(),{client,options}=await rig();client.edit("model_job_id",model);options.post=async()=>shadowReply();await client.shadow();options.get=async()=>pending.promise;const reading=client.refreshShadow();const nextID="8".repeat(32);options.post=async()=>({...shadowReply(),id:nextID});await client.shadow();pending.resolve({jobs:[shadowReply("completed")]});assert.deepEqual(await reading,{ignored:true});assert.equal(client.snapshot().shadowReceipt.id,nextID);assert.equal(client.snapshot().shadowReceipt.status,"queued");
});
