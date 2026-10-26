"use strict";
// Programmatic HTTP/DOM fixtures; no workers, signals, model imports or device IO.
const test = require("node:test"), assert = require("node:assert/strict");
const {createSharedQueueClient,sharedQueuePanel,bindSharedQueue} = require("../00-control-center/src/explorer/static/queue.js");
const clone=value=>JSON.parse(JSON.stringify(value)), GiB=1024**3, revision="a".repeat(64);
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function job(id="job-one",changed={}) {return {id,kind:"so101_skill_train",project_id:"04",status:"queued",progress:0,scheduling:{revision,persisted:false,metadata:{priority:0,preemptible:true,dependencies:[],not_before:null,resources:{cpu_cores:2,memory_bytes:2*GiB,disk_bytes:2*GiB,gpu_slots:0},backend:"act",skill_id:"skill-a",skill_version:2,experiment_slug:"so101_source_learning",dataset_sha256:null},runtime:{}},...changed};}
function fixture(jobs=[job()]) {return {schema:"explorer_shared_durable_queue_v1",jobs,resources:{cpu_count:8,available_memory_bytes:10*GiB,total_memory_bytes:16*GiB,free_disk_bytes:100*GiB,thermal:[]},plan_preview:{ranked:jobs.map(x=>x.id),start:[],waiting:{"job-one":{reasons:["dependency_waiting:ingest-job","available_UMA_memory_below_reserve_plus_budgets"]}},cpu_capacity:6,cpu_reserved_or_selected:4,gpu_reserved_or_selected:1,UMA_runtime_reserve_bytes:2.5*GiB,remaining_memory_budget_bytes:5.5*GiB},service_budget_seconds:{so101_source_learning:12},read_only_preview:true};}
async function rig(state=fixture(),overrides={}) {const calls=[];const client=createSharedQueueClient({get:async path=>{calls.push(["GET",path]);return clone(state);},post:async(path,body)=>{calls.push(["POST",path,body]);return {metadata:{...state.jobs[0].scheduling.metadata,...body.options},revision:"b".repeat(64),runtime:{},persisted:true};},...overrides});await client.refresh();return {client,calls,state};}
test("construction/render/bind are inert; explicit refresh is read-only GET",async()=>{
  const calls=[],client=createSharedQueueClient({get:async path=>{calls.push(path);return fixture();},post:async()=>{throw new Error("implicit mutation");}}),listeners={};
  const root={addEventListener:(name,fn)=>listeners[name]=fn,removeEventListener:name=>delete listeners[name],contains:()=>true};const cleanup=bindSharedQueue(root,client);sharedQueuePanel(client.snapshot(),[]);assert.equal(calls.length,0);cleanup();assert.deepEqual(listeners,{});await client.refresh();assert.deepEqual(calls,["/api/queue"]);
});
test("schedule saves only explicit scheduling changes with pinned revision, never source identity/GPU/config",async()=>{
  const {client,calls}=await rig();client.selectJob("job-one");client.edit("priority","4");await client.save();const mutation=calls.find(row=>row[0]==="POST");assert.deepEqual(mutation,["POST","/api/jobs/job-one/schedule",{options:{priority:4},expected_revision:revision}]);assert.throws(()=>client.edit("gpu_slots",1));assert.throws(()=>client.edit("dataset_sha256","f".repeat(64)));
});
test("unchanged form and invalid numbers cause zero POSTs",async()=>{
  const {client,calls}=await rig();client.selectJob("job-one");await assert.rejects(client.save(),/Изменений/);client.edit("priority","1.5");await assert.rejects(client.save(),/целое число/);client.edit("priority","11");await assert.rejects(client.save(),/от −10/);client.edit("priority","0");client.edit("memoryGiB","Infinity");await assert.rejects(client.save(),/положительными/);assert.equal(calls.filter(row=>row[0]==="POST").length,0);
});
test("polling preserves edited draft and original CAS revision; conflict never auto-retries",async()=>{
  const state=fixture();const calls=[];const {client}=await rig(state,{post:async(path,body)=>{calls.push(body);throw new Error("409 scheduling revision changed");}});client.selectJob("job-one");client.edit("priority","7");state.jobs[0].scheduling.revision="c".repeat(64);state.jobs[0].scheduling.metadata.priority=2;await client.refresh();assert.equal(client.snapshot().draft.priority,"7");assert.equal(client.snapshot().revision,revision);assert.equal(client.snapshot().conflict,true);
  await assert.rejects(client.save(),/409/);assert.equal(calls.length,1);assert.equal(calls[0].expected_revision,revision);assert.equal(client.snapshot().draft.priority,"7");client.reloadDraft();assert.equal(client.snapshot().revision,"c".repeat(64));assert.equal(client.snapshot().draft.priority,"2");
});
test("active job allows priority/preemption only and does not silently drop edited budgets",async()=>{
  const state=fixture();const {client,calls}=await rig(state);client.selectJob("job-one");client.edit("memoryGiB","3");state.jobs[0].status="running";await client.refresh();await assert.rejects(client.save(),/только приоритет/);assert.equal(calls.filter(row=>row[0]==="POST").length,0);client.reloadDraft();client.edit("priority","-1");await client.save();assert.deepEqual(calls.find(row=>row[0]==="POST")[2].options,{priority:-1});
});
test("resources convert explicit GiB to integer bytes and obey real server minimum when provided",async()=>{
  const state=fixture();state.jobs[0].minimum_resources={cpu_cores:2,memory_bytes:2*GiB,disk_bytes:2*GiB};const {client,calls}=await rig(state);client.selectJob("job-one");client.edit("memoryGiB","1");await assert.rejects(client.save(),/минимума/);client.edit("memoryGiB","3.5");client.edit("cpuCores","3");await client.save();assert.deepEqual(calls.find(row=>row[0]==="POST")[2].options.resources,{cpu_cores:3,memory_bytes:3.5*GiB});
});
test("dependency picker preserves original IDs, rejects self/duplicates, and exposes known completed dependencies",async()=>{
  const {client,calls}=await rig();client.selectJob("job-one");client.edit("dependencies",["job-one"]);await assert.rejects(client.save(),/не зависит от себя/);client.edit("dependencies",["completed-import","completed-import"]);await assert.rejects(client.save(),/разные существующие/);client.edit("dependencies",["completed-import"]);await client.save();assert.deepEqual(calls.find(row=>row[0]==="POST")[2].options.dependencies,["completed-import"]);
  const html=sharedQueuePanel(client.snapshot(),[{id:"completed-import",project_id:"03",kind:"retarget_import",status:"completed"}]);assert.ok(html.includes('value="completed-import"'));assert.ok(html.includes("Завершено"));
});
test("not-before is plain local datetime input, explicit empty clears it",async()=>{
  const state=fixture();state.jobs[0].scheduling.metadata.not_before=2_000_000_000;const {client,calls}=await rig(state);client.selectJob("job-one");client.edit("notBefore","");await client.save();assert.equal(calls.find(row=>row[0]==="POST")[2].options.not_before,null);
});
test("physical attempts cannot pause/resume/become preemptible",async()=>{
  const state=fixture([job("physical",{kind:"station_rollout",status:"running"})]);state.jobs[0].scheduling.metadata.preemptible=false;const {client,calls}=await rig(state);client.selectJob("physical");client.edit("preemptible",true);await assert.rejects(client.save(),/Физическая попытка/);await assert.rejects(client.operation("physical","pause"),/нового наблюдаемого/);await assert.rejects(client.operation("physical","resume"),/нового наблюдаемого/);assert.equal(calls.filter(row=>row[0]==="POST").length,0);const html=sharedQueuePanel(client.snapshot());assert.ok(!html.includes('data-queue-action="resume"'));
});
test("cancel bypasses outstanding pause; its late response cannot restore resume or undo local cancel intent",async()=>{
  const state=fixture([job("job-one",{status:"running"})]),paused=deferred(),calls=[];
  const {client}=await rig(state,{post:async(path,body)=>{calls.push(path);if(path.endsWith("/pause"))return paused.promise;state.jobs[0].status="cancel_requested";return clone(state.jobs[0]);}});
  const pending=client.operation("job-one","pause");await client.operation("job-one","cancel");paused.resolve(job("job-one",{status:"paused"}));assert.deepEqual(await pending,{ignored:true});assert.equal(client.snapshot().data.jobs[0].status,"cancel_requested");await assert.rejects(client.operation("job-one","resume"),/После запроса отмены/);assert.deepEqual(calls,["/api/jobs/job-one/pause","/api/jobs/job-one/cancel"]);
});
test("failed cancel keeps uncertainty instead of auto-resuming or claiming completed cancellation",async()=>{
  const {client}=await rig(fixture([job("job-one",{status:"running"})]),{post:async()=>{throw new Error("network timeout");}});await assert.rejects(client.operation("job-one","cancel"));assert.equal(client.snapshot().cancelled["job-one"],true);assert.match(client.snapshot().error,/результат сервера неизвестен/);await assert.rejects(client.operation("job-one","pause"),/После запроса отмены/);
});
test("checkpoint request still holds memory; only real receipt plus process exit shows checkpoint release",()=>{
  const one=job("pending",{status:"pause_requested"});one.scheduling.runtime={phase:"waiting_for_actual_optimizer_checkpoint",memory_retained:true,exact_optimizer_resume:true};let html=sharedQueuePanel({data:fixture([one])});assert.ok(html.includes('data-queue-pause="pending"'));assert.ok(!html.includes("память процесса освобождена"));
  one.status="paused";one.scheduling.runtime={phase:"paused_checkpoint_process_exited",memory_retained:false,exact_optimizer_resume:true,process_exit_code:75,checkpoint_pause_receipt:{optimizer_RNG_sampler_durably_saved:true,checkpoint_sha256:"b".repeat(64),checkpoint_pointer_sha256:"c".repeat(64)},completed_updates:8};html=sharedQueuePanel({data:fixture([one])});assert.ok(html.includes('data-queue-pause="checkpoint"'));assert.ok(html.includes("память процесса освобождена"));assert.ok(html.includes("optimizer updates: 8"));
  delete one.scheduling.runtime.checkpoint_pause_receipt;html=sharedQueuePanel({data:fixture([one])});assert.ok(!html.includes('data-queue-pause="checkpoint"'));
});
test("SIGSTOP pause honestly retains memory/GPU and never masquerades as checkpoint exit",()=>{
  const one=job("held",{status:"paused"});one.scheduling.runtime={phase:"paused_process_memory_retained",pause_mode:"SIGSTOP_no_exact_resume",memory_retained:true,exact_optimizer_resume:false};const html=sharedQueuePanel({data:fixture([one])});assert.ok(html.includes("SIGSTOP: процесс приостановлен, память и GPU lease удерживаются"));assert.ok(!html.includes("память процесса освобождена"));
});
test("unknown readings remain unknown; reservation/preview is never measured VRAM or optimizer compute",()=>{
  const html=sharedQueuePanel({data:{jobs:[job()],resources:{},plan_preview:{},service_budget_seconds:{source:20}}});assert.ok(html.includes("не измерено"));assert.ok(html.includes("неизвестен; не восстановлен догадкой"));assert.ok(html.includes("отдельная свободная VRAM не выдумывается"));assert.ok(html.includes("не как число optimizer updates"));assert.ok(!html.includes("0.00 GiB / 0.00 GiB"));
});
test("renderer escapes labels/reasons/drafts and contains no scientific config or GPU edit fields",async()=>{
  const state=fixture();state.jobs[0].kind='<script>start()</script>';state.plan_preview.waiting["job-one"].reasons=['<img src=x onerror=move()>'];const {client}=await rig(state);client.selectJob("job-one");const html=sharedQueuePanel({...client.snapshot(),error:'<script>move()</script>'});assert.ok(!html.includes("<script>"));assert.ok(html.includes("&lt;script&gt;"));assert.ok(!html.includes('data-queue-field="gpu_slots"'));assert.ok(!html.includes('data-queue-field="dataset_sha256"'));assert.ok(!html.includes("<pre>"));
});
test("binding cleanup, disabled controls and outside events never mutate",async()=>{
  const {client,calls}=await rig(),listeners={};const root={addEventListener:(name,handler)=>listeners[name]=handler,removeEventListener:name=>delete listeners[name],contains:target=>target.inside};const cleanup=bindSharedQueue(root,client);const before=calls.length;
  listeners.click({target:{closest:()=>({inside:true,disabled:true,dataset:{queueAction:"cancel",queueJob:"job-one"}})}});listeners.click({target:{closest:()=>({inside:false,dataset:{queueAction:"cancel",queueJob:"job-one"}})}});assert.equal(calls.length,before);cleanup();assert.deepEqual(listeners,{});
});
test("edits typed while schedule POST waits survive its response and use the new accepted revision",async()=>{
  const state=fixture(),saved=deferred();const {client}=await rig(state,{post:async()=>saved.promise});client.selectJob("job-one");client.edit("priority","4");const saving=client.save();client.edit("priority","9");state.jobs[0].scheduling.metadata.priority=4;state.jobs[0].scheduling.revision="b".repeat(64);saved.resolve(clone(state.jobs[0].scheduling));await saving;assert.equal(client.snapshot().draft.priority,"9");assert.equal(client.snapshot().revision,"b".repeat(64));assert.equal(client.snapshot().editsRetainedAfterSave,true);
});
test("missing server revision cannot weaken schedule CAS into unconditional save",async()=>{
  const state=fixture();delete state.jobs[0].scheduling.revision;const {client,calls}=await rig(state);assert.throws(()=>client.selectJob("job-one"),/ревизию/);assert.equal(calls.filter(row=>row[0]==="POST").length,0);
});
