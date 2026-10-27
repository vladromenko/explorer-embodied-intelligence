# Explorer — актуальное состояние

Updated: `2026-10-27T18:47:05+03:00`
Code commit: `f99ff7e627639f34a17fe46f8bec635e6767fb59`
Current task: `E04-IMPORT`
Next tasks: `E04-IMPORT`, `E04-COHORT`, `E04-UI`
Overall: **IN_PROGRESS; исходный master-prompt не завершён.**

Это единственный краткий текущий статус. Commit выше — последний проверенный
product checkpoint; commit этой документации виден в `git log -- docs/PROJECT_STATE.md`.
Детали функций — [ACCEPTANCE_MATRIX](ACCEPTANCE_MATRIX.md), порядок —
[BACKLOG](BACKLOG.md), машинные научные gates — [acceptance.json](../_registry/acceptance.json).

Последний milestone: `f99ff7e` — authenticated API04: confirmed SkillSpec/session/
variant→server demo attempt→exact JPEG/COMMON permission→original teleop и
record/pause/resume/finish UNKNOWN→separate approve.23API/media tests PASS10.75s,
15source-boundary tests PASS2.09s на localhost/fake buses; **не реальный показ**.
[Evidence](../_registry/evidence/experiment04-central-demo-API-20261027.json).
Approved import/aggregate/UI ещё не замкнуты; finish пока не создаёт import job.

## Фактический deploy

Homebrain current+worker: **65ae9d021796c5ec4c9cab8a129fdb827dbf36f3**.
Nano OWN observer/cache: **722755dc99094fc86bab20796d2b7bc4bf56d536**.
Pi OWN и original Nano web import pin: **4c5f4f5b0fe4e37a8a66bf3643f0f696c0232db4**.
При организационной инвентаризации actual local `/api/health` подтвердил65,
production active jobs=0. Новые04 и bootstrap03 **не deployed на Pi**.
Последний deployed frozen full:2271PASS/20SKIP/855.56s,243Node на тех же JS bytes;
actual HTTPS Firefox desktop/mobile, passiveROS20s0, source/reference/PIDs/6configs
сохранены. Это scope выпуска65, не full regression позднейшего main.
[Release](../_registry/evidence/experiment03-central-data-actual-65ae9d02-20261027.json) ·
[Инвентаризация/backup](../_registry/evidence/project-state-inventory-20261027.json).

| Опыт | Что подтверждено | Остаток и scope |
|---|---|---|
|01 PARKED|CAD/SIM, компоненты ACT/DP/reload; bounded8+8 smoke сохранены|Canonical PPO→demos/DAgger→ACT full-task успех не достигнут. Target C-v2=0/16; long248 не запускался. [Pause](../01-autonomous-learning/docs/audits/PARKED_AFTER_SMOKE8_20261027_RU.md)|
|02|SOFTWARE READY в scope одной Nano camera; actual17RGB→analysis, generated Dataset/ACT/resume/DEV, UI/deploy|0принятых real experts/models; valid live-shadow/full physical skill OPEN. [02 release](../_registry/evidence/experiment02-learning-actual-722755dc-20261027.json)|
|03|Shared data path/UI deployed65; candidate actual39RGB/0expert commands отдельно; bootstrap из исходной калибровки реализован/tested|Не SOFTWARE READY: changing real leader continuity OPEN, current reference409; новый physical path не deployed. Measured registration не блокирует bootstrap-разработку. [Bootstrap](../03-lerobot-teaching/docs/BOOTSTRAP_REGISTRATION_RU.md)|
|04 CURRENT|Source-only profile, COMMON demo permission, same Station capture/review/Range API; shared pause-aware converter tested|Approved worker/cohort/officialDataset текущего полного пути, UI и release OPEN. Existing native6 ACT/DP/resume — component proof. [Capture](../04-so101-source-learning/docs/CAPTURE_ADAPTER_CONTRACT_RU.md)|
|05 NEXT|Typed two-role components, hold WAL/interlocks и shared queued shadows tested|Полный manual/manual операторский FSM→record/review/train/shadow/release ещё открыт. FSM не learned skill. [Interlock](../05-cooperative-handover/docs/RECEIVER_INTERLOCK_TRANSPORT_RU.md)|
|06 THEN|Lineage/splits/FK/IK/SIM и ACT/adaptation компоненты; отрицательные исходы сохранены|Approved04 source и полный distinct-domain zero/adapt/target-only UI/pipeline OPEN; physical transfer не доказан. [Negative trials](../research/audits/TARGET_TEMPORAL_C_V2_RESULTS_RU.md)|

## Блокеры и ближайшие действия

- `E04-IMPORT`: review/integrate prepared archive consumer `f665951` на
  `work/04-archive-consumer` (40fixture tests, ещё unmerged); затем Store/
  worker guards, approval pins и provenance. Не выдавать unmerged branch за main.
- `E04-COHORT`: провести sealed pause envelope через accepted cohort и настоящий
  LeRobotDataset→ACT/resume/reload→independent DEV/shadow на labelled software inputs.
- `E04-UI`: связать новый flow, counters/coverage/review/models и scoped tab cleanup.
- Старый Pi4c имеет воспроизведённую shutdown race. Первый unattended Pi update
  OPEN: idleGET+server_close не drain. Нужен доказанный admission barrier либо
  отдельный наблюдаемый первый upgrade; пока продолжаем программную работу.
  [Proof](../04-so101-source-learning/docs/OLD4C_LIFECYCLE_PROOF_20261027_RU.md).
- Current native reference409/MCU stale, физическая рабочая сцена не подтверждена.
  Не делать HOME/grants/движение агентом. [Только действия владельца](PHYSICAL_CHECKS.md).

Согласованный порядок: **03 safe checkpoint →04 →05 →06 →01**.03 checkpoint уже
сохранён; его physical gates остаются OPEN.01 long tuning PARKED до04–06. Рабочий
repo на Homebrain; Mac/открытый чат не нужны runtime. Полный original master:
`~/.local/state/explorer-work/master-prompt.md`; private handoff — recovery pointer,
не второй канонический текущий статус. Историю требований/исходов не переписывать.

Организационный цикл внедрён: root AGENTS +canonical docs +repo skill +лёгкий
consistency check. Это инфраструктурный checkpoint; acceptance уровни не повышены.
