# Геймпад Mac в опыте 02

`00-control-center/src/explorer/static/gamepad.js` — браузерный клиент существующего
ручного исполнителя ROSMASTER. USB/Bluetooth геймпад остаётся на Mac. Нового
motor backend, драйвера, calibration, PID, watchdog или controller settings нет.
Шасси при проверке этого кода **не двигалось**; запросы движения на устройства
не отправлялись.

## Проверенный контракт

26 октября прочитаны настоящие авторизованные GET
`/legacy/teleop/controls` и `/legacy/teleop`. Схема — `explorer-manual-v2`.
Также прочитан публичный HTML штатной панели: её прежний gamepad режим
использует локальный Nano backend, а не Browser Gamepad API. Доказательство GET
и SHA сохранены в `_registry/evidence/browser-gamepad-readonly-controls.json`;
полная раскладка без секретов — `tests/fixtures/explorer-manual-v2-controls.json`.
Никаких live POST, даже neutral, эта квалификация не делала.

Поскольку HTTP-контракт raw axes/buttons прежнего gamepad backend не найден,
новый клиент использует **точно известный** keyboard contract того же native
manual executor:

```text
POST /api/robot/teleop/select
  {source:"keyboard", observing:true, neutral:true}
POST /api/robot/teleop/input
  {source:"keyboard", keys:[], observing:true, arm_mode:"cartesian"|"joint"}
POST /api/robot/teleop/resume
  {source:"keyboard", observing:true}
```

Стики преобразуются в направления штатных клавиш. Величина отклонения после
зоны покоя не изменяет скорость: это **digitized directions**, а не непрерывный
аналоговый velocity interface. Скорость и сглаживание остаются у существующего
native executor. UI сообщает это явно. Source `gamepad` не подставляется в
непроверенный payload. Никакие `/gamepad/mode`, arm prepare, direct servo или
base velocity endpoints не добавляются.

Клиент сверяет не только ID схемы, но и точные именованные key meanings из
свежего controls ответа. Иная раскладка не приобретает управление.

## Органы управления

- Левый стик — W/S и A/D; L1/R1 — Q/E.
- L2/R2 — N/M (открыть/закрыть захват).
- Правый стик — I/K и J/L: X/Y либо J2/J1 согласно native раскладке.
- Y/A — U/O: Z либо J3.
- Крестовина — Y/H и ,/. для J4/J5 только в суставном режиме.
- X — Shift precision; Select — существующий `teleop/mode`; RS-click —
  существующий `teleop/speed` normal/fast.
- B — neutral + STOP + disconnect, затем только явный Start может продолжить
  этот пользовательский STOP после нейтрали. После ошибок/потери окна/камеры/
  устройства требуется новая явная кнопка подключения; Start сам не подключает.

