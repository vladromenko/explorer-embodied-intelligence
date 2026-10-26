"use strict";
// ENGINEERING_GLUE: explicit jobs and their retained outcomes, with no motor API.
const workflow = { task: "candy_to_tray", sources: [], source: "", sourceError: "", loading: false };
const workflowTask = (task) => ({ candy_to_tray: "Конфета → лоток", sock_to_basket: "Носок → корзина" }[task] || task);
function manipulationPanel() {
  const jobs = model.jobs.filter((job) => ["manipulation", "manipulation_student"].includes(job.kind));
  const busy = jobs.some((job) => !TERMINAL_JOBS.includes(job.status));
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">01 / Предметные задачи</p><h2>Захватить, перенести и отпустить</h2></div><span class="badge neutral">MuJoCo · неподвижная база</span></div><div class="panel-body"><p class="form-description">Проверка требует контакта, подъёма, переноса над ёмкостью, отпускания и устойчивого размещения. Носок моделируется деформируемым телом. Полный маршрут по кухне ещё не реализован.</p><div class="form-field"><label for="manipulation-task">Задача</label><select id="manipulation-task">${["candy_to_tray", "sock_to_basket"].map((task) => `<option value="${task}" ${workflow.task === task ? "selected" : ""}>${workflowTask(task)}</option>`).join("")}</select></div><div class="teleop-actions"><button type="button" class="button primary" data-workflow="manipulation" ${busy ? "disabled" : ""}>Обучить PPO · 1024 шага</button><button type="button" class="button secondary" data-workflow="manipulation_student" ${busy ? "disabled" : ""}>Обучить визуальный ACT</button></div><p class="form-hint">PPO использует состояние симуляции. ACT учится на отдельно обозначенных показах скриптового IK и исполняется по изображению и истории команд. Успех скрипта сохраняется отдельно от успеха обученной модели. Фактическое число сред обучения — 1.</p>${jobs.filter(simulationFinished).slice(0, 4).map((job) => { const result = job.result || {}; return `<article class="recording-row"><div><h3>${esc(workflowTask(result.task || job.config.task))} · ${esc(jobTitle(job))}</h3><p>${esc(job.id)} · ${esc(simulationRatio(result.evaluation || result.student?.evaluation))} полных успешных попыток обученной модели</p><p class="form-hint">${job.kind === "manipulation" ? `Обновлений PPO: ${count(result.teacher?.actual_optimizer_steps)}. Скриптовый baseline: ${result.scripted_baseline?.evaluator?.success === true ? "успешен" : "без подтверждённого успеха"}.` : "Источник показов и результаты самостоятельного исполнения сохранены в задании."} Физическое исполнение не проверялось.</p></div></article>`; }).join("")}</div>${jobRows(jobs.slice(0, 5))}</section>`;
}
async function loadTransferSources() {
  if (workflow.loading) return;
  workflow.loading = true;
  try { workflow.sources = array((await api("/api/transfer/sources")).sources); workflow.sourceError = ""; }
  catch (error) { workflow.sourceError = error.message; }
  finally { workflow.loading = false; model.forceRender = true; render(); }
}
function policyTransferPanel() {
  if (!workflow.sources.some((source) => source.job_id === workflow.source)) workflow.source = workflow.sources[0]?.job_id || "";
  const jobs = model.jobs.filter((job) => job.kind === "policy_transfer");
  const busy = jobs.some((job) => !TERMINAL_JOBS.includes(job.status));
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">04 / Исполнение исходной модели</p><h2>ACT → геометрия → независимая физика</h2></div><span class="badge neutral">Виртуальная проверка</span></div><div class="panel-body"><p class="form-description">Выберите реально обученную модель SO-101. Её сохранённые процессоры обрабатывают исходные кадры; предсказания проходят FK, регистрацию относительно объекта и IK ROSMASTER. Целевая рука исполняет их в MuJoCo.</p><button class="quiet-button text-link" type="button" data-workflow="sources">Обновить доступные модели</button>${workflow.sources.length ? `<div class="form-field"><label for="transfer-source">Завершённое обучение SO-101</label><select id="transfer-source">${workflow.sources.map((source) => `<option value="${esc(source.job_id)}" ${source.job_id === workflow.source ? "selected" : ""}>${esc(source.task || source.job_id)} · ${esc(dateTime(source.created))}</option>`).join("")}</select></div>` : `<p class="form-hint">${esc(workflow.sourceError || "Сначала завершите обучение шестикоординатной ACT по записи SO-101 и обновите список.")}</p>`}<button type="button" class="button primary" data-workflow="policy_transfer" ${!workflow.source || busy ? "disabled" : ""}>Выполнить перенос предсказаний</button><p class="form-hint">Для физических исходных записей требуются измеренные знаки, масштабы и нули суставов. Без них ACT выполняется, а перенос получает статус «нет регистрации». Проверка положения инструмента не подтверждает захват или перенос предмета.</p>${jobs.filter(simulationFinished).slice(0, 3).map((job) => { const r = job.result || {}; return `<article class="recording-row"><div><h3>${r.status === "blocked_mapping" ? "Нет измеренной регистрации" : "Виртуальный перенос выполнен"}</h3><p>Предсказаний ACT: ${count(r.predicted_actions)} · целевых физических расчётов: ${count(r.independent_virtual_attempts)}</p>${array(r.mapping_problems).length ? `<p class="form-hint">${esc(r.mapping_problems.join("; "))}</p>` : ""}</div></article>`; }).join("")}</div>${jobRows(jobs.slice(0, 5))}</section>`;
}
function handoverRuntimePanel() {
  const jobs = model.jobs.filter((job) => ["handover_test", "handover_runtime_test"].includes(job.kind));
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">05 / Протокол взаимодействия</p><h2>Проверить порядок и подтверждения</h2></div></div><div class="panel-body"><p class="form-description">Программный координатор проверяет связь команд, объекта и свидетельств удержания. Есть отдельные сценарии обрыва, чужого объекта и просроченного подтверждения. При них подающая рука не получает команду отпускания.</p><div class="teleop-actions"><button class="button secondary" type="button" data-workflow="handover_test">Проверить переходы состояний</button><button class="button primary" type="button" data-workflow="handover_runtime_test">Проверить координатор и обрывы</button></div><p class="form-hint">Это проверки программных транспортов. Они не являются физической симуляцией передачи. Для реального опыта ещё нужны общая зарегистрированная зона, аппаратные receipts и независимое либо операторское подтверждение объекта.</p>${jobs.filter(simulationFinished).slice(0, 2).map((job) => `<p>${esc(jobTitle(job))}: ${job.result?.passed === true ? "все программные сценарии пройдены" : "есть непройденные сценарии"} · ${esc(job.id)}</p>`).join("")}</div>${jobRows(jobs.slice(0, 5))}</section>`;
}
document.addEventListener("change", (event) => {
  if (event.target.id === "manipulation-task") workflow.task = event.target.value;
  if (event.target.id === "transfer-source") workflow.source = event.target.value;
});
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-workflow]");
  if (!button || button.disabled) return;
  const kind = button.dataset.workflow;
  if (kind === "sources") { await loadTransferSources(); return; }
  const config = kind === "policy_transfer" ? { source_job_id: workflow.source, max_observations: 8 } :
    kind === "manipulation" ? { task: workflow.task, teacher_steps: 1024, benchmark: false } :
    kind === "manipulation_student" ? { task: workflow.task, demonstration_episodes: 3, act_steps: 120, evaluation_episodes: 2 } : {};
  button.disabled = true;
  try {
    const job = await post("/api/jobs", { kind, project_id: kind.startsWith("manipulation") ? "01" : kind === "policy_transfer" ? "04" : "05", config });
    model.jobs = [job, ...model.jobs.filter((entry) => entry.id !== job.id)];
    toast("Задание поставлено в очередь Homebrain."); await refresh(true);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
