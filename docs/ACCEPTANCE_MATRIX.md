# Приёмка пользовательских функций

Snapshot: product code `c82e675`; live Homebrain/Pi/NanoOWN/originalWEBpin c82 shared lab/handover (точные commits
в [PROJECT_STATE](PROJECT_STATE.md)). Это function-level view;
[машинные gates/исторические scope](../_registry/acceptance.json) сохранены.
Новая документация не повышает готовность. `PASS` software может относиться к
labelled generated/mock/SIM input, что явно записано в Evidence. Ни одна строка
не означает принятого физического полного навыка.

Обязательная политика: [MVP-FIRST / PHYSICAL-FEEDBACK-FIRST](../AGENTS.md).
Критерий MVP: shortest end-to-end operator path, targeted tests, no-motion smoke,
working deployment → `MVP_USER_TESTABLE` / `DEPLOYED_AWAITING_PHYSICAL_ACCEPTANCE`.
Отдельные [MVP BLOCKERS и DEFERRED HARDENING](BACKLOG.md) обязательны для каждой
задачи01–06 и будущих задач. Deferred не является release gate, кроме фактических
исключений политики. [Конкретные10 пунктов04](MVP_STATUS_RU.md); physical OPEN.

**Текущий USER acceptance02–04: FAIL, повторная проверка идёт.** Simple UI и
исправления02/03/04 deployed; Homebrain+Pi exact de84b48 активны.04 фактически
входил в teleoperation и отправлял6-axis команды, но добавленный2s web deadline
сбрасывал сессию. de84 убрал этот lease после явного manual start; версия
deployed и ждёт длительного USER retest/recording. Исторические PASS ниже
относятся к отдельным components; реальный launch не объявлен готовым без новых
USER-observed движений/записи.06 приостановлен пользователем.

Фиксированные уровни: Implemented=`YES/PARTIAL/NO/N/A`;
Software-tested=`PASS/PARTIAL/NOT_TESTED/N/A`;
Deployed Homebrain/Pi/Nano=`DEPLOYED/PARTIAL/NOT_DEPLOYED/N/A`;
Physical acceptance=`PASS/OPEN/N/A`. PARTIAL означает только scope из ссылки.

