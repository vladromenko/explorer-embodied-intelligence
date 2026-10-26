# 05: общий процесс двух роботов и исходные свидетельства

Статус: исходная программная интеграция; физическая передача предмета **не проверена**. Ни один тест этого пакета не обращается к настоящему приводу. Оригинальные настройки, прошивка, выключенная обратная связь ROSMASTER, периоды Ruckig и native lease не меняются.

## Контракт и этапы

`cooperative_contract05` задаёт версионированный `explorer_cooperative_skill_spec_v1`: предмет, завершающая задача принимающего, независимые критерии результата и две закреплённые версии навыков. Подающий использует существующий ROSMASTER9D из 02/03/06, принимающий — исходный SO1016D из **04**. Ссылки содержат UUID, version, SHA спецификации, точный native action contract и явную карту фаз; модель дополнительно закрепляется model ID + SHA checkpoint. Нумерация берётся по семантическому slug. Исходные архивы старой нумерации не переписываются.

Доступны обе руки вручную, policy/manual, manual/policy и обе policy. Один и тот же лидер нельзя одновременно считать независимым источником управления двумя роботами. Начальный сценарий предполагает неподвижное основание. Два action head, два датасета и часы роботов остаются раздельными; общей выдуманной 15D или ROSMASTER6D модели нет.

`CooperativeWorkflow` сохраняет Spec, явную сцену/предмет/split, сеанс, epoch, исходные ссылки, запись, фазовые события, наблюдения и результаты. Фазы:

`PREPARE → ROSMASTER_HOLDING → OFFER → SO101_APPROACH → RECEIVER_GRASP_CHECK → RELEASE_AUTHORIZED → RELEASE → CONFIRM_TRANSFER → SO101_FINISH`.

Полное завершение требует отдельного доказательства итогового действия SO101. Ошибка/STOP отзывает epoch до ожидания сети или файлов; позднее подтверждение не возобновляет процесс. Поздний настоящий ответ привода сохраняется отдельно как неизвестный исход. Перезапуск не повторяет команды. `physical_stop_confirmed` не выводится из HTTP принятия STOP.

Выпуск подающим требует нового, свежего подтверждения удержания принимающим и двух свежих подтверждений неподвижности, запрашиваемых параллельно. Закрытие клешни, busy=false, истечение времени и текст VLM этого не доказывают. Существующий `OperatorEvidenceInbox` выдаёт одноразовые, привязанные к run/object/role/after-command запросы; имя пользователя приходит из серверной аутентификации. Обновление удержания в RELEASE_AUTHORIZED сохраняет прежние TTL и фазовый deadline и требует нового receipt ID того же сеанса. При отсутствии датчиков допустимо только явно наблюдаемое оператором удержание; измеренные сила/контакт не фабрикуются.

## Реальные точки подключения

Новые чистые модули расположены в `shared/explorer_shared/`:

| Модуль | Назначение |
| --- | --- |
| `cooperative_contract05.py` | Spec, pinned native refs, отдельный план входов моделей |
| `cooperative_providers05.py` | Registry существующих workflow, native inspectors/trainers/predictors/transports |
| `cooperative_native05.py` | Реальный metadata-only HTTP ABI рекордеров и public source workflow validators |
| `cooperative_recording05.py` | Две существующие камеры/телеметрия, оригиналы, локальный TAR, безопасный NX import |
| `cooperative_workflow05.py` | Durable сеансы/фазы/обзор/агрегат/shadow/отмена |

`NativeMetadataRecorder(role, authenticated_request, prepare_context, capture_started=...)` использует только перечисленные endpoints. Конструктор не выполняет запросы.

| Роль | GET | POST |
| --- | --- | --- |
| giver | `/native-capture/capabilities`, `/native-capture/status` | `/native-capture/record/start` `{skill_context}`, `/record/stage` `{recording_id,phase_id}`, `/record/finish` `{recording_id,outcome_evidence}` |
| receiver | `/station/status` | `/station/record/start` с результатом public `capture_context`; `/station/record/stage` `{episode_id,recording_id,phase_id,request_id,phase_scope:'coordinator_phase',coordinator_context}`; `/station/record/finish` `{episode_id,outcome}` |

У SO101 требуется уже существующий отдельно наблюдаемый native teleop/policy; рекордер никогда не вызывает enable/teleop/start/policy/start/action. Новое поле status `recording_identity_fence=True` квалифицирует проверку episode ID при finish под существующими блокировками. Старый сервер без этой возможности блокируется. Прежние вызовы finish без ID сохраняют ABI. Стадия на native host — аннотация получения, а не время экспозиции/контакта.

`public_capture_preparers` принимает реальные публичные `create_session`, ROSMASTER `prepare_capture_request` и SO101 `capture_context`. Варианты показов выбираются явно; их version/split проверяет владелец исходного workflow. Метаданные кооперации сохраняются в исходном `cooperative_parent_session_id` (у SO101 внутри `source_context.context`). `capture_started` связывает реальный native episode с серверным capture intent после ответа производителя; ошибка этой связи остаётся явной и не теряет ID оригинальной записи.

