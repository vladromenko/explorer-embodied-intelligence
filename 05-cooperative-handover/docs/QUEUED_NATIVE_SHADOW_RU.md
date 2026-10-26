# Изолированная проверка двух настоящих action contracts

`explorer.rosmaster_shadow_job.shadow_request(store, model_job_id)` проверяет
завершённый `rosmaster_skill_train`, server-managed model manifest и каждый файл
checkpoint. Допускается ACT9 с состоянием106 и выходом9, только physical cohort.
Историческое состояние9, SIMULATION и смешанный cohort здесь не подменяют106D.
Функция не импортирует Torch и не загружает веса в Control Center.

Задание `rosmaster_skill_shadow` исполняется через общий durable scheduler:
CPU2cores/2GiB/GPU0, последовательный inference slot, без вытеснения. Worker
загружает модель **до** получения нового сенсорного пакета. Затем выполняет
только authenticated GET к Nano `/native-policy/observation` и
`/native-policy/observations/{observation_id}/frame`. Пакет сохраняет исходные
JSON/JPEG/сенсорные assets и SHA, независимые native boot/domain, cutoff,
отсутствующие каналы с масками и настоящие квитанции исходного publisher+базы.
Журнал позы, accepted API, измеренная odometry и запрет шасси не создают issued9.
Отсутствующий feedback сам по себе не блокирует командную оценку.

Node использует исходный owner cache и уже существующий NativeSensorCache;
новая камера/serial/ROS publisher не открывается. Новый packet требует actual
Tap basis; старый live owner без него корректно отказывается. Freshness проверяется
на Nano в его clock domain и отдельно на NX по времени получения/инференса.
Сравнения монотонных часов разных машин нет. Исходные bytes и предсказание
fsync-сохраняются; возвращаются hashes worker/loader, model/checkpoint и каналов.

Receiver04 использует существующий `so101_skill_shadow` и
`SO101CachedObservation`: его measured native6 и калибровка не преобразуются
в ROSMASTER9. Два предсказания остаются двумя native heads. Это shadow-only
проверка, без begin/input/lease/STOP, без физического успеха или разрешения.
Постоянного прогретого actor с scheduler lease пока нет: job warmup нельзя
выдавать за готовность выполнять release в двухсекундном окне удержания.

Регрессии используют настоящий локальный authenticated HTTP handler, точные
JPEG/JSON hashes и CPU predictor fixture. Они проверяют пропуски/будущие stamps,
иной boot, исходные нулевые base-команды, channel/outer-context tamper,
legacy9 rejection, warmup-before-source и cancel-before-I/O. Это проверка ПО,
не обучение модели106D и не физическое испытание.

`CooperativeService.command('shadow', {session_id, expected_epoch, request_id},
authenticated_actor=...)` теперь создаёт две отдельные native jobs через
`QueuedNativeShadows`. До первой вставки проверяются **оба** source SkillSpec,
model/checkpoint и original cohort SHA. Сеть/модели в Control Center при этом
не вызываются. `request_id` — bounded idempotency key: UI создаёт новый UUID
на явный новый запрос, а при повторе HTTP использует прежний. Без ID действует
одна reservation на закреплённые session/epoch/models. Committed SQL insert с
потерянным return восстанавливается при явном retry; missing half не стартует
автоматически при перезапуске.

GET snapshot для выбранной session добавляет `queued_native_shadows`. Каждый
элемент имеет `jobs.{giver,receiver}.job_id`, `native_heads` с actual status,
scheduler metadata и отдельными native contracts9/6. После completed ещё раз
проверяются исходные packet/JPEG/prediction assets, их hashes, модель и finite
native output. `both_native_inferences_verified` относится только ко времени
worker inference. Кадры разных роботов не объявляются синхронными; STOP меняет
epoch, старые результаты остаются историей. `persistent_warm_actor_ready` и
`reusable_for_native_dispatch` всегда false: ни completed job, ни старое shadow
не становятся свежим действием в коротком receiver-hold окне.

GET `/native-policy/status` читает только original cooperative status и original
reference guard, без нового pinned frame или control call. Настоящий source409
сохраняется как typed `BLOCKED_REFERENCE` / `NATIVE_REFERENCE_UNAVAILABLE` с
исходным bounded JSON reason и SHA. Отсутствующий actual issued9 имеет отдельный
`BLOCKED_ISSUED_COMMAND_CONTEXT`, authentication rejection и отсутствие HTTP
transport тоже различаются. Node observation возвращает typed409 для source409;
недоступная сеть не выдаётся за fault/reference. Ни этот статус, ни readback=false
не создают измеренную позу и не выполняют HOME автоматически.
