"use strict";
// Injected metadata/HTTP/JPEG fixtures only; no real robot, browser grants or IO.
const test=require("node:test"),assert=require("node:assert/strict"),{webcrypto,createHash}=require("node:crypto");
const {createSourceDemonstrationClient,sourceDemonstrationPanel,bindSourceDemonstration,sourceDemonstrationRenderKey,patchSourceDemonstrationFrame}=require("../00-control-center/src/explorer/static/source-demonstration.js");
const BASE="/api/lab/demonstrations",ID="a".repeat(32),GRANT="b".repeat(32),RECORD="c".repeat(32),CHALLENGE="d".repeat(32);
const jpeg=new Uint8Array([255,216,70,65,75,69,255,217]),sha=createHash("sha256").update(jpeg).digest("hex"),copy=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const events=()=>({handlers:{},addEventListener(name,fn){this.handlers[name]=fn;},removeEventListener(name){delete this.handlers[name];}});
function fixture(overrides={}) {
  const calls=[],urls=[],revoked=[],extraRows=[],jobs=[];let row=null,grant=null,epoch="epoch-idle",mode="idle",capture=null,manifest=null,sourceReview=null,tick=100,obs=0,hook=null;
  const context={record_start_body:{project_id:"04",skill_id:"skill-one",skill_version:1,skill_spec_sha256:"e".repeat(64),session_id:"session-one",name:"Конфета → коробка",context:{variant_id:"variant-one",object_instance_id:"object-one"}}};
  const frameMeta=(id="camera-frame-1")=>({frame_id:id,jpeg_sha256:sha,boot_id:"source-boot",camera_id:"pi-logitech-fixture"});
  function grantValue(){return {grant_id:GRANT,control_epoch:epoch,native_mode:mode,execution_permission:true,binding:row.binding,actor_id:row.actor_id,
    follower_calibration_sha256:"f".repeat(64),leader_calibration_sha256:"1".repeat(64),boot_id:"source-boot",clock_domain:"source-clock",displayed_frame:frameMeta(),
    observation_renewal_required:overrides.persistentManual && mode==="teleoperation"?false:true};}
  const native=()=>({available:true,control_epoch:epoch,mode});
  async function get(path) {
    calls.push({method:"GET",path});
    const answer=async()=>{
      if(path===BASE)return {attempts:[...(row?[copy(row)]:[]),...copy(extraRows)]};
      if(path==="/api/jobs")return {jobs:copy(jobs)};
      if(path.endsWith("/status"))return {attempt:copy(row),station:native(),permission:{grant:copy(grant)},capture:{schema:"explorer_so101_source_capture_v1",recording_id:capture?.recording_id || null,capture:copy(capture),ready_recordings:manifest?[{project_id:"04",recording_id:RECORD,archive_sha256:"9".repeat(64)}]:[]}};
      if(path.endsWith("/observation"))return {packet:{observation:{frame_id:"observation-"+obs,frame_sha256:sha,frame_boot_id:"source-boot",camera_id:"pi-logitech-fixture",follower_calibration_sha256:"f".repeat(64)}},frame_url:BASE+"/"+ID+"/observation/frame"};
      if(path.endsWith("/capture/manifest"))return copy(manifest);
      if(path.endsWith("/capture/review"))return copy(sourceReview || {source_review:null,transfer_ready:false});
      throw new Error("Unexpected fixture GET "+path);
    };
    return hook ? hook("GET",path,null,answer) : answer();
  }
  async function post(path,body) {
    calls.push({method:"POST",path,body:copy(body)});
    const answer=async()=>{
      if(path===BASE){row={demonstration_id:ID,actor_id:"software-actor",request_id:body.request_id,record_start_body:copy(context.record_start_body),
        acknowledged_recording_id:null,capture_start_pending:false,binding:{purpose:"source04_demo",skill_id:body.skill_id,skill_version:body.skill_version,spec_sha256:context.record_start_body.skill_spec_sha256,demonstration_id:ID}};return copy(row);}
      const operation=path.split("/").at(-1);
      if(operation==="challenge")return {challenge_id:CHALLENGE,control_epoch:epoch,frame:frameMeta()};
      if(operation==="authorize"){grant=grantValue();return copy(grant);}
      if(["start","recenter","stop","revoke","observe"].includes(operation)) {
        if(body.grant_id!==grant?.grant_id || body.expected_control_epoch!==epoch)throw new Error("Fixture rejects foreign/stale exact owner");
        if(operation==="start" || operation==="recenter"){epoch=operation==="start"?"epoch-start-returned":"epoch-recenter-returned";mode="teleoperation";grant=grantValue();return {grant:copy(grant),status:native()};}
        if(operation==="observe")return copy(grant);
        mode="idle";epoch="epoch-stopped";grant=null;return operation==="revoke"?{revoked:true,native_command_called:false}:{grant:null,status:native(),native_command_called:true};
      }
      if(operation==="capture-start"){
        row.acknowledged_recording_id=RECORD;capture={recording_id:RECORD,state:"RECORDING",actor_id:row.actor_id,source_context:{context:{source04_demo_permission:{binding:row.binding}}}};
        return {recording_id:RECORD};
      }
      if(operation==="pause" || operation==="resume"){capture.state=operation==="pause"?"PAUSED":"RECORDING";return copy(capture);}
      if(operation==="finish" || operation==="cancel"){
        capture.state=operation==="finish"?"completed":"cancelled";
        manifest={recording_id:RECORD,state:capture.state,operator_outcome:body.outcome || "unknown",original_jpegs_sha256:{"000000.jpg":sha},sample_selection:[{source_sample_index:0,included_after_pause_filter:true}]};return copy(manifest);
      }
      if(operation==="review"){
        const checksum=(body.decision==="approve"?"5":"6").repeat(64);
        const job={id:"data-import-job",kind:"station_import",project_id:"04",status:"queued",config:{episode_id:RECORD,source_format:"so101_source_capture_v1",archive_sha256:"9".repeat(64),source_review_sha256:checksum}};
        sourceReview={review_sha256:checksum,transfer_ready:body.decision==="approve",source_review:{decision:body.decision}};
        if(body.decision==="approve")jobs.push(job);return {...copy(sourceReview),import_job:body.decision==="approve"?copy(job):null};
      }
      if(operation==="capture-reconcile"){row.acknowledged_recording_id=RECORD;row.capture_start_pending=false;return {recording_id:RECORD,data_recording_acknowledged:true,control_authority_adopted:false};}
      throw new Error("Unexpected fixture POST "+path);
    };
    return hook ? hook("POST",path,body,answer) : answer();
  }
  const client=createSourceDemonstrationClient({get,post,now:()=>tick,newID:()=>"2".repeat(32),crypto:webcrypto,
    loadFrame:async()=>({bytes:overrides.bytes || jpeg,headers:{"x-jpeg-sha256":sha,"x-frame-id":obs?"observation-"+obs:"camera-frame-1","x-boot-id":"source-boot",...overrides.headers}}),
    makeObjectURL:()=>{const url="blob:fixture-"+urls.length;urls.push(url);return url;},revokeObjectURL:url=>revoked.push(url)});
  const posts=()=>calls.filter(call=>call.method==="POST");
  async function prepared(){client.setContext(context,["approach","grasp"]);await client.prepare();}
  async function authorized(){await prepared();await client.requestChallenge();await client.displayed(client.snapshot().frame.token);for(const key of Object.keys(client.snapshot().flags))client.affirm(key,true);await client.authorize();}
  return {client,calls,posts,get,post,context,urls,revoked,prepared,authorized,hook:value=>{hook=value;},advance:()=>{obs++;},clock:value=>{tick=value;},
    addOtherAttempt:()=>{const value=copy(row);value.demonstration_id="4".repeat(32);value.binding={...value.binding,demonstration_id:value.demonstration_id,skill_id:"skill-other"};value.record_start_body={...value.record_start_body,skill_id:"skill-other",session_id:"session-other",name:"Другой навык"};extraRows.push(value);return value.demonstration_id;},
    foreign:()=>{epoch="foreign-epoch";mode="teleoperation";grant={...grantValue(),grant_id:"3".repeat(32)};},pending:()=>{row.capture_start_pending=true;row.acknowledged_recording_id=null;}};
}

