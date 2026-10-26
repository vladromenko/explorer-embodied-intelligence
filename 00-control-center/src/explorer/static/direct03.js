"use strict";

// ENGINEERING_GLUE: explicit operator-held direct Pi -> Nano, ARM_ONLY.
const direct03 = { status: null, error: null, polling: false, busy: false, held: false, leasePending: false, sequence: 0, interrupt: 0, signature: "" };
const direct03Route = () => window.location.pathname.replace(/\/$/, "") === "/experiments/03";
function direct03Panel() {
  const status = direct03.status || {};
  const active = status.state === "ACTIVE";
  const registered = status.registration?.state === "REGISTERED_DECLARATION";
  const labels = { UNREGISTERED: "Нужна измеренная регистрация", IDLE: "Ожидает включения", ACTIVE: "Прямой контур включён", STOPPED: "Остановлен", PEER_NOT_CONFIGURED: "Не задан адрес Nano", UNKNOWN_OUTCOME: "Доставка STOP не подтверждена", INTERRUPTED: "Попытка прервана" };
  const receipt = status.last_result?.target_receipt;
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">03 / Цель ROSMASTER</p><h2>Ведущая SO-101 → рука ROSMASTER</h2></div><span class="badge ${active ? "warn" : "neutral"}">${esc(labels[status.state] || "Получаем состояние")}</span></div><div class="panel-body"><p class="form-description">Профиль ARM_ONLY. Pi читает измерения ведущей руки и отправляет геометрические команды непосредственно Nano. Ведомая SO-101 остаётся в ожидании.</p>${!registered ? `<p class="form-description">Перед запуском нужны измеренные нули и направления суставов, общие оси и раскрытие захвата.</p>${array(status.registration?.missing).length ? `<details><summary>Каких измерений не хватает</summary><ul>${status.registration.missing.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></details>` : ""}` : ""}<div class="teleop-actions"><button class="button primary" data-action="direct03-start" type="button" ${registered && status.peer_configured && !active && !direct03.busy ? "" : "disabled"}>Наблюдаю сцену · включить контур</button><button class="button secondary" data-action="direct03-recenter" type="button" ${active && !direct03.busy && !direct03.held ? "" : "disabled"}>Текущая поза — начало</button><button class="button danger" data-action="direct03-stop" type="button">STOP ROSMASTER</button></div><button class="button secondary full" id="direct03-held" type="button" style="touch-action:none;user-select:none" ${active && !direct03.busy ? "" : "disabled"}>Удерживайте для передачи движений</button><p class="form-hint">Отпускание кнопки или уход из окна завершает попытку. Heartbeat сохраняет прежнюю lease исходного исполнителя. Старт и смена начала сами не посылают движение.</p>${receipt ? `<dl class="summary-list">${kv("Последний ответ Nano", receipt.state || "—")}${kv("Доставка на приводы", receipt.dispatch_confirmed === true ? "Подтверждена источником" : "Не подтверждена: ответ API")}</dl>` : ""}${status.run_id ? `<p class="form-hint">Журнал попытки: ${esc(status.run_id)}. Физический успех отмечается отдельно.</p>` : ""}${direct03.error || status.error ? `<p class="form-error" role="alert">${esc(direct03.error || status.error)}</p>` : ""}</div></section>`;
}
async function pollDirect03() {
  if (!direct03Route() || direct03.polling || direct03.busy || document.hidden) return;
  direct03.polling = true;
  try {
    const status = await api("/api/retarget/status");
    const signature = JSON.stringify([status.state, status.control_epoch, status.registration?.registration_sha256, status.registration?.missing, status.error, status.last_result?.target_receipt?.state]);
    direct03.status = status; direct03.error = null;
    if (signature !== direct03.signature && !direct03.held) { direct03.signature = signature; model.forceRender = true; render(); }
  } catch (error) {
    if (direct03.error !== error.message) { direct03.error = error.message; if (!direct03.held) { model.forceRender = true; render(); } }
  } finally { direct03.polling = false; }
}
async function direct03Stop(quiet = true) {
  direct03.held = false; direct03.interrupt += 1;
  try { direct03.status = await post("/api/retarget/stop", {}, { keepalive: true }); direct03.error = null; if (!quiet) toast("Прямой контур завершён. Физическая остановка проверяется по наблюдению."); }
  catch (error) { direct03.error = `Ответ на STOP не получен: ${error.message}`; if (!quiet) toast(direct03.error, true); }
  if (direct03Route()) { model.forceRender = true; render(); }
}
async function direct03Lease() {
  if (!direct03.held || direct03.leasePending || !direct03Route() || document.hidden || !document.hasFocus()) return;
  direct03.leasePending = true;
  const epoch = direct03.status?.control_epoch;
  try {
    const result = await post("/api/retarget/lease", { control_epoch: epoch, sequence: direct03.sequence++, held: true, observing: true });
    if (direct03.held && direct03.status?.control_epoch === epoch) direct03.status = result;
  } catch (error) { direct03.error = error.message; await direct03Stop(); }
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
  try {
    const result = await post(`/api/retarget/${action === "direct03-start" ? "start" : "recenter"}`, { expected_control_epoch: direct03.status?.control_epoch, observing: true });
    if (interrupt !== direct03.interrupt || !direct03Route() || document.hidden || !document.hasFocus()) { await direct03Stop(); return; }
    direct03.status = result; direct03.sequence = 0; direct03.error = null;
    if (result.state === "ACTIVE") toast("Начало принято. Для передачи движений удерживайте кнопку.");
  } catch (error) { direct03.error = error.message; toast(error.message, true); }
  finally { direct03.busy = false; model.forceRender = true; render(); }
});
document.addEventListener("pointerdown", (event) => {
  const button = event.target.closest("#direct03-held");
  if (!button || button.disabled || !direct03Route() || direct03.status?.state !== "ACTIVE") return;
  event.preventDefault(); direct03.held = true; direct03Lease();
});
function releaseDirect03() { if (direct03.held) direct03Stop(); }
document.addEventListener("pointerup", releaseDirect03);
document.addEventListener("pointercancel", releaseDirect03);
window.addEventListener("blur", () => { if (direct03.held || direct03.busy || direct03.status?.state === "ACTIVE") direct03Stop(); });
window.addEventListener("pagehide", releaseDirect03);
window.addEventListener("popstate", () => { if (direct03.status?.state === "ACTIVE") direct03Stop(); });
document.addEventListener("visibilitychange", () => { if (document.hidden && (direct03.held || direct03.busy || direct03.status?.state === "ACTIVE")) direct03Stop(); });
document.addEventListener("click", (event) => { if (event.target.closest("a[data-link]") && direct03.status?.state === "ACTIVE") direct03Stop(); });
setInterval(direct03Lease, 150);
setInterval(pollDirect03, 650);
