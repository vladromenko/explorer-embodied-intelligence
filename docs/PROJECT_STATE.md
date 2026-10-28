# Explorer — актуальное состояние

Updated: `2026-10-28T15:07:21+03:00`
Code commit: `de84b48334c920eb77d03c2525623039ae59d10d`
Current task: `E02-04-OPERATOR`
Next tasks: `E02-04-DEPLOY`, `E02-04-USER-FEEDBACK`, `E04-REAL`
Overall: **IN_PROGRESS; исходный master-prompt не завершён.**

## Прямое указание пользователя — только02–04

Пользователь остановил прежнюю работу06/05/01 и требует исправить реальный
запуск02–04: открыть страницу → видеть резкую камеру → «Управлять» → сразу
существующее штатное управление; «Обучать навыку» начинает запись действий.
Упрощённые страницы02–04 deployed на Homebrain. Новые фактические blockers после
USER-проверки:02 локальный штатный gamepad отключался после единичной задержки
camera preview/слишком редкого panel heartbeat;03 старая осиротевшая Pi-сессия
`CLUTCHED` мешала новому observed authorization;04 обе SO-101 доступны, но
observed grant сначала истекал от времени старого кадра, а после первого
успешного реального запуска04 — из-за привязанного к перерисовываемой панели
цикла продления.
Предыдущие software PASS/deploy **не доказывают пригодность для пользователя**.

Текущий scope: исправить запуск/камеры и упростить02–04, переиспользуя исходный
ROSMASTER teleop и frozen Station. Без новых research/hardening/tests sweeps.
Только необходимые целевые проверки изменённого сценария. Агент не запускает
реальные движения; firmware/исходные6 configs/limits/watchdogs/feedbackOFF не меняет.
Пользовательский click управления должен подключать существующий executor;
не требовать отдельного исследовательского acceptance для ручного управления.

## Текущий operator-fix checkpoint

Код `d798b97` сохраняет управление при временной задержке camera preview, обновляет
локальный gamepad panel каждые150ms, начинает observed TTL от фактического ACK,
и одним новым кликом03 закрывает retained source, затем принимает текущие позы
двух рук за относительный ноль без стартового прыжка. Контракт03 обновлён и
`11/11` PASS в `069c516`; Station permission/publication `18/18` PASS; три
изменённых browser-contract source04 PASS. Frozen Station SHA
`2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885` не менялся.

Homebrain central/worker и Pi runtime deployed exact **de84b48**; Homebrain
central/worker active, Pi observer PID79747, idle/grantNone/0 configuration writes.
Pi cutover выполнен только после idle/no-grant/no-capture; без `Node.close`, STOP или
native command. Предшествующий worker уже был idle с реальным servo read error;
перезапуск восстановил обе руки: available, errorNone, свежие измерения <10ms.
Exact calibration SHA прежние. Nano original controller не изменён. Исходные6
controller configs/firmware/limits/watchdogs/feedbackOFF не менялись.

Фактический USER запуск04 после `16102f7` выдал grant, вошёл в teleoperation и
отправлял команды всем6 осям; третий привод получил requested103.45 и штатно
ограниченный dispatched100.0. Ошибок шины/hold не было. Через~1s grant истёк:
publication gate сохранил `OBSERVED_NATIVE_PERMISSION_REVOKED_EXPIRED_OR_SOURCE_CHANGED`
и Station вернулась idle. Последующие реальные старты доказали, что источником
повторного сброса был добавленный двухсекундный `observation_deadline`, а не USB
или штатный LeRobot. Исправление `de84b48` после явного USER start переводит
source04 manual teleop в обычную долгоживущую сессию до точного Disconnect/ухода
со страницы/native source change; browser renewal больше не ограничивает manual
session. 27 Python contract tests и новый целевой Node contract PASS; frozen
Station SHA не менялся. Повторная USER проверка >10s и записи ещё OPEN.

Последний06 DATA checkpoint: recorder/review/import/dataset/model UI реализованы
и deployed; actual software-generated whole-browser cycle прошёл.186 targeted
Python/4opt-in SKIP,300Node; не полный regression. В production текущих source04
моделей0. Physical registration/SIM whole-task/навык/master не приняты.
[Сохранённый checkpoint](../_registry/evidence/experiment06-capture-deployment-checkpoint-20261028.json).

В55s cutover passive монитор увидел0 `/arm6_joints`,1 `/explorer/request` без
payload. Поэтому composite script FAILED assertion; исходный отказ сохранён.
Последующий65s raw diagnostic0messages, но первый пакет НЕ объяснён и НЕ объявлен
безопасным/отсутствующим. API GETs: Pi idle/grantNone/forwarded0; reference409 MCUstale.
HTTPS02–06 камеры/read-only UI не означают работающего управления: новый USER
дефект имеет приоритет над прежними readiness labels.

05/06/01 остановлены по текущему указанию, без удаления кода, моделей, originals
или evidence. Исторический подробный state сохранён в Git `7932526:docs/PROJECT_STATE.md`.
[BACKLOG](BACKLOG.md), [ACCEPTANCE_MATRIX](ACCEPTANCE_MATRIX.md), [physical queue](PHYSICAL_CHECKS.md).
