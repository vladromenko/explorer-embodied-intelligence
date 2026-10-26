"use strict";

// ENGINEERING_GLUE: render actual simulation outcomes; enqueue CPU-only jobs on explicit clicks.
const simulationLearning = { pilot: "", dagger: "", cycle: "", expanded: false, busy: false };
const simulationFinished = (job) => ["completed", "complete", "succeeded", "success"].includes(String(job?.status || "").toLowerCase());
const simulationNumber = (value) => typeof value === "number" && Number.isFinite(value) ? value : null;

function simulationJobs(kind) { return model.jobs.filter((job) => job.kind === kind && projectId(job.project_id) === "01"); }
function simulationSelection(kind, key) {
  const jobs = simulationJobs(kind).filter(simulationFinished);
  if (!jobs.some((job) => job.id === simulationLearning[key])) simulationLearning[key] = jobs[0]?.id || "";
  return { jobs, selected: jobs.find((job) => job.id === simulationLearning[key]) };
}
function simulationRatio(evaluation) {
  const n = simulationNumber(evaluation?.n), successes = simulationNumber(evaluation?.successes);
  return n > 0 && Number.isInteger(n) && Number.isInteger(successes) && successes >= 0 && successes <= n ? `${count(successes)} / ${count(n)}` : "Нет измерения";
}
function simulationDistance(evaluation) {
  const distance = simulationNumber(evaluation?.mean_final_distance_m);
  return distance !== null && distance >= 0 ? `${(distance * 100).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} см` : "—";
}
function simulationComparison(rows) {
  return `<div class="sim-table-scroll"><table class="sim-comparison"><thead><tr><th scope="col">Участник / проверка</th><th scope="col">Успехи / попытки</th><th scope="col">Среднее расстояние до цели</th></tr></thead><tbody>${rows.map(([label, evaluation]) => `<tr><th scope="row">${esc(label)}</th><td>${esc(simulationRatio(evaluation))}</td><td>${esc(simulationDistance(evaluation))}</td></tr>`).join("")}</tbody></table></div>`;
}
function simulationOptions(jobs, selected) {
  return jobs.map((job) => `<option value="${esc(job.id)}" ${job.id === selected ? "selected" : ""}>${esc(dateTime(job.created || job.created_at))} · ${esc(job.id.slice(0, 12))}</option>`).join("");
}
function simulationPilotMetrics() {
  const { jobs, selected } = simulationSelection("sim_pilot", "pilot");
  const result = selected?.result || {};
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">01 / Измеренные результаты</p><h2>Чему научился робот в симуляции</h2></div><span class="badge neutral">Проверка для разработки</span></div><div class="panel-body">${jobs.length ? `<div class="form-field"><label for="sim-lab-pilot">Завершённый запуск обучения</label><select id="sim-lab-pilot">${simulationOptions(jobs, simulationLearning.pilot)}</select></div>${simulationComparison([["Без движения", result.no_motion_baseline], ["Учитель · состояние симуляции", result.teacher?.evaluation], ["Ученик · изображение и команды", result.student?.evaluation]])}<p class="form-hint">Показаны только результаты этого запуска. Отсутствующая проверка остаётся без измерения.</p>` : `<p class="form-description">Завершите первый запуск: здесь появятся результаты без движения, учителя и ученика по отдельности.</p>`}<p class="form-description sim-scope-note">Это результаты симуляции на проверочных сценах для разработки. Они не подтверждают исполнение задачи на оборудовании.</p></div></section>`;
}
function simulationRequest(kind) {
  if (simulationLearning.busy) throw new Error("Предыдущий запрос ещё отправляется.");
  if (!["dagger", "skill_cycle"].includes(kind)) throw new Error("Неизвестный этап симуляции.");
  if (simulationJobs(kind).some((job) => !TERMINAL_JOBS.includes(String(job.status).toLowerCase()))) throw new Error("Для этого этапа уже есть незавершённый запуск.");
  const { selected } = simulationSelection(kind === "dagger" ? "sim_pilot" : "dagger", kind === "dagger" ? "pilot" : "dagger");
  if (!selected) throw new Error(kind === "dagger" ? "Сначала завершите исходное обучение в симуляции." : "Сначала завершите DAgger.");
  if (kind === "dagger" && simulationNumber(selected.result?.dataset?.successful_teacher_episodes) === 0) throw new Error("В исходном запуске нет успешных показов учителя для дообучения.");
  return { kind, project_id: "01", config: kind === "dagger" ? { source_job_id: selected.id, initial_act_steps: 480, act_steps: 480, device: "cpu" } : { source_job_id: selected.id, adaptation_steps: 64, device: "cpu" } };
}
function simulationDaggerResults(job) {
  const result = job?.result;
  if (!result?.before || !result?.after) return '<p class="form-description">После завершения появятся реальные попытки ученика до и после обучения.</p>';
  return `${simulationComparison([["Ученик до сбора подсказок", result.before], ["Ученик после дообучения", result.after], ["Учитель · отдельные попытки", result.privileged_teacher_reference]])}<dl class="summary-list">${kv("Запросов к учителю", count(result.teacher_query_labels))}${kv("Подсказок из неудачных попыток ученика", count(result.valid_corrections_from_failed_learner_episodes))}${kv("Шагов исходного / нового обучения", `${count(result.initial_training?.optimizer_steps)} / ${count(result.aggregated_training?.optimizer_steps)}`)}</dl><p class="form-hint">Подсказки учителя сохранены отдельно от исполненных действий ученика. В этом опыте одновременно добавляются данные и шаги обучения: отдельный эффект новых данных не измерен.</p>`;
}
function simulationCycleResults(job) {
  const result = job?.result;
  if (!result?.acceptance) return '<p class="form-description">Здесь появятся решение о кандидате, исполнение двух этапов и повторная проверка сохранённой модели.</p>';
  const verdict = result.acceptance.accepted === true ? ["Принят для проверок в симуляции", "good"] : result.acceptance.accepted === false ? ["Кандидат отклонён", "warn"] : ["Решение ещё не получено", "neutral"];
  const conditions = result.acceptance.conditions;
  const labels = { candidate_min_successes: "Минимум успешных попыток", candidate_no_regression: "Сравнение с исходной моделью", incumbent_retention: "Сохранение исходной модели", composition_min_successes: "Оба этапа последовательности" };
  return `<div class="sim-cycle-verdict"><span class="badge ${verdict[1]}">${verdict[0]}</span></div>${simulationComparison([["Исходная модель · новые сцены", result.incumbent_acceptance], ["Кандидат · новые сцены", result.candidate_acceptance], ["Два последовательных этапа", result.composition], ["Исходная модель до дообучения", result.retention_before], ["Повтор исходной модели после", result.retention_after]])}${conditions ? `<dl class="summary-list">${Object.entries(labels).map(([key, label]) => kv(label, conditions[key] === true ? "Пройдено" : conditions[key] === false ? "Не пройдено" : "Нет измерения")).join("")}</dl>` : '<p class="form-hint">Полный набор измерений для решения пока отсутствует.</p>'}<p class="form-hint">Сохранённая версия модели проверяется повторным исполнением. Это не проверка забывания внутри одной сети. Физическая готовность этим циклом не устанавливается.</p>`;
}
function simulationAdvancedPanel() {
  const pilot = simulationSelection("sim_pilot", "pilot"), dagger = simulationSelection("dagger", "dagger"), cycle = simulationSelection("skill_cycle", "cycle");
  const busyDagger = simulationLearning.busy || simulationJobs("dagger").some((job) => !TERMINAL_JOBS.includes(String(job.status).toLowerCase()));
  const busyCycle = simulationLearning.busy || simulationJobs("skill_cycle").some((job) => !TERMINAL_JOBS.includes(String(job.status).toLowerCase()));
  const noExpert = simulationNumber(pilot.selected?.result?.dataset?.successful_teacher_episodes) === 0;
  return `<details class="panel sim-learning-panel spaced-section" id="simulation-advanced" ${simulationLearning.expanded ? "open" : ""}><summary><span><span class="eyebrow">Дополнительный опыт</span><strong>Подсказки учителя и библиотека навыков</strong></span><span class="badge neutral">CPU · симуляция</span></summary><div class="panel-body"><p class="form-description">Сначала соберите подсказки в собственных попытках ученика, затем проверьте поиск модели, дообучение и последовательность из двух достижений цели.</p><div class="sim-learning-sections"><section><h3>1. DAgger: обучение по подсказкам</h3><p class="form-description">Источник — выбранный выше завершённый запуск. 480 шагов исходного обучения и ещё 480 после сбора подсказок. Все проверки остаются этапом разработки.</p><button class="button secondary" type="button" data-action="sim-lab-dagger" ${!pilot.selected || noExpert || busyDagger ? "disabled" : ""}>${icon("play")}Запустить DAgger · CPU</button>${!pilot.selected ? '<p class="form-hint">Нужен завершённый исходный запуск симуляции.</p>' : noExpert ? '<p class="form-hint">В этом источнике нет успешных показов учителя.</p>' : busyDagger ? '<p class="form-hint">Предыдущий запуск ещё не завершён.</p>' : ""}${dagger.jobs.length ? `<div class="form-field sim-result-select"><label for="sim-lab-dagger">Завершённый DAgger</label><select id="sim-lab-dagger">${simulationOptions(dagger.jobs, simulationLearning.dagger)}</select></div>` : ""}${simulationDaggerResults(dagger.selected)}</section><section><h3>2. Полный цикл навыка</h3><p class="form-description">Из завершённого DAgger: сохранить и найти модель, дообучить 64 шага, исполнить два этапа без сброса физики и повторно проверить исходную модель. Кандидат может быть отклонён.</p><button class="button secondary" type="button" data-action="sim-lab-cycle" ${!dagger.selected || busyCycle ? "disabled" : ""}>${icon("play")}Проверить цикл навыка · CPU</button>${!dagger.selected ? '<p class="form-hint">Нужен завершённый DAgger с сохранёнными моделями.</p>' : busyCycle ? '<p class="form-hint">Предыдущая проверка цикла ещё не завершена.</p>' : ""}${cycle.jobs.length ? `<div class="form-field sim-result-select"><label for="sim-lab-cycle">Завершённая проверка цикла</label><select id="sim-lab-cycle">${simulationOptions(cycle.jobs, simulationLearning.cycle)}</select></div>` : ""}${simulationCycleResults(cycle.selected)}</section></div><p class="form-description sim-scope-note">Это симуляция достижения видимой цели. Закрытого итогового теста, проверки одежды и физической готовности здесь нет.</p></div></details>`;
}
function isSimulationOnlyArtifact(item) {
  const ids = [item?.lineage?.job_id, item?.lineage?.source_job_id];
  const job = model.jobs.find((entry) => ids.includes(entry.id));
  const methods = [...array(item?.method_ids), ...array(item?.lineage?.method_ids), ...array(item?.lineage?.methods)];
  return ["world_model", "dagger", "skill_cycle", "sim_pilot"].includes(item?.kind) || ["world_model", "dagger", "skill_cycle", "sim_pilot"].includes(job?.kind) || /^(world_model|sim_dagger|sim_adapted|sim_teacher)/.test(item?.source_type || "") || ["offline_one_step_observation_prediction", "one_round_dagger_fixed_base_reach", "executed_simulation_skill_cycle", "fixed_base_visible_target_reach"].includes(item?.task) || methods.some((method) => ["DAGGER_ROSS_2011", "SKILL_CYCLE_SIM_VALIDATION", "EXPLORER_CAD_MUJOCO"].includes(method));
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest('button[data-action="sim-lab-dagger"],button[data-action="sim-lab-cycle"]');
  if (!button || button.disabled) return;
  const kind = button.dataset.action === "sim-lab-dagger" ? "dagger" : "skill_cycle";
  try {
    const request = simulationRequest(kind);
    simulationLearning.busy = true; button.disabled = true;
    const job = await post("/api/jobs", request);
    model.jobs = [job, ...model.jobs.filter((entry) => entry.id !== job.id)];
    toast(kind === "dagger" ? "DAgger поставлен в очередь на CPU. Команды роботу не отправляются." : "Проверка цикла навыка поставлена в очередь на CPU.");
    await refresh(true);
  } catch (error) { toast(error.message, true); }
  finally { simulationLearning.busy = false; model.forceRender = true; render(); }
});
document.addEventListener("change", (event) => {
  const key = { "sim-lab-pilot": "pilot", "sim-lab-dagger": "dagger", "sim-lab-cycle": "cycle" }[event.target.id];
  if (key) { simulationLearning[key] = event.target.value; model.forceRender = true; render(); }
});
document.addEventListener("toggle", (event) => { if (event.target.id === "simulation-advanced") simulationLearning.expanded = event.target.open; }, true);
