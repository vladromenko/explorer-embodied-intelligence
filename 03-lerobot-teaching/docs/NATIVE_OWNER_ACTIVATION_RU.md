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
ROS dependencies не выполнена; `full_web_ros_startup_proven=false`.** Этот
receipt нельзя трактовать как безусловное разрешение перезапуска исходного
владельца. Аудит всех155 source-файлов сохранён приватно и привязывается SHA.
Первичная проверка конструкторов: Missions.active=None; PolicyExecution,
MobilePolicyExecution,TrajectoryExecution,DeliveryTask,TargetGaze стартуют idle;
AutonomySupervisor и OnboardAgent прерывают прежние задания, не возобновляют
motion; MeasuredVision.track=None. NavigationFootprint timer публикует costmap
polygon, не actuator команду. AutonomousDay имеет реальный dispatch callback,
поэтому для подготовки требуется его явно disabled профиль.

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
node/publisher, servo worker и камера не создаются. Default execution=false.
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
2. **Original-owner restart pending**: full_web_ros_startup_proven=false требует
   отдельной user-observed activation либо расширенной квалификации; текущий
   guard не объявлять доказательством всего startup. Ни один агент не перезапускал
   исходного владельца.
3. **Native reference invalidated**: реальный arm-state.json reference90×6 имеет
   SHA72282f…c24, а arm-telemetry-fault.json новее. Исходный ManualArm.reference
   правильно отвергает такую оценку. Не удалять fault, не принимать старый q и
   не включать feedback. Новая заводская подготовка позы допустима только в
   отдельном наблюдаемом тесте владельца.
4. **Cross-map pending**: сохранить известную SO101 calibration; запросить только
   physical axes/zero/scale/wrist/gripper correspondence. UNREGISTERED не означает
   повторную калибровку устройств. Source режимы/09D не расщеплять.
5. **Observed shadow/neutral qualification**: default permitFalse остаётся до
   отдельного явно наблюдаемого теста. API acceptance, native host publish,
   servo attainment/contact показываются раздельно; feedback остаётся null.

Guarded reference reader повторяет исходную no-readback проверку и сохраняет
точные bytes исходного arm-startup.json (base64+SHA в dispatch source page).
Поздний fault отвергается. Post-publication canonical reference теперь содержит
**current native command9**, согласованный с его stamp/receipt; preceding q
Ruckig остаётся только в preceding_command9 с собственной temporal semantics.
