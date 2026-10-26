# Explorer — фактическое состояние, 26.10.2026

Исходная задача **IN_PROGRESS**. Homebrain работает автономно; Mac не нужен
для runtime. Срок4–5часов снят в пользу качества и воспроизводимости.
H1–H5, полные физические навыки и весь мастер-промпт не подтверждены.

Control Center и worker сейчас работают на `c8be649`. Панель:
https://homebrain.tail4a7de4.ts.net/ через личный Tailscale;
в домашней сети http://homebrain.local:8088.
Pi/Nano observer и общий модуль оригинального Nano web действительно обновлены
до `f4cc5c3`. Это отдельные source pins; более новый код main не считается
автоматически развёрнутым. Службы, private environment и исходные файлы
проверены до и после обновления.

Оригинальный Nano web использует тот же экземпляр штатного владельца приводов,
Ruckig и servo conversion. Последнее обновление меняет только один import path.
За20секунд пассивного ROS-наблюдения при web restart получено0 сообщений на
`/arm6_joints` и `/explorer/request`. Original camera/MCU/control сохранили PID
2347/2356/2413. Все6 исходных controller config SHA совпадают; arm-state/startup/
fault/commissioning bytes неизменны. Настройки, firmware, limits, watchdog и
отключённый feedback не менялись. Разрешение исполнения по умолчанию false.
Свидетельство: [native/observers](_registry/evidence/native-forward-observers-20261026.json).

Обе руки и шасси агент самостоятельно не двигает. Только пользователь может
отдельно начать текущий наблюдаемый тест. Старый native reference действительно
недействителен после сохранённого telemetry fault: агент не выполняет HOME и
не стирает fault. Это конкретное требование нового reference, не общий запрет
BC из-за отсутствующего feedback. Неизвестные измерения остаются null/masks;
command-estimate и подтверждение host publication не означают измеренное
достижение/удержание предмета.

Actual GET Pi: station idle, available=true, last_command=null; native6 observation
и привязанный оригинальный JPEG с совпавшим SHA —200. Перед обновлением была
сохранена временная ошибка servo sync-read; актуальная проверка снова доступна.
Nano native106 packet отвечает503 из-за original-owner409/старого reference;
подмена позы не выполняется. Native capture200, сенсорный cache ранее проверен:
384 исходных записи шести топиков,0 errors, original CDR/JSON, полные lidar/IMU/
odom/battery/CameraInfo. RGB identity подтверждена; exposure sync и extrinsics
не выдуманы. Pi Hailo private environment сохранён побайтно.

Каноническая нумерацияv2:01 autonomous;02 keyboard/gamepad ROSMASTER;
03 SO101 leader→ROSMASTER;04 две SO101 source learning;05 cooperation;
06 source04→ROSMASTER transfer. Общий реестр используется Python/API/браузером.
Рабочая SQLite мигрирована:24 jobs,11 cards. Повторный audit после интеграции
сохранил точные тела9542 старых events, всех11 cards и config/result24 jobs.
Новые telemetry events добавляются отдельно. Три scheduling tables пока пусты;
DDL от прежнего импорта API в тестах сохранён, production jobs тесты не создавали.
Теперь conftest импортирует API только с отдельным private test state.
[Описание миграции](research/audits/EXPERIMENT_NUMBERING_V2_RU.md).

Общая регрессия frozen `c48c711`:1005passed,18skipped,8failed/244.80s.
Все8 ошибок были устаревшими fixture-сценариями после запрета переписи terminal
job. Они исправлены без ослабления production immutability:53 targeted passed.
Исторический SO101 AST proof неизменен; сравнение допускает только точные
поздние metadata-fence additions, проверяет прежние motor/sampling AST и
coordinate constants. Новые backend/export/repin tests:58passed; полная
сохранённая native factory с original/forward candidates:6passed. Новый полный
прогон нужен после текущего объединения профиля GPU и worker hooks.

В main реализованы общая fair queue с aging/приоритетом/DAG/ресурсными бюджетами,
revision CAS, durable source/PID/boot/nonce, cancel-wins и checkpoint pause→exit75→
проверенный checkpoint→новый process с optimizer/RNG/sampler. SIGSTOP отдельно
и честно удерживает RAM/GPU lease. UI `/queue` показывает настоящие причины
ожидания. Новые очередь/05 UI ещё не активированы в центральной службе.

