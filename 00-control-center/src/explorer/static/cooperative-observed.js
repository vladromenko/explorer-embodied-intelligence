"use strict";
// ENGINEERING_GLUE: exact displayed USER permissions for one cooperative intent, explicit USER native choices remain separate from permission and watching.
(function (surface) {
  const ROLES = ["giver","receiver"], ID = /^[0-9a-f]{32}$/, SHA = /^[0-9a-f]{64}$/;
  const rows = value=>Array.isArray(value) ? value : [];
  const copy = value=>value == null ? value : JSON.parse(JSON.stringify(value));
  const esc = value=>String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const keys = {giver:["observing","neutral","space_clear","user_observed_test_confirmed"],receiver:["observing","robot_workspace_checked","user_observed_test_confirmed"]};
  const emptyFlags = role=>Object.fromEntries(keys[role].map(key=>[key,false]));

  function createCooperativeObservedClient(options) {
    if (typeof options?.view !== "function" || typeof options?.invoke !== "function") throw new Error("Нужен текущий workbench и его доверенный wrapper операций");
    let context = null, setup = null, generation = 0, visible = options.visible !== false, busy = "", busyToken = 0, error = "", watching = false, watchBusy = false;
    let receiverSetup=null,receiverRefreshedAt=-Infinity,receiverReceipt=null;
    let nativeControls=null,nativeRefreshedAt=-Infinity;const nativeUncertain=new Set(),nativeFlags={giver:{observing:false,user_observed_test_confirmed:false,neutral:false},receiver:{observing:false,user_observed_test_confirmed:false,neutral:false,empty_gripper_confirmed:false,object_safe_no_dual_hold:false}};
    const nativeOperations=["native_begin","native_resume","native_recenter","native_attach","native_lease","native_stop"];
    const flags = {giver:emptyFlags("giver"),receiver:emptyFlags("receiver")}, frames = {}, challenges = {}, receipts = {}, roleRevision = {giver:0,receiver:0}, uncertain = new Set();
    const now = options.now || (()=>surface.performance?.now?.() ?? Date.now()), maxAge = options.maxFrameAgeMs ?? 1500;
    if (!Number.isFinite(maxAge) || maxAge <= 0 || maxAge > 1500) throw new Error("Нужна ограниченная свежесть текущего кадра");
    const notify = ()=>options.onChange?.();
    const roleRequired = role=>{if (!ROLES.includes(role)) throw new Error("Выберите настоящую роль ROSMASTER или SO101");};
    function identity() {
      const view = options.view(), row = rows(view?.data?.workbench?.sessions).find(item=>item.session_id === view.selectedSession), selected = view?.data?.selected_session;
      if (!row || view.stops?.[row.session_id] || ["STOPPED","INTERRUPTED","COMPLETED"].includes(row.status)) return null;
      if (!selected || selected.session_id !== row.session_id || selected.epoch !== row.epoch || !row.epoch || !row.process_session || !SHA.test(row.spec_sha256 || "")) return null;
      return {session_id:row.session_id,epoch:row.epoch,process_session:row.process_session,skill_id:row.skill_id,skill_version:row.skill_version,spec_sha256:row.spec_sha256,
        giver_input_mode:row.giver_input_mode,receiver_input_mode:row.receiver_input_mode};
    }
    const same = (a,b)=>JSON.stringify(a) === JSON.stringify(b);
    function release(role) {if (frames[role]?.url) (options.revokeObjectURL || surface.URL?.revokeObjectURL)?.(frames[role].url);delete frames[role];}
    function clearRole(role) {roleRevision[role]++;release(role);delete challenges[role];delete receipts[role];flags[role]=emptyFlags(role);if (role === "receiver") watching=false;}
    function fence() {generation++;busyToken++;busy="";watching=false;watchBusy=false;for (const role of ROLES) clearRole(role);setup=null;receiverReceipt=null;receiverSetup=null;receiverRefreshedAt=-Infinity;nativeControls=null;nativeRefreshedAt=-Infinity;nativeUncertain.clear();for(const role of ROLES) for(const key of Object.keys(nativeFlags[role]))nativeFlags[role][key]=false;}
    function sync() {
      const next = identity();
      if (!same(context,next)) {fence();context=next;uncertain.clear();error="";}
      if (context) setup=copy(options.view().data.observed_setup || null);
      return snapshot();
    }
    function required() {sync();if (!context) throw new Error("Выберите текущую сессию и обновите её состояние; STOP не возобновляет старую epoch");return copy(context);}
    function current(pin,epoch) {return epoch === generation && same(identity(),pin);}
    function snapshot() {return copy({context,setup,flags,frames:Object.fromEntries(ROLES.filter(role=>frames[role]).map(role=>[role,{...frames[role],loadedAt:undefined}])),challenges,receipts,uncertain:[...uncertain],visible,busy,error,watching,watchBusy,nativeControls,nativeFlags,receiverReceipt,receiverSetup,receiverSetupFresh:now()-receiverRefreshedAt>=0 && now()-receiverRefreshedAt<=1500,nativeUncertain:[...nativeUncertain],nativeFresh:now()-nativeRefreshedAt>=0 && now()-nativeRefreshedAt<=1500});}
    async function call(operation,fields,{bypass=false,native=false,receiver=false}={}) {
      const pin=required(), epoch=generation;
      if (busy && !bypass) throw new Error("Дождитесь текущего запроса; общий STOP остаётся доступен");
      const token=++busyToken;busy=operation;error="";notify();
      try {
        const invoke=receiver ? options.invokeReceiverSetup : native ? options.invokeNative : options.invoke;
        if(typeof invoke!=="function")throw new Error("Управление ещё не подключено к текущему серверу");
        const response=await invoke(operation,copy(fields));
        if (!current(pin,epoch)) return {ignored:true};
        return response?.result ?? response;
      } catch (failure) {if (current(pin,epoch)) error=failure.message;throw failure;}
      finally {if (token === busyToken) {busy="";notify();}}
    }
    async function refresh() {
      const pin=required(),epoch=generation,result=await call("observed_refresh",{});
      if (!current(pin,epoch) || result?.ignored) return {ignored:true};
      if (result?.schema !== "explorer_cooperative_observed_setup_v1") throw new Error("Сервер не вернул текущие раздельные source permissions");
      sync();notify();return result;
    }
    function affirm(role,key,value) {roleRequired(role);required();if (!keys[role].includes(key) || typeof value !== "boolean") throw new Error("Нужен явный выбор текущего оператора");flags[role][key]=value;notify();}
    async function binary(path) {
      if (options.loadFrame) return options.loadFrame(path);
      const response=await surface.fetch(path,{credentials:"same-origin",cache:"no-store"});
      if (!response.ok) throw new Error("Точный исходный кадр недоступен: "+response.status);
      const size=Number(response.headers.get("content-length"));if (Number.isFinite(size) && size > 8*1024**2) throw new Error("Исходный кадр превышает допустимый размер");
      return {bytes:new Uint8Array(await response.arrayBuffer()),headers:response.headers};
    }
    async function load(role,result,kind,pin,epoch,revision) {
      const choice = kind === "challenge" ? result.challenge_choice_id : result.observation_choice_id;
      if (!ID.test(choice || "") || result.frame_url !== `/api/cooperative/observed-frame/${choice}`) throw new Error("Нужен фиксированный серверный путь точного исходного кадра");
      const expected=result.frame || {},raw=await binary(result.frame_url),bytes=raw.bytes instanceof Uint8Array ? raw.bytes : new Uint8Array(raw.bytes);
      if (bytes.length < 4 || bytes.length > 8*1024**2 || bytes[0] !== 255 || bytes[1] !== 216 || bytes.at(-2) !== 255 || bytes.at(-1) !== 217) throw new Error("Нужен ограниченный настоящий JPEG");
      const header=name=>raw.headers?.get ? raw.headers.get(name) : raw.headers?.[name];
      const checksum=header("x-jpeg-sha256"),frameID=header("x-frame-id"),boot=header("x-boot-id");
      const digest=options.sha256 || surface.explorerSha256Bytes || (typeof require === "function" ? require("./sha256.js").explorerSha256Bytes : null);
      if (typeof digest !== "function") throw new Error("Не загружен общий SHA256 verifier");
      const actual=await digest(bytes,{crypto:Object.hasOwn(options,"crypto") ? options.crypto : surface.crypto});
      if (!SHA.test(checksum || "") || checksum !== actual || checksum !== (expected.jpeg_sha256 || expected.frame_sha256) || frameID !== expected.frame_id || !boot || boot !== (expected.boot_id || expected.frame_boot_id)) throw new Error("JPEG SHA, native frame или boot не совпадают с выбранной ролью");
      if (!current(pin,epoch) || revision !== roleRevision[role] || !visible) return {ignored:true};
      const url=options.makeObjectURL ? options.makeObjectURL(bytes) : surface.URL.createObjectURL(new Blob([bytes],{type:"image/jpeg"}));
      release(role);frames[role]={token:revision,kind,url,choice_id:choice,jpeg_sha256:checksum,frame_id:frameID,boot_id:boot,displayed:false,observed:false,loadedAt:now()};notify();
      if (options.displayFrame) {const shown=await options.displayFrame(url,{role,token:revision,kind,jpeg_sha256:checksum,frame_id:frameID,boot_id:boot});if (shown === true && current(pin,epoch) && revision === roleRevision[role] && visible) await displayed(role,revision);}
      return snapshot();
    }
    async function challenge(role) {
      roleRequired(role);const pin=required(),choice=setup?.setup_choices?.[role];
      if (busy) throw new Error("Дождитесь текущего запроса; общий STOP остаётся доступен");
      if (!visible || uncertain.has(role) || !SHA.test(choice?.choice_id || "") || choice.challenge_supported !== true) throw new Error("Обновите текущие источники и устраните их blockers; неопределённое разрешение требует общего STOP");
      const confirmed=copy(flags[role]);clearRole(role);flags[role]=confirmed;const epoch=generation,revision=roleRevision[role];
      const result=await call("observed_challenge",{source_setup_choice_id:choice.choice_id});
      if (!current(pin,epoch) || result?.ignored || revision !== roleRevision[role]) return {ignored:true};
      if (result.role !== role) throw new Error("Native challenge принадлежит другой роли");
      challenges[role]=copy(result);
      try {return await load(role,result,"challenge",pin,epoch,revision);}
      catch (failure) {if (current(pin,epoch)) {delete challenges[role];error=failure.message;notify();}throw failure;}
    }
    function fresh(role,kind) {const frame=frames[role];return frame?.kind === kind && frame.displayed && now()-frame.loadedAt >= 0 && now()-frame.loadedAt <= maxAge;}
    async function authorize(role) {
      roleRequired(role);const pin=required(),epoch=generation,revision=roleRevision[role],entry=challenges[role];
      if (busy) throw new Error("Дождитесь текущего запроса; общий STOP остаётся доступен");
      if (!visible || uncertain.has(role) || !entry || !fresh(role,"challenge") || !Object.values(flags[role]).every(value=>value === true)) throw new Error("Покажите свежий кадр этой роли и лично подтвердите её условия");
      uncertain.add(role);notify();
      try {
        const result=await call("observed_authorize",{challenge_choice_id:entry.challenge_choice_id,displayed_jpeg_sha256:frames[role].jpeg_sha256,...copy(flags[role])});
        if (!current(pin,epoch) || result?.ignored || revision !== roleRevision[role] || !visible) return {ignored:true};
        const native=result.native_permission_receipt;
        if (result.role !== role || native?.execution_permission !== true || !native[role === "giver" ? "authorization_id" : "grant_id"]) throw new Error("Native owner не подтвердил точный допуск этой роли");
        receipts[role]=copy(result);delete challenges[role];flags[role]=emptyFlags(role);uncertain.delete(role);
        if (role === "receiver") watching=true; // This explicit USER button also opts into displayed-frame observation.
        notify();return result;
      } catch (failure) {if (current(pin,epoch)) {if (role === "receiver") watching=false;error=failure.message;notify();}throw failure;}
    }
    async function bind() {
      required();const choice=setup?.bind_choice_id;
      if (!visible || busy || setup?.bind_observed_test_available !== true || !SHA.test(choice || "")) throw new Error("Сначала получите оба текущих отдельных допуска и обновите их состояние");
      return call("bind_observed_test",{observed_choice_id:choice});
    }
    async function displayObservation() {
      const pin=required(),choice=setup?.permission_choices?.receiver,epoch=generation;
      if (!visible || !SHA.test(choice?.permission_choice_id || "") || choice.display_observation_supported !== true) throw new Error("Нужен текущий source-bound допуск SO101 этой сессии");
      const revision=++roleRevision.receiver;release("receiver");
      const result=await call("observed_display",{permission_choice_id:choice.permission_choice_id});
      if (!current(pin,epoch) || result?.ignored || revision !== roleRevision.receiver) return {ignored:true};
      if (result.role !== "receiver") throw new Error("Точный кадр наблюдения должен принадлежать SO101");
      return load("receiver",result,"observation",pin,epoch,revision);
    }
    async function observeDisplayed() {
      const pin=required(),epoch=generation,frame=frames.receiver;
      if (busy) return {waiting:true};
      if (!visible || !watching || !fresh("receiver","observation") || frame.observed || uncertain.has("receiver")) throw new Error("Нужно явное наблюдение и новый реально показанный кадр SO101");
      frame.observed=true;
      try {
        const result=await call("observed_observe",{observation_choice_id:frame.choice_id,displayed_jpeg_sha256:frame.jpeg_sha256,observing:true});
        if (!current(pin,epoch) || result?.ignored || !watching || !visible) return {ignored:true};
        if (result.role !== "receiver" || result.native_permission_receipt?.execution_permission !== true) throw new Error("Источник не подтвердил наблюдение этой роли");
        receipts.receiver=copy(result);return result;
      } catch (failure) {if (current(pin,epoch)) {watching=false;error=failure.message;}throw failure;}
      finally {if (current(pin,epoch)) notify();}
    }
    async function displayed(role,token) {
      roleRequired(role);sync();const frame=frames[role];
      if (!visible || !frame || frame.token !== token || frame.displayed) return {ignored:true};
      frame.displayed=true;
      if (role === "receiver" && frame.kind === "observation" && watching) return observeDisplayed();
      notify();return snapshot();
    }
    function startWatching() {required();if (!visible || uncertain.has("receiver") || !SHA.test(setup?.permission_choices?.receiver?.permission_choice_id || "")) throw new Error("Сначала нужен текущий допуск и ваше явное наблюдение SO101");watching=true;notify();}
    function stopWatching() {watching=false;notify();}
    async function watchOnce() {
      required();if (!visible || !watching) return {ignored:true};if (busy || watchBusy) return {waiting:true};
      if (frames.receiver?.kind === "observation" && !frames.receiver.observed) {
        if (frames.receiver.displayed) return observeDisplayed();
        return {waiting:true};
      }
      const pin=copy(context),epoch=generation;watchBusy=true;
      try {await refresh();if (!current(pin,epoch) || !visible || !watching) return {ignored:true};return await displayObservation();}
      catch (failure) {if (current(pin,epoch)) {watching=false;error=failure.message;notify();}throw failure;}
      finally {if (current(pin,epoch)) {watchBusy=false;notify();}}
    }
    async function revoke(role) {
      roleRequired(role);const pin=required(),epoch=generation,choice=setup?.permission_choices?.[role]?.permission_choice_id;
      if (!SHA.test(choice || "")) throw new Error("Обновите точный текущий допуск; общий STOP остаётся доступен");
      clearRole(role);uncertain.add(role);notify();
      const result=await call("observed_revoke",{permission_choice_id:choice},{bypass:true});
      if (current(pin,epoch) && result?.revoked === true) {uncertain.delete(role);notify();}
      // Revocation does not assert physical STOP or let an old response grant permission.
      return result;
    }
    async function receiverRefresh() {
      const pin=required(),epoch=generation,result=await call("receiver_setup_refresh",{},{receiver:true});
      if(!current(pin,epoch) || result?.ignored || !visible)return {ignored:true};
      if(result?.schema!=="explorer_cooperative_receiver_setup_v1")throw new Error("Сервер не вернул подготовку текущего SO101");
      receiverSetup=copy(result);receiverRefreshedAt=now();notify();return result;
    }
    async function receiverCommand(operation,choiceID) {
      const pin=required(),epoch=generation,confirmed=copy(nativeFlags.receiver),ops=["bind_guard","capture_empty_open","capture_empty_closed","reset_safe","adopt_hold"];
      const state=receiverSetup?.operations?.[operation],choice=rows(state?.choices).find(item=>item.choice_id===choiceID);
      if(!ops.includes(operation) || !visible || busy || !snapshot().receiverSetupFresh || state?.implemented!==true || state?.available!==true || !choice || !SHA.test(choiceID || ""))throw new Error("Обновите подготовку SO101 и выберите текущее доступное действие");
      if(!confirmed.observing || !confirmed.user_observed_test_confirmed || (operation.startsWith("capture_") && !confirmed.empty_gripper_confirmed) || (operation==="reset_safe" && !confirmed.object_safe_no_dual_hold))throw new Error("Лично подтвердите текущий тест и состояние предмета/пустого захвата");
      const fields={receiver_setup_choice_id:choiceID,observing:true,user_observed_test_confirmed:true};
      if(operation.startsWith("capture_")) {
        if(!fresh("receiver","observation") || !ID.test(frames.receiver.choice_id || ""))throw new Error("Сначала покажите свежий точный кадр SO101; одного загруженного JPEG недостаточно");
        Object.assign(fields,{observation_choice_id:frames.receiver.choice_id,displayed_jpeg_sha256:frames.receiver.jpeg_sha256,empty_gripper_confirmed:true});
      }
      if(operation==="reset_safe")fields.object_safe_no_dual_hold=true;
      for(const key of Object.keys(nativeFlags.receiver))nativeFlags.receiver[key]=false;
      receiverRefreshedAt=-Infinity;receiverSetup=null;notify();
      const result=await call("receiver_setup_"+operation,fields,{receiver:true});
      if(!current(pin,epoch) || result?.ignored || !visible)return {ignored:true};
      if(result?.operation!==operation || result?.native_command_sent!==false || result?.physical_execution_authorized!==false)throw new Error("Не подтверждён metadata-only ответ подготовки SO101");
      if(result.metadata_receipt) {
        const receipt=result.metadata_receipt;
        if(!ID.test(receipt.receipt_id || "") || !SHA.test(receipt.sha256 || "") || receipt.url!=="/api/cooperative/artifacts/receiver-metadata/"+receipt.receipt_id)throw new Error("Сохранённая проверка не имеет точного серверного адреса и SHA");
        receiverReceipt=copy(receipt);
      }
      notify();return result;
    }
    function nativeAffirm(role,key,value) {roleRequired(role);required();if(!Object.hasOwn(nativeFlags[role],key) || typeof value!=="boolean")throw new Error("Нужно отдельное явное подтверждение управления");nativeFlags[role][key]=value;notify();}
    async function nativeRefresh() {
      const pin=required(),epoch=generation;
      const result=await call("native_refresh",{},{native:true});
      if(!current(pin,epoch) || result?.ignored || !visible)return {ignored:true};
      if(result?.schema!=="explorer_cooperative_native_controls_v1")throw new Error("Сервер не вернул текущие отдельные действия управления");
      nativeControls=copy(result);nativeRefreshedAt=now();notify();return result;
    }
    async function nativeCommand(role,operation) {
      roleRequired(role);const pin=required(),epoch=generation;
      if(!nativeOperations.includes(operation))throw new Error("Выберите реализованное действие управления");
      const choice=nativeControls?.roles?.[role]?.operations?.[operation],confirmed=copy(nativeFlags[role]);
      if(!visible || (busy && operation!=="native_stop") || (nativeUncertain.has(role) && operation!=="native_stop") || !snapshot().nativeFresh || choice?.implemented!==true || choice?.available!==true || !SHA.test(choice.choice_id || ""))throw new Error("Обновите текущее управление; действие недоступно или его ответ не подтверждён");
      if(confirmed.observing!==true || confirmed.user_observed_test_confirmed!==true || (["native_begin","native_recenter"].includes(operation) && confirmed.neutral!==true))throw new Error("Лично подтвердите текущий наблюдаемый тест и нейтральный ввод для запуска или recenter");
      nativeUncertain.add(role);for(const key of Object.keys(nativeFlags[role]))nativeFlags[role][key]=false;nativeRefreshedAt=-Infinity;notify();
      try {
        const result=await call(operation,{role,native_control_choice_id:choice.choice_id,observing:true,user_observed_test_confirmed:true,...(["native_begin","native_recenter"].includes(operation)?{neutral:true}:{})},{native:true,bypass:operation==="native_stop"});
        if(!current(pin,epoch) || result?.ignored || !visible)return {ignored:true};
        if(result?.role!==role || result?.operation!==operation || result?.native_controls?.schema!=="explorer_cooperative_native_controls_v1")throw new Error("Не подтверждён точный ответ действия выбранной руки");
        if(role==="receiver" && ["native_begin","native_stop"].includes(operation)) {
          roleRevision.receiver++;release("receiver");receiverSetup=null;receiverRefreshedAt=-Infinity;
          if(operation==="native_stop")watching=false;
        }
        nativeControls=copy(result.native_controls);nativeRefreshedAt=now();nativeUncertain.delete(role);notify();return result;
      } catch(failure) {if(current(pin,epoch)){
        if(role==="receiver"){roleRevision.receiver++;release("receiver");watching=false;receiverSetup=null;receiverRefreshedAt=-Infinity;}
        error=failure.message;notify();}throw failure;}
    }
    function interrupt(reason="Наблюдение прервано") {fence();context=identity();error=String(reason);notify();}
    function setVisible(value) {visible=value === true;if (!visible) interrupt("Страница потеряла текущее наблюдение");else notify();}
    return {snapshot:sync,sync,refresh,affirm,challenge,authorize,bind,displayObservation,displayed,observeDisplayed,startWatching,stopWatching,watchOnce,revoke,receiverRefresh,receiverCommand,nativeAffirm,nativeRefresh,nativeCommand,interrupt,setVisible,destroy:()=>{visible=false;fence();}};
  }

  function cooperativeObservedPanel(view={}) {
    const setup=view.setup || {},labels={giver:"ROSMASTER · единый 9D поток",receiver:"SO101 · native6 поток"};
    const texts={observing:"Я сейчас наблюдаю этого робота и его свежий кадр",neutral:"Все клавиши и стики отпущены",space_clear:"Рабочая область ROSMASTER свободна",robot_workspace_checked:"Рабочая область SO101 проверена",user_observed_test_confirmed:"Отдельно разрешаю этот текущий тест под моим наблюдением",empty_gripper_confirmed:"Захват сейчас пуст; я вижу его на текущем кадре",object_safe_no_dual_hold:"Предмет безопасно размещён; роботы не держат его одновременно"};
    const receipt=view.receiverReceipt,safeReceipt=receipt && ID.test(receipt.receipt_id || "") && SHA.test(receipt.sha256 || "") && receipt.url==="/api/cooperative/artifacts/receiver-metadata/"+receipt.receipt_id ? receipt : null;
    const receiverLabels={bind_guard:"Связать зарегистрированное направление с текущим SO101",capture_empty_open:"Зафиксировать пустой открытый захват",capture_empty_closed:"Зафиксировать пустой закрытый захват",reset_safe:"Снять блокировку после безопасного размещения предмета",adopt_hold:"Подтвердить наблюдение после штатной остановки SO101"};
    const receiverPanel=`<section class="spaced-section"><h3>Подготовка захвата SO101</h3><p>Покажите пустой открытый и пустой закрытый захват через штатную станцию. Здесь сохраняются фактические измерения и показанный кадр; движения не отправляются.</p><button type="button" class="button secondary" data-coop-observed-action="receiverRefresh" ${!view.visible || view.busy || !view.context ? "disabled" : ""}>Обновить подготовку SO101</button>${["observing","user_observed_test_confirmed","empty_gripper_confirmed","object_safe_no_dual_hold"].map(key=>`<label class="check-field"><input type="checkbox" data-coop-native-role="receiver" data-coop-native-affirm="${key}" ${view.nativeFlags?.receiver?.[key] ? "checked" : ""}>${texts[key]}</label>`).join("")}${Object.entries(receiverLabels).map(([op,label])=>{const state=view.receiverSetup?.operations?.[op],choices=rows(state?.choices);return choices.length ? choices.map(choice=>`<button type="button" class="button secondary" data-coop-observed-action="receiverCommand" data-receiver-operation="${op}" data-receiver-choice="${esc(choice.choice_id)}" ${!view.visible || view.busy || !view.receiverSetupFresh || state?.available!==true || !view.nativeFlags?.receiver?.observing || !view.nativeFlags?.receiver?.user_observed_test_confirmed || (op.startsWith("capture_") && !view.nativeFlags?.receiver?.empty_gripper_confirmed) || (op==="reset_safe" && !view.nativeFlags?.receiver?.object_safe_no_dual_hold) ? "disabled" : ""}>${label}${choices.length>1 ? " · "+esc(choice.label) : ""}</button>`).join("") : `<p class="form-hint">${label}: ${esc(state?.reason || "сначала обновите состояние")}</p>`;}).join("")}${view.receiverSetup?.reason ? `<p class="form-error">${esc(view.receiverSetup.reason)}</p>` : ""}${safeReceipt ? `<p><a href="${esc(safeReceipt.url)}" target="_blank" rel="noopener">Сохранённая проверка SO101</a> · SHA ${esc(safeReceipt.sha256.slice(0,12))}</p>` : ""}<p class="form-hint">Между действиями требуется новое явное подтверждение. После неопределённого ответа автоматического повтора нет: прочитайте текущее состояние. Направление и возврат записи не означают силу или удержание предмета.</p></section>`;
    const nativeLabels={native_begin:"Запустить выбранный ввод",native_resume:"Возобновить выбранный ввод",native_recenter:"Вернуть ввод в нейтральное состояние",native_attach:"Подключить текущий ввод к общей сессии",native_lease:"Продлить текущий ввод один раз",native_stop:"Остановить ввод именно этой руки"};
    const nativePanel=`<section class="spaced-section"><h3>Управление каждой рукой</h3><p>Каждое действие требует отдельного подтверждения. Обновление читает состояние. Продление выполняется только вашей кнопкой; повторов после ошибки нет.</p><button type="button" class="button secondary" data-coop-observed-action="nativeRefresh" ${!view.visible || view.busy || !view.context ? "disabled" : ""}>Обновить доступные действия управления</button><div class="form-grid">${ROLES.map(role=>{const state=view.nativeControls?.roles?.[role] || {};return `<fieldset><legend>${labels[role]}</legend>${["observing","user_observed_test_confirmed","neutral"].map(key=>`<label class="check-field"><input type="checkbox" data-coop-native-role="${role}" data-coop-native-affirm="${key}" ${view.nativeFlags?.[role]?.[key] ? "checked" : ""}>${texts[key]}</label>`).join("")}${Object.entries(nativeLabels).map(([op,label])=>{const item=state.operations?.[op];const confirmed=view.nativeFlags?.[role] || {};return `<button type="button" class="button ${op==="native_stop" ? "danger" : "secondary"}" data-coop-observed-action="nativeCommand" data-role="${role}" data-native-operation="${op}" ${!view.visible || !view.nativeFresh || (view.busy && op!=="native_stop") || item?.implemented!==true || item?.available!==true || !confirmed.observing || !confirmed.user_observed_test_confirmed || (["native_begin","native_recenter"].includes(op) && !confirmed.neutral) || (rows(view.nativeUncertain).includes(role) && op!=="native_stop") ? "disabled" : ""}>${label}</button>`;}).join("")}${state.reason ? `<p class="form-hint">${esc(state.reason)}</p>` : ""}${rows(view.nativeUncertain).includes(role) ? '<p class="form-error">Ответ действия не подтверждён. Обновите состояние и используйте точный STOP или общий STOP.</p>' : ""}</fieldset>`;}).join("")}</div><p class="form-hint">Подключение двух источников ещё не подтверждает удержание или передачу предмета. Общий протокол проверяет отдельные свидетельства.</p></section>`;
    const rolePanels=ROLES.map(role=>{
      const frame=view.frames?.[role],flags=view.flags?.[role] || emptyFlags(role),choice=setup.setup_choices?.[role],permission=setup.permission_choices?.[role];
      return `<fieldset><legend>${labels[role]}</legend><p>Фактический ввод: ${esc(view.context?.[role+"_input_mode"] || "не выбран")}. ${permission ? "Есть серверный выбор текущего допуска" : "Текущий отдельный допуск не подтверждён"}.</p>${keys[role].map(key=>`<label class="check-field"><input type="checkbox" data-coop-observed-role="${role}" data-coop-observed-affirm="${key}" ${flags[key] ? "checked" : ""}> ${texts[key]}</label>`).join("")}<p class="form-hint">Подтвердите условия, затем покажите новый исходный кадр и сразу подтвердите отдельный тест.</p><button type="button" class="button secondary" data-coop-observed-action="challenge" data-role="${role}" ${!view.visible || view.busy || choice?.challenge_supported !== true || rows(view.uncertain).includes(role) ? "disabled" : ""}>Показать точный текущий кадр ${role === "giver" ? "ROSMASTER" : "SO101"}</button>${frame ? `<figure><img class="camera-preview" data-coop-observed-frame="${role}" data-frame-token="${esc(frame.token)}" src="${esc(frame.url)}" alt="Точный native кадр ${role === "giver" ? "ROSMASTER" : "SO101"} для этой кооперативной сессии"><figcaption>${esc(frame.frame_id)} · boot ${esc(frame.boot_id)} · SHA256 ${esc(frame.jpeg_sha256)} · ${frame.displayed ? "показан браузером" : "ожидается фактический показ"}</figcaption></figure>` : ""}<button type="button" class="button primary" data-coop-observed-action="authorize" data-role="${role}" ${!view.visible || view.busy || !view.challenges?.[role] || !frame?.displayed || !Object.values(flags).every(Boolean) || rows(view.uncertain).includes(role) ? "disabled" : ""}>${role === "giver" ? "Разрешить отдельный ввод ROSMASTER" : "Разрешить SO101 и непрерывно наблюдать"}</button><button type="button" class="button danger" data-coop-observed-action="revoke" data-role="${role}" ${!permission ? "disabled" : ""}>Отозвать именно этот допуск</button>${rows(view.uncertain).includes(role) ? '<p class="form-error">Ответ разрешения или отзыва не подтверждён. Автоматического повтора нет; проверьте native состояние и используйте общий STOP.</p>' : ""}</fieldset>`;
    }).join("");
    return `<section class="panel cooperative-observed-workbench"><div class="panel-heading"><h2>Отдельное наблюдаемое разрешение обеих рук</h2><button type="button" class="button secondary" data-coop-observed-action="refresh" ${!view.context || view.busy ? "disabled" : ""}>Обновить текущие допуски</button></div><div class="panel-body"><p>Это отдельные разрешения пользователя для выбранной версии общего навыка. Шасси остаётся неподвижным. Данные и native action contracts 9D и 6D не объединяются; Experiment 06 не заменяется источником из другого эксперимента.</p>${view.error ? `<p role="alert" class="form-error">${esc(view.error)}</p>` : ""}${!view.context ? '<p>Выберите свежую сессию и обновите её серверное состояние. После STOP нужна новая сессия.</p>' : `<p>Навык ${esc(view.context.skill_id)} · версия ${esc(view.context.skill_version)} · текущая сцена и epoch закреплены сервером.</p>${rows(setup.blockers).length ? `<ul class="form-error">${setup.blockers.map(value=>`<li>${esc(value)}</li>`).join("")}</ul>` : ""}<div class="form-grid">${rolePanels}</div>${receiverPanel}${nativePanel}<button type="button" class="button primary" data-coop-observed-action="bind" ${!view.visible || view.busy || setup.bind_observed_test_available !== true ? "disabled" : ""}>Связать оба текущих допуска с этой сессией</button><p class="form-hint">Связывание сохраняет метаданные. Оно не выполняет native begin/resume, не отправляет команды и не превращает shadow в управление. Общий STOP находится в основном workbench.</p><button type="button" class="button secondary" data-coop-observed-action="startWatching" ${!view.visible || view.watching || !setup.permission_choices?.receiver || rows(view.uncertain).includes("receiver") ? "disabled" : ""}>Я непрерывно наблюдаю текущий SO101</button><button type="button" class="button secondary" data-coop-observed-action="stopWatching" ${!view.watching ? "disabled" : ""}>Прекратить подтверждение наблюдения</button><p role="status">${view.watching ? "Наблюдение включено вашим явным действием: подтверждаются только новые реально показанные кадры SO101." : "Наблюдение выключено. Возврат на вкладку или обновление его не включают."}</p>`}</div></section>`;
  }

  function bindCooperativeObserved(root,client,onError=()=>{},environment={}) {
    const document=environment.document || surface.document,window=environment.window || surface.window;let timer=null,disposed=false;
    const clear=()=>{if (timer !== null) (environment.clearTimeout || surface.clearTimeout)(timer);timer=null;};
    const fail=error=>{clear();onError(error);};
    const schedule=()=>{clear();if (disposed || !client.snapshot().watching || document?.visibilityState === "hidden") return;timer=(environment.setTimeout || surface.setTimeout)(()=>{timer=null;client.watchOnce().then(result=>{if (result?.waiting) schedule();}).catch(fail);},200);};
    const run=action=>Promise.resolve().then(action).then(schedule).catch(fail);
    const click=event=>{const button=event.target.closest?.("[data-coop-observed-action]");if (!button || !root.contains(button) || button.disabled) return;const role=button.dataset.role,actions={receiverRefresh:()=>client.receiverRefresh(),receiverCommand:()=>client.receiverCommand(button.dataset.receiverOperation,button.dataset.receiverChoice),nativeRefresh:()=>client.nativeRefresh(),nativeCommand:()=>client.nativeCommand(role,button.dataset.nativeOperation),refresh:()=>client.refresh(),challenge:()=>client.challenge(role),authorize:()=>client.authorize(role),bind:()=>client.bind(),revoke:()=>client.revoke(role),startWatching:()=>client.startWatching(),stopWatching:()=>client.stopWatching()};if (actions[button.dataset.coopObservedAction]) run(actions[button.dataset.coopObservedAction]);};
    const change=event=>{const element=event.target;if(root.contains(element) && !element.disabled && element.dataset?.coopNativeAffirm){try{client.nativeAffirm(element.dataset.coopNativeRole,element.dataset.coopNativeAffirm,element.checked);}catch(error){fail(error);}return;}if (!root.contains(element) || element.disabled || !element.dataset?.coopObservedAffirm) return;try {client.affirm(element.dataset.coopObservedRole,element.dataset.coopObservedAffirm,element.checked);} catch (error) {fail(error);}};
    const loaded=event=>{const image=event.target;if (!root.contains(image) || !image.dataset?.coopObservedFrame || !image.complete || !image.naturalWidth || document?.visibilityState === "hidden") return;if (image.getClientRects && !image.getClientRects().length) return;if (image.getBoundingClientRect) {const rect=image.getBoundingClientRect();if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0 || rect.top >= (window?.innerHeight ?? Infinity) || rect.left >= (window?.innerWidth ?? Infinity)) return;}run(()=>client.displayed(image.dataset.coopObservedFrame,Number(image.dataset.frameToken)));};
    const lost=()=>{clear();client.setVisible(false);},visibility=()=>{if (document?.visibilityState === "hidden") lost();else client.setVisible(true);},focus=()=>{if (document?.visibilityState !== "hidden") client.setVisible(true);};
    const scroll=()=>{for (const image of root.querySelectorAll?.("img[data-coop-observed-frame]") || []) loaded({target:image});};
    root.addEventListener("click",click);root.addEventListener("change",change);root.addEventListener("load",loaded,true);document?.addEventListener("visibilitychange",visibility);window?.addEventListener("blur",lost);window?.addEventListener("pagehide",lost);window?.addEventListener("focus",focus);window?.addEventListener("scroll",scroll,{passive:true});if (client.snapshot().watching) schedule();
    return ()=>{disposed=true;clear();root.removeEventListener("click",click);root.removeEventListener("change",change);root.removeEventListener("load",loaded,true);document?.removeEventListener("visibilitychange",visibility);window?.removeEventListener("blur",lost);window?.removeEventListener("pagehide",lost);window?.removeEventListener("focus",focus);window?.removeEventListener("scroll",scroll);};
  }
  const exported={createCooperativeObservedClient,cooperativeObservedPanel,bindCooperativeObserved};Object.assign(surface,exported);if (typeof module !== "undefined" && module.exports) module.exports=exported;
})(typeof globalThis === "undefined" ? window : globalThis);
