# Настоящие adapters и проверяемые ограничения передачи

`shared/explorer_shared/handover_adapters.py` связывает уже реализованный
`handover_runtime` с существующим `NanoArmHTTPClient` и отдельной очередью
подтверждений человека. Конструкторы не выполняют запросы и не открывают
оборудование. Исходные controller settings, firmware, PID, limits и watchdog
не меняются; chassis operations отсутствуют.

## Что установлено чтением настоящего Nano

26 октября выполнены только авторизованные GET через существующий Nano observer:
`/legacy/arm/feedback`, `/legacy/arm/manual`, `/legacy/arm/trajectory`. Проверка
использовала уже настроенный закрытый bearer token; SSH и обход авторизации не
использовались. Сводка с SHA оригинальных ответов —
`NATIVE_READONLY_BINDING_20261026.json`. Полный ответ сохранён в частном каталоге
Homebrain, указанном в сводке.

- feedback: `measured.available=false`, `values=null`,
  `reason=NO_PUBLISHER_IN_CURRENT_ROS_GRAPH`, `force_measured=false` и
  `command_echo_is_measurement=false`;
- manual: `ready=true`, `busy=false`, `estimated_only=true`;
- trajectory: старый ещё развёрнутый allowlist отвечает 404. Новая исходная
  интеграция расширяет именно существующий arm trajectory API; физическое
  исполнение этим чтением не проверялось.

Эти факты не подтверждают удержание предмета, неподвижность руки или физический
приём команды MCU. Отправленный endpoint и прошедшее время остаются командной
оценкой. Функция `inspect_native_observability(request)` повторяет эту узкую
GET-проверку и никогда не выдаёт её за qualification dispatch/stationarity.

## ROSMASTERArmTransport

Adapter принимает уже существующий authenticated `NanoArmHTTPClient`, текущие
root-owned ownership/authority callbacks, run/object IDs и
`OperatorEvidenceInbox`. Payload каждого `ArmCommand` содержит только
`goal_deg: [6 integers]`; порядок — исходные пять arm servos + отдельный gripper.
Никаких vx/vy/wz, `finite=true`, скрытой смены lease или guessed joint mappings.

`submit` сначала проверяет текущего владельца M3 arm и свежее разрешённое
наблюдение. Затем использует тот же Nano gateway, который владеет прямым 03:
`status → start anchor → action → original plan/start`. Это не ещё один новый
motor arbiter. Nano проверяет measured registration declaration, owner, epoch,
sequence и исходный stop revision. Конкурирующий источник не заменяется
автоматически.

**По умолчанию adapter блокирует команды до любых start/action вызовов:**

- `BLOCKED_NATIVE_DISPATCH`: исходный API acknowledgement не содержит
  достаточного независимого native-send receipt. Чтобы вернуть
  `DispatchReceipt(dispatched=true)`, root обязан подключить настоящий
  `dispatch_proof(context, timeout_s)`. Он читает receipt исходного dispatch и
  возвращает `NativeDispatchProof` с теми же run/object/command/plan/session,
  шестью endpoints и source/receive clocks. Простая установка callback не
  считается аппаратной qualification; источник доказывается отдельно.
- `BLOCKED_RELEASE_DEADLINE_AT_NATIVE_BOUNDARY`: существующий original start
  endpoint не принимает разрешение раскрытия, проверяемое в авторитетной точке
  dispatch. Проверка deadline только на NX перед HTTP не даёт такой гарантии.
  Поэтому `release_submit` также отсутствует по умолчанию. Нельзя включить его
  заглушкой, удалить проверку или без доказательств расширить `.6s` lease.

При наличии настоящих квалифицированных sources adapter проверяет каждый receipt
по exact command/plan/session, конечным данным и clock domain; не повторяет
неопределённую команду. Release дополнительно сохраняет первые пять endpoint
координат предшествующего dispatch: при общем удержании меняется только gripper.
Успешный native-send receipt также не доказывает достижения положения или
успеха предметной задачи.

`held(held=True, observing=True)` вызывается только свежим явным пользовательским
событием. В adapter нет auto-heartbeat или расширения старого lease. `stop(run_id)`
использует существующий arm-only STOP и никогда не открывает gripper.
`physical_stop_confirmed=false` остаётся явным.

## OperatorEvidenceInbox: текущие человеческие подтверждения

На Homebrain уже есть подходящая аутентификация/CSRF в панели. Интегратор должен
использовать её для следующих **конкретных методов**, без доверия к `actor`
из тела запроса:

1. Worker создаёт `inbox = OperatorEvidenceInbox(job_folder/'operator-evidence')`.
   Его новый `session_id` и `clock_domain` публикуются в durable job result для
   текущей попытки. По умолчанию новая попытка никогда не загружает старую.
2. Authenticated API проверяет, что job действительно относится к **текущему
   активному physical attempt**, затем создаёт attach с известным серверу
   `session_id`: `OperatorEvidenceInbox(folder, session_id=current_id,
   clock_domain=current_clock_domain)`. Session ID нельзя выбирать из
   произвольного клиентского тела и нельзя автоматически восстанавливать из
   завершённого/перезапущенного job.
3. `pending(run_id)` даёт вопросы, ожидаемые этим attempt. В UI показываются
   предмет, роль, команда, точная формулировка и оставшееся ожидание.
4. Защищённый POST вызывает
   `confirm(challenge_id, {'affirmed': True, 'observing': True, 'note': ...},
   authenticated_actor='owner')`. Actor — стабильное серверное имя
   авторизованного владельца, **не cookie/token**. Поля run/object/role/statement
   не принимаются из клиентского тела.
5. Worker через `inbox(statement, run_id, object_id, role, after_command_id,
   timeout_s)` получает типизированный `CorrelatedEvidence` и `HoldEvidence` с
   `kind=operator_confirmation`. Он не становится force/tactile measurement.
   `stationary_observation(...)` отдельно создаёт `ArmObservation` только после
   явного подтверждения неподвижности; `busy=false` этого не делает.
6. STOP вызывает `cancel_run(run_id)`. Timeout, поздний ответ, повторный
   challenge, другая роль/предмет/command или другая process attempt не дают
   разрешения на release.

Queue реально работает между API и worker processes: отдельный file lock,
atomic replace, fsync файла и каталога; worker ограниченно опрашивает текущий
challenge. Номер attempt и единый Homebrain clock обязательны при attach.
Время подтверждения обозначает server receive; оно не изображается временем
съёмки камеры. После рестарта новые attempt IDs исключают применение старых
подтверждений.

Вопрос `object_transferred_to_receiver` требует подтверждения двух фактов:
receiver удерживает предмет **и** giver его больше не удерживает. Повторное
сообщение «gripper закрыт» или VLM-описание не заменяют это событие.

## Фактическая проверка

`tests/test_handover_adapters.py` исполняет реальную serialisation существующего
Nano gateway и original plan/start/lease/stop в software fixture. Отдельно
проверяет отсутствие команд при заблокированной observability, несовместимые
native receipts, отсутствие авто-heartbeat, запрет release без native permit
boundary, ownership/registration и durable operator confirmation. Есть настоящий
отдельный spawned process для API↔worker подтверждения и конкурирующие handlers
одного challenge. Это `SOFTWARE_TESTED`, без вызовов физических motor endpoints.

Текущий настоящий ROSMASTER transport остаётся blocked по указанным причинам.
Предметная физическая передача, измеренная stationary feedback и новый sensor
source этим программным модулем не заявляются.
