# Explorer — актуальное состояние

Updated: `2026-10-28T11:12:16+03:00`
Code commit: `069c516d52e6e462f36d8dd3c5cd6acd8e9557eb`
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
observed grant истекал от времени старого кадра ещё до окончания сетевого start.
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

Homebrain central/worker deployed **d798b97**, PIDs2965861/2965862; authenticated
health и served static exact. Pi release d798 staged, но runtime остаётся53783ce:
переключение намеренно ждёт USER-клик03, который закроет старый target epoch,
чтобы не осиротить владельца Nano. Nano original controller не изменён. Обе SO-101
на Pi фактически доступны, exact calibration verified, ~29.93Hz, measurement age
обычно<30ms, configuration writes0. Штатный gamepad Nano connected, DISARMED,
ошибок нет, события свежие. Исходные6 controller configs/firmware/limits/
watchdogs/feedbackOFF не менялись.

Physical acceptance ещё OPEN: screenshot03 был сделан11:02 до central deploy11:06;
USER должен reload страницы03 и одним кликом «Управлять» закрыть retained
`CLUTCHED`, затем отключить управление. После этого переключить Pi d798 и выполнить
наблюдаемые USER проверки03/04;02 повторно проверить keyboard/local gamepad.

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
