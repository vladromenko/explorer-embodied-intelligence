"use strict";

const ICONS = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  research: '<path d="M9 3v6l-5 9a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-5-9V3M8 3h8M8 14h8"/><path d="m10 17 1 1m4-1 1 1"/>',
  skills: '<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.1M7 17.5h.1M17 6.5h1M17 17.5h1"/>',
  lab: '<path d="m3 9 9-5 9 5-9 5zM7 12v5c3 3 7 3 10 0v-5M21 9v8"/>',
  camera: '<rect x="3" y="6" width="18" height="15" rx="2"/><path d="m8 6 2-3h4l2 3"/><circle cx="12" cy="13" r="4"/>',
  robot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M10 4h4M2 12v4m20-4v4M9 19v2m6-2v2M8 13h.1m7.8 0h.1M9 16h6"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
  play: '<path d="m9 5 11 7-11 7z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  cancel: '<path d="m6 6 12 12M18 6 6 18"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9H3z"/>',
  wifi: '<path d="M3 8a15 15 0 0 1 18 0M6 12a10 10 0 0 1 12 0m-9 4a5 5 0 0 1 6 0m-3 4h.1"/>',
  book: '<path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1zM12 5v15"/>',
  record: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12 0l2 5M4 12l2 5a7 7 0 0 0 12 0"/>'
};
const icon = (name, className = "") => `<svg class="${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.overview}</svg>`;
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
const array = (value) => Array.isArray(value) ? value : [];
const defined = (...values) => values.find((value) => value !== null && value !== undefined);
const text = (value, fallback = "") => typeof value === "string" || typeof value === "number" ? String(value) : (value?.message || value?.reason || value?.detail || fallback);
const number = (value) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value) : null;
const count = (value) => number(value) === null ? "—" : new Intl.NumberFormat("ru-RU").format(Number(value));
const projectId = (value) => value === "personal" ? SO101_SOURCE_PROJECT : String(value ?? "").match(/\d+/)?.[0]?.padStart(2, "0") || "";
const dateValue = (value) => typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : Date.parse(value || "");
const dateTime = (value) => Number.isFinite(dateValue(value)) ? new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(dateValue(value)) : "Время не указано";
const clockTime = (value = Date.now()) => new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(value);

const PROJECTS = [
  { id: "01", title: "Автономное обучение", short: "Обучение в симуляции", brief: "От простого движения к самостоятельному навыку.", subtitle: "Исследуем обучение в MuJoCo. Каждый запуск сохраняет параметры, результат и происхождение модели.", steps: [ ["Выбрать задачу", "Начните с достижения цели. Сначала проверяем, что обучение и сохранение результатов работают в симуляции."], ["Запустить обучение", "Наблюдайте за настоящим запуском, его метриками и кадрами. Задачу можно приостановить или отменить."], ["Проверить перенос", "Результат симуляции оценивается отдельно. Готовность физического выполнения определяется по фактическим проверкам оборудования."] ], guidance: "Покажите одну понятную цель и одно движение. Для этого опыта основной источник данных — симуляция." },
  { id: "02", title: "Обучение по показам", short: "Показы на ROSMASTER", brief: "Опыт оператора становится материалом для обучения.", subtitle: "Записывайте наблюдения за роботом и связывайте их с задачей. Ручное управление доступно в штатной панели.", steps: [ ["Подготовить один показ", "Выберите одну задачу, начальное положение и ясный признак завершения. Держите объект и движение в кадре."], ["Сохранить наблюдение", "Запись камеры выполняется на узле. Она пока не заменяет эпизод с синхронизированными командами и состоянием."], ["Обучить и проверить", "Для обучения нужны проверенные эпизоды, временная привязка и отдельные испытания на новых начальных условиях."] ], guidance: "Покажите одно и то же действие несколько раз из разных начальных положений. В начале и конце ненадолго остановитесь, чтобы результат было видно." },
  { id: "03", title: "Обучение с помощью руки", short: "SO-101 → ROSMASTER", brief: "Понятный показ рукой с учётом геометрии робота.", subtitle: "Рука SO-101 задаёт намерение. Перед выполнением на ROSMASTER нужны проверенное соответствие движений и калибровки.", steps: [ ["Определить роли рук", "Убедитесь, что ведущая и ведомая руки определены верно. Они остаются подключёнными к Raspberry Pi."], ["Проверить соответствие движений", "Разные конструкции требуют калибровки и согласования поз. Номера суставов нельзя считать готовым соответствием."], ["Записать и оценить показ", "Начните с одного небольшого действия. Результаты переноса проверяются независимо от обучающих показов."] ], guidance: "Держите ведущую руку и рабочую область в общем кадре. Показывайте короткое движение с хорошо различимыми началом и концом." },
  { id: TRANSFER_PROJECT, title: "Перенос между роботами", short: "Перенос навыка", brief: "Проверяем, что сохраняется при смене конструкции.", subtitle: "Отдельно изучаем перенос опыта SO-101 на ROSMASTER. Результат на одной руке ещё не доказывает успех на другой.", steps: [ ["Сохранить исходный навык", "Зафиксируйте данные, версию модели, геометрию и условия исходной задачи на SO-101."], ["Подготовить перенос", "Сопоставьте рабочие пространства, наблюдения и действие через проверенный адаптер, сохраняя происхождение изменений."], ["Сравнить результаты", "Проведите отдельную оценку на ROSMASTER и сравните её с исходным навыком и базовыми методами."] ], guidance: "Показывайте одинаковую задачу в сопоставимых условиях. Сохраняйте положение камеры, размеры объекта и признак завершения." },
  { id: "05", title: "Совместная передача", short: "Передача предмета", brief: "Два робота, одна согласованная задача.", subtitle: "Исследуем передачу предмета от ROSMASTER к SO-101. Готовность обеих сторон и наблюдение оцениваются совместно.", steps: [ ["Подготовить общую сцену", "Обе руки, предмет и место передачи должны быть видны. Сначала требуется подтверждённая геометрия сцены."], ["Проверить готовность сторон", "Передача зависит от возможностей захватов, удержания предмета, связи и согласования участников."], ["Оценить завершение", "Разделяйте попытку передачи, видимый переход предмета и устойчивое удержание принимающей рукой."] ], guidance: "Общий кадр должен показывать обе стороны и предмет на всём пути передачи. Сохраните наблюдение до передачи и после неё." },
  { id: SO101_SOURCE_PROJECT, title: "Две руки LeRobot · показ, обучение и повторение", short: "Две SO-101", brief: "Показ, проверка данных, обучение и повторение на Raspberry Pi.", subtitle: "SO-101 leader и follower с общей настольной камерой. Обучение выполняется на Homebrain, Mac служит веб-панелью.", steps: [["Просто управлять", "Относительный leader → follower без обязательной записи."], ["Собрать серию показов", "Один навык, разные начальные условия, синхронные кадры и измерения."], ["Проверить и обучить", "Отделите raw episode replay от learned policy rollout; сначала offline и shadow."]], guidance: "В кадре должны быть предмет, захват follower и вся рабочая область. Координаты сетки требуют подтверждённой калибровки." }
].sort((left,right) => left.id.localeCompare(right.id));
const PERSONAL_PROJECT = { id: "personal", title: "Личная лаборатория SO-101", guidance: "Выберите одно простое движение. Сохраните его начало и конец в общем кадре и после записи разберите, что можно было показать понятнее." };

const STATUS = {
  queued: ["В очереди", "neutral"], pending: ["Ожидает", "neutral"], running: ["В работе", "good"], active: ["В работе", "good"],
  paused: ["Пауза", "warn"], completed: ["Завершено", "good"], complete: ["Завершено", "good"], succeeded: ["Завершено", "good"],
  success: ["Завершено", "good"], failed: ["Ошибка", "bad"], error: ["Ошибка", "bad"], cancelled: ["Отменено", "neutral"], canceled: ["Отменено", "neutral"],
  recording: ["Идёт запись", "good"], interrupted: ["Прервано", "warn"],
  trained_offline_only: ["Обучено по записи", "neutral"],
  ready: ["Готово к запуску", "good"], available: ["Доступно", "good"], blocked: ["Нужна подготовка", "warn"], needs_setup: ["Нужна подготовка", "warn"],
  partial: ["Частично готово", "warn"], implemented: ["Подготовлено", "neutral"], not_started: ["Не запускался", "neutral"], offline: ["Нет связи", "neutral"],
  not_validated: ["Не проверено", "warn"], unvalidated: ["Не проверено", "warn"], validated: ["Проверено", "good"], awaiting_validation: ["Ожидает проверки", "warn"],
  awaiting_evidence: ["Ожидает результатов", "neutral"], untested_hypothesis: ["Гипотеза не проверена", "warn"],
  pause_requested: ["Приостанавливается", "warn"], resume_requested: ["Возобновляется", "neutral"], cancel_requested: ["Отменяется", "warn"],
  physically_validated: ["Проверено на оборудовании", "good"], simulation_validated: ["Проверено в симуляции", "neutral"],
  simulation_source_evaluated: ["Проверено в симуляции", "neutral"], simulation_candidate_pending: ["Кандидат ожидает проверки", "neutral"],
  simulation_candidate_rejected: ["Кандидат отклонён", "warn"], simulation_development_accepted: ["Принят в симуляции", "neutral"]
};
function badge(value, fallback = "Ожидаем данные") {
  const item = STATUS[String(value || "").toLowerCase()] || [text(value, fallback) || fallback, "neutral"];
  return `<span class="badge ${item[1]}">${esc(item[0])}</span>`;
}
function reasonText(value) {
  const labels = {
    calibration_missing: "Нужна калибровка оборудования", calibration_required: "Нужна калибровка оборудования", no_measured_arm_feedback: "Измеренное положение руки недоступно",
    camera_unavailable: "Камера пока недоступна", observer_required: "Нужно наблюдение через камеру или подтверждение присутствия", physical_validation_required: "Физическая проверка ещё не выполнена",
    dataset_missing: "Пока нет подходящих эпизодов", no_demonstrations: "Пока нет проверенных показов", offline: "Нет связи с узлом",
    so101_roles_unassigned: "Роли ведущей и ведомой рук ещё не сопоставлены", so101_calibration_unverified: "Калибровки рук ещё не проверены"
  };
  const raw = text(value, value?.code || "Уточняется состояние оборудования");
  return labels[raw] || raw;
}

const rosmasterDemonstrationRoute = () => ["02", "03"].includes(activeProject()?.id);
const model = { state: null, jobs: [], skills: [], research: {}, recordings: [], actions: [], lastUpdate: null, lastExtras: 0, error: null, refreshing: false, simDraft: { task: "reach", updates: 256, seed: 0 }, robot: { teleop: null, controls: null, teaching: null, error: null, episodeName: "Первый показ" } };
const labVision = createLabVisionClient({get: api,post: (...args) => post(...args),onChange: () => { model.forceRender = true; render(); }});
const tabletopCalibration = createTabletopCalibrationClient({get:api,post:(...args) => post(...args),onChange:() => { model.forceRender = true; render(); }});
const cooperativeWorkbench = createCooperativeWorkbenchClient({get:api,post:(...args) => post(...args),onChange:() => { model.forceRender = true; render(); }});
let cooperativeUnbind = () => {}, cooperativeLoadedAt = 0;
const sharedQueue = createSharedQueueClient({get:api,post:(...args) => post(...args),onChange:() => { model.forceRender = true; render(); }});
let sharedQueueUnbind = () => {}, sharedQueueLoadedAt = 0;
const labSkills = createLabSkillsClient({api,onChange: () => { model.forceRender = true; render(); },onCaptureContext: context => { stationLab.skillContext = context.record_start_body; toast("Контекст навыка выбран. Запись запускается отдельно при наблюдении за руками."); }});
let labSkillsLoadedAt = 0;
const keyboard = new ManualKeyboard();
const mobileLearning = { importJob: "" };
const worldLearning = { datasets: [], error: null, loading: false, lastLoad: 0, manifest: "", development: null, signature: "", busy: false };
let episodeLeaseBusy = false;
const main = document.querySelector("#main");
const dialog = document.querySelector("#record-dialog");
const record = { step: 0, project: "02", camera: "", duration: 15, busy: false };
let toastTimeout;
let currentCamera = "";
let cameraMode = "stream";
let cameraSignature = "";
let cameraLoaded = false;
let lastSnapshotAt = 0;
let simulationJob = "";
let simulationImage = null;

