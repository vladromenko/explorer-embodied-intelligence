# Текущее состояние — финальный снимок Homebrain, 13 ноября 2026

**Код, запись, обучение и штатный rollout работают как единый локальный контур;
успешный автономный захват обученной политикой пока не подтверждён.**

- UI/API на `:8020` и MLflow на `:5020` отвечают; на момент снимка запись,
  телеоперация и jobs не активны, устройства свободны.
- Основной датасет `wrist-blue-cube-white-box-v2` содержит 50/50 wrist-only
  эпизодов. Неполные и нулевые попытки исключены из train.
- Финальный session-level split сохранённых моделей: 36 train / 9 validation /
  5 test эпизодов, seed 42.
- Обучены ACT 15k, ACT 30k и SmolVLA 15k. Checkpoints, splits, lineage и MLflow
  artifacts сохранены локально на Homebrain.
- Исправлены writer/fsync/PNG throughput, STOP→quarantine, продолжение датасета,
  auto-SAM lease, wrist-only UI, LeRobot integer FPS/plugin discovery и
  train↔rollout centered-letterbox mismatch.
- Официальный ACT rollout физически запускался и штатно завершался примерно за
  13,2 секунды, но пользователь наблюдал промахи/отсутствие захвата. Exit code 0
  не считается доказательством выполнения задачи.
- Текущая ветка содержит diagnostic rollout trace, синхронизацию со стартовой
  позой train и исправление preprocessor старых ACT checkpoints.
- Raw datasets, модели, логи и runtime не входят в Git и остаются на Homebrain.

Полное описание реализации, истории проблем и ограничений:
`docs/HOMEBRAIN_FINAL_STATE_RU.md`.

## 2026-11-13 — запущен final fit на всех 50 эпизодах

- Добавлен явный режим `validation=0`, `test=0`: все 50 эпизодов и 18 960
  пригодных кадров входят в train. Этот режим не выдаёт train loss за
  независимую оценку и показывает предупреждение об отсутствии holdout.
- Пространственная/фотометрическая image augmentation применяется и к ACT, и к
  SmolVLA только в train; raw-кадры не изменяются.
- ACT 30 000 steps запущен job `aed3d3bcab144b7482693cc7167a9c45` с
  `job_name=act_wrist_all50_aug_seed42`.
- SmolVLA 15 000 steps поставлен следом job
  `118a1aee7f6242b9aebbb0a68e9db34b`; он ждёт освобождения общего GPU lease и
  не обучается параллельно с ACT.
- Обе конфигурации прошли полную read-only проверку датасета и split hash
  `42cfef89a665e2cf634fe41f514ff8879a2f0eb72a9e162c79d4c84bfadaf234`.

## 2026-11-13 — исправлен профиль ACT после неудачного физического rollout

- Rollout `78e3a1afb2554932adb36fa6692f70f0` видел синий кубик во всех 235
  диагностических кадрах, а стартовые суставы совпадали с train-распределением.
  Ошибка была не в потере камеры: gripper оставался в диапазоне 25,5–26,5 и не
  дошёл до закрытия; 23 интервала управления превысили 100 мс.
- Причиной был сохранённый экспериментальный профиль `n_action_steps=1`,
  `temporal_ensemble_coeff=-0.5`: модель пересчитывала 50-шаговый план после
  каждого первого действия, цикл периодически падал примерно до 5,5 Гц и
  стабилизировался до фазы захвата.
- Финальный ACT checkpoint переведён без изменения весов на штатное chunk
  execution LeRobot: `n_action_steps=50`, temporal ensemble выключен. Integrity
  manifest пересчитан, прежний config сохранён в `pretrained_model.orig`.
- Код и UI больше не навязывают экспериментальный one-step профиль новым ACT
  checkpoints. Физический повтор после исправления ещё не выполнялся.

---

## Предыдущая приёмка 7 ноября 2026

**READY_FOR_OPERATOR_CONTROL_EPISODE.** Обновление v13 установлено, программные и дополнительные повторные прогоны завершены. Это готовность к1 контрольному операторскому эпизоду, не обещание исправности всей аппаратуры или качества захвата. Обновлено 2026-11-07T02:52:13.236120+03:00.

