"use strict";
// Data-only06 surface. Geometry, source mastery and physical registration stay separate.
(function(surface){
  const clone=value=>value==null?value:JSON.parse(JSON.stringify(value));
  const escape=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  function createTransferGeometryClient(options){
    let generation=0,request=null,trainingRequest=null,captureRequest=null,saved=null;
    const state={choice:null,geometry:null,wizard:null,busy:false,error:"",savedWizardId:"",targetVersion:"",targetDomain:"simulation",datasets:null,targetJob:null,modelCatalogue:null,targetEvalJob:null,captures:null,selectedCapture:"",targetDataset:"",trainingMode:"vision_adapt",demonstrations:"5"};
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
    function select(choice){generation++;request=null;trainingRequest=null;state.captures=null;state.selectedCapture="";captureRequest=null;state.modelCatalogue=null;state.targetEvalJob=null;state.datasets=null;state.targetJob=null;state.targetDataset="";state.choice=clone(choice||null);state.targetVersion=choice?String(choice.skill_version+1):"";state.targetDomain=choice?.source_domain==="PHYSICAL"?"physical":"simulation";state.geometry=null;state.wizard=null;state.error="";state.busy=false;state.savedWizardId=saved?.model_job_id===choice?.job_id?saved.wizard_id:"";changed();}
    async function preview(){return perform(async selected=>{
      const response=await options.get("/api/transfer/geometry?model_job_id="+encodeURIComponent(selected.choice.job_id));
      if(!bound(response?.source_model_binding,selected.choice))throw new Error("Ответ геометрии относится к другой исходной версии");
      if(current(selected)){state.geometry=clone(response);state.wizard=null;changed();}return clone(response);
    });}
    async function create(targetVersion){return perform(async selected=>{
      if(!bound(state.geometry?.source_model_binding,selected.choice)||state.geometry?.geometry_preview_ready!==true)throw new Error("Сначала проверьте геометрию этой исходной версии");
      if(!Number.isSafeInteger(targetVersion)||targetVersion<=selected.choice.skill_version)throw new Error("Целевая версия должна быть больше исходной");
      state.targetVersion=String(targetVersion);const targetDomain=state.targetDomain;if(!["simulation","physical"].includes(targetDomain))throw new Error("Выберите целевую среду SIM или оборудование");
      if(!request||request.model_job_id!==selected.choice.job_id||request.target_version!==targetVersion||request.target_domain!==targetDomain)request={model_job_id:selected.choice.job_id,target_version:targetVersion,target_domain:targetDomain,request_id:options.randomUUID()};
      const response=await options.post("/api/transfer/calibration",clone(request));
      if(!bound(response?.source_model_binding,selected.choice)||response?.source_version!==selected.choice.skill_version||response?.target_version!==targetVersion||response?.domain!==targetDomain)throw new Error("Сохранённая калибровка относится к другой исходной или целевой версии");
      if(current(selected)&&state.targetDomain===targetDomain&&Number(state.targetVersion)===targetVersion){
        state.captures=null;state.selectedCapture="";captureRequest=null;state.modelCatalogue=null;state.targetEvalJob=null;state.datasets=null;state.targetJob=null;trainingRequest=null;state.wizard=clone(response);state.targetVersion=String(response.target_version);state.targetDomain=response.domain;state.savedWizardId=response.wizard_id;saved={model_job_id:selected.choice.job_id,wizard_id:response.wizard_id};
        try{options.storage?.setItem("explorer.transfer.geometry.v1",JSON.stringify(saved));}catch(_){}changed();
      }return clone(response);
    });}
    async function restore(){return perform(async selected=>{
      if(!state.savedWizardId)throw new Error("Нет сохранённого мастера этой модели");
      const response=await options.get("/api/transfer/calibration/"+encodeURIComponent(state.savedWizardId));
      if(!bound(response?.source_model_binding,selected.choice)||!bound(response?.current_geometry?.source_model_binding,selected.choice)||response?.source_version!==selected.choice.skill_version||!Number.isSafeInteger(response?.target_version)||response.target_version<=response.source_version)throw new Error("Сохранённая исходная версия изменилась");
      if(current(selected)){state.captures=null;state.selectedCapture="";captureRequest=null;state.modelCatalogue=null;state.targetEvalJob=null;state.datasets=null;state.targetJob=null;trainingRequest=null;state.wizard=clone(response);state.targetVersion=String(response.target_version);state.targetDomain=response.domain;state.geometry=clone(response.current_geometry);changed();}return clone(response);
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
    function capturePath(){if(!state.wizard)throw new Error("Сначала сохраните подготовку этой версии");return "/api/transfer/calibration/"+encodeURIComponent(state.wizard.wizard_id);}
    async function readCaptures(selected){
      const active=state.wizard?.wizard_id,response=await options.get(capturePath()+"/captures");
      if(response?.wizard_id!==active||!bound(response?.source_model_binding,selected.choice))throw new Error("Записи относятся к другой исходной версии");
      if(current(selected)&&state.wizard?.wizard_id===active){state.captures=clone(response);if(!response.captures?.some(row=>row.capture_id===state.selectedCapture))state.selectedCapture=response.captures?.at(-1)?.capture_id||"";changed();}return response;
    }
    async function loadCaptures(){return perform(readCaptures);}
    function selectCapture(value){state.selectedCapture=String(value);changed();}
    async function startCapture(metadata){return perform(async selected=>{
      if(state.wizard?.domain!=="physical"||!state.captures?.phases?.length)throw new Error("Выберите оборудование и загрузите список записей/фаз");
      const path=capturePath(),signature=JSON.stringify([path,metadata]);
      if(captureRequest?.signature!==signature)captureRequest={signature,body:{...metadata,phase_id:state.captures.phases[0].id,request_id:options.randomUUID(),session_id:options.randomUUID()}};
      const response=await options.post(path+"/captures",clone(captureRequest.body));
      if(response?.wizard_id!==state.wizard?.wizard_id||response?.capture_id!==captureRequest.body.request_id||!bound(response?.context?.source_model_binding,selected.choice))throw new Error("Ответ записи относится к другой подготовке");
      if(current(selected)){state.selectedCapture=response.capture_id;captureRequest=null;await readCaptures(selected);}return clone(response);
    });}
    async function captureOperation(body){return perform(async selected=>{
      const identity=state.selectedCapture;if(!identity)throw new Error("Выберите сохранённую запись");
      const response=await options.post(capturePath()+"/captures/"+encodeURIComponent(identity),body);
      if(body.operation==="import"){
        if(response?.kind!=="transfer_target_import"||response?.project_id!=="06"||response?.config?.wizard_id!==state.wizard.wizard_id)throw new Error("Неверная очередь импорта");
      }else if(response?.capture_id!==identity||response?.wizard_id!==state.wizard.wizard_id)throw new Error("Ответ относится к другой записи");
      if(current(selected))await readCaptures(selected);return clone(response);
    });}
    async function aggregateCaptures(importJobIds){return perform(async selected=>{
      const response=await options.post(capturePath()+"/aggregate",{import_job_ids:importJobIds});
      if(response?.kind!=="transfer_target_dataset"||response?.project_id!=="06"||response?.status!=="completed")throw new Error("Dataset ещё не собран");
      if(current(selected)){state.targetDataset=response.id;state.datasets=null;changed();}return clone(response);
    });}
    function refreshChoice(choice){const keys=["job_id","spec_sha256","source_calibration_sha256","checkpoint_content_sha256","aggregate_manifest_sha256","technical_source_ready"];if(keys.some(key=>state.choice?.[key]!==choice?.[key]))select(choice);}
    function invalidateEditedWizard(){
      if(state.wizard&&(state.wizard.domain!==state.targetDomain||state.wizard.target_version!==Number(state.targetVersion))){
        generation++;state.busy=false;state.wizard=null;state.datasets=null;state.targetJob=null;state.captures=null;state.selectedCapture="";captureRequest=null;state.modelCatalogue=null;state.targetEvalJob=null;trainingRequest=null;changed();
      }
    }
    function setTargetDomain(value){state.targetDomain=String(value);invalidateEditedWizard();}
    function setTargetVersion(value){state.targetVersion=String(value);invalidateEditedWizard();}
    return {select,refreshChoice,setTargetVersion,setTargetDomain,preview,create,restore,loadDatasets,setTraining,train,loadModels,evaluateModel,loadCaptures,selectCapture,startCapture,captureOperation,aggregateCaptures,snapshot:()=>clone(state)};
  }
  function transferCapturePanel(view){
    if(view.wizard?.domain!=="physical")return "<p>Для записи native9 выберите целевую среду «Оборудование». SIM-показы создаются отдельным симулятором.</p>";
    const list=view.captures,record=list?.captures?.find(row=>row.capture_id===view.selectedCapture),busy=view.busy;
    const button=(action,label,disabled=false)=>`<button type="button" class="button secondary" data-transfer-capture="${action}" ${busy||disabled?"disabled":""}>${label}</button>`;
    const outcomes=name=>`<select ${name}><option value="unknown">Не установлено</option><option value="success">Успех — наблюдал оператор</option><option value="failure">Неудача</option></select>`;
    return `<h3>Запись целевых показов</h3><p>Используется существующий recorder ROSMASTER и его камера. Кнопки ниже записывают данные; управление рукой остаётся в существующей панели. Шасси неподвижно. Finish сохраняет явные наблюдения; одобрение выполняется отдельно.</p>${button("list","Обновить записи и импорт")}${list?`<div data-transfer-capture-form><label class="form-field">Раздел<select data-transfer-capture-field="partition"><option value="train">TRAIN</option><option value="development">DEV · отдельная сцена/объект/сессия</option><option value="sealed_test">Sealed · независимая проверка</option></select></label>${[["scene_id","Сцена"],["object_instance_id","Экземпляр предмета"],["group_id","Группа независимости"],["variant_id","Вариант задачи"]].map(([key,label])=>`<label class="form-field">${label}<input data-transfer-capture-field="${key}" maxlength="160"></label>`).join("")}${button("start","Начать запись")}</div><label class="form-field">Сохранённая запись<select data-transfer-capture-select><option value="">Выберите запись</option>${list.captures.map(row=>`<option value="${escape(row.capture_id)}" ${row.capture_id===view.selectedCapture?"selected":""}>${escape(row.context.split.partition)} · ${escape(row.state)} · ${escape(row.capture_id)}</option>`).join("")}</select></label>${record?`<p role="status">${escape(record.state)} · outcome ${escape(record.outcome?.operator_outcome||"ещё не указан")} · review ${escape(record.decision||"отдельное одобрение не выполнено")}</p>${button("refresh","Проверить завершение / восстановить ответ")}${record.state==="START_PENDING"?button("abandon","Закрыть неподтверждённый DATA-запрос без удаления записей"):""}${button("pause","Пауза",record.state!=="RECORDING")}${button("resume","Продолжить запись",record.state!=="PAUSED")}<label class="form-field">Текущая фаза<select data-transfer-capture-stage>${(list.phases||[]).map(row=>`<option value="${escape(row.id)}">${escape(row.title||row.id)}</option>`).join("")}</select></label>${button("stage","Отметить фазу",!["RECORDING","PAUSED"].includes(record.state))}<fieldset><legend>Наблюдаемый результат показа</legend><label class="form-field">Полная задача${outcomes("data-transfer-capture-outcome")}</label>${(list.phases||[]).map(row=>`<label class="form-field">${escape(row.title||row.id)}${outcomes(`data-transfer-phase-outcome="${escape(row.id)}"`)}</label>`).join("")}${button("finish","Завершить и сохранить",!["RECORDING","PAUSED"].includes(record.state))}</fieldset>${button("approve","Одобрить сохранённую запись",record.state!=="SEALED")}${button("reject","Отклонить сохранённую запись",record.state!=="SEALED")}${button("import","Импортировать одобренный оригинал",record.state!=="SEALED"||record.decision!=="approve")}`:""}<h4>Импорт в общей очереди</h4><ul>${(list.imports||[]).map(job=>`<li>${job.status==="completed"?`<input type="checkbox" data-transfer-import-id="${escape(job.id)}">`:""}${escape(job.id)} · ${escape(job.status)} · ${escape(job.error||"")}</li>`).join("")}</ul>${button("aggregate","Собрать dataset из выбранных импортов")}`:""}`;
  }
  function transferTargetModelsPanel(view){
    const catalogue=view.modelCatalogue;
    return `<h3>Сохранённые целевые модели</h3><p>Новая проверка загружает сохранённый ACT9 и считает предсказания по записанному target DEV. Optimizer, sealed и управление роботом не используются. Live-shadow и независимая предметная попытка остаются отдельными проверками.</p><button type="button" class="button secondary" data-transfer-geometry="models" ${view.busy?"disabled":""}>Обновить модели и проверки</button>${catalogue?`<p>Сохранённых моделей: ${(catalogue.models||[]).length}. Другие запуски обучения: ${(catalogue.training_jobs||[]).length}; их состояния сохранены в общей очереди.</p><ul>${(catalogue.models||[]).map(row=>`<li><strong>ACT9 · ${row.comparison_mode==="target_only"?"target-only":"адаптация vision"} · ${escape(row.demonstrations)} показов</strong><p>${escape(row.model_job_id)} · ${row.compatible?`Сохранение и reload проверены · обновлений ${escape(row.optimizer_steps)}`:escape(row.reason||row.status)}</p>${row.compatible?`<button type="button" class="button secondary" data-transfer-geometry="evaluate" data-model-job-id="${escape(row.model_job_id)}" ${view.busy?"disabled":""}>Проверить сохранённую модель на DEV</button>`:""}</li>`).join("")}</ul>${!catalogue.models?.length?"<p>Для этого мастера ещё нет целевых моделей.</p>":""}${(catalogue.checks||[]).filter(row=>row.status==="completed").map(row=>`<p>Офлайн DEV: ${escape(row.result?.evaluation?.ACT9?.evaluated_samples)} предсказаний · модель ${escape(row.config?.model_job_id)} · полная предметная задача не проверялась.</p><details><summary>Первые12 предсказаний в native9</summary><p>Порядок: base, shoulder, elbow, wrist_pitch, wrist_roll, gripper — градусы; vx, vy — м/с; wz — рад/с. Исходный action9 взят из записанного DEV.</p><ol>${(row.result?.evaluation?.raw_predictions||[]).slice(0,12).map(prediction=>`<li>Кадр ${escape(prediction.sample_index)} · предсказание: ${escape((prediction.ACT9||[]).map(value=>Number(value).toPrecision(3)).join(", "))} · исходный action9: ${escape((prediction.actual_target9||[]).map(value=>Number(value).toPrecision(3)).join(", "))}</li>`).join("")}</ol></details>`).join("")}`:""}${view.targetEvalJob?`<p role="status">DEV-проверка в общей очереди: ${escape(view.targetEvalJob.id)}. Обновите список после завершения.</p>`:""}`;
  }
  function transferGeometryPanel(view){
    const choice=view.choice,geometry=view.geometry,wizard=view.wizard,busy=view.busy;
    const reasons=values=>Array.isArray(values)?values:[];
    const status=value=>({SIM_AUTO_GEOMETRY:"SIM: физическая съёмка не требуется",CAPTURE_REQUIRED:"Нужны измеренные виды",READY_TO_FIT:"Можно рассчитать",PASS:"Измерения проверены"}[value]||value);
    const reason=row=>({source_URDF_unit_axis_alignment:"Нужно отдельно проверить соответствие поз follower модели CAD.",source_and_target_metric_gripper_aperture:"Нужно измерить апертуру обоих захватов; сила не определяется по изображению.",current_source_target_object_goal_registration:"Нужно зарегистрировать предмет и цель в обеих сценах.",qualified06_object_skill_source:"Нужна независимая полная предметная попытка исходной модели."}[row.id]||row.reason);
    const button=(action,label,disabled)=>`<button type="button" class="button secondary" data-transfer-geometry="${action}" ${disabled?"disabled":""}>${label}</button>`;
    return `<section class="panel spaced-section transfer-geometry"><div class="panel-heading"><h2>Геометрия и калибровка переноса</h2></div><div class="panel-body"><p>Исходная калибровка follower читается по SHA выбранной модели. CAD leader используется отдельно в опыте03. Энкодерная геометрия доступна до принятия полного предметного навыка; она не подтверждает физическую регистрацию.</p>${view.error?`<p class="form-error" role="alert">${escape(view.error)}</p>`:""}${button("preview","Проверить исходную геометрию",busy||choice?.technical_source_ready!==true)}${view.savedWizardId?button("restore","Восстановить сохранённую калибровку",busy):""}${geometry?`<p role="status">Исходная геометрия проверена · follower · SHA ${escape(geometry.source_model_binding.source_calibration_sha256)}</p>${geometry.source_model_binding.source_domain==="SIMULATION"?"<p class=\"form-hint\">Исходная модель SIM. Целевая среда выбирается отдельно; физические измерения не подтверждены.</p>":""}<h3>Остаются отдельные проверки</h3><ul>${reasons(geometry.audit?.B).map(row=>`<li>${escape(reason(row))}</li>`).join("")}${reasons(geometry.audit?.C).map(row=>`<li>${escape(reason(row))}</li>`).join("")}</ul><label class="form-field">Новая целевая версия этого навыка<input data-transfer-target-version type="number" min="${choice.skill_version+1}" value="${escape(view.targetVersion)}" step="1"></label><label class="form-field">Целевая среда<select data-transfer-target-domain><option value="simulation" ${view.targetDomain==="simulation"?"selected":""}>SIM · виртуальный target ROSMASTER</option><option value="physical" ${view.targetDomain==="physical"?"selected":""}>Оборудование · отдельная физическая регистрация</option></select></label>${button("create","Сохранить подготовку калибровки",busy)}`:""}${wizard?`<p>Подготовка сохранена · источник ${escape(wizard.source_model_binding?.source_domain)} → цель ${wizard.domain==="simulation"?"SIM":"оборудование"} · версия ${escape(wizard.source_version)} → ${escape(wizard.target_version)} · ${wizard.domain==="simulation"?"SIM: физические измерения не подтверждаются":"отдельные измерения двух сцен"}</p><ol>${reasons(wizard.steps).map(step=>`<li>${escape(step.title)}: ${escape(status(step.status))} · принято ${escape(step.accepted_views)}, отклонено ${escape(step.rejected_views)}</li>`).join("")}</ol><p>Источник, его версия и SHA закреплены. Целевые показы, обучение и независимая проверка ещё не завершены.</p>${transferCapturePanel(view)}<h3>Целевые показы и общий ACT9</h3><p>Адаптация переносит только совместимый visual backbone ACT6. Целевые action/state heads новые; normalizer обучается только на target TRAIN. Zero-shot FK/IK проверяется отдельно.</p>${button("datasets","Проверить целевые datasets",busy)}${view.datasets?`<p>Доступно проверенных datasets: ${reasons(view.datasets.datasets).filter(row=>row.ready).length}. Считаются исходные полные показы, не кадры. DEV и sealed не дополняют TRAIN.</p>${reasons(view.datasets.datasets).some(row=>row.ready)?`<label class="form-field">Целевой dataset<select data-transfer-training="targetDataset">${reasons(view.datasets.datasets).filter(row=>row.ready).map(row=>`<option value="${escape(row.dataset_job_id)}" ${view.targetDataset===row.dataset_job_id?"selected":""}>${escape(row.dataset_job_id)} · TRAIN ${row.train_original_demonstrations} · DEV ${row.development_original_demonstrations}</option>`).join("")}</select></label><label class="form-field">Схема обучения<select data-transfer-training="trainingMode"><option value="vision_adapt" ${view.trainingMode==="vision_adapt"?"selected":""}>Адаптация visual backbone</option><option value="target_only" ${view.trainingMode==="target_only"?"selected":""}>Target-only baseline</option></select></label><label class="form-field">Исходных целевых показов<select data-transfer-training="demonstrations">${[5,10,25].map(n=>`<option value="${n}" ${Number(view.demonstrations)===n?"selected":""}>${n}</option>`).join("")}</select></label>${button("train","Поставить ACT9 в общую очередь",busy||!view.datasets.datasets.find(row=>row.dataset_job_id===view.targetDataset)?.budgets?.some(row=>row.demonstrations===Number(view.demonstrations)&&row.ready))}`:"<p>Подходящих целевых данных пока нет. Исходный ACT6 dataset не заменяет target9 показы.</p>"}`:""}${view.targetJob?`<p role="status">Обучение сохранено в общей очереди · ${escape(view.targetJob.id)}. Результаты и состояние доступны в общей очереди; навык ещё не принят.</p>`:""}${transferTargetModelsPanel(view)}`:""}</div></section>`;
  }
  const exported={createTransferGeometryClient,transferGeometryPanel,transferTargetModelsPanel,transferCapturePanel};if(typeof module!=="undefined"&&module.exports)module.exports=exported;else Object.assign(surface,exported);
})(typeof window!=="undefined"?window:globalThis);
