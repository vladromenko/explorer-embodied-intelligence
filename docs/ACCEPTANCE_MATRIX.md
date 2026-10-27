# Приёмка пользовательских функций

Snapshot: product code `cad7928`; live Homebrain65/NanoOWN722/Pi4c (точные commits
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
|E03-RETARGET · SO101→ROSMASTER/bootstrap|YES|PASS|PARTIAL|NOT_DEPLOYED|PARTIAL|OPEN|[Bootstrap original calibration](../_registry/evidence/experiment03-bootstrap-existing-calibration-20261027.json)|Code bootstrap не measured pose; new physical runtime не deployed|
|E03-RECENTER · held/relative reference/ownership|YES|PASS|PARTIAL|NOT_DEPLOYED|PARTIAL|OPEN|[Hold/continuity contract](../03-lerobot-teaching/docs/CONTINUITY_ACCEPTANCE_RU.md)|Changing real trace и current reference; no absolute first-goal/gripper jump|
|E03-SMOOTHNESS · неизменная stable trajectory|PARTIAL|PARTIAL|NOT_DEPLOYED|NOT_DEPLOYED|NOT_DEPLOYED|OPEN|[Actual static trace only](../03-lerobot-teaching/docs/CONTINUITY_20261027.json)|Real changing-input frequency/dt/jitter/derivatives/drop/zero comparison OPEN|
|E03-MOBILE-RECORD · arm+keyboard base/camera/record|PARTIAL|PASS|PARTIAL|NOT_DEPLOYED|PARTIAL|OPEN|[Candidate39RGB](../_registry/evidence/experiment03-native-capture-candidate-0d91dff2-20261027.json)|39RGB/0expert commands private candidate; chassis never moved|
|E03-TRAIN · official Dataset/ACT/reload/DEV|YES|PASS|PARTIAL|N/A|N/A|OPEN|[Generated03 queueR3](../03-lerobot-teaching/docs/NATIVE03_QUEUE_CPU_R3_20261027.json)|No real expert03 model; actual canonical data UI deployed, generated proof отдельный|
|E04-CAPTURE · две руки/одна Logitech/full skill capture|YES|PASS|NOT_DEPLOYED|NOT_DEPLOYED|N/A|OPEN|[Actual SourceDemo UI](../_registry/evidence/experiment04-source-demo-actual-UI-20261027.json)|Actual central/Node/original recorder, generated JPEG/fake buses; desktop/mobile full capture/review scoped proof. First oldPi upgrade/release OPEN|
|E04-TRAIN · native6 Dataset/ACT+DP/resume/models|YES|PASS|PARTIAL|NOT_DEPLOYED|N/A|OPEN|[Actual learning UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json)|Approved generated cohort→actual UI ACT8/DP8/queue retained-process pause/reload/DEV/model versions; exact new-PID отдельный R7 proof. Deployment/current real experts OPEN|
|E04-REPLAY · reload/offline/live shadow/observed trial|PARTIAL|PARTIAL|PARTIAL|PARTIAL|N/A|OPEN|[Source shadow](../04-so101-source-learning/docs/LIVE_SHADOW_TRANSPORT_RU.md)|Новая модель из current approved cohort и полный UI; no object skill claim|
|E05-HANDOVER · manual/manual FSM, два робота|PARTIAL|PARTIAL|PARTIAL|PARTIAL|PARTIAL|OPEN|[Receiver interlocks](../05-cooperative-handover/docs/RECEIVER_INTERLOCK_TRANSPORT_RU.md)|Opening→independent transfer→finish/park+data path/UI/release, real shared zone|
|E05-LEARNING · two-clock capture/train/reload/shadow|PARTIAL|PARTIAL|PARTIAL|PARTIAL|PARTIAL|OPEN|[Queued native shadows](../05-cooperative-handover/docs/QUEUED_NATIVE_SHADOW_RU.md)|Whole approved dual data pipeline; FSM не learned skill|
|E06-TRANSFER · distinct-domain source→target/adapt|PARTIAL|PARTIAL|PARTIAL|PARTIAL|PARTIAL|OPEN|[Negative full-task0/16](../research/audits/TARGET_TEMPORAL_C_V2_RESULTS_RU.md)|Approved04 source, registration/UI/5-10-25 target comparisons, full release|
|E01-AUTONOMY · PPO→demos/DAgger→ACT full task|PARTIAL|PARTIAL|PARTIAL|N/A|NOT_DEPLOYED|N/A|[Bounded smoke+PARKED](../01-autonomous-learning/docs/audits/PARKED_AFTER_SMOKE8_20261027_RU.md)|Canonical learned full-task не достигнут; long tuning PARKED после04–06|

Для01 N/A означает отсутствие physical acceptance у SIM-обучения; optional
physical deployment остаётся отдельным OPEN gate в машинной матрице.04–06 PARTIAL
в deployment — старые scoped components, а не новый полный операторский flow.
