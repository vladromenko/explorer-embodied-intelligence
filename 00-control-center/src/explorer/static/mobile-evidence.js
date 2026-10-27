"use strict";
// ENGINEERING_GLUE: read-only recording/data/model evidence; no control requests.
(function (surface) {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const list = value => Array.isArray(value) ? value : [];
  const integer = value => Number.isInteger(value) && value >= 0 ? value.toLocaleString("ru-RU") : "—";
  const fact = value => value === true ? "да" : value === false ? "нет" : "не подтверждено";
  const sensorLabels = { camera_rgb: "RGB", camera_depth: "Глубина", camera_calibration: "Калибровка камеры", lidar: "Лидар",
    odometry_pose: "Поза по одометрии", base_velocity: "Скорость базы", imu: "IMU", joint_command: "Команды суставов",
    joint_feedback: "Измеренные суставы", gripper: "Захват", clocks: "Часы и загрузка", detections: "Объекты", goal: "Цель", power_context: "Питание и состояние", preceding_command9: "Предыдущая общая команда 9D" };
  function sensorEvidenceTable(channels = {}, rawCounts = {}) {
    const names = [...new Set([...Object.keys(channels), ...Object.keys(rawCounts)])];
    const rows = names.map(name => {
      const entry = channels[name] || {admitted:false,model_used:false};
      const recorded = rawCounts[name] > 0 ? true : entry.recorded;
      return `<tr><th scope="row">${escape(sensorLabels[name] || name)}</th><td>${fact(recorded)}</td><td>${fact(entry.admitted)}</td><td>${fact(entry.model_used)}</td></tr>`;
    }).join("");
    return rows ? `<div class="table-wrap"><table><caption>Запись, проверка и использование датчиков — разные факты</caption><thead><tr><th>Наблюдение</th><th>Записано</th><th>Допущено</th><th>Использует модель</th></tr></thead><tbody>${rows}</tbody></table></div><p class="form-hint">Недоступные или некорректные измерения сохраняют маску. Настоящие нулевые команды остаются в общем действии 9D.</p>` : '<p class="form-hint">Полная карта синхронных датчиков в исходной записи не подтверждена.</p>';
  }
  function mobileReason(reason) {
    const value = String(reason || "");
    const exact = {
      observation_only_no_admitted_dataset: "Сохранены наблюдения; обучающий эпизод ещё не получен.",
      operator_outcome_not_explicit_success: "Нет явной отметки оператора об успешном полном показе.",
      source_quality_not_usable_for_BC: "Показ пока не проходит проверку качества обучающих данных.",
      converted_RGB_episode_not_verified: "RGB не подтверждён в обучающем наборе.",
      converted_native_action_state9_not_verified: "Нет проверенной пары: шесть координат руки/захвата и три скорости базы.",
      frame_state_future_label_causality_not_verified: "Временная связь кадра, состояния и следующей команды не подтверждена.",
      no_original_training_split_episodes: "Эпизоды оставлены для проверки, а не для обучения.",
      source_training_admission_denied: "Исходная проверка не допускает показ в обучение.",
      recording_not_complete: "Полный показ ещё не завершён.",
      mobile_import_not_completed: "Перенос ещё не завершён.",
      mobile_embodied_contract_missing: "Не подтверждён полный набор наблюдений мобильного навыка.",
      mobile_embodied_admission_denied: "Синхронные данные мобильного навыка требуют дополнительной проверки.",
      camera_identity_changed_inside_recording: "Внутри показа изменился источник изображения."
    };
    if (exact[value]) return exact[value];
    if (value.includes("UNKNOWN_AFTER_REBOOT") || value.startsWith("unknown_or_unqualified_arm_history")) return "История положения и команд руки неизвестна; кадры остаются для анализа.";
    if (value.startsWith("evidence_inspection_failed")) return "Проверка исходных кадров, команд или контрольных сумм не завершена.";
    return value;
  }
  function liveMobileEvidence(preview = {}, teaching = {}) {
    // This must come from the actual JPEG preview helper, not node.online/inventory.
    const fresh = preview.camera_id === "nano-rgbd" && preview.freshness_clock === "browser_receive_monotonic_only" && preview.ready === true && typeof preview.frame_id === "string" && preview.frame_id.length > 0;
    const active = teaching.active || null, last = teaching.last || null;
    return { camera_id: "nano-rgbd", camera_fresh: fresh, camera_frame_id: fresh ? preview.frame_id : null,
      freshness_basis: "actual_JPEG_browser_receive_monotonic", recording_active: !!active,
      recording_id: active?.id || null, recording_name: active?.name || null,
      recorded_frames: active?.frames, recorded_samples: active?.samples,
      last_recording_id: last?.id || null, last_outcome: last?.outcome || "unknown" };
  }
  function mobileEvidencePanel(payload = {}, context = {}) {
    const counts = payload.counts || {}, imports = list(payload.imports), project = context.project_id || payload.project_id || "02";
    const live = liveMobileEvidence(context.preview, context.teaching);
    const lastLabel = { success: "оператор отметил успех", failure: "оператор отметил неудачу", unknown: "результат не определён" }[live.last_outcome] || "результат не определён";
    const rows = imports.map(item => {
      const reasons = [...new Set(list(item.reasons).map(mobileReason))];
      const trained = item.visual_ACT?.trained_RGB_model_verified === true;
      const name = item.recording_name || `Показ ${String(item.original_episode_id || item.import_job_id || "").slice(0, 12)}`;
      const sensor = item.mobile_embodied || {};
      const legacy = item.legacy_RGB9D_view || item;
      const incompleteImport = item.import_status && item.import_status !== "completed";
      return `<article class="recording-row"><div><h3>${escape(name)}</h3><p>Сохранено кадров: ${integer(item.raw_frames_recorded)} · проверено RGB для обучения: ${item.rgb_verified ? "да" : "пока нет"}</p><div class="recording-label">${incompleteImport ? "Перенос не завершён" : item.admitted_for_training === true ? "Допущен к офлайн-обучению" : "Остаётся для анализа"}</div><p class="form-hint">${escape(reasons[0] || (item.admitted_for_training ? "Данные проверены; обучение и независимая оценка модели — следующие этапы." : "Ожидаем подтверждения состава данных."))}</p><dl class="summary-list"><div><dt>RGB и записанная предыдущая команда 9D для ACT</dt><dd>${legacy.rgb_verified && legacy.action9_verified && legacy.causal_verified ? "Проверены по исходникам" : "Не подтверждены"}</dd></div><div><dt>Наблюдения мобильного навыка</dt><dd>${sensor.admitted_for_training === true ? "Контракт проверен" : "Ещё не подтверждены"}</dd></div><div><dt>Кадры в сохранённой модели ACT</dt><dd>${trained ? "Подтверждены по модели и запуску" : "Подтверждённого обучения ещё нет"}</dd></div></dl>${sensorEvidenceTable(sensor.channels, item.raw_observation_evidence?.available_source_channel_counts)}<details><summary>Причины и происхождение</summary><ul class="plain-list">${reasons.map(reason => `<li>${escape(reason)}</li>`).join("")}</ul><p class="form-hint">Исходный эпизод: ${escape(item.original_episode_id || "не указан")} · сессия регистратора: ${escape(item.original_session_id || "не записана")} · сессия показа оператора: ${escape(item.operator_session_id || "отдельно не записана")}</p><p class="form-hint">Камера записи: ${escape(item.camera_id || "аппаратный ID не записан")} · ${escape(item.camera_profile || item.camera_feature || "профиль уточняется")}</p><p class="form-hint">${item.verification_basis === "source_bound_unified_embodied_contract" ? "Единый мобильный эпизод проверен по исходным данным; измерения и их маски сохраняются раздельно." : "В старом представлении ACT положения руки обозначены как командная оценка."} Физический успех этим импортом не подтверждается.</p><p class="form-hint">SHA исходного манифеста: ${escape(item.manifest_sha256 || "не подтверждена")}</p></details></div></article>`;
    }).join("");
    return `<section class="panel mobile-evidence-panel"><div class="panel-heading"><div><p class="eyebrow">${escape(project)} / От кадра к модели</p><h2>Что попадает в обучение</h2></div><button class="quiet-button text-link" type="button" data-action="mobile-evidence-refresh">Обновить</button></div><div class="panel-body"><div class="teleop-actions" role="status"><span class="badge ${live.camera_fresh ? "good" : "warn"}">${live.camera_fresh ? "Кадры ROSMASTER поступают" : "Свежий кадр ROSMASTER не подтверждён"}</span><span class="badge ${live.recording_active ? "warn" : "neutral"}">${live.recording_active ? "Идёт запись" : "Запись не активна"}</span></div>${live.recording_active ? `<p class="form-description">${escape(live.recording_name || live.recording_id)} · кадров: ${integer(live.recorded_frames)} · телеметрия: ${integer(live.recorded_samples)}</p>` : live.last_recording_id ? `<p class="form-description">Последний показ: ${escape(lastLabel)}.</p>` : ""}<p class="form-hint">Используйте общий вид ROSMASTER, который остаётся рядом с ${project === "03" ? "leader и записью" : "клавиатурой, gamepad и записью"}. Его свежесть определяется реально полученными JPEG-кадрами.</p><dl class="summary-list"><div><dt>Перенесено записей</dt><dd>${integer(counts.imported)}</dd></div><div><dt>Ожидают переноса</dt><dd>${integer(counts.pending_imports || 0)}</dd></div><div><dt>Кадров сохранено</dt><dd>${integer(counts.raw_frames_recorded)}</dd></div><div><dt>Допущено к обучению</dt><dd>${integer(counts.admitted_for_training)}</dd></div><div><dt>Остаются для анализа</dt><dd>${integer(counts.observation_or_rejected)}</dd></div></dl>${payload.error ? `<p class="form-error">${escape(payload.error)}</p>` : ""}${rows || '<p class="form-description">Перенесённых показов пока нет. Отдельное видео не заменяет синхронный эпизод мобильного навыка.</p>'}<p class="form-hint">Новые модели сначала проходят офлайн-проверку или shadow без движения. Автоматического физического запуска здесь нет.</p></div></section>`;
  }
  function createMobileEvidenceClient(options) {
    let data = null, error = null, busy = false;
    async function refresh() {
      if (busy) return data;
      busy = true;
      try { data = await options.fetchEvidence(); error = null; }
      catch (failure) { error = failure.message || "Не удалось проверить импортированные записи."; }
      finally { busy = false; if (options.onChange) options.onChange({ data, error, busy }); }
      return data;
    }
    return { refresh, snapshot: () => ({ ...(data || {}), error, loading: busy }) };
  }
  const exported = { mobileEvidencePanel, liveMobileEvidence, mobileReason, sensorEvidenceTable, createMobileEvidenceClient };
  Object.assign(surface, exported);
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
