"use strict";
// ENGINEERING_GLUE: explicit jobs and their retained outcomes, with no motor API.
const workflow = { task: "candy_to_tray", sources: [], source: "", sourceError: "", loading: false, handover: null };
const workflowTask = (task) => ({ candy_to_tray: "Конфета → лоток", sock_to_basket: "Носок → корзина" }[task] || task);
function manipulationPanel() {
  const jobs = model.jobs.filter((job) => ["manipulation", "manipulation_student", "manipulation_curriculum", "manipulation_benchmark", "mobile_manipulation", "manipulation_selective_repair"].includes(job.kind));
  const busy = jobs.some((job) => !TERMINAL_JOBS.includes(job.status));
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">01 / Предметные задачи</p><h2>Захватить, перенести и отпустить</h2></div><span class="badge neutral">MuJoCo · неподвижная база</span></div><div class="panel-body"><p class="form-description">Проверка требует контакта, подъёма, переноса над ёмкостью, отпускания и устойчивого размещения. Носок моделируется деформируемым телом. Полный маршрут по кухне ещё не реализован.</p><div class="form-field"><label for="manipulation-task">Задача</label><select id="manipulation-task">${["candy_to_tray", "sock_to_basket"].map((task) => `<option value="${task}" ${workflow.task === task ? "selected" : ""}>${workflowTask(task)}</option>`).join("")}</select></div><div class="teleop-actions"><button type="button" class="button primary" data-workflow="manipulation" ${busy ? "disabled" : ""}>Обучить PPO · 1024 шага</button><button type="button" class="button secondary" data-workflow="manipulation_student" ${busy ? "disabled" : ""}>Обучить визуальный ACT</button><button type="button" class="button secondary" data-workflow="manipulation_curriculum" ${busy ? "disabled" : ""}>Проверить BC → PPO</button><button type="button" class="button secondary" data-workflow="manipulation_benchmark" ${busy ? "disabled" : ""}>Измерить 1 / 2 / 4 / 8 процессов</button><button type="button" class="button secondary" data-workflow="mobile_manipulation" ${busy ? "disabled" : ""}>Конфета из кухни · скриптовая проверка</button></div><p class="form-hint">PPO использует состояние симуляции. ACT учится на отдельно обозначенных показах скриптового IK и исполняется по изображению и истории команд. Успех скрипта сохраняется отдельно от успеха обученной модели. Фактическое число сред обучения — 1.</p>${jobs.filter(simulationFinished).slice(0, 4).map(manipulationResult).join("")}</div>${jobRows(jobs.slice(0, 5))}</section>`;
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
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">04 / Исполнение исходной модели</p><h2>ACT → геометрия → независимая физика</h2></div><span class="badge neutral">Виртуальная проверка</span></div><div class="panel-body"><p class="form-description">Выберите реально обученную модель SO-101. Её сохранённые процессоры обрабатывают исходные кадры; предсказания проходят FK, регистрацию относительно объекта и IK ROSMASTER. При наличии регистрации целевая рука исполняет их в MuJoCo.</p><button class="quiet-button text-link" type="button" data-workflow="sources">Обновить доступные модели</button>${workflow.sources.length ? `<div class="form-field"><label for="transfer-source">Завершённое обучение SO-101</label><select id="transfer-source">${workflow.sources.map((source) => `<option value="${esc(source.job_id)}" ${source.job_id === workflow.source ? "selected" : ""}>${esc(source.task || source.job_id)} · ${esc(dateTime(source.created))}</option>`).join("")}</select></div>` : `<p class="form-hint">${esc(workflow.sourceError || "Сначала завершите обучение шестикоординатной ACT по записи SO-101 и обновите список.")}</p>`}<button type="button" class="button primary" data-workflow="policy_transfer" ${!workflow.source || busy ? "disabled" : ""}>Выполнить перенос предсказаний</button><p class="form-hint">Для физических исходных записей требуются измеренные знаки, масштабы и нули суставов. Без них ACT выполняется, а перенос получает статус «нет регистрации». Проверка положения инструмента не подтверждает захват или перенос предмета.</p>${jobs.filter(simulationFinished).slice(0, 3).map((job) => { const r = job.result || {}; return `<article class="recording-row"><div><h3>${r.status === "blocked_mapping" ? "Нет измеренной регистрации" : "Виртуальный перенос выполнен"}</h3><p>Предсказаний ACT: ${count(r.predicted_actions)} · целевых проверок MuJoCo: ${count(r.independent_virtual_attempts)}</p>${array(r.mapping_problems).length ? `<p class="form-hint">${esc(r.mapping_problems.join("; "))}</p>` : ""}</div></article>`; }).join("")}</div>${jobRows(jobs.slice(0, 5))}</section>`;
}
function handoverRuntimePanel() {
  const jobs = model.jobs.filter((job) => ["handover_test", "handover_runtime_test"].includes(job.kind));
  const readiness = workflow.handover;
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">05 / Протокол взаимодействия</p><h2>Проверить порядок и подтверждения</h2></div></div><div class="panel-body"><p class="form-description">Программный координатор проверяет связь команд, объекта и свидетельств удержания. Есть отдельные сценарии обрыва, чужого объекта и просроченного подтверждения. При них подающая рука не получает команду отпускания.</p><div class="teleop-actions"><button class="button secondary" type="button" data-workflow="handover_test">Проверить переходы состояний</button><button class="button primary" type="button" data-workflow="handover_runtime_test">Проверить координатор и обрывы</button><button class="button secondary" type="button" data-workflow="handover_readiness">Проверить настоящие узлы без движения</button></div>${readiness ? `<dl class="summary-list">${kv("Энкодеры ROSMASTER", readiness.nano?.measured_joint_feedback ? "Доступны" : "Не подтверждены")}${kv("Измерение силы", readiness.nano?.force_measured ? "Доступно" : "Не подтверждено")}${kv("Станция SO-101", readiness.receiver?.available ? "Доступна" : "Недоступна")}${kv("Аппаратный dispatch", "Пока не квалифицирован")}</dl><p class="form-hint">${array(readiness.blockers).map(esc).join("; ")}</p>` : ""}<p class="form-hint">Это проверки программных транспортов. Они не являются физической симуляцией передачи. Для реального опыта ещё нужны общая зарегистрированная зона, аппаратные receipts и независимое либо операторское подтверждение объекта.</p>${jobs.filter(simulationFinished).slice(0, 2).map((job) => `<p>${esc(jobTitle(job))}: ${job.result?.passed === true ? "все программные сценарии пройдены" : "есть непройденные сценарии"} · ${esc(job.id)}</p>`).join("")}</div>${jobRows(jobs.slice(0, 5))}</section>`;
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
  if (kind === "handover_readiness") {
    button.disabled = true;
    try { workflow.handover = await api("/api/handover/readiness"); model.forceRender = true; render(); }
    catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
    return;
  }
  const config = kind === "policy_transfer" ? { source_job_id: workflow.source, max_observations: 8 } :
    kind === "manipulation" ? { task: workflow.task, teacher_steps: 1024, benchmark: false } :
    kind === "manipulation_student" ? { task: workflow.task, demonstration_episodes: 3, act_steps: 120, evaluation_episodes: 2 } :
    kind === "manipulation_curriculum" ? { task: workflow.task } : kind === "manipulation_benchmark" ? { task: workflow.task, benchmark_steps: 50 } : kind === "mobile_manipulation" ? { carry_profile: "lower_downward_v2", evaluation_episodes: 1 } : {};
  button.disabled = true;
  try {
    const job = await post("/api/jobs", { kind, project_id: (kind.startsWith("manipulation") || kind === "fault_repair" || kind === "mobile_manipulation") ? "01" : kind === "policy_transfer" ? "04" : "05", config });
    model.jobs = [job, ...model.jobs.filter((entry) => entry.id !== job.id)];
    toast("Задание поставлено в очередь Homebrain."); await refresh(true);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

function faultRepairPanel() {
  const jobs = model.jobs.filter((job) => job.kind === "fault_repair");
  const latest = jobs.find(simulationFinished)?.result;
  const labels = { nominal: "Без внесённых ошибок", actuator_bias: "Смещение приводов", actuator_delay: "Задержка приводов", camera_frame_bias: "Смещение координат датчика", sensor_noise_dropout: "Шум и пропуски", sensor_drift: "Дрейф", confounded_without_joint_feedback: "Без независимых энкодеров" };
  return `<section class="panel spaced-section"><div class="panel-heading"><div><p class="eyebrow">01 / Проверяемая диагностика</p><h2>Проба → причина → компенсация</h2></div></div><div class="panel-body"><p class="form-description">MuJoCo исполняет контролируемые ошибки приводов и координат датчика. Диагностика получает измерения проб; скрытые параметры внесённой ошибки ей не передаются. Недостаток независимых измерений даёт AMBIGUOUS.</p><button class="button secondary" type="button" data-workflow="fault_repair" ${jobs.some((job) => !TERMINAL_JOBS.includes(job.status)) ? "disabled" : ""}>Запустить сравнение проб и компенсации</button>${latest?.cases ? simulationComparison(latest.cases.flatMap((item) => [[`${labels[item.scenario_id] || item.scenario_id} · исходное исполнение`, item.unmodified], [`${item.diagnosis.status} · ${item.diagnosis.repair_authorized ? "целевая компенсация" : "изменение отклонено"}`, item.targeted_repair]])) : ""}<p class="form-hint">Это модельная компенсация в задаче достижения цели. Проверка не содержит RGB-диагностики, предметного контакта или сравнения с обучением всей сети. H4 остаётся непроверенной.</p></div>${jobRows(jobs.slice(0, 3))}</section>`;
}

function acceptancePanel() {
  const acceptance = model.state?.acceptance;
  if (!acceptance) return "";
  const labels = { VERIFIED: "Проверено", VERIFIED_WITHIN_SCOPE: "Проверено в указанной области", VERIFIED_WITHIN_SIMULATION: "Проверено в симуляции", PARTIAL: "Частично", OPEN: "Остаётся работа" };
  return `<section class="panel spaced-section"><div class="panel-heading"><div><h2>Приёмка Explorer</h2><p>Состояние исходной задачи по свидетельствам</p></div><span class="badge warn">Работа продолжается</span></div><div class="panel-body"><p class="form-description">Полная программная приёмка открыта. Физические предметные навыки не валидированы; научные гипотезы H1–H5 остаются непроверенными.</p><details><summary>Критерии и фактические границы проверки</summary><ul class="plain-list">${array(acceptance.criteria).map((criterion) => `<li><h3>${esc(criterion.title)} · ${esc(labels[criterion.status] || criterion.status)}</h3><p>${esc(criterion.evidence)}</p></li>`).join("")}</ul></details></div></section>`;
}

function manipulationResult(job) {
  const r = job.result || {};
  if (job.kind === "mobile_manipulation") return `<article class="recording-row"><div><h3>Мобильная конфета · скриптовая физика</h3><p>${esc(simulationRatio(r.evaluation))} полных успешных попыток baseline.</p><p class="form-hint">Маршрут, захват, удержание при перевозке и размещение проверяются целиком. Это не обученная модель. Неудачные попытки и потеря предмета сохранены.</p></div></article>`;
  if (job.kind === "manipulation_benchmark") return `<article class="recording-row"><div><h3>Измеренные процессы физики</h3>${array(r.results).map((item) => `<p>${count(item.workers)} процессов: ${item.executed ? `${Number(item.actions_per_s_including_spawn).toFixed(1)} действий/с с учётом запуска` : "не запущены: сохранён резерв памяти"}</p>`).join("")}<p class="form-hint">Это измерение throughput. Число сред реального обучения остаётся ${count(r.actual_training_workers)}.</p></div></article>`;
  if (job.kind === "manipulation_curriculum") return `<article class="recording-row"><div><h3>BC → PPO · ${esc(workflowTask(job.config.task || "candy_to_tray"))}</h3>${simulationComparison([["BC · самостоятельные попытки", r.bc_evaluation], ["BC + PPO · те же условия", r.ppo_evaluation]])}<p class="form-hint">Кандидат ${r.development_candidate_accepted ? "прошёл development-критерий" : "отклонён"}. Файл исходного BC ${r.incumbent_file_preserved ? "сохранён" : "не подтверждён"}. Физический навык не проверялся.</p></div></article>`;
  return `<article class="recording-row"><div><h3>${esc(workflowTask(r.task || job.config.task))} · ${esc(jobTitle(job))}</h3><p>${esc(simulationRatio(r.evaluation))} полных успешных попыток обученной модели</p><p class="form-hint">${job.kind === "manipulation" ? `Обновлений PPO: ${count(r.teacher?.actual_optimizer_steps)}.` : `Обновлений ACT: ${count(r.student?.optimizer_steps)}.`} Источник показов, скриптовый baseline и самостоятельные попытки сохранены отдельно. Физическое исполнение не проверялось.</p></div></article>`;
}