- UI: http://192.168.0.168:8020 · http://100.95.83.3:8020 . MLflow: те же адреса на5020. Основной UI PID920397; при установке запись/телеоперация/политика не были активны. Автоматического старта аппаратуры нет.
- Реальный прошлый обрыв: переполнение медленного последовательного PNG writer; оба пользовательских неполных эпизода сохранены. Новый lossless PNG0 writer пишет два ракурса параллельно; reader камер общий для preview/recording.
-15s подготовки, большая сцена, Пробел/R/Esc, автоматический следующий эпизод, просмотр архива. MLflow работает во время записи, доставка отдельным процессом со spool/retry; ссылка видна и в большой сцене.
- SAM3 GPU во время записи: настоящие редкие снимки точного сохранённого кадра с возрастом. Полная разметка автоматически после завершения сессии. Это не segmentation30FPS; missing/stale sidecar блокирует object-mode обучение.
-120s file replay:3572 кадра, max queue29; повтор60s вместе с SAM3 GPU:1796 кадров, max queue1,3 SAM-снимка. Оба экспорта открыты официальным LeRobot0.6.1 с точными RGB/actions/state/task/ns timestamps.
- Повторные6 сессий/3 re-record/8 гонок Start-STOP PASS; реальные MLflow метрики прочитаны при RUNNING. Outage→spool→restore и crash→quarantine/FAILED повторно PASS. Browser disconnect не останавливает запись.
-139 Python tests +16 DOM tests PASS; Chromium/WebKit: запись synthetic workflow и основной UI с реальным архивным RGB playback/LAN/Tailscale/reconnect PASS. pip check чист. Тесты аппаратуры не открывали и torque не включали.
- Старый pretrained SmolVLA GPU и full ACT CPU update/save/reload остаются подтверждены предыдущими техническими прогонами. Новых аппаратных успешных демонстраций и обученной политики захвата не заявляем.
- Конкретно осталось: операторское следование всех6 суставов и1 новая запись со стабильными USB/камерами/STOP; настоящий Safari на Mac; затем обучение на новых полных эпизодах и отдельная физическая оценка политики. Top/wrist geometry по-прежнему unknown без актуальной проверки.

Инструкция: RECORDING_GUIDE.md. Разбор upstream ошибок и меры: RECORDING_RELIABILITY_REVIEW.md. Полные доказательства: validation/recording-fix-20261107/. Текущий progress: runtime/progress.json.

---

## История до исправления записи (прежние статусы ниже не заменяют текущую приёмку)

# Текущее состояние — финальная приёмка 6–7 ноября 2026

**READY_FOR_OPERATOR_TELEOPERATION_CHECK.** Это частичная инженерная приёмка с явно оставшейся операторской проверкой, не FULLY VERIFIED.

- UI: http://192.168.0.168:8020 · http://100.95.83.3:8020 . MLflow: те же адреса на5020.
- По разрешению пользователя освобождены ресурсы трёх соседних служб; их конфигурации не менялись. Камеры и обе руки проверены read-only; показан настоящий preview без servo bus/torque.
- Оба видеопотока одновременно работают: topMJPG29.4FPS / wristYUYV30.8FPS, skew23ms. Live read-only calibration обеих рук совпала со снимками. Follower уже имел torque1, инженерные тесты его не меняли.
- Настоящий SAM3 CUDA forward PASS; full pretrained SmolVLA CUDA update+новый процесс reload PASS; full ACT CPU update+новый процесс reload PASS. Технические checkpoint запрещены для физического inference.
- 128 Python tests PASS,14 DOM tests PASS; настоящие Chromium141/WebKit26 — по12 сценариев PASS. Официальный LeRobot0.6.1 loader открывает экспорт; HTTP finalize/validation/export, MLflow spool/retry и реальные server-image/WS проверены.
- Добавлены Preflight, finalize/LeRobot export jobs, raw playback, output directory; исправлены STOP до writer drain, частичный Torque_Enable failure, wristformat, реальные SAM bbox/ambiguity/color/provenance.
- Отдельная кнопка «Включить телеоперацию» проверяет руки без датасета; STOP отменяет и ожидающую передачу к записи. Модель можно проверить на архивном кадре без USB с измерением SAM → policy latency.
- Исправлен реальный отказ Start: сохранённые калибровки совпадают с регистрами обеих рук, но расслабленный shoulder_lift follower читался830 при нижней границе834. Введён фиксированный feedback-допуск8тиков с bounded hold внутри прежнего диапазона; калибровки не менялись. Большие отклонения блокируются. После исправления движение ещё не запускалось оператором.
- После операторского сообщения о медленном/неодинаковом движении заменён постоянный limiter: плавная начальная синхронизация в пространстве команд, затем штатное прямое следование LeRobot. Все6 суставов в обоих направлениях дают те же raw goals, что SO101Leader→SO101Follower; калибровки байт-в-байт из сохранённого LeLab. Отдельная телеоперация теперь bus-only60Hz и не зависит от камер; новые физические движения разработчик не запускал. Операторский повтор обновлённого режима остаётся обязательным.
- **Остаток:** Safari на Mac, физическая телеоперация/направления/gripper/STOP с оператором и первый настоящий датасет; live SAM+VLA latency/качество. Никаких вымышленных успешных демонстраций. Геометрия top/wrist по-прежнему unknown без актуальной проверки.

