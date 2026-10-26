# Experiment 02: SkillSpec, серия показов и единый набор данных

Это инженерный workflow, а не новая обученная модель. Все шаблоны и свои описания относятся к одному ROSMASTER mobile manipulator: общие perception, база, рука и захват; одновременно сохраняются все девять команд. Нейтральные значения остаются в данных. Маска означает только реально недоступное или невалидное измерение. Отдельного arm-only/base-only SkillSpec, набора данных или пользовательской кнопки обучения нет.

Текущий полный обучающий backend perception/navigation/contact ACT/BC/coordinator/recovery не реализован. Поэтому одна кнопка «Обучить навык всего ROSMASTER» заблокирована и backend также отклоняет обход через прямой job. RGB+9D ACT-reader существует как внутренний legacy компонент; он не заменяет всю иерархию. Выполненные программные тесты используют только временные синтетические источники; физический успех не подтверждался. Шасси неподвижно; ни одна рука не получает разрешение на движение из выбора навыка или запуска записи.

## Данные и решения оператора

`proposal(text='', preset=None)` возвращает PROPOSED, стабильный UUID появляется при сохранении; русский literal pattern — редактируемое предположение, а не уверенное NLU или language input ACT. Шаблоны: носок→корзина, предмет A→B, навигация к точке и подъезд→захват→перевозка→размещение. Навигационный шаблон сохраняет тот же полный робот и 9D.

Изменения сохраняют UUID и увеличивают версию с optimistic SHA. Подтверждается конкретная версия. Сессии принадлежат одному skill/version/split. Старые записи можно явно назначить через sidecar; исходные bytes/outcome не меняются. Отсутствующий или не совпадающий source-bound skill/version оставляет запись для анализа, без автоматического повышения до unified.

Начальный технический план — три учебных варианта, отдельная development и sealed сессии; это проверка покрытия данных, не число показов до освоения. Показы whole_episode, targeted_subskill и recovery сохраняют одну SkillSpec и 9D. Матрица phase×variant и исходы success/failure/unknown учитываются отдельно. Targeted показ увеличивает охват соответствующих фаз, но не становится полным успешным эпизодом. Следующий конкретный показ выбирается по недостающему варианту и реальным причинам отклонения предыдущих записей. Обученный навык требует независимых полноценных испытаний; chunk error и количество показов не подтверждают мастерство.

## Авторитетный source contract

Workflow вызывает `inspect_mobile_import(..., include_manifest=True)` и использует `mobile_embodied`, проверенный `explorer_shared.embodied_episode`. Сам структурный admission не делает запись успешным BC-экспертом. Дополнительно проверяются завершение/quality/operator outcome, исходные независимо наблюдавшиеся успешные фазы и whole outcome, source-bound skill/version и partition. Source success нельзя создать операторской отметкой UI. Development/sealed не допускаются к оптимизатору; sealed payloads не попадают в aggregate.

Глобально проверяются исходные episode, scene, session, object instance/group и RGB frame identity; новым операторским именем сессии нельзя скрыть пересечение. Оригинальный envelope/inline wrapper и каждый payload копируются byte-exact с SHA, включая clocks, нейтральные команды, сенсорные availability и различие recorded/admitted/model-used. Оригинальные файлы не переписываются.

`build_aggregate` создаёт content-addressed immutable `explorer_unified_embodied_skill_dataset_v1` из ВСЕХ принятых train+development эпизодов одной версии. Файлы fsync, каталоги fsync, финальный rename; повторный вызов проверяет сохранённые hashes. Source paths ограничены managed root, traversal исключён. Canonical-only источнику не нужен NPZ/preceding_command9: запрещено синтезировать state9 из joint6 и измеренных скоростей одометрии. Если присутствует отдельно квалифицированный исходный legacy RGB+explicit prior-command9 view, его неизменённые NPZ могут быть сохранены как optional internal view для прежнего RecordedChunks; это не самостоятельная альтернатива full Train. Canonical primary и optional legacy view выбираются по разным manifest/path/SHA. Даже валидность обоих источников и общий episodeID не подтверждают поэлементную связь RGB/action/clocks: `cross_view_sample_correspondence_verified=False`; sensor fusion и использование всей sensor packet ACT не утверждаются. Повтор одинаковых RGB bytes между split считается неоднозначным для независимой оценки и оставляется анализом, даже при новых именах кадров/сцен.

