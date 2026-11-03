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