Основной отчёт: ACCEPTANCE_REPORT.md. Пошагово: RECORDING_GUIDE.md. Тесты: TEST_RESULTS.md. Доказательства: validation/acceptance-20261106. Сначала нужна отдельная проверка рук кнопкой UI, затем контрольный эпизод.

Обновлено 2026-11-07T01:25:44.379509+03:00 .

---

## История предыдущих этапов (старые статусы ниже не заменяют текущую приёмку)

# SO-101 — обучение SmolVLA + SAM 3 + MLflow

2026-11-03, homebrain. Программный этап реализован; новая физическая политика ещё не обучена и захват не проверен.


## Обновление: параметры обучения и ACT (2026-11-03)

Этап3 UI расширен: выбор SmolVLA/ACT, имя запуска, batch/steps/LR/Adam/AdamW/weight decay/clip/scheduler/warmup/AMP/gradient accumulation/DataLoader workers, интервалы метрик/validation/checkpoints, image/chunk, ACT KL/backbone LR, Smol denoising, augmentation, target/color, resume. Форма хранит настройки локально; сервер сохраняет реальные параметры в job/lineage/MLflow.

Train/validation/test — автоматические доли или ручное назначение включённых эпизодов, разделение сессиями/эпизодами, seed, просмотр фактического состава перед очередью. Validation оценивается целиком, test только в конце; stats и аугментации используют train. Исключённый эпизод без SAM не блокирует обучение на выбранных размеченных эпизодах. Неверные параметры/пересечение выборок/несовместимый тип Resume возвращают ошибку до worker/model load. Запись и train одного датасета защищены общей lease; вторую копию активного train через Resume запустить нельзя.

ACT — настоящий установленный LeRobot ACT, offline ResNet18 с нуля, без language input. Поддерживается тот же SAM state adapter и отдельный RGB+state baseline. Политика checkpoint загружается по config.type, preprocessing соответствует train. Общий детерминированный ACT letterbox. SmolVLA сохраняет cached pretrained и свой preprocessing. Checkpoint содержит GradScaler и полный training state; файлы fsync перед atomic finalize.

Результаты: полный прогон 56 passed за51.59s (`logs/controls-release-suite.log`); после дополнительной проверки Resume — 17 passed/2 deselected в целевом preflight/API наборе (`logs/preflight-release-check.log`). Реальный ACT tiny CPU forward/backward loss21.15685, terminal test loss6.28071, state projection update0.00014894, save/reload/resume2→3. Технический checkpoint `models/technical_act_controls_20261103/checkpoints/step_00000002` явно запрещён для физического inference. SmolVLA tiny regression тоже проходит. Ни GPU train, ни настоящее SAM, ни аппаратные движения этими тестами не доказываются.

HTTP: новая форма/catalog; неверные ACT/SmolVLA параметры422, synthetic production train422, соответствие DOM IDs проверено статически. Повторный реальный HTTP/WebSocket recorder smoke с End/Re-record/Next/Stop, MLflow artifact reload пройден; аварийный restart только собственного UI с synthetic записью — quarantine и без auto-resume. Browser inventory пуст, визуальный Safari тест по-прежнему остаётся. Документация: `docs/TRAINING_CONTROLS.md`, отчёты `validation/training-controls*.json`.

## SO-101 Studio: исправление интерфейса и предпросмотра

Обновлено 2026-11-03T21:06:42+03:00. Старый экран со служебным JSON заменён отдельными разделами: Запись / Датасеты / Обучение / Политика / Настройки. Основной экран светлый, камеры в центре, короткая форма новой записи рядом, понятные counters/elapsed/state и STOP. На узких окнах Mac боковая навигация компактная, форма перемещается под камеры. JSON оставлен только в закрытой диагностике.

