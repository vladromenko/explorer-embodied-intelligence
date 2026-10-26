# Выбранный научный экспорт и независимое восстановление

`shared/explorer_shared/research_export.py` — ENGINEERING_GLUE, без torch,
MuJoCo, устройств, сети, БД, обучения или удаления originals. Это конкретный
упаковщик выбранного графа свидетельств по `collection-export-v1.json`; он не
заменяет recovery backup и не подтверждает научные гипотезы или физический навык.

## Интерфейс для серверного worker `research_export`

1. `explorer.research_export.registry_from_store(store, job_ids, *,
   artifact_nodes=(), artifact_roots=None, event_ids=(), skill_ids=(), episodes=(),
   metric_checks=(), dependency_inventory_complete=False)` читает существующую
   SQLite через `mode=ro`, `query_only`, одну read-транзакцию. Он НЕ создаёт Store
   и НЕ вызывает его write-capable `connect`. Выбираются только jobs и их явные
   parent/import/training/retry IDs; связанные по точному job ID events и cards;
   дополнительно только явно выбранные event/card IDs. Auth sessions, tickets,
   node inventory, outbox и scheduler tables не читаются.
2. Сервер добавляет точные необходимые artifact nodes. Браузер передаёт только
   выбранные ID, **никогда** filesystem paths, SHA, registry или флаг полноты.
   Неизвестная зависимость означает `PARTIAL`, а не поиск всех файлов в state.
3. `export_evidence(new_destination, registry, managed_roots,
   protocol_path=..., protocol_sha256=...)` сохраняет exact bytes и возвращает
   `{manifest_path, manifest_sha256, verification, files, total_payload_bytes}`.
4. `verify_export(manifest_path, bound_sha256, metric_verifier=None)` независимо
   проверяет SHA/размер/пути/связи. Ничего из bundle не исполняется.
5. `restore_export(manifest_path, bound_sha256, new_independent_destination,
   metric_verifier=None)` делает новую отдельную копию, повторяет проверку и
   сохраняет `restore-verification.json`. Исходный manifest остаётся побайтно тем
   же. Только receipt отдельного восстановления может дать `VERIFIED_COMPLETE`.

У нового job не должно быть motor capabilities. Destination должен находиться
вне selected managed roots. Нужны отдельные серверные API/auth/download/UI hooks;
этот atomic их не добавляет. Не запускать export на event loop HTTP обработчика.

## Registry и точная область полноты

Schema `explorer_research_export_registry_v1`:

- `root_ids`: выбранные artifact IDs; `nodes`: явный dependency graph.
- Каждый node: `id`, `role`, `source_id`, `sha256`, `dependencies` (ID).
- File node: `root` (ключ серверного managed_roots), `relative_path`.
- Маленький exact UTF-8 original: `inline_utf8`, создаваемый `inline_node`.
- `split`: исходный train/development/sealed_test; `usage`: provenance, fitting,
  normalization, checkpoint_selection или evaluation. Sealed запрещён в трёх
  fitting/selection ролях; упаковщик вообще не вызывает fitter.
- `run_ids`, `episode_ids`, `job_ids`, `source_commits`; для episode metadata
  проверяется наличие всех полей фактического collection protocol.
- `missing_dependencies`: явные записи причины; `dependency_inventory_complete`
  по умолчанию false. Только проверенный серверный source adapter вправе объявить
  состав зависимостей известным. Без этого итог всегда `PARTIAL`.
- `metric_checks`: `{protocol, trials, report, implementation}` — четыре node ID
  для frozen benchmark protocol, JSONL всех trials, сохранённого report и exact
  generating source. Каждый должен быть выбран и сохранён.

Полнота означает полноту **заявленного выбранного графа**, не всего компьютера,
не всех экспериментальных данных и не исполнение master prompt. Реестр и поля
SHA не являются подписью или разрешением пользователю подменять originals.

Raw config/result/event/card strings сохраняются без reserialization, если
прошли secret guard. Jobs metadata — новая именованная проекция SQL колонок;
Raw error text также сохраняется exact bytes после secret guard; при отказе
остаётся SHA/reason и статус PARTIAL.
Canonical identity — отдельный derivative node с exact raw SHA/dependency,
numbering epoch и захваченной проверенной Store identity. Legacy raw bodies
не переименовываются и не переписываются. Проверка копии проекции не является
независимым восстановлением всей исходной SQLite/migration index.

## Секреты и файловые ограничения

Разрешены только явные научные roles. Нельзя экспортировать SQLite целиком,
credentials/auth/session/ticket/SSH/Wi-Fi paths, `.env`, `.key`, `.pem`.
Запрет проверяется и для locator, и для **resolved target** symlink. Внутренний
безопасный alias сохраняется в locator provenance; bundle содержит обычные файлы.
Symlink за managed roots, traversal, sockets/FIFO/devices, graph cycles, дубликаты
IDs, неполные/unreported edges, чужие лишние файлы и неверные SHA отвергаются.

JSON/JSONL проверяются рекурсивно на credential поля; private keys, Bearer/URL
credentials и environment-style secrets в доступном тексте отвергаются. Renamed
JSON в `.bin` тоже проверяется. Secret-bearing raw original **не исправляется**:
Store adapter удерживает его SHA/reason и оставляет export PARTIAL. При отказе
упаковщика потенциально запрещённые новые копии удаляются из нового destination;
остаётся только rejection receipt. Originals никогда не удаляются.

Это не универсальный детектор секрета в произвольном binary. Серверный allowlist
source adapter должен выбирать только известные scientific formats и не допускать
пользовательский blob под видом model weights. Нет anonymization claim. Возможная
анонимизация обязана создавать отдельную производную копию и карту преобразований.

Копирование streaming 1 MiB: pinned regular FD/no-follow, before/after
inode/size/mtime/ctime, actual SHA, fsync. Ограничения по умолчанию: 20 000 файлов,
8 GiB/file, 64 GiB total, 16 MiB inspected JSON, 32 MiB manifest; caller может
только ужесточить их. Никакого cross-DB/filesystem atomic snapshot claim.

## Независимые метрики и проверка

`metric_verifier(blobs, hashes) -> report` — явный доверенный callback сервера.
Он получает exact protocol/trials/report/implementation bytes + SHA. Callback
обязан сверить generating implementation SHA со своей квалифицированной версией
и пересчитать report штатным `research/benchmark/evaluate.py`. Упаковщик никогда
не запускает присланный Python из bundle. Без callback проверки метрик остаются
`NOT_RECOMPUTED`, а restored export — `COMPLETE_UNVERIFIED`, даже если все SHA
совпали. Failure/UNKNOWN/intervention и SIM/PHYSICAL/OFFLINE_REPLAY не выкидываются
и не смешиваются.

`tests/test_research_export.py`: отдельное восстановление в fresh Python process
после удаления originals; пересчёт actual benchmark tables; неверная метрика
при корректном file SHA; exact whitespace raw bodies, mixed numbering epochs,
parent failures/UNKNOWN; исключение auth-table secrets; secret aliases/plaintext;
реальная модификация source после copy fsync; tampering/path/bounds/extra files;
PARTIAL недостающего graph/episode metadata; sealed fitting denial.

Ни один тест не обращается к production state, устройству или optimizer.
