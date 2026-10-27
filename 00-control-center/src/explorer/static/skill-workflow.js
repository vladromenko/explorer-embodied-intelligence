"use strict";

// ENGINEERING_GLUE: one versioned skill, many verified originals, one aggregate training request.
const skillWorkflowUI = { entries: [], imports: [], loaded: false, loading: false, busy: false, error: "", selected: "", version: 1, state: null, editor: null, preset: "sock_to_basket", custom: "", proposalRequest: "", variant: "variant-001", session: "", scene: "", source: "", fullReview: false, goalVisible: false, allowReassignment: false, capture: "", captureContext: null, lastRecordingContext: null, stationary: false, episodeScope: "whole_episode", targetPhases: [], initialPhase: "", objectInstance: "", splitGroup: "", sequence: 0 };
const skillWorkflowSplit = { train: "Обучение", development: "Отдельная проверка", sealed_test: "Закрытый тест · не обучать" };
const skillWorkflowTask = { navigation: "Достичь точки · единый ROSMASTER", mobile_manipulation: "Полная embodied mobile manipulation", unknown: "Нужно уточнить цель" };
function skillWorkflowProject() { return typeof window !== "undefined" && window.location?.pathname.replace(/\/$/, "") === "/experiments/03" ? "03" : "02"; }
function skillWorkflowAdapterContext(body = {}) { const project_id = skillWorkflowProject(); return { ...body, project_id, input_mode: project_id === "03" ? "so101_leader_retarget" : "keyboard_gamepad" }; }

