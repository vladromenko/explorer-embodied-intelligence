# Explorer — актуальное состояние

Updated: `2026-10-28T00:58:00+03:00`
Code commit: `16c82a89f619e60043700f7193c7e765ac43ddd3`
Current task: `E03-SOFTWARE-DEPLOY`
Next tasks: `E03-SOFTWARE-DEPLOY`, `E05-MANUAL`, `E05-LEARNING`
Overall: **IN_PROGRESS; исходный master-prompt не завершён.**

Это единственный краткий текущий статус. История требований/отказов и научные
originals сохранены. Подробности: [BACKLOG](BACKLOG.md),
[ACCEPTANCE_MATRIX](ACCEPTANCE_MATRIX.md), [physical queue](PHYSICAL_CHECKS.md),
[машинные scientific gates](../_registry/acceptance.json).

## Последний checkpoint04

**IMPLEMENTED +SOFTWARE_TESTED +DEPLOYED; physical acceptance OPEN.**
[Actual release evidence](../_registry/evidence/experiment04-source-release-actual-ef3f5185-20261028.json).
Это software/operator flow, не принятый предметный навык и не весь master.

- Single original frozen Station/recorder/camera owner; source-only04 profile,
  COMMON exact actor/JPEG/boot/epoch/Skill/session/demo binding. Legacy raw04
  mutations закрыты; explicit global STOP ABI сохранён, агент его не вызывал.
- Prepare→отдельное наблюдение→teleop→record/pause/resume/cancel/stage→finish
  UNKNOWN/failure/success→saved preview→separate approve/reject→immutable
  typed import/Range/retry. UNKNOWN не становится success; finish не импортирует.
- Approved generated cohort5originals (3train/1DEV/1sealed),8raw→7selected,
  official LeRobotDataset0.6.1:28frames/8segments. Actual ACT8/DP8 +reference8/
  pause2→oldPID exit→newPID6, model/optimizer/losses/sampler/all RNG exact.
  Real accepted experts0. [Cohort/queue proof](../_registry/evidence/experiment04-approved-capture-queued-training-20261027.json).
- Actual generated desktop/mobile capture and full approved learning/model UI,
  separate ACT/DP jobs/reload/DEV/offline shadow, no physical quality claim.
  [Whole learning UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json).
- CV/VLM uses existing Logitech/Hailo. Actual oldPi Qwen event31.576s recovered
  CV/camera; original6s poll timeout retained. New admission allows semantic only
  idle/no record and fences pending/inflight/unknown-release against native start;
  cancellation не означает release.147Python/272Node/generated actual Firefox.
  [Vision](../_registry/evidence/experiment04-vision-source-admission-20261027.json).
- Cold guard34c5f10 installed on SAME buses before original worker starts;
  no sync_write forwarded until first valid COMMON observed USER start.73Python
  +actual Pi3.13.5 full Node/frozen Station/fake buses PASS: forced idle error
  caused1blocked hold/0writes. Original Station SHA unchanged.
  [Cold Pi proof](../_registry/evidence/experiment04-cold-current-Pi-20261027.json).

## Фактический deploy

| Runtime | Actual commit / evidence |
|---|---|
|Homebrain central +worker|**ef3f5185c6199d93044fe54c791e2f67946a56cc**|
|Pi OWN observer, source-only04|**9fc1fb655493a97320ce3de310dd7eb012f0cc78**, PID62031|
|Nano OWN observer/cache|**722755dc99094fc86bab20796d2b7bc4bf56d536**|
|Original Nano web import pin|**4c5f4f5b0fe4e37a8a66bf3643f0f696c0232db4**|

USER явно подтвердил отключённое силовое питание обеих SO101 рук. При этом
условии первый old4c PID43382 завершён SIGKILL без Node.close/Station STOP,
после запрета auto-restart. Новый profile disarmed, grantNone, forwarded0;
Restart=on-failure возвращён после проверки. Original Pi node.env/arms/calibs/
unit file SHA сохранены; добавлен только own runtime/profile drop-in. SDK connect
сейчас ожидаемо unavailable при powerOFF; это не physical acceptance.
Actual GDB attach к рабочему observer не выполнялся.

New ef3f518 — только UI hotfix: deferred direct03/station timers больше не
обращаются к app globals до загрузки app.js.3новых delayed-load tests,275Node
PASS2.55s. **Все600 Python files byte-identical** frozen9fc, где полный
**2435PASS/22SKIP/878.97s**, kernelpeak4593295360B/swap0/OOM0. Production25jobs,
11skills, все исходные events и schema сохранены, activejobs0.

Actual authenticated HTTPS Firefox desktop1440/mobile390 PASS: real Pi JPEG,
ровно2existing camera inventories, no overflow,0JSerrors,0nonGET requests,
no owner/affirmations/grants. Фотография рабочего стола/части SO101 стала видимой;
это не доказательство полной object/goal scene visibility или metric calibration.
Passive ROS:60s при первом переходе и20s при UI hotfix, оба0actuator messages.
Original Nano source/reference/six configs/PIDs exact до/после, включая finalUI.

