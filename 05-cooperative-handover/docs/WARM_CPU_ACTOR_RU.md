# Приватный IPC прогретого CPU worker

`cooperative_policy_actor.ScheduledNativeActor` не создаёт процесс и не импортирует
Torch/модель. `serve()` должен вызываться **внутри** уже admitted scheduler worker
после загрузки требуемых отдельных native models. Actor владеет только приватным Unix
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
Это квалификация IPC primitive, не обучение/physical test; готовность primitive
сама по себе не включает observed execution.

`explorer.cooperative_policy_job.run_cooperative_policy_session(store, job,
config)` — новый worker entry для `cooperative_policy_warm`. Pure metadata helper
`warm_request` не импортирует Torch. Entry сначала сверяет durable scheduler
PID/start/boot/launch nonce/bootstrap admission, managed original session,
coordinator process/epoch, подтверждённый Spec и точные native model/source plans
только для `required_policy_roles`. Эти роли выводятся из исходных
`giver_input_mode`/`receiver_input_mode == policy` и связаны с immutable request.
Manual role не получает placeholder модели или выдуманного результата. Для
`both_manual` compute job не нужен: ручная работа/recording доступны без модели;
явный `warm_models` объясняет отсутствие policy roles и ничего не ставит в очередь.
Warmup ограничен120s от первого сохранённого старта. Default loader использует
настоящие прежние `RosmasterSkillPolicy(ACT9)`106→9 и/или `load_shadow_policy`6→6
только для запрошенных policy roles:
cohort/checkpoint/preprocessing/camera/calibration/source hashes остаются
обязательными. Проверяются saved/current framework versions, CPU всех parameters;
READY receipt содержит SHA фактически загруженных official/adapter modules и RSS.
Worker-only CPU inference profile задаёт Torch threads2, как в сохранённой
finite qualification; control plane по-прежнему не импортирует Torch. Это
host compute настройка, motor/controller параметры не меняются.

После READY inference переиспользует загруженные объекты через существующие
GET-only source workers; исходные packet/JPEG/prediction/result сохраняются в
отдельных `jobs/<warm-job>/inferences/<sequence>-<role>` с SHA. Общий лимит
original evidence128MiB требует новой явной compute session, без удаления данных.
Отмена проверяется по родительскому authoritative job даже внутри этих вызовов.
Источник/model/hash сменился — actor отзывается; transient отсутствие camera или
reference не создаёт output и не выполняет HOME. SIGTERM/exit отзывает private
lease. Никакой native hardware lease не продлевается.

Три дополнительных worker tests используют actual temp Store/runtime nonce,
настоящий AF_UNIX и authenticated fake sensor HTTP с explicit dummy CPU models.
Проверены warm-before-source, обе independent native heads с original artifacts,
durable cancel после READY, cancel во время model load и отсутствие admission.
Это8 software tests вместе с IPC, без утверждения об actual trained-model warmup
или физическом handover.

Central `CooperativeWarmService` подключён к `CooperativeService`:
`warm_models {session_id, expected_epoch, request_id, compute_ttl_s}` ставит один
authoritative CPU4cores/4GiB/GPU0 job через общий serial inference slot. Перед
вставкой проверяются требуемые source/model bindings и server-derived resource profile.
Для одной головы сохранён консервативный тот же CPU4cores/4GiB budget.
Текущий активный job того же exact binding переиспользуется; повтор terminal request не
перезапускает его автоматически. Новый request ID создаёт новую compute session
после завершения предыдущей. Constructor не загружает веса/не читает камеры.

Snapshot для выбранной session содержит `warm_compute_sessions` с настоящими
job/scheduler statuses и public actor lease без token. `compute_ready` означает
только актуальные CPU-loaded required heads с проверенными original bindings/worker PID,
nonce, TTL и heartbeat. Dummy model receipts не дают ready. STOP сначала отзывает
coordinator epoch и затем отменяет compute jobs; shutdown отменяет только jobs
своего coordinator process. Ни один warm job не прикрепляет native transport
автоматически. `prediction_adapter` — отдельный pure IPC callback, закреплённый
за одним instance/native contract, без actuator API. Попытка вызвать predictor
для manual role отклоняется до source GET и inference. Snapshot публикует
`required_policy_roles`, `policy_role_readiness`, а каждый warm job —
`role_readiness` с отдельными required/loaded/current flags. Manual role не
выдаётся за загруженную модель.

Флаг `paired_runtime_benchmark_qualified` остаётся false для произвольной пары:
сохранённый `cooperative-dual-cpu-finite-20261026.json` относится только к двум
точным CPU models/source/runtime bindings и original SIM fixtures. Его peak RSS
895504384bytes и два inference каждой головы не означают live queue/physical
qualification других моделей. SIM cohort не проходит production native-source
admission.
Четыре central lifecycle tests проверяют настоящие resource budgets, concurrent
dedup, source/TTL rejection, отсутствие server Torch/source IO и cancel/close;
вместе с actor/worker/workbench integration24 tests проходят.
