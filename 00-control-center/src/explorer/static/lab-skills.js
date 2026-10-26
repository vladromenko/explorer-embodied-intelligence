"use strict";
// ENGINEERING_GLUE: SO101 source metadata, original-file review and offline jobs.
// No station/control/recording/device endpoint is reachable from this module.
(function (surface) {
  const sourceProject = typeof SO101_SOURCE_PROJECT !== "undefined" ? SO101_SOURCE_PROJECT : (typeof module !== "undefined" && module.exports ? require("./experiment-registry.js").SO101_SOURCE_PROJECT : null);
  const base = "/api/lab/skills";
  const array = value => Array.isArray(value) ? value : [];
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const splitTitle = { train: "Обучение", development: "Отдельная проверка", sealed_test: "Закрытый тест · не обучать" };
  const phaseTitle = { observe_select: "Наблюдение и выбор", approach: "Подход", grasp: "Захват", lift_verify: "Подъём и удержание", transport: "Перенос", place_release: "Размещение и отпускание", retreat: "Отвод и проверка" };
  const id = value => encodeURIComponent(String(value));
  const copy = value => JSON.parse(JSON.stringify(value));
  function createLabSkillsClient(options) {
    if (typeof options?.api !== "function") throw new Error("Authenticated application API callback required");
    let data = { catalogue: null, skills: [], imports: [], selected: "", version: 1, state: null, readiness: null, loading: false, busy: false, error: "", notice: "", captureContext: null, review: null, reviewImport: "", reviewFrame: 0 };
    let generation = 0, closed = false, autoSelect = true;
    const snapshot = () => copy(data);
    const emit = () => { if (!closed && options.onChange) options.onChange(snapshot()); };
    const route = () => `${base}/${id(data.selected)}`;
    const current = token => !closed && token === generation;
    async function refresh() {
      const token = ++generation; data.loading = true; emit();
      try {
        const [catalogue, listing, imports] = await Promise.all([options.api(`${base}/catalogue`), options.api(base), options.api(`${base}/imports`)]);
        if (!current(token)) return snapshot();
        data.catalogue = catalogue; data.skills = array(listing.skills); data.imports = array(imports.imports);
        if (autoSelect && !data.selected && data.skills.length) {
          const chosen = data.skills[data.skills.length - 1]; data.selected = chosen.spec.skill_id; data.version = chosen.spec.version;
        }
        if (data.selected) {
          const selected = data.selected, version = data.version;
          const [state, readiness] = await Promise.all([options.api(`${base}/${id(selected)}?version=${version}`), options.api(`${base}/${id(selected)}/readiness?version=${version}`)]);
          if (!current(token) || selected !== data.selected || version !== data.version) return snapshot();
          data.state = state; data.readiness = readiness;
        } else { data.state = null; data.readiness = null; }
        data.error = "";
      } catch (error) { if (current(token)) data.error = error.message || "Не удалось получить данные навыка"; }
      finally { if (current(token)) { data.loading = false; emit(); } }
      return snapshot();
    }
    async function select(skill, version) {
      if (closed) return snapshot();
      if (skill && (!Number.isInteger(version) || version < 1)) throw new Error("Select an explicit skill version");
      ++generation; autoSelect = false; data.selected = skill; data.version = version || 1; data.state = null; data.readiness = null; data.captureContext = null; data.review = null; data.reviewImport = "";
      return refresh();
    }
    async function mutation(path, body, after) {
      if (closed || data.busy) throw new Error("Предыдущее действие ещё выполняется");
      if (!path.startsWith(`${base}/`) && path !== base) throw new Error("Only lab metadata routes are allowed");
      data.busy = true; data.error = ""; emit();
      try {
        const result = await options.api(path, { method: "POST", body: JSON.stringify(body) });
        if (!closed && after) after(result);
        if (!closed) await refresh();
        return result;
      } catch (error) { if (!closed) data.error = error.message; throw error; }
      finally { data.busy = false; emit(); }
    }
    function selectedBody(body) {
      if (!data.selected || !data.state) throw new Error("Сначала выберите навык и версию");
      return { ...body, version: data.version };
    }
    function act(operation, body = {}, after) { return mutation(`${route()}/${operation}`, selectedBody(body), after); }
    const client = {
      snapshot, refresh, select,
      propose: body => mutation(base, body, result => {
        if (result.spec?.owner_experiment === sourceProject) { data.selected = result.spec.skill_id; data.version = result.spec.version; data.state = null; }
        else data.notice = `Предложение относится к эксперименту ${result.spec?.owner_experiment || "—"}; обучение SO101 здесь не запускается.`;
      }),
      revise: patch => act("revision", { patch, spec_sha256: data.state?.spec_sha256 }, result => { data.version = result.spec.version; data.state = null; }),
      confirm: () => act("confirm", { spec_sha256: data.state?.spec_sha256 }),
      createSession: body => act("session", body),
      captureContext: body => act("capture-context", body, result => {
        data.captureContext = result; data.notice = "Контекст показа подготовлен. Запись и управление запускаются отдельно.";
        if (options.onCaptureContext) options.onCaptureContext(copy(result));
      }),
      bind: body => act("bind", body), review: body => act("review", body),
      aggregate: () => act("aggregate"),
      train: body => act("train", body),
      async loadReview(job, after = 0) {
        const token = ++generation; data.loading = true; emit();
        try {
          const review = await options.api(`${base}/imports/${id(job)}/review?after=${after}&limit=200`);
          if (current(token)) { data.review = review; data.reviewImport = job; data.reviewFrame = review.rows?.[0]?.source_sample_index || 0; data.error = ""; }
        } catch (error) { if (current(token)) data.error = error.message; throw error; }
        finally { if (current(token)) { data.loading = false; emit(); } }
      },
      setReviewFrame(index) {
        if (!Number.isInteger(index) || !data.review || !array(data.review.rows).some(row => row.source_sample_index === index)) throw new Error("Выберите исходный кадр текущей страницы");
        data.reviewFrame = index; emit();
      },
      dispose() { closed = true; ++generation; }
    };
    return client;
  }
  function timelinePanel(data) {
    const review = data.review; if (!review) return "";
    const rows = array(review.rows), selected = rows.find(row => row.source_sample_index === data.reviewFrame) || rows[0];
    if (!selected) return '<p class="form-hint">В этой части записи нет кадров.</p>';
    const values = row => array(row).map(value => typeof value === "number" ? value.toFixed(2) : "—").join(" · ");
    return `<details open><summary>Исходное видео и суставная временная шкала · ${esc(review.total_samples)} кадров</summary><img class="camera-preview" src="${base}/imports/${id(data.reviewImport)}/frame/${selected.source_sample_index}" alt="Исходный кадр показа, без нового захвата камеры"><label>Исходный кадр <input type="range" min="${rows[0].source_sample_index}" max="${rows[rows.length - 1].source_sample_index}" value="${selected.source_sample_index}" data-lab-frame></label><p>Кадр ${selected.source_sample_index} · измеренное состояние перед этой командой</p><div class="table-wrap"><table><tbody>${[["Измерено у лидера", selected.leader_state], ["Запрошено", selected.requested_action], ["Принято", selected.accepted_action], ["Отправлено", selected.dispatched_action], ["Измерено у последователя", selected.state]].map(([label, row]) => `<tr><th scope="row">${label}</th><td>${esc(values(row))}</td></tr>`).join("")}</tbody></table></div><p class="form-hint">${esc(array(review.action_names).join(" · "))}. Это исходные нормализованные координаты, не градусы. Host-write не подтверждает достижение, контакт или успех модели.</p><details><summary>Камера, часы и контрольные суммы</summary><p>Камера: ${esc(array(review.camera_ids).join(", "))} · boot: ${esc(review.source_boot_id)}</p><p>Кадр: ${esc(selected.frame_id)} · SHA: ${esc(selected.frame_sha256)}</p><p>frame/state/dispatch ns: ${esc(selected.frame_metadata?.monotonic_ns)} / ${esc(selected.state_monotonic_ns)} / ${esc(selected.action_monotonic_ns)}</p><p>Моторные калибровки: ${esc(JSON.stringify(review.motor_calibration))}. Метрическая калибровка камеры: ${review.camera_calibration ? "записана" : "не записана"}.</p></details>${review.next_after != null ? `<button type="button" class="button secondary" data-lab-action="review-next" data-after="${review.next_after}">Следующие исходные кадры</button>` : ""}</details>`;
  }
  function labSkillsPanel(data = {}) {
    const state = data.state, spec = state?.spec, busy = data.busy ? "disabled" : "", ready = data.readiness || {};
    const option = (value, label, selected = false) => `<option value="${esc(value)}" ${selected ? "selected" : ""}>${esc(label)}</option>`;
    const variants = array(spec?.demo_plan?.variants), assignments = array(state?.assignments), sessions = array(state?.sessions);
    const variantsOptions = variants.map(row => option(row.variant_id, `${row.title} · ${splitTitle[row.split]}`)).join("");
    const sessionOptions = sessions.map(row => option(row.session_id, `${row.scene_id} · ${splitTitle[row.split]}`)).join("");
    const sourceOptions = array(data.imports).filter(row => row.status === "completed").map(row => option(row.import_job_id, `Показ ${row.original_episode_id || row.import_job_id}`)).join("");
    const creation = `<details ${spec ? "" : "open"}><summary>Предложить новый навык</summary><form data-lab-form="propose"><label>Шаблон<select name="template_id">${array(data.catalogue?.templates).map(row => option(row.template_id, row.title)).join("")}<option value="custom">Своё описание</option></select></label><label>Своё описание<textarea name="text" maxlength="600" placeholder="Взять конфетку и положить в коробку"></textarea></label><button class="button secondary" ${busy}>Предложить описание</button></form></details>`;
    let body = "";
    if (spec) {
      const editor = `<details ${state.confirmed ? "" : "open"}><summary>${state.confirmed ? "Подтверждённое описание" : "Проверьте описание"} · версия ${spec.version}</summary><form data-lab-form="revise"><label>Название<input name="title" maxlength="100" required value="${esc(spec.title)}"></label><label>Предмет<input name="object" maxlength="240" required value="${esc(spec.object?.query)}"></label><label>Цель<input name="goal" maxlength="240" required value="${esc(spec.goal?.query)}"></label><label>Наблюдаемый полный успех<textarea name="success" required>${esc(spec.success?.description)}</textarea></label><button class="button secondary" ${busy}>Сохранить новую версию</button></form>${!state.confirmed ? `<button type="button" class="button primary" data-lab-action="confirm" ${busy}>Описание верно · подтвердить</button>` : ""}<p class="form-hint">Текст — описание отдельного навыка, а не языковая команда ACT. Изменение создаёт новую версию.</p></details>`;
      const coverage = `<p>Принято целых вариантов: ${esc(state.plan?.covered_variants)} / ${esc(state.plan?.planned_variants)} · оригиналов: ${esc(state.counts?.accepted_originals)} / ${esc(state.counts?.assigned_originals)}</p><p class="form-hint">${esc(state.next_action?.reason)}</p><ul class="plain-list">${variants.map(row => `<li>${esc(row.title)} · ${esc(splitTitle[row.split])} · принято ${esc(state.plan?.whole_episode_counts?.[row.variant_id] || 0)}</li>`).join("")}</ul>`;
      const teaching = `<details><summary>Подготовить условия следующего показа</summary><form data-lab-form="session"><label>Новое имя сцены<input name="scene_id" required maxlength="160"></label><label>Выборка<select name="split">${Object.entries(splitTitle).map(([key, title]) => option(key, title)).join("")}</select></label><button class="button secondary" ${busy || !state.confirmed ? "disabled" : ""}>Создать сессию показа</button></form><form data-lab-form="capture-context"><label>Вариант<select name="variant_id">${variantsOptions}</select></label><label>Исходная сессия<select name="session_id">${sessionOptions}</select></label><label>Экземпляр предмета<input name="object_instance_id" required maxlength="160" placeholder="Уникальный предмет для этой выборки"></label><button class="button secondary" ${busy || !sessions.length ? "disabled" : ""}>Подготовить контекст записи</button></form><p class="form-hint">Подготовка сохраняет связь навыка, версии и условий. Запись и наблюдаемое управление выполняются отдельно. Закрытые сцены, сессии, предметы и кадры не используются для подбора модели.</p>${data.captureContext ? '<p role="status">Контекст подготовлен для существующей панели записи.</p>' : ""}</details>`;
      const bindings = `<details><summary>Привязать и разобрать исходный показ</summary><form data-lab-form="bind"><label>Перенесённый оригинал<select name="import_job_id">${sourceOptions}</select></label><label>Вариант<select name="variant_id">${variantsOptions}</select></label><label>Исходная сессия<select name="session_id">${sessionOptions}</select></label><button class="button secondary" ${busy || !sourceOptions || !sessions.length ? "disabled" : ""}>Привязать оригинал</button></form><div>${assignments.map(row => `<article class="recording-row"><h3>Показ ${esc(row.binding.original_episode_id)}</h3><p>${row.accepted ? "Принят для BC" : "Для анализа"} · ${esc(splitTitle[row.binding.split])}</p><ul>${array(row.reasons).map(reason => `<li>${esc(reason)}</li>`).join("")}</ul><button type="button" class="button secondary" data-lab-action="source-review" data-import="${esc(row.binding.import_job_id)}">Открыть исходное видео и суставы</button><form data-lab-form="review" data-episode="${esc(row.binding.original_episode_id)}"><label>Оператор разбора<input name="reviewed_by" required maxlength="160" value="${esc(row.review?.reviewed_by || "")}"></label><label>Результат<select name="outcome">${["unknown", "failure", "success"].map(value => option(value, { unknown: "Не определён", failure: "Неудача", success: "Полный успех" }[value], (row.review?.outcome || "unknown") === value)).join("")}</select></label>${[["observed_recording", "Я просмотрел исходное видео"], ["object_and_goal_visible", "Предмет и цель различимы"], ["full_task_confirmed", "Подтверждаю итог всей задачи"]].map(([name, label]) => `<label><input type="checkbox" name="${name}" ${row.review?.[name] === true ? "checked" : ""}>${label}</label>`).join("")}<div class="table-wrap"><table><thead><tr><th>Фаза</th><th>Начальный кадр</th><th>Конечный кадр</th><th>Исход</th></tr></thead><tbody>${array(spec.phase_ids).map(phase => `<tr><th>${esc(phaseTitle[phase] || phase)}</th><td><input name="start:${esc(phase)}" type="number" min="0" step="1" required value="${esc(row.review?.phase_intervals?.[phase]?.[0] ?? "")}"></td><td><input name="end:${esc(phase)}" type="number" min="0" step="1" required value="${esc(row.review?.phase_intervals?.[phase]?.[1] ?? "")}"></td><td><select name="outcome:${esc(phase)}">${["unknown", "failure", "success"].map(value => option(value, { unknown: "Не определён", failure: "Неудача", success: "Успех" }[value], (row.review?.phase_outcomes?.[phase] || "unknown") === value)).join("")}</select></td></tr>`).join("")}</tbody></table></div><button class="button secondary" ${busy}>Сохранить разбор показа</button></form></article>`).join("")}</div>${timelinePanel(data)}</details>`;
      const training = `<details open><summary>Набор данных и офлайн-обучение</summary><div class="teleop-actions">${[["demos", "Данные"], ["trained", "Модель"], ["shadow", "Shadow"], ["physical", "Наблюдаемый физический тест"]].map(([key, label]) => `<span class="badge ${ready[key]?.ready ? "good" : "warn"}">${label}: ${ready[key]?.ready ? "проверено" : "не подтверждено"}</span>`).join("")}</div><p class="form-hint">Операторский успех относится к показу. Offline chunk error не подтверждает успешное исполнение модели. Новая модель не двигает руки автоматически.</p><button type="button" class="button secondary" data-lab-action="aggregate" ${busy || !state.data_ready ? "disabled" : ""}>Собрать все принятые исходные показы</button><form data-lab-form="train"><label>Шагов ACT на CPU<input name="act_steps" type="number" min="1" max="20000" step="1" value="120" required></label><button class="button primary" ${busy || !state.train_ready ? "disabled" : ""}>Поставить офлайн-обучение в очередь</button></form>${array(state.training_jobs).map(job => `<p>Обучение ${esc(job.id)} · ${esc(job.status)} · ${esc(job.result?.phase || job.result?.status || "")}</p>`).join("")}</details>`;
      body = editor + coverage + teaching + bindings + training;
    }
    return `<section class="panel lab-skills-panel"><div class="panel-heading"><div><p class="eyebrow">SO101 / Показ → обучение → отдельная проверка</p><h2>Навык двух рук SO101</h2></div><button type="button" class="quiet-button text-link" data-lab-action="refresh">Обновить</button></div><div class="panel-body">${data.error ? `<p class="form-error" role="alert">${esc(data.error)}</p>` : ""}${data.notice ? `<p role="status">${esc(data.notice)}</p>` : ""}<label>Навык и версия<select data-lab-selection ${busy}><option value="">Новый навык</option>${array(data.skills).map(row => option(`${row.spec.skill_id}:${row.spec.version}`, `${row.spec.title} · v${row.spec.version}`, row.spec.skill_id === data.selected && row.spec.version === data.version)).join("")}</select></label>${creation}${body}${!spec && data.loading ? '<p role="status">Получаем данные навыка…</p>' : ""}</div></section>`;
  }
  function installLabSkillsHandlers(root, client, options = {}) {
    const fail = error => { if (options.onError) options.onError(error); };
    const run = operation => { Promise.resolve().then(operation).catch(fail); };
    function click(event) {
      const button = event.target.closest?.("[data-lab-action]"); if (!button || !root.contains(button)) return;
      event.preventDefault(); const action = button.dataset.labAction;
      run(() => ({ refresh: () => client.refresh(), confirm: () => client.confirm(), aggregate: () => client.aggregate(),
        "source-review": () => client.loadReview(button.dataset.import), "review-next": () => client.loadReview(client.snapshot().reviewImport, Number(button.dataset.after)) }[action]?.()));
    }
    function change(event) {
      if (event.target.matches?.("[data-lab-selection]")) { const [skill, version] = event.target.value.split(":"); run(() => client.select(skill, Number(version) || 1)); }
      if (event.target.matches?.("[data-lab-frame]")) run(() => client.setReviewFrame(Number(event.target.value)));
    }
    function submit(event) {
      const form = event.target.closest?.("[data-lab-form]"); if (!form || !root.contains(form)) return;
      event.preventDefault(); const fields = new FormData(form), values = Object.fromEntries(fields), state = client.snapshot().state;
      run(() => {
        switch (form.dataset.labForm) {
          case "propose": return client.propose(values.template_id === "custom" ? { text: values.text } : { template_id: values.template_id });
          case "revise": return client.revise({ title: values.title, object: { ...state.spec.object, query: values.object }, goal: { ...state.spec.goal, query: values.goal }, success: { ...state.spec.success, description: values.success } });
          case "session": return client.createSession(values);
          case "capture-context": return client.captureContext(values);
          case "bind": {
            const old = array(state.assignments).find(row => row.binding.import_job_id === values.import_job_id);
            return client.bind({ ...values, ...(old ? { expected_binding_sha256: old.binding.receipt_sha256 } : {}) });
          }
          case "review": {
            const old = array(state.assignments).find(row => row.binding.original_episode_id === form.dataset.episode);
            const phase_intervals = {}, phase_outcomes = {};
            for (const phase of state.spec.phase_ids) {
              const interval = [values[`start:${phase}`], values[`end:${phase}`]];
              if (interval.some(value => typeof value !== "string" || !value.trim() || !Number.isInteger(Number(value)) || Number(value) < 0)) throw new Error("Укажите настоящие индексы исходных кадров каждой фазы");
              phase_intervals[phase] = interval.map(Number); phase_outcomes[phase] = values[`outcome:${phase}`];
            }
            return client.review({ original_episode_id: form.dataset.episode, binding_sha256: old.binding.receipt_sha256,
              ...(old.review ? { expected_review_sha256: old.review.receipt_sha256 } : {}), reviewed_by: values.reviewed_by, outcome: values.outcome,
              observed_recording: fields.has("observed_recording"), object_and_goal_visible: fields.has("object_and_goal_visible"), full_task_confirmed: fields.has("full_task_confirmed"), phase_intervals, phase_outcomes });
          }
          case "train": return client.train({ act_steps: Number(values.act_steps), request_id: `lab-ui-${Date.now()}-${Math.random().toString(36).slice(2)}` });
          default: throw new Error("Unknown lab metadata form");
        }
      });
    }
    root.addEventListener("click", click); root.addEventListener("change", change); root.addEventListener("submit", submit);
    return () => { root.removeEventListener("click", click); root.removeEventListener("change", change); root.removeEventListener("submit", submit); };
  }
  const exported = { createLabSkillsClient, labSkillsPanel, installLabSkillsHandlers, labSkillsTimelinePanel: timelinePanel };
  Object.assign(surface, exported);
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
