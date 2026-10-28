"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), vm = require("node:vm"), fs = require("node:fs");
function harness(permissionGate=null) {
  const documentEvents={},windowEvents={},calls=[],timers=[];
  const register=(into,name,cb)=>(into[name]??=[]).push(cb);
  const context=vm.createContext({document:{hidden:false,hasFocus:()=>true,addEventListener:(name,cb)=>register(documentEvents,name,cb)},
    window:{location:{pathname:"/experiments/03"},addEventListener:(name,cb)=>register(windowEvents,name,cb)},
    setInterval:cb=>timers.push(cb),model:{forceRender:false},render:()=>{},toast:message=>calls.push({toast:message}),rosmasterCameraReady:()=>true,startCamera:()=>calls.push({camera:'live'}),
    stopKeyboard:async()=>calls.push({cleanup:'keyboard'}),browserGamepad:{disconnect:async()=>calls.push({cleanup:'gamepad'})},
    sourceDemonstration:{drain:async()=>calls.push({cleanup:'source04'})},nativeArmNeedsHome:()=>false,
    observedNative:{connect:async(mode,options)=>{calls.push({permission:true,mode,options});if(permissionGate)await permissionGate;},requirePermission:async()=>{calls.push({permission:true});if(permissionGate)await permissionGate;},chassisAllowed:()=>true},
    api:async path=>{calls.push({get:path});return {state:'IDLE',control_epoch:'fresh',source_hold_gate_schema:'explorer_pi_native_explicit_hold_v1',physical_path_admission:{allowed:true}};},
    post:async(path,body)=>{calls.push({path,body});if(path.endsWith('/start'))return {state:'CLUTCHED',control_epoch:'owned',source_hold_gate_schema:'explorer_pi_native_explicit_hold_v1',operator_profile:'MOBILE_MANIPULATION'};if(path.endsWith('/lease'))return {state:'ACTIVE',control_epoch:'owned',source_hold_gate_schema:'explorer_pi_native_explicit_hold_v1',operator_profile:'MOBILE_MANIPULATION'};return {state:'IDLE',control_epoch:'stopped',source_hold_gate_schema:'explorer_pi_native_explicit_hold_v1',physical_path_admission:{allowed:true}};},
    esc:value=>String(value??'').replaceAll('<','&lt;'),array:v=>Array.isArray(v)?v:[],count:String,kv:()=>''});
  vm.runInContext(fs.readFileSync('00-control-center/src/explorer/static/direct03.js','utf8'),context);
  const run=source=>vm.runInContext(source,context);
  const event=(kind,selector,button)=>Promise.all((documentEvents[kind]||[]).map(cb=>cb({target:{closest:s=>s===selector?button:null},preventDefault:()=>{}})));
  const click=action=>run(`direct03Control(${JSON.stringify(action)})`);
  const hold=()=>event('pointerdown','#direct03-held',{disabled:false});
  return {run,calls,click,hold,documentEvents,windowEvents,timers};
}
const status="direct03.status={state:'IDLE',control_epoch:'epoch',registration:{state:'REGISTERED_DECLARATION'},peer_configured:true,physical_path_admission:{state:'PASS',allowed:true},source_hold_gate_schema:'explorer_pi_native_explicit_hold_v1'}";
test('old Pi ABI refuses a physical permission request',async()=>{
 const h=harness();h.run(status+";delete direct03.status.source_hold_gate_schema");
 await h.click('direct03-start');assert.equal(h.calls.filter(c=>c.permission||c.path).length,0);
 assert.match(h.run('direct03.error'),/обновлённый контур Pi/);
});
test('one click starts the current source and immediately renews its held lease',async()=>{
 const h=harness();h.run(status);await h.click('direct03-start');
 assert.equal(h.run('direct03.owned'),true);assert.equal(h.run('direct03.status.state'),'ACTIVE');
 const leases=h.calls.filter(c=>c.path?.endsWith('/lease'));assert.equal(leases.length,1);
 assert.equal(leases[0].body.held,true);assert.equal(h.calls.find(c=>c.path?.endsWith('/start')).body.operator_profile,'ARM_ONLY');
 const permission=h.calls.find(c=>c.permission);assert.equal(permission.options.chassis,false);
 assert.equal(h.calls.filter(c=>c.camera==='live').length,1);
});
test('ARM_ONLY filters base keys while MOBILE_MANIPULATION forwards them',async()=>{
 const h=harness();h.run(status+";direct03.operatorProfile='ARM_ONLY';direct03.status.state='ACTIVE';direct03.owned=true;direct03.ownedEpoch='epoch';direct03.held=true;direct03BaseKeys.add('KeyW')");
 await h.run('direct03Lease()');assert.equal(JSON.stringify(h.calls[0].body.base_actions),'{}');
 h.run("direct03.operatorProfile='MOBILE_MANIPULATION'");await h.run('direct03Lease()');
 assert.equal(JSON.stringify(h.calls[1].body.base_actions),'{"forward":1}');
});
test('an unowned tab neither renews nor stops another tab on blur or pointerdown',async()=>{
 const h=harness();h.run(status+";direct03.status.state='ACTIVE';direct03.owned=false");
 await Promise.all(h.windowEvents.blur.map(cb=>cb()));await h.hold();await h.run('direct03Lease()');
 assert.equal(h.calls.length,0);
});
test('simple panel explains automatic relative alignment',()=>{
 const h=harness();h.run(status);const html=h.run('direct03Panel()');
 assert.match(html,/Совмещать руки вручную не нужно/);assert.match(html,/текущие позы становятся общей начальной точкой/);
 assert.match(html,/Шасси остаётся неподвижным/);
});

