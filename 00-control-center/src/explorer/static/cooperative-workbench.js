"use strict";
// ENGINEERING_GLUE: explicit cooperative workflow; no actuator/lease/authorization transport.
(function (surface) {
  const releaseEvidence=typeof module!=="undefined" && module.exports ? require("./cooperative-release-evidence.js") : surface;
  const rows = value => Array.isArray(value) ? value : [];
  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const key = row => `${row?.spec?.skill_id || ""}:${row?.spec?.version || ""}:${row?.spec?.spec_sha256 || row?.spec_sha256 || ""}`;
  const choiceID = choice => choice?.choice_id || "";
  const labels = {PREPARE:"Подготовка",ROSMASTER_HOLDING:"ROSMASTER удерживает предмет",OFFER:"Предмет в общей зоне",SO101_APPROACH:"SO101 подошёл к предмету",RECEIVER_GRASP_CHECK:"Проверка захвата получателем",RELEASE_AUTHORIZED:"Свежий захват подтверждён",RELEASE:"Отпускание передающим",CONFIRM_TRANSFER:"Проверка передачи",SO101_FINISH:"Завершение получателем",SAFE_STOP:"Безопасная остановка",FAULT:"Ошибка",giver_holding:"Подтвердить удержание передающим",offer_ready:"Общая зона готова",receiver_ready:"Получатель готов",receiver_at_object:"Оба механизма у предмета неподвижны",receiver_closed:"Записать закрытие захвата · это не удержание",receiver_hold_confirmed:"Проверить удержание получателем",release_requested:"Явно запросить отпускание передающим",giver_released:"Отпускание завершено",transfer_confirmed:"Независимо подтвердить передачу",receiver_finished:"Получатель завершил задачу"};
  const modes = {both_manual:"Оба вручную",giver_policy_receiver_manual:"ROSMASTER — модель, SO101 — вручную",giver_manual_receiver_policy:"ROSMASTER — вручную, SO101 — модель",both_policy:"Оба — отдельные модели"};
  const outcomes = {unknown:"Неизвестно",failure:"Неудача",success:"Наблюдался успех"};
  const inputLabels = {keyboard:"Клавиатура",browser_gamepad:"Browser Gamepad",so101_leader:"SO101 leader → ROSMASTER",leader:"SO101 leader → follower",policy:"Проверенная модель роли"};
  const freshFinish = () => ({status:"unknown",giver:"unknown",receiver:"unknown",observed:false,giver_phases:{},giver_visible:false,giver_full_task:false});
  function specRows(data) { return rows(data?.workbench?.skills); }
  function sessions(data) { return rows(data?.workbench?.sessions); }
  function roleChoices(data, role) { return rows(data?.native_choices?.[role]); }
  function legalEvents(data, session) {
    return rows(session?.legal_events || session?.available_events || data?.catalogue?.legal_events?.[session?.protocol_state]);
  }
  function episodeChoices(data, session, role) {
    const choices = data?.native_episode_choices?.[session?.session_id] || session?.native_episode_choices || data?.native_episode_choices;
    return rows(choices?.[role]);
  }
  function readiness(data, session) { return session?.readiness || data?.readiness?.[session?.session_id] || {}; }
  function activeStopped(view, session) { return Boolean(session && (view.stops?.[session.session_id] || ["STOPPED","INTERRUPTED","COMPLETED"].includes(session.status))); }
  function shadowSupported(data, skill) {
    const refs = skill?.spec?.subskills;
    return data?.queued_native_shadow_supported === true && data?.queued_native_shadow_readiness?.ready !== false && skill?.confirmed === true &&
      ["rosmaster_keyboard_learning","rosmaster_leader_learning"].includes(refs?.giver?.experiment_slug) &&
      refs?.receiver?.experiment_slug === "so101_source_learning" &&
      ["giver","receiver"].every(role => Boolean(refs?.[role]?.model_id));
  }
  function warmSupported(data,skill,session) {
    const required=data?.required_policy_roles,refs=skill?.spec?.subskills;
    const expected=["giver","receiver"].filter(role=>session?.[role+"_input_mode"]==="policy");
    return skill?.confirmed===true && Array.isArray(data?.warm_compute_sessions) && Array.isArray(required) &&
      required.length>0 && required.length===expected.length && new Set(required).size===required.length &&
      required.every(role=>expected.includes(role) && Boolean(refs?.[role]?.model_id)) &&
      ["rosmaster_keyboard_learning","rosmaster_leader_learning"].includes(refs?.giver?.experiment_slug) &&
      refs?.receiver?.experiment_slug==="so101_source_learning";
  }
  function selected(view) {
    return {skill:specRows(view.data).find(row => key(row) === view.selectedSkill),session:sessions(view.data).find(row => row.session_id === view.selectedSession)};
  }
  function createCooperativeWorkbenchClient(options) {
    if (typeof options?.get !== "function" || typeof options?.post !== "function") throw new Error("Нужен доверенный HTTP-клиент");
    let data = null, error = "", busy = false, busyAction = "", generation = 0, readSequence = 0, selectedSkill = "", selectedSession = "", lastResult = null, trainingPlanReceipt = null, aggregateReceipt = null;
    const stops = {}, draft = {name:"Передать предмет ROSMASTER → SO101 и завершить задачу",object_description:"",receiver_goal_description:"",outcome_criteria:"",mode:"both_manual",giver_choice_id:"",receiver_choice_id:"",giver_model_id:"",receiver_model_id:"",phase_maps:{giver:{},receiver:{}},scene:"",object_instance:"",split:"train",giver_input_mode:"keyboard",receiver_input_mode:"leader",execution_domain:"observation_only",stationary_chassis_confirmed:false,native_variants:{giver:"",receiver:""},zone_checked:false,zone_observing:false,record_phase:"",finish:freshFinish(),review:{status:"unknown",whole_episode_seen:false,phase_outcomes:{},native_episode_choice_ids:{giver:"",receiver:""}},evidence:{}};
    const storage = options.storage ?? (typeof sessionStorage !== "undefined" ? sessionStorage : null);
    const storageKey = options.storageKey || "explorer.cooperative-workbench.v1";
    let reviewRecordingID = "", finishRecordingID = "", pendingShadow = null, pendingWarm = null;
    function requestUUID() {
      if (options.uuid) return options.uuid();
      if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
      if (globalThis.crypto?.getRandomValues) {
        const bytes=globalThis.crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
        const hex=Array.from(bytes,value=>value.toString(16).padStart(2,"0")).join("");
        return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
      }
      throw new Error("Браузер не предоставил идентификатор запроса; обновите защищённую страницу");
    }
    function clearObservationChecks() {draft.zone_checked = false;draft.zone_observing = false;draft.finish.observed = false;draft.finish.giver_visible = false;draft.finish.giver_full_task = false;draft.review.whole_episode_seen = false;draft.evidence = {};}
    try {
      const raw = storage?.getItem(storageKey);
      if (raw && raw.length < 131072) {
        const saved = JSON.parse(raw);
        if (saved.schema === 1) {
          for (const field of Object.keys(draft)) {
            const value = saved.draft?.[field], original = draft[field];
            if (typeof original !== typeof value || value == null || Array.isArray(value)) continue;
            draft[field] = typeof original === "object" ? {...original,...copy(value)} : value;
          }
          for (const [parent,field] of [[draft.phase_maps,"giver"],[draft.phase_maps,"receiver"],[draft.review,"phase_outcomes"],[draft.review,"native_episode_choice_ids"],[draft.finish,"giver_phases"]]) if (!parent[field] || typeof parent[field] !== "object" || Array.isArray(parent[field])) parent[field] = {};
          for (const [id,stop] of Object.entries(saved.stops || {})) if (id !== "__proto__" && stop && typeof stop.originalEpoch === "string") stops[id] = copy(stop);
          selectedSkill = typeof saved.selectedSkill === "string" ? saved.selectedSkill : "";selectedSession = typeof saved.selectedSession === "string" ? saved.selectedSession : "";
          reviewRecordingID = saved.reviewRecordingID || "";finishRecordingID = saved.finishRecordingID || "";
          if (typeof saved.pendingShadow?.pin === "string" && /^[0-9a-f-]{36}$/.test(saved.pendingShadow?.request_id || "")) pendingShadow = copy(saved.pendingShadow);
          if (typeof saved.pendingWarm?.pin === "string" && /^[0-9a-f-]{36}$/.test(saved.pendingWarm?.request_id || "")) pendingWarm = copy(saved.pendingWarm);
          clearObservationChecks(); // A prior current-observation affirmation never survives page recreation.
        }
      }
    } catch (_) { /* Storage is optional; no network or actuator fallback. */ }
    function persist() {
      try {
        const safeDraft = copy(draft);safeDraft.evidence = {};safeDraft.zone_checked = false;safeDraft.zone_observing = false;
        safeDraft.finish.observed = false;safeDraft.finish.giver_visible = false;safeDraft.finish.giver_full_task = false;safeDraft.review.whole_episode_seen = false;
        storage?.setItem(storageKey,JSON.stringify({schema:1,draft:safeDraft,stops,selectedSkill,selectedSession,reviewRecordingID,finishRecordingID,pendingShadow,pendingWarm}));
      } catch (_) { /* Explicit server save remains authoritative. */ }
    }
    const notify = () => {persist();options.onChange?.();};
    function snapshot() { return copy({data,error,busy,busyAction,selectedSkill,selectedSession,draft,stops,lastResult,trainingPlanReceipt,aggregateReceipt}); }
    function current() { return selected(snapshot()); }
    let releasePins={};
    function adopt(value) {
      if (!value || typeof value !== "object") throw new Error("Сервер не вернул состояние кооперации");
      data = copy(value);
      releasePins=releaseEvidence.releaseEvidencePins(rows(data.pending_evidence),sessions(data),releasePins,draft);
      if (!selectedSkill && specRows(data).length) selectedSkill = key(specRows(data).at(-1));
      if (selectedSession && !sessions(data).some(row => row.session_id === selectedSession)) selectedSession = "";
      const currentSession = sessions(data).find(row => row.session_id === selectedSession);
      if (currentSession?.recording_id && currentSession.recording_id !== finishRecordingID) {
        draft.finish = freshFinish();finishRecordingID = currentSession.recording_id;
      }
      if (currentSession?.last_recording_id && currentSession.last_recording_id !== reviewRecordingID) {
        draft.review = {status:"unknown",whole_episode_seen:false,phase_outcomes:{},native_episode_choice_ids:{giver:"",receiver:""}};reviewRecordingID = currentSession.last_recording_id;
      }
      for (const session of sessions(data)) {
        const stop = stops[session.session_id];
        if (stop && ["STOPPED","INTERRUPTED"].includes(session.status) && session.epoch !== stop.originalEpoch) stop.acknowledged = true;
      }
    }
    async function refresh() {
      const sequence = ++readSequence, epoch = generation;
      try { const value = await options.get("/api/cooperative" + (selectedSession ? "?session_id=" + encodeURIComponent(selectedSession) : "")); if (sequence !== readSequence || epoch !== generation) return; adopt(value); error = ""; }
      catch (failure) { if (sequence === readSequence && epoch === generation) error = failure.message; }
      notify(); return snapshot();
    }
    async function operation(name, body, isStop = false) {
      if (busy && !isStop) throw new Error("Дождитесь текущего запроса; STOP доступен всегда");
      const skillPin = current().skill ? key(current().skill) : null;
      const epoch = ++generation; readSequence++; busy = true; busyAction = name; error = ""; notify();
      try {
        const reply = await options.post(`/api/cooperative/${name}`,copy(body));
        if (epoch !== generation) return {ignored:true};
        readSequence++; // An earlier GET, including one started during POST, cannot replace this response.
        if (reply?.snapshot) adopt(reply.snapshot);
        lastResult = {operation:name,result:copy(reply?.result ?? reply)};
        if (name === "training_plan") trainingPlanReceipt = {skillKey:skillPin,result:copy(lastResult.result)};
        if (name === "aggregate") aggregateReceipt = {skillKey:skillPin,result:copy(lastResult.result)};
        if (["review_recording","record_finish"].includes(name)) {trainingPlanReceipt = null;aggregateReceipt = null;}
        if (name === "propose_skill" && reply?.result?.spec) selectedSkill = key(reply.result);
        if (name === "create_session" && reply?.result?.session_id) selectedSession = reply.result.session_id;
        if (!reply?.snapshot) await refresh();
        return reply?.result ?? reply;
      } catch (failure) { if (epoch === generation) error = isStop ? `STOP запрошен, подтверждение сервера не получено: ${failure.message}` : failure.message; throw failure; }
      finally { if (epoch === generation) {busy = false;busyAction = "";notify();} }
    }
    function edit(path, value, render = false) {
      const parts = String(path).split(".");
      if (parts.some(part => ["__proto__","prototype","constructor"].includes(part))) throw new Error("Недопустимое поле формы");
      let parent = draft;
      for (const part of parts.slice(0,-1)) { if (!parent[part] || typeof parent[part] !== "object") parent[part] = {}; parent = parent[part]; }
      const previous = parent[parts.at(-1)];parent[parts.at(-1)] = value;
      if (path === "finish.giver" && previous !== value) {draft.finish.giver_phases = {};draft.finish.giver_full_task = false;}
      if (["giver_choice_id","receiver_choice_id"].includes(path)) { const role = path.split("_")[0];draft.phase_maps[role] = {};draft[role+"_model_id"] = ""; }
      persist();if (render) notify();
    }
    function chooseSkill(value) { if (selectedSkill !== value) {clearObservationChecks();trainingPlanReceipt = null;aggregateReceipt = null;}selectedSkill = value;selectedSession = "";notify(); }
    function chooseSession(value) {
      const session = sessions(data).find(row => row.session_id === value);
      if (value && !session) throw new Error("Выберите серверную сессию");
      if (selectedSession !== value) {clearObservationChecks();draft.finish = freshFinish();draft.review = {status:"unknown",whole_episode_seen:false,phase_outcomes:{},native_episode_choice_ids:{giver:"",receiver:""}};reviewRecordingID = session?.last_recording_id || "";finishRecordingID = session?.recording_id || "";}
      selectedSession = value;
      if (session) { const row = specRows(data).find(row => row.spec.skill_id === session.skill_id && row.spec.version === session.skill_version && (row.spec.spec_sha256 || row.spec_sha256) === session.spec_sha256);if (row) selectedSkill = key(row); }
      notify();
    }
    function skillRequired() { const row = current().skill;if (!row) throw new Error("Выберите точную версию навыка с сервера");return row; }
    function sessionRequired(allowStopped = false) { const row = current().session;if (!row) throw new Error("Выберите текущую сессию");if (!allowStopped && (stops[row.session_id] || ["STOPPED","INTERRUPTED","COMPLETED"].includes(row.status))) throw new Error("После STOP старые действия не возобновляются; создайте новую сессию");return row; }
    function propose() {
      for (const role of ["giver","receiver"]) if (!roleChoices(data,role).some(row => choiceID(row) === draft[role+"_choice_id"])) throw new Error("Выберите обе реальные исходные версии навыков");
      const criteria = draft.outcome_criteria.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
      if (![draft.name,draft.object_description,draft.receiver_goal_description].every(value => typeof value === "string" && value.trim()) || !criteria.length) throw new Error("Заполните задачу, предмет, цель получателя и наблюдаемые критерии");
      const phaseMaps = {};
      for (const role of ["giver","receiver"]) {
        const chosen = roleChoices(data,role).find(row => choiceID(row) === draft[role+"_choice_id"]);
        phaseMaps[role] = Object.fromEntries(Object.entries(draft.phase_maps[role]).filter(([,value]) => value));
        if (Object.entries(phaseMaps[role]).some(([phase,value]) => !rows(data?.catalogue?.phases).includes(phase) || !rows(chosen.phase_ids).includes(value))) throw new Error("Соответствие фаз должно использовать реальные исходные фазы выбранной версии");
        if (draft[role+"_model_id"] && !rows(chosen.models).some(model => (model.model_id || model.id) === draft[role+"_model_id"])) throw new Error("Выберите реальную модель выбранной исходной версии");
      }
      const body = {name:draft.name,object_description:draft.object_description,receiver_goal_description:draft.receiver_goal_description,outcome_criteria:criteria,mode:draft.mode,giver_choice_id:draft.giver_choice_id,receiver_choice_id:draft.receiver_choice_id,giver_phase_map:phaseMaps.giver,receiver_phase_map:phaseMaps.receiver};
      for (const role of ["giver","receiver"]) if (draft[role+"_model_id"]) body[role+"_model_id"] = draft[role+"_model_id"];
      return operation("propose_skill",body);
    }
    function confirm() { const row = skillRequired();return operation("confirm_skill",{skill_id:row.spec.skill_id,version:row.spec.version,spec_sha256:row.spec.spec_sha256 || row.spec_sha256}); }
    function createSession() {
      const row = skillRequired();if (!row.confirmed) throw new Error("Сначала подтвердите предложенную версию");
      if (!draft.scene.trim() || !draft.object_instance.trim() || draft.stationary_chassis_confirmed !== true) throw new Error("Укажите сцену и экземпляр предмета, подтвердите неподвижность шасси");
      if (!["train","development","sealed_test"].includes(draft.split) || !["keyboard","browser_gamepad","so101_leader","policy"].includes(draft.giver_input_mode) || !["leader","policy"].includes(draft.receiver_input_mode)) throw new Error("Выберите фактические поддерживаемые ввод и раздел данных");
      const mode = row.spec.mode, giverPolicy = ["giver_policy_receiver_manual","both_policy"].includes(mode), receiverPolicy = ["giver_manual_receiver_policy","both_policy"].includes(mode);
      if ((draft.giver_input_mode === "policy") !== giverPolicy || (draft.receiver_input_mode === "policy") !== receiverPolicy) throw new Error("Фактические источники ввода должны соответствовать режиму подтверждённого навыка");
      if (!["observation_only","shadow"].includes(draft.execution_domain)) throw new Error("Физический тест не разрешён сервером при создании: сначала создайте сессию наблюдения, затем отдельно подтвердите обоих роботов");
      return operation("create_session",{skill_id:row.spec.skill_id,version:row.spec.version,spec_sha256:row.spec.spec_sha256 || row.spec_sha256,scene_id:draft.scene,object_instance_id:draft.object_instance,split:draft.split,giver_input_mode:draft.giver_input_mode,receiver_input_mode:draft.receiver_input_mode,execution_domain:draft.execution_domain,native_variants:copy(draft.native_variants),stationary_chassis_confirmed:true});
    }
    function registerZone() { const session = sessionRequired();if (!draft.zone_checked || !draft.zone_observing) throw new Error("Подтвердите обе рабочие зоны, наблюдая их сейчас");return operation("register_zone",{session_id:session.session_id,expected_epoch:session.epoch,both_robot_workspaces_checked:true,observing:true}); }
    function recordReconcile(role="receiver") {
      const session = sessionRequired(true);
      if (!["giver","receiver"].includes(role)) throw new Error("Выберите исходный DATA поток");
      const recovery=session.native_capture_recoveries?.[role] || (role==="receiver" ? session.native_capture_recovery : null);
      if (!["PENDING_DATA_RECONCILIATION","PENDING_DATA_FINISH_RECONCILIATION"].includes(recovery?.state) && !recovery?.attachment_required && !recovery?.cleanup_required) throw new Error("Нет известного ожидающего DATA intent для сверки");
      return operation("record_reconcile",{session_id:session.session_id,expected_epoch:session.epoch,capture_attempt_id:recovery.attempt_id,...(role==="giver" ? {role} : {})});
    }
    function recordStart() { const session = sessionRequired();const state = readiness(data,session);if (rows(state.metadata_and_recording_blockers).length || state.raw_two_camera_recording_available === false) throw new Error("Запись заблокирована: "+rows(state.metadata_and_recording_blockers).join("; "));return operation("record_start",{session_id:session.session_id,expected_epoch:session.epoch}); }
    function recordStage() { const session = sessionRequired();if (!session.recording_id || !rows(data?.catalogue?.phases).includes(draft.record_phase)) throw new Error("Выберите фактическую фазу активной записи");return operation("record_stage",{session_id:session.session_id,recording_id:session.recording_id,phase_id:draft.record_phase,expected_epoch:session.epoch}); }
    function recordFinish() {
      const session = sessionRequired(true);if (!session.recording_id) throw new Error("Активной записи нет");
      if ([draft.finish.status,draft.finish.giver,draft.finish.receiver].includes("success") && draft.finish.observed !== true) throw new Error("Успех требует явного независимого наблюдения всего показа");
      const outcome = {status:draft.finish.status,native_roles:{}};
      const reference = current().skill?.spec?.subskills?.giver;
      const source = roleChoices(data,"giver").find(row => row.reference?.skill_id === reference?.skill_id && row.reference?.skill_version === reference?.skill_version && row.reference?.spec_sha256 === reference?.spec_sha256);
      const nativePhases = rows(source?.phase_ids), completed = nativePhases.filter(phase => draft.finish.giver_phases?.[phase] === true);
      if (draft.finish.giver === "success" && (!draft.finish.giver_visible || !draft.finish.giver_full_task || !nativePhases.length || completed.length !== nativePhases.length)) throw new Error("Успех ROSMASTER требует наблюдения предмета/цели и явного разбора всех фаз его исходного навыка");
      for (const role of ["giver","receiver"]) outcome.native_roles[role] = {status:draft.finish.observed === true ? draft.finish[role] : "unknown",operator_reported_status:draft.finish[role],method:draft.finish.observed === true ? "operator_observation" : "unknown",independent_of_action_commands:draft.finish.observed === true};
      outcome.native_roles.giver.review = {completed_stages:completed,object_and_goal_visible:draft.finish.giver_visible === true,full_task_confirmed:draft.finish.giver_full_task === true};
      return operation("record_finish",{session_id:session.session_id,recording_id:session.recording_id,outcome,expected_epoch:session.epoch});
    }
    function step(event) {
      const session = sessionRequired();if (!legalEvents(data,session).includes(event) || session.pending_operation) throw new Error("Шаг недоступен в текущей версии состояния");
      const state = readiness(data,session);if (session.mode !== "both_manual" && rows(state.observed_execution_blockers).length) throw new Error("Шаг модели заблокирован: "+rows(state.observed_execution_blockers).join("; "));
      return operation("step",{session_id:session.session_id,event,expected_epoch:session.epoch});
    }
    function stop() {
      const session = sessionRequired(true);stops[session.session_id] = {originalEpoch:session.epoch,acknowledged:false};
      return operation("stop",{session_id:session.session_id,expected_epoch:session.epoch,reason:"explicit_user_STOP"},true);
    }
    function confirmEvidence(challengeID) {
      const session = sessionRequired();const pending = rows(data?.pending_evidence).find(row => row.challenge_id === challengeID && row.run_id === session.session_id && row.state === "pending");
      const proof = draft.evidence[challengeID];if (!pending || pending.kind==="parked_release_bundle" || proof?.affirmed !== true || proof?.observing !== true) throw new Error("Подтвердите конкретный текущий вопрос, наблюдая предмет и обоих роботов");
      return operation("confirm_evidence",{challenge_id:challengeID,affirmed:true,observing:true,note:proof.note || ""});
    }
    function resumeParkedRelease(identity) {
      const session=sessionRequired();
      const body=releaseEvidence.parkedReleaseBody(session,rows(data?.pending_evidence),identity,draft.evidence[identity]);
      return operation("native_parked_release_resume",body);
    }
    function clearEvidence() {draft.evidence={};notify();}
    function review() {
      const session = sessionRequired(true), recording = session.last_recording_id;
      if (!recording || !draft.review.whole_episode_seen) throw new Error("Просмотрите весь завершённый показ перед независимым разбором");
      const phases = rows(data?.catalogue?.phases), phaseOutcomes = {};
      for (const phase of phases) { const value = draft.review.phase_outcomes[phase];if (!Object.hasOwn(outcomes,value)) throw new Error("Явно отметьте исход каждой фазы; неизвестно допустимо");phaseOutcomes[phase] = value; }
      for (const role of ["giver","receiver"]) if (!episodeChoices(data,session,role).some(row => choiceID(row) === draft.review.native_episode_choice_ids[role])) throw new Error("Нужны обе реальные исходные записи из списка сервера");
      return operation("review_recording",{session_id:session.session_id,recording_id:recording,object_instance_id:session.object_instance_id,whole_episode_seen:true,outcome:draft.review.status,phase_outcomes:phaseOutcomes,native_episode_choice_ids:copy(draft.review.native_episode_choice_ids),expected_epoch:session.epoch});
    }
    function aggregate() { const row = skillRequired();return operation("aggregate",{skill_id:row.spec.skill_id,version:row.spec.version}); }
    function train() {
      const row = skillRequired(), pinned = key(row);
      if (!row.confirmed || trainingPlanReceipt?.skillKey !== pinned || trainingPlanReceipt.result?.ready !== true || aggregateReceipt?.skillKey !== pinned || !/^[a-f0-9]{64}$/.test(aggregateReceipt.result?.aggregate_manifest_sha256 || "")) throw new Error("Сначала соберите допущенный общий агрегат и проверьте реальный native-план этой версии");
      return operation("train",{skill_id:row.spec.skill_id,version:row.spec.version,settings:{}});
    }
    function trainingPlan() { const row = skillRequired();return operation("training_plan",{skill_id:row.spec.skill_id,version:row.spec.version}); }
    function shadow() {
      const session = sessionRequired(), skill = current().skill;
      if (data?.queued_native_shadow_readiness?.ready === false) throw new Error("Live shadow недоступен: "+rows(data.queued_native_shadow_readiness.blockers).join("; "));
      if (!shadowSupported(data,skill)) throw new Error("Для shadow нужны обе закреплённые native-модели и серверная общая очередь inference");
      if (busy) throw new Error("Дождитесь текущего запроса; STOP доступен всегда");
      const pin = `${session.session_id}:${session.epoch}:${key(skill)}`;
      if (pendingShadow?.pin !== pin) {
        const request_id = requestUUID();
        if (!/^[0-9a-f-]{36}$/.test(request_id || "")) throw new Error("Браузер не предоставил идентификатор запроса; обновите защищённую страницу");
        pendingShadow = {pin,request_id};
      }
      const request = pendingShadow;
      return operation("shadow",{session_id:session.session_id,expected_epoch:session.epoch,request_id:request.request_id}).then(reply => {
        if (!reply?.ignored && pendingShadow === request) {pendingShadow = null;persist();}
        return reply;
      });
    }
    function warmModels() {
      const session=sessionRequired(),skill=current().skill;
      if (!warmSupported(data,skill,session)) throw new Error("Для подготовки нужны модели фактических policy-ролей и серверная вычислительная очередь; ручной ввод модели не требует");
      if (busy) throw new Error("Дождитесь текущего запроса; STOP доступен всегда");
      const pin=`${session.session_id}:${session.epoch}:${key(skill)}`;
      if (pendingWarm?.pin !== pin) {
        const request_id=requestUUID();
        if (!/^[0-9a-f-]{36}$/.test(request_id || "")) throw new Error("Нет идентификатора вычислительного запроса");
        pendingWarm={pin,request_id};
      }
      const request=pendingWarm;
      return operation("warm_models",{session_id:session.session_id,expected_epoch:session.epoch,request_id:request.request_id,compute_ttl_s:120}).then(reply=>{
        if (!reply?.ignored && pendingWarm===request) {pendingWarm=null;persist();}
        return reply;
      });
    }
    function observedOperation(name,fields={}) {
      const allowed={observed_refresh:[],observed_challenge:["source_setup_choice_id"],observed_authorize:["challenge_choice_id","displayed_jpeg_sha256","observing","neutral","space_clear","robot_workspace_checked","user_observed_test_confirmed"],
        bind_observed_test:["observed_choice_id"],observed_display:["permission_choice_id"],observed_observe:["observation_choice_id","displayed_jpeg_sha256","observing"],observed_revoke:["permission_choice_id"]};
      if(!Object.hasOwn(allowed,name) || !fields || typeof fields!=="object" || Array.isArray(fields) || Object.keys(fields).some(key=>!allowed[name].includes(key)))throw new Error("Нужна текущая typed операция наблюдения без подмены сессии или epoch");
      const session=sessionRequired();
      return operation(name,{...copy(fields),session_id:session.session_id,expected_epoch:session.epoch},name==="observed_revoke");
    }
    function nativeOperation(name,fields={}) {
      const operations=["native_begin","native_resume","native_recenter","native_attach","native_lease","native_stop","native_reset_safe","native_phase_activate","native_phase_park","native_phase_adopt","native_phase_begin","native_phase_resume"],allowed=["role","native_control_choice_id","observing","user_observed_test_confirmed","neutral",...(name==="native_reset_safe" ? ["object_safe_no_dual_hold"] : [])];
      if ((name!=="native_refresh" && !operations.includes(name)) || !fields || typeof fields!=="object" || Array.isArray(fields) || Object.keys(fields).some(key=>!(name==="native_refresh" ? [] : allowed).includes(key))) throw new Error("Нужна отдельная typed операция управления без подмены сессии или epoch");
      const session=sessionRequired();
      return operation(name,{...copy(fields),session_id:session.session_id,expected_epoch:session.epoch},name==="native_stop");
    }
    function receiverSetupOperation(name,fields={}) {
      const names=["receiver_setup_bind_guard","receiver_setup_capture_empty_open","receiver_setup_capture_empty_closed","receiver_setup_reset_safe","receiver_setup_adopt_hold"],base=["receiver_setup_choice_id","observing","user_observed_test_confirmed"];
      const allowed=name.includes("capture_") ? [...base,"observation_choice_id","displayed_jpeg_sha256","empty_gripper_confirmed"] : name==="receiver_setup_reset_safe" ? [...base,"object_safe_no_dual_hold"] : base;
      if((name!=="receiver_setup_refresh" && !names.includes(name)) || !fields || typeof fields!=="object" || Array.isArray(fields) || Object.keys(fields).some(key=>!(name==="receiver_setup_refresh" ? [] : allowed).includes(key)))throw new Error("Нужен текущий серверный выбор подготовки SO101 без native чисел, адресов или подмены epoch");
      const session=sessionRequired();return operation(name,{...copy(fields),session_id:session.session_id,expected_epoch:session.epoch});
    }
    return {snapshot,refresh,edit,chooseSkill,chooseSession,propose,confirm,createSession,registerZone,recordStart,recordReconcile,recordStage,recordFinish,step,stop,confirmEvidence,resumeParkedRelease,clearEvidence,review,aggregate,trainingPlan,train,shadow,warmModels,observedOperation,nativeOperation,receiverSetupOperation};
  }

  function cooperativeWorkbenchPanel(view = {}) {
    const data = view.data, draft = view.draft || {}, {skill,session} = selected(view), stopped = activeStopped(view,session), busy = Boolean(view.busy), phases = rows(data?.catalogue?.phases), state = readiness(data,session);
    const field = (path,label,value = "",type = "text") => `<label class="form-field">${esc(label)}<input data-coop-field="${esc(path)}" type="${type}" value="${esc(value)}" maxlength="400"></label>`;
    const check = (path,label,value) => `<label class="check-field"><input type="checkbox" data-coop-field="${esc(path)}" ${value ? "checked" : ""}> ${esc(label)}</label>`;
    const select = (path,label,values,value) => `<label class="form-field">${esc(label)}<select data-coop-field="${esc(path)}">${values.map(([id,title]) => `<option value="${esc(id)}" ${id === value ? "selected" : ""}>${esc(title)}</option>`).join("")}</select></label>`;
    const button = (action,title,disabled = false,extra = "") => `<button type="button" class="button ${action === "stop" ? "danger" : "secondary"}" data-coop-action="${esc(action)}" ${disabled ? "disabled" : ""} ${extra}>${esc(title)}</button>`;
    const reasons = values => rows(values).length ? `<ul class="form-error">${rows(values).map(value => `<li>${esc(typeof value === "string" ? value : value.reason || value.code || "Недостаёт подтверждения")}</li>`).join("")}</ul>` : "";
    const receiptNames={bind_guard:"Направление закрытия",capture_empty_open:"Пустой открытый захват",capture_empty_closed:"Пустой закрытый захват",reset_safe:"Безопасный сброс",adopt_hold:"Наблюдение после остановки"};
    const metadataHistory=rows(session?.receiver_metadata_receipts);
    const savedMetadata=metadataHistory.length ? `<details><summary>Сохранённые проверки SO101 · ${metadataHistory.length}</summary><ul>${metadataHistory.map(receipt=>{
      const safe=/^[a-f0-9]{32}$/.test(receipt?.receipt_id || "") && /^[a-f0-9]{64}$/.test(receipt?.sha256 || "") && receipt.url==="/api/cooperative/artifacts/receiver-metadata/"+receipt.receipt_id;
      return safe ? `<li><a href="${esc(receipt.url)}" target="_blank" rel="noopener">${esc(receiptNames[receipt.operation] || "Проверка подготовки")}</a> · SHA ${esc(receipt.sha256.slice(0,12))}</li>` : "<li>Сохранённая запись недоступна</li>";
    }).join("")}</ul><p class="form-hint">Записи доступны после STOP и перезапуска. Они сохраняют исходное подтверждение и его SHA.</p></details>` : "";
    const controlHistory=rows(session?.native_user_control_receipts);
    const savedControls=controlHistory.length ? `<details><summary>Сохранённые действия управления · ${controlHistory.length}</summary><ul>${controlHistory.map(receipt=>{
      const safe=/^[a-f0-9]{32}$/.test(receipt?.receipt_id || "") && /^[a-f0-9]{64}$/.test(receipt?.sha256 || "") && receipt.url==="/api/cooperative/artifacts/native-control/"+receipt.receipt_id;
      return safe ? `<li><a href="${esc(receipt.url)}" target="_blank" rel="noopener">${receipt.role==="giver" ? "ROSMASTER" : "SO101"} · запись ${esc(receipt.ordinal)}</a> · SHA ${esc(receipt.sha256.slice(0,12))}</li>` : "<li>Сохранённая запись недоступна</li>";
    }).join("")}</ul><p class="form-hint">Показаны последние действия. В каждой сохранённой записи есть ссылка на предыдущую запись этой руки. Ответ службы не подтверждает достижение позы или удержание предмета.</p></details>` : "";
    const sourceCards = ["giver","receiver"].map(role => {
      const values = roleChoices(data,role), chosen = values.find(row => choiceID(row) === draft[role+"_choice_id"]);
      const choices = [["","Выберите подтверждённую исходную версию"],...values.map(row => [choiceID(row),`${row.name || row.reference?.skill_id} · версия ${row.reference?.skill_version} · ${row.reference?.experiment_slug} ${row.confirmed ? "" : "· не подтверждено"}`])];
      const models = rows(chosen?.models), maps = draft.phase_maps?.[role] || {};
      return `<fieldset><legend>${role === "giver" ? "Передающий ROSMASTER · неизменные9D" : "Получатель SO101 · отдельные6D"}</legend>${select(role+"_choice_id","Исходный навык и версия",choices,draft[role+"_choice_id"])}${chosen ? `<p class="form-hint">SHA: ${esc(chosen.reference?.spec_sha256)} · ${chosen.confirmed ? "описание подтверждено" : "нужно подтверждение источника"}</p>${reasons(chosen.blockers)}` : ""}${select(role+"_model_id","Модель этой исходной версии",[["","Без выбранной модели"],...models.map(model => [model.model_id || model.id,`${model.name || model.model_id || model.id} · ${model.source_kind === "SIMULATION" ? "SIMULATION · только offline" : model.status || (model.trained_offline ? "обучена offline" : "готовность не подтверждена")}`])],draft[role+"_model_id"])}<details><summary>Явное соответствие фаз исходному навыку</summary>${phases.map(phase => select(`phase_maps.${role}.${phase}`,labels[phase] || phase,[["","Нет соответствующей исходной фазы"],...rows(chosen?.phase_ids).map(id => [id,id])],maps[phase] || "")).join("")}</details></fieldset>`;
    }).join("");
    const creation = `<details ${skill ? "" : "open"}><summary>Описание общего кооперативного навыка</summary><form data-coop-form="propose">${field("name","Название задачи",draft.name)}${field("object_description","Предмет: какой именно и какие ограничения",draft.object_description)}${field("receiver_goal_description","Что SO101 должен сделать после приёма",draft.receiver_goal_description)}<label class="form-field">Наблюдаемые критерии полного успеха · по одному на строку<textarea data-coop-field="outcome_criteria" maxlength="2000">${esc(draft.outcome_criteria)}</textarea></label>${select("mode","Как развиваем кооперацию",Object.entries(modes),draft.mode)}<div class="form-grid">${sourceCards}</div><p class="form-hint">Передающий остаётся ROSMASTER9D, получатель SO1016D. Наборы данных и модели не объединяются в выдуманную общую голову. Источник06 требует своего проверенного адаптера; подмена источником02 не выполняется.</p><button type="submit" class="button primary" ${busy ? "disabled" : ""}>Предложить версию и проверить описание</button></form></details>`;
    const skillSelect = `<label class="form-field">Сохранённый кооперативный навык и версия<select data-coop-select="skill"><option value="">Выберите навык</option>${specRows(data).map(row => `<option value="${esc(key(row))}" ${key(row) === view.selectedSkill ? "selected" : ""}>${esc(row.spec.name)} · версия ${esc(row.spec.version)} · ${row.confirmed ? "подтверждён" : "предложен"}</option>`).join("")}</select></label>`;
    const spec = skill ? `<section><h3>${esc(skill.spec.name)} · версия ${esc(skill.spec.version)}</h3><p>Предмет: ${esc(skill.spec.object_description)}. Итог получателя: ${esc(skill.spec.receiver_goal_description)}.</p><ul>${rows(skill.spec.outcome_criteria).map(item => `<li>${esc(item)}</li>`).join("")}</ul><p class="form-hint">${esc(modes[skill.spec.mode] || skill.spec.mode)} · точный SHA ${esc(skill.spec.spec_sha256 || skill.spec_sha256)}</p>${button("confirm","Описание верно · подтвердить точную версию",busy || skill.confirmed)}</section>` : "";
    const variantSelects = ["giver","receiver"].map(role => {
      const reference = skill?.spec?.subskills?.[role];const choice = roleChoices(data,role).find(row => row.reference?.skill_id === reference?.skill_id && row.reference?.skill_version === reference?.skill_version && row.reference?.spec_sha256 === reference?.spec_sha256);
      return select(`native_variants.${role}`,role === "giver" ? "Реальный вариант показа ROSMASTER" : "Реальный вариант показа SO101",[["","Вариант не выбран"],...rows(choice?.native_variants).map(variant => [variant.variant_id || variant.id,variant.title || variant.variant_id || variant.id])],draft.native_variants?.[role] || "");
    }).join("");
    const newSession = skill ? `<details><summary>Новая отдельная сессия показа</summary><form data-coop-form="session"><div class="form-grid">${field("scene","Сцена и положение камеры · понятное имя",draft.scene)}${field("object_instance","Конкретный экземпляр предмета · понятное имя",draft.object_instance)}${select("split","Раздел данных",[["train","Обучение"],["development","Отдельная проверка"],["sealed_test","Закрытый тест · не обучать"]],draft.split)}${select("giver_input_mode","Фактический ввод ROSMASTER",["keyboard","browser_gamepad","so101_leader","policy"].map(id => [id,inputLabels[id]]),draft.giver_input_mode)}${select("receiver_input_mode","Фактический ввод SO101",["leader","policy"].map(id => [id,inputLabels[id]]),draft.receiver_input_mode)}${select("execution_domain","Область сессии",[["observation_only","Только наблюдение / метаданные"],["shadow","Shadow без отправки команд"]],draft.execution_domain)}${variantSelects}</div>${check("stationary_chassis_confirmed","Шасси неподвижно; движение шасси запрещено",draft.stationary_chassis_confirmed)}<p>Создание сессии не активирует управление. Обе руки требуют отдельного разрешённого теста под наблюдением пользователя.</p><button type="submit" class="button secondary" ${busy || !skill.confirmed ? "disabled" : ""}>Создать сессию без движения</button></form></details>` : "";
    const ownSessions = sessions(data).filter(row => !skill || row.skill_id === skill.spec.skill_id && row.skill_version === skill.spec.version);
    const sessionSelect = `<label class="form-field">Сессия<select data-coop-select="session"><option value="">Выберите сессию</option>${ownSessions.map(row => `<option value="${esc(row.session_id)}" ${row.session_id === view.selectedSession ? "selected" : ""}>${esc(row.scene_id)} · ${esc(row.object_instance_id)} · ${esc(row.split)} · ${esc(row.status)}</option>`).join("")}</select></label>`;
    const cameraEntries = data?.cameras || data?.workbench?.cameras || {};
    const cameras = `<div class="form-grid">${["giver","receiver"].map(role => {
      const camera = cameraEntries[role] || {}, url = camera.preview_url || camera.url;
      const safe = typeof url === "string" && /^\/api\/(?!\/)[^\u0000-\u0020]*$/.test(url);
      return `<figure><figcaption>${role === "giver" ? "Камера сцены ROSMASTER" : "Камера сцены SO101"}</figcaption>${safe ? `<img class="camera-preview" src="${esc(url)}" alt="Реальный текущий кадр ${role === "giver" ? "ROSMASTER" : "SO101"}">` : '<p class="form-error">Источник камеры не предоставлен сервером</p>'}<p class="form-hint">${esc(camera.reason || camera.state || (camera.available ? "Кэш камеры доступен; экспозиция и общий clock не предполагаются" : "Свежесть кадра не подтверждена"))}</p></figure>`;
    }).join("")}</div>`;
    const giverReference = skill?.spec?.subskills?.giver;
    const giverSource = roleChoices(data,"giver").find(row => row.reference?.skill_id === giverReference?.skill_id && row.reference?.skill_version === giverReference?.skill_version && row.reference?.spec_sha256 === giverReference?.spec_sha256);
    const giverFinishReview = `<details><summary>Отдельный исход полного native-навыка ROSMASTER</summary>${rows(giverSource?.phase_ids).map(phase => check(`finish.giver_phases.${phase}`,(draft.finish?.giver === "failure" ? "Наблюдал неудачу исходной фазы: " : "Наблюдал успешное завершение исходной фазы: ")+phase,draft.finish?.giver_phases?.[phase])).join("")}${check("finish.giver_visible","Предмет и цель ROSMASTER действительно наблюдаемы",draft.finish?.giver_visible)}${check("finish.giver_full_task","ROSMASTER полностью завершил именно выбранный исходный навык",draft.finish?.giver_full_task)}<p>Если это неизвестно, сохраните исход неизвестным. Для SO101 отдельный видеоразбор с исходными фазовыми интервалами выполняется в его native workflow04.</p></details>`;
    const captureRecovery = session?.native_capture_recovery;
    const captureRecoveryLabels = {ACKNOWLEDGED:"ID исходной записи подтверждён",DATA_FINISHED:"Исходные данные завершены",PENDING_DATA_RECONCILIATION:"Ожидается сверка ID",PENDING_DATA_FINISH_RECONCILIATION:"Завершение данных ещё не подтверждено",ABORTED_BEFORE_RPC:"Источник не запускался",UNKNOWN_METADATA:"Журнал данных требует проверки"};
    const captureRecoveries = session?.native_capture_recoveries || {receiver:captureRecovery};
    const captureRecoveryPanel = Object.entries(captureRecoveries).map(([role,captureRecovery]) => captureRecovery ? `<p class="form-hint">Исходная запись ${role === "giver" ? "ROSMASTER" : "SO101"}: ${esc(captureRecoveryLabels[captureRecovery.state] || "Неизвестное состояние данных")}. Сверка DATA не создаёт разрешение управления и не возобновляет движение.${stopped ? " Незавершённая старая source-запись завершится UNKNOWN; уже sealed исход не переписывается." : ""}</p>${captureRecovery.error ? `<p class="form-error">${esc(captureRecovery.error)}</p>` : ""}${(["PENDING_DATA_RECONCILIATION","PENDING_DATA_FINISH_RECONCILIATION"].includes(captureRecovery.state) || captureRecovery.attachment_required || captureRecovery.cleanup_required) ? button("record-reconcile",stopped ? "Сверить DATA ID и завершить старую запись UNKNOWN" : "Сверить известный DATA ID",busy,`data-coop-role="${esc(role)}"`) : ""}` : "").join("");
    const recording = session ? `<section><h3>Общая запись двух исходных потоков</h3>${captureRecoveryPanel}${session.last_interrupted_recording_id ? `<p class="form-hint">Ссылка на прерванную видеозапись сохранена. Исходные файлы не переписывались, capture и управление не возобновлялись.</p>` : ""}<p>${session.recording_id ? "Запись активна" : session.last_recording_id ? "Завершённый показ сохранён" : "Активной записи нет"}. Границы и исходы каждого native-потока сохраняются отдельно.</p>${reasons(Object.entries(session.record_start_errors || {}).map(([role,reason]) => `${role}: ${reason}`))}${button("record-start","Начать только запись",busy || stopped || Boolean(session.recording_id) || rows(state.metadata_and_recording_blockers).length > 0 || Object.values(captureRecoveries).some(row => ["PENDING_DATA_RECONCILIATION","PENDING_DATA_FINISH_RECONCILIATION","UNKNOWN_METADATA"].includes(row?.state)))}${select("record_phase","Фактическая фаза показа",[["","Выберите наблюдаемую фазу"],...phases.map(phase => [phase,labels[phase] || phase])],draft.record_phase)}${button("record-stage","Отметить эту фазу",busy || stopped || !session.recording_id)}<fieldset><legend>Исход при завершении записи · автоматически неизвестен</legend>${select("finish.status","Полный кооперативный показ",Object.entries(outcomes),draft.finish?.status)}${select("finish.giver","Исход ROSMASTER",Object.entries(outcomes),draft.finish?.giver)}${select("finish.receiver","Исход SO101",Object.entries(outcomes),draft.finish?.receiver)}${check("finish.observed","Наблюдал весь показ и исходы независимо от команд / закрытия захвата",draft.finish?.observed)}${giverFinishReview}${button("record-finish","Сохранить и завершить запись",busy || !session.recording_id)}</fieldset></section>` : "";
    const zone = session ? `<section><h3>Общая зона</h3><p>${session.zone_registration_id ? "Проверка общей зоны сохранена" : "Текущая общая зона не подтверждена"}. Ручная проверка не создаёт метрические трансформы для модели.</p>${check("zone_checked","Проверил обе рабочие зоны и неподвижное шасси",draft.zone_checked)}${check("zone_observing","Сейчас наблюдаю предмет и обоих роботов",draft.zone_observing)}${button("register-zone","Зафиксировать текущие кадры общей зоны",busy || stopped)}</section>` : "";
    const protocolStatus = session?.status === "COMPLETED" ? "Протокол завершён. Физический результат требует отдельного подтверждения." : session?.status === "INTERRUPTED" ? "Сессия прервана; прежнее управление не возобновляется." : stopped ? ((view.stops?.[session.session_id]?.acknowledged ?? true) ? "STOP подтверждён сервером; старая сессия не возобновляется" : "STOP запрошен; ожидается подтверждение сервера, действия заблокированы") : session?.status;
    const protocol = session ? `<section><h3>Протокол: ${esc(labels[session.protocol_state] || session.protocol_state)}</h3><p>${esc(protocolStatus)}${session.pending_operation ? " · ожидается текущий ответ / свидетельство" : ""}</p><div class="teleop-actions">${legalEvents(data,session).map(event => button("step",labels[event] || event,busy || stopped || Boolean(session.pending_operation),`data-coop-event="${esc(event)}"`)).join("")}</div><p class="form-hint">Закрытие сервопривода, таймер и текст VLM не доказывают удержание. Отпускание требует свежего независимого подтверждения и native guard. Автоматической авторизации и lease здесь нет.</p></section>` : "";
    const pending = rows(data?.pending_evidence).filter(row => !session || row.run_id === session.session_id).map(challenge => {
      if (challenge.kind==="parked_release_bundle") return releaseEvidence.parkedReleasePanel(challenge,draft.evidence?.[challenge.bundle_id],{check,button,esc,disabled:busy || stopped || !session});
      const proof = draft.evidence?.[challenge.challenge_id] || {};
      return `<fieldset><legend>Текущее независимое свидетельство · ${esc(challenge.role)}</legend><p>${esc(challenge.question || challenge.statement)}</p>${check(`evidence.${challenge.challenge_id}.observing`,"Наблюдаю это сейчас",proof.observing)}${check(`evidence.${challenge.challenge_id}.affirmed`,"Подтверждаю именно указанное наблюдение",proof.affirmed)}${field(`evidence.${challenge.challenge_id}.note`,"Примечание, если нужно",proof.note)}${button("evidence","Подтвердить этот текущий вопрос",busy || stopped,`data-coop-challenge="${esc(challenge.challenge_id)}"`)}${button("stop","Не могу подтвердить · STOP",!session)}<p class="form-hint">Срок и привязку к текущему run/предмету/команде проверяет сервер. Часы браузера не считаются часами датчика.</p></fieldset>`;
    }).join("");
    const sourceReviewLinks = session ? ["giver","receiver"].map(role => {
      const chosen = episodeChoices(data,session,role).find(item => choiceID(item) === draft.review?.native_episode_choice_ids?.[role]);
      const url = chosen?.review_url;
      const safe = typeof url === "string" && /^\/(?:api\/|experiments\/(?:02|03|04|06)(?:[/?]|$))[^\u0000-\u0020]*$/.test(url);
      return safe ? `<p><a class="text-link" href="${esc(url)}" target="_blank" rel="noopener">Открыть исходный показ ${role === "giver" ? "ROSMASTER" : "SO101"}</a></p>` : `<p class="form-hint">${role === "giver" ? "ROSMASTER" : "SO101"}: ссылка на исходный показ не предоставлена сервером; текущая миниатюра не заменяет полный просмотр.</p>`;
    }).join("") : "";
    const review = session?.last_recording_id ? `<details><summary>Независимый разбор завершённого показа</summary>${check("review.whole_episode_seen","Просмотрел весь исходный показ и конкретный предмет",draft.review?.whole_episode_seen)}${select("review.status","Полный исход",Object.entries(outcomes),draft.review?.status)}<div class="form-grid">${phases.map(phase => select(`review.phase_outcomes.${phase}`,labels[phase] || phase,[["","Выберите исход"],...Object.entries(outcomes)],draft.review?.phase_outcomes?.[phase] || "")).join("")}</div>${["giver","receiver"].map(role => select(`review.native_episode_choice_ids.${role}`,role === "giver" ? "Завершённый оригинал ROSMASTER" : "Завершённый оригинал SO101",[["","Нет выбранного допущенного оригинала"],...episodeChoices(data,session,role).map(row => [choiceID(row),row.name || row.title || "Исходный показ · "+(row.status || "допуск проверяется")])],draft.review?.native_episode_choice_ids?.[role] || "")).join("")}<p>Отсутствующий оригинал остаётся недоступным. Ручной ввод ID, подмена intent на dispatch и перенос sealed в train не выполняются.</p>${sourceReviewLinks}${button("review","Сохранить независимый разбор",busy)}</details>` : "";
    const shadowReady = shadowSupported(data,skill);
    const warmReady=warmSupported(data,skill,session);
    const warmStatus=rows(data?.warm_compute_sessions).map(row=>`<section><h3>Подготовка моделей · CPU</h3><p>${esc(row.job_id?.slice(0,8))} · ${esc(row.status)} · ${row.compute_ready === true ? "Требуемые модели загружены в текущем worker" : "Готовность вычислительного worker не подтверждена"}</p>${reasons(row.blockers)}<p class="form-hint">Вычислительная сессия: ${esc(row.compute_ttl_s)}с после загрузки. ${row.paired_runtime_benchmark_qualified === true ? "Измерение пары моделей подтверждено." : "Независимое измерение пары моделей ещё не подтверждено."} Движение и native lease не разрешаются этой подготовкой. Для отдельного наблюдаемого исполнения нужны текущие разрешения пользователя на обоих роботах. <a href="/queue">Состояние общей очереди</a></p></section>`).join("");
    const queuedShadows = rows(data?.queued_native_shadows).map(receipt => `<section><h3>Проверка двух native-моделей в очереди</h3>${["giver","receiver"].map(role => {
      const head = receipt.native_heads?.[role], queued = receipt.jobs?.[role], error = receipt.errors?.[role];
      return `<p>${role === "giver" ? "ROSMASTER9D" : "SO1016D"}: ${esc(head?.status || (queued ? "В очереди" : "Не поставлено"))} ${head?.job_id ? "· "+esc(head.job_id.slice(0,8)) : ""}${head?.verified_fresh_shadow_at_worker_time === true ? " · свежее наблюдение проверено worker" : ""}</p>${reasons([...(head?.schedule?.waiting_reasons || []),...(head?.evidence_error ? [head.evidence_error] : []),...(error ? [error] : [])])}`;
    }).join("")}<p class="form-hint">${receipt.current_session_epoch ? "Текущая версия сессии" : "Историческая версия сессии"}. Это отдельные предсказания без команд; часы роботов не синхронизированы. Постоянный actor и физическое исполнение этим не подтверждаются. <a href="/queue">Открыть общую очередь</a></p></section>`).join("");
    const trainReady = Boolean(skill?.confirmed && view.trainingPlanReceipt?.skillKey === key(skill) && view.trainingPlanReceipt?.result?.ready === true && view.aggregateReceipt?.skillKey === key(skill) && /^[a-f0-9]{64}$/.test(view.aggregateReceipt?.result?.aggregate_manifest_sha256 || ""));
    const trainResult = view.lastResult?.operation === "train" ? `<section><h3>Раздельные задачи в общей очереди</h3>${["giver","receiver"].map(role => {
      const job = view.lastResult.result?.jobs?.[role], error = view.lastResult.result?.errors?.[role];
      return `<p>${role === "giver" ? "ROSMASTER9D" : "SO1016D"}: ${job ? "задача "+esc(String(job.id).slice(0,8))+" · "+esc(job.status) : "задача не подтверждена"}</p>${error ? `<p class="form-error">${esc(error)}</p>` : ""}`;
    }).join("")}<p class="form-hint">Частичный результат сохраняется сервером. Это отдельные native datasets/models; coordinator не объявляется обученной моделью, физический запуск не разрешается.</p></section>` : "";
    const results = view.lastResult ? `<div role="status"><p>Серверный результат: ${esc(view.lastResult.operation)}</p>${reasons(view.lastResult.result?.blockers || view.lastResult.result?.reasons)}${view.lastResult.result?.ready != null ? `<p>${view.lastResult.result.ready ? "План готов в заявленной сервером области" : "Есть незакрытые требования"}</p>` : ""}</div>` : "";
    return `<section class="panel cooperative-workbench" style="min-width:0;overflow-wrap:anywhere"><div class="panel-heading" style="position:sticky;top:0;z-index:2;background:var(--panel,var(--bg))"><div><p class="eyebrow">05 · Совместный навык</p><h2>Передать предмет и завершить действие</h2></div><div class="teleop-actions">${button("refresh","Обновить состояние")}${button("stop","STOP · остановить текущую сессию",!session)}</div></div><div class="panel-body"><p>Общий навык связывает проверенные версии ROSMASTER и SO101. Шасси неподвижно. Данные, модели и clocks двух роботов остаются раздельными. Свободный текст описывает SkillSpec и не добавляет ACT языковой вход.</p>${view.error ? `<p class="form-error" role="alert">${esc(view.error)}</p>` : ""}${!data ? '<p>Состояние ещё не загружено. Обновление читает сервер и не запускает действия.</p>' : ""}${cameras}${skillSelect}${spec}${creation}${newSession}${sessionSelect}${savedMetadata}${savedControls}${session ? `<p class="form-hint">Сцена: ${esc(session.scene_id)} · предмет: ${esc(session.object_instance_id)} · ${esc(session.split)} · ${esc(session.execution_domain)}</p>${reasons(state.metadata_and_recording_blockers)}${reasons(state.observed_execution_blockers)}` : ""}${zone}${recording}${protocol}${pending}${review}${skill ? `<section><h3>Данные → план обучения → shadow</h3>${button("aggregate","Собрать только допущенные пары показов",busy)}${button("training-plan","Проверить план двух native обучений",busy)}${button("train","Поставить две native-задачи обучения в общую очередь",busy || !trainReady)}${!trainReady ? '<p class="form-hint">Для enqueue нужны допущенный парный train/development агрегат и готовый план этой точной версии. Все допуски повторно проверяет сервер.</p>' : ""}${button("shadow","Проверить отдельные модели без команд",busy || stopped || !session || !shadowReady)}${!shadowReady ? '<p class="form-hint">Shadow заблокирован для live-режима: нужны обе закреплённые native-модели и допуск их исходных данных. SIMULATION проверяется только offline.</p>'+reasons(data?.queued_native_shadow_readiness?.blockers) : ""}<p class="form-hint">Агрегат не означает, что обучение запущено; план не означает готовность физического навыка. Shadow использует реальные серверные наблюдения и не отправляет команды. Исполнение и deploy выбираются отдельно после квалификации.</p>${button("warm-models","Подготовить модели выбранного режима · 120с",busy || stopped || !session || !warmReady)}${results}${trainResult}${queuedShadows}${warmStatus}</section>` : ""}</div></section>`;
  }

  function bindCooperativeWorkbench(root,client,onError = () => {}) {
    const failure = action => {try {Promise.resolve(action()).catch(onError);} catch (error) {onError(error);} };
    const input = event => {const field = event.target?.dataset?.coopField;if (field) client.edit(field,event.target.type === "checkbox" ? event.target.checked : event.target.value);};
    const change = event => {
      const target = event.target;
      if (target?.dataset?.coopSelect === "skill") failure(() => client.chooseSkill(target.value));
      else if (target?.dataset?.coopSelect === "session") failure(async () => {client.chooseSession(target.value);await client.refresh();});
      else if (target?.dataset?.coopField) client.edit(target.dataset.coopField,target.type === "checkbox" ? target.checked : target.value,true);
    };
    const submit = event => {
      const form = event.target?.closest?.("[data-coop-form]");if (!form || !root.contains(form)) return;
      event.preventDefault();failure(() => form.dataset.coopForm === "propose" ? client.propose() : client.createSession());
    };
    const click = event => {
      const button = event.target?.closest?.("[data-coop-action]");if (!button || !root.contains(button) || button.disabled) return;
      const actions = {refresh:() => client.refresh(),confirm:() => client.confirm(),"register-zone":() => client.registerZone(),"record-start":() => client.recordStart(),"record-reconcile":() => client.recordReconcile(button.dataset.coopRole || "receiver"),"record-stage":() => client.recordStage(),"record-finish":() => client.recordFinish(),step:() => client.step(button.dataset.coopEvent),stop:() => client.stop(),evidence:() => client.confirmEvidence(button.dataset.coopChallenge),review:() => client.review(),aggregate:() => client.aggregate(),"training-plan":() => client.trainingPlan(),train:() => client.train(),shadow:() => client.shadow(),"warm-models":() => client.warmModels()};
      if (button.dataset.coopAction==="release-resume") failure(()=>client.resumeParkedRelease(button.dataset.coopBundle));
      else if (actions[button.dataset.coopAction]) failure(actions[button.dataset.coopAction]);
    };
    const ownerDocument=root.ownerDocument || globalThis.document,ownerWindow=ownerDocument?.defaultView || globalThis.window;
    const blur=()=>client.clearEvidence(),visibility=()=>{if (ownerDocument?.visibilityState==="hidden") client.clearEvidence();};
    for (const [type,handler] of [["input",input],["change",change],["submit",submit],["click",click]]) root.addEventListener(type,handler);
    ownerDocument?.addEventListener?.("visibilitychange",visibility);ownerWindow?.addEventListener?.("blur",blur);
    return () => {for (const [type,handler] of [["input",input],["change",change],["submit",submit],["click",click]]) root.removeEventListener(type,handler);ownerDocument?.removeEventListener?.("visibilitychange",visibility);ownerWindow?.removeEventListener?.("blur",blur);};
  }
  const exported = {createCooperativeWorkbenchClient,cooperativeWorkbenchPanel,bindCooperativeWorkbench};
  Object.assign(surface,exported);if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
