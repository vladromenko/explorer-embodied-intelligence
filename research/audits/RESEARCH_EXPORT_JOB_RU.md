# Worker `research_export`

ENGINEERING_GLUE. Новый `explorer.research_export_job` подключает выбранный
scientific graph к обычной durable queue. Устройство, policy, optimizer, SSH или
исходный controller не импортируются/не вызываются.

API владеет parent control center. Его browser body строго `{job_ids:[...]}`.
`request_settings(store, job_ids)` проверяет1–32 входных lowercase32hex IDs,
сортирует и убирает повторения; существующие selected jobs должны быть terminal
(completed/failed/cancelled). Экспорт экспортов запрещён. Явные parent/import job
IDs включаются через read-only scientific registry; отсутствующий исторический
parent остаётся missing dependency. Ни raw SQLite, ни auth/session/ticket/node/
outbox/scheduler tables в пакет не попадают.

**До `Store.create_job`** функция сохраняет неизменяемый hash-bound server request
в `artifacts/research-export-requests/{sha}/request.json`. Это не browser path.
Settings содержат `schema`, sorted `job_ids`, `source_job_ids`, `request_manifest`,
`request_sha256`, `estimated_payload_bytes` (nonnegative int). Оценка включает
фактические выбранные file bytes, exact inline metadata, protocol и консервативный
file/manifest overhead. Queue должна закрепить CPU1/512MiB/GPU0, nonpreemptible,
one DATA-I/O slot и disk reservation `3×estimate +256MiB` сразу при create.
Нельзя сначала enqueue, а затем надеяться, что schedule успеет добавить budget.
Большой preflight/worker/hash download выполнять вне HTTP event loop.

Инвентарь — selected+declared parent private job folders и явно SHA-bound
scientific payload references в managed artifacts/datasets/episodes/assets/
qualification. Только названные научные расширения; unknown formats, source/env
и внешние неопределённые зависимости → `PARTIAL`. External payload без source
SHA не копируется. Ничего не сканируется рекурсивно от whole state/home. Все
оригинальные aliases/absolute references в raw JSON сохраняются; managed state
symlink допускается через проверенный resolved root. Alias к secrets/SQLite или
за managed roots не читается как допустимый artifact.

JSON/JSONL и доступный text проходят shared secret guard. TAR originals
проверяются streaming без extraction: безопасные names, никаких symlink/hardlink/
FIFO/device members, expanded/count bounds, inspected JSON/text и запрет SQLite
header. Неизвестные opaque arrays/weights не становятся qualified dataset от
расширения файла: server source producer и его admission отвечают за исходный
формат. Это не универсальный anonymizer/детектор зашифрованного секрета в binary.
Все unknown source/runtime dependencies сохраняются; generic worker сейчас
честно выпускает `PARTIAL`, а не утверждает законченный scientific study.

`run_research_export(store, job, config)` проверяет kind и exact server request,
source job fingerprints и snapshot имён файлов после очереди. Changed original
SHA не переименовывается в новую версию: export отвергается. Создаются только
`jobs/{export_job_id}/bundle`, `restore` и фиксированный `evidence.tar`.

TAR создаётся после `export_evidence` + **отдельного** `restore_export` и включает
только зарегистрированные обычные bundle members, bound collection protocol,
immutable manifest и restore receipt. Никакого `tar.add(state)`/произвольного
пути. Только после fsync и повторной проверки bundle временный TAR публикуется
как `evidence.tar`, затем сохраняется immutable `export-result.json`. Повтор при
уже сохранённом результате сверяет source request и TAR SHA/size. Прерванный
незавершённый bundle сохраняется и требует нового export job: originals не
удаляются, неподтверждённая частичная копия не публикуется.

Result aliases для API/UI:
`verification`, `archive_sha256`, `archive_bytes`, `files`,
`total_payload_bytes`, `independent_restore_verified`, `manifest_sha256`,
`missing_dependencies_count`. Полный worker result также хранит fixed paths,
restore SHA, missing ledger, source selection, actual/estimated bytes и budget.
`independent_restore_verified=True` означает проверку **сохранённых** selected
bytes в отдельной копии; общий статус всё ещё `PARTIAL`, если есть dependencies.
Это не физическая/научная квалификация или dataset admission.

`download_validation(store, job_id)` принимает только ID completed
`research_export`, проверяет фиксированный private path и полный SHA/размер.
Возвращает dict `{path:Path,size_bytes,sha256,media_type,filename,verification}`;
API предоставляет `GET /api/research/exports/{id}/download`. Path из browser
никогда не используется, source jobs никогда не перезапускаются. Смешанный
export сохраняет исходные contracts9D/6D и domains отдельно, не объединяет их
в один training head.

Тесты используют настоящий TEMP Store/SQLite и отдельный filesystem:
terminal validation/sorted dedup, server estimates/request tamper, parents/raw
failures/auth exclusion, actual TAR membership, independent restore/fixeddownload,
changed source files/rows/new files, managed state aliases, malicious original TAR,
credentials/SQLite, request symlink до записи, retained failures/partial ledger.
API/job_runner/scheduler hooks остаются у parent; production state и hardware
в этом atomic не трогаются.
