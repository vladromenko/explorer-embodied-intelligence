# Запись наблюдений ROSMASTER без изменения контроллера

`shared/explorer_shared/mobile_capture.py`, метод `ENGINEERING_GLUE`, реализует отдельный сбор данных в новом `node-state/mobile-capture`. Он **не импортирует** ROS, Core, ManualArm, старый web или исполнитель; не открывает камеры, UART/servo-bus, не отправляет команды и не меняет старые конфигурации/сервисы. LOW_POWER, unknown arm pose и `observing=false` сохраняются как сведения и не запрещают сбор. Внешнее питание не выдаётся за подтверждённый флаг зарядки: исходное `power.charging=null` остаётся null.

Это первый наблюдательный формат, намеренно отдельный от action-labelled `explorer_mobile_episode_v3`:

- episode: `explorer_mobile_observation_episode_v1`;
- строки: `explorer_mobile_observation_sample_v1`;
- `training_admission.source_quality_usable=false`, `quality.usable=false`, `native_9d_contract_satisfied=false`;
- нет `sample_contract`, `applied_action`, выдуманного reference/home или подставленных нулей суставов;
- исходный `outcome=unknown` остаётся неизвестным. Операторская success/failure — только отдельная отметка; она не превращает наблюдения в данные BC и не доказывает физический навык.

Шесть координат руки в проекции всегда null с false masks: без проверенного контракта arm command/trajectory нельзя восстанавливать их из phase/goal. Полные `arm_command_state`, `arm_state` и исходная телеметрия сохраняются. `status.velocity` — командный `gate.output`; `odom_velocity` — отдельная оценка одометрии. Они записываются в отдельных векторах команд/состояния с числовыми validity masks, названиями осей/единицами и явным происхождением. Даже полный числовой вектор не заявляется измеренным состоянием руки или физически исполненным действием.

## Источники и время

Читаются только существующие пять файлов из явно заданной папки legacy data: `status.json`, `camera-frame.json`, `frame-raw.jpg`, `rgbd-cache-metadata.json`, `rgbd-snapshot.npz`. Raw bytes сохраняются с SHA256, размером и исходными inode/device/mtime. NPZ переносится побайтово; NumPy, GPU и ROS для его декодирования не нужны. Никакая повторная JPEG-кодировка не выполняется.

Оригинальные `status.at`, `status.monotonic`, `boot_id`, ROS header sec/nanosec и camera metadata не исправляются. `collector_wall_unix`/`collector_monotonic_ns` обозначают время чтения коллектора отдельно. JPEG получает `image_stamp` только при совпадении `camera-frame.jpeg_sha256` с прочитанными байтами и числовом исходном stamp. При несовпадении изображение и metadata всё равно сохраняются, `image_stamp=null`, `same_frame_hash_verified=false`; mtime/receive time не подменяют stamp. Для NPZ и соседнего JSON атомарная принадлежность одному RGB-D кадру не доказана: `rgbd_stamp_binding_verified=false`. Проверка hash не доказывает фактическое время экспозиции или синхронизацию часов.

Устаревшие источники сохраняются с исходным временем и возрастом. Отсутствующий/изменяющийся при чтении файл отражается в `source_available`/`source_errors`; остальные доступные источники продолжают сохраняться. Ошибочный JSON остаётся точными raw bytes с parse-error, без подмены значений. Одинаковые bytes дедуплицируются внутри эпизода; повторное использование JPEG в новом телеметрическом наблюдении обозначается `frame_reused=true`. Неизменившийся набор источников не создаёт фиктивных новых кадров.

## Методы для интегратора node API

```python
from explorer_shared.mobile_capture import MobileCaptureRecorder

recorder = MobileCaptureRecorder(legacy_data_dir, node_state_dir)
episode = recorder.start("Наблюдение", observing=False, project_id="02")
recorder.heartbeat(episode["session"], observing=False)
recorder.stage("Наблюдение за объектом")  # Только текстовая аннотация, без действий.
state = recorder.status()  # active / last / recent, UUID32 IDs
finished = recorder.finish("unknown", expected_id=episode["id"])
archive_path = recorder.archive(episode["id"])
receipt = recorder.archive_info(episode["id"])  # path / SHA256 / bytes / source_files_sha256
recorder.close()
```

`get(id)` и `recordings()` возвращают сохранённые descriptors. `start` допускает `duration_s`, `recording_id` (UUID32), `project_id`, ограниченные текстовые object_label/object_class/size_class/destination. `observing` и `stage(text)` — информационные аннотации; stage сохраняется в manifest/history и journal, не выполняет движения. Heartbeat не включает движение и не является энергетическим gate. Метод `finish` сверяет ожидаемый ID, повторное завершение не меняет итоговую оценку. Если большой archive ещё формируется после 20 секунд, возвращается `state=finishing, archive_pending=true`: нужно дождаться завершения через status, а не запускать transfer раньше готовности. Реальные node/API маршруты и deployment подключает интегратор отдельно.

Жизненный `flock` гарантирует одного владельца recorder state; один фоновый writer читает и пишет с заданной частотой, без неограниченной очереди. По умолчанию 5 Hz, максимум 10 Hz; запись до 300 секунд, до 1 GiB сохранённых source+journal bytes. Чтение одного источника ограничено: status 2 MiB, metadata 256 KiB, JPEG 16 MiB, NPZ 64 MiB. Медленный диск уменьшает фактическую частоту, timestamps не выдумываются. Предел объёма/ошибка файловой системы завершает запись как interrupted с unknown outcome; собранные данные остаются. Это ограничения только нового хранилища, настройки питания/движения они не меняют.

Сначала fsync raw source/JPEG, затем journal и его fsync, затем атомарный episode descriptor. Архив tar содержит точные episode/journal/source bytes и `transport.json` с хешами. После реального чтения tar все members сверяются с исходными SHA, затем фиксируются SHA и размер самого tar. Обычные файлы/архивы имеют 600, новые каталоги — 700. Архив после завершения не переписывается; порча обнаруживается при выдаче. Исходные legacy-файлы не меняются и не удаляются.

При старте новый экземпляр под эксклюзивным lock восстанавливает оборванные записи: проверяет полные journal rows по source SHA, сохраняет исходный повреждённый хвост и orphan bytes, помечает interrupted/unknown и формирует архив без аппаратных действий. Сбой после финального episode, но до archive receipt, тоже восстанавливается. Ошибка recovery сохраняется отдельно, не скрывается удалением данных.

Для NX import этот формат означает **completed, analysis-only**: сохранить оригинальный архив/bytes, показать причину запрета BC, не создавать action-labelled dataset manifest. Сохранённое наблюдение LOW_POWER подтверждает работоспособность сбора данных и наличие исходных сведений; оно не доказывает готовность физического исполнения, правильность 9D labels или успех задачи. Обучение по достоверным имеющимся датасетам может выполняться на Homebrain отдельно от ограничений движения контроллера.