Причина пользовательского скриншота: старый select автоматически выбирал первый (мой технический) датасет, поэтому Start запускал synthetic_test с нарисованным кубиком. Исправлено: обычный выбор начинается с «Новый датасет», technical/synthetic данные и checkpoints скрыты; явное включение возможно только в настройках разработчика. Ничего не удалено; мой hardware preflight помечен purpose=technical_test. Новая запись по умолчанию только hardware.

Добавлен отдельный явный camera-only preview: кнопка «Подключить камеры» открывает только камеры, не servo bus и не torque. До неё карточки сразу показывают доступность/занятость. Общий hardware lease с записью/inference; перед началом аппаратной записи собственный preview закрывается. В паузе между эпизодами показывается текущий кадр камеры, а не последняя сохранённая картинка. Неактивная сессия больше не отдаёт старый synthetic frame через live endpoint. При потере камеры видны placeholder и переподключение. Зависший reader не освобождает lease для параллельного открытия.

Проверено: 8 DOM-сценариев (linkedom; не настоящий браузер), 26 Python/API/recording tests. Начальный выбор, скрытие тестов, busy state до Start, отсутствие auto acquisition, отдельный preview без record/start, count51, разделы, ACT submission/выборки, stale synthetic refusal, partial close/locks и live кадр в inter-episode pause. Все тесты camera acquisition используют fixtures. JS/compileall/diff checks пройдены. Сервис обновлён и отвечает; на startup камеры/моторы не открывались. Отчёт validation/ui-redesign.json.

Обе настоящие камеры всё ещё заняты процессом параллельного эксперимента. Мы не перехватываем их и не останавливаем владельца. Визуальная проверка реального Safari недоступна: browser/app inventory пуст. Пользователю открыть тот же URL и обновить страницу ⌘R. Физический camera preview и запись остаются явным действием оператора после освобождения устройств.

## Открыть

- UI LAN http://192.168.0.168:8020 · Tailscale http://100.95.83.3:8020
- MLflow http://192.168.0.168:5020 · http://100.95.83.3:5020
- Инструкция одного контрольного эпизода: README.md. Собственные systemd службы включены, работают без браузера, старт не открывает аппаратуру.

## Реализовано

Самостоятельные git/.venv/config/data/models/SQLite/logs/порты/службы. Нет runtime-imports из чужих проектов. Собственные снимки servo calibration с hash/provenance, live readback перед запуском. Геометрия камер и wrist hand-eye не объявлены валидными.

Двухкамерная raw-запись: ограниченная очередь PNG writer, монотонные timestamps состояния/камер/отправки, реальные квантованные targets, временная причинность, feedback validity, End/Next/Re-record/Stop, добавление, quarantine/atomic finalize/recovery. Явный hardware Start, владельцы устройств/locks/limits/no-motion-retry. UI серверные камеры, HTTP + reconnect WebSocket; mediaDevices не используется.

Официальный SAM3 собственной pinned копией, отложенный/resumable worker, точные PNG masks/frame/hash/time, score/2D/heuristic tracking confidence/loss, отдельный HSV color; invalid/unknown геометрия без применимой калибровки. SAM не входит в цикл записи.

SmolVLA реальная модификация входа: joints6 + 26 SAM/validity/target полей внутри stock padded32 state projection. ID/будущие actions/success/done не входят в observation. Двухкамерный основной режим + baseline RGB/state, экспериментальный wrist-only. Целые эпизоды/сессии train/validation, train-only normalization; полный checkpoint+processors/weights/config/backbone assets/optimizer/scheduler/RNG; strict reload, resume, inference тот же контракт, без deterministic fallback.

MLflow SQLite/artifacts, реальные loop loss/LR/grad/throughput/resources, hashes/lineage, буфер доставки + timer retry. Периодически только checkpoint manifest; полная конечная папка один раз. Server-side subprocess jobs с progress/stop/resume; чужие ресурсы не прерываются. Физические движения никогда автоматически не resume.

## Проверки