| Function | Implemented | Software-tested | Deployed Homebrain | Deployed Pi | Deployed Nano | Physical acceptance | Evidence/link | Blocker/next action |
|---|---|---|---|---|---|---|---|---|
|E02-KEYBOARD · рука/захват/STOP с клавиатуры|YES|PASS|DEPLOYED|N/A|DEPLOYED|OPEN|[02 software](../_registry/evidence/experiment02-learning-actual-722755dc-20261027.json)|Fresh MCU/reference и отдельный observed trial; chassis запрещено|
|E02-JOYSTICK · detection/calibration/input preview|YES|PASS|DEPLOYED|N/A|DEPLOYED|OPEN|[Browser fixture47samples](../_registry/evidence/experiment02-learning-actual-722755dc-20261027.json)|Настоящий Mac gamepad не квалифицирован; fixture не physical|
|E02-CAMERA · реальная Nano сцена|YES|PASS|DEPLOYED|N/A|DEPLOYED|OPEN|[Actual HTTPS JPEG](../_registry/evidence/experiment03-central-data-actual-65ae9d02-20261027.json)|Кадры меняются, но gripper/object/goal scene visibility не подтверждена|
|E02-RECORD · pause/cancel/review/approved transfer|YES|PASS|DEPLOYED|N/A|DEPLOYED|OPEN|[Actual17RGB analysis](../_registry/evidence/experiment02-learning-actual-722755dc-20261027.json)|Real expert commands0; собрать полные показы|
|E02-TRAIN · Dataset/ACT/resume/reload/DEV/shadow UI|YES|PASS|DEPLOYED|N/A|DEPLOYED|OPEN|[Generated queueR5](../shared/training/NATIVE02_QUEUED_CPU_R5_20261027_QUALIFICATION.json)|Real expert model/valid live-shadow отсутствуют|
|E03-RETARGET · SO101→ROSMASTER/bootstrap|YES|PASS|DEPLOYED|DEPLOYED|DEPLOYED|OPEN|[Actual gated lab release](../_registry/evidence/experiment03-shared-lab-actual-release-20261028.json)|Gated software deployed; physical Start/Recenter/held CLOSED, changing continuity/reference409/scene OPEN. Actual41RGB observations/0expert commands, not accepted skill|
|E03-RECENTER · held/relative reference/ownership|YES|PASS|DEPLOYED|DEPLOYED|DEPLOYED|OPEN|[Actual gated lab release](../_registry/evidence/experiment03-shared-lab-actual-release-20261028.json)|Gated software deployed; physical Start/Recenter/held CLOSED, changing continuity/reference409/scene OPEN. Actual41RGB observations/0expert commands, not accepted skill|
|E03-SMOOTHNESS · неизменная stable trajectory|PARTIAL|PARTIAL|NOT_DEPLOYED|NOT_DEPLOYED|NOT_DEPLOYED|OPEN|[Actual static trace only](../03-lerobot-teaching/docs/CONTINUITY_20261027.json)|Real changing-input frequency/dt/jitter/derivatives/drop/zero comparison OPEN|
|E03-MOBILE-RECORD · arm+keyboard base/camera/record|YES|PASS|DEPLOYED|DEPLOYED|DEPLOYED|OPEN|[Actual gated lab release](../_registry/evidence/experiment03-shared-lab-actual-release-20261028.json)|Gated software deployed; physical Start/Recenter/held CLOSED, changing continuity/reference409/scene OPEN. Actual41RGB observations/0expert commands, not accepted skill|
|E03-TRAIN · official Dataset/ACT/reload/DEV|YES|PASS|DEPLOYED|N/A|N/A|OPEN|[Generated03 queueR3](../03-lerobot-teaching/docs/NATIVE03_QUEUE_CPU_R3_20261027.json)|Current generated03 queueR3/profiles unchanged; actual41RGB analysis-only import shown in UI, real experts/models/independent physical trial OPEN|
|E04-CAPTURE · две руки/одна Logitech/full skill capture|YES|PASS|DEPLOYED|DEPLOYED|N/A|OPEN|[Actual04 release](../_registry/evidence/experiment04-source-release-actual-ef3f5185-20261028.json)|USER real grant/start/6-axis dispatch observed; custom2s lease identified as reset cause and removed for started manual session in de84b48. >10s control + start/finish recording still OPEN|
|E04-TRAIN · native6 Dataset/ACT+DP/resume/models|YES|PASS|DEPLOYED|DEPLOYED|N/A|OPEN|[Actual learning UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json)|Approved generated cohort→actual UI ACT8/DP8/queue retained-process pause/reload/DEV/model versions; exact new-PID отдельный R7 proof. Math workers on HB, source adapter on Pi; current real experts/physical skill OPEN|
|E04-REPLAY · reload/offline/live shadow/observed trial|YES|PASS|DEPLOYED|DEPLOYED|N/A|OPEN|[Current learning/model UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json)|Current generated models/reload/DEV/offline shadow +COMMON observed source transport qualified; actual real model/physical trial OPEN, motor power OFF|
|E05-HANDOVER · manual/manual FSM, два робота|YES|PASS|DEPLOYED|DEPLOYED|DEPLOYED|OPEN|[Actual scoped release](../05-cooperative-handover/docs/SCOPED_RELEASE_RU.md), [manual10FSM](../05-cooperative-handover/docs/WHOLE_MANUAL_FLOW_RU.md)|Software/deploy qualified; actual no-session page/two real cameras. Whole populated flow uses generated fixtures; shared zone/reference/independent physical hold/transfer OPEN|
|E05-LEARNING · two-clock capture/train/reload/shadow|YES|PASS|DEPLOYED|DEPLOYED|DEPLOYED|OPEN|[Actual scoped release](../05-cooperative-handover/docs/SCOPED_RELEASE_RU.md), [paired learning](../05-cooperative-handover/docs/WHOLE_LEARNING_FLOW_RU.md)|Generated originals/ACT2+ACT2/reload/DEV/offline6D and actual isolated saved-model UI qualified. Production has no paired expert dataset/models; SIMULATION live-shadow blocked, physical skill OPEN|
|E06-TRANSFER · distinct-domain source→target/adapt|PARTIAL|PARTIAL|PARTIAL|PARTIAL|PARTIAL|OPEN|[Actual generated source+geometry/UI](../_registry/evidence/experiment06-generated-actual-source-geometry-UI-20261028.json), [negative full-task0/16](../research/audits/TARGET_TEMPORAL_C_V2_RESULTS_RU.md)|Native6 ACT2+original calibration+whole geometry UI qualified, NOTdeployed. Generated target9 ACT2/ACT2+default02/03 parity+[whole target UI](../_registry/evidence/experiment06-actual-generated-target-learning-20261028.json) qualified. Native physical-declared writer+central DATA lifecycle/review/import/aggregate UI implemented and targeted-tested; whole-browser smoke/deploy next. SIM producer, physical registration/real5-10-25/full06 release OPEN|
|E01-AUTONOMY · PPO→demos/DAgger→ACT full task|PARTIAL|PARTIAL|PARTIAL|N/A|NOT_DEPLOYED|N/A|[Bounded smoke+PARKED](../01-autonomous-learning/docs/audits/PARKED_AFTER_SMOKE8_20261027_RU.md)|Canonical learned full-task не достигнут; long tuning PARKED после04–06|

Для01 N/A означает отсутствие physical acceptance у SIM-обучения; optional
physical deployment остаётся отдельным OPEN gate в машинной матрице.06 PARTIAL
в deployment относится к прежним components;05 scoped software flow released, physical OPEN.
