# Explorer — актуальное состояние

Updated: `2026-10-28T09:04:54+03:00`
Code commit: `7932526b7e3725411bce97cb39f5914cfa9dcaad`
Current task: `E02-04-OPERATOR`
Next tasks: `E02-04-DEPLOY`, `E02-04-USER-FEEDBACK`, `E04-REAL`
Overall: **IN_PROGRESS; исходный master-prompt не завершён.**

## Прямое указание пользователя — только02–04

Пользователь остановил прежнюю работу06/05/01 и требует исправить реальный
запуск02–04: открыть страницу → видеть резкую камеру → «Управлять» → сразу
существующее штатное управление; «Обучать навыку» начинает запись действий.
Скриншоты показывают фактические blockers:02 STOP_UNCONFIRMED до подключения;
03 continuity gate блокирует Start;04 управление требует SkillSpec/demo/challenge/
трёх checkbox и недоступно без них. Множество служебных блоков скрыть из базового
пути. Предыдущие software PASS/deploy **не доказывают пригодность для пользователя**.

Текущий scope: исправить запуск/камеры и упростить02–04, переиспользуя исходный
ROSMASTER teleop и frozen Station. Без новых research/hardening/tests sweeps.
Только необходимые целевые проверки изменённого сценария. Агент не запускает
реальные движения; firmware/исходные6 configs/limits/watchdogs/feedbackOFF не меняет.
Пользовательский click управления должен подключать существующий executor;
не требовать отдельного исследовательского acceptance для ручного управления.

## Фактический runtime перед исправлением

Homebrain central/worker +Pi/Nano OWN: **7932526b7e3725411bce97cb39f5914cfa9dcaad**.
PIDs HB2929596/2929598, Pi71479, Nano803299. Original Nano WEB737036/shared pinc82,
MCU2356/control2413/camera2347/cache459153 не менялись. Все6 controller configs,
reference и source/PIDs exact. USER both SO101 motorpowerOFF сохраняется.
Frozen Station SHA2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885.

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
