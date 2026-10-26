# Прямой ARM_ONLY: измеренный SO-101 → ROSMASTER

Новый программный маршрут — `shared/explorer_shared/arm_only_retarget.py` и `pi_nano_route.py`. Методы: `SO101_URDF_FK_IK`, `OBJECT_RELATIVE_RETARGET`, `ENGINEERING_GLUE`. Homebrain/NX не участвует в цикле измерений, IK, команд или heartbeat. Это не цикл обучения и не копирование индексов суставов SO-101 на M3.

Слои:

1. Pi получает уже существующий **измеренный** cached leader snapshot из SO101Station. Новый код не открывает servo bus, не включает torque и не командует follower.
2. Именованные координаты LeRobot переводятся в радианы SO-101 только по отдельным зарегистрированным парам измерений. Source FK использует `so101_leader_new_calib.urdf`, `base_link → gripper_frame_link`.
3. Recenter сохраняет source FK и текущую сообщённую командную опору Nano. Последующие изменения TCP переводятся через измеренное соответствие осей source base → target base. Масштаб — метры/метры без произвольного gain. Абсолютное смещение двух столов не подставляется вместо clutch anchors.
4. M3 IK использует `explorer.urdf`, `base_footprint → Gripping`. Пять целевых радиан переводятся по именованной регистрации в пять servo endpoint degrees, затем округляются под прежний ABI. Гриппер рассчитывается отдельно. Результат — **ровно шесть int**, без vx/vy/wz.
5. Pi отправляет новое геометрическое предложение непосредственно Nano observer. Nano использует прежние HTTP plan/start/lease/stop и исходный MoveIt/arm executor. Контроллер, motion/power/profile, firmware, watchdog, калибровки и старый web не редактируются.

`ArmOnlyGeometry(..., registration_path=None).status()` возвращает **UNREGISTERED** и конкретные отсутствующие поля. Профиль не создаётся с выдуманными physical zero/frame/aperture. Полная декларация даёт только `REGISTERED_DECLARATION`; `physical_acceptance_confirmed=false` остаётся явным. Успех IK, запись operator-флага или SHA файла не доказывают физическую регистрацию/успех задачи. Актуальная реальная установка пока не зарегистрирована этим модулем.

## Точные данные регистрации

Нужен новый независимый JSON `registration_state=measured`, с `registration_id`, совпадающими source/target URDF SHA и следующими полями. Существующие controller/config/calibration files не изменяются.

| Поле | Что сохранить |
|---|---|
| `source_calibration` | path + SHA оригинального **leader** LeRobot calibration JSON. Нормализация сама по себе не задаёт URDF zero/sign. |
| `source_joint_points` | Пять точных имён `shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll`; для каждого ≥2 пары `[измеренная normalized coordinate, соответствующий URDF rad]`. Не выводить radians из процента диапазона URDF. |
| `target_joint_points` | Имена `arm1_Joint..arm5_Joint`; для каждого ≥2 пары `[известный URDF rad, соответствующий оригинальный servo endpoint degree]`. Nominal `(servo−90)` и первый знак минус не выдаются за физически проверенный zero. |
| `target_base_from_source_rotation` | Измеренная proper 3×3 rotation, переводящая source base axes в target `base_footprint` axes. Нужна идентификация трёх осей/ориентаций обеих баз в общей измерительной системе; reflection/scale недопустимы. |
| `orientation_mode` | Явно `position_only` либо `pose`. В первом случае orientation не обещается; во втором недостижимая поза возвращает UNREACHABLE. |
| `gripper.source_trigger_closed_open` | Два реально зарегистрированных normalized leader trigger endpoints, соответствующих жестам «закрыть/открыть». Это жест, не измеренное раскрытие source jaw. |
| `gripper.target_aperture_closed_open_m` | Требуемые физически измеренные раскрытия target в метрах для этих двух жестов. |
| `gripper.target_aperture_m_to_servo_deg` | Отдельная монотонная по aperture таблица ≥2 пар `[измеренное раскрытие M3 в метрах, servo degree]`. Сила/удержание предмета не выводятся из положения. |
| `evidence` | Четыре path/SHA/calibration_id: `source_joint_zero_and_direction`, `target_servo_zero_and_direction`, `frame_axes`, `gripper_aperture`. Файлы читаются только для сверки SHA. |

Source pairs фитируются affine отдельно по каждому имени, residual сохраняется в объекте. Никакой identity joint mapping нет. Target quantization residual выводится отдельно от float IK residual: совпадение с численным IK tolerance не обещается после целочисленного endpoint. Проверка реальных нулей/направлений, точности, ориентации TCP и aperture выполняется регистрацией/экспериментом оператора; исходные runtime settings не подменяются. Геометрический модуль сам collision/depth scene не проверяет — исходный исполнитель выполняет собственный неизменённый planning/path admission.

## Почему не одношаговый `/arm/finite`