function csrfToken() {
  const entry = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("explorer_csrf="));
  return entry ? decodeURIComponent(entry.slice("explorer_csrf=".length)) : "";
}
async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.method && options.method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-Explorer-CSRF"] = csrfToken(); }
  const boundedControl = path.startsWith("/api/robot/teleop") || path === "/api/robot/teaching/mobile" || options.method === "POST" && ["/api/station/", "/api/retarget/stop", "/api/stop"].some(prefix => path.startsWith(prefix));
  const abort = boundedControl && !options.signal && typeof AbortController !== "undefined" ? new AbortController() : null;
  const timeout = abort ? setTimeout(() => abort.abort(), 6500) : null;
  let response;
  try { response = await fetch(path, { credentials: "same-origin", ...options, ...(abort ? {signal: abort.signal} : {}), headers }); }
  finally { if (timeout !== null) clearTimeout(timeout); }
  if (response.status === 401) { window.location.assign(`/api/login?next=${encodeURIComponent(window.location.pathname)}`); throw new Error("Требуется вход в станцию."); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = text(body.detail, text(body.error));
    throw new Error(detail || `Станция не выполнила запрос (${response.status}).`);
  }
  return response.status === 204 ? {} : response.json();
}
const post = (path, body = {}, options = {}) => api(path, { method: "POST", body: JSON.stringify(body), ...options });
const observedNative = createObservedNativeClient({api,now:()=>performance.now(),
  createURL:blob=>URL.createObjectURL(blob),revokeURL:url=>URL.revokeObjectURL(url),
  decode:async url=>{const image=document.createElement("img");image.src=url;await image.decode();},
  frame:async id=>{
    const response=await fetch(`/api/native-activation/frame/${encodeURIComponent(id)}`,{credentials:"same-origin",cache:"no-store"});
    if(!response.ok)throw new Error("Точный native-кадр недоступен; обновите запрос");
    const bytes=await response.arrayBuffer();
    if(bytes.byteLength>2*1024**2 || !window.crypto?.subtle)throw new Error("Нужен HTTPS и проверяемый ограниченный JPEG");
    const checksum=Array.from(new Uint8Array(await window.crypto.subtle.digest("SHA-256",bytes)),b=>b.toString(16).padStart(2,"0")).join("");
    if(checksum!==response.headers.get("X-JPEG-SHA256"))throw new Error("Контрольная сумма native-кадра не совпала");
    return {blob:new Blob([bytes],{type:"image/jpeg"}),sha256:checksum,frame_id:response.headers.get("X-Frame-ID"),boot_id:response.headers.get("X-Boot-ID")};
  },onChange:()=>{if(path()==="/evening"||rosmasterDemonstrationRoute()){model.forceRender=true;render();}}
});
function toast(message, error = false) {
  const node = document.querySelector("#toast");
  clearTimeout(toastTimeout); node.textContent = message; node.classList.toggle("error", error); node.hidden = false;
  toastTimeout = setTimeout(() => { node.hidden = true; }, 6500);
}
function path() { return window.location.pathname.replace(/\/$/, "") || "/"; }
function activeProject() { return PROJECTS.find((project) => path() === `/experiments/${project.id}`); }
function navigate(url) { if (path() !== url && (stationLab.owned || stationLab.present)) stopStation(true); if ((keyboard.active || keyboard.starting) && url !== "/experiments/02") stopKeyboard(true); if (path() !== url) history.pushState({}, "", url); render(); main.focus({ preventScroll: true }); window.scrollTo({ top: 0 }); if (url === "/experiments/01") refreshWorldDatasets(); }

function navigation() {
  const item = (href, label, symbol, ordinal) => `<a href="${href}" data-link class="nav-item ${path() === href ? "active" : ""}" ${path() === href ? 'aria-current="page"' : ""}>${ordinal ? `<span class="nav-number">${ordinal}</span>` : icon(symbol, "nav-icon")}<span>${label}</span></a>`;
  document.querySelector("#navigation").innerHTML = item("/", "Обзор станции", "overview") + '<div class="nav-section-label">Исследовательские опыты</div>' + PROJECTS.map((project) => item(`/experiments/${project.id}`, ["Автономное обучение", "Обучение ROSMASTER", "Leader → ROSMASTER", "Две руки LeRobot", "Совместная передача", "Перенос навыков"][Number(project.id) - 1], "", project.id)).join("") + '<div class="nav-divider"></div>' + item("/queue", "Общая очередь", "clock") + item("/research", "Исследования", "research") + item("/skills", "Библиотека навыков", "skills") + item("/infrastructure", "Оборудование", "server") + item("/evening", "Наблюдаемая проверка", "check");
}

function nodes() { return array(model.state?.nodes); }
function nodeRole(node) {
  const name = `${node.id} ${node.host} ${node.name}`.toLowerCase();
  if (/homebrain|orin nx|\bnx\b/.test(name)) return { name: "Homebrain", role: "Обучение и общая станция", symbol: "server" };
  if (/explorer-pi|raspberry|\bpi\b/.test(name)) return { name: "Explorer Pi", role: "Руки и общий вид", symbol: "chip" };
  return { name: node.name || "Explorer", role: "ROSMASTER и бортовые камеры", symbol: "robot" };
}
function compactHealth(node) {
  if (!node.online) return "Ожидаем подключение";
  const health = node.health || {};
  if (typeof health === "string") return health === "online" ? "Связь установлена" : health === "offline" ? "Нет связи" : health;
  return text(health.summary, text(health.message, array(health.errors)[0] ? reasonText(health.errors[0]) : "Связь установлена"));
}
function nodeCards() {
  const list = nodes().length ? nodes() : [ { id: "homebrain", name: "Homebrain" }, { id: "explorer", name: "Explorer" }, { id: "explorer-pi", name: "Explorer Pi" } ];
  return `<div class="node-grid">${list.map((node) => {
    const role = nodeRole(node); const online = node.online === true;
    return `<article class="node-card"><div class="node-top"><span class="node-symbol">${icon(role.symbol)}</span><div><h3 class="node-title">${esc(role.name)}</h3><div class="node-role">${esc(role.role)}</div></div><span class="node-connection ${online ? "" : "offline"}"><span class="status-dot ${online ? "good" : "muted"}"></span>${online ? "В сети" : (model.state ? "Нет связи" : "Ожидаем")}</span></div><div class="node-bottom"><span>${esc(compactHealth(node))}</span><span>${esc(online ? text(node.host, node.hostname || "") : "")}</span></div></article>`;
  }).join("")}</div>`;
}

function cameras() {
  const collection = [...array(model.state?.cameras)];
  nodes().forEach((node) => array(node.cameras).forEach((camera) => {
    const value = typeof camera === "string" ? { id: camera } : camera;
    if (value?.id && !collection.some((known) => known.id === value.id)) collection.push({ ...value, node_id: node.id, node_online: node.online });
  }));
  return collection.filter((camera) => camera.id);
}
function cameraLabel(camera) { return text(camera?.label, text(camera?.name, { "pi-overview": "Общий вид · Raspberry Pi", "nano-rgbd": "RGB-D · ROSMASTER", "nano-logitech": "Внешняя камера · ROSMASTER" }[camera?.id] || camera?.id || "Камера")); }
const cameraCard = document.createElement("section");
cameraCard.className = "panel camera-card";
cameraCard.setAttribute("aria-label", "Изображение с камеры");
cameraCard.innerHTML = `<div class="panel-heading"><div class="heading-label">${icon("camera", "nav-icon")}<h2>Рабочая сцена</h2></div><label class="sr-only" for="camera-select">Камера</label><select class="camera-select" id="camera-select"><option value="">Ожидаем камеры</option></select></div><div class="camera-stage"><img id="camera-image" alt="Текущее изображение с выбранной камеры" hidden><div class="camera-placeholder" id="camera-placeholder">${icon("camera")}<strong id="camera-placeholder-title">Подключаем камеру</strong><p id="camera-placeholder-note">Здесь появится настоящее изображение с оборудования.</p></div><div class="camera-overlay"><span class="camera-badge"><span class="status-dot muted" id="camera-dot"></span><span id="camera-mode">ОЖИДАЕМ ИЗОБРАЖЕНИЕ</span></span><span class="camera-time" id="camera-time"></span></div></div><div class="camera-bottom"><span class="camera-note">Исходное изображение · без подмены симуляцией</span><button type="button" class="quiet-button text-link" data-action="record">${icon("record", "nav-icon")} Записать наблюдение</button></div>`;
const cameraImage = cameraCard.querySelector("#camera-image");
const cameraSelect = cameraCard.querySelector("#camera-select");
cameraSelect.addEventListener("change", () => { currentCamera = cameraSelect.value; startCamera(); });
cameraImage.addEventListener("load", () => {
  if (rosmasterDemonstrationRoute()) return;
  cameraLoaded = true; cameraImage.hidden = false; cameraCard.querySelector("#camera-placeholder").hidden = true;
  cameraCard.querySelector("#camera-dot").className = "status-dot good";
  cameraCard.querySelector("#camera-mode").textContent = cameraMode === "stream" ? "ЖИВОЙ ПОТОК" : "СНИМКИ КАМЕРЫ";
  const time = cameraCard.querySelector("#camera-time"); time.textContent = `${cameraMode === "stream" ? "Поток открыт" : "Получено"} ${clockTime()}`; time.style.display = "block";
});
cameraImage.addEventListener("error", () => {
  if (rosmasterDemonstrationRoute()) return;
  if (cameraMode === "stream" && currentCamera) { cameraMode = "snapshot"; loadSnapshot(); }
  else cameraUnavailable("Камера пока недоступна", "Проверьте подключение камеры в разделе «Оборудование». Следующая попытка выполнится автоматически.");
});
function cameraUnavailable(title, note) {
  cameraLoaded = false; cameraImage.hidden = true;
  cameraCard.querySelector("#camera-placeholder").hidden = false;
  cameraCard.querySelector("#camera-placeholder-title").textContent = title;
  cameraCard.querySelector("#camera-placeholder-note").textContent = note;
  cameraCard.querySelector("#camera-dot").className = "status-dot muted";
  cameraCard.querySelector("#camera-mode").textContent = "НЕТ ИЗОБРАЖЕНИЯ";
  cameraCard.querySelector("#camera-time").style.display = "none";
}
function loadSnapshot() {
  if (currentCamera && document.contains(cameraCard)) {
    lastSnapshotAt = Date.now();
    cameraImage.src = `/api/cameras/${encodeURIComponent(currentCamera)}/snapshot?t=${Date.now()}`;
  }
}
function startCamera() {
  if (rosmasterDemonstrationRoute()) {
    currentCamera = "nano-rgbd"; cameraMode = "manual-preview";
    void manualPreview.start(); return;
  }
  if (manualPreview.state().active) manualPreview.stop("left_experiment_02");
  cameraMode = "stream"; cameraLoaded = false;
  if (currentCamera && document.contains(cameraCard)) {
    cameraUnavailable("Подключаем камеру", "Получаем изображение с выбранного узла.");
    cameraImage.src = `/api/cameras/${encodeURIComponent(currentCamera)}/stream`;
  } else {
    cameraImage.removeAttribute("src");
    cameraUnavailable("Камеры не обнаружены", "Когда камера станет доступна, здесь появится её изображение.");
  }
}
function syncCamera() {
  const available = cameras();
  const manual = rosmasterDemonstrationRoute();
  cameraSelect.disabled = manual;
  const signature = `${path()}|` + available.map((camera) => `${camera.id}:${cameraLabel(camera)}:${camera.available}:${camera.node_online}`).join("|");
  if (manual) {
    cameraSignature = signature; currentCamera = "nano-rgbd";
    cameraSelect.innerHTML = '<option value="nano-rgbd">ROSMASTER · RGB / DaBai</option>';
    cameraMode = "manual-preview";
    if (!manualPreview.state().active) void manualPreview.start();
    return;
  }
  if (manualPreview.state().active) manualPreview.stop("left_experiment_02");
  if (signature !== cameraSignature) {
    const changedRoute = cameraSignature.split("|")[0] !== path();
    cameraSignature = signature;
    const ready = available.filter((camera) => camera.available !== false && camera.node_online !== false);
    cameraSelect.innerHTML = available.length ? available.map((camera) => `<option value="${esc(camera.id)}" ${camera.available === false || camera.node_online === false ? "disabled" : ""}>${esc(cameraLabel(camera))}${camera.available === false || camera.node_online === false ? " · недоступна" : ""}</option>`).join("") : '<option value="">Камеры не обнаружены</option>';
    if (changedRoute) {
      const preferred = activeProject()?.id === "03" ? "nano-rgbd" : ["/learning/so101", SO101_SOURCE_ROUTE].includes(path()) ? "pi-overview" : null;
      if (preferred) currentCamera = preferred;
    }
    if (activeProject()?.id === "03" && !ready.some(camera => camera.id === currentCamera)) currentCamera = "nano-rgbd";
    else if (!ready.some((camera) => camera.id === currentCamera)) currentCamera = ready.find((camera) => camera.id === "pi-overview")?.id || ready[0]?.id || "";
    cameraSelect.value = currentCamera;
    startCamera();
  } else if (currentCamera && !cameraImage.getAttribute("src")) startCamera();
}

