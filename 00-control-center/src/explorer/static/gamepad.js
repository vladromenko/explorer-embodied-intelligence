/* ENGINEERING_GLUE: Browser Gamepad API -> audited explorer-manual-v2 keyboard
 * directions. Analog axes are digitized; the existing native executor retains
 * its smoothing/leases. No new motor backend, controller settings or device IO.
 * Standard layout: https://www.w3.org/TR/gamepad/#remapping
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ExplorerBrowserGamepad = api;
})(typeof window === "object" ? window : null, function () {
  "use strict";
  const SCHEME = "explorer-manual-v2";
  const PATH = "/api/robot/teleop/";
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (part) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[part]));
  const MODE = { cartesian: "Захват XYZ", joint: "Суставы" };
  const AUDITED_KEYS = {
    common: { KeyW: "forward", KeyS: "backward", KeyA: "left", KeyD: "right", KeyQ: "turn_left", KeyE: "turn_right", KeyN: "grip_open", KeyM: "grip_close" },
    cartesian: { KeyI: "arm_x_forward", KeyK: "arm_x_back", KeyJ: "arm_y_left", KeyL: "arm_y_right", KeyU: "arm_z_up", KeyO: "arm_z_down" },
    joint: { KeyJ: "joint1_decrease", KeyL: "joint1_increase", KeyI: "joint2_increase", KeyK: "joint2_decrease", KeyU: "joint3_increase", KeyO: "joint3_decrease", KeyY: "pitch_up", KeyH: "pitch_down", Comma: "wrist_left", Period: "wrist_right" }
  };
  function qualifiedControls(controls) {
    if (!controls || controls.id !== SCHEME || !Array.isArray(controls.arm_modes) || !["cartesian", "joint"].every((mode) => controls.arm_modes.includes(mode))) return false;
    return Object.entries(AUDITED_KEYS).every(([group, keys]) => Object.entries(keys).every(([key, meaning]) => controls.keyboard?.[group]?.[key] === meaning)) && controls.keyboard?.precision?.includes("ShiftLeft");
  }

  function capability(environment) {
    if (environment.secure !== true) return { supported: false, reason: "Для геймпада откройте защищённый адрес Explorer (HTTPS). Клавиатура доступна отдельно." };
    if (typeof environment.getGamepads !== "function") return { supported: false, reason: "Этот браузер не предоставляет Gamepad API. Используйте поддерживаемый браузер Mac или клавиатуру." };
    return { supported: true, reason: "" };
  }
  function deviceIdentity(pad) { return `${pad.index}:${pad.id}:${pad.mapping}`; }
  function snapshot(pad, deadzone = .18) {
    if (!pad || pad.connected !== true || pad.mapping !== "standard") throw new Error("Нужен подключённый геймпад со стандартной раскладкой браузера.");
    if (!Array.isArray(pad.axes) || pad.axes.length < 4 || !Array.isArray(pad.buttons) || pad.buttons.length < 16) throw new Error("Браузер не передал четыре оси и стандартный набор кнопок.");
    if (!Number.isFinite(deadzone) || deadzone < .05 || deadzone > .4) throw new Error("Некорректная зона покоя стиков.");
    const axes = pad.axes.slice(0, 4).map((value) => {
      if (!Number.isFinite(value) || value < -1 || value > 1) throw new Error("Некорректное значение оси геймпада.");
      return Math.abs(value) <= deadzone ? 0 : value;
    });
    const buttons = pad.buttons.map((button, index) => {
      if (!button || !Number.isFinite(button.value) || button.value < 0 || button.value > 1 || typeof button.pressed !== "boolean") throw new Error("Некорректное значение кнопки геймпада.");
      return button.pressed || button.value > ([6, 7].includes(index) ? deadzone : .5);
    });
    return { identity: deviceIdentity(pad), axes, buttons, neutral: axes.every((value) => value === 0) && buttons.every((value) => !value) };
  }
  function directionKeys(frame, mode) {
    if (!["cartesian", "joint"].includes(mode)) throw new Error("Неизвестный режим штатной руки.");
    const [lx, ly, rx, ry] = frame.axes, b = frame.buttons, keys = [];
    function pair(plus, minus, plusKey, minusKey) { if (plus !== minus) keys.push(plus ? plusKey : minusKey); }
    pair(ly < 0, ly > 0, "KeyW", "KeyS");
    pair(lx < 0, lx > 0, "KeyA", "KeyD");
    pair(Boolean(b[4]), Boolean(b[5]), "KeyQ", "KeyE");
    pair(Boolean(b[6]), Boolean(b[7]), "KeyN", "KeyM");
    pair(rx < 0, rx > 0, "KeyJ", "KeyL");
    pair(ry < 0, ry > 0, "KeyI", "KeyK");
    pair(Boolean(b[3]), Boolean(b[0]), "KeyU", "KeyO");
    if (mode === "joint") {
      pair(Boolean(b[12]), Boolean(b[13]), "KeyY", "KeyH");
      pair(Boolean(b[15]), Boolean(b[14]), "Period", "Comma");
    }
    if (b[2]) keys.push("ShiftLeft");
    return keys.sort();
  }
  function neutralExceptStart(frame) { return frame.axes.every((value) => value === 0) && frame.buttons.every((value, index) => !value || index === 9); }

  class Controller {
    constructor(options) {
      this.options = options;
      this.state = "disconnected"; this.reason = "Выберите геймпад и подключите управление явно.";
      this.selected = null; this.mode = "cartesian"; this.speed = "normal"; this.deadzone = .18;
      this.epoch = 0; this.busy = false; this.stopping = null; this.stopUnconfirmed = false; this.previousButtons = [];
      this.pending = []; this.nextSend = 0; this.neutralRequired = false; this.canResumeFromStart = false;
      this.lastKeys = []; this.lastFrame = null; this.devices = []; this.preview = false;
    }
    notify() { if (this.options.onChange) this.options.onChange(this.status()); }
    status() {
      return { state: this.state, reason: this.reason, selected: this.selected ? { ...this.selected } : null,
        mode: this.mode, speed: this.speed, deadzone: this.deadzone, devices: this.devices.map((device) => ({ ...device })),
        keys: [...this.lastKeys], preview: this.preview, capability: capability(this.options),
        wireSource: "keyboard", inputOrigin: "browser_gamepad_digitized_directions", scheme: SCHEME,
        neutralRequired: this.neutralRequired, busy: this.busy,
        chassisProhibited: typeof this.options.chassisProhibited === "function" && this.options.chassisProhibited() === true };
    }
    scan() {
      const support = capability(this.options);
      if (!support.supported) { this.devices = []; return []; }
      let pads;
      try { pads = Array.from(this.options.getGamepads() || []); }
      catch (error) { throw new Error(`Браузер не разрешил доступ к геймпаду: ${error.message || error.name}`); }
      this.devices = pads.filter((pad) => pad && pad.connected).map((pad) => ({ index: pad.index, id: pad.id, mapping: pad.mapping, identity: deviceIdentity(pad) }));
      return pads;
    }
    async select(index) {
      if (["active", "connecting", "paused"].includes(this.state) || this.busy) await this.disconnect("Выбран другой геймпад.");
      const pad = this.scan().find((item) => item && item.index === Number(index) && item.connected);
      if (!pad) throw new Error("Выбранный геймпад больше не подключён.");
      this.selected = { index: pad.index, id: pad.id, mapping: pad.mapping, identity: deviceIdentity(pad) };
      this.previousButtons = []; this.reason = "Отпустите стики и кнопки, затем подключите управление."; this.notify();
    }
    frame() {
      const support = capability(this.options);
      if (!support.supported) throw new Error(support.reason);
      const pad = this.scan().find((item) => item && this.selected && item.index === this.selected.index);
      if (!pad || deviceIdentity(pad) !== this.selected.identity) throw new Error("Выбранный геймпад отключён или заменён. Подключите нужный контроллер заново.");
      return snapshot(pad, this.deadzone);
    }
    context(requireNeutral = false, ignoreStart = false) {
      if (!this.options.visible() || !this.options.focused() || !this.options.routeActive()) throw new Error("Управление остановлено при уходе из окна опыта.");
      if (!this.options.cameraReady()) throw new Error("Нет свежего изображения ROSMASTER. Для продолжения подключите управление явно.");
      const frame = this.frame();
      if (requireNeutral && !(ignoreStart ? neutralExceptStart(frame) : frame.neutral)) throw new Error("Перед включением отпустите все стики и кнопки движения.");
      return frame;
    }
    current(epoch) {
      if (epoch !== this.epoch) throw new Error("Запрос завершён: требуется новое явное подключение.");
      if (this.options.ownershipCurrent && !this.options.ownershipCurrent(this.ownershipToken)) throw new Error("Выбран другой источник управления.");
    }
    packet(keys = [], observing = true) {
      let transmitted = [...keys];
      if (keys.length && typeof this.options.filterKeys === "function") {
        transmitted = this.options.filterKeys([...keys]);
        if (!Array.isArray(transmitted) || !transmitted.every((key) => keys.includes(key))) throw new Error("Фильтр текущих ограничений может только убирать направления.");
      }
      return { source: "keyboard", keys: transmitted, observing, arm_mode: this.mode };
    }
    async checkedPost(epoch, operation, body, neutral = false, ignoreStart = false) {
      this.current(epoch); this.context(neutral, ignoreStart);
      const result = await this.options.post(PATH + operation, body);
      this.current(epoch); this.context(neutral, ignoreStart);
      return result;
    }
    async connect(fromStart = false) {
      if (this.busy || this.state === "connecting" || this.stopping) throw new Error("Предыдущая операция управления ещё завершается.");
      if (this.stopUnconfirmed) {
        await this.disconnect("Проверяем завершение предыдущего управления.");
        if (this.stopUnconfirmed) throw new Error("STOP не подтверждён. Повторите отключение перед подключением.");
      }
      if (fromStart && (this.state !== "paused" || !this.canResumeFromStart)) return;
      const first = this.context(true, fromStart);
      this.ownershipToken = this.options.claim ? this.options.claim() : null;
      const epoch = ++this.epoch;
      this.state = "connecting"; this.preview = false; this.busy = true; this.pending = []; this.notify();
      try {
        const controls = await this.options.controls();
        this.current(epoch); this.context(true, fromStart);
        if (!qualifiedControls(controls)) throw new Error("Именованная штатная раскладка explorer-manual-v2 не подтверждена или изменилась.");
        if (this.options.beforeConnect) await this.options.beforeConnect(this.ownershipToken);
        this.current(epoch); this.context(true, fromStart);
        const native = this.options.nativeState ? this.options.nativeState() : {};
        this.mode = ["cartesian", "joint"].includes(native.arm_mode) ? native.arm_mode : controls.default_arm_mode || "cartesian";
        this.speed = ["normal", "fast"].includes(native.arm_speed_selected) ? native.arm_speed_selected : "normal";
        await this.checkedPost(epoch, "select", { source: "keyboard", observing: true, neutral: true }, true, fromStart);
        // A genuine neutral input packet is sent before resume/lease acquisition.
        await this.checkedPost(epoch, "input", this.packet([], true), true, fromStart);
        await this.checkedPost(epoch, "resume", { source: "keyboard", observing: true }, true, fromStart);
        this.state = "active"; this.canResumeFromStart = false; this.reason = "Геймпад управляет через штатные направления. Для остановки — B.";
        this.previousButtons = [...first.buttons]; this.neutralRequired = false; this.nextSend = 0;
      } catch (error) { await this.disconnect(error.message); throw error; }
      finally { this.busy = false; this.notify(); }
    }
    async disconnect(reason = "Геймпад отключён.", allowStart = false) {
      const hadControl = ["active", "connecting", "paused"].includes(this.state) || this.busy || this.stopUnconfirmed;
      ++this.epoch; this.state = allowStart ? "paused" : "disconnected"; this.reason = reason;
      this.canResumeFromStart = allowStart; this.lastKeys = []; this.pending = []; this.neutralRequired = true;
      if (!hadControl) { this.notify(); return; }
      if (!this.stopping) {
        // STOP is issued without waiting for a slow/failed neutral request.
        const calls = [["input", this.packet([], false)], ["stop", {}], ["disconnect", { source: "keyboard" }]]
          .map(([operation, body]) => Promise.resolve().then(() => this.options.post(PATH + operation, body, { keepalive: true })));
        this.stopping = Promise.allSettled(calls);
      }
      const results = await this.stopping; this.stopping = null;
      this.stopUnconfirmed = results.some(result => result.status === "rejected");
      if (this.stopUnconfirmed) { this.state = "disconnected"; this.canResumeFromStart = false; this.reason = "STOP не подтверждён. Повторите отключение перед новым подключением."; }
      this.notify();
    }
    async setPreview(value) {
      if (value) await this.disconnect("Проверка входов: команды движения не отправляются.");
      this.preview = Boolean(value); this.notify();
    }
    async setDeadzone(value) {
      if (!Number.isFinite(value) || value < .05 || value > .4) throw new Error("Зона покоя должна быть от 0,05 до 0,40.");
      await this.disconnect("Зона покоя изменена. Для движения подключите управление явно.");
      this.deadzone = value; this.notify();
    }
    async poll() {
      let frame;
      try {
        if (this.preview) { this.lastFrame = this.frame(); this.lastKeys = directionKeys(this.lastFrame, this.mode); this.notify(); return; }
        if (!["active", "connecting", "paused"].includes(this.state)) { this.scan(); return; }
        frame = this.context(); this.lastFrame = frame;
      } catch (error) { await this.disconnect(error.message); return; }
      const rising = (index) => frame.buttons[index] && !this.previousButtons[index];
      const start = rising(9), mode = rising(8), speed = rising(11);
      this.previousButtons = [...frame.buttons];
      if (frame.buttons[1] && this.state === "active") { await this.disconnect("STOP по кнопке B. Отпустите органы управления; Start — явное продолжение.", true); return; }
      if (this.state === "paused" && start && this.canResumeFromStart && !this.busy && !this.stopping) {
        try { await this.connect(true); } catch (error) { this.reason = error.message; this.notify(); } return;
      }
      if (this.state !== "active") return;
      if (mode) this.pending.push("mode");
      if (speed) this.pending.push("speed");
      if (this.pending.length > 8) { await this.disconnect("Слишком много незавершённых переключений. Подключите управление заново."); return; }
      if (this.busy) return;
      const epoch = this.epoch;
      this.busy = true;
      try {
        if (this.pending.length) {
          const operation = this.pending.shift();
          await this.checkedPost(epoch, "input", this.packet([], true));
          const next = operation === "mode" ? this.mode === "cartesian" ? "joint" : "cartesian" : this.speed === "normal" ? "fast" : "normal";
          await this.checkedPost(epoch, operation === "mode" ? "mode" : "speed", { source: "keyboard", [operation]: next });
          if (operation === "mode") this.mode = next; else this.speed = next;
          this.neutralRequired = true; this.lastKeys = []; this.notify(); return;
        }
        const now = this.options.now();
        if (now < this.nextSend) return;
        const keys = this.neutralRequired ? [] : directionKeys(frame, this.mode);
        if (frame.neutral) this.neutralRequired = false;
        const packet = this.packet(keys, true);
        await this.checkedPost(epoch, "input", packet);
        this.lastKeys = [...packet.keys]; this.nextSend = now + 100;
      } catch (error) { await this.disconnect(error.message); }
      finally { this.busy = false; }
    }
    inputProvenance() { return { input_origin: "Browser Gamepad API", native_source: "keyboard", scheme: SCHEME, axes_encoding: "digitized_directions_native_speed_and_smoothing", mapping: "standard" }; }
  }

  function keyboardGuide(controls = {}) {
    const help = controls.help || {};
    return `<section class="panel spaced-section"><div class="panel-heading"><h2>Клавиатура · раскладка всегда под рукой</h2></div><div class="panel-body keyboard-guide"><p><strong>Шасси:</strong> <kbd>W</kbd>/<kbd>S</kbd> вперёд/назад; <kbd>A</kbd>/<kbd>D</kbd> mecanum влево/вправо; <kbd>Q</kbd>/<kbd>E</kbd> поворот.</p><p><strong>Захват XYZ:</strong> ${escape(help.keyboard_cartesian || "I/K — X вперёд/назад; J/L — Y влево/вправо; U/O — Z вверх/вниз.")}</p><p><strong>Суставы:</strong> ${escape(help.keyboard_joint || "J/L — J1−/+; I/K — J2+/−; U/O — J3+/−; Y/H — J4+/−; ,/. — J5−/+.")}</p><p><strong>Захват:</strong> <kbd>N</kbd>/<kbd>M</kbd> открыть/закрыть. <kbd>Shift</kbd> — точно; <kbd>−</kbd>/<kbd>+</kbd> — обычная/быстрая рука. <kbd>1</kbd>/<kbd>2</kbd> — XYZ/суставы.</p><p><strong>STOP:</strong> <kbd>Space</kbd> или <kbd>Esc</kbd>. <kbd>Enter</kbd> — явное продолжение после отпускания клавиш.</p></div></section>`;
  }
  function panel(status) {
    const supported = status.capability.supported;
    const label = { active: "Управление включено", connecting: "Подключаем управление", paused: "STOP", disconnected: "Управление отключено" }[status.state] || "Ожидание";
    return `<section class="panel spaced-section" data-browser-gamepad-panel><div class="panel-heading"><div><h2>Геймпад на Mac</h2><p>USB или Bluetooth подключён к Mac · Browser Gamepad API</p></div><span class="badge neutral" data-gamepad-state>${escape(label)}</span></div><div class="panel-body">${!supported ? `<p class="form-description">${escape(status.capability.reason)}</p>` : ""}${status.chassisProhibited ? `<p class="form-description"><strong>Движение шасси сейчас запрещено владельцем.</strong> Направления шасси не отправляются; команды руки используют прежний исполнитель.</p>` : ""}<div class="form-field"><label for="browser-gamepad-device">Контроллер</label><select id="browser-gamepad-device" data-gamepad-device><option value="">Выберите конкретный геймпад</option>${status.devices.map((device) => `<option value="${device.index}" ${status.selected?.identity === device.identity ? "selected" : ""}>${escape(device.id)} · №${device.index}${device.mapping !== "standard" ? " · нестандартная раскладка" : ""}</option>`).join("")}</select></div><div class="teleop-actions"><button type="button" class="button secondary" data-gamepad-action="scan">Найти контроллеры</button><button type="button" class="button primary" data-gamepad-action="connect" ${!supported || !status.selected || status.state === "active" || status.preview ? "disabled" : ""}>Наблюдаю ROSMASTER · подключить</button><button type="button" class="button danger" data-gamepad-action="disconnect">STOP · отключить</button></div><p data-gamepad-message role="status">${escape(status.reason)}</p><p>Режим: <strong data-gamepad-mode>${escape(MODE[status.mode])}</strong> · скорость руки: <strong data-gamepad-speed>${status.speed === "fast" ? "быстрая" : "обычная"}</strong>.</p><p class="form-description">Стики задают направления с обычной штатной скоростью; плавность сохраняет исполнитель ROSMASTER. Непрерывная аналоговая скорость этим адаптером не передаётся.</p><div class="keyboard-guide"><p><strong>Левый стик:</strong> ход и mecanum. <strong>L1/R1:</strong> поворот. <strong>L2/R2:</strong> открыть/закрыть захват.</p><p><strong>Правый стик:</strong> X/Y либо J1/J2. <strong>Y/A:</strong> Z вверх/вниз либо J3+/−. <strong>Крестовина:</strong> J4/J5 в суставном режиме.</p><p><strong>X:</strong> точно. <strong>Select:</strong> сменить режим. <strong>Start:</strong> явное продолжение после B. <strong>B:</strong> STOP. <strong>Нажатие правого стика:</strong> скорость руки.</p></div><details><summary>Проверка входов без движения и зона покоя</summary><label><input type="checkbox" data-gamepad-preview ${status.preview ? "checked" : ""}> Только смотреть входы, без управления</label><label for="browser-gamepad-deadzone">Зона покоя стиков</label><input id="browser-gamepad-deadzone" data-gamepad-deadzone type="number" min="0.05" max="0.4" step="0.01" value="${status.deadzone}"><p data-gamepad-inputs>Направления пока не нажаты.</p><p class="form-hint">Если контроллер не появился, нажмите и отпустите его кнопку, затем обновите список. Нестандартная раскладка не угадывается.</p></details></div></section>`;
  }

  function install(options) {
    const controller = new Controller(options), win = options.window, doc = options.document;
    const changed = options.onChange;
    options.onChange = (status) => { if (changed) changed(status); patch(); };
    function patch() {
      const node = doc.querySelector("[data-browser-gamepad-panel]"); if (!node) return;
      const status = controller.status();
      const messages = { "[data-gamepad-state]": { active: "Управление включено", connecting: "Подключаем", paused: "STOP", disconnected: "Отключено" }[status.state], "[data-gamepad-message]": status.reason, "[data-gamepad-mode]": MODE[status.mode], "[data-gamepad-speed]": status.speed === "fast" ? "быстрая" : "обычная", "[data-gamepad-inputs]": status.keys.length ? `Направления через штатные клавиши: ${status.keys.join(" · ")}` : "Направления не нажаты." };
      Object.entries(messages).forEach(([selector, value]) => { const target = node.querySelector(selector); if (target) target.textContent = value; });
      const connect = node.querySelector('[data-gamepad-action="connect"]'); if (connect) connect.disabled = !status.capability.supported || !status.selected || status.state === "active" || status.state === "connecting" || status.preview || status.busy;
    }
    function interrupt(reason) { if (["active", "connecting", "paused"].includes(controller.state) || controller.busy) controller.disconnect(reason).catch(() => {}); }
    win.addEventListener("blur", () => interrupt("Окно потеряло фокус. Подключите управление явно."));
    win.addEventListener("pagehide", () => interrupt("Страница закрыта. Управление отключено."));
    win.addEventListener("popstate", () => interrupt("Страница опыта изменилась."));
    win.addEventListener("gamepaddisconnected", (event) => { if (controller.selected?.index === event.gamepad.index) interrupt("Геймпад отключён от Mac. Автоматического продолжения нет."); });
    win.addEventListener("gamepadconnected", () => { try { controller.scan(); if (options.render) options.render(); } catch (error) { controller.reason = error.message; patch(); } });
    doc.addEventListener("visibilitychange", () => { if (doc.hidden) interrupt("Вкладка скрыта. Подключите управление явно."); });
    doc.addEventListener("keydown", (event) => {
      if (["Escape", "Space"].includes(event.code) && ["active", "connecting", "paused"].includes(controller.state)) {
        event.preventDefault(); interrupt("STOP с клавиатуры. Геймпад отключён.");
      }
    });
    doc.addEventListener("click", async (event) => {
      if (event.target.closest("a[data-link]")) interrupt("Выбран другой раздел Explorer.");
      if (event.target.closest('[data-action="robot-stop"]')) interrupt("Общий STOP. Геймпад отключён.");
      const button = event.target.closest("[data-gamepad-action]"); if (!button || button.disabled) return;
      try {
        if (button.dataset.gamepadAction === "scan") { controller.scan(); if (options.render) options.render(); }
        else if (button.dataset.gamepadAction === "connect") await controller.connect();
        else await controller.disconnect("STOP: геймпад отключён пользователем.");
      } catch (error) { controller.reason = error.message; patch(); if (options.error) options.error(error.message); }
    });
    doc.addEventListener("change", async (event) => {
      try {
        if (event.target.matches("[data-gamepad-device]") && event.target.value !== "") await controller.select(Number(event.target.value));
        if (event.target.matches("[data-gamepad-preview]")) await controller.setPreview(event.target.checked);
        if (event.target.matches("[data-gamepad-deadzone]")) await controller.setDeadzone(Number(event.target.value));
        if (options.render && event.target.matches("[data-gamepad-device],[data-gamepad-preview]")) options.render();
      } catch (error) { controller.reason = error.message; patch(); if (options.error) options.error(error.message); }
    });
    function loop() { controller.poll().catch((error) => { controller.reason = error.message; patch(); }); win.requestAnimationFrame(loop); }
    win.requestAnimationFrame(loop);
    return controller;
  }
  return { SCHEME, capability, qualifiedControls, snapshot, directionKeys, Controller, panel, keyboardGuide, install };
});