При смене режима/скорости клиент отправляет нейтраль и ожидает отпускания стиков
и кнопок перед передачей новых направлений. Кнопки переключения обрабатываются
по фронту, не повторяются каждый animation frame. Browser standard mapping и
button/axis indices сверены с [первичной спецификацией W3C](https://www.w3.org/TR/gamepad/#remapping).
Неизвестная mapping не угадывается.

## Жизненный цикл

Выбор конкретного controller index/ID обязателен. Нейтральные стики и кнопки
проверяются до и после каждого шага подключения; настоящий neutral packet
предшествует resume. Один input request находится в полёте; неподтверждённая
команда не повторяется автоматически. Текущая идентичность устройства, фокус,
страница опыта и доступность камеры проверяются при каждом browser poll.

На blur, pagehide, скрытие вкладки, навигацию, disconnect/replacement controller,
ошибку transport/schema либо потерю свежего ROSMASTER image клиент инициирует
neutral + STOP + disconnect. STOP не ожидает успешного ответа neutral request.
Поздний ответ controls/select/resume не восстанавливает локальную сессию: epoch
изменяется до cleanup. Исходные native lease semantics не меняются.

Это клиентская проверка поверх прежнего native API, не заявление о новой
межпроцессной атомарности старых independent web клиентов. Реальное движение,
latency/STOP на Mac+геймпаде и аппаратные результативные показы ещё нуждаются в
отдельной разрешённой проверке; зарядочный запрет движения шасси сохраняется.

## Точные hooks для root

Загрузить `gamepad.js` после основного app.js. Он ничего не подключает и не
отправляет автоматически. Bootstrap один раз:

```javascript
const browserGamepad = ExplorerBrowserGamepad.install({
  window, document,
  secure: window.isSecureContext,
  getGamepads: typeof navigator.getGamepads === "function"
    ? () => navigator.getGamepads() : undefined,
  visible: () => !document.hidden,
  focused: () => document.hasFocus(),
  routeActive: () => activeProject()?.id === "02" && !dialog.open,
  cameraReady: () => actualFreshRosmasterImageAtBrowser(),
  now: () => performance.now(),
  controls: async () => { await loadRobot(); return model.robot.controls; },
  nativeState: () => model.robot.teleop || {},
  post,
  filterKeys: (keys) => filterManualKeysForCurrentOwnerConstraints(keys),
  chassisProhibited: () => model.state?.constraints?.chassis_motion === "PROHIBITED_BY_OWNER",
  beforeConnect: async () => {
    if (keyboard.active || keyboard.busy) await stopKeyboard(true);
    // Drain any former keyboard request before selecting the same native source.
    while (keyboard.busy) await new Promise((resolve) => setTimeout(resolve, 10));
  },
  render: () => { model.forceRender = true; render(); },
  error: (message) => toast(message, true)
});
```

`actualFreshRosmasterImageAtBrowser()` — обязательная часть root camera UI.
Один `cameraLoaded` либо старое `node.online` недостаточны: прежний inventory
poll 5s удерживает online до 20s. Для контроля нужен недавно **полученный и
отображённый** ROSMASTER cached JPEG, с local browser receipt time; Pi обзор
не подменяет выбранную ROSMASTER камеру. При недоступном/просроченном image
callback возвращает false, и controller завершает lease. Mac и Nano monotonic
clocks не сравниваются как одна шкала. Возможен отдельный периодический
snapshot preview из того же camera owner; новых USB захватчиков не требуется.

На странице 02 рядом с настоящей камерой/статусом записи разместить:

```javascript
ExplorerBrowserGamepad.panel(browserGamepad.status())
ExplorerBrowserGamepad.keyboardGuide(model.robot.controls || {})
```

Keyboard guide открыт постоянно, показывает оба режима, gripper, precision,
STOP/resume и arm speed. В root keydown handler также требуется существующее
`scheme.arm_speed` Minus/Equal → `/teleop/speed`, чтобы инструкция −/+ совпадала
с исполнением. Модуль не редактирует app.js за интегратора.

Перед `startKeyboard()` root должен завершить `browserGamepad.disconnect()` и
дождаться `browserGamepad.busy/stopping === false`, чтобы два клиента не
посылали один native source одновременно. Нужна такая же координация при
открытии модальных control/record dialogs. UI `robot-stop`, Escape/Space и
window lifecycle уже обрабатываются install; global STOP сохраняет приоритет.

`browserGamepad.inputProvenance()` возвращает честное происхождение интерфейса:
Browser Gamepad API → keyboard wire → digitized directions. Root recording
metadata должно сохранить этот source отдельно от native keyboard source,
если анализ сравнивает интерфейсы; нельзя называть его raw analog gamepad.
Запись host intent по-прежнему не становится измеренным target q или expert
BC episode из-за появления новой кнопки.

## Mac / HTTPS / проверка

Capability проверяется по `window.isSecureContext`, наличию getGamepads и
реальному ответу браузера. Неуспех security/permissions показывается явно;
наличие URL HTTP не выдаётся за поддерживаемый Gamepad API. Root предоставляет
проверенный частный HTTPS адрес. Браузер может показывать устройства только
после пользовательского нажатия/отпускания кнопки — это описано в
[W3C getGamepads](https://www.w3.org/TR/gamepad/#dom-navigator-getgamepads).

`filterKeys` может только убирать направления. Root использует общий фильтр
manual clients: при текущем запрете шасси исключаются только W/S/A/D/Q/E;
ключи руки сохраняются. Это прямое соблюдение ограничения владельца, а не
изменение native controller settings. Панель показывает запрет явно.

В UI есть явные scan/select/connect/disconnect, preview inputs без управления,
зона покоя только в клиенте, текущие режим/скорость и инструкции. Неподдерживаемая
нестандартная раскладка остаётся неподдерживаемой.

`node tests/browser/gamepad.test.cjs` исполняет 18 полезных сценариев с in-memory
transport mocks. `tests/test_browser_gamepad.py` включает их в pytest. Проверены
точный wire и neutral-before-resume, все направления/edges, safety lifecycle,
late async reply, source handoff, schema mismatch, preview и capability. Никакие
network calls или actual chassis motion в harness невозможны. Это
SOFTWARE_TESTED, а не HARDWARE_TESTED_ON_MAC.
