# Дополнение7 ноября: запись и MLflow, релиз v13

Текущая программная приёмка:139 Python +16 DOM PASS; Chromium/WebKit synthetic workflow и установленный UI/LAN/Tailscale/archive playback PASS.120s raw3572 кадра; повтор60s raw+настоящий SAM3 GPU1796 кадров, max queue1. Реальный MLflow RUNNING с метриками прочитан во время записи; Stop→FINISHED, outage/replay и crash/quarantine→FAILED проверены повторно. Новых движений аппаратуры тесты не выполняли.

Доказательства и точные границы: `validation/recording-fix-20261107/release.json`, `RECORDING_RELIABILITY_REVIEW.md`. После исправлений остаётся1 физический контрольный эпизод оператором и проверка Safari на Mac. Подробности предыдущих train/save/reload проверок сохранены ниже.

---

# Прямое следование и независимые камеры — 7 ноября

**128 passed за46.18s** (`logs/teleop-direct-final.log`), **14DOM passed** (`logs/teleop-direct-ui.log`), обновлённые Chromium/WebKit — PASS (`logs/teleop-direct-browser.log`). Дополнены: точное совпадение всех6 осей/знаков/raw goals со штатным SDK без открытия serial; корректный порядок имён независимо от dictionary order; продвижение alignment-команды при отстающем feedback; сохранение ограничений автономной политики; bus-only startup без камеры; независимость camera lease от servo lease.

Прежняя пара калибровок подтверждена из сохранённого выбора LeLab; SDK normalize/unnormalize совпадают с прежним окружением. Physical follow старого профиля получил FAIL от оператора; новый профиль физически не запускался разработчиком и требует кнопки оператора. Артефакты: `validation/teleop-follow-20261107/`.

---

# Исправление отказа включения рук — 7 ноября

Полный набор: **122 passed за45.08s**, `logs/arm-boundary-full-tests.log`; DOM: **14 passed**, `logs/arm-boundary-ui-tests.log`. Добавлено воспроизведение настоящего830/834, проверка ±8ticks, неизменности калибровки, строгих commanded limits, max_step и отказа при9ticks. UI сохраняет точные детали ошибки. Обе руки действительно прочитаны без register writes, сохранённые калибровки совпали; это не физический follow-test. Доказательства: `validation/arm-start-20261107/`.

---

# Дополнение 7 ноября — отдельная телеоперация и настоящий браузер

