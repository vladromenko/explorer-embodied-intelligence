"use strict";

// ENGINEERING_GLUE: source-bound operator review; no recording/motion endpoint is invoked.
const demonstrationWizard = { states: new Map() };
function demonstrationWizardState(project) {
  if (!demonstrationWizard.states.has(project)) demonstrationWizard.states.set(project, { task: "pick_and_place", data: null, loading: false, saving: false, error: "", selected: "", draft: null });
  return demonstrationWizard.states.get(project);
}
async function refreshDemonstrationWizard(project) {
  const state = demonstrationWizardState(project);
  if (state.loading) return;
  state.loading = true;
  try {
    state.data = await api(`/api/demonstration-wizard?project_id=${encodeURIComponent(project)}&task_id=${encodeURIComponent(state.task)}`);
    state.error = "";
    if (state.selected && !state.data.sources.some((row) => row.source_key === state.selected)) { state.selected = ""; state.draft = null; }
  } catch (error) { state.error = error.message; }
  finally { state.loading = false; model.forceRender = true; render(); }
}
function demonstrationWizardDraft(state) {
  if (!state.draft) {
    const previous = state.data?.reviews.find((row) => row.source_key === state.selected);
    state.draft = previous ? { ...previous, phases: [...previous.phases] } : { scene_id: "", session_id: "", object_id: "", approach: "front", outcome: "unknown", reviewed_by: "", phases: [], observed_recording: false };
  }
  return state.draft;
}
function demonstrationWizardPanel(project) {
  const state = demonstrationWizardState(project), data = state.data;
  if (!data && !state.loading && !state.error) queueMicrotask(() => refreshDemonstrationWizard(project));
  const headline = `<div class="panel-heading"><div><p class="eyebrow">Мастер полного показа</p><h2>Что показать следующим</h2></div><button class="button secondary small" type="button" data-demo-refresh="${esc(project)}" ${state.loading ? "disabled" : ""}>Обновить</button></div>`;
  if (!data) return `<section class="panel spaced-section" data-demo-project="${esc(project)}">${headline}<div class="panel-body"><p role="status">${esc(state.error || "Получаем проверенные записи и план показов…")}</p></div></section>`;
  const source = data.sources.find((row) => row.source_key === state.selected);
  const draft = demonstrationWizardDraft(state), counts = data.counts, recommendation = data.recommendation;
  const missingLabel = { approach: "направление подхода", scene: "другая сцена", session: "другая сессия", object: "другой предмет / складки" };
  const directionLabel = { front: "спереди", left: "слева", right: "справа", above: "сверху", other: "другое" };
  const textField = (id, title, required = true) => `<div class="form-field"><label for="demo-${esc(id)}">${esc(title)}</label><input id="demo-${esc(id)}" data-demo-field="${esc(id)}" maxlength="100" value="${esc(draft[id])}" ${required ? "required" : ""}></div>`;
  const trend = data.evaluation_trend;
  return `<section class="panel spaced-section" data-demo-project="${esc(project)}">${headline}<div class="panel-body"><div class="form-field"><label for="demo-task">Задача полного показа</label><select id="demo-task">${data.tasks.map((task) => `<option value="${esc(task.id)}" ${state.task === task.id ? "selected" : ""}>${esc(task.title)}</option>`).join("")}</select></div><p class="form-description">${esc(data.task.setup)}</p><ol class="flow-steps">${data.task.phases.map((phase) => `<li><strong>${esc(phase.instruction)}</strong></li>`).join("")}</ol><details><summary>Как управлять и завершить показ</summary><p>${esc(project === "02" ? data.controls.keyboard : data.controls.leader)}</p><p>${esc(data.controls.chassis)}</p><p>Запишите попытку обычной кнопкой записи. Отметьте «Не получилось» при неудаче, сохраните запись и отдельно повторите полный показ. После переноса выберите запись ниже и разберите завершённые фазы.</p></details><dl class="summary-list">${kv("Проверено оригинальных показов", counts.original_episodes)}${kv("Одобрено для полного показа", counts.approved_demonstrations)}${kv("Неудач / без оценки", `${counts.reviewed_failures} / ${counts.reviewed_unknown}`)}${kv("Сцен / сессий", `${data.coverage.scenes} / ${data.coverage.sessions}`)}${kv("Отдельных попыток модели с оценкой", `${trend.successes} / ${trend.attempts}`)}</dl><p class="form-description" role="status">${esc(recommendation.reason)}</p><p class="form-hint">${recommendation.next_batch ? `Следующая ориентировочная серия: ${esc(recommendation.next_batch)} показов.` : recommendation.next_evaluation_batch ? `Следующая серия проверки: ${esc(recommendation.next_evaluation_batch)} отдельных попыток.` : ""} Количество меняется по данным; освоение задачи этим планом не устанавливается.</p>${data.coverage.missing.length ? `<p class="form-hint">Ещё не покрыто: ${data.coverage.missing.map((item) => esc(`${missingLabel[item.dimension] || item.dimension}${directionLabel[item.value] ? " — " + directionLabel[item.value] : ""}`)).join("; ")}.</p>` : ""}<details ${state.selected ? "open" : ""}><summary>Разобрать сохранённую запись · ${esc(counts.unreviewed_sources)} без разбора</summary><form id="demonstration-review-form"><div class="form-field"><label for="demo-source">Оригинальный показ или отдельная попытка модели</label><select id="demo-source" required><option value="">Выберите просмотренную запись</option>${data.sources.map((row) => `<option value="${esc(row.source_key)}" ${row.source_key === state.selected ? "selected" : ""}>${row.source_kind === "evaluation" ? "Попытка модели" : "Показ"} · ${esc(row.episode_id)} · ${esc(row.frames)} кадров · ${esc(row.source_outcome)}</option>`).join("")}</select></div>${source ? `<p class="form-hint">${source.source_kind === "evaluation" ? "Оценка отдельной попытки модели по записи; независимость сцен автоматически не доказана." : source.source_training_usable ? "Исходник содержит пригодные учебные метки. Итог и все фазы проверяются отдельно." : "Исходные учебные метки не прошли допуск. Разбор сохранится для анализа и не сделает запись экспертной."}</p><div class="form-grid">${textField("scene_id", "Сцена / положение предмета")}${textField("session_id", "Сессия записи")}${textField("object_id", "Предмет / конфигурация", data.task.object_task)}${textField("reviewed_by", "Кто просмотрел запись")}<div class="form-field"><label for="demo-approach">Направление подхода</label><select id="demo-approach" data-demo-field="approach">${Object.entries(directionLabel).map(([id, title]) => `<option value="${id}" ${draft.approach === id ? "selected" : ""}>${title}</option>`).join("")}</select></div><div class="form-field"><label for="demo-outcome">Полный итог задачи</label><select id="demo-outcome" data-demo-field="outcome">${[["unknown", "Без оценки"], ["failure", "Не получилось"], ["success", "Получилось полностью"]].map(([id, title]) => `<option value="${id}" ${draft.outcome === id ? "selected" : ""}>${title}</option>`).join("")}</select></div></div><fieldset><legend>Какие фазы видны на записи</legend>${data.task.phases.map((phase) => `<label class="world-episode"><input type="checkbox" data-demo-phase="${esc(phase.id)}" ${draft.phases.includes(phase.id) ? "checked" : ""}><span>${esc(phase.instruction)}</span></label>`).join("")}</fieldset><label class="world-episode"><input type="checkbox" id="demo-observed" ${draft.observed_recording ? "checked" : ""} required><span>Я просмотрел именно эту запись и отметил наблюдаемый итог.</span></label><button class="button secondary" type="submit" ${state.saving ? "disabled" : ""}>Сохранить разбор${draft.revision_sha256 ? " · новая версия" : ""}</button>` : ""}</form></details>${state.error ? `<p class="form-error" role="alert">${esc(state.error)}</p>` : ""}${data.invalid_sources.length || data.stale_reviews.length ? `<p class="form-hint">Исключено источников с изменёнными или недоступными свидетельствами: ${esc(data.invalid_sources.length + data.stale_reviews.length)}. Они не увеличивают охват.</p>` : ""}<p class="form-hint">Условия и итоги — явные отметки оператора. Неудачные и неизвестные исходы сохраняются отдельно от одобренных показов; исходники и допуск обучения не изменяются.</p></div></section>`;
}
function demonstrationWizardContext(target) {
  const panel = target.closest("[data-demo-project]");
  return panel ? { project: panel.dataset.demoProject, state: demonstrationWizardState(panel.dataset.demoProject) } : null;
}
document.addEventListener("input", (event) => {
  const context = demonstrationWizardContext(event.target);
  if (!context) return;
  const draft = demonstrationWizardDraft(context.state), field = event.target.dataset.demoField;
  if (field) draft[field] = event.target.value;
  if (event.target.dataset.demoPhase) draft.phases = [...event.target.closest("fieldset").querySelectorAll("input:checked")].map((input) => input.dataset.demoPhase);
  if (event.target.id === "demo-observed") draft.observed_recording = event.target.checked;
});
document.addEventListener("change", (event) => {
  const context = demonstrationWizardContext(event.target);
  if (!context) return;
  if (event.target.id === "demo-task") { context.state.task = event.target.value; context.state.data = null; context.state.draft = null; context.state.selected = ""; refreshDemonstrationWizard(context.project); }
  if (event.target.id === "demo-source") { context.state.selected = event.target.value; context.state.draft = null; model.forceRender = true; render(); }
});
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-demo-refresh]");
  if (button && !button.disabled) refreshDemonstrationWizard(button.dataset.demoRefresh);
});
document.addEventListener("submit", async (event) => {
  if (event.target.id !== "demonstration-review-form") return;
  event.preventDefault();
  const context = demonstrationWizardContext(event.target), state = context.state;
  if (state.saving) return;
  const source = state.data.sources.find((row) => row.source_key === state.selected);
  if (!source) return;
  state.saving = true;
  try {
    const draft = demonstrationWizardDraft(state);
    const body = { project_id: context.project, task_id: state.task, source_key: source.source_key, source_fingerprint: source.source_fingerprint, ...draft, expected_revision_sha256: draft.revision_sha256 || null };
    state.draft = await post("/api/demonstration-wizard/annotation", body);
    state.error = ""; toast("Разбор сохранён с привязкой к исходной записи.");
    await refreshDemonstrationWizard(context.project);
  } catch (error) { state.error = error.message; toast(error.message, true); }
  finally { state.saving = false; model.forceRender = true; render(); }
});
