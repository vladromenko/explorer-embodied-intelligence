"use strict";

// ENGINEERING_GLUE: measured leader arm and explicit keyboard base share the native 9D owner.
const direct03 = { owned: false, ownedEpoch: null, pendingEpoch: null, demoMode: "mirror", operatorProfile: "ARM_ONLY", status: null, error: null, polling: false, busy: false, held: false, leasePending: false, sequence: 0, interrupt: 0, signature: "" };
const direct03Route = () => window.location.pathname.replace(/\/$/, "") === "/experiments/03";
const direct03BaseKeys = new Set();
const direct03OwnAttempt = () => ["CLUTCHED", "ACTIVE"].includes(direct03.status?.state);
const direct03ContinuityReady = () => direct03.status?.physical_path_admission?.allowed !== false;
const direct03HeldABI = () => direct03.status?.source_hold_gate_schema === "explorer_pi_native_explicit_hold_v1";
const direct03BaseMap = {KeyW:"forward",KeyS:"backward",KeyA:"left",KeyD:"right",KeyQ:"turn_left",KeyE:"turn_right"};
function direct03Panel(){
 const active=direct03.owned;
 const phase=direct03.status?.initial_alignment_phase;
 const phaseText=phase==="ALIGNING_TO_LEADER"?"ROSMASTER плавно совмещается с ведущей рукой…":phase==="FOLLOWING"?"Поза совмещена — движения повторяются.":"";
 return `<section class="panel"><div class="panel-heading"><h2>Leader → ROSMASTER</h2></div><div class="panel-body"><p>Нажмите «Управлять» и удерживайте ведущую SO-101 неподвижно до надписи «Поза совмещена». Рука ROSMASTER снача плавно встанет в ту же позу, затем начнёт повторять движения. Шасси остаётся неподвижным.</p>${phaseText?`<p class="form-note">${phaseText}</p>`:""}<div class="teleop-actions"><button class="button primary" data-action="${active?"direct03-stop":"direct03-start"}" ${direct03.busy?"disabled":""}>${active?"Отключить управление":"Управлять"}</button>${active?`<button class="button secondary" data-action="direct03-recenter">Текущая поза — начало</button>`:""}</div>${direct03.error?`<p class="form-error">${esc(direct03.error)}</p>`:""}</div></section>`;
}