function filterManualKeysForCurrentOwnerConstraints(keys) {
  // Prohibited by default; only a current native observed-session grant opens it.
  if(observedNative.chassisAllowed())return keys;
  return keys.filter(key => !["KeyW", "KeyS", "KeyA", "KeyD", "KeyQ", "KeyE"].includes(key));
}
function rosmasterCameraReady() {
  return rosmasterDemonstrationRoute() && currentCamera === "nano-rgbd" && !document.hidden && manualPreview.ready();
}
const manualPreview = createManualCameraPreview({
  image: cameraImage, cameraId: "nano-rgbd", document, window,
  isVisible: () => rosmasterDemonstrationRoute() && cameraCard.isConnected && !document.hidden,
  onFrame: frame => {
    cameraLoaded = true; cameraImage.hidden = false;
    cameraCard.querySelector("#camera-placeholder").hidden = true;
    cameraCard.querySelector("#camera-dot").className = "status-dot good";
    cameraCard.querySelector("#camera-mode").textContent = "ROSMASTER · JPEG ≈ 5 КАДРОВ/С";
    const time = cameraCard.querySelector("#camera-time"); time.style.display = "block"; time.textContent = `Получено ${clockTime()}`;
    const status = document.querySelector("#manual-camera-status"); if (status) status.textContent = `Кадр ${frame.frame_id} · камера на связи`;
  },
  onUnavailable: () => {
    cameraUnavailable("Нет свежих кадров ROSMASTER", "Проверяем камеру повторно. Управление требует нового явного подключения.");
    if (keyboard.active || keyboard.starting) void stopKeyboard(true);
    if (direct03.owned || direct03.held || direct03.busy) void direct03Stop(true);
    if (["active", "connecting", "paused"].includes(browserGamepad.state) || browserGamepad.busy) void browserGamepad.disconnect("Камера ROSMASTER недоступна. Подключите управление явно.");
    const status = document.querySelector("#manual-camera-status"); if (status) status.textContent = "Свежий кадр пока не получен";
  }
});
let manualInputOwner = null;
let manualInputFreezeCount = 0;
const claimManualInput = owner => {
  if (manualInputFreezeCount) throw new Error("Завершается STOP или выход из панели.");
  manualInputOwner = { owner }; return manualInputOwner;
};
const ownsManualInput = token => !manualInputFreezeCount && token === manualInputOwner;
const waitManualIdle = async (controller, token) => {
  const check = () => { if (!ownsManualInput(token)) throw new Error("Выбран другой источник управления."); };
  check();
  while (controller.busy || controller.starting || controller.stopping) { check(); await new Promise(resolve => setTimeout(resolve, 10)); }
  check();
};
const browserGamepad = ExplorerBrowserGamepad.install({
  window, document, secure: window.isSecureContext,
  getGamepads: typeof navigator.getGamepads === "function" ? () => navigator.getGamepads() : undefined,
  visible: () => !document.hidden, focused: () => document.hasFocus(), routeActive: () => activeProject()?.id === "02" && !dialog.open,
  cameraReady: rosmasterCameraReady, now: () => performance.now(), post,
  controls: async () => { await loadRobot(); return model.robot.controls; }, nativeState: () => model.robot.teleop || {},
  claim: () => claimManualInput("gamepad"), ownershipCurrent: ownsManualInput,
  beforeConnect: async token => { await stopKeyboard(true); await waitManualIdle(keyboard, token); if (keyboard.stopUnconfirmed) throw new Error("STOP клавиатуры не подтверждён."); await observedNative.requirePermission("browser_gamepad"); },
  filterKeys: filterManualKeysForCurrentOwnerConstraints,
  chassisProhibited: () => !observedNative.chassisAllowed(),
  render: () => { model.forceRender = true; render(); }, error: message => toast(message, true)
});
keyboard.options = {
  post, filterKeys: filterManualKeysForCurrentOwnerConstraints,
  ready: () => activeProject()?.id === "02" && rosmasterCameraReady() && document.hasFocus() && !dialog.open,
  claim: () => claimManualInput("keyboard"), ownershipCurrent: ownsManualInput,
  beforeConnect: async token => { await browserGamepad.disconnect("Выбрана клавиатура."); await waitManualIdle(browserGamepad, token); if (browserGamepad.stopUnconfirmed) throw new Error("STOP геймпада не подтверждён."); },
  prepare: async () => {
    if (!await manualPreview.ensureFresh()) throw new Error("Дождитесь свежих кадров ROSMASTER.");
    if (!model.robot.controls) await loadRobot();
    if (!ExplorerBrowserGamepad.qualifiedControls(model.robot.controls)) throw new Error("Штатная раскладка explorer-manual-v2 не подтверждена.");
    await observedNative.requirePermission("keyboard");
  },
  changed: () => { if (rosmasterDemonstrationRoute()) { model.forceRender = true; render(); } }
};
const mobileEvidence = createMobileEvidenceClient({
  fetchEvidence: () => api("/api/mobile/evidence"),
  onChange: () => { if (rosmasterDemonstrationRoute()) { model.forceRender = true; render(); } }
});
let mobileEvidenceSignature = "";
let skillWorkflowSignature = "";
let recoveredCaptureEpisode = "";

function experimentState(id) { return array(model.state?.experiments).find((entry) => projectId(entry.id || entry.project_id) === id) || {}; }
function heading(title, description, eyebrow = "Исследовательская станция", actions = "") { return `<div class="page-heading"><div><p class="eyebrow">${esc(eyebrow)}</p><h1>${esc(title)}</h1>${description ? `<p>${esc(description)}</p>` : ""}</div>${actions ? `<div class="heading-actions">${actions}</div>` : ""}</div>`; }
function notice(title, description, kind = "info") { return `<div class="notice ${kind}">${icon("info")}<div>${title ? `<strong>${esc(title)}</strong>` : ""}<p>${esc(description)}</p></div></div>`; }
function empty(title, description, symbol = "folder") { return `<div class="empty-state">${icon(symbol)}<strong>${esc(title)}</strong><p>${esc(description)}</p></div>`; }
function readinessCard(project) {
  const admission = model.state?.admission || {};
  const reasons = [...array(admission.reasons), ...array(project ? experimentState(project.id).blockers : [])].map(reasonText);
  const unique = [...new Set(reasons)];
  const enabled = admission.physical_enabled === true;
  const observation = defined(admission.observer_authorized, admission.observation_authorized);
  return `<section class="panel admission-card"><div class="admission-title">${icon("shield")}Готовность навыков</div><p>${enabled ? "Управление доступно. Результаты каждой модели показаны в её карточке." : (unique.length ? "Проверяем необходимые условия для автоматического выполнения навыков." : "Получаем сведения о калибровках, наблюдении и доступных операциях.")}</p>${admission.owner_authorization ? `<p>${esc(admission.owner_authorization)}</p>` : ""}${observation === true ? '<span class="badge good">Наблюдение подтверждено</span>' : ""}${unique.length ? `<ul class="blocker-list">${unique.slice(0, 4).map((reason) => `<li>${esc(reason)}</li>`).join("")}</ul>` : ""}<button class="button secondary full" type="button" data-action="choose-skill" ${enabled ? "" : 'disabled aria-disabled="true"'} title="${esc(enabled ? "Открыть подготовленные навыки" : unique.join(". ") || "Ожидаем подтверждения готовности оборудования")}">${enabled ? "Выбрать навык" : "Запуск навыка пока недоступен"}</button><a class="text-link" style="margin-top:14px;font-size:10px" href="http://explorer.local:8080" target="_blank" rel="noopener noreferrer">Штатное ручное управление <span aria-hidden="true">↗</span></a></section>`;
}
function experimentsGrid() {
  return `<div class="experiment-grid">${PROJECTS.map((project) => { const state = experimentState(project.id); return `<a class="experiment-card" href="/experiments/${project.id}" data-link><div class="experiment-top"><span class="experiment-index">ОПЫТ ${project.id}</span>${icon(Number(project.id) === 1 ? "chip" : "robot", "nav-icon")}</div><h3>${esc(project.title)}</h3><p>${esc(project.brief)}</p><div class="experiment-bottom">${badge(state.status, "Ожидаем состояние")}<span class="experiment-arrow" aria-hidden="true">↗</span></div></a>`; }).join("")}</div>`;
}
const TERMINAL_JOBS = ["completed", "complete", "succeeded", "success", "failed", "error", "cancelled", "canceled"];
function jobTitle(job) { return text(job.title, text(job.name, { mobile_manipulation: "Мобильная конфета · скриптовая симуляция", manipulation_selective_repair: "Исправление размещения · симуляция", retarget_import: "Импорт прямого показа для анализа", manipulation_curriculum: "BC → PPO · предметная задача", manipulation_benchmark: "Измерение процессов MuJoCo", fault_repair: "Диагностика и компенсация в симуляции", manipulation: "Предметная задача · PPO", manipulation_student: "Предметная задача · визуальный ACT", policy_transfer: "Перенос предсказаний ACT", handover_runtime_test: "Координатор передачи · программные сценарии", sim_pilot: "Обучение в симуляции", dagger: "Обучение с подсказками DAgger", skill_cycle: "Проверка цикла навыка", program_trace: "Разметка фаз программы", world_model: "Модель изменений сцены", camera_recording: "Запись наблюдения", recording: "Запись наблюдения", train: "Обучение модели", train_act: "Обучение ACT по показу", mobile_import: "Перенос показа ROSMASTER", station_import: "Перенос показа SO-101", station_rollout: "Попытка модели SO-101", station_shadow: "Проверка предсказаний без движения", virtual_transfer: "Проверка геометрического переноса", handover_test: "Проверка протокола передачи", evaluation: "Оценка навыка" }[job.kind] || job.kind || "Исследовательская задача")); }
function jobMetrics(job) {
  const metrics = job.metrics || job.result?.metrics || {};
  const list = [];
  const steps = defined(metrics.updates, metrics.steps, job.completed_updates);
  if (steps !== undefined) list.push(`Шаги: ${count(steps)}`);
  if (number(metrics.loss) !== null) list.push(`Ошибка: ${Number(metrics.loss).toPrecision(3)}`);
  if (number(metrics.reward) !== null) list.push(`Награда: ${Number(metrics.reward).toFixed(2)}`);
  if (number(metrics.episodes) !== null) list.push(`Эпизоды: ${count(metrics.episodes)}`);
  return list;
}
function jobRows(jobs, emptyDescription = "Здесь появятся ваши записи, обучение и проверки.") {
  if (!jobs.length) return empty("Пока нет запусков", emptyDescription, "clock");
  return `<div class="job-list">${jobs.slice(0, 12).map((job) => {
    const state = String(job.status || job.state || "queued").toLowerCase(); const id = text(job.id, job.job_id); const metrics = jobMetrics(job);
    let progress = number(defined(job.progress, job.progress_fraction));
    if (progress !== null && progress <= 1) progress *= 100;
    const project = projectId(job.project_id);
    const details = [project ? `Опыт ${project}` : job.project_id === "personal" ? "Личная лаборатория" : "", dateTime(job.created_at || job.created_utc || job.started_at || job.created), ...metrics].filter(Boolean).join(" · ");
    const buttons = [];
    if (["failed", "cancelled"].includes(state) && ["station_import", "mobile_import", "retarget_import"].includes(job.kind)) buttons.push(`<button type="button" class="button small secondary" data-job="${esc(id)}" data-job-action="retry">Повторить перенос</button>`);
    if (state === "running" && ["sim_pilot", "train_act", "rosmaster_skill_train", "so101_skill_train", "world_model", "dagger", "skill_cycle", "manipulation", "manipulation_student", "manipulation_benchmark", "manipulation_curriculum", "mobile_manipulation", "manipulation_selective_repair", "policy_transfer", "fault_repair"].includes(job.kind)) buttons.push(`<button type="button" class="icon-button" data-job="${esc(id)}" data-job-action="pause" aria-label="Приостановить: ${esc(jobTitle(job))}" title="Приостановить">${icon("pause")}</button>`);
    if (state === "paused" && ["sim_pilot", "train_act", "rosmaster_skill_train", "so101_skill_train", "world_model", "dagger", "skill_cycle", "manipulation", "manipulation_student", "manipulation_benchmark", "manipulation_curriculum", "mobile_manipulation", "manipulation_selective_repair", "policy_transfer", "fault_repair"].includes(job.kind)) buttons.push(`<button type="button" class="icon-button" data-job="${esc(id)}" data-job-action="resume" aria-label="Продолжить: ${esc(jobTitle(job))}" title="Продолжить">${icon("play")}</button>`);
    if (!TERMINAL_JOBS.includes(state) && !["cancel_requested", "pause_requested", "resume_requested"].includes(state)) buttons.push(`<button type="button" class="icon-button" data-job="${esc(id)}" data-job-action="cancel" aria-label="Отменить: ${esc(jobTitle(job))}" title="Отменить">${icon("cancel")}</button>`);
    return `<article class="job-row"><span class="job-mark">${icon(/record/.test(job.kind || "") ? "camera" : "chip")}</span><div class="job-description"><div class="job-name">${esc(jobTitle(job))}${badge(state)}</div><div class="job-detail">${esc(details)}</div>${progress !== null ? `<div class="job-progress" role="progressbar" aria-label="Ход задачи" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.max(0, Math.min(100, Math.round(progress)))}"><span style="width:${Math.max(0, Math.min(100, progress))}%"></span></div>` : ""}${job.error ? `<div class="job-error">${esc(text(job.error, "Задача завершилась с ошибкой"))}</div>` : ""}</div><div class="job-actions">${buttons.join("")}</div></article>`;
  }).join("")}</div>`;
}
function jobsPanel(jobs, title = "Последние запуски") { return `<section class="panel"><div class="panel-heading"><div><h2>${esc(title)}</h2><p>Сохранённые задачи и их фактическое состояние</p></div><span class="badge neutral">${count(jobs.length)}</span></div>${jobRows(jobs)}</section>`; }