test("constructor, context, render-free refresh and reloaded foreign GET never acquire control",async()=>{
  const f=fixture();assert.equal(f.posts().length,0);await f.client.refresh();assert.equal(f.posts().length,0);
  await f.prepared();assert.deepEqual(f.posts().map(call=>call.path),[BASE]);assert.equal(f.client.snapshot().owner,null);
  await f.client.refresh();assert.equal(f.posts().length,1);
});

test("second JPEG consumer requires actual attached visible decode and retains watch through ordinary rebind",async()=>{
  const f=fixture();await f.prepared();await f.client.requestChallenge();
  const root={...events(),contains:()=>true},doc={...events(),visibilityState:"visible"},timers=new Set();
  const win={...events(),innerHeight:800,innerWidth:1000,setTimeout:fn=>{timers.add(fn);return fn;},clearTimeout:fn=>timers.delete(fn)};
  let rect={width:320,height:240,top:900,bottom:1140,left:0,right:320};
  const image={dataset:{sourceDemoFrame:String(f.client.snapshot().frame.token)},isConnected:true,complete:true,naturalWidth:320,getClientRects:()=>[{}],getBoundingClientRect:()=>rect};
  root.querySelectorAll=()=>[image];let cleanup=bindSourceDemonstration(root,f.client,{document:doc,window:win});
  root.handlers.load({target:image});await Promise.resolve();assert.equal(f.client.snapshot().frame.displayed,false);
  rect={...rect,top:30,bottom:270};image.complete=false;root.handlers.load({target:image});await Promise.resolve();assert.equal(f.client.snapshot().frame.displayed,false);
  image.complete=true;image.isConnected=false;root.handlers.load({target:image});await Promise.resolve();assert.equal(f.client.snapshot().frame.displayed,false);
  image.isConnected=true;doc.visibilityState="hidden";root.handlers.load({target:image});await Promise.resolve();assert.equal(f.client.snapshot().frame.displayed,false);
  doc.visibilityState="visible";win.handlers.scroll();await new Promise(resolve=>setImmediate(resolve));assert.equal(f.client.snapshot().frame.displayed,true);
  for(const key of Object.keys(f.client.snapshot().flags))f.client.affirm(key,true);await f.client.authorize();
  cleanup();assert.equal(f.client.snapshot().watching,true);assert.equal(timers.size,0);
  cleanup=bindSourceDemonstration(root,f.client,{document:doc,window:win});assert.equal(timers.size,1);
  win.handlers.blur();await new Promise(resolve=>setImmediate(resolve));assert.equal(f.client.snapshot().watching,false);
  win.handlers.focus();assert.equal(f.client.snapshot().watching,false);cleanup();
  assert.equal(f.posts().filter(row=>row.path.endsWith("/revoke")).length,1);
});

