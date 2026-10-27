"use strict";
// ENGINEERING_GLUE: source-bound Pi image selection; no robot execution interface.
(function (surface) {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const list = value => Array.isArray(value) ? value : [];
  function maskPixels(rle, width, height) {
    if (rle?.encoding !== "row_major_runs_v1" || rle.starts_with !== 0 || rle.shape?.[0] !== height || rle.shape?.[1] !== width ||
        !Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width * height > 2097152 ||
        !Array.isArray(rle.counts) || !rle.counts.length || rle.counts.length > 120000 ||
        rle.counts.some(value => !Number.isInteger(value) || value < 0) || rle.counts.reduce((a,b) => a+b, 0) !== width * height) throw new Error("Некорректная маска исходного кадра");
    const pixels = new Uint8Array(width * height); let offset = 0;
    rle.counts.forEach((length,index) => { if (index % 2) pixels.fill(1, offset, offset + length); offset += length; });
    return pixels;
  }
  function sourcePixel(event, rectangle, frame) {
    if (!(rectangle.width > 0 && rectangle.height > 0 && frame?.width > 0 && frame.height > 0)) throw new Error("Размер исходного кадра не подтверждён");
    const x = (event.clientX - rectangle.left) * frame.width / rectangle.width;
    const y = (event.clientY - rectangle.top) * frame.height / rectangle.height;
    if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x >= frame.width || y >= frame.height) throw new Error("Выбор вне кадра");
    return [x,y];
  }
  function createLabVisionClient(options) {
    let status = null, frozen = null, selection = null, grid = null, error = null, busy = false, updatedAt = 0, revision = 0;
    const notify = () => options.onChange?.();
    async function refresh(force = false) {
      if (busy || !force && Date.now() - updatedAt < 5000) return;
      try { status = await options.get("/api/lab/vision/status"); updatedAt = Date.now(); error = null; }
      catch (failure) { error = failure.message; }
      notify();
    }
    async function operation(work) {
      if (busy) throw new Error("Дождитесь завершения текущего запроса");
      busy = true; error = null; notify();
      try { return await work(); }
      catch (failure) { error = failure.message; throw failure; }
      finally { busy = false; notify(); }
    }
    async function freeze() {
      const expected = ++revision;
      return operation(async () => {
        const pair = await options.get("/api/lab/vision/freeze");
        if (pair.frame?.frame_id !== pair.observation?.frame_id || pair.frame?.frame_sha256 !== pair.observation?.frame_sha256 ||
            pair.frame?.camera_id !== pair.observation?.camera_id || pair.frame?.camera_id !== "pi-overview") throw new Error("Изображение и маски относятся к разным кадрам");
        if (expected === revision) { frozen = pair; selection = null; grid = null; }
        return pair;
      });
    }
    async function select(pixel, purpose) {
      const pair = frozen, expected = revision;
      if (!pair) throw new Error("Сначала зафиксируйте кадр для выбора");
      if (!["object","receptacle","placement_cell"].includes(purpose)) throw new Error("Неизвестное назначение выбора");
      return operation(async () => {
        const value = await options.post("/api/lab/vision/select", {frame_id:pair.frame.frame_id, frame_sha256:pair.frame.frame_sha256, pixel, purpose});
        if (expected === revision) selection = value;
        return value;
      });
    }
    async function semantic() {
      if(status?.runtime_admission?.semantic_admission_allowed===false)throw new Error("Перед анализом завершите запись и остановите своё управление; предыдущий VLM должен освободить ресурсы");
      return operation(async () => {
        const value = await options.post("/api/lab/vision/semantic", {trigger:"user_selection", ...(selection ? {selection_id:selection.selection_id} : {})});
        return value;
      });
    }
    async function metricGrid(cellSize) {
      if (!status?.calibration?.valid) throw new Error("Для метрической сетки нужна действующая измеренная калибровка");
      return operation(async () => { grid = await options.post("/api/lab/vision/grid", {cell_size_m:cellSize}); return grid; });
    }
    function clear() { revision++; frozen = null; selection = null; grid = null; }
    return {refresh,freeze,select,semantic,metricGrid,clear,snapshot:() => ({status,frozen,selection,grid,error,busy,updatedAt})};
  }
  function labVisionPanel(data = {}) {
    const status = data.status || {}, calibration = status.calibration || {}, perception = status.perception || {};
    const worker = status.vlm?.worker || {}, receipts = list(worker.receipts), receipt = receipts.at(-1);
    const selected = data.selection;
    return `<section class="panel spaced-section"><div class="panel-heading"><div><h2>Зрение и выбор на столе</h2><p>Одна камера Pi · Hailo · исходный кадр и маски</p></div><button class="button secondary small" type="button" data-lab-vision="refresh">Обновить</button></div><div class="panel-body">
      <p>${status.segmentation_available ? "Сегментация доступна" : "Сегментация пока не подтверждена"} · ${esc(perception.model?.name || perception.model?.model || "модель уточняется")} · ${calibration.valid ? "Измеренная калибровка действует" : "Метрические координаты не подтверждены"}</p>
      <div class="teleop-actions"><button class="button secondary" type="button" data-lab-vision="freeze" ${data.busy ? "disabled" : ""}>Зафиксировать кадр и выбрать</button><button class="button secondary" type="button" data-lab-vision="semantic" ${data.busy || !status.vlm?.model_staged || status.runtime_admission?.semantic_admission_allowed===false ? "disabled" : ""}>Описать сцену локальной VLM</button></div>
      ${status.runtime_admission?.semantic_requires_idle?'<p class="form-hint">Анализ VLM выполняется при остановленном управлении и завершённой записи. Управление снова доступно после фактического освобождения ресурсов и свежего наблюдения.</p>':""}
      ${data.frozen ? `<label class="form-field">Назначение клика<select id="lab-vision-purpose"><option value="object">Предмет</option><option value="receptacle">Ёмкость / цель</option><option value="placement_cell">Свободное место размещения</option></select></label><div class="lab-vision-frozen"><img id="lab-vision-image" src="/api/lab/vision/frames/${encodeURIComponent(data.frozen.frame.frame_id)}" alt="Зафиксированный исходный кадр Pi"><canvas id="lab-vision-overlay" aria-label="Маски и выбор в исходном кадре"></canvas></div><p class="form-hint">Кадр ${esc(data.frozen.frame.frame_id)}. Маска и track ID относятся именно к нему; этот кадр не является текущим live preview.</p>` : ""}
      ${selected ? `<p>Выбор сохранён: ${esc(selected.purpose)} · track ${esc(selected.track_id ?? "нет: свободное место")}.</p><p class="form-hint">${selected.coordinate_confirmed ? "Геометрия рассчитана по измеренной калибровке. Высота предмета и контакт проверяются отдельно." : esc(selected.grounding_blocker || "Координаты не подтверждены")}</p>` : ""}
      <div class="teleop-actions"><label>Шаг сетки, см <input id="lab-grid-size" type="number" min="0.5" max="25" step="0.5" value="3" style="width:75px"></label><button class="button secondary small" type="button" data-lab-vision="grid" ${!calibration.valid || !data.frozen || data.busy ? "disabled" : ""}>Показать метрическую сетку</button><a class="button secondary small" href="/api/lab/calibration/board.pdf" target="_blank" rel="noopener">Мишень ChArUco для печати</a></div>
      <p class="form-hint">${esc(calibration.reason || "Калибровка привязана к камере, геометрии и области стола")}. Пустое место не считается предметом. Текст VLM не подтверждает координаты, удержание или право движения.</p>
      <details><summary>Локальное семантическое наблюдение · ${esc(worker.state || "не запускалось")}</summary><p>${esc(receipt?.result?.semantic_text || receipt?.outcome || "Нажмите кнопку описания сцены. Автоматических запросов на каждом кадре нет.")}</p></details>
      ${data.error ? `<p class="form-error">${esc(data.error)}</p>` : ""}</div></section>`;
  }
  function bindLabVision(root, client) {
    const canvas = root.querySelector("#lab-vision-overlay"), image = root.querySelector("#lab-vision-image");
    const pair = client.snapshot().frozen;
    if (!canvas || !image || !pair) return;
    const draw = () => {
      if (!canvas.isConnected || !image.complete || client.snapshot().frozen !== pair) return;
      canvas.width = pair.frame.width; canvas.height = pair.frame.height;
      const context = canvas.getContext("2d"); if (!context) return;
      for (const [index,detection] of list(pair.observation.detections).entries()) {
        if (!detection.mask_available) continue;
        try {
          const mask = maskPixels(detection.mask_rle, canvas.width, canvas.height), pixels = context.createImageData(canvas.width, canvas.height);
          const color = index % 2 ? [40,200,100] : [40,130,255];
          mask.forEach((value,pixel) => { if (value) { pixels.data.set(color, pixel * 4); pixels.data[pixel * 4 + 3] = 85; } });
          const layer = document.createElement("canvas"); layer.width=canvas.width; layer.height=canvas.height; layer.getContext("2d").putImageData(pixels,0,0); context.drawImage(layer,0,0);
        } catch (_) { /* Invalid masks remain unavailable; the backend validates selection independently. */ }
      }
      context.strokeStyle = "#ffe680"; context.fillStyle = "#ffe680"; context.font = "10px sans-serif";
      for (const cell of list(client.snapshot().grid?.cells)) {
        const corners = list(cell.corners_pixel); if (corners.length !== 4) continue;
        context.beginPath(); corners.forEach((point,index) => index ? context.lineTo(...point) : context.moveTo(...point)); context.closePath(); context.stroke(); context.fillText(cell.cell_id,...corners[0]);
      }
      const pixel = client.snapshot().selection?.pixel;
      if (pixel) { context.beginPath(); context.arc(...pixel,7,0,2*Math.PI); context.stroke(); }
    };
    image.addEventListener("load",draw); draw();
    canvas.addEventListener("click",event => {
      const purpose = root.querySelector("#lab-vision-purpose")?.value || "object";
      try { void client.select(sourcePixel(event,canvas.getBoundingClientRect(),pair.frame),purpose).catch(() => {}); }
      catch (_) { /* Out-of-frame clicks do not issue a request. */ }
    });
  }
  const exported = {createLabVisionClient,labVisionPanel,bindLabVision,maskPixels,sourcePixel};
  Object.assign(surface,exported);
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
