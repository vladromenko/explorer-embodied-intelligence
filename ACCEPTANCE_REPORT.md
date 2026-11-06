# Дополнение7 ноября: запись и MLflow, релиз v13

Текущая программная приёмка:139 Python +16 DOM PASS; Chromium/WebKit synthetic workflow и установленный UI/LAN/Tailscale/archive playback PASS.120s raw3572 кадра; повтор60s raw+настоящий SAM3 GPU1796 кадров, max queue1. Реальный MLflow RUNNING с метриками прочитан во время записи; Stop→FINISHED, outage/replay и crash/quarantine→FAILED проверены повторно. Новых движений аппаратуры тесты не выполняли.

Доказательства и точные границы: `validation/recording-fix-20261107/release.json`, `RECORDING_RELIABILITY_REVIEW.md`. После исправлений остаётся1 физический контрольный эпизод оператором и проверка Safari на Mac. Подробности предыдущих train/save/reload проверок сохранены ниже.

---

# Финальная инженерная приёмка SO-101 Studio — 6–7 ноября 2026

Статус: **READY_FOR_FIRST_OPERATOR_EPISODE**, не FULLY VERIFIED. Настоящие камеры, read-only serial, SAM GPU и полный pretrained SmolVLA GPU проверены. Реальная телеоперация/запись с torque и Safari ещё требуют оператора. Технические checkpoints не разрешены для управления роботом.

Работа выполнена на `homebrain`, `/home/jetsonnx/Projects/so101_ai_learning`, поверх milestone `acceptance-baseline-20261106`. Новая просьба пользователя «порты можно освобождать, соседняя работа приостановлена» разрешила остановить владельцев ресурсов: user `so101-wristlab-web`, system `so101-pick-place` и `lelab`. Их файлы/калибровки/enablement не изменялись. Эти службы оставлены остановленными, чтобы не отобрать устройства у записи. VPN/авторизация/remote Codex не изменялись.

## Фактические результаты

| Компонент | Реализация | Фактическая проверка | Результат | Ограничения |
|---|---|---|---|---|
| UI | Studio: запись/библиотека/обучение/политика, Preflight, playback | Реальные HTTP/WS по LAN и Tailnet + DOM-сценарии | PASS API/DOM | Chromium/WebKit реально проверены в дополнении ниже; Mac Safari NOT TESTED |
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

## Дополнение 7 ноября: проверка рук отдельно от записи

По уточнению пользователя добавлена отдельная серверная телеоперация без датасета: `POST /api/teleoperation/start`, кнопка «Включить телеоперацию», состояние подключения/следования, счётчик частоты, камеры и STOP. Конструктор и загрузка страницы инертны. Тот же единственный HardwareSource сохраняет калибровку/пределы/блокировки и не повторяет движения. Перед записью собственная телеоперация останавливается, cleanup подтверждается, только затем стартует recorder. STOP отменяет и ожидающую передачу владения; при неподтверждённом torque новый запуск блокируется. Закрытие браузера не владеет жизнью серверной сессии. Это программная проверка; физический Start остаётся за оператором.

«Проверить модель и задержку» теперь загружает checkpoint и при наличии исходного raw выполняет два настоящих forward через текущий SAM/preprocessing/политику на сохранённом пригодном кадре. Исходные timestamps/hashes не меняются. Без raw веса остаются загружаемыми, но forward получает NOT_TESTED. Медленная проверка с тем же hash checkpoint блокирует физический Start до повторной проверки; live guard2000ms сохранён. Новая обвязка проверена CPU fixtures; новый совместный GPU SAM+VLA benchmark и физическое исполнение не объявлены пройденными. Прежние настоящие отдельные GPU SAM и полный pretrained update/reload остаются доказательствами своих этапов.

Реальная браузерная проверка: Chromium141.0.7390.37 и WebKit26.0, 12 сценариев каждый, PASS. Обе настоящие камеры отображаются; переключение ACT/SmolVLA, 51 эпизод, Preflight, явный разрыв WebSocket/reconnect и ширины1024/768/390. Найден и исправлен overflow блока Preflight. Скриншоты локальны/исключены из Git, отчёт `validation/browser-20261107/report.json`. Настоящий Safari/Mac остаётся NOT_TESTED. Браузеры и недостающие .so установлены только в cache своего проекта; системные пакеты не менялись. Воспроизведение: `bash scripts/setup_browser_tests.sh`, затем `npm run test:browser`.