test("demo panel escapes metadata, preserves unknown outcome and never exposes legacy global control endpoints",async()=>{
  const f=fixture();await f.prepared();const view=f.client.snapshot();
  view.context.record_start_body.name='<img src=x onerror="bad()">';view.error='<script>bad()</script>';
  const html=sourceDemonstrationPanel(view);
  assert.match(html,/&lt;img/);assert.match(html,/&lt;script&gt;/);assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('/api/station') && !html.includes('/api/stop'));
  assert.match(html,/Следующий новый показ/);assert.match(html,/Просматриваемый показ/);
});

test("import queue status is reconstructed by exact archive and review pins; reject retains prior job without admission",async()=>{
  const f=fixture();await f.authorized();await f.client.start();await f.client.captureStart();await f.client.finish("unknown");
  f.client.previewDisplayed(RECORD,sha);await f.client.review("approve");await f.client.refresh();
  let view=f.client.snapshot();assert.equal(view.importJobs.length,1);assert.equal(view.importJobs[0].status,"queued");
  assert.equal(view.review.import_job,undefined);assert.match(sourceDemonstrationPanel(view),/текущее одобрение/);
  await f.client.review("reject");view=f.client.snapshot();assert.equal(view.importJobs.length,1);
  assert.match(sourceDemonstrationPanel(view),/предыдущее решение · допуск по нему закрыт/);
  assert.equal(view.manifest.operator_outcome,"unknown");
  const other=f.addOtherAttempt();await f.client.refresh();await f.client.selectAttempt(other);assert.deepEqual(f.client.snapshot().importJobs,[]);
});

