# Единый исходный эпизод ROSMASTER 9D

`shared/explorer_shared/embodied_episode.py` — чистый анализ переданных JSON и
байтов. Он не открывает камеры, порты, сеть или файлы, не выполняет команды и
не меняет настройки контроллера. Допуск означает проверенный **контракт исходных
свидетельств**, а не физическую валидацию робота, научную новизну или освоение навыка.
Все положительные примеры в `tests/test_embodied_episode.py` синтетические.
Три имеющиеся реальные записи наблюдений сами по себе полного допуска не имеют.

## Неизменный объект обучения

Experiment02 всегда описывает один мобильный манипулятор. Контракт
`rosmaster_joint9_position6_velocity3_v1` содержит одновременно:

| Координаты | Единицы |
| --- | --- |
| base, shoulder, elbow, wrist_pitch, wrist_roll, gripper | deg |
| vx, vy, wz | m/s, m/s, rad/s |

Первый `base` — сустав основания руки. Все девять координат сохраняются в одном
векторе. Реально записанные нули валидны; неизвестное значение остаётся `null`
с невалидной маской. Нет отдельных arm-only/base-only контрактов или режимов
обучения. `targeted_subskill` и `recovery` обозначают область эпизода внутри того
же навыка и сохраняют весь 9D-контракт.

## Python API и размещение в импорте

```python
from explorer_shared.embodied_episode import (
    inspect_embodied_episode, canonicalize_episode,
    sensor_contract, observation_channels, hierarchy_plan,
)
report = inspect_embodied_episode(envelope, {"facts.json": original_json_bytes,
                                            "rgb.bin": original_rgb_bytes})
bundle = canonicalize_episode(original_envelope_bytes, source_payloads)
assert bundle.source_bytes["envelope"] == original_envelope_bytes
```

`bundle.source_bytes['payloads']` сохраняет все переданные байты под исходными
именами, включая неиспользованные файлы; контейнеры доступны только для чтения.
При передаче `dict` вместо байтов создаётся помеченная каноническая сериализация,
которую отчёт **не** называет оригинальными байтами оболочки. Анализ не изменяет
оболочку или исходные payloads.

Манифест импорта может содержать `embodied_episode` либо относительный
`embodied_episode_path` вместе с `embodied_episode_sha256`. Таблица
`embodied_source_files_sha256` задаёт файлы относительно каталога манифеста.
Загрузчик проверяет разрешённые пути и хеши, передаёт точные байты модулю и
сверяет `episode_id` с ID оригинальной записи. Модуль дополнительно проверяет
внутреннюю таблицу `source_files_sha256`; абсолютные пути и `..` не допускаются.
При отсутствии оболочки возвращается типизированный analysis-only отчёт.

## Исходная оболочка `explorer_embodied_episode_v1`

Обязательные поля:

| Поле | Значение |
| --- | --- |
| episode_id, run_id | Непустые исходные идентификаторы |
| project_id, source_kind | `02` или ROSMASTER-target `03`; `physical` или `simulation` |
| skill | id, version, variant_id, session_id |
| episode_scope | whole_episode, targeted_subskill, recovery |
| action_contract | id, names, units; simultaneous=true |
| identity | Те же episode_id/run_id/project_id/source_kind/skill/episode_scope/action_contract, с исходным доказательством |
| source_files_sha256 | Относительное имя → SHA256 точных байтов |
| clock_domains | Имя → clock_type=monotonic, boot_id, доказательство |
| causal_protocol | max_observation_age_ns, доказательство протокола данных |
| recording | status=completed, source_quality_usable, operator_outcome, доказательство |
| samples | Непустой список одновременных действий и наблюдений |
| phases | Неперекрывающиеся последовательные интервалы, покрывающие samples |
| outcomes | Независимый end_to_end и per_phase для каждой фазы |
| split | partition, scene_id, session_id, episode_id, object_instance_id, group_id, group_by |
| model_input | implementation_id, channels, action_contract_id, output_names, usage, доказательство |

### Профиль без обратного чтения ROSMASTER

Уточнение владельца: заводская обратная связь намеренно отключена из-за
исторического зависания readback. Этот модуль не включает её и не требует
обновления firmware. Отсутствие измеренных суставов, depth, LiDAR, IMU или силы
само по себе не запрещает честный dataset с оценкой команд. Полный набор 14
каналов обязателен как **описание схемы и масок**, а доступность значений зависит
от конкретного pipeline. Неизвестные значения остаются неизвестными.

Для такого источника задаются две дополнительные исходные записи с provenance:

