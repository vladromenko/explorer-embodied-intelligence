# Explorer — атомарный backlog

Приоритет владельца (последнее указание): **04 implemented +software-tested +deployed →03 software/deploy →05 →06 →01**.
После checkpoint04 обязательно возобновить `E03-SOFTWARE-DEPLOY`, не переходить к05.
Физическое acceptance ведётся отдельно; требования не завершены одним checkpoint.
Организационный checkpoint не меняет этот порядок. `DONE` относится только к
конкретному критерию строки, не к целому опыту/master. Физические gates отдельны.

Статусы: `DONE`, `IN_PROGRESS`, `OPEN`, `WAIT_USER`, `BLOCKED`, `PARKED`.
Root выбирает один текущий ID; независимый bounded subtask не создаёт второго
integrator. При отсутствии полезного software-действия внутри gated опыта
переходить к следующему разрешённому software ID, сохраняя gate OPEN.

| ID | Scope | Depends | Done / evidence | Status | Next action |
|---|---|---|---|---|---|
|OPS-STATE|shared|none|AGENTS/state/backlog/function matrix/runbook/skill и лёгкий consistency check; verified original-doc backup|DONE|После отдельного организационного commit продолжить E04-IMPORT|
|E03-CHECKPOINT|03|none|Central-only65 actual release/full regression/GET UI, physical path CLOSED; [proof](../_registry/evidence/experiment03-central-data-actual-65ae9d02-20261027.json)|DONE|Сохранять separate physical gates|
|E03-BOOTSTRAP|03|E03-CHECKPOINT|Автоматический labelled existing-calibration/URDF bootstrap, command-estimate recenter, provisional gripper; [proof](../_registry/evidence/experiment03-bootstrap-existing-calibration-20261027.json)|DONE|Не требовать полного measured registration для software разработки|
|E03-CONTINUITY|03|E03-BOOTSTRAP|Real changing leader shadow before/after с dt/jitter/steps/derivatives/drop/zero/transitions, original stable ACTIVE preserved|WAIT_USER|Нужны changing real inputs и valid current reference; static100GET не PASS|
|E03-SOFTWARE-DEPLOY|03/shared|E04-RELEASE|Direct Pi→Nano route в main, existing real calibration+SO101/ROSMASTER URDF+servo_coordinates+current command_estimate bootstrap с честным provenance, без ложного UNREGISTERED; Pi/Nano endpoints, UI03 Start/Recenter/held heartbeat/STOP/record; deploy HB/Pi/Nano и бездвигательная связная проверка|OPEN|Обязательно возобновить после04 checkpoint; цель DEPLOYED_AWAITING_PHYSICAL_ACCEPTANCE, без firmware/config/servo-feedback изменений|
|E03-PHYSICAL-RELEASE|03|E03-CONTINUITY|Полный physical path qualified и отдельная малая observed trial, ARM_ONLY/mobile permissions раздельны|BLOCKED|Не обновлять03 physical runtime до continuity PASS|
|E02-REAL|02|none|Real expert demos/model, valid live-shadow и independent full-task trial с сохранёнными failures|WAIT_USER|Камера/task scene, fresh MCU/reference, gamepad и показы — PHYSICAL_CHECKS|
|E04-CAPTURE|04|E03-CHECKPOINT|Thin same-Station capture +shared review/Range +pause-aware converter; component [proof](../_registry/evidence/experiment04-node-capture-shared-HTTP-20261027.json)|DONE|Сохранять raw originals и typed scope|
|E04-PERMISSION|04/shared|E04-CAPTURE|Source-only process isolation и COMMON server demo identity, exact JPEG/actor/epoch; [API proof](../_registry/evidence/experiment04-central-demo-API-20261027.json)|DONE|Не применять legacy uncontrolled mutations в новом profile|
|E04-IMPORT|04/shared|E04-CAPTURE,E04-PERMISSION|Approved source archive→bounded immutable import→Store/worker, current review/archive pins, retry/cancel/Range, no legacy approval bypass; [145PASS/4SKIP](../_registry/evidence/experiment04-approved-import-worker-20261027.json)|DONE|Сохранить отдельные current review и outcome; продолжить E04-COHORT|
|E04-COHORT|04/shared|E04-IMPORT|Capture/review SHA входят в admission/fingerprint/aggregate;189contracts+officialDataset; actual ACT8/DP8 current math +approved capture queued2→exit→newPID6, exact reference/RNG, DEV/offline ACT shadow; [proof](../_registry/evidence/experiment04-approved-capture-queued-training-20261027.json)|DONE|Current generated-data software core qualified; продолжить E04-UI, physical source remains OPEN|
|E04-UI|04|E04-PERMISSION,E04-IMPORT,E04-COHORT|One scene/task/connect/teleop/record pause cancel preview approve reject/train versions/replay/shadow flow, ownership cleanup и mobile UI|DONE|Actual SourceDemo desktop/mobile +approved cohort→UI ACT8/DP8/queue pause/resume/reload/DEV/current model cards [PASS](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json); no new deploy/physical acceptance; продолжить VISION|
|E04-VISION|04/perception|E04-UI|Existing Logitech/cache VLM/grid/click/tabletop capabilities have real UI/backend and honest availability; no autonomous grasp claim|DONE|Actual Pi Qwen31.576s/CV recovery, exact JPEG/click/grid400 +new idle-only admission147Python/272Node/actual generated Firefox [proof](../_registry/evidence/experiment04-vision-source-admission-20261027.json); guard deployment и physical lighting/metric calibration OPEN|
|E04-FIRST-UPGRADE|04/deploy|E04-UI|First old4c teardown защищён доказанным admission/drain либо отдельно наблюдаемым USER upgrade; source-only Pi profile активен|IN_PROGRESS|Cold guard34c5f10/73Python [PASS](../_registry/evidence/experiment04-cold-publication-guard-20261027.json), Pi stdlib fake all-stop/read-only snapshot PASS. Далее exactold4c fake/partialHTTP и active/raw-owner negatives; actual43382 не трогали. До доказанного barrier не рестартовать; fallback observed USER upgrade. Old [race proof](../04-so101-source-learning/docs/OLD4C_LIFECYCLE_PROOF_20261027_RU.md)|
|E04-RELEASE|04/shared|E04-UI,E04-VISION,E04-FIRST-UPGRADE|Frozen current integration/full regression, scoped deployment, actual authenticated UI/GET verification/rollback evidence|BLOCKED|Сначала закончить независимо доступное ПО; один full перед release|
|E04-REAL|04|E04-RELEASE|3–5 technical smoke, затем покрытие Spec и независимая physical source skill acceptance; не обещать mastery по числу demos|WAIT_USER|Питание/calibration/scene, реальные показы и observed model trial|
|E05-MANUAL|05|E04-RELEASE,E03-SOFTWARE-DEPLOY|Reuse check; manual/manual first, full FSM opening→hold→release→transfer→finish/park, two cameras, STOP/ownership/recovery/data UI|OPEN|Завершить04 software; не считать FSM learned skill|
|E05-LEARNING|05/shared|E05-MANUAL|Two original clocks/actions/handshake/grasp/release/outcome→review/dataset/train/reload/shadow через shared02/04 core|OPEN|Тонкий coordinator/producer adapter, не новый recorder/trainer|
|E05-RELEASE|05|E05-LEARNING|Software/operator-ready flow deployed, software evidence полный; real hold/transfer gates остаются отдельными|OPEN|Frozen regression/deploy только после полной интеграции|
|E05-REAL|05|E05-RELEASE|Shared-zone visibility и independent hold/release/full handover observed acceptance|WAIT_USER|Максимум две реальные камеры; physical double-hold не выводить из receipts|
|E06-PIPELINE|06|E04-COHORT,E05-LEARNING|Approved immutable04 source lineage, distinct domains, spatial/FK/object-relative→target registration/IK/reachable/SIM UI|OPEN|Reuse audit и thin target adapter; labelled fixtures разрешены для code qualification|
|E06-ADAPT|06|E06-PIPELINE|5/10/25 target-demo UI; zero/adapt/target-only сравнения→save/reload/offline/shadow, no sealed optimizer leakage|OPEN|Один shared training/eval backend; physical transfer success отдельно|
|E06-RELEASE|06|E06-ADAPT|Software/operator-ready pipeline deployed с provenance/unknown/failures; не фиктивный successful source/target skill|OPEN|Current regression и честный rollout checklist|
|E06-REAL|06|E06-RELEASE,E04-REAL|Accepted real source, measured workspace/target calibration и observed independent transfer|WAIT_USER|Не переносить action head или считать camera domains одинаковыми|
|E01-REPAIR|01|E04-RELEASE,E05-RELEASE,E06-RELEASE|Canonical PPO→admitted demos/DAgger→ACT full-task threshold и доказанный targeted repair|PARKED|Не запускать long248; bounded8+8 и отрицательные исходы сохранены|
|E01-RELEASE|01|E01-REPAIR|Reproducible independent SIM full-task, UI/model provenance и disarmed optional deploy preparation|PARKED|Не заменять sock/kitchen target простым reach claim|
|OPS-RECOVERY|shared|E04-IMPORT|Проверенные backup/restore raw datasets/models/calibs, outbox/partial-transfer/process restart без replay grant|OPEN|Reuse existing restore tools; metadata backup не full SSD backup|
|SCI-HYPOTHESES|research|E05-LEARNING,E06-ADAPT,E01-REPAIR|H1–H5/retention/transfer/recovery, preregistered splits, all outcomes и figures from actual data|PARKED|Сохранить исходные protocols/benchmark, no novelty claim по infrastructure|
|SCI-PAPER|research|SCI-HYPOTHESES|100+ primary literature/provenance, reproducible article/supplement, offline build/visual review|PARKED|Paper branch a5865cc сохранена, submission_ready=false|

Зависимости — acceptance dependencies, а не причина простаивать при доступной
независимой работе.04/05/06 можно квалифицировать на явно labelled generated
inputs до real successful demos, но software-ready требует целого deployed flow.
Требования и все прежние gates: [GOALS](../GOALS.md), [machine acceptance](../_registry/acceptance.json).