test("rerender never postpones the next observation deadline and control return schedules immediate renewal",async()=>{
  const f=fixture();await f.authorized();assert.equal(f.client.snapshot().watchDelayMs,0);
  f.advance();await f.client.watchOnce();assert.equal(f.client.snapshot().watchDelayMs,300);
  f.clock(200);const delays=[];const root={...events(),contains:()=>true},doc={...events(),visibilityState:"visible"};
  const win={...events(),setTimeout:(fn,delay)=>{delays.push(delay);return fn;},clearTimeout:()=>{}};
  let cleanup=bindSourceDemonstration(root,f.client,{document:doc,window:win});assert.equal(delays.at(-1),200);cleanup();
  f.clock(250);cleanup=bindSourceDemonstration(root,f.client,{document:doc,window:win});assert.equal(delays.at(-1),150);cleanup();
  await f.client.start();assert.equal(f.client.snapshot().watchDelayMs,0);
});

test("fresh observation patches the SAME image without recreating buttons; control/recording changes invalidate render key",async()=>{
  const f=fixture();await f.authorized();await f.client.start();f.advance();await f.client.watchOnce();
  const before=f.client.snapshot(),after=copy(before);after.frame={...after.frame,url:"blob:new-actual-frame",token:99,frame_id:"new-frame",displayed:true};
  after.owner.displayed_frame={frame_id:"new-frame"};after.owner.observation_deadline_ns=1234;after.watchDelayMs=50;after.watchBusy=true;
  assert.equal(sourceDemonstrationRenderKey(before),sourceDemonstrationRenderKey(after));
  const caption={},counter={},image={dataset:{},getAttribute:()=>before.frame.url,closest:()=>({querySelector:()=>caption})};
  const root={querySelector:selector=>selector.startsWith("img")?image:counter};
  assert.equal(patchSourceDemonstrationFrame(root,after),true);assert.equal(image.src,after.frame.url);assert.equal(image.dataset.sourceDemoFrame,"99");assert.match(caption.textContent,/действительно показан/);
  after.owner.control_epoch="NEW-RETURNED-EPOCH";assert.notEqual(sourceDemonstrationRenderKey(before),sourceDemonstrationRenderKey(after));
  after.owner=copy(before.owner);after.status={capture:{capture:{state:"PAUSED"}}};assert.notEqual(sourceDemonstrationRenderKey(before),sourceDemonstrationRenderKey(after));
});

test("VLM native-admission changes update controls while keeping explicit scoped STOP available",async()=>{
  const f=fixture();await f.authorized();const before=f.client.snapshot(),view=copy(before);
  view.status.permission.runtime_admission={native_commands_blocked:true};
  assert.notEqual(sourceDemonstrationRenderKey(before),sourceDemonstrationRenderKey(view));
  const html=sourceDemonstrationPanel(view);
  assert.match(html,/data-source-demo-action="start" disabled/);
  assert.match(html,/data-source-demo-action="stop" >STOP/);
  assert.match(html,/освобождение его ресурсов не подтверждено/);
});

