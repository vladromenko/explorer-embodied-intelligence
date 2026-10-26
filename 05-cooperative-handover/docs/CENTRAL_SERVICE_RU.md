# Центральная сборка 05

`explorer.cooperative_service.CooperativeService(CONFIG, STORE)` связывает реальные версии навыков 02/03 и source04, публичные исходные workflow, producer APIs, source inspectors, operator inbox и dual-cache recorder. Новый файл не импортирует app.py, Torch, модель или драйвер. Конструктор не делает сетевых запросов, не выбирает управление и не вызывает begin/resume/lease. Это проверяется с настоящим временным STORE и public source APIs.

Сервис владеет **синхронным** cached `httpx.Client(trust_env=False, follow_redirects=False)`. Не передавать в него AsyncClient приложения. Root может вызвать sync методы из FastAPI sync route или через `asyncio.to_thread`. При завершении вызвать `close()`: завершаются только принадлежащие ему native метаданные с unknown outcome, затем локальный sidecar; новые команды управления не посылаются. Исходные наблюдательные lease остаются прежними.

## Минимальная root интеграция

```python
service = CooperativeService(CONFIG, STORE)
# GET /api/cooperative?session_id=...
service.snapshot(session_id)
# POST /api/cooperative/{operation}; actor задаёт authenticated server session
service.command(operation, body, authenticated_actor='authenticated_explorer_owner')
# GET fixed source artifact; identity — выданный сервером ID, не путь
path, media_type = service.artifact('zone', zone_id, role='giver')
path, media_type = service.artifact('recording', recording_id)
```

GET возвращает `{workbench,catalogue,native_choices:{giver,receiver},pending_evidence,cameras,selected_session,native_episode_choices,missing_native_providers,model_adapters_bound}`. `workbench.sessions[]` и `selected_session` имеют `legal_events` и `readiness`. Камеры имеют `preview_url`, `snapshot_url`, `stream_url` существующих `/api/cameras/{id}/...`; их наличие в каталоге не выдаётся за проверенную live доступность. При выборе сеанса UI сохраняет `?session_id=` при последующих GET.

POST возвращает `{result,snapshot}`. Имена операций: `propose_skill`, `confirm_skill`, `create_session`, `register_zone`, `record_start`, `record_stage`, `record_finish`, `step`, `stop`, `confirm_evidence`, `review_recording`, `aggregate`, `training_plan`, `train`, `shadow`, `attach_native_giver`.

`propose_skill` получает `giver_choice_id`/`receiver_choice_id`, а не клиентские произвольные refs. Сервер вновь разрешает их в confirmed UUID/version/SpecSHA/native action contract. У choice есть `reference`, `name`, `phase_ids`, `native_variants`, реальные проверенные `models`. Опциональные `giver_model_id`/`receiver_model_id` выбираются только из фактического managed checkpoint inventory. Broken/orphan checkpoint не становится моделью и не блокирует ручные варианты; ошибка видна в `model_source_errors`. Empty phase map означает whole native skill, без выдуманной correspondence и без пустого targeted phase list. Transfer06 остаётся явным missing provider, пока его собственный adapter не подключён.

`create_session` требует точного `spec_sha256`, явных scene/object/split/input adapters и `native_variants` обеих ролей. Сервис актуальных физических узлов сохраняет source execution domain как **серверно объявленную** метку в оригинальном source context, отдельно от независимого подтверждения успеха. Старый архив без нужной provenance не получает эту метку задним числом. Native role source inspectors и соответствие исходных domains/parent/session всё равно проверяются при обзоре.

Все изменения существующего сеанса требуют `session_id` и текущего `expected_epoch`. `record_stage`/`record_finish` дополнительно требуют текущего `recording_id`: старый HTTP finish не может закрыть следующую запись того же сеанса. Start metadata не начинает ни teleop, ни policy. Source04 требует уже отдельно наблюдаемый исходный native control session.

`register_zone` принимает только наблюдаемую проверку `both_robot_workspaces_checked=True, observing=True`. Сервер получает исходные два cached JPEG/headers, сохраняет SHA/оригиналы и подставляет authenticated actor. Клиентские пути, fake source SHA, actor и матрицы не используются. Это ручная проверка общей зоны, а не метрическая калибровка; policy metric blocker остаётся до настоящей регистрации. Source JPEG доступен только через server artifact ID.

`confirm_evidence` передаёт inbox только `affirmed`, `observing`, `note`; `challenge_id` используется как route identity. Имя подтверждающего приходит из server session. `step` использует только допустимый event текущего FSM; source callbacks не запускаются при неверном epoch/фазе. STOP не объявляет физическую остановку по принятию HTTP; при отсутствии принадлежащего сеансу native transport чужое управление не затрагивается.

## Итоги и обучение

`record_finish.outcome.native_roles.giver.review` содержит **явно выбранные** `completed_stages`, `object_and_goal_visible`, `full_task_confirmed`. Отсутствующий/некорректный независимый обзор закрывает source запись с unknown, сохраняя оригинал. Общий outcome FSM не подставляется в native labels. У receiver native status success/failure требует operator_observation; для source04 admission отдельно нужен существующий source video/phase-interval review.

`native_episode_choices` содержит только действительно завершённые импорты с оригинальным parent05 ID. Каждый choice имеет `choice_id`, `import_job_id`, `original_episode_id`, admission/reasons и `review_url`/`timeline_url`. `review_recording` получает `native_episode_choice_ids:{giver,receiver}`, исходы всех cooperative phases, whole video/object confirmation. Оба оригинала ещё раз проверяются существующими native admission functions; raw/legacy/неполный source остаётся analysis-only.

`aggregate` сохраняет отдельные native sources/action contracts, не создаёт 15D датасет. `training_plan` не ставит задачи. Явный `train` сначала проверяет paired cooperative aggregate и оба native preflight, затем вызывает **существующие** `mobile.enqueue_training` и `lab.enqueue_training`. Lab request имеет стабильный server request ID; уже committed ROSMASTER request восстанавливается по его действительному immutable native config. Частичный результат сохраняется в managed `training-requests`, виден пользователю и не удваивает задание при потерянном возврате. Перезапуск сам недостающую половину не ставит. Coordinator остаётся baseline FSM, не якобы обученной моделью.

## Наблюдаемая policy интеграция

`model_adapters` по умолчанию пуст; shadow/execution при этом недоступны. Root может передать реальные доверенные native model+fresh-observation adapters. Browser RGB/state, invented bool и потерянные измерения не принимаются как runtime observation. `shadow` вызывает только native pure predictors и сохраняет разные 9D/6D outputs с `dispatch_performed=False`.

`attach_native_giver` **не** вызывает select/bind/begin/resume/lease. Он привязывает только уже отдельно наблюдаемый и возобновлённый native owner, подтверждённый реальным cooperative API, точным run/object/epoch/auth/control epoch, текущим lease и pinned checkpoint. Требуются явные observing/user_observed_test_confirmed и доверенный server model adapter. Native `NativeGiverPolicyTransport` затем ждёт настоящий publication receipt, а release — свежий native challenge + consumed operator proof и точный grant у реального исходного издателя. Неопределённый sensor/model adapter или неподключённая receiver-side защита остаются программным blocker; полная физическая квалификация не подразумевается. Конструктор всегда оставляет физическое исполнение выключенным.
