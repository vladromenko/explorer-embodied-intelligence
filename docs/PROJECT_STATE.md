# Explorer — актуальное состояние

Updated: `2026-10-27T22:19:51+03:00`
Code commit: `4993c6d83d97bca9a6056548b7a398c02d405ead`
Current task: `E04-FIRST-UPGRADE`
Next tasks: `E04-FIRST-UPGRADE`, `E04-RELEASE`, `E03-SOFTWARE-DEPLOY`
Overall: **IN_PROGRESS; исходный master-prompt не завершён.**

Это единственный краткий текущий статус. Commit выше — последний проверенный
product checkpoint; commit этой документации виден в `git log -- docs/PROJECT_STATE.md`.
Детали функций — [ACCEPTANCE_MATRIX](ACCEPTANCE_MATRIX.md), порядок —
[BACKLOG](BACKLOG.md), машинные научные gates — [acceptance.json](../_registry/acceptance.json).

Последний milestone: approved04 archive consumer `99eee52` интегрирован с typed
Store/worker, COMMON demo review API и inventory recovery.145PASS/4SKIP22.07s:
GET-only Range-resume4096B→same original3raw samples→2unpaused samples→official
LeRobotDataset0.6.1 native6 с точным readback; UNKNOWN не promoted. Поздний reject
закрывает admission; renewed approval создаёт отдельные immutable job/paths,
старые originals/review сохраняются. Негативные прогоны и generated fixtures retained.
[Evidence](../_registry/evidence/experiment04-approved-import-worker-20261027.json).
Approved cohort теперь проверен отдельно:189PASS/5SKIP42.33s,5generated originals
(3train/1DEV/1sealed),8raw→7selected, official28frames/8segments,0sealed payload.
Capture/review/provenance SHA входят в binding/fingerprint/aggregate; late reject
закрывает cached aggregate, reapproval сохраняет предыдущие artifacts.
[Evidence](../_registry/evidence/experiment04-approved-capture-cohort-20261027.json).
Полный operator learning UI/deploy ещё OPEN; это не готовность всего04.
Fresh frozen3b81623 ACT8/DP8 CPU qualification PASS с actual save/reload и прежними
R2 weights exact; native6 binding теперь содержит21 исходник, actual selector PASS.
[CPU evidence](../_registry/evidence/experiment04-current-native6-CPU-20261027.json).
Actual approved-capture queue proof теперь PASS:2tests/120.16s (qualifier124.52s),
ACT/DP каждый reference8 +pause2→oldPID exit→newPID6,32actual updates total,
model/optimizer/losses/sampler/Torch/Python/NumPy continuation exact; actual DEV
metrics и ACT offline shadow from saved official processors. Kernel peak3445071872B,
swap0/OOM0. [Queue proof](../_registry/evidence/experiment04-approved-capture-queued-training-20261027.json).
Новые source inputs отличаются от PROFILE4: weights сравниваются со своим actual
uninterrupted reference; прежняя math equality отдельно сохранена ACT8/DP8 выше.
R6/PROFILE4 originals immutable; current runtime/source pins проверяются полностью.


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
|04 CURRENT|Source-only/COMMON capture, approved cohort/ACT+DP resume; actual desktop/mobile SourceDemo HTTP+original recorder on labelled generated inputs|Полный learning/model UI и release OPEN; новый04 не deployed. [UI proof](../_registry/evidence/experiment04-source-demo-actual-UI-20261027.json)|
|05 NEXT|Typed two-role components, hold WAL/interlocks и shared queued shadows tested|Полный manual/manual операторский FSM→record/review/train/shadow/release ещё открыт. FSM не learned skill. [Interlock](../05-cooperative-handover/docs/RECEIVER_INTERLOCK_TRANSPORT_RU.md)|
|06 THEN|Lineage/splits/FK/IK/SIM и ACT/adaptation компоненты; отрицательные исходы сохранены|Approved04 source и полный distinct-domain zero/adapt/target-only UI/pipeline OPEN; physical transfer не доказан. [Negative trials](../research/audits/TARGET_TEMPORAL_C_V2_RESULTS_RU.md)|

## Блокеры и ближайшие действия