test("prepare -> actual delivered JPEG -> explicit grant -> returned epoch -> DATA finish/review, with no finish STOP",async()=>{
  const f=fixture();await f.authorized();assert.equal(f.client.snapshot().owner.native_mode,"idle");
  await f.client.start();assert.equal(f.client.snapshot().owner.control_epoch,"epoch-start-returned");
  await f.client.captureStart();assert.equal(f.client.snapshot().recordingID,RECORD);
  assert.deepEqual(f.client.previewDisplayed("wrong",sha),{ignored:true}); // Stale preview never approves another recording.
});

test("source04 manual control has no two-second web lease and still stops on page leave",async()=>{
  const f=fixture({persistentManual:true});await f.authorized();await f.client.start();
  assert.equal(f.client.snapshot().watching,false);assert.equal(f.client.snapshot().owner.observation_renewal_required,false);
  f.advance();const count=f.calls.length;assert.deepEqual(await f.client.watchOnce(),{ignored:true});assert.equal(f.calls.length,count);
  await f.client.captureStart();assert.equal(f.client.snapshot().recordingID,RECORD);
  await f.client.setVisible(false);assert.equal(f.client.snapshot().owner,null);
  const stops=f.posts().filter(call=>call.path.endsWith("/stop"));assert.equal(stops.length,1);assert.equal(stops[0].body.expected_control_epoch,"epoch-start-returned");
});

test("pause/resume/finish UNKNOWN preserve control; only separate reviewed archive approval can import",async()=>{
  const f=fixture();await f.authorized();await f.client.start();await f.client.captureStart();
  await f.client.pause();await f.client.resume();await f.client.finish("unknown");
  assert.equal(f.client.snapshot().manifest.operator_outcome,"unknown");assert.equal(f.client.snapshot().owner.control_epoch,"epoch-start-returned");
  assert.equal(f.posts().filter(call=>call.path.endsWith("/stop")).length,0);
  await assert.rejects(f.client.review("approve"),/Просмотрите/);
  f.client.previewDisplayed(RECORD,sha);const approved=await f.client.review("approve");assert.equal(approved.import_job.id,"data-import-job");
  assert.deepEqual(f.posts().find(call=>call.path.endsWith("/review")).body,{decision:"approve",recording_id:RECORD,expected_archive_sha256:"9".repeat(64)});
});

test("late start after blur cleans only the returned epoch, never the polled/foreign owner",async()=>{
  const f=fixture();await f.authorized();const gate=deferred();let entered=false;
  f.hook(async(method,path,body,next)=>{const result=await next();if(method==="POST" && path.endsWith("/start")){entered=true;await gate.promise;}return result;});
  const pending=f.client.start();while(!entered)await new Promise(resolve=>setImmediate(resolve));
  await f.client.setVisible(false);gate.resolve();await pending;
  const stops=f.posts().filter(call=>call.path.endsWith("/stop") || call.path.endsWith("/revoke"));assert.deepEqual(stops.map(call=>call.body.expected_control_epoch),["epoch-idle","epoch-start-returned"]);
  assert.equal(f.client.snapshot().owner,null);assert.equal(f.client.snapshot().watching,false);
  assert.ok(stops.every(call=>call.path===BASE+"/"+ID+(call.path.endsWith("/revoke")?"/revoke":"/stop")));
});

test("foreign owner status drops local authority without any POST, focus cannot reacquire",async()=>{
  const f=fixture();await f.authorized();await f.client.start();const count=f.posts().length;f.foreign();await f.client.refresh();
  assert.equal(f.client.snapshot().owner,null);assert.equal(f.posts().length,count);f.client.setVisible(true);
  await assert.rejects(f.client.start(),/наблюдение/);assert.equal(f.posts().length,count);
});

test("new decoded/displayed original frame renews once; loading, rerender and duplicate frame do not",async()=>{
  const f=fixture();await f.authorized();await f.client.start();f.advance();await f.client.watchOnce();
  assert.equal(f.posts().filter(call=>call.path.endsWith("/observe")).length,0);
  await f.client.displayed(f.client.snapshot().frame.token);await f.client.displayed(f.client.snapshot().frame.token);await f.client.watchOnce();
  assert.equal(f.posts().filter(call=>call.path.endsWith("/observe")).length,1);
});