async function pollDirect03() {
  if (typeof api !== "function" || typeof model === "undefined" || typeof render !== "function") return;
  if (!direct03Route() || direct03.polling || direct03.busy || document.hidden) return;
  direct03.polling = true;
  try {
    const status = await api("/api/retarget/status");
    const signature = JSON.stringify([status.state, status.control_epoch, status.registration?.registration_sha256 || status.registration?.registration?.sha256, status.registration?.state, status.registration?.missing_cross_map || status.registration?.missing, status.operator_profile, status.source_hold_gate_schema, status.initial_alignment_phase, status.error, status.last_result?.target_receipt?.state, status.recording]);
    if (direct03.ownedEpoch && status.control_epoch !== direct03.ownedEpoch) {
      direct03.owned = false; direct03.ownedEpoch = null; direct03.held = false;
      direct03BaseKeys.clear(); direct03.interrupt += 1;
    }
    direct03.status = status; direct03.error = null;
    if (direct03OwnAttempt() && status.operator_profile) direct03.operatorProfile = status.operator_profile;
    if (signature !== direct03.signature && !direct03.held) { direct03.signature = signature; model.forceRender = true; render(); }
  } catch (error) {
    if (direct03.error !== error.message) { direct03.error = error.message; if (!direct03.held) { model.forceRender = true; render(); } }
  } finally { direct03.polling = false; }
}
async function direct03Stop(quiet = true, capturedEpoch = direct03.ownedEpoch) {
  direct03BaseKeys.clear();
  direct03.held = false; direct03.owned = false; direct03.ownedEpoch = null; direct03.interrupt += 1;
  const interrupt = direct03.interrupt;
  if (!capturedEpoch) { if (direct03Route()) { model.forceRender = true; render(); } return; }
  try { const result = await post("/api/retarget/stop", { control_epoch: capturedEpoch }, { keepalive: true }); if (interrupt === direct03.interrupt) direct03.status = result; direct03.error = null; if (!quiet) toast("Прямой контур завершён. Физическая остановка проверяется по наблюдению."); }
  catch (error) { direct03.error = `Ответ на STOP не получен: ${error.message}`; if (!quiet) toast(direct03.error, true); }
  if (direct03Route()) { model.forceRender = true; render(); }
}
async function direct03Lease() {
  if (!direct03.owned && !direct03.held) return;
  if (typeof rosmasterCameraReady !== "function" || !rosmasterCameraReady()) { await direct03Stop(); return; }
  if (!direct03.owned || !direct03HeldABI() || !direct03.held || direct03.leasePending || !direct03Route() || document.hidden || !document.hasFocus()) return;
  direct03.leasePending = true;
  const epoch = direct03.ownedEpoch;
  if (!epoch) { direct03.leasePending = false; return; }
  try {
    const previousPhase=direct03.status?.initial_alignment_phase;
    const base_actions=direct03.operatorProfile === "MOBILE_MANIPULATION" && typeof observedNative!=="undefined"&&observedNative.chassisAllowed()?Object.fromEntries([...direct03BaseKeys].map(key=>[direct03BaseMap[key],1])):{};
    const result = await post("/api/retarget/lease", { control_epoch: epoch, sequence: direct03.sequence++, held: true, observing: true,base_actions });
    if (direct03.held && direct03.ownedEpoch === epoch) { direct03.status = result; direct03.ownedEpoch = result.control_epoch; if(previousPhase!==result.initial_alignment_phase){model.forceRender=true;render();} }
  } catch (error) { direct03.error = error.message; await direct03Stop(true, epoch); }
  finally { direct03.leasePending = false; }
}
async function direct03Control(action){
  if (action === "direct03-stop") { await direct03Stop(false); return; }
  if (direct03.busy || !["direct03-start", "direct03-recenter"].includes(action)) return;
  direct03.busy = true;
  const interrupt = direct03.interrupt;
  let capturedEpoch = direct03.ownedEpoch || direct03.status?.control_epoch;
  direct03.pendingEpoch = capturedEpoch;
  try {
    if (!direct03ContinuityReady()) throw new Error("Сначала требуется continuity-проверка на реальном меняющемся входе leader. Физический контур пока закрыт.");
    if (!direct03HeldABI()) throw new Error("Сначала требуется обновлённый контур Pi с передачей только по удержанию.");
    if (!rosmasterCameraReady()) throw new Error("Нужны свежие кадры камеры ROSMASTER.");
    if(action==="direct03-start"){
      await stopKeyboard(true);await browserGamepad.disconnect();await sourceDemonstration.drain();
      // A reload or an expired browser heartbeat may leave an exact old Pi
      // retarget epoch CLUTCHED even though this tab no longer owns it.  The
      // user's new "Управлять" click explicitly replaces that source: stop
      // the original shared native teleop first.  This also invalidates a Nano
      // mux whose browser lease expired before Pi could deliver its scoped
      // STOP; Pi can then verify that the retained target epoch is inactive.
      const previous=direct03.status || await api("/api/retarget/status");
      await post("/api/robot/teleop/stop",{},{keepalive:true});
      if(previous?.target_control_epoch || ["CLUTCHED","ACTIVE","ACTIVATING","CLUTCHING","INTERRUPTED","STOP_DELIVERY_UNKNOWN"].includes(previous?.state)){
        const stopped=await post("/api/retarget/stop",{control_epoch:previous.control_epoch},{keepalive:true});
        direct03.status=stopped;capturedEpoch=stopped.control_epoch;
      }
      await post("/api/operator/source-connect",{});
      const fresh=await api("/api/retarget/status");direct03.status=fresh;capturedEpoch=fresh.control_epoch;
    }
    if(action==="direct03-start")await observedNative.connect("so101_leader",{chassis:direct03.operatorProfile==="MOBILE_MANIPULATION",initializeArm:nativeArmNeedsHome()});
    else await observedNative.requirePermission("so101_leader");
    if (interrupt !== direct03.interrupt || !direct03Route() || document.hidden || !document.hasFocus()) return;
    const result = await post(`/api/retarget/${action === "direct03-start" ? "start" : "recenter"}`, { expected_control_epoch: capturedEpoch, observing: true, ...(action === "direct03-start" ? { operator_profile: direct03.operatorProfile, initial_alignment: "LEADER_POSE" } : {}) });
    if (interrupt !== direct03.interrupt || !direct03Route() || document.hidden || !document.hasFocus()) { await direct03Stop(true, result.control_epoch); return; }
    direct03.status = result; direct03.owned = ["CLUTCHED", "ACTIVE"].includes(result.state); direct03.ownedEpoch = direct03.owned ? result.control_epoch : null; direct03.sequence = 0; direct03.error = null;
    if (direct03.owned && action === "direct03-start") {direct03.held=true;await direct03Lease();}
    if (typeof startCamera === "function") startCamera();
  } catch (error) {
    direct03.error=/Power policy blocks arm commissioning|NATIVE_REFERENCE_UNAVAILABLE.*Power policy/i.test(error.message)
      ? "Питание ROSMASTER слишком низкое для руки. Подключите зарядку или заряженную батарею; после восстановления питания нажмите «Управлять» ещё раз."
      : /Existing native STOP and neutral inputs|required before observed authorization/i.test(error.message)
      ? "Предыдущее ручное управление ещё завершалось. Нажмите «Управлять» ещё раз — Explorer сначала остановит прежний источник и возьмёт текущие позы как начало."
      : error.message;
    toast(direct03.error, true);
  }
  finally { direct03.pendingEpoch = null; direct03.busy = false; model.forceRender = true; render(); }
}
document.addEventListener("click",event=>{const button=event.target.closest('button[data-action^="direct03-"]');if(button&&!button.disabled)direct03Control(button.dataset.action);});

