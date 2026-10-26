"use strict";
// ENGINEERING_GLUE: explicit observed permission and cached-frame wizard.
// Loading/refreshing never authorizes, homes, starts a controller or records.
(function (surface) {
  const base = "/api/robot/explorer/retarget/activation/";
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function createObservedNativeClient(options) {
    let data = { activation:null, registration:null, challenge:null, frameURL:null, displayed:false, busy:false, error:"", inputMode:"so101_leader", confirmations:{observing:false,neutral:false,space_clear:false,user_observed_test_confirmed:false}, chassis:false, axes:false, home:false };
    let revision=0, displayedAt=null;
    const now=options.now || (()=>performance.now());
    const emit=()=>options.onChange?.(clone(data));
    const call=(path,body)=>options.api(path,body===undefined?{}:{method:"POST",body:JSON.stringify(body)});
    function clearFrame() { if(data.frameURL)options.revokeURL(data.frameURL);data.frameURL=null;data.displayed=false;data.challenge=null;displayedAt=null; }
    function cancelPending() { ++revision;clearFrame();emit(); }
    async function refresh() {
      const token=revision;
      try {
        const [activation,source]=await Promise.all([call(base+"status"),call("/api/retarget/status")]);
        if(token===revision){data.activation=activation;data.registration=source.calibration_wizard||null;data.error="";emit();}
      } catch(error){if(token===revision){data.error=error.message;emit();}throw error;}
      return clone(data);
    }
    async function freeze() {
      if(data.busy)throw new Error("Дождитесь предыдущего действия");
      data.busy=true;clearFrame();const token=++revision;emit();
      let temporary=null;
      try {
        const challenge=await call(base+"challenge");
        if(!/^[0-9a-f]{32}$/.test(challenge.challenge_id||""))throw new Error("Не получен точный ID native-кадра");
        const frame=await options.frame(challenge.challenge_id);
        if(frame.sha256!==challenge.frame?.jpeg_sha256 || frame.frame_id!==challenge.frame?.frame_id || frame.boot_id!==challenge.frame?.boot_id)throw new Error("Кадр отличается от native challenge");
        temporary=options.createURL(frame.blob);await options.decode(temporary);
        if(token!==revision)throw new Error("Показ кадра прерван; запросите новый кадр");
        data.challenge=challenge;data.frameURL=temporary;temporary=null;data.displayed=true;displayedAt=now();data.error="";
      } catch(error){if(token===revision)data.error=error.message;throw error;}
      finally{if(temporary)options.revokeURL(temporary);data.busy=false;emit();}
      return clone(data);
    }
    async function authorize() {
      if(data.busy || !data.displayed || !data.challenge || displayedAt===null || now()-displayedAt>2000)throw new Error("Покажите новый native-кадр и подтвердите его сразу после просмотра");
      if(!Object.values(data.confirmations).every(value=>value===true))throw new Error("Нужны все явные подтверждения текущего наблюдаемого теста");
      const token=revision;data.busy=true;emit();
      try {
        const result=await call(base+"authorize",{...data.confirmations,challenge_id:data.challenge.challenge_id,displayed_jpeg_sha256:data.challenge.frame.jpeg_sha256,input_mode:data.inputMode,owner_chassis_test_authorized:data.chassis===true});
        if(token!==revision){await call(base+"revoke",{expected_authorization_id:result.authorization_id});throw new Error("Подтверждение прервано; разрешение отозвано");}
        data.activation=result;clearFrame();data.error="";
      } catch(error){data.error=error.message;throw error;}
      finally{data.busy=false;emit();}
      return clone(data);
    }
    async function revoke() {
      ++revision;clearFrame();data.activation=null;emit();
      return call(base+"revoke",{});
    }
    async function requirePermission(mode) {
      const token=revision;const result=await call(base+"status");
      if(token!==revision)throw new Error("Проверка наблюдения прервана; подключите управление заново");
      data.activation=result;emit();
      if(result.execution_permission!==true || !result.authorization_id || result.input_mode!==mode)throw new Error("Сначала подтвердите отдельный наблюдаемый тест для этого способа управления");
      return result;
    }
    async function home() {
      if(data.home!==true || data.activation?.execution_permission!==true)throw new Error("Отдельно подтвердите видимое штатное движение HOME");
      const id=data.activation.authorization_id;cancelPending();data.activation=null;emit();
      const result=await call("/api/robot/arm/prepare",{observing:true,space_clear:true,authorization_id:id,acknowledge_factory_home_motion:true});
      data.home=false;await refresh();return result;
    }
    async function capture(kind) {
      if(data.confirmations.observing!==true || data.confirmations.user_observed_test_confirmed!==true)throw new Error("Подтвердите присутствие и текущий наблюдаемый тест");
      if(kind==="task_axes" && data.axes!==true)throw new Error("Проверьте оси и ориентацию двух рук");
      const result=await call("/api/retarget/calibration/capture",{kind,observing:true,user_observed_test_confirmed:true,axes_and_orientation_confirmed:data.axes===true});
      data.registration=result;emit();return result;
    }
    async function acceptRegistration() {
      const result=await call("/api/retarget/calibration/accept",{observing:data.confirmations.observing===true,accept_observed_cross_map:true,expected_registration_sha256:data.registration?.current_registration_sha256??null});
      data.registration=result;emit();return result;
    }
    return {snapshot:()=>clone(data),refresh,freeze,authorize,revoke,requirePermission,home,capture,acceptRegistration,cancelPending,
      configure(patch){for(const key of ["inputMode","chassis","axes","home"])if(key in patch)data[key]=patch[key];if(patch.confirmations)Object.assign(data.confirmations,patch.confirmations);emit();},
      chassisAllowed:()=>data.activation?.execution_permission===true && data.activation?.chassis_motion_allowed===true};
  }
  function observedNativePanel(data={}) {
    const activation=data.activation||{},disabled=data.busy?"disabled":"",captures=data.registration?.captured||[];
    const checked=(name)=>data.confirmations?.[name]===true?"checked":"";
    return `<section class="panel spaced-section"><div class="panel-heading"><h2>Отдельный наблюдаемый тест ROSMASTER</h2><span class="badge ${activation.execution_permission?"warn":"neutral"}">${activation.execution_permission?"Разрешение активно":"Нужно явное подтверждение"}</span></div><div class="panel-body"><p>Сначала подтвердите условия, затем покажите свежий кадр и подтвердите его. Это разрешение сессии; движение начинается отдельной кнопкой управления. STOP, потеря камеры или смена native-сессии отменяют разрешение.</p>${data.error?`<p class="form-error" role="alert">${esc(data.error)}</p>`:""}<label>Способ управления<select data-observed-mode>${[["keyboard","Клавиатура"],["browser_gamepad","Геймпад браузера"],["so101_leader","SO101 leader"]].map(([id,label])=>`<option value="${id}" ${data.inputMode===id?"selected":""}>${label}</option>`).join("")}</select></label>${[["observing","Я сейчас наблюдаю робота и руку"],["neutral","Все клавиши и стики отпущены"],["space_clear","Область движения свободна"],["user_observed_test_confirmed","Я начинаю отдельный наблюдаемый тест"]].map(([id,label])=>`<label><input type="checkbox" data-observed-confirm="${id}" ${checked(id)}>${label}</label>`).join("")}<label><input type="checkbox" data-observed-field="chassis" ${data.chassis?"checked":""}>Отдельно разрешаю движение шасси ТОЛЬКО в этом наблюдаемом тесте</label><p class="form-hint">По умолчанию шасси заблокировано. Настройки, пределы и lease штатного контроллера сохраняются.</p><div class="teleop-actions"><button type="button" class="button secondary" data-observed-action="refresh" ${disabled}>Проверить состояние</button><button type="button" class="button secondary" data-observed-action="freeze" ${disabled}>Показать текущий native-кадр</button><button type="button" class="button primary" data-observed-action="authorize" ${disabled||!data.displayed?"disabled":""}>Вижу этот кадр · разрешить тест</button><button type="button" class="button danger" data-observed-action="revoke">Отозвать разрешение</button></div>${data.frameURL?`<img class="camera-preview" src="${esc(data.frameURL)}" alt="Точный native-кадр для подтверждения текущего наблюдения"><p>Кадр ${esc(data.challenge?.frame?.frame_id)}. Подтвердите сразу; просроченный кадр требуется обновить.</p>`:""}<details><summary>Штатный HOME и текущая командная оценка</summary><p>HOME действительно двигает руку: штатные шесть осей к 90° за 4000 ms. Выполняйте только под текущим наблюдением, если исходный reference недействителен. Servo feedback остаётся выключенным. После HOME требуется новый кадр и новое разрешение.</p><label><input type="checkbox" data-observed-field="home" ${data.home?"checked":""}>Подтверждаю именно это физическое движение штатного HOME</label><button type="button" class="button danger" data-observed-action="home" ${!data.home||!activation.execution_permission||data.busy?"disabled":""}>Выполнить штатный HOME под моим наблюдением</button></details><details><summary>Регистрация leader: три наблюдения без программного движения</summary><p>Исходная моторная калибровка уже известна. Вручную покажите открытый и закрытый захват leader; затем проверьте направления и ориентацию двух рук. Сохраняются cached-измерения и исходные кадры.</p><label><input type="checkbox" data-observed-field="axes" ${data.axes?"checked":""}>Направления и ориентация рук проверены; текущий reference ROSMASTER действителен</label><div class="teleop-actions">${[["leader_gripper_open","Захват leader открыт"],["leader_gripper_closed","Захват leader закрыт"],["task_axes","Оси и ориентация проверены"]].map(([id,label])=>`<button type="button" class="button secondary" data-observed-capture="${id}" ${disabled}>${label}${captures.includes(id)?" ✓":""}</button>`).join("")}<button type="button" class="button primary" data-observed-action="accept" ${captures.length===3&&!data.busy?"":"disabled"}>Принять наблюдённую регистрацию</button></div><p>Принятие сохраняет регистрацию приложения и не запускает зеркало. Геометрия контакта и метрическая апертура этим не подтверждаются.</p></details></div></section>`;
  }
  const exported={createObservedNativeClient,observedNativePanel};Object.assign(surface,exported);
  if(typeof module!=="undefined" && module.exports)module.exports=exported;
})(typeof window!=="undefined"?window:globalThis);