test("USER scoped STOP remains available while data finish reply is pending",async()=>{
  const f=fixture();await f.authorized();await f.client.start();await f.client.captureStart();const gate=deferred();let entered=false;
  f.hook(async(method,path,body,next)=>{const result=await next();if(method==="POST" && path.endsWith("/finish")){entered=true;await gate.promise;}return result;});
  const finishing=f.client.finish("unknown");while(!entered)await new Promise(resolve=>setImmediate(resolve));
  await f.client.stop();assert.equal(f.client.snapshot().owner,null);gate.resolve();await finishing;
  assert.equal(f.posts().filter(call=>call.path.endsWith("/stop")).length,1);
});

test("lost capture ACK cannot blindly retry or adopt global recording; explicit DATA reconciliation only",async()=>{
  const f=fixture();await f.authorized();await f.client.start();let lost=true;
  f.hook(async(method,path,body,next)=>{const result=await next();if(lost && method==="POST" && path.endsWith("/capture-start")){lost=false;f.pending();throw new Error("lost acknowledgement");}return result;});
  await assert.rejects(f.client.captureStart(),/lost/);await f.client.refresh();
  assert.equal(f.client.snapshot().recordingID,null);assert.equal(f.client.snapshot().recoveryCandidate,RECORD);
  await assert.rejects(f.client.captureStart(),/не повторяйте/);await f.client.reconcile();assert.equal(f.client.snapshot().recordingID,RECORD);
  assert.equal(f.posts().filter(call=>call.path.endsWith("/capture-start")).length,1);
  assert.equal(f.posts().filter(call=>call.path.endsWith("/stop")).length,0);
});

test("a second tab cannot adopt a polled grant or STOP the first tab on blur",async()=>{
  const f=fixture();await f.authorized();await f.client.start();const count=f.posts().length;
  const other=createSourceDemonstrationClient({get:f.get,post:f.post});await other.refresh();await other.selectAttempt(ID);await other.refresh();
  assert.equal(other.snapshot().owner,null);assert.equal(other.snapshot().watching,false);await other.setVisible(false);assert.equal(f.posts().length,count);
  assert.equal(f.client.snapshot().owner.control_epoch,"epoch-start-returned");
});

test("old status read cannot overwrite returned Recenter epoch",async()=>{
  const f=fixture();await f.authorized();await f.client.start();const gate=deferred();let entered=false;
  f.hook(async(method,path,body,next)=>{const result=await next();if(method==="GET" && path.endsWith("/status")){entered=true;await gate.promise;}return result;});
  const old=f.client.refresh();while(!entered)await new Promise(resolve=>setImmediate(resolve));
  await f.client.recenter();gate.resolve();await old;assert.equal(f.client.snapshot().owner.control_epoch,"epoch-recenter-returned");
  assert.equal(f.posts().filter(call=>call.path.endsWith("/stop")).length,0);
});

test("late observe cannot restore a previous Recenter epoch or retire current local owner",async()=>{
  const f=fixture();await f.authorized();await f.client.start();f.advance();await f.client.watchOnce();const gate=deferred();let entered=false;
  f.hook(async(method,path,body,next)=>{const result=await next();if(method==="POST" && path.endsWith("/observe")){entered=true;await gate.promise;}return result;});
  const old=f.client.displayed(f.client.snapshot().frame.token);while(!entered)await new Promise(resolve=>setImmediate(resolve));
  await f.client.recenter();gate.resolve();await old;
  assert.equal(f.client.snapshot().owner.control_epoch,"epoch-recenter-returned");assert.equal(f.client.snapshot().watching,true);
});

