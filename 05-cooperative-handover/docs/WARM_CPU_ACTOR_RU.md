# Приватный IPC прогретого CPU worker

`cooperative_policy_actor.ScheduledNativeActor` не создаёт процесс и не импортирует
Torch/модель. `serve()` должен вызываться **внутри** уже admitted scheduler worker
после загрузки двух отдельных native models. Actor владеет только приватным Unix
socket и callback pure inference; control/begin/lease/STOP API отсутствуют.

Приватный lease в каталоге задания имеет mode0600; socket находится в
`/run/user/<uid>/explorer-policy05/<job_id>.sock` под directory0700. Сверяются
SO_PEERCRED UID/PID, actual PID/start/boot, launch nonce, actor instance,
model/source binding digest, token и one-use свежий request nonce. Public runtime
receipt не содержит токена. Запрос принимает только named native role, без
браузерных observations/target actions. Caller владеет проверкой authoritative
job status и текущего session/process/epoch через обязательные callbacks.

Время compute session30..600s начинается в READY; это не hardware/user lease.
Повторный worker сохраняет первый READY deadline и получает новый instance/token.
Старый клиент закрепляет instance и не следует за заменой. Idle poll100ms
проверяет cancellation/epoch; после inference проверка повторяется до ответа.
Heartbeat и срок проверяются клиентом ещё раз после IPC. Истечение/cancel закрывают
socket и сохраняют REVOKED с token=null; прекращение процесса также обнаруживается
через worker identity. Автоматического восстановления физического разрешения нет.

Пять software tests используют настоящий AF_UNIX и блокированный callback:
native role9/6, таймаут с revocation, cancel во время inference, token/nonce replay,
точный private-file mode, recovery без продления deadline и stale-client reject.
Это квалификация IPC primitive, не обучение/physical test. Queue worker bridge,
actual source/model loading и central lifecycle должны подключаться отдельно;
готовность этого primitive сама по себе не включает observed execution.
