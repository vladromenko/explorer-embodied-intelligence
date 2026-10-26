"use strict";
// ENGINEERING_GLUE: one read-only queue view plus explicit CAS scheduling/operation forms.
(function (surface) {
  const rows = value => Array.isArray(value) ? value : [];
  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? "").replace(/[&<>"']/g,c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const GiB = 1024 ** 3;
  const terminal = status => ["completed","failed","cancelled"].includes(status);
  const physical = job => ["station_rollout","handover_physical"].includes(job?.kind);
  const pausable = job => !physical(job) && ["sim_pilot","train_act","rosmaster_skill_train","so101_skill_train","world_model","dagger","skill_cycle","manipulation","manipulation_student","manipulation_benchmark","manipulation_curriculum","mobile_manipulation","manipulation_selective_repair","policy_transfer","fault_repair"].includes(job?.kind);
  const statusLabels = {queued:"В очереди",running:"Выполняется",pause_requested:"Запрошена пауза",paused:"Приостановлено",resume_requested:"Запрошено возобновление",cancel_requested:"Запрошена отмена",completed:"Завершено",failed:"Ошибка",cancelled:"Отменено"};
  const reasonLabels = {scheduled_time_not_reached:"Ожидается заданное время",cpu_concurrency_budget_exhausted:"Резерв CPU занят",job_CPU_budget_exceeds_host_worker_pool:"Запрошено больше CPU, чем есть в worker pool",single_ingest_disk_network_budget_occupied:"Занят слот переноса данных",single_interactive_inference_budget_occupied:"Занят слот интерактивного inference",single_heavy_GPU_or_inference_slot_occupied:"GPU lease уже занят, включая сохранённый SIGSTOP-процесс",available_UMA_memory_below_reserve_plus_budgets:"Недостаточно общей UMA-памяти с учётом runtime reserve",job_memory_budget_exceeds_host_UMA_capacity:"Бюджет памяти превышает ёмкость хоста с резервом",disk_reserve_plus_job_budget_unavailable:"Недостаточно диска с учётом резерва",job_disk_budget_exceeds_host_storage_capacity:"Бюджет диска превышает ёмкость хранилища",host_cpu_reserve_busy:"Нагрузка CPU не оставляет резерв интерфейсу и камерам",no_active_source_bound_checkpoint_phase:"Текущий этап не поддерживает подтверждённую checkpoint-паузу",optimizer_finished_before_pause_poll:"Оптимизатор завершился до принятия запроса паузы"};
  function reason(value) {
    if (reasonLabels[value]) return reasonLabels[value];
    const [code,...parts] = String(value).split(":");
    const known = {dependency_waiting:"Ожидается завершение зависимости",dependency_failed:"Зависимость завершилась ошибкой",dependency_cancelled:"Зависимость отменена",dependency_missing:"Исходная зависимость не найдена",near_existing_thermal_trip:"Температура близка к действующему аппаратному порогу"};
    return known[code] ? known[code]+": "+parts.join(":") : String(value);
  }
  function bytes(value) { return typeof value === "number" && Number.isFinite(value) ? `${(value/GiB).toFixed(2)} GiB` : "не измерено"; }
  function localTime(seconds) {
    if (seconds == null) return "";const date = new Date(seconds*1000);
    if (!Number.isFinite(date.getTime())) return "";
    const pad = number => String(number).padStart(2,"0");
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }
  function draftFor(metadata) { return {priority:String(metadata.priority),preemptible:metadata.preemptible === true,dependencies:rows(metadata.dependencies).slice(),notBefore:localTime(metadata.not_before),cpuCores:String(metadata.resources?.cpu_cores ?? ""),memoryGiB:typeof metadata.resources?.memory_bytes === "number" ? String(metadata.resources.memory_bytes/GiB) : "",diskGiB:typeof metadata.resources?.disk_bytes === "number" ? String(metadata.resources.disk_bytes/GiB) : ""}; }
  function optionsFor(draft,base,job) {
    const original = draftFor(base), options = {};
    const integer = (value,name) => {if (typeof value !== "string" || !/^-?\d+$/.test(value.trim())) throw new Error(name+": нужно целое число");const number = Number(value);if (!Number.isSafeInteger(number)) throw new Error(name+": число вне диапазона");return number;};
    const priority = integer(draft.priority,"Приоритет");if (priority < -10 || priority > 10) throw new Error("Приоритет должен быть от −10 до 10");
    if (priority !== base.priority) options.priority = priority;
    if (typeof draft.preemptible !== "boolean") throw new Error("Нужен явный выбор возможности прерывания");
    if (physical(job) && draft.preemptible) throw new Error("Физическая попытка не становится прерываемым обучением");
    if (draft.preemptible !== base.preemptible) options.preemptible = draft.preemptible;
    if (!Array.isArray(draft.dependencies) || draft.dependencies.length > 32 || new Set(draft.dependencies).size !== draft.dependencies.length || draft.dependencies.some(id => typeof id !== "string" || !id || id === job.id)) throw new Error("Выберите разные существующие зависимости; задача не зависит от себя");
    if (JSON.stringify([...draft.dependencies].sort()) !== JSON.stringify([...rows(base.dependencies)].sort())) options.dependencies = draft.dependencies.slice();
    if (draft.notBefore !== original.notBefore) {
      const value = draft.notBefore === "" ? null : new Date(draft.notBefore).getTime()/1000;
      if (value !== null && (!Number.isFinite(value) || value < 0)) throw new Error("Укажите корректное время планирования или оставьте поле пустым");
      options.not_before = value;
    }
    const resources = {};
    for (const [field,name] of [["cpuCores","cpu_cores"],["memoryGiB","memory_bytes"],["diskGiB","disk_bytes"]]) {
      if (draft[field] !== original[field]) {
        const value = name === "cpu_cores" ? integer(draft[field],"CPU") : Math.round(Number(draft[field])*GiB);
        if (!String(draft[field]).trim() || !Number.isSafeInteger(value) || value <= 0) throw new Error("CPU, память и диск должны быть конечными положительными бюджетами");
        if (job.minimum_resources?.[name] != null && value < job.minimum_resources[name]) throw new Error("Бюджет ниже объявленного сервером минимума");
        resources[name] = value;
      }
    }
    if (Object.keys(resources).length) options.resources = resources;
    if (job.status !== "queued" && ["dependencies","not_before","resources"].some(field => Object.hasOwn(options,field))) throw new Error("Задание уже активно: сейчас изменяются только приоритет и возможность прерывания. Обновите основу черновика");
    return options;
  }
  function createSharedQueueClient(options) {
    if (typeof options?.get !== "function" || typeof options?.post !== "function") throw new Error("Нужен доверенный HTTP-клиент очереди");
    let data = null, error = "", selected = "", base = null, revision = null, draft = null, readSequence = 0, generation = 0, saving = false, editGeneration = 0, draftDirty = false, editsRetainedAfterSave = false;
    const operations = {}, cancelled = {};
    const notify = () => options.onChange?.();
    function snapshot() { const current = rows(data?.jobs).find(row => row.id === selected);return copy({data,error,selected,base,revision,draft,saving,operations,cancelled,editsRetainedAfterSave,conflict:Boolean(current && revision && current.scheduling?.revision !== revision)}); }
    function job(id = selected) { const current = rows(data?.jobs).find(row => row.id === id);if (!current) throw new Error("Задача не найдена в текущем серверном состоянии");return current; }
    async function refresh() {
      const sequence = ++readSequence, epoch = generation;
      try { const value = await options.get("/api/queue");if (sequence !== readSequence || epoch !== generation) return;data = copy(value);error = ""; }
      catch (failure) {if (sequence === readSequence && epoch === generation) error = failure.message;}
      notify();return snapshot();
    }
    function selectJob(id) { const current = job(id);if (!current.scheduling?.metadata || !/^[a-f0-9]{64}$/.test(current.scheduling?.revision || "")) throw new Error("Сервер не предоставил проверяемую ревизию расписания");selected = id;base = copy(current.scheduling.metadata);revision = current.scheduling.revision;draft = draftFor(base);draftDirty = false;editsRetainedAfterSave = false;editGeneration++;error = "";notify(); }
    function reloadDraft() { if (!selected) throw new Error("Выберите задачу");selectJob(selected); }
    function edit(field,value) { if (!draft || !Object.hasOwn(draft,field)) throw new Error("Выберите разрешённое поле расписания");draft[field] = copy(value);draftDirty = true;editGeneration++; }
    async function save() {
      const current = job();if (terminal(current.status) || cancelled[current.id]) throw new Error("Расписание завершённой или отменяемой задачи не меняется");
      if (saving) throw new Error("Дождитесь сохранения расписания");
      const changes = optionsFor(draft,base,current);if (!Object.keys(changes).length) throw new Error("Изменений расписания нет");
      const id = current.id, pinnedRevision = revision, sentEditGeneration = editGeneration, epoch = ++generation;readSequence++;saving = true;error = "";notify();
      try {
        const response = await options.post(`/api/jobs/${encodeURIComponent(id)}/schedule`,{options:changes,expected_revision:pinnedRevision});
        if (epoch !== generation || cancelled[id]) return {ignored:true};
        readSequence++;
        if (selected === id) {
          const retain = draftDirty && editGeneration !== sentEditGeneration;
          base = copy(response.metadata);revision = response.revision;
          if (!retain) {draft = draftFor(base);draftDirty = false;}
          editsRetainedAfterSave = retain;
        }
        await refresh();return response;
      } catch (failure) {if (epoch === generation) error = failure.message;throw failure;}
      finally {saving = false;notify();}
    }
    async function operation(id,action) {
      const current = job(id);
      if (!["pause","resume","cancel"].includes(action) || terminal(current.status)) throw new Error("Для этой задачи операция недоступна");
      if (action !== "cancel" && (cancelled[id] || current.status === "cancel_requested")) throw new Error("После запроса отмены пауза и возобновление запрещены");
      if (action !== "cancel" && !pausable(current)) throw new Error("Физическая или интерактивная попытка требует нового наблюдаемого запуска, без pause/resume");
      if (action === "resume" && current.status !== "paused") throw new Error("Возобновление доступно только после подтверждённой паузы");
      if (action === "pause" && !["queued","running","resume_requested"].includes(current.status)) throw new Error("Пауза уже запрошена или недоступна");
      if (operations[id] && action !== "cancel") throw new Error("Дождитесь текущей операции; отмена остаётся доступна");
      const token = (operations[id]?.token || 0)+1, epoch = ++generation;readSequence++;
      operations[id] = {action,token};if (action === "cancel") cancelled[id] = true;error = "";notify();
      try {
        const response = await options.post(`/api/jobs/${encodeURIComponent(id)}/${action}`,{});
        if (operations[id]?.token !== token || operations[id]?.action !== action) return {ignored:true};
        readSequence++;
        if (data) data.jobs = rows(data.jobs).map(row => row.id === id ? {...row,...copy(response)} : row);
        await refresh();return response;
      } catch (failure) {if (operations[id]?.token === token && epoch === generation) error = action === "cancel" ? "Отмена запрошена, результат сервера неизвестен: "+failure.message : failure.message;throw failure;}
      finally {if (operations[id]?.token === token && operations[id]?.action === action) delete operations[id];notify();}
    }
    return {snapshot,refresh,selectJob,reloadDraft,edit,save,operation};
  }
  function pauseFacts(job) {
    const runtime = job.scheduling?.runtime || {}, phase = runtime.phase, receipt = runtime.checkpoint_pause_receipt;
    const receiptBound = receipt?.optimizer_RNG_sampler_durably_saved === true && /^[a-f0-9]{64}$/.test(receipt.checkpoint_sha256 || "") && /^[a-f0-9]{64}$/.test(receipt.checkpoint_pointer_sha256 || "") && [75,-999].includes(runtime.process_exit_code);
    if (["paused_checkpoint_process_exited","preempted_checkpoint_waiting"].includes(phase) && runtime.memory_retained === false && runtime.exact_optimizer_resume === true && receiptBound) return {kind:"checkpoint",text:phase === "preempted_checkpoint_waiting" ? "Проверенный checkpoint сохранён, процесс завершён; задача уступила ресурс и ждёт очереди возобновления." : "Проверенный checkpoint сохранён, процесс завершён; память процесса освобождена.",detail:"Точное продолжение связано с model/optimizer/RNG/sampler/dataset состояниями. Глобальная доступная UMA измеряется отдельно."};
    if (phase === "paused_process_memory_retained" || runtime.pause_mode === "SIGSTOP_no_exact_resume") return {kind:"retained",text:"SIGSTOP: процесс приостановлен, память и GPU lease удерживаются.",detail:"Это не освобождение RAM и не подтверждённое точное восстановление optimizer после выхода процесса."};
    if (phase === "waiting_for_actual_optimizer_checkpoint" || job.status === "pause_requested") return {kind:"pending",text:"Пауза запрошена; ожидаются настоящий checkpoint и подтверждение завершения процесса.",detail:runtime.memory_retained === false ? "Смотрите подтверждённую фазу процесса; сохранение ещё не доказано." : "До завершения процесса его память остаётся занятой."};
    if (phase === "paused_before_work") return {kind:"before",text:"Пауза до начала работы; процесс завершён до импорта задачи.",detail:"Это не выполненное обучение и не optimizer checkpoint."};
    return {kind:"other",text:phase || "Фаза процесса не подтверждена",detail:runtime.preemption_blocked_reason ? reason(runtime.preemption_blocked_reason) : "Готовность exact resume не выводится из одного статуса paused."};
  }
  function sharedQueuePanel(view = {},knownjobs = []) {
    const data = view.data, plan = data?.plan_preview || {}, host = data?.resources || {}, jobs = rows(data?.jobs), draft = view.draft;
    const known = new Map(rows(knownjobs).map(job => [job.id,job]));for (const row of jobs) known.set(row.id,{...known.get(row.id),...row});
    const label = job => `${job.project_id || "?"} · ${job.name || job.title || job.kind || "задача"}${job.scheduling?.metadata?.skill_version ? " · навык v"+job.scheduling.metadata.skill_version : ""} · ${statusLabels[job.status] || job.status || "неизвестно"} · ${String(job.id).slice(0,8)}`;
    const value = number => typeof number === "number" && Number.isFinite(number) ? String(number) : "не измерено";
    const button = (action,title,id,disabled = false) => `<button class="button ${action === "cancel" ? "danger" : "secondary"}" type="button" data-queue-action="${esc(action)}" ${id ? `data-queue-job="${esc(id)}"` : ""} ${disabled ? "disabled" : ""}>${esc(title)}</button>`;
    const waiting = job => rows(plan.waiting?.[job.id]?.reasons || job.scheduling?.runtime?.wait_reasons);
    const cards = jobs.map(job => {
      const meta = job.scheduling?.metadata || {}, runtime = job.scheduling?.runtime || {}, reservation = meta.resources || {}, pause = pauseFacts(job), pending = view.operations?.[job.id];
      const requestedCancel = view.cancelled?.[job.id] || job.status === "cancel_requested";
      const actualUpdates = runtime.completed_updates;
      return `<article class="panel"><div class="panel-heading"><h3>${esc(label(job))}</h3></div><div class="panel-body"><p>${esc(statusLabels[job.status] || job.status)}${pending ? " · текущий запрос: "+esc(pending.action) : ""}${requestedCancel ? " · отмена запрошена, автоматическое возобновление заблокировано" : ""}</p><p>CPU: ${esc(value(reservation.cpu_cores))} · резерв UMA: ${esc(bytes(reservation.memory_bytes))} · диск: ${esc(bytes(reservation.disk_bytes))} · GPU slots: ${esc(value(reservation.gpu_slots))}</p><p>Приоритет: ${esc(meta.priority)} · ${meta.preemptible ? "может уступить ресурс при поддержке checkpoint" : "автоматическое прерывание не разрешено"}</p>${waiting(job).length ? `<ul class="form-error">${waiting(job).map(item => `<li>${esc(reason(item))}</li>`).join("")}</ul>` : plan.start?.includes(job.id) ? '<p class="form-hint">Кандидат на допуск в текущем прогнозе; это не факт запуска процесса.</p>' : ""}<div role="status" data-queue-pause="${pause.kind}"><p>${esc(pause.text)}</p><p class="form-hint">${esc(pause.detail)}</p>${typeof actualUpdates === "number" ? `<p>Сохранённые фактические optimizer updates: ${esc(actualUpdates)}</p>` : ""}</div><div class="teleop-actions">${button("edit","Настроить расписание",job.id,terminal(job.status) || requestedCancel)}${pausable(job) ? button("pause",job.status === "queued" ? "Отложить запуск" : "Запросить паузу",job.id,Boolean(pending) || requestedCancel || !["queued","running","resume_requested"].includes(job.status)) + button("resume","Возобновить",job.id,Boolean(pending) || requestedCancel || job.status !== "paused") : '<p class="form-hint">Pause/resume для этого класса недоступны; физическая попытка требует нового наблюдаемого запуска.</p>'}${button("cancel","Отменить задачу",job.id,terminal(job.status) || job.status === "cancel_requested")}</div><details><summary>Неизменяемая научная привязка и режим паузы</summary><p>Источник: ${esc(meta.experiment_slug || "неизвестен")} · backend: ${esc(meta.backend || "неизвестен")} · навык: ${esc(meta.skill_id || "не указан")} · версия: ${esc(meta.skill_version ?? "не указана")}</p><p>Dataset SHA: ${esc(meta.dataset_sha256 || "неизвестен; не восстановлен догадкой")}</p><p>Ревизия расписания: ${esc(job.scheduling?.revision)} · ${job.scheduling?.persisted ? "сохранена отдельно от config/result" : "read-only исходный прогноз; metadata ещё не создана"}</p><p>Память удерживается: ${runtime.memory_retained === true ? "да" : runtime.memory_retained === false ? "нет по свидетельству фазы" : "неизвестно"} · exact optimizer resume: ${runtime.exact_optimizer_resume === true ? "заявлен для текущего протокола; проверяйте подтверждённый checkpoint и выход выше" : runtime.exact_optimizer_resume === false ? "не подтверждён" : "неизвестно"} · auto-resume: ${runtime.auto_resume === true ? "разрешён scheduler" : "не разрешён"}</p>${runtime.preemption_blocked_reason ? `<p>${esc(reason(runtime.preemption_blocked_reason))}</p>` : ""}</details></div></article>`;
    }).join("");
    const selected = jobs.find(job => job.id === view.selected), locked = selected?.status !== "queued";
    const form = selected && draft ? `<section class="panel"><div class="panel-heading"><h3>Расписание: ${esc(label(selected))}</h3></div><form class="panel-body" data-queue-form><p>Сохраняется только scheduling metadata. Исходные config/result, dataset/model SHA, эксперимент и GPU slot не редактируются.</p>${view.editsRetainedAfterSave ? '<p class="form-hint">Отправленная версия сохранена; более новые локальные правки остались черновиком.</p>' : ""}${view.conflict ? '<p class="form-error">Серверная ревизия изменилась. Черновик сохранён; автоматической перезаписи нет. Обновите основу или получите отказ CAS при сохранении старой ревизии.</p>' : ""}<div class="form-grid"><label class="form-field">Приоритет · от −10 до 10<input type="number" min="-10" max="10" step="1" data-queue-field="priority" value="${esc(draft.priority)}"></label><label><input type="checkbox" data-queue-field="preemptible" ${draft.preemptible ? "checked" : ""} ${physical(selected) ? "disabled" : ""}> Разрешить уступить ресурс, если текущий этап поддерживает проверенный checkpoint</label><label class="form-field">Зависимости · существующие задачи<select multiple style="max-width:100%;min-width:0" data-queue-field="dependencies" ${locked ? "disabled" : ""}>${[...known.values()].filter(job => job.id !== selected.id).map(job => `<option value="${esc(job.id)}" ${rows(draft.dependencies).includes(job.id) ? "selected" : ""}>${esc(label(job))}</option>`).join("")}</select></label><label class="form-field">Не раньше · местное время (${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)})<input type="datetime-local" step="1" data-queue-field="notBefore" value="${esc(draft.notBefore)}" ${locked ? "disabled" : ""}></label>${[["cpuCores","CPU cores","1"],["memoryGiB","Бюджет UMA, GiB","0.25"],["diskGiB","Бюджет диска, GiB","0.25"]].map(([field,title,step]) => `<label class="form-field">${esc(title)}<input type="number" step="${step}" data-queue-field="${field}" value="${esc(draft[field])}" ${locked ? "disabled" : ""}></label>`).join("")}</div><p class="form-hint">${selected.minimum_resources ? `Серверный минимум: CPU ${esc(selected.minimum_resources.cpu_cores)}, UMA ${esc(bytes(selected.minimum_resources.memory_bytes))}, диск ${esc(bytes(selected.minimum_resources.disk_bytes))}.` : "Точный минимум проверяет сервер; он не выводится из выдуманных показателей модели."} GPU slots: ${esc(selected.scheduling?.metadata?.resources?.gpu_slots ?? "неизвестно")} · только чтение. Бюджет не является измеренным пиком или OS-изоляцией.</p>${locked ? '<p class="form-hint">Активная задача: меняются только приоритет и разрешение прерывания.</p>' : ""}<p>Основа сохранения: ${esc(view.revision)}</p><div class="teleop-actions"><button class="button primary" type="submit" ${view.saving || terminal(selected.status) || view.cancelled?.[selected.id] ? "disabled" : ""}>Сохранить расписание с проверкой ревизии</button>${button("reload-draft","Взять свежую основу вместо черновика",selected.id,view.saving)}</div></form></section>` : view.selected ? '<p class="form-hint">Выбранная задача вышла из активной очереди; завершённая история не редактируется.</p>' : "";
    const thermals = rows(host.thermal).length ? `<ul>${rows(host.thermal).map(zone => `<li>${esc(zone.name)}: ${esc(value(zone.temperature_c))}°C · действующий hot/critical trip: ${esc(zone.existing_hot_critical_trip_c ?? "не найден")}°C</li>`).join("")}</ul>` : '<p class="form-hint">Температурные показания не предоставлены; отсутствие данных не означает0°C.</p>';
    return `<section class="shared-queue" style="min-width:0;overflow-wrap:anywhere"><div class="panel-heading"><div><p class="eyebrow">Общая платформа обучения · 01–06</p><h2>Очередь, ресурсы и причины ожидания</h2></div>${button("refresh","Обновить без запуска")}</div>${view.error ? `<p role="alert" class="form-error">${esc(view.error)}</p>` : ""}<section class="panel"><div class="panel-body"><dl class="summary-list"><div><dt>CPU хоста / worker pool</dt><dd>${esc(value(host.cpu_count))} / ${esc(value(plan.cpu_capacity))}</dd></div><div><dt>CPU reserved / выбрано прогнозом</dt><dd>${esc(value(plan.cpu_reserved_or_selected))}</dd></div><div><dt>GPU lease reserved / прогноз</dt><dd>${esc(value(plan.gpu_reserved_or_selected))} из одного слота</dd></div><div><dt>Доступная UMA / всего</dt><dd>${esc(bytes(host.available_memory_bytes))} / ${esc(bytes(host.total_memory_bytes))}</dd></div><div><dt>Runtime reserve / остаток бюджета</dt><dd>${esc(bytes(plan.UMA_runtime_reserve_bytes))} / ${esc(bytes(plan.remaining_memory_budget_bytes))}</dd></div><div><dt>Свободный диск</dt><dd>${esc(bytes(host.free_disk_bytes))}</dd></div></dl><p class="form-hint">Распределение учитывает приоритеты, зависимости и запас памяти для интерфейса. CPU и GPU используют общую память. Указанные бюджеты задают запас для запуска; фактическое потребление может отличаться.</p>${thermals}<details><summary>Fair-share и очередь кандидатов</summary><p>Очередь учитывает время занятости ресурсов разными опытами. Долго ожидающие задания постепенно получают приоритет.</p><ul>${Object.entries(data?.service_budget_seconds || {}).map(([slug,seconds]) => `<li>${esc(slug)}: ${esc(seconds)} budget·s</li>`).join("")}</ul><p>Порядок прогноза: ${rows(plan.ranked).map(id => esc(label(known.get(id) || {id}))).join(" → ") || "нет кандидатов"}</p></details></div></section>${form}<div class="card-grid">${cards || '<p>Активные задачи не возвращены сервером. Обновление не создаёт scheduling metadata.</p>'}</div></section>`;
  }
  function bindSharedQueue(root,client,onError = () => {}) {
    const run = action => {try {Promise.resolve(action()).catch(onError);} catch (error) {onError(error);} };
    const input = event => {
      const field = event.target?.dataset?.queueField;if (!field) return;
      const value = field === "dependencies" ? Array.from(event.target.selectedOptions || []).map(option => option.value) : event.target.type === "checkbox" ? event.target.checked : event.target.value;
      run(() => client.edit(field,value));
    };
    const submit = event => {const form = event.target?.closest?.("[data-queue-form]");if (!form || !root.contains(form)) return;event.preventDefault();run(() => client.save());};
    const click = event => {
      const button = event.target?.closest?.("[data-queue-action]");if (!button || !root.contains(button) || button.disabled) return;
      const action = button.dataset.queueAction, id = button.dataset.queueJob;
      if (action === "refresh") run(() => client.refresh());
      else if (action === "edit") run(() => client.selectJob(id));
      else if (action === "reload-draft") run(() => client.reloadDraft());
      else if (["pause","resume","cancel"].includes(action)) run(() => client.operation(id,action));
    };
    for (const [name,handler] of [["input",input],["change",input],["submit",submit],["click",click]]) root.addEventListener(name,handler);
    return () => {for (const [name,handler] of [["input",input],["change",input],["submit",submit],["click",click]]) root.removeEventListener(name,handler);};
  }
  const exported = {createSharedQueueClient,sharedQueuePanel,bindSharedQueue};Object.assign(surface,exported);if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