Отказы сохранены: early full467 interrupted; fullcad2434PASS/22SKIP/1FAIL old
current-GPU SHA. Fresh actual CUDA8 +reference8/pause4→exit/newPID4 квалифицировали
current16source bytes, reload0/model+optimizer+all RNG exact; DEV0/1 retained.
[Fresh CUDA](../_registry/evidence/act9-source04-current-20261027.json).
Current02/03 queues ACT4/reload0/DEV10 PASS, source pins verified.
[02R5](../shared/training/NATIVE02_QUEUED_CPU_R5_20261027_QUALIFICATION.json) ·
[03R3](../03-lerobot-teaching/docs/NATIVE03_QUEUE_CPU_R3_20261027.json).
01 long tuning/248 не возобновлялся.

Реальный HTTPS выявил startup ReferenceError, исправленный ef3f518. Затем mDNS
и GET3s timeout сорвали private cutover checks; старый one-off rollback helper
вернул только symlink при уже работающем ef3f518. Фактический health проверен,
current согласован без повторного restart. Live UI503 из-за `.local` устранён
изменением ТОЛЬКО own central nodes.host: Pi192.168.0.131/Nano192.168.0.177;
токены и остальные поля exact, private config backup retained. Это наблюдаемые
LAN/DHCP IP, не OS/router reservation. Final Firefox PASS после изменения.
Не переиспользовать старый rollback helper без исправления process reconciliation.

## Восстановление и границы

- Actual HB11981payloadfiles8,563,858,810B +metadata/private configs/calibs copied;
  isolated real restore/Store integrity/SHA PASS. Pi99files62,759,615B +online
  outbox SQLite snapshot saved/verified on Homebrain. SameSSD не защита от его
  потери; source/venv/OS и невыбранные remote paths не входят в полный image.
- Current supervisor/worker SIGKILL recovery сохраняет pinned source, outbox3
  lostACK/retry/dedup и two-process stale action/lease rejection PASS. Первый
  worker остановлен TEST-OWNED exec gate до bootstrap, scope явно записан.
  [Recovery](../_registry/evidence/experiment04-current-recovery-20261027.json).
- Old4c all-stop12fake cases: idle/partialHTTP kill0writes; active/raw/inflight
  rejected. Но unknown detach+expired fake serial deadline дал1idle hold.
  Это отрицательный исход безопасного abort, не qualified unattended barrier.
  Actual первый переход выполнен отдельно при USER-confirmed powerOFF.
  [Negative proof](../04-so101-source-learning/docs/FIRST_UPGRADE_ALLSTOP_20261027_RU.md).
- No real native grants/HOME/begin/resume/policy/action/STOP от агента, no chassis
  or arm motion. Original6 ROSMASTER configs/firmware/limits/watchdogs/feedbackOFF
  и frozen Station SHA2d191534…a885 сохраняются.

## Следующая обязательная задача03

04 checkpoint закрыт в software/deploy scope выше. **Сразу E03-SOFTWARE-DEPLOY,
не05**: direct Pi→Nano, original existing calibration/URDF/factory servo mapping/
current command_estimate bootstrap, endpoints и UI03, deploy/no-motor checks.
Цель — **DEPLOYED_AWAITING_PHYSICAL_ACCEPTANCE**. Measured manual registration
не блокирует bootstrap-разработку; derived pose не называется measured.
[Bootstrap evidence](../_registry/evidence/experiment03-bootstrap-existing-calibration-20261027.json).

Текущий Pi source-only profile намеренно не создаёт03peer/client/worker. Нужен
software integration/profile, сохраняющий04 capture/cold/semantic guards и
единственного Station owner при добавлении gated03 capabilities. Physical path
по-прежнему CLOSED: real changing leader continuity OPEN, current reference409,
сцена/метрическая привязка/реальные эксперты не приняты. Static100GET не PASS.
После03 software/deploy идут05→06→01; физический gate не причина бросать доступное ПО.

Candidate16c82a8 реализует explicit `so101_lab`: thin wrapper existing03 service,
COMMON/cold/capture04 на том же Station, source-claim fence для CV/VLM. Bootstrap
status/data доступны; physical03 start/recenter/lease/challenge/authorize закрыты
до peer entry. Global no-owner cleanup не объявляет native STOP delivered;
original STOP method/body unchanged.93Python PASS6.75s, actual localhost Node
HTTP оба profiles, source/evaluation/cooperative/original03 fake peer contracts.
Candidate не deployed: actual commits выше не менялись. Далее actual Pi3.13
isolated fake-I/O/bootstrap proof, endpoint/UI integration, frozen scoped release.
[Contract/evidence](../03-lerobot-teaching/docs/LAB_RUNTIME_CONTRACT_RU.md).

01 PARKED: current C-v2=0/16, canonical full-task mastery/DAgger/ACT не достигнут;
02 software ready в scope одной Nano camera, real experts/models/live physical
skill OPEN;05 full manual/manual→learning flow и06 distinct-domain pipeline
ещё открыты. Research originals/negative outcomes/protocols не переписаны.
Исходный полный master: `~/.local/state/explorer-work/master-prompt.md`.
AGENTS/runbook/repo skill обязательны. Mac не является runtime dependency.