`existing_workflow_providers` связывает существующие trainer APIs. Для source04 без чистого training_request выдаётся дескриптор **существующего** `lab_skill_workflow.enqueue_training`, без постановки задания во время планирования. ACT/DP backend/profile и ресурсы повторно проверяются исходным владельцем при enqueue. Transfer06 требует явного собственного provider; нельзя незаметно назвать его 02. Отсутствующая модель, checkpoint mismatch, source inspector или native dispatch capability — явная блокировка.

## Подключение root API/UI

Сервис получает один `OperatorEvidenceInbox(..., clock_domain=NX_boot_domain)`, registry и один `CooperativeRecorder`. Recorder не открывает устройств; `CachedNodeObservation` получает оригинальные JPEG `/cameras/nano-rgbd/snapshot` и `/cameras/pi-overview/snapshot`, а также `/legacy/explorer/retarget/dispatch?after_sequence=N` и `/station/status` через Bearer auth. Фиксированные GET не следуют redirect. Тела/заголовки, frame IDs, SHA, source clock/boot, независимые request/receive estimates сохраняются побайтно. Дубли frame ID не становятся новым кадром. Gap в dispatch ring остаётся блокирующим фактом. Никакого приравнивания Pi/Nano/NX clocks.

Рекомендуемый тонкий root route mapping (сам root владеет аутентификацией и UI):

| Route | Метод сервиса / тело |
| --- | --- |
| GET `/api/cooperative` | `snapshot()` + `catalogue()` |
| POST `/api/cooperative/skills` | `propose_skill(body)` |
| POST `/api/cooperative/skills/{id}/confirm` | `confirm_skill(id, version, spec_sha256)` |
| POST `/api/cooperative/skills/{id}/sessions` | `create_session(id,version,body)`; scene/object/split/input adapters/native_variants явные |
| GET `/api/cooperative/sessions/{id}` | `snapshot(id)`, `readiness(id)` |
| POST `/api/cooperative/sessions/{id}/zone` | `register_zone(id,body, trusted_original_payloads)` |
| POST `/api/cooperative/sessions/{id}/record/{start,stage,finish}` | `record_start(id)`, `record_stage(id,phase_id)`, `record_finish(id,outcome)` |
| POST `/api/cooperative/sessions/{id}/step` | `request_step(id,event,expected_epoch=...)`; сразу operation ID, затем poll |
| POST `/api/cooperative/sessions/{id}/stop` | `cancel_session(id)`; epoch отзывает до файлов |
| GET `/api/cooperative/evidence` | текущий `inbox.pending()` |
| POST `/api/cooperative/evidence/{challenge}/confirm` | `inbox.confirm(..., authenticated_actor=server_actor)` |
| POST `/api/cooperative/sessions/{id}/shadow` | `shadow(id, requests)`; только реальные pure predictors, dispatch=False |
| POST `/api/cooperative/sessions/{id}/recordings/{record}/review` | `review_recording(...,authenticated_actor=server_actor)` |
| POST `/api/cooperative/skills/{id}/aggregate` | `build_aggregate(id,version)` |
| POST `/api/cooperative/skills/{id}/training-plan` | `training_requests(id,version,settings)`; не enqueue |

В record/finish `outcome.native_roles.giver/receiver` — **отдельные явно наблюдаемые** итоги исходных навыков; общий успех FSM не подставляется. По умолчанию unknown. Root затем импортирует настоящие native originals существующими source workers и записывает их IDs в обзор 05. Для sidecar: `import_cooperative_recording(archive,managed_destination,expected_sha256=...)` возвращает `cooperative_manifest`; отдельная ветка worker может вызвать этот helper, сохранив TAR, без изменения native trainer.

## Допуск и честные ограничения

Raw sidecar всегда analysis-only: его host sampling/intent не создаёт BC targets. Допускающий overlay требует целых исходных файлов, двух камер, обоих независимо допущенных native episodes того же фактического сеанса/Spec/split, явного полного видеообзора и исходов каждой фазы. Агрегат повторно проверяет исходные SHA/native inspectors; train/development разделены по сценам, экземплярам предметов, сеансам и native episode IDs. Повторный обзор не удваивает один оригинал. Sealed IDs не попадают в fitting. Исходные bytes и отзывы не переписываются.

Операторская проверка общей зоны позволяет ручной процесс, но не выдаёт metric transforms. Для policy нужны сохранённые оригиналы измеренной регистрации двух base↔zone, совпадающие JSON pointers/role/scene/metres и текущая native ownership/отдельная user-observed authority. Сам факт хеша не доказывает физическую точность калибровки; автономная физическая квалификация отдельно.

`submit_observed(command,release_authorization)` provider обязан возвращать типизированный `DispatchReceipt` из **реального native host publication**, с точными run/object/command/role; API ACK отвергается. Для giver.release нужен guard **на исходной границе публикации**: проверка свежего receiver hold/STOP/сеанса непосредственно перед каждым открывающим native write. NX deadline нельзя сравнить с Nano monotonic напрямую. Пока такой provider/clock-safe guard не привязан, `authoritative_release_deadline=False` и автоматический policy-процесс блокируется. Конструктор, metadata, shadow и challenge не разрешают движение. Этот пакет не заявляет выполненной физическую передачу или весь исходный master prompt.