document.addEventListener("pointerdown", (event) => {
  const button = event.target.closest("#direct03-held");
  if (!button || button.disabled || !direct03Route() || !direct03OwnAttempt() || !direct03.owned || !direct03HeldABI()) return;
  event.preventDefault(); direct03.held = true; direct03Lease();
});
function releaseDirect03(event) { if (direct03.held && event?.target?.closest?.("#direct03-held")) direct03Stop(); }
document.addEventListener("pointerup", releaseDirect03);
document.addEventListener("pointercancel", releaseDirect03);
document.addEventListener("keydown",event=>{
  if(!direct03Route()||!direct03.held||event.target.closest("input,select,textarea,[contenteditable=true]"))return;
  if(["Space","Escape"].includes(event.code)){event.preventDefault();void direct03Stop();return;}
  if(direct03.operatorProfile==="MOBILE_MANIPULATION"&&direct03BaseMap[event.code]&&typeof observedNative!=="undefined"&&observedNative.chassisAllowed()){event.preventDefault();direct03BaseKeys.add(event.code);}
});
document.addEventListener("keyup",event=>{if(direct03BaseKeys.delete(event.code))event.preventDefault();});
window.addEventListener("blur", () => { if (direct03.owned || direct03.held || direct03.busy) direct03Stop(); });
window.addEventListener("pagehide",()=>{if(direct03.owned)direct03Stop();});
window.addEventListener("popstate", () => { if (direct03.owned) direct03Stop(); });
document.addEventListener("visibilitychange", () => { if (document.hidden && (direct03.owned || direct03.held || direct03.busy)) direct03Stop(); });
document.addEventListener("click", (event) => { if (event.target.closest("a[data-link]") && direct03.owned) direct03Stop(); });
setInterval(direct03Lease, 150);
setInterval(pollDirect03, 650);

document.addEventListener("change", event => {
  if (event.target.id !== "direct03-operator-profile") return;
  if (direct03OwnAttempt() || direct03.busy) { event.target.value = direct03.operatorProfile; return; }
  if (!["ARM_ONLY", "MOBILE_MANIPULATION"].includes(event.target.value)) return;
  direct03.operatorProfile = event.target.value; direct03BaseKeys.clear(); model.forceRender = true; render();
});