function dashboard() {
  return heading("Всё начинается с наблюдения", "Ваша станция для показов, обучения и проверки навыков. Каждый опыт сохраняет свой путь от данных к результату.", "Explorer / Рабочее пространство", '<button class="button secondary" data-action="record" type="button">' + icon("record") + 'Записать наблюдение</button>') + nodeCards() + `<div class="overview-grid"><div id="camera-slot"></div><div class="side-stack"><section class="panel next-card"><p class="eyebrow">Следующий шаг</p><h2>Дайте роботу<br>понятную задачу</h2><p>Начните с короткого запуска в симуляции или сохраните наблюдение за реальным показом.</p><a href="/experiments/01" data-link class="button primary">Открыть первый опыт ${icon("arrow")}</a></section>${readinessCard()}</div></div><section class="spaced-section"><div class="section-heading"><div><h2>Шесть опытов в одной системе</h2><p>Единые данные, отдельные исследовательские вопросы</p></div><a class="text-link" data-link href="/research">Исследования <span aria-hidden="true">→</span></a></div>${experimentsGrid()}</section><div class="spaced-section">${jobsPanel(model.jobs.slice(0, 6))}</div>`;
}
function flow(project) { return `<section class="panel"><div class="panel-heading"><h2>Как проходит опыт</h2>${badge(experimentState(project.id).status, "Ожидаем состояние")}</div><div class="panel-body flow-steps">${project.steps.map(([title, description], index) => `<div class="flow-step"><span class="step-number">${String(index + 1).padStart(2, "0")}</span><div><h3>${esc(title)}</h3><p>${esc(description)}</p></div></div>`).join("")}</div></section>`; }
function simulationPanel(job) {
  const hasFrame = job && (job.frame_available === true || job.metrics?.frame_available === true || job.frame_url || job.result?.frame_available === true);
  return `<section class="panel simulation-panel"><div class="panel-heading"><div><h2>Наблюдение за симуляцией</h2><p>Кадры фактического запуска MuJoCo</p></div>${job ? badge(job.status) : '<span class="badge neutral">Нет активного кадра</span>'}</div><div class="simulation-stage" id="simulation-stage">${hasFrame ? `<img id="simulation-image" data-simulation-job="${esc(job.id || job.job_id)}" src="/api/simulations/${encodeURIComponent(job.id || job.job_id)}/frame?t=${Date.now()}" alt="Кадр фактического запуска симуляции"><div class="camera-placeholder" id="simulation-error" hidden>${icon("chip")}<strong>Кадр пока недоступен</strong><p>Проверяем следующий кадр этого запуска.</p></div>` : `<div class="camera-placeholder">${icon("chip")}<strong>${job ? "Запуск ещё не передал кадр" : "Здесь появится ваша симуляция"}</strong><p>${job ? "Метрики задачи доступны ниже. Изображение появится после получения настоящего кадра." : "Создайте запуск справа. Панель показывает только реально рассчитанные кадры."}</p></div>`}</div><div class="simulation-footer"><span>${esc(job ? jobTitle(job) : "Наблюдение не запущено")}</span><span>${job?.completed_updates !== undefined ? `Обновлений: ${count(job.completed_updates)} · ` : ""}${job?.frame_metadata?.wall_utc ? `Последний кадр: ${esc(dateTime(job.frame_metadata.wall_utc))} · ` : ""}Симуляция ≠ физическая проверка</span></div></section>`;
}
function simulationForm() {
  return `<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Новый запуск</p><h2>Первый шаг в симуляции</h2></div></div><div class="panel-body"><p class="form-description">Пилотный запуск проверяет обучение достижению цели, сохранение модели и результатов.</p><form id="simulation-form"><div class="form-grid"><div class="form-field full"><label for="sim-task">Задача</label><select id="sim-task" name="task"><option value="reach">Достичь цели</option></select></div><div class="form-field"><label for="sim-updates">Шаги обучения</label><input id="sim-updates" name="updates" type="number" min="32" max="100000" step="32" value="${model.simDraft.updates}" required></div><div class="form-field"><label for="sim-seed">Вариант запуска</label><input id="sim-seed" name="seed" type="number" min="0" max="2147483647" step="1" value="${model.simDraft.seed}" required><small>Одинаковое число — для повторения условий</small></div></div><button class="button primary full" type="submit" id="start-simulation">${icon("play")}Запустить обучение</button><p class="form-hint">Задача выполняется на Homebrain. Кнопки паузы и отмены появятся в списке запусков.</p><p id="simulation-form-error" class="form-error" role="alert" hidden></p></form></div></section>`;
}
function experimentPage(project) {
  if ([TRANSFER_PROJECT, SO101_SOURCE_PROJECT].includes(project.id)) return stationPage(project.id);
  if (["02", "03"].includes(project.id)) return experiment02Page(project);
  const state = experimentState(project.id);
  const jobs = model.jobs.filter((job) => projectId(job.project_id) === project.id);
  if (project.id === "05") return heading("Совместная передача ROSMASTER → SO101","Выберите два исходных навыка, закрепите общую задачу и сохраните настоящие записи каждого робота.","Исследовательский опыт / 05") + cooperativeWorkbenchPanel(cooperativeWorkbench.snapshot()) + `<details class="spaced-section"><summary>Программные проверки протокола и обрывов</summary>${handoverRuntimePanel()}</details><div class="spaced-section">${jobsPanel(jobs,"Запуски кооперации")}</div>`;
  const action = project.id === "01" ? '<a href="#simulation-form" class="button primary">' + icon("play") + 'Новый запуск</a>' : '<button type="button" class="button primary" data-action="record" data-project="' + project.id + '">' + icon("record") + 'Записать наблюдение</button>';
  let content = heading(project.title, project.subtitle, `Исследовательский опыт / ${project.id}`, action);
  if (state.summary) content += notice("Текущее состояние опыта", text(state.summary));
  if (project.id === "01") {
    const rendered = (entry) => ["sim_pilot", "dagger", "skill_cycle", "manipulation", "manipulation_student", "mobile_manipulation", "manipulation_curriculum", "manipulation_selective_repair"].includes(entry.kind);
    const job = jobs.find((entry) => rendered(entry) && !TERMINAL_JOBS.includes(entry.status)) || jobs.find(rendered);
    content += `<div class="two-column">${simulationPanel(job)}${simulationForm()}</div><div class="spaced-section">${flow(project)}</div>`;
    content += simulationPilotMetrics() + simulationAdvancedPanel() + manipulationPanel() + faultRepairPanel();
    content += `<div class="spaced-section">${worldTrainingPanel()}</div>`;
  } else {
    content += notice(project.id === "02" ? "Два разных материала" : "Запись наблюдения с камеры", project.id === "02" ? "Полный показ записывается через штатный ROSMASTER вместе с его командами и наблюдениями. Отдельная кнопка «Записать наблюдение» сохраняет только видео камеры." : "Сейчас мастер сохраняет видео сцены. Такой материал отмечается как наблюдение и не выдаётся за синхронизированный обучающий эпизод.");
    content += `<div class="overview-grid"><div><div id="camera-slot"></div>${project.id === "02" ? `<div class="spaced-section">${keyboardPanel()}</div><div class="spaced-section">${episodePanel()}</div>` : ""}</div><div class="side-stack">${readinessCard(project)}${flow(project)}</div></div>`;
    if (project.id === "02") content += `<div class="spaced-section">${mobileTrainingPanel()}</div>` + demonstrationWizardPanel("02");
    if (project.id === "05") content += handoverRuntimePanel();
  }
  content += `<div class="spaced-section">${jobsPanel(jobs, "Запуски этого опыта")}</div>`;
  if (project.id !== "01") content += `<div class="spaced-section">${recordingsPanel(project.id)}</div>`;
  return content;
}

function keyboardPanel() {
  const live = model.robot.teleop;
  const help = model.robot.controls?.help || {};
  return `<section class="panel"><div class="panel-heading"><div><h2>Показ с клавиатуры</h2><p>Штатный исполнитель ROSMASTER · текущие ограничения</p></div>${badge(keyboard.active ? keyboard.paused ? "paused" : "running" : "not_started")}</div><div class="panel-body"><p class="form-description">Наблюдайте за рабочей сценой во время управления. После ухода из окна клавиатура отключается и сама не возобновляет движение.</p><div class="teleop-actions"><button class="button ${keyboard.active ? "secondary" : "primary"}" type="button" data-action="keyboard-toggle">${keyboard.active ? "Отключить клавиатуру" : "Наблюдаю сцену · включить клавиатуру"}</button><button class="button danger" type="button" data-action="robot-stop">${icon("cancel")}STOP</button></div><div class="teleop-mode"><span>Режим руки</span><button class="button small ${keyboard.mode === "cartesian" ? "ghost" : "secondary"}" data-arm-mode="cartesian" type="button" ${keyboard.active ? "" : "disabled"}>XYZ</button><button class="button small ${keyboard.mode === "joint" ? "ghost" : "secondary"}" data-arm-mode="joint" type="button" ${keyboard.active ? "" : "disabled"}>Суставы</button></div><div class="keyboard-guide"><p><kbd>W</kbd><kbd>S</kbd> вперёд / назад · <kbd>A</kbd><kbd>D</kbd> влево / вправо · <kbd>Q</kbd><kbd>E</kbd> поворот</p><p>${esc(keyboard.mode === "joint" ? help.keyboard_joint || "J/L — J1; I/K — J2; U/O — J3; Y/H — J4; ,/. — J5." : help.keyboard_cartesian || "I/K — X; J/L — Y; U/O — Z.")}</p><p><kbd>N</kbd><kbd>M</kbd> захват · <kbd>Shift</kbd> точно · <kbd>Space</kbd> / <kbd>Esc</kbd> STOP · <kbd>Enter</kbd> продолжить</p></div>${keyboard.active && keyboard.paused ? '<p class="teleop-message">STOP зафиксирован. Отпустите клавиши; Enter — продолжить.</p>' : ""}${keyboard.error || model.robot.error ? `<p class="form-error">${esc(keyboard.error || model.robot.error)}</p>` : ""}<p class="form-hint">Углы руки — командная оценка. Измеренная обратная связь суставов ${live?.measured_joint_feedback === true ? "доступна по данным робота" : "не подтверждена"}.</p></div></section>`;
}
function demonstrationTeaching() {
  if (activeProject()?.id !== "03") {
    const capture = model.robot.nativeCapture, activeId = capture?.active_recording_id;
    const legacy = model.robot.teaching || {};
    if (activeId) return {active:{...(capture.capture || {}),id:activeId,source:"canonical",samples:capture.capture?.samples_written},last:array(capture.ready_recordings).at(-1)};
    return {...legacy,active:legacy.active ? {...legacy.active,source:"legacy"} : null};
  }
  const recording = direct03.status?.recording;
  const own = direct03.recordings.find(row => row.recording_id === direct03.recordId);
  const activeId = typeof recording?.active === "string" ? recording.active : recording?.active?.recording_id || recording?.active?.id;
  const activeRecord = direct03.recordings.find(row => row.recording_id === activeId);
  const current = activeId ? activeRecord || { recording_id: activeId } : own && ["recording", "finishing"].includes(own.state) ? own : null;
  return { active: current ? { ...current, id: current.recording_id || current.id, samples: current.samples_written } : null,
    last: direct03.recordings.find(row => ["complete", "completed"].includes(row.state)) || null };
}
function demonstrationRecordPath(operation) {
  if (activeProject()?.id === "03") return `/api/retarget/record/${operation}`;
  return operation !== "start" && demonstrationTeaching().active?.source === "legacy" ? `/api/robot/teaching/mobile/${operation}` : `/api/mobile/capture/record/${operation}`;
}
function experiment02Page(project) {
  const leader = project.id === "03";
  const jobs = model.jobs.filter(job => ["02", "03"].includes(projectId(job.project_id)));
  const teaching = demonstrationTeaching(), active = teaching.active;
  const status = active ? `Идёт запись · ${text(active.name, active.id)}` : "Запись остановлена";
  const guide = leader ? `<section class="panel spaced-section"><div class="panel-heading"><h2>Leader → рука · клавиатура → база</h2></div><div class="panel-body keyboard-guide"><p>Рука ROSMASTER получает команды от измеряемого SO-101 leader. Recenter задаёт видимый новый epoch относительно текущей командной оценки цели.</p><p><kbd>W</kbd>/<kbd>S</kbd> вперёд/назад, <kbd>A</kbd>/<kbd>D</kbd> mecanum, <kbd>Q</kbd>/<kbd>E</kbd> поворот. ${observedNative.chassisAllowed() ? "Разрешён отдельный наблюдаемый тест базы; удерживайте кнопку передачи движений." : "Клавиши базы заблокированы до отдельного разрешения наблюдаемого теста."}</p><p><kbd>Space</kbd>/<kbd>Esc</kbd> — STOP. Ручное зеркало не создаёт датасет.</p></div></section>` : ExplorerBrowserGamepad.keyboardGuide(model.robot.controls || {});
  const modes = leader ? `<section class="panel"><div class="panel-heading"><h2>Источник показа · SO-101 leader</h2></div><div class="panel-body"><div class="teleop-actions"><button class="button ${direct03.demoMode !== "record" ? "primary" : "secondary"}" type="button" data-action="direct03-mode" data-demo-mode="mirror">Зеркало · без записи</button><button class="button ${direct03.demoMode === "record" ? "primary" : "secondary"}" type="button" data-action="direct03-mode" data-demo-mode="record">Запись показа для обучения</button></div><p>Обучение, проверка и модели общие с опытом 02. Отличается только способ управления рукой во время демонстрации.</p></div></section>` : "";
  const controls = leader ? modes + direct03Panel() : keyboardPanel() + ExplorerBrowserGamepad.panel(browserGamepad.status());
  const recordPanel = !leader || direct03.demoMode === "record" ? `<div class="spaced-section">${episodePanel()}${episodeStagePanel()}</div>` : notice("Зеркало без обучения", "Включение ручного контура не создаёт эпизод. Для демонстраций выберите режим записи и подтверждённую версию навыка.");
  return heading("Обучение ROSMASTER", "Единый мобильный манипулятор: восприятие, база, рука и захват. Общие 9D данные, охват показов, обучение и версии навыка.", `Исследовательский опыт / ${project.id}`) +
    notice(observedNative.chassisAllowed()?"Наблюдаемый тест шасси разрешён":"Шасси по умолчанию заблокировано", "Любое движение — только в отдельной проверке под текущим наблюдением пользователя. Исходные настройки штатного исполнителя сохранены.") + '<a class="button secondary" href="/evening" data-link>Открыть мастер наблюдаемой сессии</a>' +
    `<div class="experiment02-layout"><aside class="experiment02-aside"><div id="camera-slot"></div><div class="manual-session-status" role="status"><strong>${esc(status)}</strong><span id="manual-camera-status">${rosmasterCameraReady() ? "Свежие кадры ROSMASTER получены" : "Ожидаем свежий кадр ROSMASTER"}</span><span>Предпросмотр ≈ 5 кадров/с; исходная запись сохраняет свою частоту.</span></div>${guide}${leader ? `<section class="panel spaced-section"><div class="panel-heading"><h3>Измерения leader</h3></div><div class="panel-body">${stationMeasurements(true)}</div></section>` : ""}</aside><div class="experiment02-workflow">` +
    controls + skillWorkflowPanel() + recordPanel +
    `<div class="spaced-section">${mobileEvidencePanel(mobileEvidence.snapshot(), {preview: manualPreview.state(), teaching})}</div><div class="spaced-section">${jobsPanel(jobs, "Обучение и проверка ROSMASTER")}</div><div class="spaced-section">${recordingsPanel(project.id)}</div></div></div>`;
}

