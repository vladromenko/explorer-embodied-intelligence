"use strict";
const test=require('node:test'),assert=require('node:assert/strict');
const {createResearchExportClient,researchExportPanel,bindResearchExport}=require('../00-control-center/src/explorer/static/research-export.js');
const id='a'.repeat(32),other='b'.repeat(32),clone=value=>JSON.parse(JSON.stringify(value));
const fixture=()=>({choices:[{id,project_id:'04',kind:'so101_skill_train',status:'failed',label:'Неудачная модель сохраняется'}],exports:[]});
test('construct/render/bind never post; explicit selection submits only current job IDs',async()=>{
 const calls=[],data=fixture(),client=createResearchExportClient({get:async path=>{calls.push(['GET',path]);return clone(data);},post:async(path,body)=>{calls.push(['POST',path,body]);return {id:other};}});
 const handlers={},root={addEventListener:(event,fn)=>handlers[event]=fn,removeEventListener:event=>delete handlers[event],contains:()=>true};
 const unbind=bindResearchExport(root,client);researchExportPanel(client.snapshot());assert.equal(calls.length,0);
 await client.refresh();assert.throws(()=>client.choose('/etc/passwd',true));assert.throws(()=>client.choose(other,true));client.choose(id,true);
 await client.create();assert.deepEqual(calls.find(row=>row[0]==='POST'),['POST','/api/research/exports',{job_ids:[id]}]);assert.deepEqual(client.snapshot().selected,[]);unbind();assert.deepEqual(handlers,{});
});
test('failed enqueue retains selection/error and cannot silently start new jobs on refresh',async()=>{
 let posts=0;const client=createResearchExportClient({get:async()=>fixture(),post:async()=>{posts++;throw new Error('network unknown');}});
 await client.refresh();client.choose(id,true);await assert.rejects(client.create(),/network unknown/);assert.deepEqual(client.snapshot().selected,[id]);assert.match(client.snapshot().error,/network unknown/);await client.refresh();assert.equal(posts,1);
});
test('late GET cannot replace post-refresh state and disappeared jobs are removed from selection',async()=>{
 let resolve,reads=0;const pending=new Promise(yes=>resolve=yes),newer=fixture();newer.choices=[];
 const client=createResearchExportClient({get:async()=>++reads===2?pending:reads===1?fixture():newer,post:async()=>({id:other})});
 await client.refresh();client.choose(id,true);const old=client.refresh();await client.create();resolve(fixture());await old;assert.deepEqual(client.snapshot().data.choices,[]);assert.deepEqual(client.snapshot().selected,[]);
});
test('partial export remains partial; only fixed completed download link is rendered and text is escaped',()=>{
 const data=fixture();data.exports=[{id:other,status:'completed',download_url:`/api/research/exports/${other}/download`,result:{verification:'PARTIAL',independent_restore_verified:true,archive_sha256:'c'.repeat(64),files:4}},{id,status:'completed',download_url:'javascript:control()',error:'<img onerror=move()>',result:{verification:'COMPLETE_UNVERIFIED'}}];
 const html=researchExportPanel({data,selected:[]});assert.match(html,/Частичный экспорт/);assert.match(html,/не заменяет резервную копию/);assert.equal((html.match(/Скачать выбранные свидетельства/g)||[]).length,1);assert.ok(!html.includes('javascript:'));assert.ok(!html.includes('<img'));assert.match(html,/&lt;img/);
});
test('busy and disabled/detached DOM controls cannot duplicate enqueue',async()=>{
 let finish,posts=0;const client=createResearchExportClient({get:async()=>fixture(),post:async()=>{posts++;return new Promise(yes=>finish=yes);}});
 await client.refresh();client.choose(id,true);const first=client.create();await assert.rejects(client.create());assert.throws(()=>client.choose(id,false));assert.equal(posts,1);finish({id:other});await first;
});