- `E04-IMPORT` DONE в software scope выше; archive/review/provenance pins обязательны.
- `E04-COHORT` DONE: accepted cohort/officialDataset/current ACT+DP/actual queued resume/DEV/offline-shadow на labelled generated inputs.
- `E04-UI` DONE в software scope: thin SourceDemo client/panel подключён к настоящим API;
  legacy04 STOP-on-blur/finish-autoimport заменены scoped flow. Actual Firefox
  desktop1440/mobile390: контекст→видимый verifiedJPEG→COMMON→same fake-bus
  Station/recorder→pause/resume/stage→finishUNKNOWN→отдельный STOP→preview→
  approve immutable import→reject PASS,35raw/31selected и30raw/25selected;
  268Node/26Python PASS. JPEG обновляется без замены кнопок/сброса таймера;
  logout ждёт поздние ответы, uncertain cleanup сохраняет вход.0realIO/deploy.
  [UI proof](../_registry/evidence/experiment04-source-demo-actual-UI-20261027.json).
- Approved learning actual Firefox:5/5 покрытие→original JPEG/raw8→official28frames/
  8segments→ACT8+DP8 через UI/общую очередь, pause/resume, save/reload0 и отдельный
  DEV. Пауза здесь SIGSTOP/memory retained, exact new-PID отдельно R7 выше.
  Новые карточки обоих backend/версий/checkpoint/DEV проверены отдельным GET-only
  Firefox desktop/mobile без повторного optimizer; SIM не допускается физически.
  270Node +36targeted +3current policy pins PASS; kernelpeak2115121152B/swap0/OOM0.
  [Learning UI proof](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json).
- `E04-VISION` DONE scoped software: actual Pi GET camera/CV/tabletop200; одна Logitech, CV
  instance segmentation. Один non-actuating Qwen2-VL event получил SEMANTIC_RESULT
  за31.576s; тот же old4c PID, camera/CV восстановлены, Stationidle/configwrites0.
  Во время native initialization был HTTP ReadTimeout6s, затем восстановление.
  Новый source-only04 admission сериализует VLM submission и COMMON entry:
  только idle/no recording, pending/inflight/unknown-release блокируют native;
  cancel не означает release, scoped STOP остаётся отдельным обходом.
  147Python/86.63s +272Node PASS; actual localhost Firefox generated CV/VLM→
  exact JPEG/mask/click→native block→CV recovery→new shown grant/fake teleop→
  semantic block→scoped STOP PASS, desktop/mobile. Guard НЕ deployed.
  [Vision proof](../_registry/evidence/experiment04-vision-source-admission-20261027.json).
  Метрическая калибровка отсутствует, actual later JPEG почти чёрный max2/255;
  exact old VLM input JPEG не сохранён. Semantic accuracy/physical skill OPEN.
- `E04-FIRST-UPGRADE` CURRENT: проверить безопасный первый переход old4c.
  PiSSH ключ работает, sudo-n требует пароль. Read-only cold-connect SDK source:
  port open +motor identity/firmware checks; запрет actual grants/STOP сохраняется.
  Проверить возможность доказанного freeze/zero-write termination barrier на
  изолированном old4c/fake IO, прежде любого действия с настоящим процессом.
  IdleGET/daemon server_close остаются недостаточными; fallback — отдельный
  наблюдаемый upgrade. Параллельно можно подготовить current release checks.
- Старый Pi4c имеет воспроизведённую shutdown race. Первый unattended Pi update
  OPEN: idleGET+server_close не drain. Нужен доказанный admission barrier либо
  отдельный наблюдаемый первый upgrade; пока продолжаем программную работу.
  [Proof](../04-so101-source-learning/docs/OLD4C_LIFECYCLE_PROOF_20261027_RU.md).
- Current native reference409/MCU stale, физическая рабочая сцена не подтверждена.
  Не делать HOME/grants/движение агентом. [Только действия владельца](PHYSICAL_CHECKS.md).

Согласованный новый порядок: **04 →03 →05 →06 →01**. Сначала04 до
IMPLEMENTED +SOFTWARE_TESTED +DEPLOYED; сразу после04 checkpoint обязательно
возобновить `E03-SOFTWARE-DEPLOY`, не05. В03 закрыть software gaps direct Pi→Nano,
existing-calibration/URDF/mapping/command-estimate bootstrap, endpoints и UI03,
развернуть HB/Pi/Nano и выполнить бездвигательную связную проверку; целевой статус
**DEPLOYED_AWAITING_PHYSICAL_ACCEPTANCE**. Physical gates остаются отдельными;
новый порядок не разрешает агенту движения/feedback/config changes.01 long tuning PARKED. Рабочий
repo на Homebrain; Mac/открытый чат не нужны runtime. Полный original master:
`~/.local/state/explorer-work/master-prompt.md`; private handoff — recovery pointer,
не второй канонический текущий статус. Историю требований/исходов не переписывать.

Организационный цикл внедрён: root AGENTS +canonical docs +repo skill +лёгкий
consistency check. Это инфраструктурный checkpoint; acceptance уровни не повышены.
