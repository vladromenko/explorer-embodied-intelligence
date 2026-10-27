"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), vm = require("node:vm"), fs = require("node:fs");
function harness(permissionGate=null) {
  const documentEvents={},windowEvents={},calls=[],timers=[];
  const register=(into,name,cb)=>(into[name]??=[]).push(cb);
  const context=vm.createContext({document:{hidden:false,hasFocus:()=>true,addEventListener:(name,cb)=>register(documentEvents,name,cb)},
    window:{location:{pathname:"/experiments/03"},addEventListener:(name,cb)=>register(windowEvents,name,cb)},
    setInterval:cb=>timers.push(cb),model:{forceRender:false},render:()=>{},toast:()=>{},rosmasterCameraReady:()=>true,
    observedNative:{requirePermission:async()=>{calls.push({permission:true});if(permissionGate)await permissionGate;},chassisAllowed:()=>true},
    api:async()=>({}),post:async(path,body)=>{calls.push({path,body});return {state:path.endsWith('/start')?'CLUTCHED':'ACTIVE',control_epoch:'epoch',source_hold_gate_schema:'explorer_pi_native_explicit_hold_v1',operator_profile:'ARM_ONLY'};},
    esc:value=>String(value??'').replaceAll('<','&lt;'),array:v=>Array.isArray(v)?v:[],count:String,kv:()=>''});
  vm.runInContext(fs.readFileSync('00-control-center/src/explorer/static/direct03.js','utf8'),context);
  const run=source=>vm.runInContext(source,context);
  const event=(kind,selector,button)=>Promise.all((documentEvents[kind]||[]).map(cb=>cb({target:{closest:s=>s===selector?button:null},preventDefault:()=>{}})));
  const click=action=>event('click','button[data-action^="direct03-"]',{disabled:false,dataset:{action}});
  const hold=()=>event('pointerdown','#direct03-held',{disabled:false});
  return {run,calls,click,hold,documentEvents,windowEvents,timers};
}
const status="direct03.status={state:'IDLE',control_epoch:'epoch',registration:{state:'REGISTERED_DECLARATION'},peer_configured:true,physical_path_admission:{state:'PASS',allowed:true},source_hold_gate_schema:'explorer_pi_native_explicit_hold_v1'}";
test('old Pi ABI cannot enable start or request a physical permission',async()=>{
 const h=harness();h.run(status+";delete direct03.status.source_hold_gate_schema");
 assert.match(h.run('direct03Panel()'),/data-action="direct03-start" type="button" disabled/);
 await h.click('direct03-start');assert.equal(h.calls.length,0);
});
test('start returns owned CLUTCHED and no lease is sent before an explicit hold',async()=>{
 const h=harness();h.run(status);await h.click('direct03-start');
 assert.equal(h.run('direct03.owned'),true);assert.equal(h.run('direct03.status.state'),'CLUTCHED');
 await h.run('direct03Lease()');assert.equal(h.calls.filter(c=>c.path?.endsWith('/lease')).length,0);
 await h.hold();await new Promise(r=>setImmediate(r));
 const leases=h.calls.filter(c=>c.path?.endsWith('/lease'));assert.equal(leases.length,1);
 assert.equal(leases[0].body.held,true);assert.equal(h.calls.find(c=>c.path?.endsWith('/start')).body.operator_profile,'ARM_ONLY');
});
test('ARM_ONLY filters base keys even with a hypothetical chassis grant',async()=>{
 const h=harness();h.run(status+";direct03.status.state='ACTIVE';direct03.owned=true;direct03.ownedEpoch='epoch';direct03.held=true;direct03BaseKeys.add('KeyW')");
 await h.run('direct03Lease()');assert.equal(JSON.stringify(h.calls[0].body.base_actions),'{}');
 h.run("direct03.operatorProfile='MOBILE_MANIPULATION'");await h.run('direct03Lease()');
 assert.equal(JSON.stringify(h.calls[1].body.base_actions),'{"forward":1}');
});
test('an unowned tab neither renews nor stops another tab on blur or pointerdown',async()=>{
 const h=harness();h.run(status+";direct03.status.state='ACTIVE';direct03.owned=false");
 await Promise.all(h.windowEvents.blur.map(cb=>cb()));await h.hold();await h.run('direct03Lease()');
 assert.equal(h.calls.length,0);
});
test('source registration facts have concrete operator wording',()=>{
 const h=harness();h.run(status+";direct03.status.registration={state:'UNREGISTERED',missing_cross_map:['OBSERVED_TASK_AXES_AND_ORIENTATION','TWO_OBSERVED_LEADER_GRIPPER_ENDPOINTS']}");
 const html=h.run('direct03Panel()');assert.match(html,/Две визуально подтверждённые пустые позы/);assert.match(html,/привязка осей и ориентации/);
 assert.doesNotMatch(html,/Доставка на приводы/);
});

test('scoped STOP uses retained ownership epoch, never a polled newer attempt',async()=>{
 const h=harness();h.run(status+";direct03.owned=true;direct03.ownedEpoch='owned-old';direct03.status.control_epoch='new-foreign'");
 await h.run('direct03Stop()');assert.equal(h.calls.length,1);
 assert.equal(h.calls[0].body.control_epoch,'owned-old');
});
test('fresh unowned STOP has no source epoch to revoke and sends no POST',async()=>{
 const h=harness();h.run(status);await h.run('direct03Stop()');assert.equal(h.calls.length,0);
});

test('blur while checking permission cannot STOP a foreign attempt or issue a late begin',async()=>{
 let release;const gate=new Promise(resolve=>release=resolve);const h=harness(gate);h.run(status);
 const pending=h.click('direct03-start');await new Promise(r=>setImmediate(r));
 await Promise.all(h.windowEvents.blur.map(cb=>cb()));release();await pending;
 assert.equal(h.calls.filter(c=>c.path).length,0);
});

test('open real-input continuity gate prevents permission requests and physical begin',async()=>{
 const h=harness();h.run(status+";direct03.status.physical_path_admission={state:'OPEN',allowed:false}");
 assert.match(h.run('direct03Panel()'),/data-action="direct03-start" type="button" disabled/);
 await h.click('direct03-start');assert.equal(h.calls.length,0);
});