Две настоящие CPU проверки Supervisor→bootstrap→job_runner→official ACT6/DP6
прошли108.89s: pause после2 updates, exit75/старый PID исчез, новый PID довёл
до8, model/optimizer/loss/RNG/sampler точные. Это frozen source8480, отдельно
архивированная квалификация, не автоматическая гарантия всех следующих edits.
Текущие source04 ACT/DP math proofs сохраняют original native6 SIM и reload0.
ACT9 CUDA graph3:12tests/8updates/reload0; teacher1/1, независимая попытка0/1.
GPU resume с прежними nondeterministic flags НЕ совпал побитно; отрицательный
результат сохранён. После отдельного fixture исправлен порядок startup: профиль
настраивается до первого CUDA device probe; прежний отказ до optimizer сохранён.
Новый production profile4:13actual tests/71.41s,8updates/reload0; отдельный
4→exit75→новыйPID4 прошёл42.52s с точными model/optimizer/loss/всеми RNG/sampler.
Профиль ограничен state9/action9, image64/batch2/chunk4 FP32. Source6/106 CUDA
не допущены. Актуальные CPU ACT6/DP6/106 повторно исполнены; веса ACT6/DP6
совпали с прежними, новый source signature сохранён отдельно. Привязка к
кандидату826a430 опубликована после проверки16 source SHA и runtime; активный
центральный c8be649 ещё не переключён. [Точные области](shared/training/PROFILE4_ACTUAL_20261026_RU.md).

Реальные source04/calibration API и веб-формы подключены: original frame/SHA,
10 diverse intrinsics views, отдельный reference, пользовательские измерения,
solve/overlay/CAS accept. Actual Pi ChArUco capabilities200; настоящих измерений
и принятой физической calibration пока нет. На Homebrain Firefox153/BiDi
действительно проверил settled overview/evening/02/04: error=null, без overflow,
реальные камеры. После нового центрального deploy предстоит05/queue review.
29 новых Node lifecycle/real JS↔temporary service tests прошли, включая STOP,
поздние ответы и UUID retry без дублирования queued native9/native6 shadow.

Transfer v8b дал1 nominal SCRIPTED_SIM полную цель с release/settle/free fingers;
это геометрический teacher, не learned source→target навык. Все439 actual target
frames связаны с21.95s видео. Заранее frozen v9:4 новых development attempts,
2/4 полных успеха,2/4 неудачи release/settle с contact penetration; все попытки
и отрицательные исходы сохранены, retuning/exclusions не было. Target development
не разрешён для optimizer, независимо от legacy source-only permission field.
[Результаты](research/audits/transfer-hover-development-v9-results-20261026.json).

Выбранный scientific evidence graph уже реально экспортирован и независимо
восстановлен:1898 files/75MB; unknown dependencies честно дают PARTIAL.
Raw scientific originals/SHA и canonical projections разделены; auth/tickets/
node tables и raw SQLite не экспортируются. Queue worker/API/UI для удобного
выбора и скачивания сейчас подключаются. Это не recovery backup.

Полная recovery-копия на T5 ранее действительно восстановлена отдельно:
11812 files/8,483,365,831bytes, все SHA, SQLite integrityok. Snapshot и старый NVMe
state сохранены. T5 ext4 смонтирован в `/mnt/explorer-data` по прежнему явному
разрешению владельца для точного serial. Полный hardware reboot не проверялся.
Новые квалификации лежат отдельно от production-state.

Остаются полезные программные пункты: завершить warm two-model05 actor и
явный пользовательский observed flow; включить новую центральную очередь/UI;
source04 DP live-history shadow; source-bound transfer06 dataset/student/eval;
общий Pi current-user permission и source04 observed evaluation owner;
исправление и независимая оценка предметных политик;
научные сравнения, повторы/ablations и широкая recovery campaign.102 full texts
и35 deep reviews не подтверждают гипотезы автоматически. Физические шаги
отдельны в [веб-чеклисте](EVENING_CHECKLIST_RU.md); программные блокеры
не перекладываются на пользователя. Весь мастер-промпт остаётся открытым.

В main экспорт Research Hub уже подключён к общей очереди и скачиванию.
Отдельный настоящий API→Supervisor→bootstrap→worker прогон завершён0.873s:
133120bytes TAR,7files, SHA и две независимые копии проверены, original SQL
неизменён. Это private fixture с сохранёнными реальными SCRIPTED_SIM receipt/
video bytes, не новая научная попытка и не production job. Результат PARTIAL
с3 незакрытыми зависимостями; первый harness/reap отказ сохранён отдельно.
[Свидетельство](_registry/evidence/research-export-public-queue-20261026.json).

Фактическая загрузка двух отдельных native CPU моделей и2 predictions каждой
прошла6.07s/peak895504384bytes. Это проверка конкретных SIM checkpoints при
2 CPU threads; произвольные будущие пары и физический runtime ею не допущены.
[Точный scope](_registry/evidence/cooperative-dual-cpu-finite-20261026.json).
В main добавлен current source04→06 каталог с явными A/C blockers и неизменяемым
событием выбора source; dimension6, loss и старый hold не означают object skill.

Новая полная регрессия frozen826a430 сейчас выполняется отдельно от
production-state:5GiB/swap0/CPU250%, EGL и фактические CAD meshes. Последние
118 Node tests и60 targeted Python tests прошли; после новых source API/SQL
ограничений отдельно38passed и24 browser-bootstrap checks. Все новые55 Python
файлов/изменений проверены против5de105e: новых continue operators нет.