function skillWorkflowResetDraft() {
  const spec = skillWorkflowUI.state?.spec;
  skillWorkflowUI.editor = spec ? { title: spec.title, object: spec.object.name, goal: spec.goal.name, task_type: spec.task_type, cameras: spec.cameras.join(", "), training_camera: spec.training_camera, success: spec.success.description } : null;
}
async function refreshSkillWorkflow(force = false) {
  if (skillWorkflowUI.loading) return;
  skillWorkflowUI.loading = true;
  const sequence = ++skillWorkflowUI.sequence;
  try {
    const catalogue = await api(`/api/skill-workflow?project_id=${skillWorkflowProject()}`);
    if (sequence !== skillWorkflowUI.sequence) return;
    skillWorkflowUI.entries = array(catalogue.skills); skillWorkflowUI.imports = array(catalogue.imports); skillWorkflowUI.loaded = true;
    if (!skillWorkflowUI.selected && skillWorkflowUI.entries.length) {
      const latest = skillWorkflowUI.entries[skillWorkflowUI.entries.length - 1];
      skillWorkflowUI.selected = latest.spec.skill_id; skillWorkflowUI.version = latest.spec.version;
    }
    if (skillWorkflowUI.selected) {
      const selected = skillWorkflowUI.selected, version = skillWorkflowUI.version;
      const state = await api(`/api/skill-workflow/${encodeURIComponent(selected)}?version=${version}&project_id=${skillWorkflowProject()}`);
      if (sequence !== skillWorkflowUI.sequence || selected !== skillWorkflowUI.selected || version !== skillWorkflowUI.version) return;
      const changed = skillWorkflowUI.state?.spec_sha256 !== state.spec_sha256;
      skillWorkflowUI.state = state;
      if (changed || !skillWorkflowUI.editor) skillWorkflowResetDraft();
      if (!state.variants.some((item) => item.variant_id === skillWorkflowUI.variant)) skillWorkflowUI.variant = state.next_action?.variant_id || state.variants[0]?.variant_id || "";
      if (!state.sessions.some((item) => item.session_id === skillWorkflowUI.session)) skillWorkflowUI.session = "";
    }
    skillWorkflowUI.error = "";
  } catch (error) { skillWorkflowUI.error = error.message; }
  finally { skillWorkflowUI.loading = false; model.forceRender = true; render(); }
}
function skillWorkflowReasons(values) {
  const labels = { paused_native02_requires_segment_loader_qualification: "Показ с паузой сохранён; обучение таких сегментов станет доступно после отдельной программной проверки 02B", skill_spec_not_confirmed: "Сначала подтвердите описание навыка", pipeline_not_implemented_for_this_task_type: "Для этого типа задачи ещё нет проверенного полного pipeline", need_3_distinct_train_variants: "Нужны три разных одобренных учебных варианта", need_1_distinct_development_variants: "Нужна отдельная проверочная сессия", need_1_distinct_sealed_test_variants: "Нужна отдельная закрытая запись", full_task_phase_review_incomplete: "Разбор не подтверждает все фазы задачи", object_or_goal_not_confirmed_visible: "Предмет и цель не подтверждены в кадре", completed_recording_not_verified: "Запись не завершена", operator_success_not_verified: "Исходный оператор не отметил полный успех", source_quality_usable_not_verified: "Качество исходных меток не прошло допуск", rgb_verified_not_verified: "В учебном наборе нет проверенных RGB-кадров", action9_verified_not_verified: "Девять команд не подтверждены", causal_verified_not_verified: "Причинная привязка кадров и команд не подтверждена", admitted_for_training_not_verified: "Источник не допущен к BC-обучению", bound_source_evidence_changed: "Исходные данные изменились после привязки", owner_chassis_motion_forbidden: "Движение шасси сейчас запрещено владельцем", runtime_shared_pipeline_unavailable: "Текущий общий backend недоступен", canonical_embodied_episode_not_admitted_legacy_kept_for_analysis: "Нет подтверждённого canonical embodied контракта; legacy сохранён только для анализа" };
  return array(values).map((value) => {
    if (value.startsWith("source_input:EXPLICIT_RECORDED_PRIOR_COMMAND9_REQUIRED:")) return "Источник " + value.split(":").slice(2).join(":") + ": нужен буквально записанный prior-command9; его нельзя восстановить склейкой joint6 и odometry";
    return labels[value] || value;
  });
}
function skillWorkflowPanel() {
  if (!skillWorkflowUI.loaded && !skillWorkflowUI.loading && !skillWorkflowUI.error) queueMicrotask(() => refreshSkillWorkflow());
  const current = skillWorkflowUI.state, draft = skillWorkflowUI.editor;
  const selected = current?.spec, next = current?.next_action;
  const pipeline = current?.runtime_pipeline || { implemented: false };
  const runtimeFacts = current?.runtime_missing_facts || selected?.missing_facts || [];
  const textInput = (field, label) => `<div class="form-field"><label for="skill-edit-${field}">${label}</label><input id="skill-edit-${field}" data-skill-field="${field}" maxlength="${field === "success" ? 400 : 100}" value="${esc(draft?.[field] || "")}" required></div>`;
  const selector = `<div class="form-field"><label for="skill-workflow-select">Навык и версия</label><select id="skill-workflow-select"><option value="">Создать новый навык</option>${skillWorkflowUI.entries.map((item) => `<option value="${esc(item.spec.skill_id)}:${item.spec.version}" ${item.spec.skill_id === skillWorkflowUI.selected && item.spec.version === skillWorkflowUI.version ? "selected" : ""}>${esc(item.spec.title)} · версия ${item.spec.version}${item.confirmed ? "" : " · предложено"}</option>`).join("")}</select></div>`;
  const creation = `<details ${selected ? "" : "open"}><summary>Новый навык из шаблона или своего описания</summary><form id="skill-workflow-create"><div class="form-field"><label for="skill-workflow-preset">Что хотим научить</label><select id="skill-workflow-preset"><option value="sock_to_basket" ${skillWorkflowUI.preset === "sock_to_basket" ? "selected" : ""}>Носок → корзина</option><option value="object_a_to_b" ${skillWorkflowUI.preset === "object_a_to_b" ? "selected" : ""}>Предмет A → B</option><option value="navigate" ${skillWorkflowUI.preset === "navigate" ? "selected" : ""}>Навигация к точке</option><option value="approach_grasp_transport_place" ${skillWorkflowUI.preset === "approach_grasp_transport_place" ? "selected" : ""}>Подъехать → захватить → перевезти → поставить</option><option value="custom" ${skillWorkflowUI.preset === "custom" ? "selected" : ""}>Своё описание по-русски</option></select></div><div class="form-field"><label for="skill-workflow-custom">Своё описание</label><textarea id="skill-workflow-custom" maxlength="600" placeholder="Например: переложи красный носок в синюю корзину">${esc(skillWorkflowUI.custom)}</textarea></div><p class="form-hint">Описание станет редактируемым предложением. Система не считает русский текст однозначной командой или разрешением на движение.</p><button class="button secondary" type="submit" ${skillWorkflowUI.busy ? "disabled" : ""}>Предложить описание навыка</button></form></details>`;
  const editor = selected ? `<details ${current.confirmed ? "" : "open"}><summary>${current.confirmed ? "Описание подтверждено" : "Проверьте предложенное описание"} · версия ${selected.version}</summary><form id="skill-workflow-edit"><div class="form-grid">${textInput("title", "Название навыка")}${textInput("object", "Какой предмет")}${textInput("goal", "Куда / какой итог")}<div class="form-field"><label for="skill-edit-task_type">Цель задачи · робот всегда целиком</label><select id="skill-edit-task_type" data-skill-field="task_type">${Object.entries(skillWorkflowTask).map(([id, title]) => `<option value="${id}" ${draft.task_type === id ? "selected" : ""}>${title}</option>`).join("")}</select></div>${textInput("cameras", "Камеры записи · через запятую")}${textInput("training_camera", "Основной RGB-источник показа")}${textInput("success", "Наблюдаемый полный успех")}</div><p class="form-hint">Положение и материал предмета, геометрия сцены и физическая готовность не выводятся из названия. Изменение создаёт новую версию, сохраняя старые показы и модели.</p><button class="button secondary" type="submit" ${skillWorkflowUI.busy ? "disabled" : ""}>Сохранить новую версию</button>${!current.confirmed ? `<button class="button primary" type="button" data-skill-action="confirm" ${skillWorkflowUI.busy ? "disabled" : ""}>Описание верно · подтвердить эту версию</button>` : ""}</form><details><summary>Pipeline и недостающие факты</summary><p>${esc(selected.pipeline.pipeline_class)} · ${pipeline.implemented ? "реализованы offline ACT9 и hybrid-кандидат" : "текущий backend недоступен"} · state ${esc(pipeline.state_dimension || "UNKNOWN")} → action ${esc(pipeline.action_dimension || 9)} · ${esc(array(pipeline.qualified_devices).join(", "))}</p><p>План: perception и goal tracking → navigation baseline → contact ACT/BC → coordinator и recovery. Всегда одна совместная 9D-команда (6 координат руки/захвата + vx, vy, wz), включая настоящие нейтральные значения. RGB/depth и калибровки, LiDAR, odometry/pose/velocity, IMU, feedback/маски, gripper, clocks, detections/goal и power context сохраняются по фактическому источнику. Маска относится только к реально недоступному/невалидному измерению. Recorded, admitted и model-used каналы различаются.</p><ul>${array(runtimeFacts).map((item) => `<li>${esc(item.reason)}</li>`).join("")}</ul></details></details>` : "";
  let recording = "", training = "";
  if (selected) {
    const variant = current.variants.find((row) => row.variant_id === skillWorkflowUI.variant) || current.variants[0];
    const sessions = current.sessions.filter((row) => row.split === variant?.split);
    const source = skillWorkflowUI.imports.find((row) => row.import_job_id === skillWorkflowUI.source);
    const oldBinding = current.assignments.find((row) => row.binding.import_job_id === skillWorkflowUI.source)?.binding;
    const reassigned = source?.assigned_to && !(source.assigned_to.skill_id === selected.skill_id && source.assigned_to.version === selected.version && source.assigned_to.variant_id === variant?.variant_id);
    recording = `<section class="spaced-section"><h3>Следующий конкретный показ</h3><p class="form-description" role="status">${esc(next?.reason || "Получаем план")}</p><dl class="summary-list">${kv("Одобрено вариантов / начальный технический план", `${current.counts.covered_variants} / ${current.counts.planned_variants}`)}${kv("Принятых исходных показов / отклонено", `${current.counts.accepted_originals} / ${current.counts.rejected_originals}`)}${kv("Учебных / проверочных / закрытых", `${current.counts.accepted_train_originals} / ${current.counts.accepted_development_originals} / ${current.counts.sealed_originals}`)}</dl><div class="form-field"><label for="skill-workflow-variant">Вариант условий</label><select id="skill-workflow-variant">${current.variants.map((row) => `<option value="${esc(row.variant_id)}" ${variant?.variant_id === row.variant_id ? "selected" : ""}>${row.covered ? "✓ " : ""}${esc(row.title)} · ${esc(skillWorkflowSplit[row.split])} · ${row.accepted_originals} принято / ${row.rejected_originals} отклонено</option>`).join("")}</select></div>${variant ? `<ol class="plain-list"><li>Робот: ${esc(variant.initial_robot)}.</li><li>Предмет: ${esc(variant.initial_object)}.</li><li>Цель: ${esc(variant.goal_position)}.</li><li>Запишите всю последовательность: ${selected.stages.map((row) => esc(row.title)).join(" → ")}.</li></ol><p class="form-hint">Следующая попытка этого варианта: ${variant.next_repeat_number}. Первый план из пяти разных показов проверяет данные и pipeline; это не обещание освоения навыка.</p>` : ""}${!current.recording_allowed ? `<p class="form-error">${esc(skillWorkflowReasons(current.recording_blockers).join(". ") || "Сначала подтвердите описание навыка.")}</p>` : `<p class="form-hint">Шасси не перемещать. Источник показа: ${skillWorkflowProject() === "03" ? "SO-101 leader → native ROSMASTER" : "keyboard/gamepad → native ROSMASTER"}. Нативную запись неподвижной сцены запускайте обычной кнопкой ROSMASTER ниже. Контекст выбранного навыка будет сохранён в отдельном sidecar, без новых полей в протоколе контроллера.</p>`}<div class="form-field"><label for="skill-workflow-session">Сессия ${esc(skillWorkflowSplit[variant?.split])}</label><select id="skill-workflow-session"><option value="">Выберите или создайте отдельную сессию</option>${sessions.map((row) => `<option value="${esc(row.session_id)}" ${skillWorkflowUI.session === row.session_id ? "selected" : ""}>${esc(row.scene)} · ${esc(row.description)}${row.historical ? " · архивная отметка" : ""}</option>`).join("")}</select></div><form id="skill-workflow-session-form"><div class="form-field"><label for="skill-workflow-scene">Новая сцена / сессия</label><input id="skill-workflow-scene" maxlength="240" value="${esc(skillWorkflowUI.scene)}" placeholder="Например: стол, корзина справа, вечер" required></div><div class="form-field"><label for="skill-workflow-object-instance">Конкретный предмет</label><input id="skill-workflow-object-instance" maxlength="160" value="${esc(skillWorkflowUI.objectInstance)}" placeholder="Например: носок №1" required></div><div class="form-field"><label for="skill-workflow-split-group">Серия независимых сцен</label><input id="skill-workflow-split-group" maxlength="160" value="${esc(skillWorkflowUI.splitGroup)}" placeholder="Например: серия A · предметы 1–3" required></div><button class="button secondary" type="submit" ${!current.confirmed || skillWorkflowUI.busy ? "disabled" : ""}>Начать отдельную сессию этого раздела</button></form><p class="form-hint">Одна сессия не пересекает обучение, проверку и закрытый тест. Отличие физических сцен подтверждает оператор, оно не измеряется автоматически.</p><label class="world-episode"><input id="skill-workflow-stationary" type="checkbox" ${skillWorkflowUI.stationary ? "checked" : ""}><span>Шасси остаётся неподвижным: разрешения на его движение нет. Нейтральные 9D-каналы всё равно записываются.</span></label><div class="form-field"><label for="skill-workflow-scope">Объём эпизода · контракт робота не меняется</label><select id="skill-workflow-scope"><option value="whole_episode" ${skillWorkflowUI.episodeScope === "whole_episode" ? "selected" : ""}>Вся последовательность задачи</option><option value="targeted_subskill" ${skillWorkflowUI.episodeScope === "targeted_subskill" ? "selected" : ""}>Целевой показ выбранных фаз</option><option value="recovery" ${skillWorkflowUI.episodeScope === "recovery" ? "selected" : ""}>Восстановление после ошибки</option></select></div>${skillWorkflowUI.episodeScope !== "whole_episode" ? `<fieldset><legend>Фазы целевого показа / recovery</legend>${selected.stages.map((phase) => `<label class="world-episode"><input type="checkbox" data-skill-target-phase="${esc(phase.id)}" ${skillWorkflowUI.targetPhases.includes(phase.id) ? "checked" : ""}><span>${esc(phase.title)}</span></label>`).join("")}</fieldset>` : ""}<div class="form-field"><label for="skill-workflow-initial-phase">Фаза в начале записи</label><select id="skill-workflow-initial-phase"><option value="">Выберите фактическую начальную фазу</option>${selected.stages.map(phase => `<option value="${esc(phase.id)}" ${skillWorkflowUI.initialPhase === phase.id ? "selected" : ""}>${esc(phase.title)}</option>`).join("")}</select></div><label class="world-episode"><input id="skill-workflow-review-full" type="checkbox" ${skillWorkflowUI.fullReview ? "checked" : ""}><span>Я просмотрел именно этот эпизод и подтверждаю успешное завершение выбранного объёма и отмеченных фаз.</span></label><label class="world-episode"><input id="skill-workflow-review-visible" type="checkbox" ${skillWorkflowUI.goalVisible ? "checked" : ""}><span>Предмет и цель действительно видны в выбранном RGB-источнике.</span></label><details><summary>Привязать уже записанный показ</summary><div class="form-field"><label for="skill-workflow-source">Перенесённый оригинал</label><select id="skill-workflow-source"><option value="">Выберите существующую запись</option>${skillWorkflowUI.imports.map((row) => `<option value="${esc(row.import_job_id)}" ${row.import_job_id === skillWorkflowUI.source ? "selected" : ""}>${esc(row.original_episode_id)} · источник ${esc(row.source_project_id || "UNKNOWN")} / ${esc(row.demonstration_input?.input_mode || "UNKNOWN")} · ${row.admitted_for_training ? "canonical embodied доказательства проверены" : "анализ; полный embodied допуск не подтверждён"}${row.assigned_to ? " · уже привязан" : ""}</option>`).join("")}</select></div>${reassigned ? `<label class="world-episode"><input id="skill-workflow-reassign" type="checkbox" ${skillWorkflowUI.allowReassignment ? "checked" : ""}><span>Явно переназначить этот оригинал выбранной версии и варианту; история сохраняется.</span></label>` : ""}<button class="button secondary" type="button" data-skill-action="bind" ${!source || !skillWorkflowUI.session || skillWorkflowUI.busy || (reassigned && !skillWorkflowUI.allowReassignment) ? "disabled" : ""}>Сохранить привязку и разбор</button><p class="form-hint">Неудачные и camera-only записи остаются материалом анализа. Эта привязка не меняет исходные bytes, исходный outcome или canonical допуск.</p>${source && !source.admitted_for_training ? `<p class="form-hint">${esc(skillWorkflowReasons(source.reasons).join("; ") || "Проверьте качество, девять команд и причинную привязку.")}</p>` : ""}</details>${current.phase_variant_coverage ? `<details><summary>Охват фаз × вариантов · whole / targeted / recovery</summary><ul class="plain-list">${selected.stages.map((phase) => `<li>${esc(phase.title)}: ${current.variants.map((item) => `${esc(item.variant_id)} — ${count(current.phase_variant_coverage[phase.id]?.[item.variant_id] || 0)}`).join("; ")}</li>`).join("")}</ul><p class="form-hint">Целевой показ фазы не становится полным успешным эпизодом. Отдельно учитываются whole episodes и исход каждой фазы.</p></details>` : ""}${current.assignments.length ? `<details><summary>Принятые и отклонённые показы · ${current.assignments.length}</summary><ul class="plain-list">${current.assignments.map((row) => `<li><strong>${esc(row.binding.original_episode_id)}</strong> · ${row.accepted ? "Одобрен в заявленном объёме" : "Оставлен для анализа"}<p>${esc(skillWorkflowReasons(row.reasons).join("; "))}</p></li>`).join("")}</ul></details>` : ""}</section>`;
    const jobs = model.jobs.filter((job) => job.config?.skill_workflow?.skill_id === selected.skill_id && job.config.skill_workflow.skill_version === selected.version);
    training = `<section class="spaced-section"><h3>Один навык всего ROSMASTER по всем принятым эпизодам</h3><p class="form-description">Собирается один canonical aggregate всех принятых train/development эпизодов с envelope и всеми сенсорными payloads. Whole episodes, targeted фазы и recovery принадлежат одной SkillSpec и полному 9D-контракту. Закрытые payloads исключены. Legacy RGB+9D без embodied доказательств остаётся анализом. Один backend обучает end-to-end ACT9 с фактическими sensor features/masks и сравнивает hybrid-кандидат. Недостающие geometry/map блокируют соответствующие hybrid-компоненты; наличие кода не подтверждает автономный или физический навык.</p><button class="button primary full" type="button" data-skill-action="train" ${!current.train_ready || skillWorkflowUI.busy ? "disabled" : ""}>Обучить общий навык ROSMASTER · ACT9 + hybrid</button>${current.training_blockers.length ? `<p class="form-hint" role="status">${esc(skillWorkflowReasons(current.training_blockers).join(". "))}</p>` : '<p class="form-hint">Начальный технический охват есть. Обучение всё равно требует отдельной проверки полного навыка.</p>'}${typeof jobRows === "function" ? jobRows(jobs.slice(0, 4), "Здесь появятся задания именно этого навыка и версии.") : ""}</section>`;
  }
  return `<section class="panel spaced-section" id="skill-workflow-panel"><div class="panel-heading"><div><p class="eyebrow">${skillWorkflowProject()} / Общий навык ROSMASTER · показы02+03</p><h2>От задачи к серии показов</h2></div><button class="button secondary small" type="button" data-skill-action="refresh" ${skillWorkflowUI.loading ? "disabled" : ""}>Обновить</button></div><div class="panel-body">${selector}${creation}${editor}${recording}${training}${skillWorkflowUI.error ? `<p class="form-error" role="alert">${esc(skillWorkflowUI.error)}</p>` : ""}${!skillWorkflowUI.loaded && !skillWorkflowUI.error ? '<p role="status">Получаем определения навыков и реальные записи…</p>' : ""}</div></section>`;
}
function skillWorkflowRecordingContext() {
  const state = skillWorkflowUI.state;
  if (!state || !state.confirmed) throw new Error("Выберите и подтвердите описание навыка перед записью.");
  if (!state.recording_allowed) throw new Error("Текущий план не разрешает новую запись с движением шасси.");
  const variant = state.variants.find((item) => item.variant_id === skillWorkflowUI.variant);
  const session = state.sessions.find((item) => item.session_id === skillWorkflowUI.session && item.split === variant?.split);
  if (!session || !variant) throw new Error("Выберите отдельную сессию текущего варианта.");
  if (!skillWorkflowUI.stationary) throw new Error("Подтвердите неподвижность шасси; разрешения на его движение нет.");
  const targets = skillWorkflowUI.episodeScope === "whole_episode" ? state.spec.stages.map((phase) => phase.id) : skillWorkflowUI.targetPhases;
  if (!targets.length) throw new Error("Отметьте фазы целевого показа / recovery.");
  if (!targets.includes(skillWorkflowUI.initialPhase)) throw new Error("Выберите фактическую начальную фазу в пределах выбранного показа.");
  const context = skillWorkflowAdapterContext({ skill_id: state.spec.skill_id, version: state.spec.version, spec_sha256: state.spec_sha256, variant_id: variant.variant_id, session_id: session.session_id, episode_scope: skillWorkflowUI.episodeScope, target_phase_ids: [...targets], phase_id: skillWorkflowUI.initialPhase, stationary_chassis_confirmed: true });
  skillWorkflowUI.lastRecordingContext = structuredClone(context);
  return context;
}
function skillWorkflowFinishContext(outcome) {
  const captured = skillWorkflowUI.capture ? skillWorkflowUI.captureContext : null;
  if (skillWorkflowUI.capture && !captured) throw new Error("Восстановите контекст активной записи с сервера перед её разбором.");
  if (captured && (captured.skill_id !== skillWorkflowUI.state?.spec.skill_id || captured.version !== skillWorkflowUI.state?.spec.version || captured.variant_id !== skillWorkflowUI.variant || captured.session_id !== skillWorkflowUI.session)) throw new Error("Выбранный навык или вариант отличается от активной записи. Вернитесь к её сохранённому контексту.");
  const scope = captured?.episode_scope || skillWorkflowUI.episodeScope;
  const targets = captured?.target_phase_ids || (scope === "whole_episode" ? (skillWorkflowUI.state?.spec.stages || []).map((item) => item.id) : skillWorkflowUI.targetPhases);
  return { capture_id: skillWorkflowUI.capture || null, outcome, full_task_confirmed: outcome === "success" && skillWorkflowUI.fullReview && scope === "whole_episode", target_scope_confirmed: outcome === "success" && skillWorkflowUI.fullReview, object_and_goal_visible: skillWorkflowUI.goalVisible, completed_stages: skillWorkflowUI.fullReview ? targets : [], phase_outcomes: Object.fromEntries(targets.map((phase) => [phase, skillWorkflowUI.fullReview && outcome === "success" ? "success" : outcome === "failure" ? "failure" : "unknown"])) };
}
function skillWorkflowCaptureStarted(captureId, context = null) { skillWorkflowUI.capture = captureId || ""; skillWorkflowUI.captureContext = context ? structuredClone(context) : structuredClone(skillWorkflowUI.lastRecordingContext); skillWorkflowUI.fullReview = false; skillWorkflowUI.goalVisible = false; }
async function skillWorkflowRecoverCapture(nativeEpisodeId) {
  if (!nativeEpisodeId) return null;
  const response = await api(`/api/skill-workflow/capture-context?episode_id=${encodeURIComponent(nativeEpisodeId)}`);
  const context = response?.capture ?? response;
  if (!context?.capture_id || context.finished) return null;
  skillWorkflowCaptureStarted(context.capture_id, context);
  skillWorkflowUI.selected = context.skill_id; skillWorkflowUI.version = context.version; skillWorkflowUI.variant = context.variant_id; skillWorkflowUI.session = context.session_id; skillWorkflowUI.episodeScope = context.episode_scope; skillWorkflowUI.targetPhases = [...context.target_phase_ids];
  await refreshSkillWorkflow(true);
  return context;
}
function skillWorkflowCaptureFinished() { skillWorkflowUI.capture = ""; skillWorkflowUI.captureContext = null; skillWorkflowUI.lastRecordingContext = null; skillWorkflowUI.fullReview = false; skillWorkflowUI.goalVisible = false; refreshSkillWorkflow(true); }
async function skillWorkflowOperation(operation) {
  if (skillWorkflowUI.busy) return;
  skillWorkflowUI.busy = true;
  try { await operation(); skillWorkflowUI.error = ""; await refreshSkillWorkflow(true); }
  catch (error) { skillWorkflowUI.error = error.message; toast(error.message, true); }
  finally { skillWorkflowUI.busy = false; model.forceRender = true; render(); }
}
document.addEventListener("input", (event) => {
  const field = event.target.dataset.skillField;
  if (field && skillWorkflowUI.editor) skillWorkflowUI.editor[field] = event.target.value;
  if (event.target.id === "skill-workflow-stationary") skillWorkflowUI.stationary = event.target.checked;
  if (event.target.dataset.skillTargetPhase) { const phase = event.target.dataset.skillTargetPhase; skillWorkflowUI.targetPhases = event.target.checked ? [...new Set([...skillWorkflowUI.targetPhases, phase])] : skillWorkflowUI.targetPhases.filter((value) => value !== phase); }
  if (event.target.id === "skill-workflow-custom") skillWorkflowUI.custom = event.target.value;
  if (event.target.id === "skill-workflow-object-instance") skillWorkflowUI.objectInstance = event.target.value;
  if (event.target.id === "skill-workflow-split-group") skillWorkflowUI.splitGroup = event.target.value;
  if (event.target.id === "skill-workflow-scene") skillWorkflowUI.scene = event.target.value;
  if (event.target.id === "skill-workflow-review-full") skillWorkflowUI.fullReview = event.target.checked;
  if (event.target.id === "skill-workflow-review-visible") skillWorkflowUI.goalVisible = event.target.checked;
  if (event.target.id === "skill-workflow-reassign") { skillWorkflowUI.allowReassignment = event.target.checked; model.forceRender = true; render(); }
});
document.addEventListener("change", (event) => {
  if (event.target.id === "skill-workflow-initial-phase") skillWorkflowUI.initialPhase = event.target.value;
  if (event.target.id === "skill-workflow-scope") { skillWorkflowUI.episodeScope = event.target.value; skillWorkflowUI.fullReview = false; model.forceRender = true; render(); }
  if (event.target.id === "skill-workflow-preset") skillWorkflowUI.preset = event.target.value;
  if (event.target.id === "skill-workflow-select") {
    const [identity, version] = event.target.value.split(":");
    skillWorkflowUI.sequence += 1; skillWorkflowUI.selected = identity || ""; skillWorkflowUI.version = Number(version) || 1; skillWorkflowUI.state = null; skillWorkflowUI.editor = null; skillWorkflowUI.session = ""; skillWorkflowUI.fullReview = false;
    refreshSkillWorkflow(true);
  }
  if (event.target.id === "skill-workflow-variant") { skillWorkflowUI.variant = event.target.value; skillWorkflowUI.session = ""; skillWorkflowUI.fullReview = false; model.forceRender = true; render(); }
  if (event.target.id === "skill-workflow-session") skillWorkflowUI.session = event.target.value;
  if (event.target.id === "skill-workflow-source") { skillWorkflowUI.source = event.target.value; skillWorkflowUI.allowReassignment = false; skillWorkflowUI.fullReview = false; model.forceRender = true; render(); }
});
document.addEventListener("submit", (event) => {
  if (!["skill-workflow-create", "skill-workflow-edit", "skill-workflow-session-form"].includes(event.target.id)) return;
  event.preventDefault();
  skillWorkflowOperation(async () => {
    if (event.target.id === "skill-workflow-create") {
      if (!skillWorkflowUI.proposalRequest) skillWorkflowUI.proposalRequest = crypto.randomUUID();
      const chosen = await post("/api/skill-workflow", skillWorkflowAdapterContext({ request_id: skillWorkflowUI.proposalRequest, preset: skillWorkflowUI.preset === "custom" ? null : skillWorkflowUI.preset, text: skillWorkflowUI.custom }));
      skillWorkflowUI.selected = chosen.spec.skill_id; skillWorkflowUI.version = chosen.spec.version; skillWorkflowUI.state = chosen; skillWorkflowUI.editor = null; skillWorkflowUI.proposalRequest = ""; toast("Проверьте предложенный SkillSpec и подтвердите выбранную версию.");
    } else if (event.target.id === "skill-workflow-edit") {
      const current = skillWorkflowUI.state, draft = skillWorkflowUI.editor;
      const patch = { title: draft.title, object: { ...current.spec.object, name: draft.object }, goal: { ...current.spec.goal, name: draft.goal }, task_type: draft.task_type, cameras: draft.cameras.split(",").map((item) => item.trim()).filter(Boolean), training_camera: draft.training_camera, success: { ...current.spec.success, description: draft.success, full_task: true } };
      const chosen = await post(`/api/skill-workflow/${encodeURIComponent(current.spec.skill_id)}/revision`, { version: current.spec.version, spec_sha256: current.spec_sha256, patch });
      skillWorkflowUI.version = chosen.spec.version; skillWorkflowUI.state = chosen; skillWorkflowUI.editor = null; skillWorkflowUI.session = ""; toast("Новая версия сохранена; старые показы и модели остаются в прежней версии.");
    } else {
      const current = skillWorkflowUI.state, variant = current.variants.find((row) => row.variant_id === skillWorkflowUI.variant);
      const session = await post(`/api/skill-workflow/${encodeURIComponent(current.spec.skill_id)}/session`, { version: current.spec.version, split: variant.split, scene: skillWorkflowUI.scene, object_instance_id: skillWorkflowUI.objectInstance, group_id: skillWorkflowUI.splitGroup, description: "Отдельная сессия для " + skillWorkflowSplit[variant.split], ...skillWorkflowAdapterContext() });
      skillWorkflowUI.session = session.session_id; toast("Сессия сохранена отдельно от других разделов данных.");
    }
  });
});
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-skill-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.skillAction;
  if (action === "refresh") { refreshSkillWorkflow(true); return; }
  skillWorkflowOperation(async () => {
    const current = skillWorkflowUI.state;
    if (!current) throw new Error("Сначала выберите навык.");
    const base = `/api/skill-workflow/${encodeURIComponent(current.spec.skill_id)}`;
    if (action === "confirm") {
      await post(base + "/confirm", { version: current.spec.version, spec_sha256: current.spec_sha256 }); toast("Версия навыка подтверждена.");
    } else if (action === "bind") {
      const source = skillWorkflowUI.imports.find((row) => row.import_job_id === skillWorkflowUI.source);
      const previous = current.assignments.find((row) => row.binding.import_job_id === skillWorkflowUI.source)?.binding || source?.assigned_to;
      await post(base + "/bind", { version: current.spec.version, variant_id: skillWorkflowUI.variant, import_job_id: skillWorkflowUI.source, ...skillWorkflowAdapterContext(), session_id: skillWorkflowUI.session, ...skillWorkflowFinishContext(skillWorkflowUI.fullReview ? "success" : "unknown"), episode_scope: skillWorkflowUI.episodeScope, target_phase_ids: skillWorkflowUI.episodeScope === "whole_episode" ? current.spec.stages.map((phase) => phase.id) : skillWorkflowUI.targetPhases, expected_binding_sha256: previous?.binding_sha256 || null });
      toast("Привязка сохранена; исходные данные и допуск проверяются отдельно.");
    } else if (action === "train") {
      if (!current.train_ready) throw new Error(skillWorkflowReasons(current.training_blockers).join(". "));
      const job = await post(base + "/train", skillWorkflowAdapterContext({ version: current.spec.version, settings: {} }));
      model.jobs = [job, ...model.jobs.filter((row) => row.id !== job.id)]; toast("Общее offline обучение ROSMASTER поставлено в очередь на Homebrain; физический запуск этим не разрешается.");
    }
  });
});
