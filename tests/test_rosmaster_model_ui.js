"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), vm = require("node:vm"), fs = require("node:fs");
function harness(respond = async () => ({})) {
  const listeners = {}, calls = [];
  const context = vm.createContext({document: {addEventListener: (name, cb) => listeners[name] = cb}, window: {location: {pathname: "/experiments/02"}},
    model: {jobs: []}, array: value => Array.isArray(value) ? value : [], count: value => String(value),
    esc: value => String(value ?? "").replaceAll("<", "&lt;"), render: () => {}, toast: () => {}, structuredClone, queueMicrotask,
    api: async path => {calls.push({path,method:"GET"}); return respond(path);},
    post: async (path,body) => {calls.push({path,body,method:"POST"}); return {id:"queued",config:{}};}});
  vm.runInContext(fs.readFileSync("00-control-center/src/explorer/static/skill-workflow.js", "utf8"), context);
  const run = source => vm.runInContext(source, context);
  return {run,calls,listeners};
}
test("model panel never loads model or posts automatically; unavailable versions cannot be evaluated", () => {
  const {run,calls} = harness();
  run('skillWorkflowUI.state={confirmed:true}; skillWorkflowUI.models=[]');
  const html = run('skillWorkflowModelsPanel()');
  assert.match(html,/Готовых моделей пока нет/); assert.match(html,/data-skill-action="evaluate" disabled/);
  assert.match(html,/NATIVE_REFERENCE_UNAVAILABLE/); assert.equal(calls.length,0);
});
test("saved DEV result and live-source fault remain separate from physical skill mastery and HTML", () => {
  const {run,calls} = harness();
  run(`skillWorkflowUI.state={confirmed:true}; skillWorkflowUI.modelJob="model";
    skillWorkflowUI.models=[{model_job_id:"model",compatible_native9_106:true,save_reload_passed:true,optimizer_steps:4,aggregate_sha256:"dataset-SHA"}];
    skillWorkflowUI.checks=[{id:"dev",config:{model_job_id:"model"},status:"completed",kind:"rosmaster_skill_eval",result:{model_job_id:"model",evaluation:{ACT9:{evaluated_samples:10,action_error:{arm:{mae_native:1.25,unit:"degree"}}}}}},
      {id:"shadow",config:{model_job_id:"model"},status:"failed",error:"NATIVE_REFERENCE_UNAVAILABLE <script>"}];`);
  const html = run('skillWorkflowModelsPanel()');
  assert.match(html,/MAE 1.2500 degree/); assert.match(html,/Полный навык: не оценён/);
  assert.match(html,/dataset-SHA/); assert.match(html,/NATIVE_REFERENCE_UNAVAILABLE &lt;script>/);
  assert.doesNotMatch(html,/<script>/); assert.equal(calls.length,0);
});
test("explicit evaluation posts only exact server model ID and skill version", async () => {
  const {run,calls,listeners} = harness();
  run(`skillWorkflowUI.state={confirmed:true,spec:{skill_id:"chosen",version:2}}; skillWorkflowUI.modelJob="saved";
    skillWorkflowUI.models=[{model_job_id:"saved",compatible_native9_106:true}];`);
  listeners.click({target:{closest:()=>({disabled:false,dataset:{skillAction:"evaluate"}})}});
  await new Promise(resolve=>setImmediate(resolve));
  const posts=calls.filter(row=>row.method==="POST"); assert.equal(posts.length,1);
  assert.equal(posts[0].path,"/api/skill-workflow/chosen/evaluate");
  assert.equal(JSON.stringify(posts[0].body),JSON.stringify({version:2,model_job_id:"saved"}));
});
test("training uses the measured compact CPU profile and explicit optimizer budget", async () => {
  const {run,calls,listeners} = harness();
  run('skillWorkflowUI.state={confirmed:true,train_ready:true,spec:{skill_id:"chosen",version:2}};skillWorkflowUI.optimizerSteps=240');
  listeners.click({target:{closest:()=>({disabled:false,dataset:{skillAction:"train"}})}});
  await new Promise(resolve=>setImmediate(resolve));
  const posts=calls.filter(row=>row.method==="POST"); assert.equal(posts.length,1);
  assert.equal(posts[0].path,"/api/skill-workflow/chosen/train");
  assert.equal(JSON.stringify(posts[0].body.settings),JSON.stringify({act_steps:240,batch_size:2,image_size:32,chunk_size:2,device:"cpu"}));
});
test("invalid optimizer budget cannot enqueue a model", async () => {
  const {run,calls,listeners} = harness();
  run('skillWorkflowUI.state={confirmed:true,train_ready:true,spec:{skill_id:"chosen",version:2}};skillWorkflowUI.optimizerSteps=0');
  listeners.click({target:{closest:()=>({disabled:false,dataset:{skillAction:"train"}})}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.filter(row=>row.method==="POST").length,0);
});
