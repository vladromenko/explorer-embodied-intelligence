# Explorer — Homebrain, 27 октября 2026

**IN_PROGRESS. Весь master-prompt не завершён.** Canonical Git:
`/home/jetsonnx/work/Explorer`; работающие службы не зависят от Mac/чата.


**CURRENT PRIORITY=02, NEXT=03, THEN=01; PARKED=04/05/06.**
Очередь: **02 полностью → 03 полностью → 01 → 04 → 05 → 06**.
Сначала02A — пригодная оператору запись показов, затем без смены приоритета02B —
полный цикл обучения.03 переиспользует02. Новые широкие работы04–06 отложены;
разрешены общие исправления, необходимые текущему02/03.

Ни один опыт сейчас не SOFTWARE READY. Проценты ниже — число полностью
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
| 01 — THEN | 40% (4/10) | 0% (0/3) | CAD/MuJoCo render, ACT updates/reload, версии/splits; scoped UI | Канонический PPO→demos/DAgger→ACT; исправить причину отказа, пройти full-task threshold и UI/release | Отдельный deploy/preflight и наблюдаемый тест; SIM не требует движения |
| 02 — CURRENT PRIORITY | 28.6% (4/14) | 0% (0/4) | Страница/backend, прежний keyboard contract, scheduler, checkpoint/reload компоненты | 02A: полноценные камеры/gamepad/запись/approve/передача; 02B: один Nano→NX→ACT→shadow→UI путь, регресс и release | Текущий manual/gamepad/STOP тест, реальные 3–5 smoke-показов и supervised model trial |
| 03 — NEXT | 41.7% (5/12) | 0% (0/3) | Pi→Nano software stream, исходная calibration arithmetic, typed9D запись, epoch/STOP компоненты | Переиспользовать полный02; mirror, два режима, FK/IK/clutch/gripper, latency, один полный UI цикл | Наблюдаемая регистрация/калибровка, direct mirror, показы и learned task |
| 04 — PARKED | 60% (6/10) | 0% (0/3) | Scoped UI, native6 cohort, ACT/DP train/resume/reload, shadow/owner tests | Полный source recording→approved dataset→train→independent object evaluation и release | Питание/reference, полные показы и независимая source-проверка |
| 05 — PARKED | 50% (5/10) | 0% (0/3) | Dual typed record/train, WAL/interlocks, cancellation IO fence, fake Publisher receipt в одной FSM | Полный opening→independent transfer→finish/park lifecycle, камеры/режимы/recovery и release | Общая сцена, независимое удержание и полный наблюдаемый handover |
| 06 — PARKED | 40% (4/10) | 0% (0/3) | Typed lineage/splits, target ACT optimizer/reload, portable SIM и отрицательные исходы | Принятый source04, mapping/адаптация, успешный full-task transfer, H2 comparison и UI/release | Принятый физический source, target mapping и наблюдаемый transfer |
<!-- READINESS_TABLE_FROM_ACCEPTANCE:END -->


Live central+worker и собственные Nano observer/cache — **d923ea94becb68e3d540ba523b635483e2758689**.
Pi observer и import pin оригинального Nano web остаются **4c5f4f5b0fe4e37a8a66bf3643f0f696c0232db4**.
Оригинальные Nano camera/MCU/control/web не перезапускались; PIDs/source/reference
и6 исходных configs совпали до/после. Cutover central2.478s, passiveROS20s:0сообщений.
[Фактический scoped02A выпуск](_registry/evidence/experiment02-acquisition-actual-d923ea94-20261027.json).
Полная frozen регрессия:2154PASS/15SKIP/0FAIL;226Node. Настоящий HTTPS Firefox
на desktop/mobile декодировал кадры Nano; калибровка gamepad проверена только
явным software fixture, физический Mac gamepad ещё не квалифицирован.

02A содержит запись/паузы/отмену, независимые кадры/сенсоры, input journal,
просмотр/скачивание и отдельное Source approve→hash-bound resumable transfer.
02B ещё **IN_PROGRESS**: новые исходники связывают official LeRobotDataset,
source-bound сегменты без склейки пауз, ACT106, saved versions, DEV и GET-only
shadow с API/UI. Реальная вычислительная fixture квалификация:3PASS/67.78s,
ACT4→новыйPID+4 совпал с uninterrupted8; реальные физические demos0.
Supervisor→bootstrap→job_runner106, актуальный общий controller, итоговая
UI/регрессия и новый02B release проверяются отдельно. Неподвижная техническая
запись сохраняется только для анализа и не заменяет успешный экспертный показ.
Durable giver healthy/uninitialized; это не физическая готовность.

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

Текущий программный остаток02B: итоговый канонический
record→approve→transfer→LeRobotDataset→ACT→save/reload→offline→live-shadow→UI
с реальным dataset/model evidence, адресной регрессией и новым release.
3–5 показов — технический smoke, не обещание освоенного навыка. Готовность02A
не завершает02. Для03 далее нужны полный mirror/retargeting/record/train flow,
два режима и конкретная наблюдаемая calibration checklist.

01 ждёт полной программной готовности02/03: нужен один воспроизводимый
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
Все прежние активные агенты завершили безопасные checkpoints и idle.
Последнее указание пользователя: продолжать текущую работу автономно
через02→03→01→04→05→06; не завершать turn после одного этапа. Повышать effort
только для конкретного трудного вопроса. Этот документ не переключает модель/effort.
Проверки: адресные при редактировании, интеграционные в конце эксперимента,
один полный прогон перед deploy. Повторные широкие04–06 и2102-file аудиты
не запускать без новой причины. Подробные логи остаются в артефактах.
Критические motion/STOP/interlock/data/provenance инварианты сохраняются.
