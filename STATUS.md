# Explorer — Homebrain, 27 октября 2026

**IN_PROGRESS. Весь master-prompt не завершён.** Canonical Git:
`/home/jetsonnx/work/Explorer`; работающие службы не зависят от Mac/чата.


**CURRENT=03 → NEXT=04 → THEN=05→06→01.**
02A+02B развёрнуты и независимо доступная программная работа выполнена.
Отдельные02 gates реальных expert demos и valid live-shadow остаются OPEN до
наблюдаемой подготовки владельца. Они не подменяются технической записью или
fixtures. Продолжаем03 по исходной очереди, не ожидая человека и не снимая02 gates.

02 имеет статус **SOFTWARE READY / PHYSICAL VALIDATION REQUIRED** с одной
подтверждённой камерой Nano. Real expert dataset/model/live-shadow и физический
полный навык не подтверждены; эти отдельные gates сохранены OPEN. Другие опыты
пока не SOFTWARE READY. Проценты ниже — число полностью
подтверждённых применимых acceptance gates; частичный gate даёт0, веса равны.
Это не вероятность успеха, не объём кода и не оценка оставшегося времени.
Разные опыты имеют разные критерии. Scoped mock/SIM компоненты учитываются
только в собственном scope; весь вертикальный путь и актуальный release —
обязательные отдельные gates. Физическая приёмка посчитана отдельно и не скрывает
программный остаток. Для01 physical относится только к опциональному deploy.
Определения gates и evidence — в единственной канонической матрице
[_registry/acceptance.json](_registry/acceptance.json); таблица — её представление.

<!-- READINESS_TABLE_FROM_ACCEPTANCE:BEGIN -->
| Опыт / очередь | SOFTWARE | PHYSICAL | Доказано в указанном scope | Программный остаток | Физический остаток |
|---|---:|---:|---|---|---|
| 01 — PARKED | 40% (4/10) | 0% (0/3) | CAD/MuJoCo render, ACT updates/reload, версии/splits; scoped UI | Канонический PPO→demos/DAgger→ACT; исправить причину отказа, пройти full-task threshold и UI/release | Отдельный deploy/preflight и наблюдаемый тест; SIM не требует движения |
| 02 — REAL DATA / PHYSICAL OPEN | 100% (14/14) | 0% (0/4) | Развёрнуты02A+02B, actual Nano17frames→analysis import, official Dataset/ACT/resume и очередной DEV на generated inputs, model UI и регрессия | ПО02 готово с одной Nano camera_rgb; real expert/model/live-shadow и physical task gates остаются OPEN отдельно | Свежая MCU-телеметрия/reference и ракурс; physical gamepad/manual/STOP, полные реальные показы и supervised model trial |
| 03 — CURRENT PRIORITY | 53.8% (7/13) | 0% (0/3) | Pi→Nano software/epoch fences, common capture/import, Dataset106/9+actual generated03 ACT4→DEV10; real Nano39RGB→analysis import и desktop/mobile data UI | Real continuity PASS до physical path; registration/mirror/два режима и полный release; реальные expert demos/model/live-shadow отдельно | Registration/task axes/gripper, real changing shadow; неподвижное шасси, малая амплитуда и одно непрерывное supervised движение без рывков |
| 04 — NEXT | 60% (6/10) | 0% (0/3) | Scoped UI, native6 cohort, ACT/DP train/resume/reload, shadow/owner tests | Полный source recording→approved dataset→train→independent object evaluation и release | Питание/reference, полные показы и независимая source-проверка |
| 05 — THEN | 50% (5/10) | 0% (0/3) | Dual typed record/train, WAL/interlocks, cancellation IO fence, fake Publisher receipt в одной FSM | Полный opening→independent transfer→finish/park lifecycle, камеры/режимы/recovery и release | Общая сцена, независимое удержание и полный наблюдаемый handover |
| 06 — THEN | 40% (4/10) | 0% (0/3) | Typed lineage/splits, target ACT optimizer/reload, portable SIM и отрицательные исходы | Принятый source04, mapping/адаптация, успешный full-task transfer, H2 comparison и UI/release | Принятый физический source, target mapping и наблюдаемый transfer |
<!-- READINESS_TABLE_FROM_ACCEPTANCE:END -->


