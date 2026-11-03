# SO-101 — обучение SmolVLA + SAM 3 + MLflow

2026-11-03, homebrain. Программный этап реализован; новая физическая политика ещё не обучена и захват не проверен.

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
