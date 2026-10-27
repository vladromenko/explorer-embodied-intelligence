"use strict";

// ENGINEERING_GLUE: real station measurements, recordings and explicit import lineage.
const stationLab = { status: null, recordings: [], error: null, busy: false, polling: false, updatedAt: 0, owned: false, present: false, name: "Первый показ SO-101", manifest: "", skill: "", importJob: "", lastEpisode: null, signature: "", interruptEpoch: 0, pendingControl: false, rolloutJob: "" };
const stationRoute = (route = window.location.pathname) => ["/learning/so101", "/experiments/03", TRANSFER_ROUTE, SO101_SOURCE_ROUTE].includes(route.replace(/\/$/, ""));
const stationSourceOnlyRoute = (route = window.location.pathname) => ["/learning/so101", SO101_SOURCE_ROUTE].includes(route.replace(/\/$/, ""));
const stationProject = () => activeProject()?.id || SO101_SOURCE_PROJECT;
const stationFinished = (job) => ["completed", "complete", "succeeded", "success"].includes(job.status);
function stationImports(project) {
  return model.jobs.filter((job) => job.kind === "station_import" && stationFinished(job) && typeof job.result?.dataset_manifest === "string" && job.result.dataset_manifest && job.result.manifest_exists !== false).filter((job) => {
    const episode = stationLab.recordings.find((item) => item.episode_id === (job.config?.episode_id || job.result?.episode_id));
    return projectId(episode?.project_id || job.project_id) === projectId(project);
  });
}
function stationSkills() {
  return model.skills.filter((skill) => {
    if (typeof isSimulationOnlyArtifact === "function" && isSimulationOnlyArtifact(skill)) return false;
    const job = model.jobs.find((item) => item.id === skill.lineage?.job_id);
    if (!job || job.kind !== "train_act" || !stationFinished(job)) return false;
    const expected = array(stationLab.status?.action_names);
    const names = array(skill.action_contract?.names);
    return names.length === 6 && expected.length === 6 && names.every((name, index) => name === expected[index]) && (job?.result?.trained === true || skill.status === "trained_offline_only") && (job?.result?.feature_dimension === undefined || job.result.feature_dimension === 6);
  });
}
function stationFresh() {
  const status = stationLab.status; const measured = status?.measurements;
  return status?.available === true && typeof status.control_epoch === "string" && status.control_epoch.length > 0 && Date.now() - stationLab.updatedAt < 3500 && ["leader", "follower"].every((role) => array(measured?.[role]).length === 6 && measured[role].every((value) => Number.isFinite(value))) && (number(status.measurement_age_ms) === null || status.measurement_age_ms < 3000);
}
function stationMode() { return { idle: "Руки в ожидании", teleoperation: "Повторяем движения ведущей руки", policy: "Выполняется модель" }[stationLab.status?.mode] || "Получаем состояние рук"; }
function stationValue(value) { return number(value) === null ? "—" : Number(value).toFixed(1); }
function stationMeasurements(leaderOnly = false) {
  const data = stationLab.status?.measurements || {};
  const labels = ["Поворот плеча", "Подъём плеча", "Локоть", "Наклон запястья", "Поворот кисти", "Захват"];
  return `<div class="station-measurements ${leaderOnly ? "leader-only" : ""}"><div class="station-joint-row station-joint-head"><span>Положение</span><span>Ведущая</span>${leaderOnly ? "" : "<span>Ведомая</span>"}</div>${labels.map((label, index) => `<div class="station-joint-row"><span>${label}</span><output data-station-joint="leader-${index}">${stationValue(data.leader?.[index])}</output>${leaderOnly ? "" : `<output data-station-joint="follower-${index}">${stationValue(data.follower?.[index])}</output>`}</div>`).join("")}</div><p class="form-hint">Измеренные координаты калибровки: суставы −100…100, захват 0…100. Это не градусы и не сила удержания.</p>`;
}
function stationControlPanel(project) {
  const status = stationLab.status || {}; const moving = status.mode === "teleoperation"; const recording = status.recording;
  const ready = stationFresh(); const blocked = stationLab.busy;
  return `<section class="panel station-control"><div class="panel-heading"><div><p class="eyebrow">01 / Показ движением</p><h2>Ведущая → ведомая</h2></div>${badge(ready ? moving ? "running" : "ready" : "offline")}</div><div class="panel-body"><p class="form-description">Двигайте ведущую руку. Ведомая повторяет изменения относительно позы при включении. USB остаётся на Raspberry Pi.</p><div class="station-presence"><button class="button primary full" data-action="station-start" type="button" ${ready && !blocked && !moving ? "" : "disabled"}>${icon("play")}Я рядом · просто управлять</button><p>Нажатие подтверждает, что вы сейчас рядом и наблюдаете за руками.</p></div><div class="teleop-actions"><button class="button secondary" data-action="station-recenter" type="button" ${ready && moving && stationLab.present && !blocked ? "" : "disabled"}>Считать текущую позу началом</button><button class="button danger" data-action="station-stop" type="button">${icon("cancel")}STOP рук</button></div><div class="station-live-status" role="status"><span class="status-dot ${ready ? "good" : "muted"}"></span><span id="station-mode">${esc(stationMode())}</span></div><form id="station-record-form"><div class="form-field"><label for="station-name">Что показываем</label><input id="station-name" maxlength="80" value="${esc(stationLab.name)}" placeholder="Например, поднести кубик" required></div>${recording ? `<div class="station-record-active"><span class="status-dot bad"></span><strong>Идёт запись движений и камеры</strong><span>${esc(recording.name)}</span></div><p class="form-hint">Закончите показ и отметьте результат. Ваша отметка сохраняется как оценка оператора.</p><div class="episode-outcomes"><button type="button" class="button secondary" data-station-outcome="success" ${blocked ? "disabled" : ""}>Получилось</button><button type="button" class="button secondary" data-station-outcome="failure" ${blocked ? "disabled" : ""}>Не получилось</button><button type="button" class="button ghost" data-station-outcome="unknown" ${blocked ? "disabled" : ""}>Без оценки</button></div>` : `<button class="button secondary full" type="submit" ${ready && moving && stationLab.present && !blocked ? "" : "disabled"}>${icon("record")}Начать запись показа</button>`}</form>${stationLab.error || status.error ? `<p class="form-error" role="alert">${esc(stationLab.error || text(status.error))}</p>` : ""}<p class="form-hint">При уходе из окна движение останавливается. Для продолжения снова нажмите «Я рядом».</p></div></section>`;
}
function stationTrainingPanel(project) {
  const imports = stationImports(project);
  if (!imports.some((job) => job.result.dataset_manifest === stationLab.manifest)) stationLab.manifest = imports[0]?.result.dataset_manifest || "";
  const importJobs = model.jobs.filter((job) => job.kind === "station_import" && (job.id === stationLab.importJob || projectId(job.project_id) === projectId(project) || stationLab.recordings.some((entry) => projectId(entry.project_id) === projectId(project) && entry.episode_id === job.config?.episode_id)));
  const training = model.jobs.filter((job) => job.kind === "train_act" && projectId(job.project_id) === projectId(project));
  const activeTraining = training.some((job) => !TERMINAL_JOBS.includes(job.status));
  return `<section class="panel"><div class="panel-heading"><div><p class="eyebrow">02 / Обучение по записи</p><h2>Сохранить опыт в модели</h2></div>${badge(imports.length ? "ready" : "awaiting_evidence")}</div><div class="panel-body"><p class="form-description">После завершения показ переносится на Homebrain. Обучение использует синхронизированные состояния, действия и кадры, а не отдельное видео наблюдения.</p>${imports.length ? `<div class="form-field"><label for="station-dataset">Готовая запись для обучения</label><select id="station-dataset">${imports.map((job) => `<option value="${esc(job.result.dataset_manifest)}" ${job.result.dataset_manifest === stationLab.manifest ? "selected" : ""}>${esc(stationLab.recordings.find((entry) => entry.episode_id === job.config?.episode_id)?.name || "Показ SO-101")} · ${esc(dateTime(job.created_at || job.created))}</option>`).join("")}</select></div>` : notice("Ждём готовый набор данных", importJobs.some((job) => !TERMINAL_JOBS.includes(job.status)) ? "Показ переносится и проверяется. Кнопка обучения появится после завершения переноса." : "Запишите движения и завершите показ. Его перенос будет запущен автоматически.")}<button class="button primary full" data-action="station-train" type="button" ${stationLab.manifest && !activeTraining && !stationLab.busy ? "" : "disabled"}>${icon("chip")}Обучить ACT · 120 шагов</button><p class="form-hint">Короткое обучение на Homebrain. Завершённый запуск ещё не означает, что навык проверен на предметах.</p></div>${jobRows([...training, ...importJobs].slice(0, 5), "Здесь появятся фактические состояния переноса и обучения.")}</section>`;
}
function stationEvaluationPanel(project) {
  const skills = stationSkills();
  if (!skills.some((skill) => skill.id === stationLab.skill)) stationLab.skill = skills[0]?.id || "";
  const selected = skills.find((skill) => skill.id === stationLab.skill);
  const training = model.jobs.find((job) => job.id === selected?.lineage?.job_id);
  const evaluation = training?.result?.evaluation || selected?.evaluation;
  const enabled = model.state?.capabilities?.station_rollout === true && selected && stationFresh() && stationLab.present;
  const reason = !selected ? "Сначала завершите обучение модели по показу." : model.state?.capabilities?.station_rollout !== true ? "Выполнение модели на руках ещё не предоставлено станцией." : !stationLab.present ? "Перед выполнением подтвердите присутствие кнопкой «Я рядом»." : !stationFresh() ? "Ждём свежие измерения обеих рук." : "Десятисекундная попытка будет записана как отдельный запуск.";
  return `<section class="panel"><div class="panel-heading"><div><p class="eyebrow">03–04 / Проверка и попытка</p><h2>Что умеет модель</h2></div>${badge(selected ? "trained_offline_only" : "not_started")}</div><div class="panel-body">${skills.length ? `<div class="form-field"><label for="station-skill">Модель с шестью координатами SO-101</label><select id="station-skill">${skills.map((skill) => `<option value="${esc(skill.id)}" ${skill.id === stationLab.skill ? "selected" : ""}>${esc(skill.task || skill.name || skill.id)}</option>`).join("")}</select></div>` : empty("Модель ещё не обучена", "После обучения здесь появятся сохранённая модель и фактические результаты проверки.", "skills")}${selected ? `<dl class="skill-metadata">${kv("Проверка сохранения модели", training?.result?.save_reload_passed === true ? "Пройдена" : "Не подтверждена")}${kv("Изменение весов", training?.result?.weights_changed === true ? "Подтверждено" : "Не подтверждено")}${kv("Проверка на отдельных эпизодах", evaluation?.independent_episode_split === true && evaluation?.available === true ? "Есть отдельные проверочные записи" : "Отдельные проверочные записи пока отсутствуют")}${kv("Физический успех задачи", "Не подтверждён обучением")}</dl>` : ""}<p class="form-description">Ошибка на записанных данных помогает оценить модель. Успешное выполнение предметной задачи проверяется отдельно на руках.</p><button class="button secondary full" type="button" data-action="station-rollout" ${enabled && !stationLab.busy ? "" : "disabled"}>${icon("play")}Пробовать модель · 10 секунд</button><p class="form-hint">${esc(reason)}</p></div></section>`;
}
function stationRecordingPanel(project) {
  const entries = stationLab.recordings.filter((entry) => projectId(entry.project_id) === projectId(project));
  return `<section class="panel"><div class="panel-heading"><div><h2>Мои показы</h2><p>Движения, камера и оценка оператора</p></div><span class="badge neutral">${count(entries.length)}</span></div><div class="panel-body">${entries.length ? `<div class="recording-list">${entries.slice(0, 8).map((entry) => `<article class="recording-row"><div><h3>${esc(entry.name || "Показ SO-101")}</h3><p>${esc(dateTime(entry.started_wall_utc))} · ${esc(entry.label_source === "automated_engineering_test" ? "Технический тест" : { success: "Оператор: получилось", failure: "Оператор: не получилось", unknown: "Без оценки оператора" }[entry.outcome] || "Идёт запись")}</p><div class="recording-label">Измеренное состояние · 6 действий · камера</div></div>${badge(entry.state === "complete" ? "completed" : entry.state)}</article>`).join("")}</div>` : empty("Показы ещё впереди", "Включите повторение движений и запишите один короткий показ.", "record")}</div></section>`;
}
function stationPage(project = SO101_SOURCE_PROJECT) {
  const personal = ["personal", SO101_SOURCE_PROJECT].includes(project); const experiment = PROJECTS.find((item) => item.id === project);
  if (personal) return heading("Две руки SO101 · показать и обучить", "Одна Logitech на Raspberry Pi. Подтверждённый навык и условия выбираются до отдельного наблюдаемого управления.", `Исследовательский опыт / ${SO101_SOURCE_PROJECT}`) +
    `<div id="source-skills-slot">${labSkillsPanel(labSkills.snapshot())}</div><div class="overview-grid station-workspace"><div><div id="camera-slot"></div><section class="panel spaced-section"><div class="panel-heading"><h2>Исходные измерения рук</h2></div><div class="panel-body">${stationMeasurements()}<p class="form-hint">Источник — Raspberry Pi · текущие положения ведущей и ведомой рук.</p></div></section></div><div id="source-demonstration-slot">${sourceDemonstrationPanel(sourceDemonstration.snapshot(),{phaseTitle:labSkillPhaseTitle})}</div></div>` +
    labVisionPanel(labVision.snapshot()) + tabletopCalibrationPanel(tabletopCalibration.snapshot()) + `<div id="source-evaluation-slot">${sourceEvaluationPanel(sourceEvaluation.snapshot())}</div>`;
  if (project === "03") return experiment02Page(experiment);
  if (project === TRANSFER_PROJECT) return heading("Перенос обученного навыка", `Выберите уже обученную исходную модель. Прямая телеманипуляция находится в опыте 03, обучение follower — в опыте ${SO101_SOURCE_PROJECT}.`, `Исследовательский опыт / ${TRANSFER_PROJECT}`) +
    policyTransferPanel() + notice("Разные embodiment", "Перед исполнением нужны подтверждённая геометрия, соответствие команд и независимая проверка. Inference исходной модели сам по себе не подтверждает перенос навыка.") +
    `<p><a href="${SO101_SOURCE_ROUTE}" data-link>Открыть опыт ${SO101_SOURCE_PROJECT} · две руки LeRobot</a> · <a href="/experiments/03" data-link>Открыть прямое управление ROSMASTER</a></p>` + jobsPanel(model.jobs.filter(job => job.project_id === TRANSFER_PROJECT), "Перенос и независимые проверки");
  return heading(personal ? "Две руки LeRobot · показ, обучение и повторение" : experiment.title, personal ? "Покажите движение ведущей рукой, сохраните опыт и обучите свою первую модель." : experiment.subtitle, personal ? `Исследовательский опыт / ${SO101_SOURCE_PROJECT}` : `Исследовательский опыт / ${project}`) + (project === TRANSFER_PROJECT ? policyTransferPanel() : "") + demonstrationWizardPanel(project) + `<ol class="station-steps" aria-label="Путь от показа к навыку"><li><b>01</b> Записать показ</li><li><b>02</b> Обучить модель</li><li><b>03</b> Проверить данные</li><li><b>04</b> Пробовать на руках</li></ol>${project === TRANSFER_PROJECT ? notice("Сначала исходный навык SO-101", "Здесь сохраняется и обучается исходный показ. Перенос на ROSMASTER отдельно проверяет геометрию и рабочее пространство; модель руки не выдаётся за готовый навык другого робота.") : ""}<div class="overview-grid station-workspace"><div><div id="camera-slot"></div><section class="panel spaced-section"><div class="panel-heading"><div><h2>Руки на связи</h2><p>Настоящие измерения Raspberry Pi</p></div><span id="station-rate" class="badge neutral">${number(stationLab.status?.loop_hz) === null ? "Ждём измерения" : `${Number(stationLab.status.loop_hz).toFixed(1)} Гц`}</span></div><div class="panel-body">${stationMeasurements()}<p id="station-measured-at" class="form-hint">${stationLab.updatedAt ? `Получено ${clockTime(stationLab.updatedAt)}` : "Получаем измерения ведущей и ведомой рук"}</p></div></section></div>${stationControlPanel(project)}</div>${personal ? labVisionPanel(labVision.snapshot()) + tabletopCalibrationPanel(tabletopCalibration.snapshot()) + sourceEvaluationPanel(sourceEvaluation.snapshot()) : ""}<div class="two-column spaced-section station-learning">${personal ? labSkillsPanel(labSkills.snapshot()) : stationTrainingPanel(project)}${stationEvaluationPanel(project)}</div><div class="spaced-section">${stationLab.lastEpisode ? notice("Последний показ сохранён", `Измерений в записи: ${count(stationLab.lastEpisode.samples)}. Оценка оператора хранится отдельно от проверки навыка.`) : ""}${stationRecordingPanel(project)}</div>`;
}
async function pollStation(extras = false) {
  if (!stationRoute() || stationLab.polling || document.hidden) return;
  stationLab.polling = true;
  try {
    const status = await api("/api/station/status");
    stationLab.status = status; stationLab.updatedAt = Date.now(); stationLab.error = null;
    if (status.mode === "idle") stationLab.owned = false;
    if (extras || !stationLab.lastRecordings || Date.now() - stationLab.lastRecordings > 5000) {
      const result = await api("/api/station/recordings"); stationLab.recordings = array(result.recordings); stationLab.lastRecordings = Date.now();
    }
    const signature = JSON.stringify([status.available, status.mode, status.error, status.recording?.episode_id, stationLab.recordings.map((entry) => [entry.episode_id, entry.state])]);
    if (signature !== stationLab.signature) { stationLab.signature = signature; render(); }
    for (const role of ["leader", "follower"]) array(status.measurements?.[role]).forEach((value, index) => { const output = main.querySelector(`[data-station-joint="${role}-${index}"]`); if (output) output.textContent = stationValue(value); });
    const rate = main.querySelector("#station-rate"); if (rate) rate.textContent = number(status.loop_hz) === null ? "Ждём измерения" : `${Number(status.loop_hz).toFixed(1)} Гц`;
    const measured = main.querySelector("#station-measured-at"); if (measured) measured.textContent = `Получено ${clockTime(stationLab.updatedAt)}${number(status.measurement_age_ms) !== null ? ` · задержка ${Number(status.measurement_age_ms).toFixed(0)} мс` : ""}`;
  } catch (error) { const changed = stationLab.error !== error.message || stationLab.updatedAt !== 0; stationLab.error = error.message; stationLab.updatedAt = 0; if (changed) render(); }
  finally { stationLab.polling = false; }
}
async function stopStation(quiet = false, { explicitUserGlobalStop = false } = {}) {
  if (stationSourceOnlyRoute() && !explicitUserGlobalStop) {
    stationLab.present=false;stationLab.owned=false;stationLab.pendingControl=false;
    return {ignored:true,reason:"SOURCE04_USES_OWNED_OBSERVED_SESSION"};
  }
  stationLab.interruptEpoch += 1;
  stationLab.present = false; stationLab.owned = false;
  if (stationLab.rolloutJob) { const id = stationLab.rolloutJob; stationLab.rolloutJob = ""; post(`/api/jobs/${encodeURIComponent(id)}/cancel`, {}, { keepalive: true }).catch(() => {}); }
  try { stationLab.status = await post("/api/station/stop", {}, { keepalive: true }); if (!quiet) toast("Движение рук остановлено. Показ можно завершить и сохранить."); }
  catch (error) { stationLab.error = `STOP рук не подтверждён: ${error.message}`; if (!quiet) toast(stationLab.error, true); }
  if (stationRoute()) { model.forceRender = true; render(); }
}
async function stationOperation(operation, body, success) {
  if (stationSourceOnlyRoute()) throw new Error("Для04 используйте подтверждённый SourceDemo flow");
  if (stationLab.busy) return;
  stationLab.busy = true;
  try { const result = await post(`/api/station/${operation}`, body); if (result.mode) stationLab.status = result; if (success) toast(success); return result; }
  catch (error) { stationLab.error = error.message; toast(error.message, true); throw error; }
  finally { stationLab.busy = false; model.forceRender = true; await pollStation(true); render(); }
}
document.addEventListener("input", (event) => { if (event.target.id === "station-name") stationLab.name = event.target.value; });
document.addEventListener("change", (event) => { if (event.target.id === "station-dataset") stationLab.manifest = event.target.value; if (event.target.id === "station-skill") { stationLab.skill = event.target.value; model.forceRender = true; render(); } });
document.addEventListener("submit", async (event) => {
  if (stationSourceOnlyRoute()) return;
  if (event.target.id !== "station-record-form") return;
  event.preventDefault();
  if (stationLab.busy) return;
  if (!stationFresh() || !stationLab.present || stationLab.status?.mode !== "teleoperation") { toast("Сначала включите повторение движений, находясь рядом с руками.", true); return; }
  if (!stationLab.skillContext) { toast("Сначала выберите подтверждённый навык, вариант и отдельную сессию в мастере показов.",true); return; }
  try { await stationOperation("record/start", { ...stationLab.skillContext, name: stationLab.name.trim() || stationLab.skillContext.name, project_id: stationProject() }, "Записываем измеренные движения, команды и камеру выбранной версии навыка."); } catch (_) { /* Displayed by stationOperation. */ }
});
document.addEventListener("click", async (event) => {
  if (stationSourceOnlyRoute()) return;
  const button = event.target.closest("button"); if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (stationLab.busy && (button.dataset.stationOutcome || (action?.startsWith("station-") && action !== "station-stop"))) return;
  if (["station-start", "station-recenter"].includes(action) || button.dataset.stationOutcome) button.disabled = true;
  try {
    if (action === "station-stop") { await stopStation(); return; }
    if (action === "station-start") {
      if (!stationFresh()) throw new Error("Ждём связь и свежие измерения рук.");
      const epoch = stationLab.interruptEpoch; stationLab.pendingControl = true;
      try {
        await stationOperation("teleop/start", { observing: true, operator_present: true, expected_control_epoch: stationLab.status.control_epoch });
        if (epoch !== stationLab.interruptEpoch || document.hidden || !document.hasFocus()) { await stopStation(true); return; }
        stationLab.present = true; stationLab.owned = true; toast("Повторение движений включено относительно текущей позы.");
      } finally { stationLab.pendingControl = false; model.forceRender = true; render(); }
      return;
    }
    if (action === "station-recenter") { if (!stationLab.present || !stationFresh()) throw new Error("Сначала подтвердите присутствие рядом с подключёнными руками."); await stationOperation("teleop/recenter", { observing: true, operator_present: true, expected_control_epoch: stationLab.status.control_epoch }, "Текущие положения приняты за начало движения."); return; }
    if (button.dataset.stationOutcome) { const result = await stationOperation("record/finish", { outcome: button.dataset.stationOutcome }, "Показ сохранён. Переносим данные на Homebrain."); stationLab.lastEpisode = result; stationLab.importJob = result?.import_job?.id || ""; await stopStation(true); await refresh(true); return; }
    if (action === "station-train") {
      const imported = stationImports(stationProject()).find((job) => job.result.dataset_manifest === stationLab.manifest); if (!imported) throw new Error("Сначала дождитесь завершения переноса выбранного показа.");
      if (stationLab.busy) return; stationLab.busy = true; button.disabled = true;
      try { await post("/api/jobs", { kind: "train_act", project_id: stationProject(), config: { dataset_manifest: imported.result.dataset_manifest, act_steps: 120, device: "cpu" } }); toast("Обучение ACT поставлено в очередь на Homebrain."); await refresh(true); }
      finally { stationLab.busy = false; model.forceRender = true; render(); }
      return;
    }
    if (action === "station-rollout") {
      if (model.state?.capabilities?.station_rollout !== true || !stationFresh() || !stationLab.present || !stationSkills().some((skill) => skill.id === stationLab.skill)) throw new Error("Выполнение этой модели пока недоступно.");
      const epoch = stationLab.interruptEpoch; stationLab.pendingControl = true; button.disabled = true;
      try {
        const job = await post("/api/jobs", { kind: "station_rollout", project_id: stationProject(), config: { skill_id: stationLab.skill, duration_s: 10, observing: true, operator_present: true, expected_control_epoch: stationLab.status.control_epoch } }); stationLab.rolloutJob = job.id;
        if (epoch !== stationLab.interruptEpoch || document.hidden || !document.hasFocus()) { await stopStation(true); return; }
        stationLab.owned = true; toast("Десятисекундная попытка поставлена в очередь."); await refresh(true);
      } finally { stationLab.pendingControl = false; }
    }
  } catch (error) { stationLab.error = error.message; toast(error.message, true); model.forceRender = true; render(); }
});
function stationInterrupt() { stationLab.interruptEpoch += 1; if(stationSourceOnlyRoute()){stationLab.present=false;stationLab.owned=false;stationLab.pendingControl=false;return;} if (stationLab.owned || stationLab.present || stationLab.pendingControl) stopStation(true); }
window.addEventListener("blur", stationInterrupt);
window.addEventListener("pagehide", stationInterrupt);
window.addEventListener("popstate", stationInterrupt);
document.addEventListener("visibilitychange", () => { if (document.hidden) stationInterrupt(); else pollStation(true); });
setInterval(() => { if (!document.hidden && stationRoute()) pollStation(); }, 650);
