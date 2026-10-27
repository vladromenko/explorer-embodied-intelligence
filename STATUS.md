# Explorer — Homebrain, 27 октября 2026

**IN_PROGRESS. Весь master-prompt не завершён.** Canonical Git:
`/home/jetsonnx/work/Explorer`; работающие службы не зависят от Mac/чата.

Receiver setup05 подключён к service/API/UI; root выполняет frozen релизную
регрессию и готовит guarded cutover. Подготовленные ветки и stash сохранены;
изменения интегрируются отдельными commits с адресными проверками.

В production central+worker активен **36a2d6c72295b8bf0598e1af953ade06cf217752**;
собственные Pi/Nano observers и import pin оригинального Nano web —
**f4cc5c37096698633aba313dbc67b2769a647dd7**. Поздний main ещё не считается live.
[Фактический cutover](_registry/evidence/central-profile4-actual-activation-20261027.json).

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
  loss/corruption/restart/legacy/fault race проверены на fake owners.
  [Границы](05-cooperative-handover/docs/RECEIVER_INTERLOCK_TRANSPORT_RU.md).
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
- Frozen883478fe:1579passed/31skipped/2failed за315.00s: Git-снимок не
  содержал внешние CAD для двух MuJoCo tests. С проверенными20CAD и явным
  headless EGL оба tests прошли за5.79s; исправленный полный прогон идёт.
  Все198 Node tests прошли. Исходные24jobs/11cards/events/schema сохранены.
  [Первый прогон](_registry/evidence/full-regression-release-first-883478fe-20261027.json).


Блокеры: Pi station idle с sync-read error при двух присутствующих USB-адаптерах;
Nano native reference невалиден после сохранённого telemetry fault. Агент не
делает HOME, не двигает шасси/руки и не меняет6 исходных ROSMASTER configs,
firmware/limits/watchdogs/readbackOFF. Unknown и command-estimate не становятся
measured. Source/target splits раздельны; development/sealed/unknown не optimizer.

Открытый программный объём: phase/hold-successor lifecycle
без скрытого TTL/lease продления; final regression/deploy/Firefox/live scientific
byte audit; полный learned object/transfer/retention цикл, независимые H1–H5
comparisons/ablations и broad recovery. Физическая подготовка отдельно.

Единственная матрица01–06: [_registry/acceptance.json](_registry/acceptance.json).
[GOALS](GOALS.md) · [PLAN](PLAN.md) · [DECISIONS](DECISIONS.md) ·
[HARDWARE](HARDWARE.md) · [PHYSICAL_CHECKLIST](PHYSICAL_CHECKLIST.md).

## USAGE_POLICY

Root — единственный координатор; по умолчанию0–2 субагента. Третий допустим
только для трёх крупных независимых потоков; короткие проверки root делает сам.
Новые рутинные агенты — Sol Medium/High при доступном выборе, сложная интеграция/
финальный audit могут требовать Ultra; активный чат/модель не переключаются.
Проверки: static→адресные→интеграционные→один полный прогон перед release;
критические motion/STOP/interlock/data/provenance проверки не сокращаются.
Исключение: полный d52-прогон понадобился после сквозной04/05/queue интеграции;
после исправлений и новых publication guards нужен релизный прогон. Повтор883
потребовался из-за отсутствовавших во frozen environment внешних CAD/headless EGL;
код не менялся, оба адресных actual MuJoCo tests прошли перед повтором.
Повторный внешний сбой без новых фактов после2–3 попыток фиксируется как блокер.