Live central+worker — **65ae9d021796c5ec4c9cab8a129fdb827dbf36f3**;
собственные Nano observer/cache остаются **722755dc99094fc86bab20796d2b7bc4bf56d536**.
[Фактический central-only выпуск03](_registry/evidence/experiment03-central-data-actual-65ae9d02-20261027.json):
2271PASS/20SKIP, kernel peak4343767040B, swap/OOM0;243Node на тех же JS bytes.
Только central+worker restart2.550s; originalNano source/reference/PIDs/6configs exact,
passiveROS20s0. Настоящий HTTPS Firefox desktop/mobile: advancing Nano JPEG,2cameras,
0overflow/0nonGET; data02/03 scoped. Technical39RGB остаётся privatecandidate,
production03imports0. Real continuity/B-registration/gripper/current-reference OPEN;
03 **не SOFTWARE READY**, новый03physical runtime не развёрнут.
Далее04→05→06;01 long training PARKED.
Pi observer и import pin оригинального Nano web остаются **4c5f4f5b0fe4e37a8a66bf3643f0f696c0232db4**.
Оригинальные Nano camera/MCU/control/web не перезапускались; PIDs/source/reference
и6 исходных configs совпали до/после. Central cutover2.518s; passiveROS20s:0сообщений.
[Фактический scoped02B выпуск](_registry/evidence/experiment02-learning-actual-722755dc-20261027.json).
Frozen полный прогон:2191PASS/18SKIP/0FAIL за823.50s;231Node. CPU3/5GiB,
peak4695658496B, swap/OOM0.24старых jobs/11cards/15948старых events byte-exact
после реального I/O; добавлен только новый analysis import, activejobs0/schema same.

Actual Nano observations-only:17реальных кадров,11depth,17IMU/LiDAR/odom/velocity,
7valid power records; masks и исходные clocks сохранены, exposure не подтверждена.
Start/pause/resume/finish UNKNOWN→исходный TAR/SHA/JPEG preview→actual206 Range→
отдельное approve FOR ANALYSIS→настоящий production worker import прошли.
Ни одной экспертной команды; принятых expert originals0; обучение не запускалось.
Ещё один passiveROS20s:0сообщений, исходные files/reference/PIDs/6configs exact.
HTTPS Firefox desktop/mobile: меняющиеся реальные frame IDs,0overflow/0nonGET,
точная техническая SkillSpec,0expert originals/0моделей. Gamepad calibration —
только явный disconnected browser fixture47samples; физический Mac gamepad OPEN.
Первый browser attempt предполагал auto-selection новой SkillSpec, но существовал
отдельный старый draft; он сохранён, повтор выбрал точную technical version явно.

Official LeRobotDataset106/9 и source-bound chunks без склейки пауз реально
проверены на generated source inputs: ACT4→новыйPID+4 exact uninterrupted8;
actual authenticated ASGI→Supervisor→bootstrap→job_runner ACT4/save-reload→
отдельный DEV worker с0optimizer тоже прошёл. Это не реальные expert demos.
UI: budget/train/progress/logs/pause/resume/cancel, saved versions, DEV и shadow,
метрики и SHA provenance. Все computational/physical scopes раздельны.
Current native GET409: MCU telemetry is stale; reference после прежнего fault
невалиден. Агент не делает HOME, grants, STOP или реальное движение.
Реальный expert model/live-shadow/full-task trial остаётся OPEN; master не завершён.
[Порядок работы02](02-demonstration-learning/docs/MODEL_CHECKS_RU.md).
Durable giver healthy/uninitialized — не физическая готовность.

