# Исходный владелец: подготовленный монтаж и предел доказательства

Реальное read-only состояние Nano26.10.2026: explorer-web.service active,
MainPID2418, uptime18002.49s. Четыре critical source SHA совпали с предыдущим
аудитом. Свежий native status: STOP=true, MANUAL, velocity[0,0,0].
autonomous-day.enabled=false. Исходные6 JSON конфигурации не изменялись.

`native_startup_characterization.characterize_native_manual_startup` выполняет
точные исходные классы ManualArm/ManualTeleop/GamepadPanel через проверенный AST,
с fake publisher и перехваченным запуском threads. Не импортирует web/ROS/evdev,
не создаёт второго владельца, не обращается к устройствам. Результат:

| Условия | HOME attempts | Arm publications | Base callbacks |
| --- | --- | --- | --- |
| текущий uptime18002.49s, constructors+1000 idle ticks | 0 | 0 | 0 |
| отрицательный контроль uptime120s | 1, перехвачен | 0 | 0 |

Свежая загрузка действительно может отправить HOME90×6 на4000ms. Старый uptime
делает неактивной именно эту ветвь. **Полная runtime-проверка всего web/внешних
ROS dependencies на этом раннем этапе не выполнялась;
`full_web_ros_startup_proven=false` относится к этому scoped receipt.** Этот
receipt нельзя трактовать как безусловное разрешение перезапуска исходного
владельца. Аудит всех155 source-файлов сохранён приватно и привязывается SHA.
Первичная проверка конструкторов: Missions.active=None; PolicyExecution,
MobilePolicyExecution,TrajectoryExecution,DeliveryTask,TargetGaze стартуют idle;
AutonomySupervisor и OnboardAgent прерывают прежние задания, не возобновляют
motion; MeasuredVision.track=None. NavigationFootprint timer публикует costmap
polygon, не actuator команду. AutonomousDay имеет реальный dispatch callback,
поэтому для подготовки требуется его явно disabled профиль.

## Расширенная квалификация исходной web-фабрики

Последующий `native_web_startup_probe` выполняет исходный web.py и86 реально
импортированных native Python modules, все11 захваченных startup threads и3
таймера ×3 прохода. Используются exact private155 source inventory,
62 config/geometry assets и998 metadata/read-only SQL snapshots; SQL получен
через отдельные read-only транзакции9 databases. Ни один рабочий DB не меняется.
Проверены также реальный ASGI startup/shutdown и authenticated read-only GET.

| Factory | Uptime | Source actuator requests | Result |
| --- | --- | --- | --- |
| exact original | 18002.49s | 0 | passed |
| exact original | 120s | 1 original HOME90×6/time4000, fake boundary only | negative control detected |
| prepared same-owner candidate | 18002.49s | 0 | same existing arm+teleop, default permission=false |
| prepared candidate | 120s | 0 | guard rejects before rclpy/threads/owners |

Перехвачены ВСЕ создаваемые ROS publishers, action/service boundaries, source
subprocess/OS execution, camera opening и sockets. Разрешён только mock read-only
`systemctl --user is-active explorer-train.service`. Реальный connected-but-
DISARMED gamepad snapshot воспроизведён без новых операторских событий. За счёт
контролируемой fixture обновляется только receive-time status.at, не сенсорные
значения; это не запись реального измерения для обучения. Циклы ограничены
инструментацией AST, исходные motor methods не заменяются.

Положительный факт — **полная исходная Python startup-фабрика не запрашивает
движение при проверенных current-boot условиях**. Внешние ROS/MoveIt/evdev drivers
инжектированы, их реализация и физическое поведение не исполнялись и не объявлены
квалифицированными. Uvicorn TCP listener не запускался. Полные input/adapter SHA,
trace и условия сохраняются в private qualification receipts; публичный summary
не содержит камер/секретов. Фактический оригинальный владелец не перезапускался.

## Подготовка точного patch и rollback

`native_owner_patch.prepare_native_owner_patch(source_root, output_dir,
pinned_shared_path=ABSOLUTE_IMMUTABLE_RELEASE_SHARED, nano_node_id=ACTUAL_NODE_ID,
baseline=original_six_config_hashes)` работает только с локальными файлами.
Требует exact original web SHAbe68ea…74f5, сохраняет web.original.py,
web.patched.py и plan.json с обоими SHA, полным source inventory и baseline.
Ни service/unit/env, ни конфигурации не пишет, не перезапускает процесс.

Candidate добавляет read-only `assert_native_restart_safe` **до rclpy.init**:
проверяет154 исходных module SHA,6 baselineSHA, отсутствие другого
controller-profile, uptime за пределами исходного startup_window, disabled
AutonomousDay и свежий actual native manual STOP с известной нулевой velocity.
Это дополнительные условия активации приложения, не новые настройки/лимиты
контроллера. Условия не заменяют отсутствующую full-owner qualification.

После существующего `gamepad_panel.bind_arm(manual_arm,reference_arm)` candidate
монтирует API через `mount_factory_owner` на тех же двух объектах. Новый ROS
node/publisher, servo worker и камера не создаются. Default execution=false до
явной metadata-only observed authorization ниже; редактировать код для её
включения не требуется.
Глобальный web.stop_all тоже заменяется обёрткой mux: обычный `/api/control` STOP
сразу инвалидирует latest intent и его permit, даже если teleop.stop не вызван.
Все исходные STOP callbacks по-прежнему выполняются.

