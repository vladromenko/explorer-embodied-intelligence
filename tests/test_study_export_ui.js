"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {createStudyExportClient,studyExportPanel,bindStudyExport}=require("../00-control-center/src/explorer/static/study-export.js");
const id="transfer-hover-development-v9-20261026",job="a".repeat(32),clone=value=>JSON.parse(JSON.stringify(value));
const fixture=()=>({studies:[{study_id:id,title:"Четыре попытки V9",attempts:4}],exports:[]});

test("construct render and bind are inert; explicit current registered ID is the entire POST body",async()=>{
 const calls=[],client=createStudyExportClient({get:async path=>{calls.push(["GET",path]);return fixture();},post:async(path,body)=>{calls.push(["POST",path,body]);return {id:job};}});
 const listeners={},root={addEventListener:(type,fn)=>listeners[type]=fn,removeEventListener:type=>delete listeners[type],contains:()=>true};
 const unbind=bindStudyExport(root,client);studyExportPanel(client.snapshot());assert.equal(calls.length,0);
 await client.refresh();assert.throws(()=>client.choose("/etc/passwd"));assert.throws(()=>client.choose("unregistered"));client.choose(id);await client.create();
 assert.deepEqual(calls.find(row=>row[0]==="POST"),["POST","/api/research/studies",{study_id:id}]);assert.equal(client.snapshot().selected,"");
 unbind();assert.deepEqual(listeners,{});
});
test("duplicate enqueue and selection during request are blocked; errors retain selection without autoretry",async()=>{
 let finish,posts=0;const client=createStudyExportClient({get:async()=>fixture(),post:async()=>{posts++;return new Promise((yes,no)=>finish=no);}});
 await client.refresh();client.choose(id);const active=client.create();await assert.rejects(client.create());assert.throws(()=>client.choose(id));assert.equal(posts,1);
 finish(new Error("network outcome unknown"));await assert.rejects(active,/unknown/);assert.equal(client.snapshot().selected,id);assert.match(client.snapshot().error,/unknown/);
 await client.refresh();assert.equal(posts,1);
});
test("late GET cannot restore obsolete selection/state after successful enqueue",async()=>{
 let resolve,reads=0;const pending=new Promise(yes=>resolve=yes);
 const client=createStudyExportClient({get:async()=>++reads===1?fixture():reads===2?pending:{studies:[],exports:[]},post:async()=>({id:job})});
 await client.refresh();client.choose(id);const stale=client.refresh();await client.create();resolve(fixture());await stale;
 assert.deepEqual(client.snapshot().data.studies,[]);assert.equal(client.snapshot().selected,"");
});
test("destroy fences late GET and prevents all further mutation",async()=>{
 let finish,changes=0,posts=0;const client=createStudyExportClient({get:()=>new Promise(yes=>finish=yes),post:async()=>{posts++;},onChange:()=>changes++});
 const pending=client.refresh();client.destroy();finish(fixture());await pending;
 assert.equal(client.snapshot().data,null);assert.equal(changes,0);assert.throws(()=>client.choose(id));await assert.rejects(client.create());assert.equal(posts,0);
});
test("PARTIAL graph and all outcomes remain visible; untrusted links/HTML never become actions",()=>{
 const data=fixture();data.exports=[{id:job,status:"completed",download_url:`/api/research/studies/${job}/download`,result:{verification:"PARTIAL",independent_restore_verified:true,archive_sha256:"b".repeat(64),files:7640,outcomes:{a:"SUCCESS",b:"FAILURE",c:"SUCCESS",d:"FAILURE"},dependency_closure:{recorded_required_payloads:"VERIFIED",scene_and_cad_assets:"VERIFIED",frozen_benchmark:"VERIFIED"}}},
 {id:"c".repeat(32),status:"completed",download_url:"javascript:move()",error:"<img onerror=move()>",result:{verification:"COMPLETE"}}];
 const html=studyExportPanel({data,selected:id});assert.match(html,/PARTIAL/);assert.match(html,/успехов: 2, неуспехов: 2/);assert.match(html,/target development/);assert.match(html,/стационарное основание/);
 assert.equal((html.match(/Скачать Study со всеми попытками/g)||[]).length,1);assert.ok(!html.includes("javascript:"));assert.ok(!html.includes("<img"));assert.match(html,/&lt;img/);
});
test("disabled or detached buttons cannot submit and unbind removes handlers",async()=>{
 let posts=0;const client=createStudyExportClient({get:async()=>fixture(),post:async()=>{posts++;return {id:job};}});
 await client.refresh();client.choose(id);const handlers={},root={contains:()=>false,addEventListener:(type,fn)=>handlers[type]=fn,removeEventListener:type=>delete handlers[type]};
 const unbind=bindStudyExport(root,client);handlers.click({target:{closest:()=>({disabled:false,dataset:{studyExportAction:"create"}})}});
 root.contains=()=>true;handlers.click({target:{closest:()=>({disabled:true,dataset:{studyExportAction:"create"}})}});await Promise.resolve();assert.equal(posts,0);unbind();assert.deepEqual(handlers,{});
});