function episodeStagePanel() {
  const active = demonstrationTeaching().active;
  if (!active || !skillWorkflowUI.capture) return "";
  const phases = skillWorkflowUI.captureContext?.spec?.stages || skillWorkflowUI.state?.spec?.stages || [];
  return `<section class="panel spaced-section"><div class="panel-heading"><h3>Фаза текущего показа</h3></div><div class="panel-body"><p>Отмечайте переход в момент начала фазы. Это отметка оператора, а не автоматическое подтверждение контакта или успеха.</p><div class="teleop-actions">${phases.map(phase => `<button class="button secondary small" type="button" data-episode-stage="${esc(phase.id)}" data-episode-id="${esc(active.id)}">${esc(phase.title)}</button>`).join("")}</div></div></section>`;
}
function episodePanel() {
  const teaching = demonstrationTeaching(); const active = teaching.active; const last = teaching.last;
  return `<section class="panel"><div class="panel-heading"><div><h2>Запись ROSMASTER</h2><p>Камера, датчики и доступные команды</p></div>${badge(active ? "running" : "not_started")}</div><div class="panel-body">${active ? `<p class="form-description">Идёт запись: ${esc(text(active.name, active.id || "показ"))}. Завершите показ и отметьте наблюдавшийся результат.</p><div class="episode-outcomes"><button class="button primary" data-episode-outcome="success" data-episode-id="${esc(active.id)}" type="button">Задача выполнена</button><button class="button secondary" data-episode-outcome="failure" data-episode-id="${esc(active.id)}" type="button">Не получилось</button><button class="button secondary" data-episode-outcome="unknown" data-episode-id="${esc(active.id)}" type="button">Результат неясен</button></div><p class="form-hint">Оценка оператора сохраняется отдельно от независимой проверки навыка.</p>` : `<form id="episode-form"><div class="form-field"><label for="episode-name">Название задачи</label><input id="episode-name" name="name" value="${esc(model.robot.episodeName)}" maxlength="80" required placeholder="Например: подвести захват к предмету"></div><button type="submit" class="button primary" style="margin-top:16px">${icon("record")}Начать запись</button><p class="form-hint">При неизвестных положениях руки сохраняются кадры и датчики. Полный показ с командами сохраняется, когда их история доступна.</p></form>`}${last ? `<div class="episode-last"><span class="status-dot good"></span>Последний показ сохранён${last.outcome ? ` · ${esc({ success: "отмечен успех", failure: "отмечена неудача", unknown: "результат неясен" }[last.outcome] || last.outcome)}` : ""}</div>` : ""}${array(teaching.record_errors).length ? `<p class="form-error">${esc(teaching.record_errors.map(reasonText).join(". "))}</p>` : ""}</div></section>`;
}

// ENGINEERING_GLUE: explicit whole-episode holdout and offline scene prediction only.
function worldDatasetPlan(dataset, requestedDevelopment = null) {
  const fail = (reason) => ({ reason, training: [], development: [], sealed: [], unused: [], episodes: [] });
  if (!dataset || typeof dataset.dataset_manifest !== "string" || !dataset.dataset_manifest.trim()) return fail("Выберите набор записанных эпизодов.");
  const episodes = array(dataset.episodes); const ids = episodes.map((episode) => episode.id);
  if (!ids.length || ids.some((id) => typeof id !== "string" || !id) || new Set(ids).size !== ids.length) return fail("Нужны различные эпизоды с уникальными названиями.");
  const trainSplits = ["train", "training", "imitation_train"], devSplits = ["development", "validation", "dev"], sealedSplits = ["test", "sealed_test", "sealed_final_test"];
  if (episodes.some((episode) => ![...trainSplits, ...devSplits, ...sealedSplits].includes(episode.split))) return fail("Разделение исходных эпизодов на обучение, проверку и тест ещё не подтверждено.");
  const sealed = episodes.filter((episode) => sealedSplits.includes(episode.split));
  const available = episodes.filter((episode) => !sealedSplits.includes(episode.split));
  const train = available.filter((episode) => trainSplits.includes(episode.split));
  const declaredDevelopment = available.filter((episode) => devSplits.includes(episode.split));
  if (available.length < 2 || !train.length) return fail("Нужны хотя бы два незакрытых эпизода, включая один обучающий.");
  const defaults = declaredDevelopment.length ? declaredDevelopment.map((episode) => episode.id) : train.slice(-Math.max(1, Math.ceil(train.length * .2))).map((episode) => episode.id);
  const requested = requestedDevelopment === null ? defaults : requestedDevelopment;
  if (!Array.isArray(requested) || new Set(requested).size !== requested.length || requested.some((id) => !available.some((episode) => episode.id === id))) return fail("Проверка может использовать только различные незакрытые эпизоды этого набора.");
  const development = available.filter((episode) => requested.includes(episode.id)).map((episode) => episode.id);
  const training = train.filter((episode) => !development.includes(episode.id)).map((episode) => episode.id);
  const unused = available.filter((episode) => !training.includes(episode.id) && !development.includes(episode.id)).map((episode) => episode.id);
  return { reason: !development.length ? "Выберите хотя бы один целый эпизод для проверки." : !training.length ? "Оставьте хотя бы один обучающий эпизод вне проверки." : "", training, development, sealed: sealed.map((episode) => episode.id), unused, episodes };
}
function worldSelectedDataset() { return worldLearning.datasets.find((dataset) => dataset.dataset_manifest === worldLearning.manifest); }
function worldJobs() { return model.jobs.filter((job) => job.kind === "world_model" && projectId(job.project_id) === "01"); }
function worldTrainingRequest() {
  if (worldLearning.error || worldLearning.loading) throw new Error("Дождитесь доступного каталога записей.");
  const dataset = worldSelectedDataset(); const plan = worldDatasetPlan(dataset, worldLearning.development);
  if (plan.reason) throw new Error(plan.reason);
  if (worldJobs().some((job) => !TERMINAL_JOBS.includes(job.status))) throw new Error("Модель сцены уже обучается или ожидает запуска.");
  return { kind: "world_model", project_id: "01", config: { dataset_manifest: dataset.dataset_manifest, optimizer_steps: 120, device: "cpu", development_episode_ids: plan.development } };
}
function isWorldModelArtifact(item) {
  const job = model.jobs.find((entry) => entry.id === item?.lineage?.job_id);
  return item?.kind === "world_model" || item?.source_type === "world_model" || item?.task === "offline_one_step_observation_prediction" || job?.kind === "world_model";
}
function worldMetric(value) { return typeof value === "number" && Number.isFinite(value) ? value.toExponential(5) : "Нет данных"; }
function worldComparison(evaluation) {
  const learned = [evaluation.action_conditioned, evaluation.action_agnostic], baseline = evaluation.persistence;
  const values = (entry) => [entry?.episode_weighted?.rgb_mse_0_1, entry?.episode_weighted?.normalized_state_mse];
  const complete = (entry) => values(entry).every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0);
  if (!baseline || !complete(baseline) || !(baseline.episodes > 0 && baseline.transitions > 0) || !learned.every((entry) => complete(entry) && entry.episodes > 0 && entry.episodes === baseline.episodes && entry.transitions === baseline.transitions)) return "Сравнение будет доступно после получения сопоставимых ошибок на проверочных эпизодах.";
  if (learned.every((entry) => values(baseline).every((value, index) => value < values(entry)[index]))) return "В этом запуске сохранение текущего наблюдения точнее обеих обученных моделей по двум показателям.";
  return "Сравнивайте ошибки кадра и состояния отдельно. Эти показатели относятся только к выбранным проверочным эпизодам.";
}
function worldResults(jobs) {
  const methods = [["action_conditioned", "Модель с командами"], ["action_agnostic", "Модель без команд"], ["persistence", "Сохранить текущее наблюдение"], ["command_copy_plus_rgb_persistence", "Повторить команду и текущий кадр"]];
  return jobs.filter((job) => job.status === "completed").slice(0, 4).map((job) => {
    const result = job.result || {}, evaluation = result.evaluation || {};
    const rows = methods.filter(([key]) => evaluation[key]);
    const optimization = result.optimization || {};
    return `<section class="panel spaced-section"><div class="panel-heading"><div><h3>Проверка модели изменений сцены</h3><p>${esc(dateTime(job.updated || job.created))}</p></div>${badge(job.status)}</div><div class="panel-body"><p class="form-description">${esc(worldComparison(evaluation))}</p>${rows.length ? `<div class="world-table-wrap"><table class="world-comparison"><caption>Средние ошибки с одинаковым весом каждого эпизода. Меньше — точнее на этой записи.</caption><thead><tr><th scope="col">Метод</th><th scope="col">Ошибка кадра, MSE [0–1]</th><th scope="col">Ошибка состояния, MSE норм.</th><th scope="col">Эпизоды / переходы</th></tr></thead><tbody>${rows.map(([key, label]) => `<tr><th scope="row">${esc(label)}</th><td>${worldMetric(evaluation[key].episode_weighted?.rgb_mse_0_1)}</td><td>${worldMetric(evaluation[key].episode_weighted?.normalized_state_mse)}</td><td>${count(evaluation[key].episodes)} / ${count(evaluation[key].transitions)}</td></tr>`).join("")}</tbody></table></div>` : notice("Численные результаты ещё не получены", "Состояние запуска сохранено; сравнение появится при получении фактических метрик.")}<dl class="skill-metadata">${["action_conditioned", "action_agnostic"].filter((key) => optimization[key]).map((key) => kv(`${key === "action_conditioned" ? "С командами" : "Без команд"}: обновления весов`, count(optimization[key].optimizer_steps)) + kv("Изменение весов", optimization[key].weights_changed === true ? "Подтверждено" : "Не подтверждено") + kv("Ошибка повторной загрузки", worldMetric(optimization[key].reload_max_abs_error))).join("")}${kv("Независимость сеансов записи", result.source?.independent_session_split_verified === true ? "Подтверждена" : "Не подтверждена")}${kv("Успех физической задачи", "Этим запуском не оценивался")}</dl><p class="form-hint">${result.source?.state_basis === "command_estimate" ? "Состояние исходника — командная оценка; низкая ошибка может отражать повторение сохранённых команд. " : ""}Это одношаговый прогноз по записям. Управление роботом и проверка выполнения задачи выполняются отдельно.</p></div></section>`;
  }).join("");
}
function worldTrainingPanel() {
  const dataset = worldSelectedDataset();
  const signature = dataset ? JSON.stringify([dataset.dataset_manifest, dataset.episodes]) : "";
  if (signature !== worldLearning.signature) { worldLearning.signature = signature; worldLearning.development = dataset ? worldDatasetPlan(dataset).development : null; }
  const plan = worldDatasetPlan(dataset, worldLearning.development); const jobs = worldJobs();
  const active = jobs.some((job) => !TERMINAL_JOBS.includes(job.status));
  const reason = worldLearning.loading ? "Получаем каталог записей." : worldLearning.error || (worldLearning.busy ? "Ставим обучение в очередь." : active ? "Модель сцены уже обучается или ожидает запуска." : plan.reason);
  const source = { measured_state: "Измеренное состояние", command_estimate: "Командная оценка состояния" }[dataset?.state_basis] || "Основа состояния не уточнена";
  const origin = { real_mujoco_ppo_rollouts: "Записи MuJoCo и PPO", station_demonstration: "Показы SO-101", explorer_mobile_episode_v3: "Показы ROSMASTER" }[dataset?.source] || "Тип исходных записей не уточнён";
  const action = { simulated_command: "Команды симуляции", command_endpoint_labels: "Записанные командные метки", accepted_action: "Принятые команды", dispatched_command: "Отправленные команды" }[dataset?.action_basis] || "Основа команд не уточнена";
  return `<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Дополнительный опыт / Прогноз по записи</p><h2>Модель изменений сцены</h2></div><span class="badge neutral">Без выполнения на роботе</span></div><div class="panel-body"><p class="form-description">Модель учится прогнозировать следующий кадр и состояние с учётом записанной команды. Сравниваем её с моделью без команд и сохранением текущего наблюдения. Управление движением остаётся у штатного контроллера.</p>${worldLearning.datasets.length ? `<div class="form-field"><label for="world-dataset">Набор записанных эпизодов</label><select id="world-dataset"><option value="">Выберите набор данных</option>${worldLearning.datasets.map((item) => `<option value="${esc(item.dataset_manifest)}" ${item.dataset_manifest === worldLearning.manifest ? "selected" : ""} ${worldDatasetPlan(item).reason ? "disabled" : ""}>${esc(item.name || item.id || "Записанные эпизоды")}${typeof item.created === "number" ? " · " + esc(dateTime(item.created)) : ""}${worldDatasetPlan(item).reason ? " · нужны отдельные эпизоды обучения и проверки" : ""}</option>`).join("")}</select></div>` : notice("Каталог записей", worldLearning.error || (worldLearning.loading ? "Получаем доступные наборы данных." : "Здесь появятся записанные эпизоды симуляции и показов."))}<button type="button" class="quiet-button text-link" data-action="world-refresh">Обновить каталог записей</button>${dataset ? `<p class="form-hint">${esc(origin)} · ${esc(source)} · ${esc(action)}</p><dl class="skill-metadata">${kv("Эпизодов для обучения", count(plan.training.length))}${kv("Целых эпизодов для проверки", count(plan.development.length))}${kv("Закрытых тестовых эпизодов — не используются", count(plan.sealed.length))}${plan.unused.length ? kv("Других неиспользуемых эпизодов", count(plan.unused.length)) : ""}</dl><details><summary>Выбрать проверочные эпизоды · ${count(plan.development.length)}</summary><p class="form-hint">Сохраняем исходные проверочные эпизоды; иначе по умолчанию откладываем последние 20%, минимум один. Закрытые тесты не участвуют. Неудачные и неоценённые попытки также могут описывать изменения сцены.</p><div class="world-episodes">${plan.episodes.map((episode) => `<label class="world-episode"><input type="checkbox" data-world-development="${esc(episode.id)}" ${plan.development.includes(episode.id) ? "checked" : ""} ${plan.sealed.includes(episode.id) ? "disabled" : ""}><span>${esc(episode.id)} · ${count(episode.frames)} кадров${plan.sealed.includes(episode.id) ? " · закрытый тест" : ["development", "validation", "dev"].includes(episode.split) ? " · проверочный эпизод исходника" : ""}</span></label>`).join("")}</div></details>` : ""}<button type="button" class="button primary full" data-action="world-train" ${reason ? "disabled" : ""}>${icon("chip")}Обучить модель сцены на CPU</button><p class="form-hint" role="status">${esc(reason || "120 обновлений каждой обучаемой модели. Проверочные эпизоды целиком исключены из обучения; исходное разделение записей сохраняется.")}</p>${jobRows(jobs.slice(0, 5), "Здесь появятся фактические состояния запуска и сравнение прогнозов.")}</div></section>${worldResults(jobs)}`;
}
async function refreshWorldDatasets() {
  if (worldLearning.loading) return;
  worldLearning.loading = true;
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 8000);
  try { const result = await api("/api/datasets", { signal: controller.signal }); worldLearning.datasets = array(result.datasets); worldLearning.error = null; }
  catch (_) { worldLearning.error = "Каталог записей не обновлён. Повторите запрос перед обучением."; }
  finally { clearTimeout(timeout); worldLearning.loading = false; worldLearning.lastLoad = Date.now(); if (activeProject()?.id === "01") { model.forceRender = true; render(); } }
}
// END offline scene-prediction UI.