`apply_prepared_native_patch(root, prepared_dir, rollback=False)` — отдельная
явная операция **только атомарной замены web.py**, exact-current-SHA guarded.
Она повторно проверяет baseline/guard и не перезапускает сервис. Rollback=True
восстанавливает точные original bytes только если текущий SHA соответствует
известному candidate; неизвестное последующее изменение не затирается.
Перезапуск после apply/rollback остаётся отдельным действием интегратора в
согласованном idle/наблюдаемом окне. MCU/камера/драйверы/настройки не трогаются.

## Минимальные этапы активации в web03

1. **Software mount pending**: показать source/patchSHA, native_owner_ready=false
   до реального монтажа. Это не ошибка SO101 calibration.
2. **Original-owner restart pending**: расширенная source-фабрика квалифицирована
   в описанной области. Интегратор проверяет actual source/config/idle/uptime и
   отдельное окно активации; это не выполненный remote restart и не физическая
   квалификация внешних drivers. Ни один агент не перезапускал владельца.
3. **Native reference invalidated**: реальный arm-state.json reference90×6 имеет
   SHA72282f…c24, а arm-telemetry-fault.json новее. Исходный ManualArm.reference
   правильно отвергает такую оценку. Не удалять fault, не принимать старый q и
   не включать feedback. Новая заводская подготовка позы допустима только в
   отдельном наблюдаемом тесте владельца.
4. **Cross-map pending**: сохранить известную SO101 calibration; запросить только
   physical axes/zero/scale/wrist/gripper correspondence. UNREGISTERED не означает
   повторную калибровку устройств. Source режимы/09D не расщеплять.
5. **Observed shadow/neutral qualification**: metadata activation остаётся false
   до отдельного явно наблюдаемого подтверждения. API acceptance, native host publish,
   servo attainment/contact показываются раздельно; feedback остаётся null.

Guarded reference reader повторяет исходную no-readback проверку и сохраняет
точные bytes исходного arm-startup.json (base64+SHA в dispatch source page).
Поздний fault отвергается. Post-publication canonical reference теперь содержит
**current native command9**, согласованный с его stamp/receipt; preceding q
Ruckig остаётся только в preceding_command9 с собственной temporal semantics.

## Authenticated web ABI без SSH и без автоматического движения

Все JSON операции доступны Nano Node через `/legacy/explorer/retarget/activation/`:

* GET `status` — authorization_id, execution_permission, native STOP/active,
  source receipt/events; отсутствие разрешения — нормальный default;
* GET `challenge` — challenge_id, exact native frame ID/SHA/receiveclock/boot;
* GET **Nano Node** `/native-activation/frame/{challenge_id}` — точные bytes
  того закреплённого JPEG (не latest fallback), headers X-JPEG-SHA256,
  X-Frame-ID, X-Monotonic-NS, X-Boot-ID, Cache-Control:no-store. Query запрещён;
* POST `authorize` — `{challenge_id, displayed_jpeg_sha256, actor_id,
  observing:true, neutral:true, space_clear:true,
  user_observed_test_confirmed:true}` после показа именно pinned frame;
* POST `revoke` — только отзыв metadata permission, без actuator command/lease.

Challenge/frame и ongoing camera используют существующий2s freshness contract,
без другого capture/stream owner. Auth token и actor берутся из центральной
authenticated session. ID authorization привязан к native generation/epoch,
передаётся во всех новых begin/recenter/resume/input/base. Pi client получает его
из status.activation только при явном start. Clutch transition сохраняет связь
атомарно по metadata revision; поздний status/camera error старой authorization
не отзывает новую. STOP/preemption/camera loss инвалидируют и старый nonzero
Ruckig permit. Permission само НЕ продлевает исходный native user lease.

**Отдельная физическая кнопка исходной позы**: Nano Node POST
`/legacy/arm/prepare` принимает `{observing:true, space_clear:true,
authorization_id, acknowledge_factory_home_motion:true}`. Bridge требует fresh
observed authorization, native STOP и inactive mux, отсутствие common capture;
передаёт исходному `/api/arm/prepare` **ровно** `{observing:true,space_clear:true}`.
Это тот же ManualArm.home_reference(True), pose90×6/time4000, не новый executor.
UI обязан прямо предупредить о движении при нажатии и требовать отдельного
user-observed test. Агент этот POST никогда не выполняет.

Исходная prepare сама вызывает original global STOP, поэтому pre-HOME
authorization отзывается. После завершённого реального90 receipt и успешного
исходного reference guard UI показывает новый pinned challenge и требует новое
наблюдаемое подтверждение mirror перед явным start. Не переиспользовать старое
permission, не стирать fault, не включать feedback и не auto-resume.

Для отдельно наблюдаемого теста шасси authorize дополнительно принимает
`input_mode:keyboard|browser_gamepad|so101_leader` и явный
`owner_chassis_test_authorized:true`. DefaultFalse; это scoped permission к
существующему executor/lease, не изменение motor limits/watchdog. Текущая
`activation.status.chassis_motion_allowed` учитывает camera/native lease/owner.
Для02 браузерный gamepad всё так же использует native source=keyboard;
input_mode сохраняет честное происхождение directions bridge.
