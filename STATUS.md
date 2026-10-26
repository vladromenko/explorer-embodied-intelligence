# Explorer — выполняется на Homebrain, 27 октября 2026

Весь исходный мастер-промпт **не завершён**. Канонический Git находится в
`/home/jetsonnx/work/Explorer`; Mac не нужен для работающих служб.
Предыдущий статус сохранён [побайтно](research/audits/status-snapshots/STATUS-before-20261027-0210.md).

В работающем Control Center и его worker активен release
`36a2d6c72295b8bf0598e1af953ade06cf217752`. Собственные Pi/Nano observers и
import pin оригинального Nano web — `f4cc5c37096698633aba313dbc67b2769a647dd7`.
Последующие коммиты main ещё не считаются развёрнутыми. При прошлом cutover
сохранены все24 jobs,11 cards и10922 старых events;
[проверка](_registry/evidence/central-profile4-actual-activation-20261027.json).

Агент не двигает шасси или руки, не выполняет HOME и не выдаёт физические
разрешения. Все6 исходных ROSMASTER controller config SHA, firmware, limits,
watchdogs и отключённый readback сохраняются. Command estimate и возврат записи
не подменяют измерение или подтверждение удержания объекта. Последняя GET
проверка: Pi idle, available=false из-за sync-read, last_command=null;
Nano IDLE, permission=false, STOP latched. Старый native reference невалиден
после сохранённого telemetry fault; нового HOME агент не выполняет.

В main подключены настоящие backend/API/UI source04 наблюдаемой проверки,
отдельный DP history2 read-only shadow, зарегистрированный Study export через
общую очередь, два наблюдаемых cooperative05 разрешения и native transport.
Явные native-сессии05 ещё интегрируются. Два разрешения и вычислительный warm
не дают полной физической готовности: receiver dual-hold interlock на самой
публикации остаётся открытым. Новый Pi permission gate проверяет право после
ожидания исходной bus lock; отзыв до admission не даёт старой команде/sample
пройти.42 targeted tests проходят; это fake buses, без физического запуска.

Настоящий Study V9 API→Supervisor/bootstrap→worker→download→независимый новый
PID restore выполнен:7640 files,150620160 bytes TAR, все4 исходные попытки
сохранены (2 успеха,2 неудачи). Статус **PARTIAL**: старый полный initial/RNG
не записан; это SCRIPTED_SIM, не learned skill.
[Свидетельство](_registry/evidence/study-v9-public-queue-20261027.json).
Новый frozen SIM initial-state профиль отдельно дал побитно одинаковые
13×769 native float64 состояний после12 команд, RNG и отчёты нового PID;
4 resealed malformed profiles отклонены до native setter.
[Точная область](_registry/evidence/sim-initial-state-preflight-new-pid-20261027.json).
Позднее ревью выявило mutable-bundle TOCTOU; исправление готовится отдельно.
RGB, полная OS closure и learned outcome этим не квалифицированы.

Настоящие ACT6/DP6/106 CPU и ограниченный ACT9 CUDA profile4 уже проверялись;
original negative GPU resume и все неудачные object trials сохранены.
Последняя правка Station меняет один из18 DP source hashes, поэтому для новой
версии требуется новая CPU квалификация; агент готовит её с2 CPU/4GiB/swap0.
Предыдущие веса/доказательства не заменяются результатами новой версии.
[Профили](shared/training/PROFILE4_ACTUAL_20261026_RU.md).
Проверка original Nano factory/startup:6 tests, fake ROS/драйверы,
[receipt](_registry/evidence/native-current-source-startup-20261027.json).

Полная последняя архивная регрессия826a430:1161 passed,21 skipped,1 failed;
устаревшая API fixture исправлена и32 targeted passed. Это не утверждение
полного прохода текущего main. Следующий общий прогон — после source freeze.
Методологический provenance проверяется автоматически; нет новых Python
`continue`. Все28 worktrees и сохранённый cooperative stash инвентаризированы,
прежние агенты продолжили работу; исходники не удалялись и не сбрасывались.

Далее: исправления ревью, native05 API/UI и dual-hold publication interlock,
новые текущие CPU proofs, полная регрессия, проверенный immutable release,
реальный браузер/API и audit старых scientific bytes после cutover. Затем
новая SIM initial capture в будущих trial, contact repair и независимые
learned object trials; source04→target06 lineage/student/eval; H1–H5,
сравнения/ablations/retention и recovery campaign.102 full texts и35 deep
reviews, инфраструктура и отдельный успешный этап не означают научную новизну
или исполнение всего задания. Физические навыки остаются NOT_VALIDATED до
отдельных текущих пользовательских наблюдаемых испытаний.
