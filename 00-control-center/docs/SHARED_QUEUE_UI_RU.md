# Общая веб-очередь всех экспериментов01–06

`static/queue.js` — самостоятельный компонент. Экспорты browser globals и CommonJS:

```javascript
const client = createSharedQueueClient({ get: api, post, onChange: render });
root.innerHTML = sharedQueuePanel(client.snapshot(), knownJobsFromExistingAPI);
const unbind = bindSharedQueue(root, client, onError);
await client.refresh();
```

Constructor, render и bind не делают HTTP-запросов. Refresh — только
GET `/api/queue`, без запуска worker/создания metadata. Root владеет монтажом
страницы, existing authenticated/CSRF wrappers, API и запуском scheduler.
Перед заменой root вызвать unbind; cleanup не отправляет pause/resume/cancel.

## Данные

Компонент читает настоящий `queue_status`:
`jobs[].scheduling={metadata,revision,runtime,persisted}`, `resources`,
`plan_preview`, `service_budget_seconds`. KnownJobs используется только для
понятных названий/вариантов существующих зависимостей, в том числе completed.
Не создаётся вторая очередь или экспериментальная копия jobs table.

Показаны host CPU/worker pool, CPU/GPU reservations + выбранные кандидаты
**прогноза**, доступная/полная UMA, runtime reserve, оставшийся memory budget,
свободный диск, фактические температуры/действующие hot-critical trip показания.
Отдельная свободная VRAM и неизвестные значения не заменяются нулями.
Reservation не объявляется измеренным пиком или OS-isolation. Budget×time
fair-share charge не выдаётся за научный счётчик optimizer updates.

По каждой задаче: статус, реальные причины ожидания, budget, priority,
preemptible, backend/skill version/dataset SHA из сервера и проверяемая revision.
Неизвестный SHA остаётся неизвестным. Config/result/model/dataset/experiment/GPU
slot не редактируются.

## Явные изменения

Выбор «Настроить» фиксирует revision и исходный metadata snapshot. Форма:
priority[-10,10], preemptible boolean, зависимости через server job dropdown,
not_before через местный datetime, CPU cores / memory GiB / disk GiB.
GiB переводятся в целые bytes; not_before — Unix seconds или null. Это
планирование по wall time, не преобразование часов камеры/датчика.

Save отправляет только **изменённые** допустимые поля:
`POST /api/jobs/{id}/schedule {options, expected_revision}`. Ни implicit creation,
ни unconditional save без ревизии нет. Истинные minimum_resources используются,
если сервер их раскрывает; иначе минимум проверяет сервер, без выдуманных floor.
На активной задаче изменяются только priority/preemptible. Если за время правки
queued стала running, несовместимый бюджет не отбрасывается молча — пользователь
получает отказ и сохраняет черновик.

Периодический GET сохраняет текст черновика **и старую CAS revision**. Конфликт
не вызывает automatic retry/перезапись новой metadata. «Взять свежую основу» —
отдельное явное действие, заменяющее черновик. Правки, набранные уже после отправки
save, не стираются ответом: отправленная версия сохранена, новые правки остаются
черновиком на принятой revision.

Pause/resume/cancel — отдельные explicit POST существующих routes
`/api/jobs/{id}/{operation}`, body `{}`. Эти routes обязаны использовать
`Store.request_job_operation`, чтобы сохранить user_pause_requested и запрет
automatic resume. UI не редактирует status напрямую. Physical/interactive attempts
не получают pause/resume; новая физическая попытка требует отдельного свежего
наблюдаемого запуска. Cancel остаётся доступным при ожидающем pause. Поздний
ответ pause не снимает локальную отмену и не возобновляет задачу. Unknown network
outcome не объявляется подтверждённой отменой. Backend также обязан необратимо
защитить cancel_requested от поздних pause/resume.

## Что действительно означает пауза

- waiting_for_actual_optimizer_checkpoint / pause_requested: пауза только
  запрошена; checkpoint/exit не доказаны, процесс ещё может удерживать RAM.
- paused_checkpoint_process_exited / preempted_checkpoint_waiting: текст о
  подтверждённом checkpoint/освобождении памяти **процесса** показывается только
  с memory_retained=false, exact_optimizer_resume=true, actual durable RNG/sampler
  receipt, checkpoint/pointer SHA и подтверждённым кодом выхода supervisor.
  Глобальная доступная UMA измеряется отдельно, не выводится из этого флага.
- paused_process_memory_retained / SIGSTOP_no_exact_resume: честно показаны
  SIGSTOP, удерживаемые RAM/GPU lease и отсутствие exact optimizer restore после
  выхода процесса. Это не автоматическое освобождение ресурса.
- paused_before_work: процесс завершён до задачи; это не выполненные updates.
- Недостаточные/потерянные доказательства не заменяются optimistic resume.
  Показываются реальные preemption_blocked_reason и неизвестность.

Все проверки компонента — локальные Node HTTP/DOM fixtures, без сигналов,
worker/model imports, GPU и hardware. Реальную правильность checkpoint protocol,
source/runtime pins и durable queue отдельно доказывают backend tests/qualification.
