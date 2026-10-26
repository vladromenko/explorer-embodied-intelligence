"use strict";
// ENGINEERING_GLUE: measured cached-camera calibration; no actuator interface.
(function (surface) {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const rows = value => Array.isArray(value) ? value : [];
  const base = "/api/lab/vision/calibration/";
  function createTabletopCalibrationClient(options) {
    let status = null, error = null, busy = false, updatedAt = 0, displayed = null, displayedSolution = null, newSeries = false, epoch = 0;
    const draft = {}, notify = () => options.onChange?.();
    function snapshot() { return {status,error,busy,updatedAt,displayed,displayedSolution,newSeries,draft:{...draft}}; }
    async function refresh(force = false) {
      if (busy || !force && Date.now() - updatedAt < 5000) return;
      const expected = ++epoch;
      try {
        const session = !newSeries && status?.session?.session_id;
        const value = await options.get(base + "status" + (session ? "?session_id=" + encodeURIComponent(session) : ""));
        if (expected !== epoch) return;
        status = newSeries ? {...value,session:null,ready_to_solve:false} : value;
        updatedAt = Date.now(); displayed = null; error = null;
      } catch (failure) { if (expected === epoch) error = failure.message; }
      notify();
    }
    async function operation(name, body) {
      if (busy) throw new Error("Дождитесь завершения текущего запроса");
      epoch++; busy = true; error = null; notify();
      try { status = await options.post(base + name, body); updatedAt = Date.now(); displayed = null; newSeries = false; return status; }
      catch (failure) { error = failure.message; throw failure; }
      finally { busy = false; notify(); }
    }
    function displayedFrame(frameId) {
      if (status?.current_frame?.frame_id === frameId && displayed !== frameId) { displayed = frameId; notify(); }
    }
    function solutionDisplayed(solutionId) {
      if (status?.session?.solutions?.at(-1)?.solution_id === solutionId && displayedSolution !== solutionId) { displayedSolution = solutionId; notify(); }
    }
    function edit(name, value) { draft[name] = value; }
    function revision() {
      const session = status?.session;
      if (!session || newSeries) throw new Error("Сначала сохраните виды доски");
      return {session_id:session.session_id,expected_revision:session.revision};
    }
    async function capture(purpose) {
      if (status?.session?.solutions?.length || ["QUEUED","RUNNING"].includes(status?.session?.state)) throw new Error("Для новой серии видов нажмите «Новая серия»");
      const frame = status?.current_frame;
      if (!frame || displayed !== frame.frame_id || Date.now() - updatedAt > 1800) {
        error = "Обновите кадр и сразу сохраните показанный вид доски"; notify(); throw new Error(error);
      }
      if (!["intrinsics","reference"].includes(purpose)) throw new Error("Выберите вид или опорное место");
      return operation("capture", {request_id:crypto.randomUUID(), ...(status?.session && !newSeries ? revision() : {new_session:true}), frame_id:frame.frame_id,frame_sha256:frame.frame_sha256,purpose});
    }
    async function solve() {
      const references = rows(status?.session?.captures).filter(row => row.purpose === "reference" && row.quality?.usable);
      const number = name => { const value = draft[name]; if (value === undefined || value === "" || !Number.isFinite(Number(value))) throw new Error("Заполните все измерения в миллиметрах"); return Number(value); };
      const measurements = {reference_capture_id:draft.reference_capture_id || references.at(-1)?.capture_id,
        printed_ruler_length_mm:number("printed_ruler_length_mm"),printed_ruler_sigma_mm:number("printed_ruler_sigma_mm"),measurement_sigma_mm:number("measurement_sigma_mm"),board_thickness_mm:number("board_thickness_mm"),
        base_corners_mm:[0,1,2,3].map(index => ["x","y","z"].map(axis => number(`corner_${index}_${axis}`)))};
      for (const name of ["printed_scale_checked","board_flat_on_table_confirmed","camera_fixed_confirmed","robot_base_fixed_confirmed"]) {
        if (draft[name] !== true) throw new Error("Подтвердите масштаб, плоскость и неподвижность камеры и основания");
        measurements[name] = true;
      }
      return operation("solve", {request_id:crypto.randomUUID(),...revision(),measurements});
    }
    async function accept() {
      const solution = status?.session?.solutions?.at(-1);
      if (!solution || displayedSolution !== solution.solution_id || draft.confirm_axes_overlay !== true || draft.confirm_mount_unchanged !== true) throw new Error("Проверьте наложение осей на опорном снимке и подтвердите крепление");
      return operation("accept", {request_id:crypto.randomUUID(),...revision(),solution_id:solution.solution_id,solution_sha256:solution.artifact_sha256,
        confirm_axes_overlay:true,confirm_mount_unchanged:true,expected_active_calibration_sha256:status.expected_active_calibration_sha256});
    }
    async function startNew() { newSeries = true; displayed = null; displayedSolution = null; for (const key of Object.keys(draft)) delete draft[key]; await refresh(true); if (status) status = {...status,session:null,ready_to_solve:false}; notify(); }
    return {snapshot,refresh,capture,solve,accept,startNew,edit,displayedFrame,solutionDisplayed};
  }
  function tabletopCalibrationPanel(data = {}) {
    const status = data.status || {}, session = status.session || {}, captures = rows(session.captures), solution = rows(session.solutions).at(-1), frame = status.current_frame;
    const reference = captures.find(row => row.capture_id === solution?.reference_capture_id) || captures.filter(row => row.purpose === "reference" && row.quality?.usable).at(-1);
    const draft = data.draft || {}, disabled = data.busy ? "disabled" : "", captureDisabled = data.busy || solution || ["QUEUED","RUNNING"].includes(session.state);
    const field = (name,label) => `<label class="form-field">${esc(label)}<input type="number" step="any" data-calibration-field="${name}" value="${esc(draft[name] ?? "")}" required></label>`;
    const confirm = (name,label) => `<label class="calibration-confirm"><input type="checkbox" data-calibration-field="${name}" ${draft[name] === true ? "checked" : ""}>${esc(label)}</label>`;
    return `<section class="panel spaced-section tabletop-calibration"><div class="panel-heading"><div><h2>Калибровка камеры и плоскости стола</h2><p>Измерения относительно основания ведомой SO101 · ${esc(session.state || "новая серия")}</p></div><button type="button" class="button secondary small" data-calibration="refresh" ${disabled}>Обновить кадр</button></div><div class="panel-body">
      <p>Распечатайте мишень на A4 в масштабе 100%, без подгонки. Камеру и роботов оставьте неподвижными; для разных видов двигайте только доску.</p>
      <a class="button secondary" href="${base}board/pdf" target="_blank" rel="noopener">Скачать мишень для печати 100%</a>
      ${frame ? `<div class="calibration-image"><img data-calibration-preview="${esc(frame.frame_id)}" src="${base}preview/${encodeURIComponent(frame.frame_id)}" alt="Точный текущий кадр общей камеры для калибровки"></div><p class="form-hint">Обновите кадр и сохраните его сразу: сервер принимает кадры не старше 2 секунд.</p>` : `<p class="form-hint">${esc(status.current_frame_error || "Ожидаем текущий кадр Pi")}</p>`}
      <div class="teleop-actions"><button type="button" class="button secondary" data-calibration="intrinsics" ${captureDisabled || !frame || data.displayed !== frame.frame_id ? "disabled" : ""}>Сохранить вид доски</button><button type="button" class="button secondary" data-calibration="reference" ${captureDisabled || !frame || data.displayed !== frame.frame_id ? "disabled" : ""}>Сохранить опорное место на столе</button><button type="button" class="button secondary small" data-calibration="new" ${disabled}>Новая серия</button></div>
      <p>Хороших видов: ${captures.filter(row => row.quality?.usable).length} из необходимых ${status.steps?.find(row => row.id === "views")?.minimum_good_views || 10}. Отклонённых: ${captures.filter(row => !row.quality?.usable).length}. Нужны разные места и наклоны по двум осям.</p>
      ${captures.length ? `<details><summary>Сохранённые виды и причины отклонения</summary><ol>${captures.map(row => `<li>${row.purpose === "reference" ? "Опорный" : "Вид"}: ${row.quality?.usable ? "подходит" : esc(rows(row.quality?.reasons).join(", "))} · углов ${esc(row.quality?.detected_corners)}</li>`).join("")}</ol></details>` : ""}
      <p>Основание: <b>${esc(status.trusted_context?.robot_id || "не подтверждено")} / ${esc(status.trusted_context?.base_frame || "не подтверждено")}</b>. Координаты в эту систему нужно измерить физически.</p>
      ${reference ? `<div class="calibration-image"><img data-calibration-reference src="${base}media/${encodeURIComponent(session.session_id)}/${encodeURIComponent(reference.capture_id)}" alt="Сохранённое опорное место доски и оси"><canvas data-calibration-overlay></canvas></div><p class="form-hint">A/B/C/D — внешние углы самой доски 180×240 мм. Метки показаны на опорном снимке. Это не углы листа A4. Оси X — красная, Y — зелёная, длина 30 мм.</p>` : ""}
      <form data-calibration-form><div class="form-grid">${field("printed_ruler_length_mm","Измеренная линия 100 мм, мм")}${field("printed_ruler_sigma_mm","Погрешность линейки, мм")}${field("measurement_sigma_mm","Погрешность координат углов, мм")}${field("board_thickness_mm","Толщина распечатанной доски, мм")}</div>
      <div class="calibration-corners">${["A","B","C","D"].map((label,index) => `<fieldset><legend>Угол ${label}</legend>${["x","y","z"].map(axis => field(`corner_${index}_${axis}`,`${axis.toUpperCase()}, мм`)).join("")}</fieldset>`).join("")}</div>
      ${confirm("printed_scale_checked","Масштаб распечатки проверен линейкой")}${confirm("board_flat_on_table_confirmed","Опорная доска лежит плоско на столе")}${confirm("camera_fixed_confirmed","Камера закреплена и не перемещалась")}${confirm("robot_base_fixed_confirmed","Основание робота не перемещалось")}
      <button type="submit" class="button primary" ${disabled || !status.ready_to_solve ? "disabled" : ""}>Рассчитать привязку</button></form>
      ${rows(status.solve_blockers).length ? `<p class="form-hint">Для расчёта ещё требуется: ${rows(status.solve_blockers).map(esc).join("; ")}</p>` : ""}
      ${session.last_failure ? `<p class="form-error">Расчёт отклонён: ${rows(session.last_failure.reasons).map(esc).join("; ")}</p>` : ""}
      ${solution ? `<p>Расчёт сохранён. Проверьте границу измеренной области и направление осей на опорном снимке выше. Доска должна оставаться в этом месте для проверки текущего кадра.</p>${confirm("confirm_axes_overlay","На опорном снимке проверены граница и оси")}${confirm("confirm_mount_unchanged","Камера и основание с момента измерений не перемещались")}<button type="button" class="button primary" data-calibration="accept" ${disabled || data.displayedSolution !== solution.solution_id ? "disabled" : ""}>Применить к метрической сетке</button>` : ""}
      ${status.active ? `<p>Привязка применена к сетке. Область ограничена измеренным следом доски; высота предмета и контакт требуют отдельных измерений.</p>` : ""}
      ${data.error ? `<p class="form-error" role="alert">${esc(data.error)}</p>` : ""}</div></section>`;
  }
  function bindTabletopCalibration(root,client,onError) {
    const panel = root.querySelector(".tabletop-calibration"); if (!panel) return;
    const data = client.snapshot(), status = data.status || {}, session = status.session || {}, solution = rows(session.solutions).at(-1);
    const preview = panel.querySelector("[data-calibration-preview]");
    if (preview) { const loaded = () => { if (preview.isConnected && preview.naturalWidth > 0) client.displayedFrame(preview.dataset.calibrationPreview); }; preview.addEventListener("load",loaded); if (preview.complete) loaded(); }
    const reference = rows(session.captures).find(row => row.capture_id === solution?.reference_capture_id) || rows(session.captures).filter(row => row.purpose === "reference" && row.quality?.usable).at(-1);
    const image = panel.querySelector("[data-calibration-reference]"), canvas = panel.querySelector("[data-calibration-overlay]");
    if (image && canvas && reference) {
      const draw = () => {
        if (!image.isConnected || !image.complete || !image.naturalWidth) return;
        canvas.width = reference.frame.width; canvas.height = reference.frame.height; const context = canvas.getContext("2d"); if (!context) return;
        const guide = reference.quality?.board_corner_guide;
        context.font = "bold 18px sans-serif"; context.lineWidth = 2; context.fillStyle = "#ffe07b"; context.strokeStyle = "#ffe07b";
        rows(guide?.corners_px).forEach((point,index) => { context.beginPath(); context.arc(...point,5,0,2*Math.PI); context.stroke(); context.fillText(guide.labels?.[index] || "ABCD"[index],point[0]+7,point[1]-7); });
        const overlay = solution?.overlay;
        if (rows(overlay?.workspace_polygon_px).length) { context.beginPath(); overlay.workspace_polygon_px.forEach((point,index) => index ? context.lineTo(...point) : context.moveTo(...point)); context.closePath(); context.stroke(); }
        if (overlay?.axes_px?.length === 3) { const [origin,x,y] = overlay.axes_px; for (const [target,color,label] of [[x,"#ff6666","X"],[y,"#67ed8d","Y"]]) { context.strokeStyle=color; context.fillStyle=color; context.beginPath(); context.moveTo(...origin); context.lineTo(...target); context.stroke(); context.fillText(label,...target); } }
        if (solution) client.solutionDisplayed(solution.solution_id);
      };
      image.addEventListener("load",draw); draw();
    }
    panel.addEventListener("input",event => { const field = event.target.dataset.calibrationField; if (field) client.edit(field,event.target.type === "checkbox" ? event.target.checked : event.target.value); });
    panel.addEventListener("click",event => {
      const button = event.target.closest("[data-calibration]"); if (!button) return;
      const action = button.dataset.calibration;
      const work = action === "refresh" ? client.refresh(true) : action === "new" ? client.startNew() : action === "accept" ? client.accept() : client.capture(action);
      Promise.resolve(work).catch(onError);
    });
    panel.querySelector("[data-calibration-form]")?.addEventListener("submit",event => { event.preventDefault(); client.solve().catch(onError); });
  }
  const exported = {createTabletopCalibrationClient,tabletopCalibrationPanel,bindTabletopCalibration};
  Object.assign(surface,exported); if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