Подготовленная статья отдельно сохранена в `work/temporal-numeric-admission`,
`a5865cc22b973a5e1a1c6a988042f4c3668b129a`: настоящий TeX PDF6pages,
8light tests PASS. В main этот последний paper checkpoint не интегрирован;
повторный offline build и полный visual review не выполнены,
`submission_ready=false`. Ветка сохранена, работа PARKED вместе с04–06.

Подтверждено:

- Новые native6 ACT/DP и fused106 CPU updates/reload совпали с R2. Настоящий
  Supervisor→pause2→exit→новый PID→ещё6 прошёл2tests/112.60s, exact typed
  model/optimizer/loss/RNG/sampler; peak3.33GiB, swap/OOM0.
  [R3B](shared/training/NATIVE6_QUEUE_STATION_R3B_20261027_RU.md).
- Source04 observed evaluation и ACT/DP read-only shadow подключены в main;
  точный доставленный/показанный JPEG, actor/model/epoch, cancel/logout/STOP
  ограждены. Физическая object evaluation не выдана за пройденную.
- 05: одноразовые native choices; original Pi publication gate и durable
  double-hold WAL; receiver-first/giver arm и Pi challenge после actual giver
  opening→новое независимое подтверждение→finish. Native STOP также охватывает
  завершённый begin до attach по exact returned epoch; end-to-end fake-source
  проверка прошла.45 interlock targeted tests прошли;
  Pi и giver loss/corruption/restart/legacy/fault race проверены на fake owners;
  source global/base zero STOP не ждёт задержанного arm Publisher. Root требует
  фактический healthy/initialized durable giver proof; metadata receipts и reset
  с source CAS доступны в main. Полный parked phase coordinator ещё открыт.
  [Границы](05-cooperative-handover/docs/RECEIVER_INTERLOCK_TRANSPORT_RU.md).
  Позднейший main (ещё не live): source-held scopes/metadata arm, новые USER
  facts после actual Source challenge и отдельный parked CONTROL уже связаны
  с существующей FSM/API/UI. Fresh per-role admission повторяется перед native
  submit; cached preview не разрешает команды. STOP охватывает returned owner
  и отдельный declared cancel-only scope до ответа.100 helper cases и57 combined
  fresh/STOP cases прошли; истечение lease/наблюдения в парных GET блокируется.
  Полный policy Publisher→observed opening→transfer/finish lifecycle ещё открыт.
- Настоящий future initial capture→authenticated API→Supervisor/bootstrap→worker→
  download→новый PID восстановил все33 исходных файла:153859356B MJB,20CAD,
  один RGB,0target advances. Literal failed diagnostic trial сохранён; export
  PARTIAL, numerical/RGB reexecution не заявлены.
  [Receipt](research/audits/recorded-initial-capture-export-a1-20261027-RU.md).
- V10:16 заранее frozen matched contact probes дали2/4,3/4,4/4,4/4. Signed
  opposing floor/bottom forces измерены; unique cause AMBIGUOUS, penetration
  сохраняется. Это contact diagnostics, не learned/full task/physical mastery.
  [Все исходы](research/audits/TRANSFER_CONTACT_V10_RESULTS_RU.md).
- Immutable SIM helper V7b дал новый PID/native/RNG/report bit-exact replay.
  Полный portable V10:16 новых PID/UUID,
  14016×769 float64, contacts/solver/score bytes exact,0diff. Python old-path
  ban проверен; C opens UNMEASURED, OS closure PARTIAL. Отрицательные исходы
  сохранены. [Receipt](research/audits/CONTACT_PORTABLE_V10_RESULTS_RU.md).