Текущий выпуск:118pytest PASS,13DOM PASS. UI PID799786 на8020, MLflow parent PID2370 на5020. Адреса LAN/Tailscale прежние. Полный release proof: `validation/teleoperation-release-20261107.json`.

## Исправление реального старта рук — 7 ноября

Оператор нажал «Включить телеоперацию», backend отказал до включения torque: resting shoulder_lift830ticks против сохранённого min834. Ошибочное обобщение UI назвало это провалом калибровки. Повторное read-only чтение обеих рук с запретом register writes подтвердило полное совпадение сохранённых калибровок с регистрами. Никакая новая калибровка не нужна и не запускалась.

Исправление: фиксированный допуск энкодера8ticks (≈0,70°), дополнительно ограниченный max_step. Стартовый hold обрезается внутрь прежнего диапазона, коррекция реального случая4ticks (≈0,35°); normal targets остаются внутри сохранённых границ, максимальный абсолютный шаг сохраняется. Большие отклонения и несовпадение калибровок всё ещё блокируются. Измеренный state не подменяется граничным значением. Политика не получает послабления для предсказаний вне диапазона. UI теперь показывает истинную причину отказа и числа, а не скрывает любую ошибку со словом calibration.

Доказательства: `validation/arm-start-20261107/{failure,read-only,fix}.json`. Реальные read-only позиции, software reproduction startup/первой команды и реальные SDK normalize→unnormalize endpoints без serial open.122pytest PASS/45.08s,14DOM PASS. Повторный физический Start после исправления выполняет только оператор кнопкой UI; его успех ещё не заявлен.

## Исправление медленного следования — 7 ноября

Предыдущий физический операторский тест: FAIL по сообщению пользователя (медленно/неестественно, отдельные суставы воспринимаются как обратные). Лог дополнительно показывает остановку по top camera stale100ms. Новое физическое следование после изменений пока NOT_TESTED.

Подтверждённые дефекты: ограничение каждого нового target относительно измеренного отстающего follower искусственно удерживало малую ошибку и замедляло движение; цикл отдельной проверки рук был связан со свежестью камеры. Исправление: начальное выравнивание постепенно продвигает предыдущую **команду**, а не откатывается к отстающему feedback. После совпадения поз включается прямой joint action штатного LeRobot с сохранёнными абсолютными пределами. Startup deviation guard10deg/10percent не даёт бесконечно накапливать цель при неподвижном follower. Профиль автономной политики по-прежнему ограничен; физические движения не ретраятся. Запрошенная частота отдельного human loop60Hz, реальная показывается отдельно. Запись raw остаётся30FPS с обязательной синхронизацией.

Самостоятельная телеоперация открывает только обе serial bus. Камеры имеют отдельную lease; preview можно включать/выключать отдельно. Обычная запись/inference получают bus+camera leases, и единственный контроллер сохраняется. Потеря/занятость камеры больше не управляет отдельной проверкой рук.

Сохранённый выбор LeLab действительно so101_leader.json/so101_follower.json, наши снимки идентичны байт-в-байт. AST функций normalize/unnormalize в собственном и прежнем LeLab SDK идентичны. Никакие offsets/PID/направления регистров не менялись. Численная сверка с **настоящими классами установленного SDK** SO101Leader.get_action→SO101Follower.send_action:6 суставов, оба знака, обратный порядок dictionary — raw Goal_Position точно совпадает с HardwareSource.sample. Это подтверждает программный контракт, не заменяет проверку физического направления оператором. В сверке serial open запрещён.

128pytest PASS/46.18s,14DOM PASS; Chromium/WebKit PASS на обновлённом UI. Новая сворачиваемая таблица показывает Leader / фактически отправленную команду / измеренный Follower, а не смешивает их. Доказательства `validation/teleop-follow-20261107/`; прежние калибровки оставлены без изменений.
