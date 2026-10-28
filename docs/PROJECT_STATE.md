# Explorer — актуальное состояние

Updated: `2026-10-28T15:58:15+03:00`
Code commit: `94f6f436f10a61ddfe3e830d6177bb8bf45cdbce`
Current task: `E02-04-OPERATOR`
Next tasks: `E02-04-USER-FEEDBACK`, `E04-REAL`, `E03-CONTINUITY`
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

Homebrain central/worker deployed **551c967**, Pi runtime **b38fcbf**; Homebrain
central/worker active, Pi observer PID81085, idle/grantNone/0 configuration writes.
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
session. USER после выкладки подтвердил, что этот сброс устранён; фактическая
teleoperation оставалась active более10 минут без ошибок и configuration writes.

После этого обнаружен отдельный UI-дефект камеры: challenge-frame временно заменял
общий `#camera-image`, а после долгоживущего старта отозванный blob оставлял его
пустым. `00b6837` явно возвращает live stream сразу после успешного start.
`b38fcbf` исправляет уточнённый USER-критерий запуска: для source04 только
текущая измеренная поза ведущей становится первой целью ведомой, затем frozen
Station продолжает обычное копирование. Тонкий adapter не открывает второй bus и
не пишет Goal_Position сам: он меняет только start baseline под исходным lock,
а все команды остаются у штатного30Hz worker и publication gate. Поведение
cooperative05 остаётся относительным и отдельно проверено. Это не motor
calibration: calibration файлы, контроллер и servo settings не записываются.
21 связанных Python contracts и2 целевых Node contracts PASS. Полный старый
Node-файл:22 PASS/3 FAIL — legacy advanced-panel ожидания не соответствуют уже
введённой простой панели; этот исход сохранён и не назван полным PASS. Frozen
Station SHA не менялся.

После deploy `b38fcbf` direct Pi camera и authenticated Homebrain proxy вернули
fresh1280x720 JPEG и MJPEG с JPEG markers; Pi observer active PID81085, Station
idle/available/errorNone. Визуальный USER camera retest и полный start→record→finish
ещё OPEN; deploy/GET не повышаются до physical acceptance.

По новому прямому указанию USER опыт03 настроен на короткий arm-only путь.
`551c967` меняет только Homebrain UI: default `ARM_ONLY`, native authorization
получает `chassis=false`, lease всегда отправляет пустые base actions, а после
start возвращается live camera. Шасси программно остаётся неподвижным. Существующий
Pi direct03 уже peer-configured/idle/errorNone, SO-101 leader calibrated; Nano
observer `764c9ac` active PID2373, native mux idle/STOP-latched, original Ruckig
backend и command-estimate reference `[90×6]` доступны. Nano controller/configs
не перезапускались и не записывались. 12 целевых UI contracts PASS; authenticated
Homebrain `nano-rgbd` snapshot вернул JPEG200. Физическое повторение руки ещё
OPEN до USER click «Управлять» и наблюдения; GET/deploy не доказывают движение.

Первый USER-observed запуск03 после этого реально провёл команды от leader через
Pi в исходный Nano Ruckig: пользователь увидел совместное движение, Pi накопил
132 measured-cache samples, последний native source sequence был6, шасси не
запрашивалось. Сессия оборвалась на одном `Native target IK rejected`; этот
исход сохранён, а не назван PASS. Уточнённый USER-критерий требует сначала
плавно поставить ROSMASTER в позу ведущей SO-101 и только затем повторять дельты.

`94f6f43` добавляет явную opt-in фазу `LEADER_POSE`: calibrated -100..100
положение каждого из5 суставов становится той же долей native +/-90° диапазона
ROSMASTER с исходными axis signs; захват использует существующий mapper. Команды
идут только через неизменённый original Ruckig и его limits. После command-estimate
сходимости backend автоматически переходит `ALIGNING_TO_LEADER → FOLLOWING` и
фиксирует новую относительную точку. Единичный недостижимый последующий IK-кадр
теперь packet-drop `TARGET_IK_UNREACHABLE`, а не завершение всей сессии.
50 связанных Python contracts/1skip, 41 native mux contracts и12 UI contracts
PASS. Frozen Station SHA прежний. Homebrain и Pi deployed exact `94f6f43`; Pi
observer PID82081, state IDLE/targetNone, свежие measured SO-101 данные. Cutover
выполнен при Nano execution_permission=false/lease0/chassis=false, не отправил
native command/STOP/HOME, не вызвал Node.close и не перезапустил исходный Nano
controller. Физическое совмещение и sustained following после исправления ещё
OPEN до нового USER-observed клика. [Evidence](../_registry/evidence/experiment03-initial-leader-alignment-94f6f43-20261028.json).

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
