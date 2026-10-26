# Перенумерация без изменения научных источников

`migrate_experiment_numbers.py` использует `explorer_shared.experiments` и явную исходную эпоху: v1 `04=перенос`, `06/personal=SO101`; v2 `04=SO101`, `06=перенос`. Установка новой нумерации не меняет embodiment, действие, источник датасета или качество модели.

Команды `dry-run`, `prepare`, `verify`, `check-source`, `rollback-copy` не активируют копию, не перезапускают службы и не обращаются к устройствам. `prepare --state ROOT --destination NEW_EXTERNAL_DIR --lineage-inventory AUDIT` создаёт отдельные `original/research.sqlite3`, `candidate/research.sqlite3`, `experiment-projection.json`, `manifest.json`. Исходную базу открывают URI `mode=ro` + `query_only` в транзакции. SQLite backup даёт согласованный снимок отдельной базы; согласованность между несколькими базами не утверждается. Явные `--outbox PATH` сохраняются отдельными original/candidate SQLite: исходные event_id, payload и delivered не меняются.

В одной транзакции копии только `jobs.project_id` завершённых заданий переводятся через временное пространство имён в канонический ID. Другие поля, job UUID, source release pins, config/result, таблицы events/skills/nodes/sessions/tickets и научные файлы не изменяются. Активные задания сохраняют исходный project_id и pin; индекс содержит их каноническую проекцию и `deferred_active_pin=true`. Проекция должна оставаться привязана к исходной эпохе даже после появления общего маркера v2. Новые задания получают эпоху v2. Указание source-numbering-version=2 не делает повторный swap. Уже подготовленная копия с маркером возвращает no_op.

Индекс `explorer_experiment_projection_v1` содержит `source_numbering_version`, `target_numbering_version`, `migration_id`, `source_database` (logical SHA/counts каждой таблицы), `entries_sha256`, `job_updates`, `outboxes`, `artifact_inventory`, `entries`. Запись entries содержит:

- `entity_kind`: job/event/skill/outbox_event/artifact_reference; `entity_id` — исходный стабильный ID или относительный путь#JSON-pointer.
- `original_project_id`, `original_numbering_version`, `canonical_project_id`, `experiment_slug`, `basis`.
- `raw_sha256`: исходная сериализованная строка body/payload; для job SHA канонической исходной SQLite-строки, для артефакта SHA всего исходного файла.
- `projection_sha256`: SHA canonical JSON остальных полей записи.
- event/outbox: `original_event_id`, `original_specs[]` с pointer/original_spec/original_spec_sha256; исходный spec и его SHA не становятся новым spec.
- skill: исходное тело карточки + SHA и `lineage_job_id`, если владелец доказан через задание.
- artifact_reference: исходные path/pointer/reference и `immutable_payload_rewritten=false`.

Raw SHA job привязан к строке в original-снимке. При проверке перенесённой строки можно восстановить original_project_id; последующие штатные обновления status/progress/result не являются изменением классификации и не должны навсегда блокироваться исходным SHA. Для события/SkillSpec/карточки неизменяемые body/SHA остаются постоянными. Каноническое представление должно явно показывать original_spec + SHA проекции; нельзя тайно редактировать исходное тело и выдавать его за прежний SHA.

В candidate SQLite новая таблица `experiment_numbering_migrations` хранит migration_id PRIMARY KEY, source_numbering_version, target_numbering_version, source_database_logical_sha256, index_sha256, terminal_job_updates, deferred_active_jobs (JSON IDs). Manifest связывает все файлы подготовки по байтам/SHA. `verify` проверяет также логические строки через WAL: подмена config/result/pin обнаруживается даже до checkpoint. Повторная доставка исходного outbox события остаётся идемпотентной после миграции.

`check-source` — обязательная проверка перед отдельной активацией интегратором: исходная SQLite должна ещё совпадать с preparation, указанные outbox и source JSON должны сохранять SHA. Телеметрия после снимка даёт `LIVE_DATABASE_CHANGED_REPREPARE_AFTER_QUIESCENCE`: нужна новая подготовка в окне остановленных собственных записывающих служб. Нельзя заменить работающую SQLite под открытыми WAL/SHM или потерять события после backup. Этот скрипт этого не делает. Отдельная активация/обратная активация принадлежит интегратору. `rollback-copy` после проверки manifest восстанавливает original в НОВЫЙ внешний каталог, не перезаписывая live или уже существующий каталог.

Подготовка незавершённого каталога не маскируется автоматическим повтором: сохраняется для разбора, исходник остаётся нетронутым. Ссылки маршрутов/путей не подменяются текстовым replace. Ошибочные/устаревшие JSON-pointer или SHA получают явную unresolved-причину. Неразобранные/большие JSON, архивы и веса остаются исходными; наличие индекса не означает переписывание всех исторических байтов.

## Реальная проверка 26.10.2026

Восемь тестов на временных Store/outbox проверяют транзакционный swap, неизменность остальных полей и SkillSpec, card lineage, независимость 02, active pin, идемпотентную доставку, повторную подготовку, восстановление исходной базы, подмену WAL/config, устаревшую инвентаризацию, symlink-путь и изменения источника после снимка. Все прошли с настоящим semantic registry.

Read-only dry-run production: 24 jobs, 8003 events (телеметрия продолжает добавляться), 11 skills; переносится 8 завершённых jobs, 0 активных, проецируются 8 исторических events и 3 modelcards. Инвентаризация 4798 JSON нашла 115 затронутых; 202 простые ссылки personal/04/06 повторно проверены по SHA/JSON-pointer; 10 ссылок маршрутов/путей оставлены без текстовой подмены. 39 больших/нечитаемых JSON в исходной инвентаризации явно исключены. Central outbox не был передан/найден; outbox логика отдельно проверена временной SQLite.

С production получена внешняя COPY и успешно проверена обратная COPY. Receipt: `~/.local/state/explorer-work/renumber-copy-qualification-receipt-20261026.json`; index SHA `98ca9e3c16325ffbd22c09cbf9ffc94989c57f01c1b9603364915b5c33306225`. После снимка настоящий check-source правильно отказал в активации из-за новой телеметрии events/nodes. Production не изменён. Эти копии — свидетельство механизма, не готовый к подмене актуальный снимок и не доказательство завершения всей задачи Explorer.