## Встраивание в API и запись

Все функции принимают `store` первым аргументом, не выполняют motor/camera/network IO и используют существующую таблицу immutable `events`; миграций БД нет. `WorkflowBlocked(ValueError)` содержит `.reasons: list[str]`, строка — причины через `; `. Root API переводит это в HTTP409, ошибки формы/identity — HTTP400.

| Маршрут | Функция |
|---|---|
| GET /api/skill-workflow | list_skills + available_imports → {skills, imports} |
| POST /api/skill-workflow | create_skill(store, {preset или text, request_id}) |
| GET /api/skill-workflow/{skill_id}?version=N | workflow(store, skill_id, version) |
| POST /{skill_id}/revision | revise_skill(store, skill_id, version, patch, spec_sha256) |
| POST /{skill_id}/confirm | confirm_skill(store, skill_id, version, spec_sha256) |
| POST /{skill_id}/session | create_session(store, skill_id, version, body) |
| POST /{skill_id}/bind | bind_episode(store, skill_id, version, variant_id, body) |
| POST /{skill_id}/aggregate | build_aggregate(store, skill_id, version) |
| POST /{skill_id}/train | enqueue_training(store, skill_id, version, settings) |
| GET /api/skill-workflow/capture-context?episode_id=... | capture_for_episode(store, episode_id) → context или null; зарегистрировать перед /{skill_id} |

Во всех POST версии целое положительное число. Session body: `split, scene, description`. Bind: `import_job_id, session_id, completed_stages, full_task_confirmed, object_and_goal_visible`, optional `episode_scope, target_phase_ids, phase_outcomes, recovery_context`; при исправлении требуется `expected_binding_sha256`. Binding возвращает `binding_sha256`, original ID/skill/version/variant/split и основания решения.

Запись связывается без добавления неизвестных полей в native protocol:

1. UI `skillWorkflowRecordingContext()` проверяет confirmed spec, сессию варианта, выбранные фазы и неподвижность шасси. Root вызывает `begin_capture(..., request_id?, episode_scope?, target_phase_ids?, stationary_chassis_confirmed=True)` и сохраняет capture_id.
2. Root запускает прежний native start без новых полей и при достоверном успехе вызывает `capture_started(store,capture_id,native_episode_id)`. UI `skillWorkflowCaptureStarted(captureId, context?)` сохраняет снимок контекста.
3. ПЕРЕД forwarding stage/finish root вызывает `verify_capture_episode(store,capture_id,native_episode_id)`; другая вкладка/старый episodeID отклоняются до proxy. `capture_stage` сохраняет операторскую отметку времени central receipt, не exposure/contact measurement.
4. После успешного native finish root вызывает `capture_finished(..., reviewBody)`. Невалидный post-finish review сохраняется как analysis/unknown вместе с причиной, а не теряет реальную запись. `bind_import_from_capture(store, completedMobileImport)` допускает запоздалую загрузку и повторные callbacks, никогда не перезаписывает уже вручную исправленную привязку.
5. При reload root берёт действительно активный native episodeID и вызывает UI `skillWorkflowRecoverCapture(nativeEpisodeId)`. GET возвращает durable context; наличие sidecar само по себе не утверждает, что native запись ещё активна. Изменение выбранного skill/version/variant/session не переименовывает текущую запись: finish UI отклоняет mismatch.

`enqueue_training` — единственный путь будущей постановки verified whole job: в одной SQLite BEGIN IMMEDIATE вставляет jobs+workflow.training и reservation, повторно проверяя spec/binding SHA. Same aggregate retry возвращает уже активный job; другой active aggregate той же версии блокируется. Отдельный `register_training_job` намеренно отклоняется, чтобы исключить orphan queued job между двумя транзакциями. Сейчас full backend blocker возникает до вставки job. Прямой API jobs обязан вызвать `validate_02_training_request(...,server_generated=False)`; клиентский partial/single-clip02 request отклоняется. Серверный флаг сам не заменяет проверки pipeline.

`attach_skill_lineage(card, job_config, source_manifest_sha256)` связывает модель с skill/version/aggregate SHA, проверяя файл. Эта связь не переименовывает legacy model contract в unified и не подтверждает весь навык: `unified_model_qualified=False` до настоящих model-used/hierarchy receipts. Текст SkillSpec остаётся metadata, а не языковым входом ACT.

