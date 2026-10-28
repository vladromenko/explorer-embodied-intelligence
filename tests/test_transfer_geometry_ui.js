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

function targetFixture({getExtra,postExtra}={}){
  const row=choice(),active=wizard(row),calls=[];
  const data={wizard_id:active.wizard_id,target_version:2,source_model_binding:binding(row),datasets:[{dataset_job_id:'3'.repeat(32),ready:true,train_original_demonstrations:5,development_original_demonstrations:1,budgets:[{demonstrations:5,ready:true},{demonstrations:10,ready:false}]}]};
  const client=createTransferGeometryClient({get:async path=>path.endsWith('/datasets')?(getExtra?getExtra(data):data):response(row),post:async(path,body)=>{
    calls.push([path,body]);if(!path.endsWith('/train'))return active;
    return postExtra?postExtra(body):{id:'4'.repeat(32),kind:'transfer_target_train',project_id:'06',config:{...body,wizard_id:active.wizard_id,source_model_binding:binding(row)}};
  },randomUUID:()=> '55555555-5555-4555-8555-555555555555'});
  client.select(row);return {client,row,active,data,calls};
}

test('target data uses the saved wizard and insufficient original counts never enqueue',async()=>{
  const {client,calls}=targetFixture();await client.preview();await client.create(2);await client.loadDatasets();
  assert.match(transferGeometryPanel(client.snapshot()),/TRAIN 5 · DEV 1/);
  client.setTraining('demonstrations','10');await assert.rejects(client.train(),/достаточное число/);
  assert.equal(calls.filter(row=>row[0].endsWith('/train')).length,0);
  assert.match(transferGeometryPanel(client.snapshot()),/target_only/);
});

test('target training posts only managed IDs and reuses request identity after lost ACK',async()=>{
  let attempts=0;const {client,calls,active,row}=targetFixture({postExtra:body=>{
    if(++attempts===1)throw new Error('lost ACK');return {id:'4'.repeat(32),kind:'transfer_target_train',project_id:'06',config:{...body,wizard_id:active.wizard_id,source_model_binding:binding(row)}};
  }});
  await client.preview();await client.create(2);await client.loadDatasets();await assert.rejects(client.train(),/lost ACK/);await client.train();
  const posts=calls.filter(call=>call[0].endsWith('/train'));assert.deepEqual(posts[0],posts[1]);
  assert.deepEqual(Object.keys(posts[0][1]).sort(),['dataset_job_id','demonstrations','mode','request_id']);
  assert.equal(client.snapshot().targetJob.kind,'transfer_target_train');assert.ok(calls.every(call=>call[0].startsWith('/api/transfer/')));
});

test('late target dataset response cannot attach after source selection changes',async()=>{
  const wait=deferred(),{client,data}=targetFixture({getExtra:()=>wait.promise});
  await client.preview();await client.create(2);const pending=client.loadDatasets();client.select(choice('b'));wait.resolve(data);await pending;
  assert.equal(client.snapshot().datasets,null);assert.equal(client.snapshot().targetJob,null);
});

test('wrong wizard target data and wrong budget queue ACK are rejected',async()=>{
  const bad=targetFixture({getExtra:data=>({...data,target_version:3})});await bad.client.preview();await bad.client.create(2);
  await assert.rejects(bad.client.loadDatasets(),/другому мастеру/);assert.equal(bad.client.snapshot().datasets,null);
  const queue=targetFixture({postExtra:body=>({kind:'transfer_target_train',project_id:'06',config:{...body,demonstrations:25}})});
  await queue.client.preview();await queue.client.create(2);await queue.client.loadDatasets();await assert.rejects(queue.client.train(),/другую привязку/);
  assert.equal(queue.client.snapshot().targetJob,null);
});

test('saved target models use exact wizard binding and only DEV model ID is submitted',async()=>{
  const base=targetFixture(),{client,row,active}=base;
  await client.preview();await client.create(2);
  // A separate client wires actual catalogue/evaluation response contracts.
  const calls=[];const saved=createTransferGeometryClient({get:async path=>path.endsWith('/models')?{wizard_id:active.wizard_id,source_model_binding:binding(row),models:[{model_job_id:'6'.repeat(32),compatible:true,comparison_mode:'target_only',demonstrations:5,optimizer_steps:2}]}:response(row),post:async(path,body)=>{
    if(!path.endsWith('/evaluate'))return active;calls.push([path,body]);return {kind:'transfer_target_eval',project_id:'06',id:'7'.repeat(32),config:{wizard_id:active.wizard_id,model_job_id:body.model_job_id,partition:'development',sealed_test_used:false,source_model_binding:binding(row)}};
  },randomUUID:()=>active.wizard_id});
  saved.select(row);await saved.preview();await saved.create(2);await saved.loadModels();
  assert.match(transferGeometryPanel(saved.snapshot()),/Сохранение и reload проверены/);
  await saved.evaluateModel('6'.repeat(32));assert.deepEqual(calls[0][1],{model_job_id:'6'.repeat(32)});
  assert.equal(saved.snapshot().targetEvalJob.kind,'transfer_target_eval');
  await assert.rejects(saved.evaluateModel('f'.repeat(32)),/совместимую/);
});