- Финальный полный набор: 35 passed за21.28s (logs/final-tests.log); отдельная проверка запрета inference checkpoint с0 updates пройдена.
- 17 recorder/hardware tests, 11 SAM/geometry tests, 3 resource tests, 4 real tiny-SmolVLA tests.
- Реальные HTTP+WebSocket: 51 сохраняется как 51, disconnect не останавливает запись, End/Re-record/Next/Stop, просмотр PNG; MLflow params/metric/artifact download+reload.
- GPU job честно waiting_resources, Stop завершает очередь; аппаратный HTTP Start отказал busy до открытия; MLflow outage→spool→replay.
- Принудительный SIGKILL только собственного UI во время synthetic recording: systemd restart, incomplete в quarantine, без auto-resume движений.
- Настоящий уменьшенный float32 CPU SmolVLA forward/backward/update, loss=2.33052754, state_proj update=0.00015000999, checkpoint save/reload/перенос, одинаковый inference, влияние SAM-признаков на реальный forward loss, resume2→3.
- Это тестовая random tiny архитектура и synthetic annotation fixture. Не настоящий SAM forward, не pretrained GPU train, не обученный успешный захват.
- pip check чист; JS syntax/compileall пройдены. LAN/Tailscale HTTP endpoints отвечают локально с Jetson; внешний Mac/Safari недоступен для визуальной проверки (browser inventory пуст).

## Ресурсы и оставшиеся обязательные проверки

Финальный audit validation/resource-audit-final.json: MemAvailable около3.5GB, чужой сервер около8GB, обе камеры заняты. LeLab тоже держит CUDA context. Тяжёлые задачи не запускались. Память Jetson общая CPU/GPU. Нужно освобождение конфликтующих ресурсов владельцем, лаборатория их не убивает.

1. Оператор: реальная новая запись 1 эпизода, обе камеры/controls/frequency/timestamps/STOP/torque и актуальность моторной калибровки. Не считать тестовый источник аппаратной проверкой.
2. После освобождения RAM/GPU: SAM3 реальный forward на новых raw кадрах; NumPy2 compatibility upstream metadata patch проверить настоящим forward. Raw не теряется при ожидании.
3. После минимум двух новых пригодных эпизодов: полный cached pretrained SmolVLA короткий GPU train/save/reload, затем реальное обучение. Совместное SAM+VLA inference требует памяти; guard может отказать, нельзя обещать вместимость заранее.
4. Проверка с Mac Safari и VPN; отклик реального UI и переподключение.
5. Физический inference только явным Start, оператор/STOP/latency, независимые испытания качества. Новые camera extrinsics/top/wrist geometry по-прежнему unvalidated. Успех после N демонстраций не обещается.