test('one new click replaces a retained CLUTCHED source before authorization',async()=>{
 const h=harness();h.run(status+";direct03.status.state='CLUTCHED';direct03.status.control_epoch='old-source';direct03.status.target_control_epoch='old-target'");
 await h.click('direct03-start');
 const operations=h.calls.filter(c=>c.path||c.get||c.permission).map(c=>c.path||(c.get?`GET:${c.get}`:'permission'));
 assert.deepEqual(operations,[
  '/api/robot/teleop/stop','/api/retarget/stop','/api/operator/source-connect','GET:/api/retarget/status','permission','/api/retarget/start','/api/retarget/lease'
 ]);
 assert.equal(h.calls.find(c=>c.path==='/api/retarget/stop').body.control_epoch,'old-source');
 assert.equal(h.run('direct03.owned'),true);assert.equal(h.run('direct03.status.state'),'ACTIVE');
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
 assert.equal(h.calls.filter(c=>c.path?.endsWith('/start')||c.path?.endsWith('/lease')).length,0);
});

test('open real-input continuity gate prevents permission requests and physical begin',async()=>{
 const h=harness();h.run(status+";direct03.status.physical_path_admission={state:'OPEN',allowed:false}");
 await h.click('direct03-start');assert.equal(h.calls.filter(c=>c.permission||c.path).length,0);
 assert.match(h.run('direct03.error'),/continuity-проверка/);
});

test('an observed authorization race is translated into a retryable Russian message',async()=>{
 const h=harness();h.run(status+";observedNative.connect=async()=>{throw new Error('Existing native STOP and neutral inputs required before observed authorization')}");
 await h.click('direct03-start');assert.match(h.run('direct03.error'),/Предыдущее ручное управление/);
});

test('native low-power refusal tells the operator how to restore experiment03',async()=>{
 const h=harness();h.run(status+";observedNative.connect=async()=>{throw new Error('NATIVE_REFERENCE_UNAVAILABLE: Power policy blocks arm commissioning')}");
 await h.click('direct03-start');assert.match(h.run('direct03.error'),/Питание ROSMASTER слишком низкое/);
 assert.match(h.run('direct03.error'),/зарядку или заряженную батарею/);
});