Аудирован оригинальный `web.py` SHA `be68ea5233d1edb8aef476151b2ebe32c88f6bd90a761e23ef31ce54099f74f5` и `trajectory_execution.py` SHA `53d82efdef94280ef319a72a11e7b71e0c7ef5ab2be574ff9f027978bdec1083`. Исходники только прочитаны, не импортированы/запущены.

`/api/arm/finite` делает plan+start внутри одного вызова без caller epoch/expected revision. Запоздалый запрос может начать новый plan уже **после STOP**, увидев новый revision. Поэтому direct gateway использует тот же существующий executor через **две фазы**:

```text
POST arm/trajectory/plan   {goal_deg: [6 ints]}
  → plan_id + goal_deg; original planner stores stop_revision
  → recheck direct owner/control_epoch
POST arm/trajectory/start  {plan_id, observing:true}
  → original start compares saved/current stop_revision
POST arm/trajectory/lease  {session, held:true}   # Только реальный operator heartbeat
POST arm/trajectory/stop   {}                    # ARM_ONLY, без base/teleop/control calls
```

Это прежний `operator_held` с прежней `.6s` lease, а не скрытый `finite=True`/600s. Никакого нового watchdog или изменения сроков контроллера нет. Heartbeat идёт от существующего явного operator held/presence flow; worker не выдумывает его сам. При отпускании кнопки/потере heartbeat срабатывает существующий executor. Уже принятый ограниченный robotio endpoint может закончиться; остановка MCU/механики и физическая задержка STOP не заявляются.

Nano gateway хранит owner, control_epoch, монотонный sequence, целевую original session и один inflight request. Смена anchor/recenter закрывает прежнюю сессию и выдаёт новый epoch, поэтому старые packets/lease не применяются к новой опоре. STOP сначала инвалидирует epoch, затем вызывает прежний arm-only cancel. Между plan и start повторно проверяется epoch; STOP после проверки инвалидирует original pending revision. Ошибка/потерянный start reply означает UNKNOWN_OUTCOME, arm-only cancel и **никакого автоматического retry**. Новый start всегда явный.

`requested` — шесть предложенных Pi endpoints. `accepted` появляется только из совпавшего `goal_deg` оригинального API acknowledgement с original plan/session. `dispatched=null`, `dispatch_confirmed=false`: host API ACK не доказывает публикацию, приём MCU или измеренное движение. Оригинальные receipts/status сохраняются отдельно. `physical_stop_confirmed=false` также не подменяется фиктивным успехом.

Старая независимая web-панель остаётся отдельным источником управления. Чужая original session/STOP обнаруживается по status и прерывает новую route ownership. Это не закрывает старый bypass: глобальный STOP/смена владельца должны явно вызывать `cancel_all` новой route при интеграции. Нельзя заявлять абсолютную межпроцессную транзакционность за пределами этих audited API.

## Минимальная интеграция root без изменений старого исполнителя

Новые модули сами не запускаются и устройство не развёртывают. Интегратор изменяет только свои Node/UI:

| Узел | Минимальная связь |
|---|---|
| Nano Node | Создать `NanoArmGateway(legacy_bridge.request, registered_geometry_status)` из согласованной декларации. При отсутствии профиля — UNREGISTERED. |
| Nano Node routes | Authenticated `GET /arm-retarget/status → status(refresh=True)`; POST `start/action/lease/stop → start/action/heartbeat/stop`. При global STOP — `cancel_all()`, в том числе после ambiguous result. |
| LegacyBridge allowlist | GET `arm/trajectory`; POST `arm/trajectory/plan`, `arm/trajectory/start`, `arm/trajectory/lease`, `arm/trajectory/stop`. Это существующие original endpoints, не новые моторные команды. |
| Pi Node | `NanoArmHTTPClient(Nano observer LAN URL, existing Nano node token)` + `PiNanoRetargetSession(geometry, client)`. Endpoint/token из закрытой observer config; NX URL не нужен. |
| Pi source adapter | Cached `station.measurements.leader` → `positions[6]`; `joint_names=SO101 names`, `units=5×calibrated_normalized_-100_100 + normalized_0_100`, `measured=true`, original `monotonic_ns/clock_domain/control_epoch`, фактический leader calibration SHA из `station.roles.leader`. Не открывать второй bus и не вызывать локальный follower teleop/start. |
| Pi runtime | Явные start/recenter/step/heartbeat/stop; `run(read_cached_measurement, stop_event)` можно запустить отдельным worker вне station locks. 30 Hz — чтение готового cache, не частота моторных endpoint. BUSY/одинаковые cached snapshots отбрасываются без очереди, heartbeat остаётся отдельным операторским событием. |
| UI03 | Отдельная цель **ROSMASTER**, профиль ARM_ONLY, фактический REGISTERED/UNREGISTERED state, source feedback, target API receipt, recenter, existing held heartbeat и STOP. Локальная SO-101 follower-панель не является этой целью. Не добавлять chassis controls. |

