# Приёмка пользовательских функций

Snapshot: product code `9766bc3`; live Homebrain9766bc3/NanoOWNc516/Pi c516 shared lab (точные commits
в [PROJECT_STATE](PROJECT_STATE.md)). Это function-level view;
[машинные gates/исторические scope](../_registry/acceptance.json) сохранены.
Новая документация не повышает готовность. `PASS` software может относиться к
labelled generated/mock/SIM input, что явно записано в Evidence. Ни одна строка
не означает принятого физического полного навыка.

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
|E04-CAPTURE · две руки/одна Logitech/full skill capture|YES|PASS|DEPLOYED|DEPLOYED|N/A|OPEN|[Actual04 release](../_registry/evidence/experiment04-source-release-actual-ef3f5185-20261028.json)|Generated full capture/review +actual HTTPS desktop/mobile/real JPEG/COMMON guard; motor power OFF, real experts0, physical scene/skill OPEN|
|E04-TRAIN · native6 Dataset/ACT+DP/resume/models|YES|PASS|DEPLOYED|DEPLOYED|N/A|OPEN|[Actual learning UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json)|Approved generated cohort→actual UI ACT8/DP8/queue retained-process pause/reload/DEV/model versions; exact new-PID отдельный R7 proof. Math workers on HB, source adapter on Pi; current real experts/physical skill OPEN|
|E04-REPLAY · reload/offline/live shadow/observed trial|YES|PASS|DEPLOYED|DEPLOYED|N/A|OPEN|[Current learning/model UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json)|Current generated models/reload/DEV/offline shadow +COMMON observed source transport qualified; actual real model/physical trial OPEN, motor power OFF|
|E05-HANDOVER · manual/manual FSM, два робота|PARTIAL|PARTIAL|PARTIAL|PARTIAL|PARTIAL|OPEN|[Receiver interlocks](../05-cooperative-handover/docs/RECEIVER_INTERLOCK_TRANSPORT_RU.md)|Opening→independent transfer→finish/park+data path/UI/release, real shared zone|
|E05-LEARNING · two-clock capture/train/reload/shadow|PARTIAL|PARTIAL|PARTIAL|PARTIAL|PARTIAL|OPEN|[Queued native shadows](../05-cooperative-handover/docs/QUEUED_NATIVE_SHADOW_RU.md)|Whole approved dual data pipeline; FSM не learned skill|
|E06-TRANSFER · distinct-domain source→target/adapt|PARTIAL|PARTIAL|PARTIAL|PARTIAL|PARTIAL|OPEN|[Negative full-task0/16](../research/audits/TARGET_TEMPORAL_C_V2_RESULTS_RU.md)|Approved04 source, registration/UI/5-10-25 target comparisons, full release|
|E01-AUTONOMY · PPO→demos/DAgger→ACT full task|PARTIAL|PARTIAL|PARTIAL|N/A|NOT_DEPLOYED|N/A|[Bounded smoke+PARKED](../01-autonomous-learning/docs/audits/PARKED_AFTER_SMOKE8_20261027_RU.md)|Canonical learned full-task не достигнут; long tuning PARKED после04–06|

Для01 N/A означает отсутствие physical acceptance у SIM-обучения; optional
physical deployment остаётся отдельным OPEN gate в машинной матрице.04–06 PARTIAL
в deployment — старые scoped components, а не новый полный операторский flow.
