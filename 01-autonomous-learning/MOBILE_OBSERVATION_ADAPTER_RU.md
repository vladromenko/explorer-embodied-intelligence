# Из наблюдений LOW_POWER в проверяемые командные данные

`src/explorer_sim/mobile_observation_adapter.py`, `ENGINEERING_GLUE`, читает завершённый `explorer_mobile_observation_episode_v1`. Исходники остаются неизменными. Преобразователь не управляет роботами, не импортирует старый web/Core/ManualArm и не создаёт поддельный исходник `explorer_mobile_episode_v3`.

```python
from explorer_sim.mobile_observation_adapter import convert_mobile_observation

report = convert_mobile_observation(episode_json, new_dataset_directory)
# report.dataset_manifest — путь, только если найдены достоверные причинные пары.
# Иначе normal completed analysis-only result с точными причинами.
```

Результат `explorer_mobile_observation_import_v1` содержит `dataset_available`, `dataset_manifest` или null, `analysis_only`, `training_admission`, причины исключения строк/пар, SHA исходников и выходных файлов. `source_originals/` сохраняет точные исходные bytes, в том числе JSONL, JPEG, RGB-D NPZ, неизвестные значения и оборванный хвост журнала. NPZ источника не декодируется; RGB берётся из JPEG, связанного с оригинальной camera metadata по SHA. Копии проверяются, исходное дерево перечитывается после преобразования. Повторный вызов разрешён только при том же fingerprint, FPS, split и implementation SHA; существующие результаты не перезаписываются.

Текущий реальный `UNKNOWN_AFTER_REBOOT` возвращает **analysis-only** без BC manifest. Это нормальный результат сохранения наблюдений, а не ошибка записи. Неизвестные суставы не заполняются нулями и не восстанавливаются из будущих команд. Сырые projection-поля recorder, где рука null, также не подменяются: производные координаты существуют только в отдельном новом датасете при доказанной истории команд.

## Условия квалификации строки

Сначала проверяются сохранённые raw status/camera/JPEG, их размеры/SHA и точное совпадение parsed telemetry с raw bytes. Для этой версии известен только clock producer `onboard_ros_cpu_cache_v1` с оригинальным ROS header. `camera-frame.jpeg_sha256` должен совпадать с JPEG. Иначе исходник сохраняется с причиной, а строка исключается.

Состояние должно быть MANUAL. Принимается только положительно указанный `command_source=operator` или `coordinated_operator` из проверенного ManualArm producer. Политика, automatic, mission, отсутствующий/неизвестный источник не переименовываются в показ оператора, включая фазу удержания после выполнения политики. Host boot истории руки должен совпадать с boot status. Допустимые фазы: `command_in_progress`, `command_elapsed_observation_required`; `measured=false` обязателен. Native CommandOnly `arm_state/estimated_only/reference_valid` с другой системой session/MCU-time пока не смешивается с этим контрактом: без полного положительного manual provenance он остаётся для анализа.

История ManualArm теряет достоверность после более нового `arm-telemetry-fault.json`. Поэтому recorder должен дополнительно сохранить этот необязательный файл **либо факт FileNotFoundError при чтении**, отражённый в source_available/source_errors. Новый fault позже `arm.at`, ошибка чтения или отсутствие каких-либо сведений об этом файле исключают строку. Старые пятифайловые наблюдения при неизвестной fault history остаются полноценными raw records для анализа. Добавление источника fault выполняет интегратор в новом recorder; старый контроллер и его файлы не меняются.

Входная поза берётся из **уже сообщённого** `q_estimated_deg`; для завершённой фазы без него допустим только явно известный endpoint `servo_deg` как elapsed command estimate. При движении endpoint не подменяет отсутствующую текущую оценку. Числовые координаты проверяются в прежних единицах/диапазонах: шесть degree-команд плюс vx/vy/wz в m/s,m/s,rad/s. `status.velocity` — командный gate.output, а не `odom_velocity`; одометрия не превращается в action label.

Относительно collector time сохраняются исходные прежние bounds: status младше 1 с, камера младше 2 с, расхождение camera/status не более 1.5 с. Исходные stamps не сдвигаются. `LOW_POWER`, charging, voltage и observing не входят в admission данных. Проверка данных не меняет ни одного моторного/энергетического ограничения.

## Причинность и смысл действий

Наблюдение состоит из **текущего** JPEG и текущей сообщённой командной оценки руки/шасси. Label берётся только из **следующей соседней квалифицированной** строки, чей исходный status.at строго позже всех frame/status/collector times текущей строки. Будущие endpoint/goal не используются как текущие state/features. Через неизвестную, policy или некаузальную строку пара не строится; такие островки разделяются на эпизоды. Временной пропуск свыше трёх номинальных периодов тоже разделяет эпизоды. Сетка использует только прошлую пару, без будущего интерполирования. Unix-clock reversal, смена host/controller boot или несоответствие raw checksums блокируют повышение всего эпизода; часы не «ремонтируются» и не переименовываются в monotonic.

Выходной `explorer_robot_episode_v1` содержит реальные RGB/NPZ, state/action в native и normalized units, исходные frame/state/label timestamps, source row indices и arm timestamp. Общие `dataset_export.load_episode/validate_splits` реально проверяются до завершения. Затем интегратор может вызвать официальный `export_lerobot_v3`; тест включает настоящий LeRobot 0.6.1 writer/finalize/reader, сохранность изображений, чисел, границ и source SHA.

**Точная граница provenance:** ManualArm пишет `command_sent_at` и endpoint cache **до** `self.pub.publish`. Поэтому `action_basis=host_reported_target_snapshot`, `dispatch_confirmed=false`. Это host target intent, а не доказательство отправки, приёма MCU или физического применения. Сохранённое имя массива `arm_command_sent_wall_unix_ns` повторяет исходное имя, но его basis — host prepublication record time; fallback `arm.at` также обозначен. Время публикации body velocity недоступно. `state.at` будущей строки — момент снимка host target, не новое 9D hardware action. Получаемые command-estimate BC данные пригодны для офлайн исследования намерений оператора в этих пределах; они не заявляют measured pose, attainment или динамику физического исполнения. Clock alignment и фактическая экспозиция остаются непроверенными.

Порог полезности BC сохраняет статистические условия прежнего recorder: не менее 12 квалифицированных наблюдений и 5 секунд, не менее пяти различных команд, camera rate не ниже 1.5 Hz и разрыв не более 1.5 с. Слишком короткий или однообразный, но причинно правильный набор может иметь NPZ для анализа, при `source_quality_usable=false`. Для BC дополнительно нужны явная операторская success-метка и train split. Failed/unknown, development и sealed данные сохраняются для анализа/других офлайн задач; sealed split нельзя переименовать в train. Никакая метка не доказывает успешный физический навык.

## Прочитанные источники

Аудированы локальные копии исходного sampler/core/ManualArm/VelocityGenerator. Их SHA включены в derived provenance: sampler `bc792ed1…`, core `a052ce93…`, ManualArm `8ddadb77…`, arm_velocity `ee5300c7…`. Исходники с аппаратными конструкторами не исполнялись. SHA текстового аудита не означает независимое подтверждение активного бинарного producer на устройстве: `producer_runtime_binary_identity_verified=false`. Эта версия не зависит от архивного mobile_policy_contract с другим SHA и не выдаёт его за актуальный контракт.
