"use strict";
// ENGINEERING_GLUE: selected scientific copies; no filesystem input or physical control.
(function (surface) {
  const rows = value => Array.isArray(value) ? value : [];
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const validID = value => typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
  function createResearchExportClient(options) {
    if (typeof options?.get !== "function" || typeof options?.post !== "function") throw new Error("Нужен доверенный HTTP-клиент");
    let data = null, error = "", busy = false, sequence = 0, generation = 0;
    const selected = new Set();
    const snapshot = () => clone({data,error,busy,selected:[...selected]});
    const notify = () => options.onChange?.();
    async function refresh() {
      const read = ++sequence, epoch = generation;
      try {
        const result = await options.get("/api/research/exports");
        if (read === sequence && epoch === generation) {
          data = clone(result);error = "";
          const available = new Set(rows(data.choices).map(row=>row.id));
          for (const id of selected) if (!available.has(id)) selected.delete(id);
        }
      } catch (failure) { if (read === sequence && epoch === generation) error = failure.message; }
      notify();return snapshot();
    }
    function choose(id, checked) {
      if (busy) throw new Error("Дождитесь подтверждения задания экспорта");
      if (!validID(id) || !rows(data?.choices).some(row=>row.id === id)) throw new Error("Выберите настоящее завершённое задание из списка");
      if (checked && !selected.has(id) && selected.size >= 32) throw new Error("В один экспорт можно включить до32 заданий");
      checked ? selected.add(id) : selected.delete(id);notify();
    }
    async function create() {
      if (busy || !selected.size) throw new Error("Выберите завершённые задания и дождитесь текущего запроса");
      const ids = [...selected];
      if (ids.some(id=>!rows(data?.choices).some(row=>row.id === id))) throw new Error("Список изменился; обновите выбор");
      busy = true;error = "";const epoch = ++generation;sequence++;notify();
      try {
        const reply = await options.post("/api/research/exports",{job_ids:ids});
        if (epoch === generation) { for (const id of ids) selected.delete(id);await refresh(); }
        return reply;
      } catch (failure) { if (epoch === generation) error = failure.message;throw failure; }
      finally { if (epoch === generation) {busy = false;notify();} }
    }
    return {snapshot,refresh,choose,create};
  }
  function researchExportPanel(view = {}) {
    const data = view.data, chosen = rows(view.selected);
    const choices = rows(data?.choices).map(job=>`<label class="check-field"><input type="checkbox" data-research-export-job="${esc(job.id)}" ${chosen.includes(job.id) ? "checked" : ""} ${view.busy ? "disabled" : ""}> ${esc(job.project_id)} · ${esc(job.label || job.kind)} · ${esc(job.status)} · ${esc(String(job.id).slice(0,8))}</label>`).join("");
    const exports = rows(data?.exports).map(job=>{
      const result = job.result || {}, url = job.download_url;
      const safe = validID(job.id) && url === `/api/research/exports/${job.id}/download` && job.status === "completed";
      const verification = result.verification === "VERIFIED_COMPLETE" ? "Полнота и восстановление проверены в заявленной области" : result.verification === "PARTIAL" ? "Частичный экспорт · есть незакрытые зависимости" : "Проверка полноты ещё не подтверждена";
      return `<li><p>${esc(job.id?.slice(0,8))} · ${esc(job.status)} · ${esc(verification)}</p>${job.error ? `<p class="form-error">${esc(job.error)}</p>` : ""}${safe ? `<a class="button secondary" href="${esc(url)}">Скачать выбранные свидетельства</a><p class="form-hint">SHA256 архива: ${esc(result.archive_sha256)} · ${esc(result.files ?? "—")} файлов · независимая копия восстановлена: ${result.independent_restore_verified === true ? "да" : "не подтверждено"}</p>` : ""}</li>`;
    }).join("");
    return `<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Research Hub</p><h2>Экспорт выбранных свидетельств</h2></div><button type="button" class="button secondary" data-research-export-action="refresh">Обновить список</button></div><div class="panel-body"><p>Выберите задания для отдельной переносимой копии исходных научных записей и доступных артефактов. Сервер проверит SHA и независимое восстановление. Исходные данные сохраняются; секреты и база входа в архив не включаются.</p>${view.error ? `<p class="form-error" role="alert">${esc(view.error)}</p>` : ""}${choices || '<p>Завершённые задания ещё не загружены или отсутствуют.</p>'}<button type="button" class="button primary" data-research-export-action="create" ${view.busy || !chosen.length ? "disabled" : ""}>${view.busy ? "Сохраняем запрос…" : `Экспортировать выбранное · ${chosen.length}`}</button><p class="form-hint">Экспорт выполняется в общей очереди Homebrain. Недостающие данные, source/runtime или зависимости означают PARTIAL. Такая копия не подтверждает полный научный результат и не заменяет резервную копию системы.</p>${exports ? `<ul class="plain-list">${exports}</ul>` : ""}</div></section>`;
  }
  function bindResearchExport(root,client,onError=()=>{}) {
    const click = event => {
      const button = event.target.closest("[data-research-export-action]");
      if (!button || !root.contains(button) || button.disabled) return;
      const action = {refresh:client.refresh,create:client.create}[button.dataset.researchExportAction];
      if (action) Promise.resolve().then(action).catch(onError);
    };
    const change = event => {
      const element = event.target;
      if (!root.contains(element) || element.disabled || !element.dataset?.researchExportJob) return;
      try { client.choose(element.dataset.researchExportJob,element.checked); } catch (error) {onError(error);}
    };
    root.addEventListener("click",click);root.addEventListener("change",change);
    return ()=>{root.removeEventListener("click",click);root.removeEventListener("change",change);};
  }
  const exported = {createResearchExportClient,researchExportPanel,bindResearchExport};
  Object.assign(surface,exported);if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof globalThis === "undefined" ? window : globalThis);