- Новый TARGET ACT9:1/4полных SCRIPTED TRAIN показов допущен (439RGB/prior9),
  3отрицательных сохранены отдельно.1200настоящих CPU updates/reload0;
  8самостоятельных dev/sealed rollout/6400dispatch дали полный успех0/8.
  Chunk8:grasp4/4,lift+carry2/4, measured-open0/4; это не полный навык.
  [Результат](research/audits/target-visual-object-v3-results-20261027.json).
  C2×2 clock×absolute/increment:4×1200CPU train завершены/reload0, все4
  модели frozen. C-v1:1UNKNOWN из-за доказанной float64 roundoff границы,
  остальные15NOT_STARTED. Отдельный preregistered C-v2 завершил16новых
  независимых SIM trials:DEV0/8 иSEALED0/8 полных успехов,16FAILURE/0UNKNOWN,
  12800actual dispatch; grasp1/16, lift/carry/place0. Numeric admission2 раза
  исправил только доказанный roundoff. Все исходы и2073pins сохранены;
  модели не выбраны и не повышены до освоенного навыка.
  [C-v2](research/audits/TARGET_TEMPORAL_C_V2_RESULTS_RU.md).
  Отдельный TRAIN-only audit B завершил1756 prior-zero forward на тех же4
  checkpoint:0posterior/optimizer/physics/dispatch, модель/RNG неизменны;
  все2102pins/input/output и NumPy scorer перепроверены. Остаточные ошибки
  сохраняются; normalized ABS/INCR MAE между representations не ранжируются.
  Это не closed-loop gain и не уникальное причинное объяснение.
  [TRAIN audit](research/audits/TARGET_TRAIN_FORWARD_AUDIT_B_RESULTS_RU.md).
- Frozen4c5f4f5b с20 проверенными CAD/headless EGL:1835passed/15skipped,
  0failed за458.79s; CPU3/5GiB, peak4.20GB, swap/OOM0.215 Node tests прошли.
  Firefox:9desktop/mobile pages,0overflow,0non-GET;12authenticated live GETs.
  24jobs/11cards/14042старыхevents/schema exact; centralcutover2.430s.
  154исходных non-web.py,6configs,94shared files exact; только web restart,
  пассивный ROS20s:0actuator messages. Старые отрицательные прогоны сохранены.



Текущее оборудование: Pi station available=true, idle/error=null, configuration
writes0 и last_command=null; фактическое чтение восстановилось после перезапуска.
Nano native reference невалиден после сохранённого telemetry fault. Агент не
делает HOME, не двигает шасси/руки и не меняет6 исходных ROSMASTER configs,
firmware/limits/watchdogs/readbackOFF. Unknown и command-estimate не становятся
measured. Source/target splits раздельны; development/sealed/unknown не optimizer.

Остаток строгой приёмки02: канонический
record→approve→transfer→LeRobotDataset→ACT→save/reload→offline→live-shadow→UI
с реальным expert dataset/model evidence. Адресная/полная регрессия и выпуск02B
уже выполнены; новые проверки/исправления нужны только по фактическим новым данным.
3–5 показов — технический smoke, не обещание освоенного навыка. Готовность02A
не завершает02. Для03 далее нужны полный mirror/retargeting/record/train flow,
два режима и конкретная наблюдаемая calibration checklist.

01 PARKED до программной/operator-ready готовности04–06: нужен один воспроизводимый
PPO teacher→пригодные demos/DAgger→ACT путь. TARGET C-v2 full success0/16 —
NOT READY; это не доказательство канонического01 PPO pipeline. Причина отказа
и фактический full-task threshold остаются OPEN. Для04–06 сохранены
phase/hold-successor/transfer/retention/recovery и H1–H5 остатки; они PARKED,
не отменены и не объявлены выполненными.

Единственная матрица01–06: [_registry/acceptance.json](_registry/acceptance.json).
[GOALS](GOALS.md) · [PLAN](PLAN.md) · [DECISIONS](DECISIONS.md) ·
[HARDWARE](HARDWARE.md) · [PHYSICAL_CHECKLIST](PHYSICAL_CHECKLIST.md).

## USAGE_POLICY

