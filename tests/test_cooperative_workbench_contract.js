"use strict";
// Real temporary Store + frozen CooperativeService fixture over a JSON-line API
// bridge. Fake source/camera/HTTP packets only; NO actual nodes/devices/actuators.
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),os=require("node:os"),{spawn}=require("node:child_process"),readline=require("node:readline");
const {createCooperativeWorkbenchClient}=require("../00-control-center/src/explorer/static/cooperative-workbench.js");
const backendRoot=process.env.EXPLORER_COOPERATIVE_CONTRACT_ROOT || path.resolve(__dirname,"..");
const available=fs.existsSync(path.join(backendRoot,"00-control-center/src/explorer/cooperative_service.py"));
const bridgeSource=String.raw`
import json,sys,tempfile,inspect
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from test_cooperative_service import fixture
from explorer.cooperative_service import CooperativeService
folder=tempfile.TemporaryDirectory(prefix='explorer-cooperative-js-contract-')
service,store,calls,active,contexts,native_client=fixture(Path(folder.name))
print(json.dumps({'ready':True,'source':inspect.getfile(CooperativeService)}),flush=True)
try:
    for line in sys.stdin:
        request=json.loads(line)
        try:
            route=urlparse(request['path'])
            if route.path=='/__fixture_summary__':
                result={'calls':[(method,host,path) for method,host,path,body in calls],'active':active,'contexts':contexts,'physical_execution_default':False}
            elif request['method']=='GET' and route.path=='/api/cooperative':
                result=service.snapshot((parse_qs(route.query).get('session_id') or [None])[0])
            elif request['method']=='POST' and route.path.startswith('/api/cooperative/'):
                result=service.command(route.path.rsplit('/',1)[-1],request['body'],authenticated_actor='UI_CONTRACT_SOFTWARE_FIXTURE_OWNER')
            else:raise ValueError('Unknown software fixture path')
            print(json.dumps({'id':request['id'],'ok':True,'result':result},allow_nan=False),flush=True)
        except Exception as error:print(json.dumps({'id':request['id'],'ok':False,'error':type(error).__name__+':'+str(error)}),flush=True)
finally:
    service.close();native_client.close();folder.cleanup()
`;
test("standalone JS request bodies execute actual temporary native-workflow service lifecycle",{skip:!available,timeout:20000},async()=>{
  const env={...process.env,PYTHONDONTWRITEBYTECODE:"1",PYTHONPATH:["shared","00-control-center/src","01-autonomous-learning/src","tests"].map(item=>path.join(backendRoot,item)).join(":")};
  const managedPython=path.join(os.homedir(),".local/share/explorer/venv/bin/python");
  const child=spawn(process.env.EXPLORER_TEST_PYTHON || (fs.existsSync(managedPython)?managedPython:"python"),["-u","-c",bridgeSource],{cwd:backendRoot,env,stdio:["pipe","pipe","pipe"]});
  let stderr="",serial=0;const pending=new Map();child.stderr.on("data",chunk=>stderr+=chunk);
  let readyResolve,readyReject;const ready=new Promise((yes,no)=>{readyResolve=yes;readyReject=no;});
  const lines=readline.createInterface({input:child.stdout});lines.on("line",line=>{try {const value=JSON.parse(line);if(value.ready)readyResolve(value);else {const waiter=pending.get(value.id);pending.delete(value.id);if(waiter)value.ok?waiter.resolve(value.result):waiter.reject(new Error(value.error));}}catch(error){readyReject(new Error(line+":"+error.message));}});
  child.on("exit",code=>{const error=new Error("Fixture exited "+code+": "+stderr);readyReject(error);for(const waiter of pending.values())waiter.reject(error);});
  const request=(method,url,body={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,method,path:url,body})+"\n");});
  try {
    const loaded=await ready;assert.equal(path.resolve(loaded.source),path.join(backendRoot,"00-control-center/src/explorer/cooperative_service.py"));
    const client=createCooperativeWorkbenchClient({get:url=>request("GET",url),post:(url,body)=>request("POST",url,body),storage:{getItem:()=>null,setItem:()=>{}}});
    await client.refresh();const original=client.snapshot().data;assert.equal(original.physical_execution_default,false);assert.equal(original.native_choices.giver.length,1);assert.equal(original.native_choices.receiver.length,1);
    for(const role of ["giver","receiver"]){const choice=original.native_choices[role][0];client.edit(role+"_choice_id",choice.choice_id);client.edit("phase_maps."+role+".PREPARE",choice.phase_ids[0]);client.edit("native_variants."+role,choice.native_variants.find(value=>value.split==="train").variant_id);}
    client.edit("object_description","Программный тестовый предмет, без физического робота");client.edit("receiver_goal_description","Программно проверить передачу метаданных");client.edit("outcome_criteria","Нет аппаратных команд\nСохранены отдельные native исходники");
    await client.propose();await client.confirm();client.edit("scene","SOFTWARE_FIXTURE_SCENE");client.edit("object_instance","SOFTWARE_FIXTURE_OBJECT");client.edit("stationary_chassis_confirmed",true);await client.createSession();
    client.edit("zone_checked",true);client.edit("zone_observing",true);await client.registerZone();const recording=await client.recordStart();
    assert.match(recording.native_source_errors.receiver,/observed cooperative intent\/actor/); // This fixture has no explicit COMMON receiver grant.
    client.edit("record_phase","PREPARE");await client.recordStage();await client.recordFinish();
    assert.equal(client.snapshot().data.selected_session.recording_id,null);assert.ok(client.snapshot().data.selected_session.last_recording_id);
    // Full annotation-only protocol through the actual JS client, Root service
    // and durable concurrent inbox. These explicit fixture confirmations are
    // software inputs; they assert no physical object hold or actuator motion.
    let receiverHoldPrompts=0;
    const events=["giver_holding","offer_ready","receiver_ready","receiver_at_object","receiver_closed","receiver_hold_confirmed","release_requested","giver_released","transfer_confirmed","receiver_finished"];
    for(const event of events){
      await client.step(event);let completed=false;
      for(let attempt=0;attempt<120&&!completed;attempt++){
        await client.refresh();const current=client.snapshot().data;
        for(const challenge of current.pending_evidence){
          assert.equal(challenge.run_id,current.selected_session.session_id);
          if(challenge.statement==="receiver_holds_object")receiverHoldPrompts++;
          client.edit("evidence."+challenge.challenge_id+".observing",true);
          client.edit("evidence."+challenge.challenge_id+".affirmed",true);
          await client.confirmEvidence(challenge.challenge_id);
        }
        await client.refresh();const row=client.snapshot().data.selected_session;
        assert.notEqual(row.status,"STOPPED",JSON.stringify(row.errors));
        completed=!row.pending_operation;
        if(!completed)await new Promise(resolve=>setTimeout(resolve,5));
      }
      assert.ok(completed,"Actual durable inbox operation must finish: "+event);
      if(event==="receiver_closed")assert.equal(client.snapshot().data.selected_session.protocol_state,"RECEIVER_GRASP_CHECK");
    }
    assert.equal(receiverHoldPrompts,2,"Closure is not hold; release requires a second fresh independent fixture observation");
    assert.equal(client.snapshot().data.selected_session.protocol_completed,true);
    assert.equal(client.snapshot().data.selected_session.status,"COMPLETED");
    await client.stop();assert.equal(client.snapshot().data.selected_session.status,"STOPPED");
    const summary=await request("GET","/__fixture_summary__");assert.ok(summary.calls.length>0);assert.ok(summary.calls.every(([method,,route])=>method==="GET" || route.includes("/record/")));assert.ok(summary.calls.every(([, ,route])=>!/authorize|heartbeat|trajectory|cooperative\/guard|cooperative\/action/.test(route)));
    assert.equal(summary.active["nano.test"],null);assert.equal(summary.active["pi.test"],null);assert.equal(summary.contexts["nano.test"].skill_context.project_id,"02");assert.equal(summary.contexts["pi.test"],undefined);
  } finally {
    child.stdin.end();await Promise.race([new Promise(resolve=>child.once("exit",resolve)),new Promise(resolve=>setTimeout(resolve,1000))]);if(child.exitCode===null)child.kill("SIGTERM");lines.close();
  }
});