```json
{
  "admission_profile": {
    "id": "rosmaster_command_estimate9_v1",
    "required_channels": ["camera_rgb", "clocks", "preceding_command9"]
  },
  "state_estimate_reference": {
    "reference_id": "<original reference>", "estimator_id": "<original native estimator>",
    "reference_kind": "factory_startup_reference", "state_source": "command_estimate",
    "reference_action9": [0, 0, 0, 0, 0, 0, 0, 0, 0],
    "units": ["deg", "deg", "deg", "deg", "deg", "deg", "m/s", "m/s", "rad/s"],
    "stamp": {"clock_domain": "<original>", "boot_id": "<original>", "monotonic_ns": 0},
    "command_log_contiguous_since_reference": true,
    "reference_receipt_id": "<original reference receipt>"
  }
}
```

Показанные нули — лишь синтаксический пример; их нельзя применять к реальному
роботу без соответствующего исходного reference. Допустимы также
explicit_operator_reference и simulation_initial_state. Reference обязан
совпадать с boot/clock действия, а сама 9D-оценка остаётся явно записанной.
`preceding_command9.value` содержит reference_id, estimator_id,
last_dispatch_receipt_id и state_source=command_estimate. Последняя квитанция
должна принадлежать reference либо предыдущей записанной доставке. Reference не
позволяет синтезировать отсутствующий вектор, продолжить его через reboot или
превратить HTTP acceptance в native dispatch.

Недоступный канал имеет available=false, value=null, mask=false и непустой
unavailable_reason в точной исходной записи. Для частичного суставного вектора
сохраняются null/реальные значения и покомпонентная маска. Неполная обратная связь
не блокирует профиль command_estimate и не становится допущенным measured q.
Запрошенный моделью канал всё равно должен иметь проверенные доступные значения.
Старый полный мультимодальный профиль без admission_profile продолжает требовать
все исходные значения; это не подразумеваемая конфигурация физического ROSMASTER.

Dataset-admission отделён от `capability_admission`: источник RGB+оценка9 может
быть допущен для contact ACT/BC, а полноценная 3D-перцепция или навигация останутся
BLOCKED без необходимых именно им наблюдений. IMU, joint feedback или сила
не назначаются обязательными для всех pipelines. Никаких утверждений о достижении
позы/контакте из оценки команд нет (`measured_attainment_or_contact_claimed=false`).

Каждая запись со свидетельством содержит:

```json
{"provenance": {"source_file": "facts.json", "sha256": "<exact SHA256>",
                "json_pointer": "/samples/0/channels/joint_feedback"}}
```

Указатель JSON Pointer должен разрешаться в **точную исходную запись**: все поля
оболочки без служебных `provenance` должны совпадать. Хеш файла без такого
соответствия значениям не подтверждает произвольно дописанные координаты или
время. Для RGB/depth `value` дополнительно ссылается на отдельные исходные байты
через `source_file` и `sha256`; декодирование и «улучшение» изображения не нужны.
Пример полностью согласованной оболочки создаёт `package()` в тесте; это fixture,
не реальная запись оборудования. Наличие хешей не удостоверяет честность датчика
или оператора: итоговая область проверки явно `source_declarations_not_hardware_attestation`.

Один sample содержит `sample_id`, `action` и `channels`. `action` сохраняет
`values[9]`, `mask[9]`, source_id, command_id, stamp и delivery. Stamp содержит
`clock_domain`, `boot_id`, `monotonic_ns`. Доставка требует отдельной исходной
квитанции с тем же command_id/stamp, receipt_id и соответствующей парой:

| stage/level | observer_kind |
| --- | --- |
| native_dispatch | native_dispatch_callback |
| measured_execution | joint_feedback |
| simulated_dispatch | simulation_step |

`api_accepted`, HTTP200, host_requested, план и `busy=false` не являются такой
квитанцией. Simulation нельзя переименовать в physical. Даже native dispatch не
равен фактически измеренному q: обратная связь записывается отдельно.

## Каналы и модель

Каждый канал задаёт `present`, `available`, `value`, `mask`, `source_id`, `stamp`
и доказательство. Отсутствующие значения не восстанавливаются из команд.

| Канал | Минимальное содержимое value |
| --- | --- |
| camera_rgb | source_file/sha256, camera_id, frame_id, encoding, width/height |
| camera_depth | То же, units=m/mm, aligned_to_rgb_frame_id |
| camera_calibration | calibration_id, RGB/depth camera IDs, intrinsics[9], distortion, depth_intrinsics/depth_distortion, extrinsics[16], depth_to_rgb_extrinsics[16], frame_id, units=m, uncertainty, valid_from_ns/valid_until_ns |
| lidar | ranges, angle_min_rad/angle_increment_rad, frame_id, units=m, state_source |
| odometry_pose | position[3], orientation_xyzw[4], frame_id, units=m, state_source |
| base_velocity | values[3], units=[m/s,m/s,rad/s], state_source |
| imu | acceleration[3], angular_velocity[3], frame_id, units=[m/s^2,rad/s], state_source |
| joint_command | values[6], units=deg; это команда |
| joint_feedback | values[6], units=deg, state_source; это отдельное измерение |
| gripper | aperture, units=m, state_source; неизвестная force остаётся null |
| clocks | clock_domain и boot_id, согласованные с действием |
| detections | detections (включая честный пустой список), model_id, frame_id |
| goal | goal_id, description |
| power_context | voltage, units=V, current, current_available, charging; неизвестное null |
| preceding_command9 | Обязателен для command_estimate9; только явно записанные values[9], names[9]; не сборка из суставов и одометрии |