Root — единственный координатор и интегратор; максимум1 независимый субагент.
Прежние checkpoints сохранены; единственный независимый агент делает review03.
Последнее указание пользователя: продолжать текущую работу автономно
через02→03→04→05→06→01; не завершать turn после одного этапа. Повышать effort
только для конкретного трудного вопроса. Этот документ не переключает модель/effort.
Проверки: адресные при редактировании, интеграционные в конце эксперимента,
один полный прогон перед deploy. Повторные широкие04–06 и2102-file аудиты
не запускать без новой причины. Подробные логи остаются в артефактах.
Критические motion/STOP/interlock/data/provenance инварианты сохраняются.

Каноническое уточнение владельца: всего2физических camera streams системы —
один Nano ROSMASTER и одна Logitech Pi для LeRobot. Nano identity подтверждена
действующим драйвером/USB как Orbbec DaBai DCW2; RGB и optional depth — одно
устройство.17реальных кадров относятся к нему. Вторая Nano камера и отдельная
Pi overview камера не существуют и не являются gates. Software-ready02 отделён
от real-data/physical validation. [Inventory](_registry/camera-inventory-20261027.json).

03: новый physical command path НЕ развёртывается до PASS continuity на реальных
leader inputs. Stable ACTIVE должен сохранять прежнюю плавную траекторию и
частоту, без новых clamp/filter/zero/reacquire. [Приёмка03](03-lerobot-teaching/docs/CONTINUITY_ACCEPTANCE_RU.md).

Подготовлено в main, пока не live: общий `/api/retarget/capture/*` на Nano,
проектные fences02/03, metadata/pause/cancel/preview/Range/review/approved transfer,
тот же Dataset/train/eval backend. Actual isolated source03 generated proof:
5Source originals→ACT4/save-reload0→отдельный DEV10,1PASS33.30s;0real experts,
0physical requests. Общий executor R6 ACT/DP pause/resume2PASS123.67s; точный
final-app02 queue R3 3PASS34.37s. Старые originals сохранены;19+18SHA не ослаблены.
Проверки API/UI и будущий central-only выпуск сохраняют begin/recenter03 закрытыми
до real changing-input continuity PASS. Pi/native web пока4c, master не завершён.

Actual03 isolated candidate:39NanoRGB/0expert commands, pause/resume→UNKNOWN→
originalTAR53,831,680B/SHA/JPEG/206Range→approve→actualNativeRetarget analysis import.
Технический source затем явно rejected, archive/review chain сохранены; production
не получает unbound candidate SkillSpec. Source/ref/PIDs/6configs exact, passiveROS20s0.
Firefox desktop/mobile actualdecoded/advancing frames, archivepreview,0overflow/0POST;
счётчик исправлен на39raw/0admitted, project03 отделён от02 и queued copy.
[Candidate evidence](_registry/evidence/experiment03-native-capture-candidate-0d91dff2-20261027.json).
Final inspector+app: native02R4 3PASS35.60s и native03R2 1PASS35.49s, actualACT4→DEV10
в каждой private fixture очереди. 19+18pins и дополнительный inspector SHA строгие;
R1–R3 originals сохранены. Это не реальные экспертные показы/continuity/whole-master.
Полный frozen regression и central-only выпуск65ae9d0 завершены; physical03
begin/recenter остаются CLOSED, Pi/originalNanoWEB не обновлялись.

Последнее окончательное указание владельца: CURRENT03, NEXT04, THEN05→06→01.
01 паркуется после завершённого bounded audit/уже начатых8-step smoke; долгие
продолжения не запускаются. Для04–06 сначала короткий reuse check: существующие
shared modules, новый тонкий adapter, contract tests. Текущий reuse status04: gap/deployment audits сохранены в отдельной ветке;
реализуется тонкий metadata capture adapter с общими timeline/review и contract tests.
Station writer/control/camera owner и физическое ПО Pi пока неизменны.05/06 — reuse check
перед реализацией, процент за копирование не прибавляется. Общие Recorder/API/
Dataset/trainer/model-registry/UI не копируются; существующий владелец камеры
остаётся единственным. Physical/real-data gates и critical03 continuity не сняты.