test("an unshown observation is retained until actual image display",async()=>{
  const f=fixture();await f.authorized();await f.client.start();f.advance();await f.client.watchOnce();const token=f.client.snapshot().frame.token;
  const count=f.calls.length;await f.client.watchOnce();assert.equal(f.calls.length,count);assert.equal(f.client.snapshot().frame.token,token);
});

test("late authorize after blur revokes only its exact returned grant; focus grants nothing",async()=>{
  const f=fixture();await f.prepared();await f.client.requestChallenge();await f.client.displayed(f.client.snapshot().frame.token);
  for(const key of Object.keys(f.client.snapshot().flags))f.client.affirm(key,true);
  const gate=deferred();let entered=false;
  f.hook(async(method,path,body,next)=>{const result=await next();if(method==="POST" && path.endsWith("/authorize")){entered=true;await gate.promise;}return result;});
  const pending=f.client.authorize();while(!entered)await new Promise(resolve=>setImmediate(resolve));await f.client.setVisible(false);gate.resolve();await pending;
  assert.equal(f.posts().filter(call=>call.path.endsWith("/revoke")).length,1);f.client.setVisible(true);assert.equal(f.client.snapshot().owner,null);assert.equal(f.client.snapshot().watching,false);
});

test("the second shared JPEG consumer rejects foreign boot before authorization",async()=>{
  const f=fixture({headers:{"x-boot-id":"foreign-boot"}});await f.prepared();await assert.rejects(f.client.requestChallenge(),/SHA/);
  for(const key of Object.keys(f.client.snapshot().flags))f.client.affirm(key,true);
  await assert.rejects(f.client.authorize(),/свежий кадр/);assert.equal(f.posts().filter(call=>call.path.endsWith("/authorize")).length,0);
});

test("logout drain reports unconfirmed own STOP and a retry uses exactly the original pair",async()=>{
  const f=fixture();await f.authorized();await f.client.start();
  f.hook(async(method,path,body,next)=>{if(method==="POST" && path.endsWith("/stop"))throw new Error("fixture source unavailable");return next();});
  const cleanup=await f.client.interrupt("logout");assert.equal(cleanup.confirmed,false);assert.equal(f.client.snapshot().cleanupUnconfirmed,true);
  await assert.rejects(f.client.drain(1000),/Вход сохранён/);f.hook(null);await f.client.retryCleanup();await f.client.drain(1000);
  const stops=f.posts().filter(call=>call.path.endsWith("/stop"));assert.equal(stops.length,2);assert.deepEqual(stops[0].body,stops[1].body);
  assert.equal(stops[1].body.expected_control_epoch,"epoch-start-returned");assert.equal(f.client.snapshot().cleanupUnconfirmed,false);
});

test("logout drain waits for late native return AND exact returned-epoch cleanup before losing authentication",async()=>{
  const f=fixture();await f.authorized();const gate=deferred();let entered=false,drained=false;
  f.hook(async(method,path,body,next)=>{const result=await next();if(method==="POST" && path.endsWith("/start")){entered=true;await gate.promise;}return result;});
  const start=f.client.start();while(!entered)await new Promise(resolve=>setImmediate(resolve));await f.client.interrupt("logout");
  const drain=f.client.drain(1000).then(()=>{drained=true;});await new Promise(resolve=>setImmediate(resolve));assert.equal(drained,false);
  gate.resolve();await start;await drain;assert.equal(f.client.snapshot().pendingNativeCommands,0);assert.equal(f.client.snapshot().cleanupUnconfirmed,false);
  assert.equal(f.posts().filter(call=>call.path.endsWith("/stop")).at(-1).body.expected_control_epoch,"epoch-start-returned");
});

test("selecting another attempt cannot silently prepare its displayed task with the previous context",async()=>{
  const f=fixture();await f.prepared();const other=f.addOtherAttempt();await f.client.refresh();await f.client.selectAttempt(other);
  assert.equal(f.client.snapshot().context,null);const count=f.posts().length;
  await assert.rejects(f.client.prepare(),/контекст/);assert.equal(f.posts().length,count);
});
