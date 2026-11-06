# Финальная инженерная приёмка SO-101 Studio — 6–7 ноября 2026

Статус: **READY_FOR_FIRST_OPERATOR_EPISODE**, не FULLY VERIFIED. Настоящие камеры, read-only serial, SAM GPU и полный pretrained SmolVLA GPU проверены. Реальная телеоперация/запись с torque и Safari ещё требуют оператора. Технические checkpoints не разрешены для управления роботом.

Работа выполнена на `homebrain`, `/home/jetsonnx/Projects/so101_ai_learning`, поверх milestone `acceptance-baseline-20261106`. Новая просьба пользователя «порты можно освобождать, соседняя работа приостановлена» разрешила остановить владельцев ресурсов: user `so101-wristlab-web`, system `so101-pick-place` и `lelab`. Их файлы/калибровки/enablement не изменялись. Эти службы оставлены остановленными, чтобы не отобрать устройства у записи. VPN/авторизация/remote Codex не изменялись.

## Фактические результаты

| Компонент | Реализация | Фактическая проверка | Результат | Ограничения |
|---|---|---|---|---|
| UI | Studio: запись/библиотека/обучение/политика, Preflight, playback | Реальные HTTP/WS по LAN и Tailnet + DOM-сценарии | PASS API/DOM | Реальная отрисовка в браузере не проверена: inventory пуст |
| Safari | Серверные JPEG/PNG, без mediaDevices; WS по текущему host | Только проверка кода/API | NOT TESTED | Нужен checklist из RECORDING_GUIDE.md; DOM не считается Safari |
| Top Camera | C920, MJPG 640×480 30 | Одновременно с wrist 5s, реальный JPEG через HTTP | PASS capture | ~29,4 FPS; движения/свет/длительная запись не проверены |
| Wrist Camera | HD USB, YUYV 640×480 30 | Одновременно с top 5s, HTTP stop/reconnect | PASS capture | ~30,8 FPS; исправлен ошибочный запрос MJPG30 при поддержке MJPG120 |
| Leader/Follower | Независимый calibration snapshot, пределы, единственный send | Реальные 6 моторов каждой руки: ping, положения, mode, calibration, torque readback | PASS READ ONLY | Direction/следование/torque writes НЕ проверены; follower Torque_Enable прочитан1 и не менялся |
| Recording | Серверная запись, End/Next/Re-record/Stop, bounded writer, quarantine | HTTP/WS synthetic multi-episode + disconnect/restart + SDK/fault fixtures | PASS software | NOT TESTED реальная телеоперационная запись. Нет вымышленных успешных демонстраций |
| Dataset LeRobot | Собственный raw + экспорт LeRobot0.6.1 v3.0, image-backed Parquet | Официальный loader открыл экспорт, сравнил обе картинки/позиции/actions/tasks/timestamps; после перемещения raw тоже | PASS technical export | Аппаратный датасет должен быть записан оператором; номинальный FPS отдельно от измеренных timestamps |
| SAM 3 | Собственный pinned source, официальный cache, PNG masks/causal association/HSV | Настоящий CUDA forward на двух настоящих снимках | PASS GPU forward | Top3cube+2box; wrist одна область распознана иcube иbox → обе ambiguous. Temporal tracking/occlusion quality NOT TESTED |
| ACT | Реальная LeRobot ACT, ResNet18 с нуля, full512/4enc/4VAE | CPU forward/backward/AdamW/save, новый процесс reload/inference | PASS full CPU engineering | 51 573 638 параметров; GPU ACT NOT TESTED; не политика захвата |
| SmolVLA | Полный cached pretrained, 450 046 176 параметров | Настоящий CUDA update, полный save, CUDA reload в новом процессе | PASS pretrained GPU engineering | 99 880 992 trainable; техническая несинхронная композиция входов и условная loss-цель, не демонстрация |
| SAM Adapter | 6 joints+24object/validity+2target, tracking ID исключён | Fixed-input perturbation меняет вычисление; actualSAM slots проверены | PASS numerical contract | По одному выбранному объекту cube/box на ракурс; ambiguous исключены; bbox клиппируется к изображению только для признаков |
| MLflow | Собственный SQLite/artifacts, fsync spool/process lock/replay | Настоящие ACT/Smol GPU/CPU runs FINISHED, metric/param readback, artifact download, outage/recover | PASS | Нет публикации HF/W&B; full checkpoints хранятся локально, периодические ссылки малы |
| Checkpoint | Weights/config/processors/adapter/lineage/optimizer/scheduler/RNG/scaler/integrity | Обе full-модели загружены новым процессом; processor tamper отклонён; resume CPU регрессии | PASS software | Только technical models до новых демонстраций; компонентам legacy без integrity нельзя доверять как production |
| Inference | Только обученная модель, fresh obs, нет deterministic fallback, guard возраста/диапазонов/STOP | Реальные модельные forwards, fixed-noise reload equality; device dispatch fixtures | PASS model forward | NOT TESTED физическое исполнение. Live2camera SAM+VLA latency может превысить2s и останавливает отправку |

## GPU и память