Продолжение в этом чате: сначала прочитать STATUS.md и validation/*.json; не переустанавливать чужие среды и не запускать чужие scripts. Все незавершённые пункты выше требуют свободной аппаратуры/ресурсов или Mac.

## Аудит 7 ноября 2026: обучение на Jetson 16 ГБ

Исправлено: (1) fp16 AMP падал на первом нефинитном градиенте — теперь GradScaler штатно пропускает шаг, по умолчанию bf16; (2) загрузка checkpoint в MLflow обрывалась по read timeout 3 с и повторялась каждую минуту с нуля — таймаут масштабируется по размеру артефакта; (3) /ws считал status() в event loop для каждой вкладки — теперь пул потоков и общий кэш 1.5 с.
Ускорено: TF32 для fp32, DataLoader workers в форме по умолчанию 2, промежуточная validation по детерминированной подвыборке (итоговая — по всем кадрам), кэш SHA-256 raw PNG (cache/hash-verified/, датасет не меняется), метрики MLflow пакетами.
Добавлено: keep_last_checkpoints, оценка диска до очереди, amp_dtype, eval_max_samples. Подробности и ограничения: docs/TRAINING_CONTROLS.md. Тесты: tests/test_training_efficiency.py.
Не менялось: запись, hardware, SAM, safety-логика, формат raw (PNG без сжатия ~55 МБ/с, ~3.3 ГБ на минуту записи).

## Аппаратная wrist-only запись 8 ноября 2026

Пользователь записал первый настоящий wrist-only эпизод `ep_20261108T071225_197529ebdc` в датасет `ds_045fe3a18fd84445`: 632 кадра за 21.1218 с, 632 lossless wrist PNG, ни одного top-кадра, 0 отсутствующих файлов, camera contract `wrist` соблюдён во всех строках, SHA-256 manifest совпадает. Writer peak queue 1, 3 повторённых и 3 пропущенных camera frame, максимальный возраст 34.2 мс. Live readback обеих servo calibration подтверждён, остановка закрыла устройства без ошибок.

MLflow run `f780843b0d5e4e10bc329fcfdbe24eef` завершён FINISHED и содержит 632 финальных кадра, 1 complete episode, 583729104 байт, writer queue peak 1 и wrist FPS около 29.67. Для этого уже завершённого run добавлены точные параметры `camera_mode=wrist_only`, `cameras=wrist` и `image_encoding_actual`; будущая telemetry пишет выбранные камеры сразу.

Полная post-recording SAM3-разметка `6c78f15e46ec4a118dba0bc4b434b52b` завершена: 632/632 wrist-кадра, backend `sam3`, 986 наблюдений объектов (153 cube, 833 box), ошибок нет; raw не менялся. UI в wrist-only скрывает неиспользуемую top-карточку в live и playback и не опрашивает её frame endpoint. Исправлена tablet-layout формы и поднята версия статических ресурсов до v18.

Целевое число записей теперь хранится в metadata датасета. Повторное открытие показывает глобальный прогресс (`1 / 50`) и новая сессия получает количество уже готовых эпизодов плюс только оставшийся объём. Текущий датасет закреплён на цели 50. Проверки: 19 UI passed; 20 recording passed; ранее 47 recording/hardware/export passed.

После продолжения сбора обнаружена и устранена блокировка: полная auto-SAM-разметка запускалась после каждой частичной сессии и держала dataset lease, поэтому нельзя было сразу продолжить запись. Теперь auto-SAM запускается только при достижении сохранённой общей цели (для текущего датасета 50/50); ручной запуск разметки остаётся доступен. Промежуточная задача остановлена штатно на 926/2681 кадрах, маски сохранены и будут переиспользованы. Текущий raw: 6/50 эпизодов, 2681 wrist-only кадров, read-only validation valid. Проверки после исправления: 19 UI + 49 Python passed.
## 2026-11-12 — устранён отказ записи при прямой телеоперации

- Физический сеанс `session_4b730e903a4f4d75` остановился не из-за USB или моторов: очередь raw writer переполнилась во время синхронного `fsync` manifest в активном 30 FPS контуре.
- После первого исправления успешно записались 11 эпизодов подряд, затем несжатый поток (~26 MB/s, 6.5 GB накопленных данных) вызвал длительный kernel writeback и повторное переполнение.
- Принудительная синхронизация manifest удалена из активного контура; полная синхронизация файлов и каталогов остаётся при завершении эпизода.
- Wrist PNG теперь сохраняется с lossless compression level 1: измерено ~10 MB/s вместо ~26 MB/s, декодированное RGB остаётся байт-в-байт идентичным.
- Буфер lossless wrist-кадров увеличен до 600 кадров, то есть вмещает целиком текущий 18-секундный эпизод при 30 FPS. Прямое calibrated leader→follower повторение не изменено.
- Неудачный эпизод сохранён в quarantine, один завершённый эпизод датасета сохранён.
- Проверено без открытия аппаратуры: 63 Python-теста и 19 UI-тестов прошли.

## 2026-11-12 — STOP больше не сохраняет незавершённый эпизод

- Эпизод 41 был создан при `STOP` как `complete`, хотя содержал только 3.64 секунды и нулевое движение. Он исключён из train и перемещён в quarantine; активный датасет снова содержит 40 полных эпизодов.
- Красный `STOP` теперь отправляет `abort`: команды руке прекращаются, незавершённый raw сохраняется только в quarantine. Добавление в train выполняет отдельная кнопка «Сохранить эпизод».
- Raw последних 20 полных эпизодов проверен: медианный период 33.41 ms, p95 33.62 ms, максимальный возраст wrist-кадра 36.52 ms. Browser preview увеличен с 10 до 15 FPS без изменения lossless raw 30 FPS.
- Проверено без открытия аппаратуры: 65 Python-тестов и 19 UI-тестов прошли.

## 2026-11-12 — визуальная проверка первых 40 эпизодов

- Просмотрены старт, подход, захват, перенос, укладка и возврат всех записей wrist-only.
- Эпизод `ep_20261112T013304_2fc8ae05d7` (№29, 11 кадров / 0.35 s, без движения) исключён из train и помещён в quarantine.
- В датасете осталось 39 полных эпизодов. Основной недостаток выборки — избыток стартов у нижнего/бокового края и более быстрые движения в поздних сессиях; захват, появление коробки, отпускание и возврат в остальных эпизодах визуально согласованы.