## Проверка

`pytest tests/test_skill_workflow.py tests/test_mobile_dataset_evidence.py tests/test_embodied_episode.py -q`

Синтетические tempfile fixtures проверяют реальные source-bound canonical parser/inspector и byte-exact aggregate; optional legacy view проверяется реальным RecordedChunks без оптимизации. Отдельная SQLite-trigger проверка доказывает rollback job при отказе lineage-event и active-job dedup. Node VM проверяет единственную заблокированную Train, полный контракт и reload/wrong-tab context. Реальные state, камеры, контроллеры и приводы эти тесты не используют.

## Общее downstream опытов02 и03

Latest owner requirement:02 keyboard/gamepad и03 SO101 leader retarget отличаются только input adapter. Один `rosmaster_shared_demonstration_pipeline_v1` использует одну SkillSpec/version, canonical9D qualification, общие сессии/счётчики, aggregate, train queue/model lineage и дальнейшую evaluation/deploy selection. Никакой follower6D/raw leader dataset не становится ROSMASTER targets. Pipeline архитектура, action/state contract и robot type от страницы не зависят.

`create_skill` и `create_session` принимают `body.project_id`02/03 и `body.input_mode` keyboard_gamepad/so101_leader_retarget как контекст происхождения. `workflow(...,project_id='02',input_mode=None)`, `training_request(...,project_id='02',input_mode=None)`, `enqueue_training(...,project_id='02',input_mode=None)` сохраняют defaults02; project03 меняет контекст запроса/очереди, а не набор совместимых источников. `list_skills(...,project_id=...)` и `available_imports(...,project_id=...)` показывают общий пул обоих опытов. Повторный queue request02/03 одной версии/aggregate возвращает один active job; другая active reservation этой версии блокируется глобально.

`bind_episode` принимает completed mobile_import и canonical retarget_import с registry project02/03, использует общий `inspect_rosmaster_import` (fallback старого inspector закрыт для неквалифицированного ретаргета). Оригинальные episode/session/scene/object/RGB leakage guards общие между проектами. Envelope skill/version и 9D source success проверяются одинаково. Raw leader archive остаётся анализом. Registry source_project/import_kind сохраняются отдельно от подтверждённого input adapter: без source-bound mode receipt `input_mode=UNKNOWN`; номер страницы не является доказательством, каким устройством создана команда. Один canonical aggregate может содержать принятые источники обоих adapters; исключённые sealed payloads и модельные ограничения общие.

`begin_capture(...,project_id='02',input_mode=None)` сохраняет операторский контекст в sidecar; natives получают прежний protocol без добавленных полей. `bind_import_from_capture` понимает завершение mobile/retarget imports и fallback `recording_id`. Event project отражает исходный контекст, но записи считываются одним workflow. `validate_rosmaster_training_request` защищает02 и03 одинаково; прежнее имя `validate_02_training_request` — совместимый alias новой общей защиты, а не разрешение обхода через03.

Неполный feedback не включается принудительно: canonical command-estimate profile может сохранять measured channels null/false mask с причинами, когда есть реальный исходный reference/boot/explicit prior9/native dispatch. Схема сенсоров не означает, что каждое измерение обязано существовать. Отсутствующая навигация/геометрия остаётся capability blocker; source-bound full outcomes всё равно требуются для целого эксперта. Полный обучающий backend ещё не реализован этим refactor и остаётся отдельной незавершённой работой, а не выдаётся за готовый навык.

Root API wiring: читать `project_id` query для GET catalogue/workflow, POST body для создания/сессии/Train; при Train передать keyword project_id/input_mode в atomic enqueue. Generalized static script сам выбирает context по /experiments/02 или /experiments/03 и использует тот же API/selector/counters/Train. `skillWorkflowRecordingContext()` уже возвращает оба поля; root должен передать их только в capture helper, не в native controller protocol. Inspector source-mode proof должен приходить от recorder/authoritative adapter. Смешанные tempfixtures проверяют общий aggregate/пул, отсутствие fake feedback, отказ rawleader, cross-project leakage и общую очередь; никакого физического исполнения здесь нет.
