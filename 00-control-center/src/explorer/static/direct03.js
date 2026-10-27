"use strict";

// ENGINEERING_GLUE: measured leader arm and explicit keyboard base share the native 9D owner.
const direct03 = { owned: false, ownedEpoch: null, pendingEpoch: null, demoMode: "mirror", operatorProfile: "ARM_ONLY", status: null, error: null, polling: false, busy: false, held: false, leasePending: false, sequence: 0, interrupt: 0, signature: "" };
const direct03Route = () => window.location.pathname.replace(/\/$/, "") === "/experiments/03";
const direct03BaseKeys = new Set();
const direct03OwnAttempt = () => ["CLUTCHED", "ACTIVE"].includes(direct03.status?.state);
const direct03ContinuityReady = () => direct03.status?.physical_path_admission?.allowed === true && direct03.status?.physical_path_admission?.state === "PASS";
const direct03HeldABI = () => direct03.status?.source_hold_gate_schema === "explorer_pi_native_explicit_hold_v1";
const direct03BaseMap = {KeyW:"forward",KeyS:"backward",KeyA:"left",KeyD:"right",KeyQ:"turn_left",KeyE:"turn_right"};
function direct03Panel() {
  const status = direct03.status || {};
  const active = status.state === "ACTIVE";
  const attempt = ["CLUTCHED", "ACTIVE"].includes(status.state);
  const safeHold = direct03HeldABI() && direct03ContinuityReady();
  const registered = ["REGISTERED_DECLARATION", "BOOTSTRAP_DERIVED"].includes(status.registration?.state);
  const bootstrap = status.registration?.state === "BOOTSTRAP_DERIVED";
  const registrationProfile = status.registration?.registration || {};
  const labels = { CLUTCHED: "Контур открыт · передача движений выключена", CLUTCHING: "Меняем начало · передача выключена", UNREGISTERED: "Нужны cross-map и текущий reference", IDLE: "Ожидает включения", ACTIVE: "Прямой контур включён", STOPPED: "Остановлен", PEER_NOT_CONFIGURED: "Не задан адрес Nano", UNKNOWN_OUTCOME: "Доставка STOP не подтверждена", INTERRUPTED: "Попытка прервана" };
  const receipt = status.last_result?.target_receipt;
  const missing = array(status.registration?.missing_cross_map || status.registration?.missing);
  const missingLabels = { OBSERVED_TASK_AXES_AND_ORIENTATION: "Наблюдаемая привязка осей и ориентации в рабочей системе координат", TWO_OBSERVED_LEADER_GRIPPER_ENDPOINTS: "Две визуально подтверждённые пустые позы захвата leader: открыто и закрыто" };
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">03 / Цель ROSMASTER</p><h2>Ведущая SO-101 → рука ROSMASTER</h2></div><span class="badge ${active ? "warn" : "neutral"}">${esc(labels[status.state] || "Получаем состояние")}</span></div><div class="panel-body"><p class="form-description">Ручной контур Pi → Nano: ведущая SO-101 задаёт цель руки ROSMASTER через FK/IK. Обучение для этого не требуется. Ведомая SO-101 не является целевым роботом опыта 03. Рука и клавиатурная база используют единый штатный исполнитель. База доступна только после отдельного явного разрешения пользователя на наблюдаемый тест.</p>${bootstrap ? `<p class="form-description" role="status">Bootstrap: существующая калибровка LeRobot и геометрия URDF. Физическая привязка ещё не проверена. Направления базы предполагаются согласованными; проверьте «leader вперёд = ROSMASTER вперёд», затем малые движения по трём осям, wrist и захват. Захват 0…100 предварительно соответствует 170…30°. Текущее начало — командная оценка ROSMASTER, не измеренное положение.</p><p class="form-hint direct03-provenance">Ориентация: ${esc(registrationProfile.frame_convention || "—")} · provenance: ${esc(registrationProfile.provenance || "—")} · SHA: ${esc(registrationProfile.sha256 || "—")}</p>` : ""}${!registered ? `<p class="form-description">Калибровка leader сохраняется. Bootstrap строится из существующих исходников; проверьте их доступность и SHA. Для старта нужен текущий native reference/boot; измеренный feedback ROSMASTER не требуется и не включается.</p>${missing.length ? `<details><summary>Каких измерений не хватает</summary><ul>${missing.map((item) => `<li>${esc(missingLabels[item] || item)}</li>`).join("")}</ul></details>` : ""}` : ""}<div class="form-field"><label for="direct03-operator-profile">Режим показа</label><select id="direct03-operator-profile" ${attempt || direct03.busy ? "disabled" : ""}><option value="ARM_ONLY" ${direct03.operatorProfile === "ARM_ONLY" ? "selected" : ""}>Только рука · шасси неподвижно</option><option value="MOBILE_MANIPULATION" ${direct03.operatorProfile === "MOBILE_MANIPULATION" ? "selected" : ""}>Рука + клавиатурная база</option></select></div><p class="form-hint">Оба режима сохраняют полный9D контракт. В режиме руки три скорости базы равны нулю. Для мобильного режима отдельно требуется текущее разрешение владельца; сейчас шасси запрещено.</p>${!safeHold ? '<p class="form-hint" role="status">Передача движений закрыта до проверки непрерывности на реальных меняющихся входах leader; также требуется совместимый контур Pi. Запись технических наблюдений доступна без движения.</p>' : ""}${attempt && !direct03.owned ? '<p class="form-hint">Контур открыт в другой вкладке. Эта вкладка не отправляет ввод; завершите прежний контур перед новым включением.</p>' : ""}<div class="teleop-actions"><button class="button primary" data-action="direct03-start" type="button" ${registered && status.peer_configured && safeHold && !attempt && !direct03.busy ? "" : "disabled"}>Наблюдаю сцену · включить контур</button><button class="button secondary" data-action="direct03-recenter" type="button" ${attempt && direct03.owned && safeHold && !direct03.busy && !direct03.held ? "" : "disabled"}>Текущая поза — начало</button><button class="button danger" data-action="direct03-stop" type="button">STOP · контур leader</button></div><button class="button secondary full" id="direct03-held" type="button" style="touch-action:none;user-select:none" ${attempt && direct03.owned && safeHold && !direct03.busy ? "" : "disabled"}>Удерживайте для передачи движений</button><p class="form-hint">Первое удержание отдельно включает передачу. Отпускание кнопки или уход из своей вкладки завершает попытку. Heartbeat сохраняет прежнюю lease исходного исполнителя. Старт и смена начала сами не посылают движение.</p>${receipt ? `<dl class="summary-list">${kv("Последний ответ Nano", receipt.state || "—")}${kv("Публикация native host", receipt.dispatch_confirmed === true ? "Подтверждена источником; wire ACK не измерен" : "Не подтверждена: ответ API")}</dl>` : ""}${status.run_id ? `<p class="form-hint">Журнал попытки: ${esc(status.run_id)}. Физический успех отмечается отдельно.</p>` : ""}${direct03.error || status.error ? `<p class="form-error" role="alert">${esc(direct03.error || status.error)}</p>` : ""}</div></section>`;
}
async function pollDirect03() {
  if (typeof api !== "function" || typeof model === "undefined" || typeof render !== "function") return;
  if (!direct03Route() || direct03.polling || direct03.busy || document.hidden) return;
  direct03.polling = true;
  try {
    const status = await api("/api/retarget/status");
    const signature = JSON.stringify([status.state, status.control_epoch, status.registration?.registration_sha256 || status.registration?.registration?.sha256, status.registration?.state, status.registration?.missing_cross_map || status.registration?.missing, status.operator_profile, status.source_hold_gate_schema, status.error, status.last_result?.target_receipt?.state, status.recording]);
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
    const base_actions=direct03.operatorProfile === "MOBILE_MANIPULATION" && typeof observedNative!=="undefined"&&observedNative.chassisAllowed()?Object.fromEntries([...direct03BaseKeys].map(key=>[direct03BaseMap[key],1])):{};
    const result = await post("/api/retarget/lease", { control_epoch: epoch, sequence: direct03.sequence++, held: true, observing: true,base_actions });
    if (direct03.held && direct03.ownedEpoch === epoch) { direct03.status = result; direct03.ownedEpoch = result.control_epoch; }
  } catch (error) { direct03.error = error.message; await direct03Stop(true, epoch); }
  finally { direct03.leasePending = false; }
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest('button[data-action^="direct03-"]');
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (action === "direct03-stop") { await direct03Stop(false); return; }
  if (direct03.busy || !["direct03-start", "direct03-recenter"].includes(action)) return;
  direct03.busy = true;
  const interrupt = direct03.interrupt;
  const capturedEpoch = direct03.ownedEpoch || direct03.status?.control_epoch;
  direct03.pendingEpoch = capturedEpoch;
  try {
    if (!direct03ContinuityReady()) throw new Error("Сначала требуется continuity-проверка на реальном меняющемся входе leader. Физический контур пока закрыт.");
    if (!direct03HeldABI()) throw new Error("Сначала требуется обновлённый контур Pi с передачей только по удержанию.");
    if (!rosmasterCameraReady()) throw new Error("Нужны свежие кадры камеры ROSMASTER.");
    await observedNative.requirePermission("so101_leader");
    if (interrupt !== direct03.interrupt || !direct03Route() || document.hidden || !document.hasFocus()) return;
    const result = await post(`/api/retarget/${action === "direct03-start" ? "start" : "recenter"}`, { expected_control_epoch: capturedEpoch, observing: true, ...(action === "direct03-start" ? { operator_profile: direct03.operatorProfile } : {}) });
    if (interrupt !== direct03.interrupt || !direct03Route() || document.hidden || !document.hasFocus()) { await direct03Stop(true, result.control_epoch); return; }
    direct03.status = result; direct03.owned = ["CLUTCHED", "ACTIVE"].includes(result.state); direct03.ownedEpoch = direct03.owned ? result.control_epoch : null; direct03.sequence = 0; direct03.error = null;
    if (direct03.owned) toast("Начало принято. Для передачи движений удерживайте кнопку.");
  } catch (error) { direct03.error = error.message; toast(error.message, true); }
  finally { direct03.pendingEpoch = null; direct03.busy = false; model.forceRender = true; render(); }
});
document.addEventListener("pointerdown", (event) => {
  const button = event.target.closest("#direct03-held");
  if (!button || button.disabled || !direct03Route() || !direct03OwnAttempt() || !direct03.owned || !direct03HeldABI()) return;
  event.preventDefault(); direct03.held = true; direct03Lease();
});
function releaseDirect03() { if (direct03.held) direct03Stop(); }
document.addEventListener("pointerup", releaseDirect03);
document.addEventListener("pointercancel", releaseDirect03);
document.addEventListener("keydown",event=>{
  if(!direct03Route()||!direct03.held||event.target.closest("input,select,textarea,[contenteditable=true]"))return;
  if(["Space","Escape"].includes(event.code)){event.preventDefault();void direct03Stop();return;}
  if(direct03.operatorProfile==="MOBILE_MANIPULATION"&&direct03BaseMap[event.code]&&typeof observedNative!=="undefined"&&observedNative.chassisAllowed()){event.preventDefault();direct03BaseKeys.add(event.code);}
});
document.addEventListener("keyup",event=>{if(direct03BaseKeys.delete(event.code))event.preventDefault();});
window.addEventListener("blur", () => { if (direct03.owned || direct03.held || direct03.busy) direct03Stop(); });
window.addEventListener("pagehide", releaseDirect03);
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
