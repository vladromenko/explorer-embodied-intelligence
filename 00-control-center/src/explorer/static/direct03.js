"use strict";

// ENGINEERING_GLUE: explicit operator-held direct Pi -> Nano, ARM_ONLY.
const direct03 = { status: null, error: null, polling: false, busy: false, held: false, leasePending: false, sequence: 0, interrupt: 0, signature: "", recordings: [], recordId: "", recordName: "Показ SO-101 → ROSMASTER", recordBusy: false };
const direct03Route = () => window.location.pathname.replace(/\/$/, "") === "/experiments/03";
function direct03RecordingPanel() {
  const recording = direct03.status?.recording;
  return `<section class="panel spaced-section"><div class="panel-heading"><h2>Запись прямого показа</h2><span class="badge neutral">Исходные наблюдения</span></div><div class="panel-body"><p class="form-description">Сохраняются измерения ведущей руки, обе cached-камеры, геометрия и настоящие ответы Nano. Полного причинного 9D-контракта пока нет: импорт остаётся материалом для анализа.</p><form id="direct03-record-form"><div class="form-field"><label for="direct03-record-name">Название показа</label><input id="direct03-record-name" maxlength="120" value="${esc(direct03.recordName)}" required></div><button class="button secondary" type="submit" ${direct03.status?.state === "ACTIVE" && !recording?.active && !direct03.recordBusy ? "" : "disabled"}>Начать запись показа</button></form>${direct03.recordId ? `<p>Мой показ: ${esc(direct03.recordId)}</p><div class="teleop-actions">${[["success","Получилось"],["failure","Не получилось"],["unknown","Без оценки"]].map(([value,label]) => `<button class="button secondary" type="button" data-direct03-outcome="${value}" ${direct03.recordBusy ? "disabled" : ""}>${label}</button>`).join("")}</div>` : recording?.active ? `<p class="form-hint">Есть активная запись другой вкладки. Её ID не заменяет ID вашего показа.</p>` : ""}<p class="form-hint">Оценка сохраняется как отметка оператора. Успех не превращает отсутствующие состояния или команды базы в учебные цели.</p>${direct03.recordings.slice(0,6).map((entry) => `<article class="recording-row"><div><h3>${esc(entry.name)}</h3><p>${esc(entry.recording_id)} · ${count(entry.samples_written)} измерений · ${esc(entry.state)}</p><p class="form-hint">${esc(entry.outcome)} · только анализ · исходные JPEG и clocks сохранены</p></div>${entry.archive_sha256 ? `<button class="button small secondary" type="button" data-direct03-import="${esc(entry.recording_id)}" data-archive-sha="${esc(entry.archive_sha256)}">Импортировать</button>` : ""}</article>`).join("")}</div></section>`;
}
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
    const signature = JSON.stringify([status.state, status.control_epoch, status.registration?.registration_sha256, status.registration?.missing, status.error, status.last_result?.target_receipt?.state, status.recording, direct03.recordings.map((entry) => [entry.recording_id, entry.state, entry.archive_sha256])]);
    direct03.status = status; direct03.error = null;
    if (!direct03.lastRecordsAt || Date.now() - direct03.lastRecordsAt > 3000) { const catalog = await api("/api/retarget/recordings"); direct03.recordings = array(catalog.recordings); direct03.lastRecordsAt = Date.now(); }
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

document.addEventListener("input", (event) => { if (event.target.id === "direct03-record-name") direct03.recordName = event.target.value; });
document.addEventListener("submit", async (event) => {
  if (event.target.id !== "direct03-record-form") return;
  event.preventDefault(); if (direct03.recordBusy) return;
  direct03.recordBusy = true;
  try { const result = await post("/api/retarget/record/start", { expected_control_epoch: direct03.status?.control_epoch, observing: true, name: direct03.recordName }); direct03.recordId = result.recording_id; }
  catch (error) { toast(error.message, true); }
  finally { direct03.recordBusy = false; model.forceRender = true; render(); }
});
document.addEventListener("click", async (event) => {
  const finish = event.target.closest("button[data-direct03-outcome]");
  const imported = event.target.closest("button[data-direct03-import]");
  if ((!finish && !imported) || direct03.recordBusy) return;
  direct03.recordBusy = true;
  try {
    if (finish && direct03.recordId) { await post("/api/retarget/record/finish", { recording_id: direct03.recordId, outcome: finish.dataset.direct03Outcome }); direct03.recordId = ""; toast("Исход показа сохранён; архив упаковывается отдельно."); }
    if (imported) { await post("/api/retarget/import", { recording_id: imported.dataset.direct03Import, archive_sha256: imported.dataset.archiveSha }); toast("Импорт исходных наблюдений поставлен в очередь."); }
    direct03.lastRecordsAt = 0;
  } catch (error) { toast(error.message, true); }
  finally { direct03.recordBusy = false; model.forceRender = true; render(); }
});