Root выполняет конкретную аппаратную регистрацию и реальные испытания отдельно. Локальные тесты содержат условные calibration fixtures, реальные URDF FK/IK и подставной HTTP executor, включая сериализацию direct Pi→Nano. Они проверяют алгоритм/ABI/STOP race/epoch/error handling и **не являются** свидетельством физической калибровки или работы оборудования.

## Подключённый Node runtime

NodeAgent теперь создаёт маршрут на обоих узлах. По умолчанию профиль
`~/.config/explorer/arm-retarget-registration.json` отсутствует, поэтому
`GET /arm-retarget/status` возвращает `UNREGISTERED`. Такой запрос и попытка
`start` при отсутствии регистрации не обращаются к старому motor API.
Неверный JSON регистрации также остаётся `UNREGISTERED`, а камеры продолжают
иметь собственный сервис. Калибровки и controller settings не переписываются.

На Pi адрес Nano и **существующий** закрытый token observer читаются из
`~/.config/explorer/arm-retarget-peer.json`; пример без рабочего секрета находится
в `configs/arm-retarget-peer.example.json`. Этот файл нельзя коммитить с настоящим
значением. На Nano профиль регистрации и его evidence должны быть доступны
локально с теми же SHA. Совпадение `registration_id` и всего профиля обязательно.
Наличие файла не заменяет реальные измерения и процедуру допуска.

Авторизованные API:

- Pi: GET `status`; POST `start`, `recenter`, `lease`, `stop` под `/arm-retarget/`.
  `start/recenter` требуют `expected_control_epoch` из свежего Pi status.
  `lease` требует `control_epoch`, возрастающий `sequence`, `held=true`,
  `observing=true`. `stop` может дополнительно проверять
  `expected_control_epoch`; пустой объект — явный STOP текущего маршрута.
- Nano: GET `status`; POST `start`, `action`, `lease`, `stop`, `cancel` под тем же
  префиксом. `cancel` — явная повторная доставка STOP после неопределённого
  ответа; команда движения никогда автоматически не повторяется.

На Pi SO101Station остаётся в `idle`: сервис только читает уже измеренный leader
cache и SHA оригинальной leader calibration. Он не вызывает `teleop/start`, не
включает follower и не открывает второй serial bus. Устаревшее или будущее
измерение отклоняется по уже существующему freshness contract камеры.
`recenter` при незавершённой обработке sample возвращает отказ: следующая попытка
нуждается в новом явном запросе, прежний sample не попадает в новую anchor.

Один явный операторский heartbeat разрешает обработать не более одного
coalesced sample. Pi worker не генерирует lease самостоятельно. Наличие новых
leader measurements после отпускания кнопки само по себе не запускает команды.
Таким образом частота команд соответствует фактическим held-events и готовности
исходного планировщика; этот runtime **не заявляет 30 Гц моторного исполнения**.
После STOP/start-race поздний ответ start отменяется и не восстанавливает
маршрут. Новый процесс начинает с `IDLE`, без восстановления старых команд.

Каждая явная сессия создаёт частный
`node-state/direct-teaching/<run_id>/events.jsonl`. Journal сохраняет fsync после
события: исходный measured snapshot/clock/calibration SHA, registration SHA,
геометрическое предложение, оригинальные receipts и STOP. Записи не объявляются
синхронизированным датасетом target RGB-D: источник Nano камеры и достоверность
фактической доставки остаются отдельными критериями. Логи не превращаются
автоматически в BC-targets.

Глобальный STOP, переданный через существующий Explorer Nano adapter,
инвалидирует direct epoch и доставляет оригинальный arm-only STOP перед штатным
STOP. Конкурирующие motor requests через **тот же новый adapter** отклоняются
на время прямого владения. Прежний web-процесс, вызываемый в обход observer,
по-прежнему не становится транзакционной частью этого mutex. Полная физическая
арбитрация исходных независимых процессов ещё должна быть подтверждена.

`direct03.js` предоставляет настоящую отдельную панель ROSMASTER ARM_ONLY:
фактический статус регистрации, явное включение anchor, recenter, удерживаемая
кнопка передачи и STOP. Она использует интеграторский proxy
`/api/retarget/{operation}`. Обычная панель leader→follower SO-101 не выдаётся за
этот прямой маршрут.

Тест `tests/test_direct03_nodes.py` запускает два настоящих localhost HTTP
NodeAgent с bearer authentication и подставленным original executor. Проверены
Pi→Nano сериализация, measured cache, отсутствие команд после последнего
held-event, fsynced journal, stale source, recenter epoch, STOP во время start,
отказ competing manual request и потеря start/STOP replies. Эти результаты —
`SOFTWARE_TESTED`; аппаратный маршрут и регистрация пока `NOT_VALIDATED`.
