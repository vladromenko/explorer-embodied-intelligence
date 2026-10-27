# Принятые архитектурные решения

Текущие факты не хранятся здесь: [PROJECT_STATE](PROJECT_STATE.md).
История прежнего root DECISIONS сохранена в Git `f99ff7e:DECISIONS.md` и
[проверяемом inventory](../_registry/evidence/project-state-inventory-20261027.json).

| ID | Решение и основание |
|---|---|
|D-REPO|Один private monorepo, canonical checkout Homebrain, immutable releases и отдельные worktrees. Root — единственный integrator/deployer. Mac/чат не runtime dependency|
|D-NUMBERING|Единственный mapping01 autonomous,02 keyboard/gamepad ROSMASTER,03 leader→ROSMASTER,04 SO101 source,05 handover,06 cross-embodiment. Старые raw IDs/SHA не переписываются; [mapping/projection](../research/audits/EXPERIMENT_NUMBERING_V2_RU.md)|
|D-REALTIME|03 realtime — напрямую Pi→Nano; Homebrain — control/data/learning plane, не сетевой посредник каждого движения. [Исходный route](../03-lerobot-teaching/docs/DIRECT_PI_NANO_RU.md)|
|D-EXECUTOR|ROSMASTER использует прежние ManualArm/Ruckig/interpolation/publisher/lease; Pi — frozen Station/servo loop. Новые guards только у существующих владельцев, не второй исполнитель.6configs/firmware/feedbackOFF сохраняются|
|D-STATE|`command_estimate ≠ measured_state`; host publication/HTTP receipt ≠ wire ACK/attainment/contact. Native9 и native6, source/target camera/kinematics и clocks различаются|
|D-BOOTSTRAP|03 known calibration/URDF/factory mapping дают labelled bootstrap без отдельного measured registration. Source nominal CAD excess — diagnostic без clipping; current clutch от command estimate. Observed axes/gripper/metric accuracy отдельны. [Proof](../03-lerobot-teaching/docs/BOOTSTRAP_REGISTRATION_RU.md)|
|D-CAMERAS|Ровно2 физические камеры: Nano Orbbec DaBai DCW2 CH82C5200KB RGB+optional depth — одно устройство; Pi Logitech — одно. `pi-overview` legacy alias той же Logitech. Один capture owner+cache/fanout; no scene/sync/viewpoint equivalence inference. [Inventory](../_registry/camera-inventory-20261027.json)|
|D-SHARED|Один shared lifecycle/review/SHA/Range/timeline/split/Dataset/queue/model/eval/auth/ownership core; thin adapters по experiment. Новое общее поведение проверяется для обоих consumers; без копий целых pipelines|
|D-OWNER|Grant/actor/exact shown JPEG/boot/calib/returned epoch связаны. Auto-cleanup только acknowledged own scope; явный USER global STOP сохраняет original ABI. Permission loss не возобновляет old grant. Double-hold WAL переживает STOP/restart/corruption; отдельный безопасный release. [Source boundary](../04-so101-source-learning/docs/SOURCE_RUNTIME_BOUNDARY_RU.md)|
|D-QUEUE|Shared durable Supervisor/resources сериализует тяжёлые задания и один inference slot. Exact source/runtime/shape pins; ACT/DP model+optimizer+RNG+sampler сохраняются на durable pause→exit75→new PID resume. SIGSTOP memory retained не называется освобождением RAM/exact resume. [Queue](../shared/training/DURABLE_QUEUE_RU.md)|
|D-EXPORT|Scientific export копирует только выбранные проверенные bytes и честно сообщает PARTIAL. Не запускает архивный код и не публикует auth/rawSQLite. Metadata backup не заменяет raw data/models/full-disk backup|
|D-CLAIMS|Mock/SIM/software/deployed/physical/scientific novelty — разные claims. Все failures/unknown/setup failures сохраняются. FSM не learned skill; loss не task success; source train не target qualification|
|D-PI-UPGRADE|IdleGET и daemon server_close не доказывают zero-write drain старого4c; conditional idle close и actual fake race сохранены. Первый unattended Pi update OPEN. [Контрпример](../04-so101-source-learning/docs/OLD4C_LIFECYCLE_PROOF_20261027_RU.md)|
|D-VLM-ADMISSION|Actual Pi Qwen inference31.576s/CV recovery подтверждены, native initialization дал ReadTimeout6s. Новый source-only04 profile допускает VLM только idle/no recording и блокирует COMMON native entry до actual release, включая cancel/unknown. Scoped/global STOP ABI сохранены.147Python/272Node и actual generated Firefox; не latency-free/physical/semantic-accuracy claim. [Proof](../_registry/evidence/experiment04-vision-source-admission-20261027.json)|
|D-PRIORITY|После03 safe checkpoint:04→05→06→01.01 long training PARKED; full248 smoke continuation не запускать. Инфраструктурная организация не меняет эту очередь|
|D-STATE-DOCS|AGENTS→PROJECT_STATE→BACKLOG→Git+evidence — recovery entrypoint. Один короткий state и один приоритетный backlog; root STATUS/PLAN/DECISIONS/PHYSICAL_CHECKLIST — compatibility pointers, старые тексты остаются в Git/verified backup|

## Последнее указание порядка:04 →03 →05 (2026-10-27)

Не прерывая текущий атомик04, довести04 до implemented +software-tested +deployed.
Сразу после04 checkpoint возобновить `E03-SOFTWARE-DEPLOY`: direct Pi→Nano, honest
existing-calibration/URDF/servo mapping/current-command-estimate bootstrap, endpoints,
UI03 и HB/Pi/Nano deploy с бездвигательной связной проверкой.05 начинается после
закрытия этих software gaps03. Физическое acceptance остаётся отдельным; целевое
состояние03 DEPLOYED_AWAITING_PHYSICAL_ACCEPTANCE. Firmware, исходные настройки
ROSMASTER и проблемный servo feedback не менять. Gate shutdown/drain не обходить.