// ENGINEERING_GLUE: offline ROSMASTER demonstration selection and result presentation.
function mobileImportJobs() {
  return model.jobs.filter((job) => job.kind === "mobile_import" && projectId(job.project_id) === "02");
}
async function loadRobot() {
  const results = await Promise.allSettled([api("/api/robot/teleop"), api("/api/robot/teleop/controls"), api("/api/robot/teaching/mobile"),api("/api/mobile/capture/capabilities"),api("/api/mobile/capture/status")]);
  if (results[0].status === "fulfilled") { model.robot.teleop = results[0].value; model.robot.error = null; }
  else model.robot.error = results[0].reason.message;
  if (results[1].status === "fulfilled") model.robot.controls = results[1].value;
  if (results[2].status === "fulfilled") model.robot.teaching = results[2].value;
  model.robot.captureCapabilities = results[3].status === "fulfilled" ? results[3].value : null;
  model.robot.nativeCapture = results[4].status === "fulfilled" ? results[4].value : null;
}
async function sendKeyboard() { await keyboard.send(); }
async function stopKeyboard(disconnect = false) { await keyboard.stop(disconnect); }
async function startKeyboard() { await keyboard.start(); main.focus({ preventScroll: true }); }
async function setArmMode(mode) { await keyboard.configure("mode", mode); }

function metric(label, value, note = "") { return `<article class="metric-card"><div class="metric-value">${esc(count(value))}</div><div class="metric-label">${esc(label)}</div>${note ? `<div class="metric-note">${esc(note)}</div>` : ""}</article>`; }
function researchPage() {
  const r = model.research || {}; const c = r.counts || r.registry || {};
  const papers = defined(c.papers, c.literature, r.candidates, r.paper_count, r.papers_total, r.literature?.total, Array.isArray(r.papers) ? r.papers.length : undefined);
  const verified = defined(c.verified_papers, c.papers_verified, r.primary_verified, r.verified_papers, r.literature?.verified);
  const runs = array(r.runs); const hypotheses = array(r.hypotheses);
  const status = text(r.status?.summary, text(r.summary, typeof r.status === "string" ? r.status : "Исследовательский реестр связывает источники, гипотезы, данные и фактические запуски."));
  return heading("Исследования", "Проверяем идеи по данным, сохраняем происхождение методов и отделяем программную готовность от научного результата.", "Explorer / Research hub") + acceptancePanel() + `<section class="panel research-status"><span class="icon-box">${icon("research")}</span><div><h2>От вопроса — к проверяемому результату</h2><p>${esc(status)}</p></div></section><div class="metric-grid">${metric("Источников в реестре", papers, "Наличие записи ещё не означает глубокое прочтение")}${metric("Проверенных источников", verified)}${metric("Исследовательских запусков", defined(c.runs, r.run_count, Array.isArray(r.runs) ? runs.length : undefined))}${metric("Гипотез", defined(c.hypotheses, r.hypothesis_count, Array.isArray(r.hypotheses) ? hypotheses.length : undefined))}</div><div class="two-column"><section class="panel"><div class="panel-heading"><h2>Проверяемые гипотезы</h2></div><div class="panel-body">${hypotheses.length ? `<ul class="plain-list">${hypotheses.map((entry) => `<li><h3>${esc(text(entry.title, text(entry.question, entry.id || "Исследовательский вопрос")))}</h3><p>${esc(text(entry.description, text(entry.hypothesis, entry.statement || "")))}</p><div class="meta">${esc([text(entry.id), text(entry.status)].filter(Boolean).join(" · "))}</div></li>`).join("")}</ul>` : empty("Гипотезы ещё не загружены", "Здесь появятся вопросы из исследовательского реестра.", "research")}</div></section><section class="panel"><div class="panel-heading"><h2>Фактические результаты</h2></div><div class="panel-body">${runs.length ? `<ul class="plain-list">${runs.slice(0, 10).map((entry) => `<li><h3>${esc(text(entry.title, text(entry.name, entry.id || "Исследовательский запуск")))}</h3><p>${esc(text(entry.summary, text(entry.result, "Результат сохранён в реестре")))}</p><div class="meta">${esc(dateTime(entry.created_at || entry.wall_utc || entry.created))} · ${esc(text(entry.project_id, "Общая программа"))}</div></li>`).join("")}</ul>` : empty("Научные результаты ещё не получены", "Графики и выводы появятся после реальных запусков и независимой оценки. Они не создаются из примерных данных.", "book")}</div></section></div><section class="spaced-section"><div class="section-heading"><h2>Общая программа</h2></div>${experimentsGrid()}</section>`;
}
function skillsPage() {
  const list = model.skills.filter((skill) => !isWorldModelArtifact(skill));
  return heading("Библиотека навыков", "Данные, модели и независимые проверки в одном месте. Записанные показы, обучение и попытки каждой версии сохраняются отдельно.", "Explorer / Навыки") + notice("Как появляется навык", "Показы связываются с набором данных, обучение создаёт новую версию модели, а отдельная оценка показывает границы её применения.") + (list.length ? `<div class="skill-grid">${list.map((skill) => {
    const validation = skill.validation || skill.evaluation || {};
    const passed = validation.accepted === true || skill.validated === true;
    return `<article class="panel skill-card"><div class="skill-top">${icon("skills", "nav-icon")}${badge(passed ? "validated" : skill.status || "not_validated")}</div><h3>${esc(text(skill.title, text(skill.name, skill.id || "Навык")))}</h3><p>${esc(text(skill.description, skill.summary || "Описание навыка ещё не добавлено."))}</p><dl class="skill-metadata"><div class="key-value"><dt>Версия</dt><dd>${esc(text(skill.version, text(skill.model_version, "Не указана")))}</dd></div><div class="key-value"><dt>Источник опыта</dt><dd>${esc(projectId(skill.project_id) ? `Опыт ${projectId(skill.project_id)}` : "Не указан")}</dd></div><div class="key-value"><dt>Набор данных</dt><dd>${esc(text(skill.dataset_id, "Не указан"))}</dd></div><div class="key-value"><dt>Независимая оценка</dt><dd>${passed ? "Принята" : "Не подтверждена"}</dd></div></dl></article>`;
  }).join("")}</div>` : `<section class="panel">${empty("Первые навыки ещё впереди", "Начните с запуска симуляции или наблюдения за показом. Реестр сохранит связь между данными, моделью и проверками.", "skills")}<div class="panel-body" style="text-align:center"><a href="/experiments/01" data-link class="button primary">Открыть первый опыт ${icon("arrow")}</a></div></section>`);
}

function kv(label, value) { return `<div class="key-value"><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`; }
function bytes(value) {
  const amount = number(value);
  if (amount === null) return "Не измерено";
  if (amount >= 1024 ** 4) return `${(amount / 1024 ** 4).toFixed(1)} ТБ`;
  if (amount >= 1024 ** 3) return `${(amount / 1024 ** 3).toFixed(1)} ГБ`;
  return `${(amount / 1024 ** 2).toFixed(0)} МБ`;
}
function storagePanel() {
  const s = model.state?.storage || {}; const volume = s.artifacts || s.primary || s;
  const total = number(defined(volume.total_bytes, volume.total)); const free = number(defined(volume.free_bytes, volume.free));
  const used = total !== null && free !== null ? Math.max(0, Math.min(100, (total - free) / total * 100)) : null;
  const reasons = array(s.blockers || s.reasons || volume.blockers);
  return `<section class="panel"><div class="panel-heading"><div><h2>Хранилище исследований</h2><p>Данные и модели сохраняются на Homebrain</p></div>${icon("folder", "nav-icon")}</div><div class="panel-body"><dl>${kv("Доступное пространство", free !== null ? bytes(free) : "Ожидаем сведения")}${kv("Накопитель", text(volume.label, text(volume.device, "Ожидаем сведения")))}${kv("Состояние", text(volume.status, volume.mounted === true ? "Подключён" : volume.mounted === false ? "Не смонтирован" : "Ожидаем сведения"))}</dl>${used !== null ? `<div class="storage-bar" aria-label="Занято ${Math.round(used)} процентов"><span style="width:${used}%"></span></div><div class="storage-stats"><span>Занято ${Math.round(used)}%</span><span>Всего ${bytes(total)}</span></div>` : ""}${reasons.length ? `<ul class="blocker-list">${reasons.map((reason) => `<li>${esc(reasonText(reason))}</li>`).join("")}</ul>` : ""}</div></section>`;
}
function operatorActions() {
  return `<section class="panel"><div class="panel-heading"><div><h2>Что требует внимания</h2><p>Действия из текущего состояния станции</p></div></div><div class="panel-body">${model.actions.length ? `<ul class="plain-list">${model.actions.map((action) => `<li><h3>${esc(text(action.title, text(action.label, "Требуется действие")))}</h3><p>${esc(text(action.description, text(action.reason, action.detail || action.message || "")))}</p>${action.node_id ? `<div class="meta">${esc(action.node_id)}</div>` : ""}</li>`).join("")}</ul>` : empty("Нет загруженных указаний", "После проверки узлов здесь появятся необходимые действия, если они потребуются.", "info")}</div></section>`;
}
function infrastructurePage() {
  return heading("Оборудование", "Живое состояние трёх узлов, камер, подключённых устройств и хранилища. Штатные настройки контроллера робота сохраняются.", "Explorer / Станция", '<button class="button secondary" type="button" data-action="refresh">' + icon("refresh") + 'Обновить</button>') + `<div class="three-column">${nodes().map((node) => {
    const role = nodeRole(node); const health = typeof node.health === "object" && node.health ? node.health : {};
    const hardware = node.hardware || health.hardware || {}; const memory = hardware.memory || health.memory || {};
    const serial = array(node.serial_devices || node.devices?.serial || hardware.serial_devices);
    const cameraList = array(node.cameras);
    const memoryTotal = defined(memory.total_bytes, hardware.memory_total);
    const memoryLabel = number(memoryTotal) !== null ? bytes(memoryTotal) : text(hardware.ram, text(health.memory_summary, "Не измерено"));
    const temperature = number(defined(hardware.temperature_c, health.temperature_c));
    const specifications = kv("Адрес", text(node.host, node.hostname || "Не указан")) + kv("Последний ответ", node.last_seen ? dateTime(node.last_seen) : "Пока не получен") + kv("Память", memoryLabel) + kv("Температура", temperature !== null ? `${temperature.toFixed(0)} °C` : "Не измерено") + kv("Камер", count(cameraList.length)) + kv("USB-адаптеров рук", serial.length ? count(serial.length) : "Ожидаем инвентаризацию");
    return `<article class="panel infrastructure-node"><div class="node-top"><span class="node-symbol">${icon(role.symbol)}</span><div><h2 class="node-title">${esc(role.name)}</h2><div class="node-role">${esc(role.role)}</div></div></div>${badge(node.online ? "available" : "offline")}<p class="node-health">${esc(compactHealth(node))}</p><dl>${specifications}</dl>${cameraList.length ? `<div class="device-pills">${cameraList.map((camera) => `<span class="device-pill">${esc(cameraLabel(typeof camera === "string" ? { id: camera } : camera))}</span>`).join("")}</div>` : ""}</article>`;
  }).join("") || `<section class="panel">${empty("Ожидаем сведения об узлах", "Инвентаризация появится после подключения служб станции.", "server")}</section>`}</div><div class="two-column spaced-section">${storagePanel()}${operatorActions()}</div><div class="spaced-section">${readinessCard()}</div>`;
}

function recordingsPanel(project) {
  const list = project ? model.recordings.filter((entry) => project === "personal" ? entry.project_id === "personal" : projectId(entry.project_id) === project) : model.recordings;
  return `<section class="panel"><div class="panel-heading"><div><h2>Сохранённые наблюдения</h2><p>Видео сцены — отдельный тип исследовательского материала</p></div><button type="button" class="button secondary small" data-action="record" ${project ? `data-project="${project}"` : ""}>${icon("record")}Записать</button></div><div class="panel-body">${list.length ? `<div class="recording-list">${list.slice(0, 12).map((entry) => `<article class="recording-row"><div><h3>${esc(text(entry.title, text(entry.id, entry.recording_id || "Наблюдение")))}</h3><p>${esc(cameraLabel(cameras().find((camera) => camera.id === (entry.camera_id || entry.config?.camera_id)) || { id: entry.camera_id || entry.config?.camera_id }))} · ${esc(dateTime(entry.created_at || entry.wall_utc || entry.created))}${number(defined(entry.duration_s, entry.config?.duration_s)) !== null ? ` · ${defined(entry.duration_s, entry.config?.duration_s)} с` : ""}</p><div class="recording-label">Запись камеры · обучающий эпизод не подтверждён</div></div>${badge(entry.status || "not_validated")}</article>`).join("")}</div>` : empty("Записей пока нет", "Сохраните короткое наблюдение с выбранной камеры. Готовая запись появится здесь.", "camera")}</div></section>`;
}
function labPage() { return stationPage(SO101_SOURCE_PROJECT); }
function eveningPage() {
  const labels={READY:"Готово",PHYSICAL_ACTION:"Действие пользователя",SOFTWARE_BLOCKER:"Доработка Homebrain"};
  const checks=array(model.evening?.checks);
  return heading("Подготовка наблюдаемой проверки","Текущее состояние, понятные физические действия и оставшаяся работа Homebrain. Исходная задача остаётся активной.","Explorer / Вечерняя проверка")+
    `<section class="panel"><div class="panel-heading"><h2>Что уже готово и что требуется</h2></div><div class="panel-body">${checks.length?checks.map(row=>`<article class="recording-row"><h3>${esc(row.title)} · ${esc(labels[row.status]||row.status)}</h3><p>${esc(row.detail)}</p></article>`).join(""):"<p>Обновляем фактическое состояние backend…</p>"}<p>Код, очереди и API не заменяют успешное выполнение задачи. Непроверенные физические результаты и гипотезы остаются открытыми.</p></div></section>`+
    observedNativePanel(observedNative.snapshot())+
    `<section class="panel spaced-section"><div class="panel-heading"><h2>После подготовки</h2></div><div class="panel-body"><a class="button secondary" data-link href="/experiments/02">02 · Клавиатура и геймпад</a> <a class="button secondary" data-link href="/experiments/03">03 · Зеркало и показ ROSMASTER</a> <a class="button secondary" data-link href="${SO101_SOURCE_ROUTE}">04 · Показ и обучение SO101</a> <a class="button secondary" data-link href="${TRANSFER_ROUTE}">06 · Перенос навыка</a><p>Сначала проверяйте входы геймпада без подключения управления. Запись, обучение, shadow и физический запуск выполняются отдельными явными действиями.</p></div></section>`;
}

