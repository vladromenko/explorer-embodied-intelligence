"use strict";
// Data-only06 surface. Geometry, source mastery and physical registration stay separate.
(function(surface){
  const clone=value=>value==null?value:JSON.parse(JSON.stringify(value));
  const escape=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  function createTransferGeometryClient(options){
    let generation=0,request=null,trainingRequest=null,saved=null;
    const state={choice:null,geometry:null,wizard:null,busy:false,error:"",savedWizardId:"",targetVersion:"",datasets:null,targetJob:null,modelCatalogue:null,targetEvalJob:null,targetDataset:"",trainingMode:"vision_adapt",demonstrations:"5"};
    try{saved=JSON.parse(options.storage?.getItem("explorer.transfer.geometry.v1")||"null");}catch(_){}
    const changed=()=>options.onChange?.();
    const current=pin=>generation===pin.generation && state.choice?.job_id===pin.choice.job_id && state.choice?.spec_sha256===pin.choice.spec_sha256;
    function pin(){
      if(state.choice?.technical_source_ready!==true)throw new Error("Выберите модель с проверенным checkpoint и происхождением");
      return {generation,choice:clone(state.choice)};
    }
    function bound(binding,selected){return binding?.model_job_id===selected.job_id && binding?.spec_sha256===selected.spec_sha256 && (!selected.source_calibration_sha256||binding.source_calibration_sha256===selected.source_calibration_sha256) && (!selected.checkpoint_content_sha256||binding.checkpoint_content_sha256===selected.checkpoint_content_sha256);}
    async function perform(operation){
      const selected=pin();if(state.busy)throw new Error("Дождитесь текущего чтения");
      state.busy=true;state.error="";changed();
      try{return await operation(selected);}catch(error){if(current(selected)){state.error=error.message;changed();}throw error;}
      finally{if(current(selected)){state.busy=false;changed();}}
    }
    function select(choice){generation++;request=null;trainingRequest=null;state.modelCatalogue=null;state.targetEvalJob=null;state.datasets=null;state.targetJob=null;state.targetDataset="";state.choice=clone(choice||null);state.targetVersion=choice?String(choice.skill_version+1):"";state.geometry=null;state.wizard=null;state.error="";state.busy=false;state.savedWizardId=saved?.model_job_id===choice?.job_id?saved.wizard_id:"";changed();}
    async function preview(){return perform(async selected=>{
      const response=await options.get("/api/transfer/geometry?model_job_id="+encodeURIComponent(selected.choice.job_id));
      if(!bound(response?.source_model_binding,selected.choice))throw new Error("Ответ геометрии относится к другой исходной версии");
      if(current(selected)){state.geometry=clone(response);state.wizard=null;changed();}return clone(response);
    });}
    async function create(targetVersion){return perform(async selected=>{
      if(!bound(state.geometry?.source_model_binding,selected.choice)||state.geometry?.geometry_preview_ready!==true)throw new Error("Сначала проверьте геометрию этой исходной версии");
      if(!Number.isSafeInteger(targetVersion)||targetVersion<=selected.choice.skill_version)throw new Error("Целевая версия должна быть больше исходной");
      if(!request||request.model_job_id!==selected.choice.job_id||request.target_version!==targetVersion)request={model_job_id:selected.choice.job_id,target_version:targetVersion,request_id:options.randomUUID()};
      const response=await options.post("/api/transfer/calibration",clone(request));
      if(!bound(response?.source_model_binding,selected.choice)||response?.source_version!==selected.choice.skill_version||response?.target_version!==targetVersion)throw new Error("Сохранённая калибровка относится к другой исходной или целевой версии");
      if(current(selected)){
        state.modelCatalogue=null;state.targetEvalJob=null;state.datasets=null;state.targetJob=null;trainingRequest=null;state.wizard=clone(response);state.targetVersion=String(response.target_version);state.savedWizardId=response.wizard_id;saved={model_job_id:selected.choice.job_id,wizard_id:response.wizard_id};
        try{options.storage?.setItem("explorer.transfer.geometry.v1",JSON.stringify(saved));}catch(_){}changed();
      }return clone(response);
    });}
    async function restore(){return perform(async selected=>{
      if(!state.savedWizardId)throw new Error("Нет сохранённого мастера этой модели");
      const response=await options.get("/api/transfer/calibration/"+encodeURIComponent(state.savedWizardId));
      if(!bound(response?.source_model_binding,selected.choice)||!bound(response?.current_geometry?.source_model_binding,selected.choice)||response?.source_version!==selected.choice.skill_version||!Number.isSafeInteger(response?.target_version)||response.target_version<=response.source_version)throw new Error("Сохранённая исходная версия изменилась");
      if(current(selected)){state.modelCatalogue=null;state.targetEvalJob=null;state.datasets=null;state.targetJob=null;trainingRequest=null;state.wizard=clone(response);state.targetVersion=String(response.target_version);state.geometry=clone(response.current_geometry);changed();}return clone(response);
    });}
    async function loadDatasets(){return perform(async selected=>{
      const active=clone(state.wizard);if(!active)throw new Error("Сначала сохраните подготовку калибровки");
      const response=await options.get("/api/transfer/calibration/"+encodeURIComponent(active.wizard_id)+"/datasets");
      if(response?.wizard_id!==active.wizard_id||response?.target_version!==active.target_version||!bound(response?.source_model_binding,selected.choice))throw new Error("Данные относятся к другому мастеру или исходной версии");
      if(current(selected)&&state.wizard?.wizard_id===active.wizard_id){state.datasets=clone(response);if(!response.datasets?.some(row=>row.ready&&row.dataset_job_id===state.targetDataset))state.targetDataset=response.datasets?.find(row=>row.ready)?.dataset_job_id||"";changed();}return clone(response);
    });}
    function setTraining(name,value){if(!["targetDataset","trainingMode","demonstrations"].includes(name))throw new Error("Неизвестный выбор обучения");state[name]=String(value);changed();}
    async function train(){return perform(async selected=>{
      const active=clone(state.wizard),dataset=state.datasets?.datasets?.find(row=>row.dataset_job_id===state.targetDataset);
      const count=Number(state.demonstrations),mode=state.trainingMode;
      if(!active||dataset?.ready!==true||![5,10,25].includes(count)||!dataset.budgets?.some(row=>row.demonstrations===count&&row.ready)||!["target_only","vision_adapt"].includes(mode))throw new Error("Нужен проверенный целевой dataset и достаточное число исходных показов");
      const body={dataset_job_id:dataset.dataset_job_id,demonstrations:count,mode};
      const signature=JSON.stringify([active.wizard_id,body]);
      if(trainingRequest?.signature!==signature)trainingRequest={signature,body:{...body,request_id:options.randomUUID()}};
      const response=await options.post("/api/transfer/calibration/"+encodeURIComponent(active.wizard_id)+"/train",clone(trainingRequest.body));
      if(response?.kind!=="transfer_target_train"||response?.project_id!=="06"||response?.config?.wizard_id!==active.wizard_id||response?.config?.dataset_job_id!==body.dataset_job_id||response?.config?.demonstrations!==count||response?.config?.mode!==mode||!bound(response?.config?.source_model_binding,selected.choice))throw new Error("Очередь вернула другую привязку обучения");
      if(current(selected)&&state.wizard?.wizard_id===active.wizard_id){state.targetJob=clone(response);changed();}return clone(response);
    });}
    async function loadModels(){return perform(async selected=>{
      const active=clone(state.wizard);if(!active)throw new Error("Сначала восстановите подготовку этой версии");
      const response=await options.get("/api/transfer/calibration/"+encodeURIComponent(active.wizard_id)+"/models");
      if(response?.wizard_id!==active.wizard_id||!bound(response?.source_model_binding,selected.choice))throw new Error("Модели относятся к другой исходной версии");
      if(current(selected)&&state.wizard?.wizard_id===active.wizard_id){state.modelCatalogue=clone(response);changed();}return clone(response);
    });}
    async function evaluateModel(modelJobId){return perform(async selected=>{
      const active=clone(state.wizard),model=state.modelCatalogue?.models?.find(row=>row.model_job_id===modelJobId);
      if(!active||model?.compatible!==true)throw new Error("Выберите сохранённую совместимую целевую модель");
      const response=await options.post("/api/transfer/calibration/"+encodeURIComponent(active.wizard_id)+"/evaluate",{model_job_id:modelJobId});
      if(response?.kind!=="transfer_target_eval"||response?.project_id!=="06"||response?.config?.wizard_id!==active.wizard_id||response?.config?.model_job_id!==modelJobId||response?.config?.partition!=="development"||response?.config?.sealed_test_used!==false||!bound(response?.config?.source_model_binding,selected.choice))throw new Error("Ответ проверки относится к другой модели или выборке");
      if(current(selected)&&state.wizard?.wizard_id===active.wizard_id){state.targetEvalJob=clone(response);changed();}return clone(response);
    });}
    function refreshChoice(choice){const keys=["job_id","spec_sha256","source_calibration_sha256","checkpoint_content_sha256","aggregate_manifest_sha256","technical_source_ready"];if(keys.some(key=>state.choice?.[key]!==choice?.[key]))select(choice);}
    function setTargetVersion(value){state.targetVersion=String(value);}
    return {select,refreshChoice,setTargetVersion,preview,create,restore,loadDatasets,setTraining,train,loadModels,evaluateModel,snapshot:()=>clone(state)};
  }
  function transferTargetModelsPanel(view){
    const catalogue=view.modelCatalogue;
    return `<h3>Сохранённые целевые модели</h3><p>Новая проверка загружает сохранённый ACT9 и считает предсказания по записанному target DEV. Optimizer, sealed и управление роботом не используются. Live-shadow и независимая предметная попытка остаются отдельными проверками.</p><button type="button" class="button secondary" data-transfer-geometry="models" ${view.busy?"disabled":""}>Обновить модели и проверки</button>${catalogue?`<ul>${(catalogue.models||[]).map(row=>`<li><strong>ACT9 · ${row.comparison_mode==="target_only"?"target-only":"адаптация vision"} · ${escape(row.demonstrations)} показов</strong><p>${escape(row.model_job_id)} · ${row.compatible?`Сохранение и reload проверены · обновлений ${escape(row.optimizer_steps)}`:escape(row.reason||row.status)}</p>${row.compatible?`<button type="button" class="button secondary" data-transfer-geometry="evaluate" data-model-job-id="${escape(row.model_job_id)}" ${view.busy?"disabled":""}>Проверить сохранённую модель на DEV</button>`:""}</li>`).join("")}</ul>${!catalogue.models?.length?"<p>Для этого мастера ещё нет целевых моделей.</p>":""}${(catalogue.checks||[]).filter(row=>row.status==="completed").map(row=>`<p>Офлайн DEV: ${escape(row.result?.evaluation?.ACT9?.evaluated_samples)} предсказаний · модель ${escape(row.config?.model_job_id)} · полная предметная задача не проверялась.</p><details><summary>Первые12 предсказаний в native9</summary><ol>${(row.result?.evaluation?.raw_predictions||[]).slice(0,12).map(prediction=>`<li>Кадр ${escape(prediction.sample_index)}: ${escape((prediction.ACT9||[]).map(value=>Number(value).toPrecision(3)).join(", "))}</li>`).join("")}</ol></details>`).join("")}`:""}${view.targetEvalJob?`<p role="status">DEV-проверка в общей очереди: ${escape(view.targetEvalJob.id)}. Обновите список после завершения.</p>`:""}`;
  }
  function transferGeometryPanel(view){
    const choice=view.choice,geometry=view.geometry,wizard=view.wizard,busy=view.busy;
    const reasons=values=>Array.isArray(values)?values:[];
    const status=value=>({SIM_AUTO_GEOMETRY:"SIM: физическая съёмка не требуется",CAPTURE_REQUIRED:"Нужны измеренные виды",READY_TO_FIT:"Можно рассчитать",PASS:"Измерения проверены"}[value]||value);
    const reason=row=>({source_URDF_unit_axis_alignment:"Нужно отдельно проверить соответствие поз follower модели CAD.",source_and_target_metric_gripper_aperture:"Нужно измерить апертуру обоих захватов; сила не определяется по изображению.",current_source_target_object_goal_registration:"Нужно зарегистрировать предмет и цель в обеих сценах.",qualified06_object_skill_source:"Нужна независимая полная предметная попытка исходной модели."}[row.id]||row.reason);
    const button=(action,label,disabled)=>`<button type="button" class="button secondary" data-transfer-geometry="${action}" ${disabled?"disabled":""}>${label}</button>`;
    return `<section class="panel spaced-section transfer-geometry"><div class="panel-heading"><h2>Геометрия и калибровка переноса</h2></div><div class="panel-body"><p>Исходная калибровка follower читается по SHA выбранной модели. CAD leader используется отдельно в опыте03. Энкодерная геометрия доступна до принятия полного предметного навыка; она не подтверждает физическую регистрацию.</p>${view.error?`<p class="form-error" role="alert">${escape(view.error)}</p>`:""}${button("preview","Проверить исходную геометрию",busy||choice?.technical_source_ready!==true)}${view.savedWizardId?button("restore","Восстановить сохранённую калибровку",busy):""}${geometry?`<p role="status">Исходная геометрия проверена · follower · SHA ${escape(geometry.source_model_binding.source_calibration_sha256)}</p>${geometry.source_model_binding.source_domain==="SIMULATION"?"<p class=\"form-hint\">Источник SIM: физические измерения ниже относятся к будущему переносу на оборудование и не блокируют программную геометрию.</p>":""}<h3>Остаются отдельные проверки</h3><ul>${reasons(geometry.audit?.B).map(row=>`<li>${escape(reason(row))}</li>`).join("")}${reasons(geometry.audit?.C).map(row=>`<li>${escape(reason(row))}</li>`).join("")}</ul><label class="form-field">Новая целевая версия этого навыка<input data-transfer-target-version type="number" min="${choice.skill_version+1}" value="${escape(view.targetVersion)}" step="1"></label>${button("create","Сохранить подготовку калибровки",busy)}`:""}${wizard?`<p>Подготовка сохранена · версия ${escape(wizard.source_version)} → ${escape(wizard.target_version)} · ${wizard.domain==="simulation"?"SIM: физические измерения не подтверждаются":"отдельные измерения двух сцен"}</p><ol>${reasons(wizard.steps).map(step=>`<li>${escape(step.title)}: ${escape(status(step.status))} · принято ${escape(step.accepted_views)}, отклонено ${escape(step.rejected_views)}</li>`).join("")}</ol><p>Источник, его версия и SHA закреплены. Целевые показы, обучение и независимая проверка ещё не завершены.</p><h3>Целевые показы и общий ACT9</h3><p>Адаптация переносит только совместимый visual backbone ACT6. Целевые action/state heads новые; normalizer обучается только на target TRAIN. Zero-shot FK/IK проверяется отдельно.</p>${button("datasets","Проверить целевые datasets",busy)}${view.datasets?`<p>Доступно проверенных datasets: ${reasons(view.datasets.datasets).filter(row=>row.ready).length}. Считаются исходные полные показы, не кадры. DEV и sealed не дополняют TRAIN.</p>${reasons(view.datasets.datasets).some(row=>row.ready)?`<label class="form-field">Целевой dataset<select data-transfer-training="targetDataset">${reasons(view.datasets.datasets).filter(row=>row.ready).map(row=>`<option value="${escape(row.dataset_job_id)}" ${view.targetDataset===row.dataset_job_id?"selected":""}>${escape(row.dataset_job_id)} · TRAIN ${row.train_original_demonstrations} · DEV ${row.development_original_demonstrations}</option>`).join("")}</select></label><label class="form-field">Схема обучения<select data-transfer-training="trainingMode"><option value="vision_adapt" ${view.trainingMode==="vision_adapt"?"selected":""}>Адаптация visual backbone</option><option value="target_only" ${view.trainingMode==="target_only"?"selected":""}>Target-only baseline</option></select></label><label class="form-field">Исходных целевых показов<select data-transfer-training="demonstrations">${[5,10,25].map(n=>`<option value="${n}" ${Number(view.demonstrations)===n?"selected":""}>${n}</option>`).join("")}</select></label>${button("train","Поставить ACT9 в общую очередь",busy||!view.datasets.datasets.find(row=>row.dataset_job_id===view.targetDataset)?.budgets?.some(row=>row.demonstrations===Number(view.demonstrations)&&row.ready))}`:"<p>Подходящих целевых данных пока нет. Исходный ACT6 dataset не заменяет target9 показы.</p>"}`:""}${view.targetJob?`<p role="status">Обучение сохранено в общей очереди · ${escape(view.targetJob.id)}. Результаты и состояние доступны в общей очереди; навык ещё не принят.</p>`:""}${transferTargetModelsPanel(view)}`:""}</div></section>`;
  }
  const exported={createTransferGeometryClient,transferGeometryPanel,transferTargetModelsPanel};if(typeof module!=="undefined"&&module.exports)module.exports=exported;else Object.assign(surface,exported);
})(typeof window!=="undefined"?window:globalThis);