- Настоящий SAM3: peak CUDA allocated **3,788 GiB**, reserved **4,152 GiB**, минимум MemAvailable **4,460 GiB**. Загрузка33,8s; первый top forward10,32s, тёплый wrist1,26s. Это не обещание realtime control.
- Полный pretrained SmolVLA: loss **3,1717882**, update state projection **1,00005e-5**, peak CUDA allocated **1,797 GiB**, минимум MemAvailable **6,804 GiB**. После reload нового процесса fixed-noise inference совпал в допуске1e-6.
- Полный ACT CPU: loss **57,6102104**, update state projection **9,99793e-5**, peak RSS около1,79 GiB, минимум свободной памяти **8,49 GiB**. Reload действия совпали побитно.
- Каждый GPU smoke запускался отдельным процессом под внешним монитором с резервом1,3GiB. Другие тяжёлые модели не загружались параллельно. До освобождения ресурсов CUDA-проверки были BLOCKED, затем реально выполнены.

## Исправленные реальные дефекты

1. Wrist MJPG640×480 поддерживал120FPS, а код просил30: получалось два кадра вместо устойчивого потока. Теперь wrist YUYV30 с readback format/FPS и проверкой двух кадров до startup.
2. STOP/error мог ждать PNG writer до снятия torque. Теперь источник останавливается раньше, drain ограничен; при отказе одного Torque_Enable делается одна попытка для каждого оставшегося мотора.
3. STOP во время End/Re-record мог начать новый capture уже закрытого источника. Отмена проверяется до нового capture/send.
4. Реальный SAM возвращал bbox −0,43px или642,39px для изображения640px, и адаптер ошибочно отвергал его. Исходный bbox сохраняется, численный input корректно клиппируется; целиком внешние/нефинитные boxes отклоняются.
5. Почти одинаковые cube/box masks на wrist теперь ambiguous, не уверенные два объекта. Низкая освещённость HSV становится unknown, не ложным уверенным цветом.
6. Resume SAM после дыры в JSONL восстанавливает причинный tracking suffix. Проверяются centroid/area/hash/provenance и совпадение train/inference.
7. Официальный экспорт LeRobot отсутствовал. Добавлены CPU export/validation/finalization jobs, официальный loader и точные int64 timestamps sidecar.
8. MLflow spool получил fsync metadata и межпроцессную блокировку replay; processor/config checkpoint защищены hash manifest.
9. Технические checkpoints теперь скрываются по manifest, а не только по имени папки. Выход за абсолютные пределы обученной политики отклоняется до send.
10. Добавлены главный status Leader/Follower/диск/FPS, безопасный Preflight и автоматическое проигрывание сохранённых кадров по их timestamps.

Фокус/exposure controls кодом не записывались. При первом переключении режима драйвер C920 изменил exposure readback156→38; факт сохранён в исходном hardware-readonly report. Теперь избыточные setters пропускаются, readback фиксируется. В последнем одновременном тесте controls readback не менялся; autofocus оставался0. Значение exposure самовольно не восстанавливалось. Геометрия после перемещения камер всё ещё unknown/invalid.

## Доказательства и остаток

Индекс: `validation/acceptance-20261106/`. В нём camera simultaneous report, hardware READONLY, SAM actual masks/report, pretrained train/reload, ACT CPU train/reload, official export/HTTP finalize, MLflow proof и HTTP/WS preview. Снимки/маски/checkpoints/логи не включаются в Git.

Непроверенные пункты: Safari с Mac; реальное Leader→Follower движение, направления/захват/gripper/STOP при активной телеоперации, длительная настоящая raw-запись; live SAM+VLA latency/качество; обобщение политики и успех захвата. Новых измеренных успешных демонстраций ещё нет. Запускать сегодня можно **первый контрольный эпизод с оператором** по RECORDING_GUIDE.md, затем проверить его валидатором и playback. Универсальность/успех после N эпизодов не обещаются.

## Доступ и версии на момент сдачи

- UI LAN http://192.168.0.168:8020 ; Tailscale http://100.95.83.3:8020 . `so101-ai-learning-ui`, PID747477 , python/uvicorn, порт8020.
- MLflow LAN http://192.168.0.168:5020 ; Tailscale http://100.95.83.3:5020 . `so101-ai-learning-mlflow`, parent PID2370 , HTTP worker PID2769, порт5020.
- LeRobot0.6.1; ACT/SmolVLA из этой же библиотеки; полный SmolVLA base revision `5e8d12a6e2975b0e5e5fce7c8caf47c371d257b6`. PyTorch2.11.0+cu130; torchvision0.26.0+cu130; Transformers5.5.4; MLflow3.6.0; SAM3 source0.1.0 pinned `f1647e8d0c1d280991f4d1b7d974c2704537fe66`, weights cache3c879f39826c281e95690f02c7821c4de09afae7. SAM имеет custom SAM License (не MIT), источник и hash в docs/SAM_ACCEPTANCE_20261106.json.
- Финальные проверки:104pytest PASS/43.67s,10DOM PASS; pip/JS/compile checks PASS. Предпросмотр обоих настоящих камер включён без bus/torque, текущий Preflight `READY_FOR_OPERATOR_START`. LAN/Тailscale endpoints проверены с Jetson, а не с Mac.
- Произвольные новые классы не подаются в adapter автоматически: поддерживаются semantic slots cube/box; общий SAM text API на других классах в этой приёмке NOT TESTED. FK/TCP/оптическая геометрия без проверенных актуальных transforms остаются unknown.