- Финальный полный pytest: **118 passed за45.23s**, `logs/teleop-final-release.log`.
- DOM: **13 passed**, `logs/teleop-dom-tests.log`.
- Chromium141.0.7390.37 и WebKit26.0: по12 настоящих браузерных проверок PASS, реальные обе камеры/Preflight/ACT/Smol/51/обрыв WS/reconnect/ширины1440,1024,768,390. `validation/browser-20261107/report.json`, скриншоты в той же папке исключены из Git. Mac Safari этим не считается проверенным.
- Телеоперация: явный start без датасета; STOP; cleanup при USB error; запрет рестарта после неподтверждённого torque; отсутствие send после STOP во время leader read; передача recorder только после закрытия источника; STOP отменяет ожидающую передачу; waiting policy останавливается. Только изолированные fixtures, реального моторного Start разработчик не отправлял.
- Проверка модели: текущий saved raw/hash/schema, SAM→policy timing, отбрасывание невалидных observation как в train, NOT_TESTED при отсутствии raw, серверный запрет известного медленного checkpoint до открытия устройств. CPU fixtures новой обвязки; реальные GPU модельные проверки предыдущего этапа перечислены ниже.
- Сервисы/порты/LAN/Tailscale/WS и safe cross-origin rejection — `validation/teleoperation-release-20261107.json`.
- Воспроизведение браузерных тестов: `bash scripts/setup_browser_tests.sh` (всё в собственных node_modules/cache, без установки системных пакетов), `npm run test:browser`. Тест блокирует любые POST кроме безопасного Preflight и не нажимает Start рук. Первичная документация инструмента: [Playwright Browsers](https://playwright.dev/docs/browsers).

---

# Результаты приёмки — 6–7 ноября 2026

## Automated software tests

- `logs/acceptance-final-all-tests.log`: **104 passed in43.67s**.
- `logs/acceptance-ui-tests.log`: **10 DOM tests PASS** (linkedom). Это не реальный браузер и не Safari.
- `pip check`: No broken requirements found; JS syntax/compileall/git diff checks пройдены.
- Тесты охватывают disk/USB busy/missing/stale/corruption/schema/calibration/torque failure/STOP при drain+re-record, raw hashes/timestamps, train/validation/test, data leakage, actualSDK quantization с запрещённым Serial.open, processor tamper, out-of-envelope policy rejection и отмену startup.
- `validation/acceptance-20261106/busy-and-telemetry.json`: собственный inert GPU lease держался отдельным процессом, задача ждала; test-source frames росли2→7. Затем Stop; модель GPU в этом fault test не загружалась. Реальный MLflow получил восстановленные параметры/метрики из durable spool.
- `http-finalize-job.json`: реальный HTTP finalize→CPU exporter→официальный loader, source synthetic_test, training_eligible=False. Неполное/повреждённое не выдаётся за готовый dataset.
- Реальные HTTP/WebSocket test-source multi-episode, Re-record, playback PNG, browser-socket disconnect и restart/quarantine: `logs/acceptance-http-recording.log`, `validation/service-smoke.json`, `validation/restart-smoke.json`.

## Actual CPU tests

- Полный ACT production config:512dim,8heads,3200FF,4encoder,1decoder,4VAE, ResNet18 без скачивания weights, **51 573 638 параметров**. One real forward/backward/AdamW, loss57.6102104; state projection изменился9.997934e-05. Полный checkpoint и reload в новом процессе: действия побитно совпали. Минимальный MemAvailable8.49GiB. `validation/acceptance-20261106/act_cpu/`.
- Уменьшенные реальные архитектуры ACT/SmolVLA в pytest проверяют object perturbation при фиксированном noise, optimizer/RNG resume, отсутствие leakage и единый preprocessing. Они не заменяют pretrainedGPU проверку.
- Экспорт установленного LeRobot0.6.1/v3.0: официальный loader сравнил6кадров/2эпизода, RGB обоих камер без потерь, state/action/task/границы и точные ns. Экспорт независим от исходной raw папки. `validation/lerobot-export-acceptance.json`.

## Actual GPU tests

- SAM3, собственный source pinned f1647e8d..., реальные weights SHA9999e234... cache3c879..., CUDA forward на двух настоящих image snapshots: **PASS**. Peakallocated3.788GiB; peakreserved4.152GiB; минимумMemAvailable4.460GiB. Реальные PNG masks+boxes+centroids+score. Top3cube+2box; wrist2overlappingcategories отмечены ambiguous, не считаются двумя уверенными объектами. Color в низком свете unknown. Mask/raw hashes и timestamps проверены для отдельных снимков.
- Полный cached pretrained SmolVLA, **450 046 176 параметров**,99 880 992 trainable: CUDA forward/backward/update **PASS**, loss3.1717882, state_proj delta1.000054e-05. Peakallocated1.797GiB; минимумMemAvailable6.804GiB.
- SmolVLA full checkpoint загружен отдельным CUDA процессом: fixed-noise normalized+unnormalized actions совпали (rtol/atol1e-6). `validation/acceptance-20261106/pretrained/{report,reload_report}.json`.
- Два GPU smoke запускались последовательно, с внешним RAM guard. Отдельные снимки и pose readings не синхронны и не являются демонстрацией; loss-target — явно техническое повторение измеренной pose, никогда не отправлявшееся моторам. Все acceptance checkpoints technical_test=True и запрещены для робота.
- Native warning PyTorch оCC8.7 записан в logs, но реальные CUDA kernels обеих моделей выполнены. PyTorch/CUDA не заменялись.

## Real hardware tests

- По новому явному разрешению пользователя освобождены камеры и serial: остановлены3их владельца, файлы/настройки/enablement не менялись. `resource-release.json`.
- Обе камеры одновременно640×480: topMJPG~29.4FPS, wristYUYV~30.8FPS, skew23.06ms. Исправлен реальный ошибочный wristMJPG120/30 mode. `cameras-simultaneous/report.json`.
- Реальный HTTP camera-only preview: JPEG top/wrist, disconnect→404→reconnect, LAN HTTP200 и WS receive, Tailnet HTTP200 и WS receive. Моторы не открывались. `http-live-preview.json`.
- Read-only Leader/Follower: 6ID каждого, Present_Position и normalization, PositionMode0, calibration hash/live match. Чтение Torque_Enable: leader0, follower1. **0 Goal_Position writes, 0 Torque_Enable writes**. `hardware-readonly.json`.
- NOT TESTED: физическое следование, направления/реальные actions при движении, gripper grasp, силовой feedback, длительная запись и STOP активной реальной телеоперации. Эти действия оставлены оператору через UI.

## Real browser / Safari tests

**NOT TESTED**. Доступный browser/app inventory пуст. Проверены HTTP/WS endpoints и DOM, но они не доказывают визуальную работоспособность Safari. Точный checklist — RECORDING_GUIDE.md. HTTPS/WSS на8020/5020 не настроены; server-side image не требует navigator.mediaDevices/secure context.

## MLflow / reproducibility

`validation/acceptance-20261106/mlflow-proof.json`: четыре фактических full ACT/Smol train/reload runs имеют FINISHED; params/metrics прочитаны обратно, checkpoint.json artifact скачан и прочитан. Последние refs: Smoltrain `d162cc0177cd4adc89bd4ebaa0cfc7ed`, Smolreload `f32320de009541e4a1e0fdb316f3510c`, ACTtrain `834b894b03544d3095362f470f23ad58`, ACTreload `2e4c4f8b30dc429280825b53b9f9425a`.

Воспроизведение программных тестов: `.venv/bin/python -m pytest -q --basetemp=runtime/acceptance-rerun` и `npm test`. Hardware/GPU скрипты требуют явного флага; не запускать параллельно с операторской записью/другими моделями. Нельзя повторно обучать в той же acceptance папке: завершённые checkpoints не перезаписываются. Для обучения политики использовать новые настоящие демонстрации и отдельный job.
