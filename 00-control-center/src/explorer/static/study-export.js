"use strict";
// ENGINEERING_GLUE: registered recorded Study exports only; no acting/device routes.
(function (surface) {
  const rows = value => Array.isArray(value) ? value : [];
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? "").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const validStudy = value => typeof value === "string" && /^[a-z0-9][a-z0-9-]{0,95}$/.test(value);
  const validJob = value => typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
  const validSHA = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  const base = "/api/research/studies";
  function createStudyExportClient(options) {
    if (typeof options?.get !== "function" || typeof options?.post !== "function") throw new Error("Нужен доверенный HTTP-клиент");
    let data = null, error = "", busy = false, selected = "", sequence = 0, generation = 0, closed = false;
    const snapshot = () => clone({data,error,busy,selected,closed});
    const notify = () => { if (!closed) options.onChange?.(snapshot()); };
    async function refresh() {
      if (closed) return snapshot();
      const read = ++sequence, epoch = generation;
      try {
        const result = await options.get(base);
        if (!closed && read === sequence && epoch === generation) {
          data = clone(result);error = "";
          if (!rows(data.studies).some(row=>row.study_id === selected)) selected = "";
        }
      } catch (failure) { if (!closed && read === sequence && epoch === generation) error = failure.message || "Не удалось получить Study"; }
      notify();return snapshot();
    }
    function choose(id) {
      if (closed || busy) throw new Error("Дождитесь подтверждения текущего запроса");
      if (!validStudy(id) || !rows(data?.studies).some(row=>row.study_id === id)) throw new Error("Выберите зарегистрированный Study из списка");
      selected = id;notify();
    }
    async function create() {
      if (closed || busy || !selected || !rows(data?.studies).some(row=>row.study_id === selected)) throw new Error("Выберите Study и дождитесь текущего запроса");
      const id = selected, epoch = ++generation;sequence++;busy = true;error = "";notify();
      try {
        const reply = await options.post(base,{study_id:id});
        if (!closed && epoch === generation) { selected = "";await refresh(); }
        return reply;
      } catch (failure) { if (!closed && epoch === generation) error = failure.message || "Результат запроса неизвестен; проверьте общую очередь";throw failure; }
      finally { if (!closed && epoch === generation) {busy = false;notify();} }
    }
    function destroy() { closed = true;generation++;sequence++; }
    return {snapshot,refresh,choose,create,destroy};
  }
  function studyExportPanel(view = {}) {
    const choices = rows(view.data?.studies).map(study=>`<label class="check-field"><input type="radio" name="registered-study-export" data-study-export-id="${esc(study.study_id)}" ${view.selected === study.study_id ? "checked" : ""} ${view.busy ? "disabled" : ""}> ${esc(study.title || study.study_id)} · ${esc(study.attempts ?? "—")} попытки</label>`).join("");
    const exports = rows(view.data?.exports).map(job=>{
      const result = job.result || {}, url = job.download_url;
      const safe = validJob(job.id) && url === `${base}/${job.id}/download` && job.status === "completed" && result.verification === "PARTIAL" && result.independent_restore_verified === true && validSHA(result.archive_sha256);
      const outcomes = Object.values(result.outcomes && typeof result.outcomes === "object" ? result.outcomes : {});
      const success = outcomes.filter(value=>value === "SUCCESS").length, failure = outcomes.filter(value=>value === "FAILURE").length, unknown = outcomes.filter(value=>value !== "SUCCESS" && value !== "FAILURE").length;
      const closure = result.dependency_closure || {};
      const checked = result.independent_restore_verified === true ? "Независимая копия восстановлена" : "Проверка восстановления ещё не завершена";
      const state = result.verification === "PARTIAL" ? "PARTIAL · исходный runtime/RNG не закрыт" : "Полнота исходного runtime не подтверждена";
      return `<li><p>${esc(String(job.id ?? "").slice(0,8))} · ${esc(job.status)} · ${esc(state)}</p>${job.error ? `<p class="form-error">${esc(job.error)}</p>` : ""}${job.details_withheld_reason ? '<p class="form-hint">Подробности удержаны ограничением метаданных; используйте общую очередь.</p>' : ""}<p>${esc(checked)}${outcomes.length ? ` · успехов: ${success}, неуспехов: ${failure}, неизвестных: ${unknown}` : ""}</p><p class="form-hint">Записанные файлы: ${closure.recorded_required_payloads === "VERIFIED" ? "проверены" : "не подтверждено"} · сцены/CAD: ${closure.scene_and_cad_assets === "VERIFIED" ? "проверены" : "не подтверждено"} · frozen benchmark: ${closure.frozen_benchmark === "VERIFIED" ? "повторён" : "не подтверждено"}</p>${safe ? `<a class="button secondary" href="${esc(url)}">Скачать Study со всеми попытками</a><p class="form-hint">${esc(result.files ?? "—")} файлов · SHA256: ${esc(result.archive_sha256)}</p>` : ""}</li>`;
    }).join("");
    return `<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Research Hub</p><h2>Переносимый Study</h2></div><button type="button" class="button secondary" data-study-export-action="refresh">Обновить список</button></div><div class="panel-body"><p>Копия включает все объявленные development-попытки, отрицательные результаты, RGB, 9D действия, сцены/CAD, исходники и frozen protocol. Статистический отчёт повторяется из сохранённых свидетельств.</p><p class="form-hint">Источник SCRIPTED_SIM: геометрический teacher и стационарное основание в симуляции. Обученная исходная политика, научная H1 и физический навык не подтверждены. Полный исходный runtime и начальный RNG остаются PARTIAL; target development не передаётся оптимизатору.</p>${view.error ? `<p class="form-error" role="alert">${esc(view.error)}</p>` : ""}${choices || '<p>Зарегистрированные Study ещё не загружены или недоступны.</p>'}<button type="button" class="button primary" data-study-export-action="create" ${view.busy || !view.selected || view.closed ? "disabled" : ""}>${view.busy ? "Сохраняем запрос…" : "Подготовить переносимую копию в очереди"}</button><p class="form-hint">Экспорт и проверка выполняются в общей очереди Homebrain. Оригиналы сохраняются; действия робота не запрашиваются.</p>${exports ? `<ul class="plain-list">${exports}</ul>` : ""}</div></section>`;
  }
  function bindStudyExport(root,client,onError=()=>{}) {
    const click = event => {
      const button = event.target.closest("[data-study-export-action]");
      if (!button || !root.contains(button) || button.disabled) return;
      const action = {refresh:client.refresh,create:client.create}[button.dataset.studyExportAction];
      if (action) Promise.resolve().then(action).catch(onError);
    };
    const change = event => {
      const element = event.target;
      if (!root.contains(element) || element.disabled || !element.dataset?.studyExportId) return;
      try { client.choose(element.dataset.studyExportId); } catch (error) {onError(error);}
    };
    root.addEventListener("click",click);root.addEventListener("change",change);
    return ()=>{root.removeEventListener("click",click);root.removeEventListener("change",change);};
  }
  const exported = {createStudyExportClient,studyExportPanel,bindStudyExport};
  Object.assign(surface,exported);if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof globalThis === "undefined" ? window : globalThis);