Измерительные state_source — `measured` для physical и `simulated` для
simulation. Командное эхо и `busy=false` не заменяют измерение. Напряжение не
доказывает зарядку. Нулевая апертура не доказывает удержание предмета.

Отчёт разделяет `present`, `available`, `recorded`, `admitted`, `model_used`.
`presence_known=false` означает отсутствие исходного утверждения об установленном
сенсоре. `mask` и `sample_masks` описывают измерение, а не включение канала в модель.
При проблеме исходов или split валидный RGB не маскируется.

`model_input.usage=declared_plan` задаёт только план. `executed` требует
execution_receipt_id и исходную запись; только тогда каналы отмечаются model_used.
Текущий ограниченный ACT baseline может честно использовать RGB и явно записанную
preceding_command9. LiDAR/IMU/depth при этом остаются записанными и допущенными,
но не использованными моделью. Это всё тот же целый 9D-навык. Каноническому
мультимодальному эпизоду не требуется legacy NPZ с выдуманной prior-command9.

## Причинность, фазы и исходы

Наблюдение должно предшествовать действию и укладываться в исходный протокол
свежести данных. Это проверка dataset, не новый параметр контроллера. Сравнивать
тики разных устройств напрямую запрещено. Для разных доменов нужна ровно одна
исходная `clock_mappings` запись: source/target_domain, source/target_boot_id,
offset_ns, uncertainty_ns, source_valid_from_ns/source_valid_until_ns,
method=measured_sync/simulation_common_clock и доказательство. Весь интервал
неопределённости должен лежать до действия и в допустимом возрасте. Перезагрузка
не склеивается с прежним boot_id; действия одного эпизода строго возрастают в
одном clock/boot. Время приёма HTTP не выдаётся за exposure time камеры.

Фаза задаёт id, start_sample/end_sample (включительно), transition_reason и
recovery_of (null или ID предыдущей фазы). Пересечения, дыры и ссылки на будущую
фазу не допускаются. Для каждой фазы и всего эпизода outcome содержит status
(success/failure/inconclusive), method=operator_observation/independent_sensor/
simulation_oracle, observer_id, independent_of_action_commands=true, непустые
metrics и доказательство. Принятая команда и loss не являются исходом.
Структурно корректный неуспешный эпизод сохраняется с operator_success=false:
допуск исходных данных не превращает его в успешную экспертную BC-демонстрацию.
`admitted_for_dataset` и историческое имя `admitted_for_training` обозначают этот
структурный допуск. Перед оптимизацией отдельно необходим
`optimizer_partition_allowed=true`; для успешной BC-демонстрации дополнительно
`eligible_as_successful_bc_expert=true`. Development/sealed_test всегда имеют
оба разрешения false и сохраняются в исходном разделе. Политика обучения может
вводить более строгие требования покрытия, но не повышать эти разрешения.

Split задаёт partition=train/development/sealed_test и
group_by=[scene_id,session_id,episode_id,object_instance_id]. session/episode должны
совпадать с идентичностью эпизода. `scene_split_verified` означает проверку этой
исходной декларации; **cross_episode_split_checked=false**. Сборщик общего dataset
обязан отдельно запретить пересечение сцен/сессий/объектов между разделами и
зафиксировать закрытый test. Один эпизод не может доказать это за весь корпус.

## Интеграция и границы

`inspect_embodied_episode` возвращает schema, episode_id/run_id, admitted_for_training,
analysis_only, reasons/missing_facts, channels, phases_verified/outcomes_verified,
scene_split_verified, source_integrity, model_input_plan и неизменные исходные
raw_simultaneous_actions. Для UI/сборщика также доступны completed_recording,
source_quality_usable, operator_outcome/operator_success, session_id/scene_id/split,
action9_verified/causal_verified/rgb_verified. Недостаточные реальные записи не
обновляются автоматически и не получают экспертные метки.

`hierarchy_plan(spec,evidence)` описывает внутренние perception/navigation/
contact ACT-or-BC/coordinator одного робота, требуемые/записанные/допущенные/
фактически использованные каналы. Все стадии имеют
`implementation_status=NOT_IMPLEMENTED_BY_THIS_CONTRACT`: этот модуль не
реализует навигацию, fusion или контроллер. EVIDENCE_DECLARED означает достаточность
переданного контракта, а не реализованную и испытанную полную иерархию. В отсутствие
доказательств возвращается BLOCKED. Никакого физического допуска здесь нет.