function updateHeader() {
  const online = nodes().filter((node) => node.online === true).length; const total = nodes().length;
  const label = document.querySelector("#connection-label");
  const connected = model.lastUpdate && !model.error;
  label.innerHTML = `<span class="status-dot ${connected ? "good" : model.error ? "warn" : "muted"}"></span>${connected ? `В сети ${online} из ${total} узлов` : model.error ? "Нет обновлений" : "Подключение…"}`;
  document.querySelector("#sidebar-dot").className = `status-dot ${connected ? "good" : "muted"}`;
  document.querySelector("#sidebar-state").textContent = connected ? "Станция на связи" : "Ожидаем связь со станцией";
  document.querySelector("#last-updated").textContent = model.lastUpdate ? `Последнее обновление ${clockTime(model.lastUpdate)}` : "Ожидаем данные";
  const banner = document.querySelector("#connection-banner"); banner.hidden = !model.error;
  if (model.error) banner.textContent = model.lastUpdate ? "Связь со станцией прервалась. На экране последние полученные данные; пробуем подключиться снова." : `Ожидаем ответ станции. ${model.error}`;
}
function render() {
  const route = path(); const project = activeProject();
  const focused = document.activeElement;
  if (focused && main.contains(focused) && ["INPUT", "SELECT", "TEXTAREA"].includes(focused.tagName) && !model.forceRender) { updateHeader(); return; }
  model.forceRender = false;
  navigation();
  let title = "Обзор"; let markup;
  if (project) { title = `Опыт ${project.id}`; markup = experimentPage(project); }
  else if (route === "/research") { title = "Исследования"; markup = researchPage(); }
  else if (route === "/skills") { title = "Библиотека навыков"; markup = skillsPage(); }
  else if (route === "/infrastructure") { title = "Оборудование"; markup = infrastructurePage(); }
  else if (route === "/queue") { title = "Общая очередь"; markup = heading("Очередь обучения и проверок","Все опыты используют общий бюджет Homebrain. Здесь можно изменить порядок и параметры ожидания.") + sharedQueuePanel(sharedQueue.snapshot(),model.jobs); }
  else if (route === "/evening") { title = "Наблюдаемая проверка"; markup = eveningPage(); }
  else if (route === "/learning/so101") { title = "Моя лаборатория"; markup = labPage(); }
  else if (route === "/") markup = dashboard();
  else markup = heading("Раздел не найден", "Вернитесь к обзору станции или выберите опыт в меню.") + '<a href="/" class="button primary" data-link>Открыть обзор</a>';
  const cameraWasVisible = document.contains(cameraCard); const slotNeeded = markup.includes('id="camera-slot"');
  if (cameraWasVisible) cameraCard.remove();
  cooperativeUnbind(); cooperativeUnbind = () => {};
  sharedQueueUnbind(); sharedQueueUnbind = () => {};
  main.innerHTML = markup;
  if (project?.id === "05") cooperativeUnbind = bindCooperativeWorkbench(main,cooperativeWorkbench,error => toast(error.message,true));
  if (route === "/queue") sharedQueueUnbind = bindSharedQueue(main,sharedQueue,error => toast(error.message,true));
  if ([SO101_SOURCE_ROUTE, "/learning/so101"].includes(route)) {
    bindLabVision(main,labVision);
    bindTabletopCalibration(main,tabletopCalibration,error => toast(error.message));
  }
  if (slotNeeded) { main.querySelector("#camera-slot").append(cameraCard); syncCamera(); }
  else { if (manualPreview.state().active) manualPreview.stop("preview_not_visible"); cameraImage.removeAttribute("src"); cameraLoaded = false; }
  simulationImage = main.querySelector("#simulation-image");
  simulationJob = simulationImage?.dataset.simulationJob || "";
  if (simulationImage) {
    simulationImage.addEventListener("error", () => { simulationImage.hidden = true; main.querySelector("#simulation-error").hidden = false; });
    simulationImage.addEventListener("load", () => { simulationImage.hidden = false; main.querySelector("#simulation-error").hidden = true; });
  }
  document.querySelector("#breadcrumb-title").textContent = title;
  document.title = `${project?.title || title} · Explorer`;
  updateHeader();
}

async function refresh(forceExtras = false) {
  if (model.refreshing) return;
  model.refreshing = true;
  try {
    const state = await api("/api/state");
    model.state = state; model.error = null; model.lastUpdate = Date.now();
    model.jobs = array(state.jobs); model.skills = array(state.skills); model.research = state.research || model.research;
    if (forceExtras || Date.now() - model.lastExtras > 20000) {
      const endpoints = ["/api/jobs", "/api/skills", "/api/research", "/api/recordings", "/api/operator-actions"];
      const results = await Promise.allSettled(endpoints.map((endpoint) => api(endpoint)));
      if (results[0].status === "fulfilled") model.jobs = array(results[0].value.jobs);
      if (results[1].status === "fulfilled") model.skills = array(results[1].value.skills);
      if (results[2].status === "fulfilled") model.research = results[2].value;
      if (results[3].status === "fulfilled") model.recordings = array(results[3].value.recordings);
      if (results[4].status === "fulfilled") model.actions = array(results[4].value.actions);
      model.lastExtras = Date.now();
    }
    if (rosmasterDemonstrationRoute()) {
      await loadRobot();
      const signature = model.jobs.filter(job => ["mobile_import", "retarget_import"].includes(job.kind)).map(job => `${job.id}:${job.status}:${job.updated}`).join("|");
      if (signature !== mobileEvidenceSignature || !mobileEvidence.snapshot().schema) {
        mobileEvidenceSignature = signature; void mobileEvidence.refresh();
      }
      const skillSignature = model.jobs.filter(job => ["02", "03"].includes(projectId(job.project_id))).map(job => `${job.id}:${job.status}`).join("|");
      if (forceExtras || skillSignature !== skillWorkflowSignature || !skillWorkflowUI.loaded) {
        skillWorkflowSignature = skillSignature; void refreshSkillWorkflow(true);
      }
      const episode = demonstrationTeaching().active?.id;
      if (episode && recoveredCaptureEpisode !== episode) {
        recoveredCaptureEpisode = episode;
        void skillWorkflowRecoverCapture(episode).catch(error => { skillWorkflowUI.error = error.message; });
      }
    }
    if (activeProject()?.id === "01" && (forceExtras || Date.now() - worldLearning.lastLoad > 20000)) await refreshWorldDatasets();
    if(path()==="/evening")model.evening=await api("/api/evening");
    if (activeProject()?.id === "05" && (forceExtras || Date.now()-cooperativeLoadedAt>3000)) { cooperativeLoadedAt=Date.now(); await cooperativeWorkbench.refresh(); }
    if (path() === "/queue" && (forceExtras || Date.now()-sharedQueueLoadedAt>3000)) { sharedQueueLoadedAt=Date.now(); await sharedQueue.refresh(); }
    if (stationRoute()) await pollStation(forceExtras);
    if ([SO101_SOURCE_ROUTE, "/learning/so101"].includes(path())) await Promise.all([labVision.refresh(forceExtras),tabletopCalibration.refresh(forceExtras)]);
    if ([SO101_SOURCE_ROUTE, "/learning/so101"].includes(path()) && (forceExtras || Date.now() - labSkillsLoadedAt > 15000)) { labSkillsLoadedAt = Date.now(); await labSkills.refresh(); }
  } catch (error) { model.error = error.message === "Failed to fetch" ? "Проверьте подключение к домашней сети." : error.message; }
  finally { model.refreshing = false; render(); }
}

function openRecord(project) {
  if (stationLab.owned || stationLab.present || stationLab.pendingControl) stopStation(true);
  if (keyboard.active || keyboard.starting) stopKeyboard(true);
  record.step = 0; record.project = projectId(project || activeProject()?.id) || "02";
  const ready = cameras().filter((camera) => camera.available !== false && camera.node_online !== false);
  record.camera = ready.find((camera) => camera.id === currentCamera)?.id || ready[0]?.id || ""; record.duration = 15; record.busy = false;
  renderRecord(); dialog.showModal();
}
function renderRecord() {
  const project = record.project === "personal" ? PERSONAL_PROJECT : PROJECTS.find((entry) => entry.id === record.project) || PROJECTS[1];
  const options = cameras().filter((camera) => camera.available !== false && camera.node_online !== false);
  let html;
  if (record.step === 0) {
    html = `<p class="form-description">Сохраните короткое видео сцены и привяжите его к опыту. Запись сама не запускает движения оборудования.</p><div class="form-grid"><div class="form-field full"><label for="record-project">Исследовательский опыт</label><select id="record-project" name="project_id">${PROJECTS.map((entry) => `<option value="${entry.id}" ${entry.id === record.project ? "selected" : ""}>${entry.id === "personal" ? "Личная практика" : entry.id} · ${esc(entry.title)}</option>`).join("")}</select></div><div class="form-field full"><label for="record-camera">Камера</label><select id="record-camera" name="camera_id" required>${options.length ? options.map((camera) => `<option value="${esc(camera.id)}" ${camera.id === record.camera ? "selected" : ""}>${esc(cameraLabel(camera))}</option>`).join("") : '<option value="">Камеры пока не обнаружены</option>'}</select></div><div class="form-field"><label for="record-duration">Длительность, секунд</label><input id="record-duration" name="duration_s" type="number" min="5" max="60" step="1" value="${record.duration}" required></div></div>`;
  } else if (record.step === 1) {
    html = `<p class="form-description">Посмотрите на живое изображение перед началом: предмет и всё интересующее движение должны оставаться в кадре.</p><div class="wizard-guidance"><h3>Что и как показывать</h3><p>${esc(project.guidance)}</p></div><div class="summary-label">Сохраняется видео наблюдения. Совместная запись команд и измеренных суставов в этом мастере не подтверждается.</div>`;
  } else {
    html = `<p class="form-description">Запись выполнится на узле камеры. Готовый материал появится в списке наблюдений.</p><dl class="summary-list">${kv("Опыт", `${record.project === "personal" ? "Личная практика" : record.project} · ${project.title}`)}${kv("Источник", cameraLabel(options.find((camera) => camera.id === record.camera) || { id: record.camera }))}${kv("Длительность", `${record.duration} секунд`)}${kv("Тип материала", "Видео наблюдения")}</dl><div class="summary-label">Материал не считается проверенной демонстрацией навыка до отдельной оценки.</div>`;
  }
  document.querySelector("#record-step-content").innerHTML = html;
  dialog.querySelectorAll(".wizard-steps span").forEach((element, index) => { element.classList.toggle("active", index === record.step); if (index === record.step) element.setAttribute("aria-current", "step"); else element.removeAttribute("aria-current"); });
  document.querySelector("#record-back").textContent = record.step ? "Назад" : "Отмена";
  const next = document.querySelector("#record-next"); next.disabled = record.busy || (!record.camera && record.step === 0);
  next.textContent = record.busy ? "Запускаем запись…" : record.step === 2 ? `Начать запись · ${record.duration} с` : "Далее →";
  document.querySelector("#record-error").hidden = true;
}
document.querySelector("#close-record").addEventListener("click", () => { if (!record.busy) dialog.close(); });
document.querySelector("#record-back").addEventListener("click", () => { if (record.step > 0) { record.step -= 1; renderRecord(); } else dialog.close(); });
document.querySelector("#record-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (record.busy) return;
  if (record.step === 0) {
    record.project = document.querySelector("#record-project").value; record.camera = document.querySelector("#record-camera").value;
    record.duration = Number(document.querySelector("#record-duration").value);
    if (!record.camera || !Number.isInteger(record.duration) || record.duration < 5 || record.duration > 60) return;
  }
  if (record.step < 2) { record.step += 1; renderRecord(); return; }
  record.busy = true; renderRecord();
  try {
    await post("/api/recordings", { camera_id: record.camera, project_id: record.project, duration_s: record.duration });
    dialog.close(); toast("Запись наблюдения поставлена в очередь. Состояние видно в списке запусков."); await refresh(true);
  } catch (error) {
    const node = document.querySelector("#record-error"); node.textContent = error.message; node.hidden = false;
  } finally { record.busy = false; const next = document.querySelector("#record-next"); next.disabled = false; next.textContent = `Начать запись · ${record.duration} с`; }
});
dialog.addEventListener("cancel", (event) => { if (record.busy) event.preventDefault(); });

