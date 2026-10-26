"use strict";
// ENGINEERING_GLUE: explicit current USER-observed source04 evaluation; no automatic permission.
(function (surface) {
  const BASE = "/api/lab/source-evaluations", ID = /^[0-9a-f]{32}$/, SHA = /^[0-9a-f]{64}$/;
  const rows = value => Array.isArray(value) ? value : [];
  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const terminal = status => ["completed","failed","cancelled"].includes(status);
  const validID = value => typeof value === "string" && ID.test(value);
  const defaults = () => ({model_job_id:"",duration_s:"10",review_timeout_s:"120",object_instance_id:"",scene_id:"",goal_instance_id:""});
  const affirmations = () => ({observing:false,robot_workspace_checked:false,user_observed_test_confirmed:false});


  function createSourceEvaluationClient(options) {
    if (typeof options?.get !== "function" || typeof options?.post !== "function") throw new Error("Нужен доверенный HTTP-клиент source04");
    let data = null, selected = "", draft = defaults(), flags = affirmations(), error = "";
    let generation = 0, readSequence = 0, frameSequence = 0, frame = null, challenge = null, grant = null;
    let busy = "", visible = options.visible !== false, watching = false, watchBusy = false, authorizing = false, prepareUncertain = false;
    let prepareBaseline = [];
    let shadowBusy = false, shadowUncertain = false, shadowReceipt = null, shadowError = "", shadowSequence = 0, shadowReadSequence = 0;
    const cancelled = new Set(), uncertain = new Set(), outcomeDrafts = {}, outcomesBusy = new Set();
    const now = options.now || (()=>surface.performance?.now?.() ?? Date.now());
    const maxFrameAgeMs = options.maxFrameAgeMs ?? 1500;
    if (!Number.isFinite(maxFrameAgeMs) || maxFrameAgeMs <= 0 || maxFrameAgeMs > 1500) throw new Error("Нужна ограниченная свежесть отображения кадра");
    const notify = ()=>options.onChange?.();
    const storage = options.storage;
    try {
      const saved = JSON.parse(storage?.getItem("explorer.source04.evaluation-draft.v1") || "null");
      if (saved?.schema === 1) {
        for (const key of Object.keys(draft)) if (typeof saved.draft?.[key] === "string") draft[key] = saved.draft[key];
        prepareUncertain = saved.prepareUncertain === true;
        prepareBaseline = rows(saved.prepareBaseline).filter(validID).slice(0,32);
        shadowUncertain = saved.shadowUncertain === true;
        if (validID(saved.shadowReceipt?.id) && validID(saved.shadowReceipt?.model_job_id) && typeof saved.shadowReceipt?.status === "string") shadowReceipt = {id:saved.shadowReceipt.id,model_job_id:saved.shadowReceipt.model_job_id,status:saved.shadowReceipt.status,backend:typeof saved.shadowReceipt.backend === "string" ? saved.shadowReceipt.backend : null};
        for (const id of rows(saved.cancelled)) if (validID(id)) cancelled.add(id);
        for (const id of rows(saved.uncertain)) if (validID(id)) uncertain.add(id);
      }
    } catch (_) { /* Text draft storage never grants authority. */ }
    function save() {try {storage?.setItem("explorer.source04.evaluation-draft.v1",JSON.stringify({schema:1,draft,prepareUncertain,prepareBaseline,shadowUncertain,shadowReceipt,cancelled:[...cancelled],uncertain:[...uncertain]}));} catch (_) { /* A denied optional store cannot authorize. */ }}
    const current = ()=>rows(data?.attempts).find(row=>row.id === selected);
    function attempt(active = false) {
      const row = current();
      if (!row || !validID(row.id)) throw new Error("Выберите настоящую попытку из списка сервера");
      if (active && (row.status !== "running" || cancelled.has(row.id))) throw new Error("Попытка остановлена или ещё не выполняется");
      return row;
    }
    function releaseFrame(value = frame) {if (value?.url) (options.revokeObjectURL || surface.URL?.revokeObjectURL)?.(value.url);}
    function fence() {
      generation++;readSequence++;frameSequence++;watching = false;watchBusy = false;
      releaseFrame();frame = null;challenge = null;grant = null;flags = affirmations();authorizing = false;busy = "";
    }
    function canResolvePreparation() {const row=current();return Boolean(prepareUncertain && row && !prepareBaseline.includes(row.id) && row.model_job_id === draft.model_job_id && ["object_instance_id","scene_id","goal_instance_id"].every(key=>row.context?.[key] === draft[key]));}
    function acknowledgePreparation() {if (!canResolvePreparation()) throw new Error("Выберите найденную новую попытку с теми же предметом, сценой, целью и моделью");prepareUncertain=false;save();notify();}
    function snapshot() {return copy({data,selected,draft,flags,error,busy,visible,watching,watchBusy,authorizing,prepareUncertain,canResolvePreparation:canResolvePreparation(),
      shadowBusy,shadowUncertain,shadowReceipt,shadowError,
      challenge,grant,frame:frame ? {...frame,loadedAt:undefined} : null,cancelled:[...cancelled],uncertain:[...uncertain],outcomeDrafts,outcomesBusy:[...outcomesBusy]});}
    async function refresh() {
      const sequence = ++readSequence, epoch = generation;
      try {
        const result = await options.get(BASE);
        if (sequence !== readSequence || epoch !== generation) return snapshot();
        data = copy(result);error = "";
        const row = current();
        if (selected && (!row || terminal(row.status) || row.status === "cancel_requested")) {
          watching = false;grant = null;challenge = null;flags = affirmations();
        }
      } catch (failure) {if (sequence === readSequence && epoch === generation) error = failure.message;}
      notify();return snapshot();
    }
    function selectAttempt(id) {
      if (!rows(data?.attempts).some(row=>row.id === id) || !validID(id)) throw new Error("Выберите попытку сервера");
      fence();selected = id;error = "";notify();
    }
    function edit(key,value) {
      if (!Object.hasOwn(draft,key) || typeof value !== "string") throw new Error("Измените разрешённое поле задачи");
      const changedModel = key === "model_job_id" && value !== draft[key];
      if (changedModel) fence();
      draft[key] = value;save();
      if (changedModel) notify();
    }
    function affirm(key,value) {
      if (!Object.hasOwn(flags,key) || typeof value !== "boolean") throw new Error("Нужно явное подтверждение пользователя");
      flags[key] = value;notify();
    }
    function outcomeEdit(id,key,value) {
      if (!rows(current()?.pending_outcomes).some(row=>row.challenge_id === id) || !["observing","note"].includes(key)) throw new Error("Выберите текущий запрос исхода");
      if ((key === "observing" && typeof value !== "boolean") || (key === "note" && typeof value !== "string")) throw new Error("Проверьте поле исхода");
      outcomeDrafts[id] = {...(outcomeDrafts[id] || {observing:false,note:""}),[key]:value};
    }
    async function prepare() {
      if (busy || prepareUncertain) throw new Error("Не повторяйте создание после неопределённого ответа: сначала проверьте список попыток");
      if (!rows(data?.models).some(row=>row.id === draft.model_job_id && row.eligible === true)) throw new Error("Выберите допустимую настоящую source04 модель");
      const body = copy(draft);
      for (const [key,maximum] of [["duration_s",60],["review_timeout_s",300]]) {
        const number = Number(body[key]);if (!String(body[key]).trim() || !Number.isFinite(number) || number <= 0 || number > maximum) throw new Error("Длительность должна быть положительной и не превышать "+maximum+" с");body[key] = number;
      }
      for (const key of ["object_instance_id","scene_id","goal_instance_id"]) if (!body[key].trim() || body[key].length > 256) throw new Error("Опишите предмет, сцену и цель без ручных технических ID");
      busy = "prepare";error = "";const epoch = generation;prepareBaseline=rows(data?.attempts).map(row=>row.id);prepareUncertain = true;save();notify();
      try {
        const reply = await options.post(BASE,body);
        if (epoch !== generation) return {ignored:true};
        if (!validID(reply?.id)) throw new Error("Сервер не подтвердил ID новой попытки; проверьте список");
        prepareUncertain = false;save();await refresh();
        if (epoch === generation && rows(data?.attempts).some(row=>row.id === reply.id)) selectAttempt(reply.id);
        return reply;
      } catch (failure) {if (epoch === generation) error = failure.message;throw failure;}
      finally {if (epoch === generation) {busy = "";notify();}}
    }
    async function shadow() {
      if (busy || shadowBusy || shadowUncertain) throw new Error("Дождитесь запроса shadow; после неопределённого ответа сначала проверьте очередь и явно подтвердите новый запрос");
      const chosen = rows(data?.models).find(row=>row.id === draft.model_job_id && row.eligible === true);
      if (!chosen || !validID(chosen.id)) throw new Error("Выберите допустимую настоящую source04 модель для shadow");
      const modelID = chosen.id, epoch = generation, sequence = ++shadowSequence;shadowReadSequence++;
      shadowBusy = true;shadowUncertain = true;shadowError = "";save();notify();
      try {
        const reply = await options.post("/api/lab/source-shadow",{model_job_id:modelID});
        if (epoch !== generation || sequence !== shadowSequence) return {ignored:true};
        if (!validID(reply?.id) || reply.kind !== "so101_skill_shadow" || !["queued","running","pause_requested","paused","resume_requested","cancel_requested","completed","failed","cancelled"].includes(reply.status) ||
            reply.config?.model_job_id !== modelID) throw new Error("Сервер не подтвердил точное задание shadow; проверьте общую очередь");
        shadowReceipt = {id:reply.id,status:reply.status,model_job_id:modelID,backend:reply.config?.backend || chosen.backend || null};
        shadowUncertain = false;save();return reply;
      } catch (failure) {if (epoch === generation && sequence === shadowSequence) shadowError = failure.message;throw failure;}
      finally {if (sequence === shadowSequence) {shadowBusy = false;notify();}}
    }
    function acknowledgeShadow() {
      if (shadowBusy || !shadowUncertain) throw new Error("Сначала дождитесь ответа и проверьте общую очередь shadow");
      shadowUncertain = false;shadowError = "";save();notify();
    }
    async function refreshShadow() {
      if (!validID(shadowReceipt?.id)) throw new Error("Подтверждённого задания shadow ещё нет; проверьте общую очередь");
      const id = shadowReceipt.id, modelID = shadowReceipt.model_job_id, sequence = ++shadowReadSequence, epoch = generation;
      try {
        const reply = await options.get("/api/jobs");
        if (sequence !== shadowReadSequence || epoch !== generation || shadowReceipt?.id !== id) return {ignored:true};
        const found = rows(reply?.jobs).find(row=>row.id === id && row.kind === "so101_skill_shadow" && row.config?.model_job_id === modelID);
        if (!found || typeof found.status !== "string") throw new Error("Задание отсутствует в текущем списке; последний статус не обновлён, проверьте очередь");
        shadowReceipt = {...shadowReceipt,status:found.status};shadowError = "";save();return copy(shadowReceipt);
      } catch (failure) {if (sequence === shadowReadSequence && epoch === generation) shadowError = failure.message;throw failure;}
      finally {notify();}
    }
    async function binary(path) {
      if (options.loadFrame) return options.loadFrame(path);
      const response = await surface.fetch(path,{credentials:"same-origin",cache:"no-store"});
      if (!response.ok) throw new Error("Исходный кадр недоступен: "+response.status);
      const size = Number(response.headers.get("content-length"));
      if (Number.isFinite(size) && size > 8*1024**2) throw new Error("Кадр превышает допустимый размер");
      return {bytes:new Uint8Array(await response.arrayBuffer()),headers:response.headers};
    }
    async function load(path,kind,expected,epoch) {
      const token = ++frameSequence, id = selected, raw = await binary(path);
      const bytes = raw.bytes instanceof Uint8Array ? raw.bytes : new Uint8Array(raw.bytes);
      if (bytes.length < 4 || bytes.length > 8*1024**2 || bytes[0] !== 255 || bytes[1] !== 216 || bytes.at(-2) !== 255 || bytes.at(-1) !== 217) throw new Error("Нужен настоящий ограниченный JPEG");
      const header = name=>raw.headers?.get ? raw.headers.get(name) : raw.headers?.[name.toLowerCase()];
      const checksum = header("x-jpeg-sha256"), frameID = header("x-frame-id"), boot = header("x-boot-id");
      const digest = options.sha256 || surface.explorerSha256Bytes || (typeof require === "function" ? require("./sha256.js").explorerSha256Bytes : null);
      if (typeof digest !== "function") throw new Error("Не загружен общий SHA256 verifier");
      const actual = await digest(bytes,{crypto:Object.hasOwn(options,"crypto") ? options.crypto : surface.crypto});
      if (!SHA.test(checksum || "") || checksum !== actual || !frameID || !boot ||
          checksum !== expected.jpeg_sha256 || frameID !== expected.frame_id || (expected.boot_id && boot !== expected.boot_id)) throw new Error("SHA, камера или boot исходного кадра не совпадают");
      if (epoch !== generation || id !== selected || token !== frameSequence || !visible || cancelled.has(id)) return {ignored:true};
      const url = options.makeObjectURL ? options.makeObjectURL(bytes) : surface.URL.createObjectURL(new Blob([bytes],{type:"image/jpeg"}));
      releaseFrame();frame = {token,kind,url,jpeg_sha256:checksum,frame_id:frameID,boot_id:boot,displayed:false,observed:false,loadedAt:now()};
      notify();
      if (options.displayFrame) {
        const shown = await options.displayFrame(url,{token,kind,jpeg_sha256:checksum,frame_id:frameID,boot_id:boot});
        if (shown === true && epoch === generation && token === frame?.token && visible) await displayed(token);
      }
      return snapshot();
    }
    async function requestChallenge() {
      const row = attempt(true);
      if (busy || uncertain.has(row.id) || row.result?.phase !== "awaiting_source_observed_grant" || !visible) throw new Error("Дождитесь загрузки модели и проверьте состояние попытки; неопределённое разрешение требует STOP");
      const confirmed = copy(flags);fence();flags = confirmed;const epoch = generation;busy = "challenge";notify();
      try {
        const reply = await options.post(`${BASE}/${row.id}/challenge`,{});
        if (epoch !== generation) return {ignored:true};
        if (!validID(reply?.challenge_id) || !reply.control_epoch || !reply.frame?.frame_id || !SHA.test(reply.frame?.jpeg_sha256 || "")) throw new Error("Сервер не предоставил проверяемый запрос наблюдения");
        challenge = copy(reply);
        return await load(`${BASE}/${row.id}/challenge/${reply.challenge_id}/frame`,"challenge",reply.frame,epoch);
      } catch (failure) {if (epoch === generation) {challenge = null;error = failure.message;}throw failure;}
      finally {if (epoch === generation) {busy = "";notify();}}
    }
    function fresh(kind) {return frame?.kind === kind && frame.displayed === true && now()-frame.loadedAt >= 0 && now()-frame.loadedAt <= maxFrameAgeMs;}
    async function authorize() {
      const row = attempt(true);
      if (busy || uncertain.has(row.id) || !visible || !challenge || !fresh("challenge") || !Object.values(flags).every(value=>value === true)) throw new Error("Покажите свежий проверенный кадр и лично подтвердите все три условия");
      const epoch = generation, body = {challenge_id:challenge.challenge_id,displayed_jpeg_sha256:frame.jpeg_sha256,expected_control_epoch:challenge.control_epoch,...copy(flags)};
      busy = "authorize";authorizing = true;uncertain.add(row.id);save();notify();
      try {
        const reply = await options.post(`${BASE}/${row.id}/authorize`,body);
        if (epoch !== generation || !visible || cancelled.has(row.id)) return {ignored:true};
        if (!validID(reply?.grant_id) || reply.execution_permission !== true || reply.control_epoch !== body.expected_control_epoch || reply.displayed_frame?.jpeg_sha256 !== body.displayed_jpeg_sha256) throw new Error("Сервер не подтвердил точное текущее разрешение");
        grant = copy(reply);challenge = null;watching = true;uncertain.delete(row.id);save();flags = affirmations();error = "";
        return reply;
      } catch (failure) {if (epoch === generation) {watching = false;grant = null;error = failure.message;}throw failure;}
      finally {if (epoch === generation) {busy = "";authorizing = false;notify();}}
    }
    async function watchOnce() {
      const row = attempt(true);
      if (!visible || !watching || !grant || watchBusy || cancelled.has(row.id)) return {ignored:true};
      if (frame?.kind === "observation" && !frame.observed) return {waitingForDisplay:true};
      const epoch = generation, grantID = grant.grant_id;watchBusy = true;
      try {
        const permission = await options.get(`${BASE}/${row.id}/permission`);
        if (epoch !== generation || !watching || !visible || grant?.grant_id !== grantID) return {ignored:true};
        if (permission.execution_permission !== true || permission.grant?.grant_id !== grantID || permission.grant?.boot_id !== grant.boot_id ||
            JSON.stringify(permission.grant?.binding) !== JSON.stringify(grant.binding)) throw new Error("Наблюдаемое разрешение истекло или принадлежит другой попытке; нужен STOP и новый запуск");
        grant = copy(permission.grant);
        // The owner rotates the same grant's epoch when its original policy/start returns.
        // Do not prepare a renewal against the preceding idle epoch in that interval.
        if (grant.native_mode === "idle") return {waitingForNativeStart:true};
        if (grant.native_mode !== "policy") throw new Error("Native source ещё не подтвердил режим этой политики; наблюдение не продлено");
        const reply = await options.get(`${BASE}/${row.id}/observation`);
        if (epoch !== generation || !watching || !visible) return {ignored:true};
        const packet = reply.packet?.observation;
        if (!validID(packet?.observation_id) || reply.frame_url !== `${BASE}/${row.id}/observation/${packet.observation_id}/frame` || packet.frame_boot_id !== grant.boot_id) throw new Error("Источник не подтвердил текущий кадр той же попытки и boot");
        return await load(reply.frame_url,"observation",{jpeg_sha256:packet.frame_sha256,frame_id:packet.frame_id,boot_id:packet.frame_boot_id},epoch);
      } catch (failure) {if (epoch === generation) {watching = false;error = failure.message;}throw failure;}
      finally {if (epoch === generation) {watchBusy = false;notify();}}
    }
    async function displayed(token) {
      if (!visible || !frame || frame.token !== token || cancelled.has(selected)) return {ignored:true};
      if (frame.displayed) return {ignored:true};
      frame.displayed = true;
      if (frame.kind !== "observation" || !watching || !grant || frame.observed) {notify();return snapshot();}
      if (!fresh("observation")) {watching = false;error = "Кадр устарел до показа; наблюдение не продлено";notify();return {ignored:true};}
      const epoch = generation, id = selected, receipt = copy(grant);
      frame.observed = true;
      try {
        const reply = await options.post(`${BASE}/${id}/observe`,{grant_id:receipt.grant_id,frame_id:frame.frame_id,displayed_jpeg_sha256:frame.jpeg_sha256,expected_control_epoch:receipt.control_epoch,observing:true});
        if (epoch !== generation || !visible || !watching || cancelled.has(id) || grant?.grant_id !== receipt.grant_id) return {ignored:true};
        if (reply?.grant_id !== receipt.grant_id || reply.control_epoch !== receipt.control_epoch || reply.execution_permission !== true) throw new Error("Сервер не подтвердил наблюдение той же native epoch");
        grant = copy(reply);return reply;
      } catch (failure) {if (epoch === generation) {watching = false;error = failure.message;}throw failure;}
      finally {if (epoch === generation) notify();}
    }
    function setVisible(value) {if (value !== true) {visible = false;fence();}else visible = true;notify();}
    function stopWatching() {watching = false;frameSequence++;notify();}
    function interrupt(reason = "Наблюдение прервано") {fence();error = String(reason);notify();}
    async function cancel() {
      const row = attempt();
      if (terminal(row.status)) throw new Error("Попытка уже завершена");
      cancelled.add(row.id);save();fence();const epoch = generation;error = "";notify();
      try {const reply = await options.post(`/api/jobs/${row.id}/cancel`,{});if (epoch === generation) await refresh();return reply;}
      catch (failure) {if (epoch === generation) error = "Ответ STOP не подтверждён: "+failure.message;throw failure;}
      finally {notify();}
    }
    async function advancePhase(phaseID) {
      const row = attempt(true), index = rows(row.phases).indexOf(row.phase?.phase_id);
      if (!visible || !watching || !grant || !row.phase?.revision || index < 0 || rows(row.phases)[index+1] !== phaseID) throw new Error("Выберите следующий настоящий этап текущей наблюдаемой попытки");
      const epoch = generation, body = {phase_id:phaseID,expected_revision:row.phase.revision,expected_control_epoch:grant.control_epoch};
      const reply = await options.post(`${BASE}/${row.id}/phase`,body);
      if (epoch !== generation || cancelled.has(row.id)) return {ignored:true};
      readSequence++;row.phase = copy(reply);await refresh();return reply;
    }
    async function outcome(challengeID,status) {
      const row = attempt(true), entry = rows(row.pending_outcomes).find(item=>item.challenge_id === challengeID), form = outcomeDrafts[challengeID] || {observing:false,note:""};
      if (!entry || !validID(challengeID) || !["success","failure","unknown"].includes(status) || outcomesBusy.has(challengeID) || row.result?.phase !== "source_outcome_review_after_native_stop") throw new Error("Выберите текущий независимый исход после остановки руки");
      if (status !== "unknown" && (!visible || form.observing !== true)) throw new Error("Лично просмотрите свидетельства этой фазы; неизвестный исход остаётся отдельным");
      const epoch = generation;outcomesBusy.add(challengeID);notify();
      try {const reply = await options.post(`${BASE}/${row.id}/outcomes/${challengeID}`,{status,observing:form.observing === true,note:form.note || ""});if (epoch === generation) {delete outcomeDrafts[challengeID];await refresh();}return reply;}
      finally {outcomesBusy.delete(challengeID);notify();}
    }
    return {snapshot,refresh,selectAttempt,edit,affirm,outcomeEdit,prepare,acknowledgePreparation,shadow,acknowledgeShadow,refreshShadow,requestChallenge,authorize,watchOnce,displayed,setVisible,stopWatching,interrupt,cancel,advancePhase,outcome,destroy:()=>{visible=false;fence();}};
  }

  function sourceEvaluationPanel(view = {}) {
    const data = view.data || {}, row = rows(data.attempts).find(item=>item.id === view.selected), draft = view.draft || defaults(), flags = view.flags || affirmations();
    const stopped = rows(view.cancelled).includes(row?.id), uncertain = rows(view.uncertain).includes(row?.id);
    const modelOptions = rows(data.models).map(model=>`<option value="${esc(model.id)}" ${model.id === draft.model_job_id ? "selected" : ""} ${model.eligible !== true ? "disabled" : ""}>${esc(model.title)} · ${esc(model.backend)}${model.eligible !== true ? " · недоступна" : ""}</option>`).join("");
    const field = (key,label,type="text")=>`<label>${esc(label)}<input type="${type}" data-source-eval-field="${key}" value="${esc(draft[key])}" ${type === "number" ? 'min="0.1" step="0.1"' : 'maxlength="256"'}></label>`;
    const modelReasons = rows(data.models).filter(model=>model.eligible !== true).map(model=>`<li>${esc(model.title)}: ${esc(model.reason || "Источник ещё не квалифицирован")}</li>`).join("");
    const attempts = rows(data.attempts).map(item=>`<option value="${esc(item.id)}" ${item.id === view.selected ? "selected" : ""}>${esc(item.context?.object_instance_id || "Предмет")} → ${esc(item.context?.goal_instance_id || "Цель")} · ${esc(item.status)} · ${esc(item.id.slice(0,8))}</option>`).join("");
    const shadowReady = rows(data.models).some(model=>model.id === draft.model_job_id && model.eligible === true);
    const shadowPanel = `<section data-source-shadow-panel><h3>Read-only shadow выбранной модели</h3><p>Сервер получает реальные cached наблюдения и сохраняет предсказания без отправки команд. Предметный исход проверяется отдельной наблюдаемой попыткой.</p><button type="button" class="button secondary" data-source-eval-action="shadow" ${view.busy || view.shadowBusy || view.shadowUncertain || !shadowReady ? "disabled" : ""}>${view.shadowBusy ? "Создаём read-only shadow…" : "Проверить модель в read-only shadow"}</button><a class="button secondary" href="/queue">Открыть общую очередь</a>${view.shadowError ? `<p class="form-error" role="alert">${esc(view.shadowError)}</p>` : ""}${view.shadowUncertain && !view.shadowBusy ? `<p class="form-error">Ответ создания shadow не подтверждён. Автоматического повтора нет. Сначала проверьте очередь; следующий явный запрос создаст отдельное задание.</p><button type="button" class="button secondary" data-source-eval-action="acknowledgeShadow" ${view.shadowBusy ? "disabled" : ""}>Я проверил(а) очередь · разрешить следующий новый запрос shadow</button>` : ""}${validID(view.shadowReceipt?.id) ? `<p>Последнее подтверждённое задание shadow: ${esc(view.shadowReceipt.id)} · модель ${esc(view.shadowReceipt.model_job_id)} · ${esc(view.shadowReceipt.backend || "backend выбран сервером")} · последний статус ${esc(view.shadowReceipt.status)}</p><button type="button" class="button secondary" data-source-eval-action="refreshShadow">Обновить только статус shadow</button>` : ""}</section>`;
    const display = view.frame ? `<figure><img class="camera-preview" data-source-eval-frame="${esc(view.frame.token)}" src="${esc(view.frame.url)}" alt="Проверенный исходный кадр SO101 текущей попытки"><figcaption>Кадр ${esc(view.frame.frame_id)} · boot ${esc(view.frame.boot_id)} · SHA256 ${esc(view.frame.jpeg_sha256)} · ${view.frame.displayed ? "показ подтверждён браузером" : "ожидается загрузка изображения"}</figcaption></figure>` : '<p>Исходный кадр ещё не показан. Разрешение движения не выдано этой страницей.</p>';
    const check = (key,label)=>`<label class="check-field"><input type="checkbox" data-source-eval-affirm="${key}" ${flags[key] ? "checked" : ""}> ${esc(label)}</label>`;
    const canChallenge = row?.status === "running" && row?.result?.phase === "awaiting_source_observed_grant" && !stopped && !uncertain;
    const index = rows(row?.phases).indexOf(row?.phase?.phase_id), next = index >= 0 ? rows(row?.phases)[index+1] : null;
    const phase = row ? `<p>Текущая фаза: ${esc(row.phase?.phase_id || "ещё не задана")} · независимый исход: ${esc(row.result?.end_to_end?.status || "неизвестен")}</p>${next ? `<button type="button" class="button secondary" data-source-eval-action="phase" data-phase="${esc(next)}" ${!view.watching || stopped ? "disabled" : ""}>Отметить следующий этап: ${esc(next)}</button>` : ""}<p class="form-hint">Метка фазы сохраняет контекст; она не доказывает grasp, удержание или успех предметной задачи.</p>` : "";
    const timeline = rows(row?.timeline).map(step=>{
      const safe = validID(row?.id) && typeof step.frame_url === "string" && new RegExp(`^${BASE}/${row.id}/frames/[0-9a-f]{32}$`).test(step.frame_url);
      return `<li>${esc(step.index)} · ${esc(step.phase_id)} · native command ${esc(step.command_id)}${safe ? `<a href="${esc(step.frame_url)}" target="_blank" rel="noopener">Открыть исходный кадр</a>` : ""}<span class="form-hint"> SHA256 ${esc(step.frame_sha256)}</span></li>`;
    }).join("");
    const outcomes = rows(row?.pending_outcomes).map(entry=>{
      const form = view.outcomeDrafts?.[entry.challenge_id] || {};
      return `<fieldset><legend>${esc(entry.phase_id === "__whole_task__" ? "Полная предметная задача" : entry.phase_id)}</legend><p>${esc(entry.question)}</p><label class="check-field"><input type="checkbox" data-source-eval-outcome="${esc(entry.challenge_id)}" data-outcome-field="observing" ${form.observing ? "checked" : ""}> Я лично просмотрел(а) исходные свидетельства этой фазы после остановки</label><label>Комментарий<input maxlength="1000" data-source-eval-outcome="${esc(entry.challenge_id)}" data-outcome-field="note" value="${esc(form.note || "")}"></label>${[["success","Успех"],["failure","Неуспех"],["unknown","Неизвестно"]].map(([status,label])=>`<button type="button" class="button secondary" data-source-eval-action="outcome" data-challenge="${esc(entry.challenge_id)}" data-status="${status}" ${rows(view.outcomesBusy).includes(entry.challenge_id) ? "disabled" : ""}>${label}</button>`).join("")}</fieldset>`;
    }).join("");
    return `<section class="panel source-evaluation-workbench"><div class="panel-heading"><div><p class="eyebrow">Experiment 04 · исходный SO101</p><h2>Наблюдаемая оценка исходного навыка</h2></div><button type="button" class="button secondary" data-source-eval-action="refresh">Обновить состояние</button></div><div class="panel-body"><p>Проверяется сохранённая исходная политика native6. Обучение и shadow не подтверждают выполнение предметной задачи. Перенос на ROSMASTER в Experiment 06 остаётся отдельным этапом.</p>${view.error ? `<p class="form-error" role="alert">${esc(view.error)}</p>` : ""}<form data-source-eval-form><div class="form-grid"><label>Сохранённая модель<select data-source-eval-field="model_job_id"><option value="">Выберите допустимую модель</option>${modelOptions}</select></label>${field("object_instance_id","Какой конкретный предмет")}${field("scene_id","Где находится сцена")}${field("goal_instance_id","Куда и с каким результатом")}${field("duration_s","Максимальная длительность попытки, с","number")}${field("review_timeout_s","Время на независимые исходы, с","number")}</div><button type="submit" class="button primary" ${view.busy || view.prepareUncertain ? "disabled" : ""}>Загрузить модель и подготовить попытку</button><p class="form-hint">Этот шаг создаёт задание и загружает модель. Разрешение движения запрашивается отдельно после показа точного исходного кадра.</p>${view.prepareUncertain ? '<p class="form-error">Ответ создания не подтверждён. Не создавайте дубль: обновите список и проверьте уже подготовленные попытки.</p>' : ""}</form>${shadowPanel}${modelReasons ? `<details><summary>Почему остальные модели недоступны</summary><ul>${modelReasons}</ul></details>` : ""}<label>Попытка<select data-source-eval-select><option value="">Выберите попытку</option>${attempts}</select></label>${view.prepareUncertain ? `<button type="button" class="button secondary" data-source-eval-action="acknowledge" ${view.canResolvePreparation ? "" : "disabled"}>Я нашёл(ла) созданную попытку с теми же моделью и сценой</button>` : ""}${row ? `<div class="toolbar"><button type="button" class="button danger" data-source-eval-action="cancel" ${terminal(row.status) ? "disabled" : ""}>STOP · отменить эту попытку</button><span>${esc(row.status)} · ${esc(row.result?.phase || "ожидание worker")}</span></div>${stopped ? '<p class="form-error">STOP запрошен. Старая попытка не возобновляется от запоздавшего ответа.</p>' : ""}${uncertain ? '<p class="form-error">Результат разрешения неизвестен. Проверьте состояние и используйте STOP; автоматического повтора нет.</p>' : ""}${display}<p class="form-hint">Сначала лично подтвердите условия ниже. Затем покажите новый кадр и сразу нажмите отдельную кнопку запуска.</p><button type="button" class="button secondary" data-source-eval-action="challenge" ${!canChallenge || view.busy || !view.visible ? "disabled" : ""}>Показать текущий кадр для отдельного разрешения</button>${check("observing","Я сейчас вижу эту руку и свежий исходный кадр")}${check("robot_workspace_checked","Я проверил(а) свободное рабочее пространство руки")}${check("user_observed_test_confirmed","Я отдельно разрешаю одну эту наблюдаемую физическую попытку")}
      <button type="button" class="button primary" data-source-eval-action="authorize" ${!view.challenge || !view.frame?.displayed || !Object.values(flags).every(Boolean) || view.busy || !view.visible || stopped || uncertain ? "disabled" : ""}>Начать одну разрешённую пользователем попытку и наблюдать</button><button type="button" class="button secondary" data-source-eval-action="stopWatching" ${!view.watching ? "disabled" : ""}>Прекратить подтверждение наблюдения</button><p role="status">${view.watching ? "Наблюдение включено вашим явным действием. Подтверждаются только новые кадры, реально показанные на видимой странице." : "Наблюдение выключено. Открытие страницы, обновление и возвращение на вкладку его не включают."}</p>${phase}${outcomes}${timeline ? `<details><summary>Исходные кадры и native receipts · ${rows(row.timeline).length}</summary><ol>${timeline}</ol></details>` : ""}` : '<p>Выберите подготовленную попытку. Если допустимых источников нет, сначала нужен настоящий source04 cohort.</p>'}</div></section>`;
  }

  function bindSourceEvaluation(root,client,onError=()=>{},environment={}) {
    const document = environment.document || surface.document, window = environment.window || surface.window;
    let timer = null, disposed = false;
    const clear = ()=>{if (timer !== null) (environment.clearTimeout || surface.clearTimeout)(timer);timer = null;};
    const report = failure=>{clear();onError(failure);};
    function schedule() {
      clear();if (disposed || !client.snapshot().watching || document?.visibilityState === "hidden") return;
      timer = (environment.setTimeout || surface.setTimeout)(()=>{timer=null;client.watchOnce().then(result=>{if (result?.waitingForDisplay || result?.waitingForNativeStart) schedule();}).catch(report);},200);
    }
    function run(action) {Promise.resolve().then(action).then(schedule).catch(report);}
    const click = event=>{
      const button = event.target.closest?.("[data-source-eval-action]");
      if (!button || !root.contains(button) || button.disabled) return;
      const actions = {refresh:()=>client.refresh(),shadow:()=>client.shadow(),acknowledgeShadow:()=>client.acknowledgeShadow(),refreshShadow:()=>client.refreshShadow(),acknowledge:()=>client.acknowledgePreparation(),challenge:()=>client.requestChallenge(),authorize:()=>client.authorize(),cancel:()=>client.cancel(),stopWatching:()=>client.stopWatching(),phase:()=>client.advancePhase(button.dataset.phase),outcome:()=>client.outcome(button.dataset.challenge,button.dataset.status)};
      if (actions[button.dataset.sourceEvalAction]) run(actions[button.dataset.sourceEvalAction]);
    };
    const change = event=>{
      const element = event.target;if (!root.contains(element) || element.disabled) return;
      try {
        if (element.hasAttribute?.("data-source-eval-select")) client.selectAttempt(element.value);
        else if (element.dataset?.sourceEvalField) client.edit(element.dataset.sourceEvalField,element.value);
        else if (element.dataset?.sourceEvalAffirm) client.affirm(element.dataset.sourceEvalAffirm,element.checked);
        else if (element.dataset?.sourceEvalOutcome) client.outcomeEdit(element.dataset.sourceEvalOutcome,element.dataset.outcomeField,element.dataset.outcomeField === "observing" ? element.checked : element.value);
      } catch (failure) {report(failure);}
    };
    const submit = event=>{if (!event.target.hasAttribute?.("data-source-eval-form") || !root.contains(event.target)) return;event.preventDefault();run(()=>client.prepare());};
    const loaded = event=>{
      const image = event.target;
      if (!root.contains(image) || image.dataset?.sourceEvalFrame == null || !image.complete || !image.naturalWidth || document?.visibilityState === "hidden") return;
      if (image.getClientRects && !image.getClientRects().length) return;
      if (image.getBoundingClientRect) {
        const rect = image.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0 || rect.top >= (window?.innerHeight ?? Infinity) || rect.left >= (window?.innerWidth ?? Infinity)) return;
      }
      const token = Number(image.dataset.sourceEvalFrame);run(()=>client.displayed(token));
    };
    const scrolled = ()=>{for (const image of root.querySelectorAll?.("img[data-source-eval-frame]") || []) loaded({target:image});};
    const lost = ()=>{clear();client.setVisible(false);};
    const visibility = ()=>{if (document?.visibilityState === "hidden") lost();else client.setVisible(true);};
    const focus = ()=>{if (document?.visibilityState !== "hidden") client.setVisible(true);};
    root.addEventListener("click",click);root.addEventListener("change",change);root.addEventListener("input",change);root.addEventListener("submit",submit);root.addEventListener("load",loaded,true);
    document?.addEventListener("visibilitychange",visibility);window?.addEventListener("pagehide",lost);window?.addEventListener("blur",lost);window?.addEventListener("focus",focus);
    window?.addEventListener("scroll",scrolled,{passive:true});
    if (client.snapshot().watching) schedule();
    // Ordinary app rerender only removes this binding; route/logout/STOP calls interrupt separately.
    return ()=>{disposed = true;clear();root.removeEventListener("click",click);root.removeEventListener("change",change);root.removeEventListener("input",change);root.removeEventListener("submit",submit);root.removeEventListener("load",loaded,true);document?.removeEventListener("visibilitychange",visibility);window?.removeEventListener("pagehide",lost);window?.removeEventListener("blur",lost);window?.removeEventListener("focus",focus);window?.removeEventListener("scroll",scrolled);};
  }
  const exported = {createSourceEvaluationClient,sourceEvaluationPanel,bindSourceEvaluation};
  Object.assign(surface,exported);if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof globalThis === "undefined" ? window : globalThis);