document.addEventListener("click", async (event) => {
  const link = event.target.closest("a[data-link]");
  if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) { event.preventDefault(); observedNative.cancelPending(); model.forceRender = true; navigate(link.getAttribute("href")); return; }
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (button.dataset.labVision) {
    try {
      const command = button.dataset.labVision;
      if (command === "refresh") await labVision.refresh(true);
      if (command === "freeze") await labVision.freeze();
      if (command === "semantic") { await labVision.semantic(); toast("Локальная VLM обрабатывает один выбранный кадр."); }
      if (command === "grid") await labVision.metricGrid(Number(main.querySelector("#lab-grid-size")?.value || 3) / 100);
    } catch (error) { toast(error.message,true); }
    return;
  }
  if (action === "record") { openRecord(button.dataset.project); return; }
  if (action === "choose-skill") { model.forceRender = true; navigate("/skills"); return; }
  if (action === "refresh") { await refresh(true); return; }
  if (action === "mobile-evidence-refresh") { await mobileEvidence.refresh(); return; }
  if (action === "world-refresh") { await refreshWorldDatasets(); return; }
  if (action === "world-train") {
    if (worldLearning.busy) return;
    try {
      const request = worldTrainingRequest(); worldLearning.busy = true; button.disabled = true;
      const job = await post("/api/jobs", request); model.jobs = [job, ...model.jobs.filter((entry) => entry.id !== job.id)];
      toast("Обучение прогноза сцены поставлено в очередь. Команды на робота не передаются."); await refresh(true);
    } catch (error) { toast(error.message, true); }
    finally { worldLearning.busy = false; model.forceRender = true; render(); }
    return;
  }
  if (action === "direct03-mode") {
    if (demonstrationTeaching().active) { toast("Сначала завершите текущий показ.", true); return; }
    direct03.demoMode = button.dataset.demoMode; model.forceRender = true; render(); return;
  }
  if (action === "keyboard-toggle") {
    button.disabled = true;
    try { if (keyboard.active) await stopKeyboard(true); else await startKeyboard(); }
    catch (error) { keyboard.error = error.message; toast(error.message, true); button.disabled = false; }
    return;
  }
  if (action === "robot-stop") { await stopKeyboard(false); return; }
  if (button.dataset.armMode) { try { await setArmMode(button.dataset.armMode); } catch (error) { toast(error.message, true); } return; }
  if (button.dataset.episodeOutcome) {
    button.disabled = true;
    try {
      const review = skillWorkflowFinishContext(button.dataset.episodeOutcome);
      const finished = await post(demonstrationRecordPath("finish"), { recording_id: activeProject()?.id === "03" ? button.dataset.episodeId : undefined, outcome: button.dataset.episodeOutcome, expected_id: button.dataset.episodeId, skill_capture_id: review.capture_id, skill_review: review });
      if (finished.import_job?.id) {
        mobileLearning.importJob = finished.import_job.id;
        model.jobs = [finished.import_job, ...model.jobs.filter((job) => job.id !== finished.import_job.id)];
      }
      toast("Показ завершён. Оценка оператора сохранена отдельно от проверки навыка.");
      if (finished.skill_workflow_error) toast(finished.skill_workflow_error, true);
      skillWorkflowCaptureFinished();
      await loadRobot(); model.forceRender = true; render();
    } catch (error) { toast(error.message, true); button.disabled = false; }
    return;
  }
  if (button.dataset.episodeStage) {
    button.disabled = true;
    try { await post(demonstrationRecordPath("stage"), { recording_id: activeProject()?.id === "03" ? button.dataset.episodeId : undefined, stage: button.dataset.episodeStage, expected_id: button.dataset.episodeId, skill_capture_id: skillWorkflowUI.capture }); }
    catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
    return;
  }
  if (button.dataset.jobAction) {
    button.disabled = true;
    try { await post(`/api/jobs/${encodeURIComponent(button.dataset.job)}/${button.dataset.jobAction}`); await refresh(true); }
    catch (error) { toast(error.message, true); button.disabled = false; }
  }
});
document.addEventListener("input", (event) => {
  if (event.target.id === "sim-updates") model.simDraft.updates = Number(event.target.value);
  if (event.target.id === "sim-seed") model.simDraft.seed = Number(event.target.value);
  if (event.target.id === "episode-name") model.robot.episodeName = event.target.value;
});
document.addEventListener("change", (event) => {
  if (event.target.id === "world-dataset") { worldLearning.manifest = event.target.value; worldLearning.signature = ""; worldLearning.development = null; model.forceRender = true; render(); }
  if (event.target.dataset.worldDevelopment !== undefined) {
    const id = event.target.dataset.worldDevelopment; const selected = array(worldLearning.development).filter((value) => value !== id);
    worldLearning.development = event.target.checked ? [...selected, id] : selected; model.forceRender = true; render();
  }
});
document.addEventListener("submit", async (event) => {
  if (event.target.id === "episode-form") {
    event.preventDefault();
    const button = event.target.querySelector('button[type="submit"]'); button.disabled = true;
    try {
      if (!await manualPreview.ensureFresh() || !rosmasterCameraReady()) throw new Error("Дождитесь свежих кадров ROSMASTER перед началом показа.");
      const context = skillWorkflowRecordingContext();
      if (model.robot.captureCapabilities?.native_owner_ready !== true) throw new Error("Запись с подтверждёнными командами пока недоступна. Её активация требует отдельной наблюдаемой проверки; видео наблюдения доступно отдельно.");
      if (activeProject()?.id === "03" && direct03.status?.state !== "ACTIVE") throw new Error("Сначала включите наблюдаемое зеркало ROSMASTER.");
      const started = await post(demonstrationRecordPath("start"), { expected_control_epoch: activeProject()?.id === "03" ? direct03.status?.control_epoch : undefined, name: model.robot.episodeName, observing: true, object_label: "", object_class: "unknown", size_class: "medium", destination: "", skill_context: context });
      if (activeProject()?.id === "03") direct03.recordId = started.recording_id || started.skill_capture?.original_episode_id || "";
      if (started.skill_capture?.capture_id) skillWorkflowCaptureStarted(started.skill_capture.capture_id, started.skill_capture);
      if (started.skill_workflow_error) toast(started.skill_workflow_error, true);
      await loadRobot(); model.forceRender = true; render(); toast("Запись началась. Полнота датчиков и 9D действий проверяется после импорта.");
    } catch (error) { toast(error.message, true); button.disabled = false; }
    return;
  }
  if (event.target.id !== "simulation-form") return;
  event.preventDefault();
  const button = document.querySelector("#start-simulation"); const errorNode = document.querySelector("#simulation-form-error");
  button.disabled = true; errorNode.hidden = true;
  try {
    await post("/api/jobs", { kind: "sim_pilot", project_id: "01", config: { task: "reach", updates: Number(document.querySelector("#sim-updates").value), seed: Number(document.querySelector("#sim-seed").value) } });
    model.forceRender = true; toast("Обучение поставлено в очередь. Здесь появятся состояние и фактические результаты."); await refresh(true);
  } catch (error) { errorNode.textContent = error.message; errorNode.hidden = false; button.disabled = false; }
});
document.querySelector("#stop-button").addEventListener("click", async () => {
  const button = document.querySelector("#stop-button"); button.disabled = true;
  manualInputFreezeCount += 1;
  void stopKeyboard(true);
  void browserGamepad.disconnect("Общий STOP.");
  // Dispatch both STOP paths immediately, even if one node is unreachable.
  void stopStation(true);
  try { const result = await post("/api/stop"); toast(result.robot_error ? `Задачи остановлены. Запрос STOP роботу не подтверждён: ${result.robot_error}` : "Запрос STOP передан штатному управлению робота. Задачи Explorer останавливаются.", Boolean(result.robot_error)); await refresh(true); }
  catch (error) { toast(error.message, true); }
  finally { manualInputFreezeCount -= 1; button.disabled = false; }
});
document.querySelector("#logout-button").addEventListener("click", async () => {
  manualInputFreezeCount += 1;
  try {
    await Promise.allSettled([
      stopKeyboard(true), browserGamepad.disconnect("Выход из панели."),
      stationLab.owned || stationLab.present || stationLab.pendingControl ? stopStation(true) : Promise.resolve(),
      direct03.held || direct03.busy ? direct03Stop(true) : Promise.resolve()
    ]);
    const drainUntil = performance.now() + 15000;
    while (keyboard.busy || keyboard.starting || keyboard.stopping || browserGamepad.busy || browserGamepad.stopping) {
      if (performance.now() > drainUntil) throw new Error("Завершение управления ещё ожидается. Вход сохранён.");
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    if (keyboard.stopUnconfirmed || browserGamepad.stopUnconfirmed) throw new Error("STOP не подтверждён. Вход сохранён, повторите STOP перед выходом.");
    await post("/api/logout"); window.location.assign("/api/login");
  }
  catch (error) { toast(error.message, true); }
  finally { manualInputFreezeCount -= 1; }
});
window.addEventListener("popstate", () => { if ((keyboard.active || keyboard.starting) && path() !== "/experiments/02") stopKeyboard(true); model.forceRender = true; render(); });
window.addEventListener("blur", () => { if (keyboard.active || keyboard.starting) stopKeyboard(true); });
window.addEventListener("blur", () => {const owned=observedNative.snapshot().activation?.execution_permission;observedNative.cancelPending();if(owned)void observedNative.revoke().catch(error=>toast(error.message,true));});
window.addEventListener("pagehide", () => {if(observedNative.snapshot().activation?.execution_permission)void observedNative.revoke().catch(()=>{});else observedNative.cancelPending();});
document.addEventListener("change", event=>{
  if(event.target.dataset?.observedConfirm)observedNative.configure({confirmations:{[event.target.dataset.observedConfirm]:event.target.checked}});
  if(event.target.dataset?.observedField)observedNative.configure({[event.target.dataset.observedField]:event.target.checked});
  if(event.target.matches?.("[data-observed-mode]"))observedNative.configure({inputMode:event.target.value});
});
document.addEventListener("click",async event=>{
  const button=event.target.closest?.("button[data-observed-action],button[data-observed-capture]");
  if(!button||button.disabled)return;
  button.disabled=true;
  try{
    if(button.dataset.observedCapture)await observedNative.capture(button.dataset.observedCapture);
    else{
      const operations={refresh:()=>observedNative.refresh(),freeze:()=>observedNative.freeze(),authorize:()=>observedNative.authorize(),revoke:()=>observedNative.revoke(),home:()=>observedNative.home(),accept:()=>observedNative.acceptRegistration()};
      if(!operations[button.dataset.observedAction])throw new Error("Неизвестный шаг наблюдаемой проверки");
      await operations[button.dataset.observedAction]();
    }
  }catch(error){toast(error.message,true);}
  finally{model.forceRender=true;render();}
});
window.addEventListener("pagehide", () => { if (keyboard.active || keyboard.starting) stopKeyboard(true); });
window.addEventListener("focus", () => { if (rosmasterDemonstrationRoute() && !document.hidden) void manualPreview.start(); });
document.addEventListener("focusin", (event) => { if ((keyboard.active || keyboard.starting) && event.target.closest("input,select,textarea,[contenteditable=true]")) stopKeyboard(true); });
document.addEventListener("keydown", async (event) => {
  const motionKeys = new Set(["KeyW", "KeyS", "KeyA", "KeyD", "KeyQ", "KeyE", "KeyI", "KeyK", "KeyJ", "KeyL", "KeyU", "KeyO", "KeyY", "KeyH", "Comma", "Period", "KeyN", "KeyM", "ShiftLeft", "ShiftRight"]);
  if (motionKeys.has(event.code)) keyboard.heldKeys.add(event.code);
  if (!keyboard.active || activeProject()?.id !== "02" || dialog.open || event.target.closest("input,select,textarea,[contenteditable=true]")) return;
  const scheme = model.robot.controls?.keyboard || {};
  if (array(scheme.stop || ["Space", "Escape"]).includes(event.code)) { event.preventDefault(); stopKeyboard(false); return; }
  if (event.code === (scheme.resume || "Enter") && keyboard.paused && !event.repeat) {
    event.preventDefault();
    try { await keyboard.resume(); }
    catch (error) { toast(error.message, true); }
    return;
  }
  if (scheme.mode?.[event.code] && !event.repeat) { event.preventDefault(); try { await setArmMode(scheme.mode[event.code]); } catch (error) { toast(error.message, true); } return; }
  if ([scheme.arm_speed?.faster || "Equal", scheme.arm_speed?.slower || "Minus"].includes(event.code) && !event.repeat) {
    event.preventDefault(); try { await keyboard.configure("speed", event.code === (scheme.arm_speed?.faster || "Equal") ? "fast" : "normal"); } catch (error) { toast(error.message, true); } return;
  }
  const allowed = new Set([...Object.keys(scheme.common || {}), ...Object.keys(scheme[keyboard.mode] || {}), ...array(scheme.precision)]);
  if (allowed.has(event.code)) { event.preventDefault(); keyboard.keys.add(event.code); sendKeyboard(); }
});
document.addEventListener("keyup", (event) => {
  keyboard.heldKeys.delete(event.code);
  if (keyboard.keys.has(event.code)) { event.preventDefault(); keyboard.keys.delete(event.code); sendKeyboard(); }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { if (keyboard.active || keyboard.starting) stopKeyboard(true); cameraImage.removeAttribute("src"); cameraLoaded = false; }
  else { syncCamera(); refresh(); }
});
setInterval(() => { if (!document.hidden) refresh(); }, 5000);
setInterval(() => { if (keyboard.active && !keyboard.paused && !document.hidden) sendKeyboard(); }, 100);
setInterval(async () => {
  const active = model.robot.teaching?.active;
  if ((active?.session || active?.id) && activeProject()?.id === "02" && rosmasterCameraReady() && document.hasFocus() && !episodeLeaseBusy) {
    episodeLeaseBusy = true;
    try { await post("/api/robot/teaching/mobile/lease", { session: active.session || active.id, observing: true }); }
    catch (error) { model.robot.error = error.message; }
    finally { episodeLeaseBusy = false; }
  }
}, 250);
setInterval(() => {
  if (!document.hidden && cameraMode === "snapshot" && currentCamera && document.contains(cameraCard) && Date.now() - lastSnapshotAt > 2400) loadSnapshot();
  if (!document.hidden && simulationJob && simulationImage && document.contains(simulationImage)) simulationImage.src = `/api/simulations/${encodeURIComponent(simulationJob)}/frame?t=${Date.now()}`;
}, 2500);
installLabSkillsHandlers(main,labSkills,{onError:error => toast(error.message,true)});
render();
refresh(true);
