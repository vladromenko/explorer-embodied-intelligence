# Переносимый Study: V9 SCRIPTED_SIM

Новый read-only слой сохраняет конкретную кампанию `transfer-hover-development-v9-20261026` целиком: четыре заранее объявленные попытки, включая два неуспеха release/settle. Это инженерный development-протокол со стационарным симуляционным основанием и геометрическим teacher. Он не доказывает научную H1, обученную исходную политику, навигацию или физический навык.

`explorer.study_registry.registered_studies()` возвращает публичные ID без серверных путей. `inspect_registered_study(study_id)` строит граф свидетельств; `export_registered_study(study_id, destination)` создаёт CAS-пакет. Destination назначает сервер/worker, пользовательский HTTP-запрос должен содержать только зарегистрированный StudyID. Эти helpers не добавляют маршруты, jobs или записи в Store.

Включены исходные RGB PNG, requested/dispatched 9D, observation и causal renderer receipts, post-step native integration states, result/trace, параметры/seed, scene XML, исходный 6D scripted trace, source-archive/CAD/runtime bytes, source-verification, исходные trial snapshots, frozen protocol/benchmark, все отрицательные результаты. Отдельно сохраняются draft driver/protocol с поправкой **до первой попытки** и последующий forward-only contact analysis. Derived contact analysis явно отделён от исходного захвата сил. Старые абсолютные locator strings остаются неизменными данными; restore разрешает их только в архивированные blob-объекты и не открывает исходные пути.

Все четыре target-эпизода сохраняют `development`, `target_optimizer_allowed=false`. Исторический raw-флаг `optimizer_partition_allowed=true` относится к **source train**, сохраняется байт в байт и не становится разрешением обучаться на target. Номинальная предшествующая 7321 не добавляется в знаменатель четырёх новых испытаний.

При проверке выполняется только точная allowlisted версия `research/benchmark/evaluate.py`, SHA `352c62c3d58207dadb368f9250b5b14195addf12046d533077b9451f1d715f1a`. Она восстанавливает frozen statistical report из сохранённых independent result/phase/terminal receipts и trial rows. Физика, renderer, policy и архивированные Python drivers не исполняются. Это повтор статистического учёта, а не новая проверка контактов или повтор dynamics.

`study_bundle.export_study_bundle` сохраняет каждое логическое имя и неизменные bytes в дедуплицированном CAS. Manifest фиксируется SHA256; `archive_study_bundle` создаёт детерминированный regular-file USTAR без PAX/GNU/sparse headers. Перед tarfile parser выполняется ограниченная проверка сырых 512-byte headers и нулевого end padding. `unpack_study_archive` требует ожидаемые SHA архива и manifest, запрещает links/traversal/duplicates и ограничивает число и размер файлов. `restore_study_bundle` восстанавливает новый root и заново проверяет весь граф и frozen benchmark, не исполняя source drivers. Неуспешный export сохраняет частичный каталог без итогового receipt; originals никогда не перемещаются и не удаляются.

Границы: не более 20 000 логических файлов, 1 GiB исходных bytes, 64 MiB на файл, manifest не более 16 MiB. Worker/API должны резервировать место для CAS, TAR, unpack и restore отдельно; здесь нет scheduler или обхода его квот.

## Почему PARTIAL

Пакет может быть целостным и независимо восстановленным, но оригинальная повторяемость остаётся `PARTIAL`:

- Исходный archive прямо ограничен перечисленными Python/CAD/scene/libmujoco bytes. Нет полного OS/system-library inventory, всего NumPy compiled runtime, MuJoCo Python extension и остальных транзитивных зависимостей.
- Есть scenario seed и post-step native states, но отсутствует полный первоначальный RNG/native reset-state receipt. Нынешняя версия Python и экспортёра фиксируется отдельно и не выдаётся за исходный runtime испытания.

Эти пробелы нельзя закрыть выдуманным receipt или заменой старых файлов текущими библиотеками. Поэтому COMPLETE здесь запрещён даже для самосогласованного поддельного manifest.

## Qualification

`PYTHONPATH=shared:00-control-center/src:01-autonomous-learning/src ~/.local/share/explorer/venv/bin/python scripts/qualify_registered_study.py --output <новый-приватный-каталог>`

Команда читает только зарегистрированные originals, создаёт CAS/TAR, независимо распаковывает и запускает restore в новом PID. До restore child устанавливает Python audit guard, запрещающий открытие каждого исходного study-файла. Затем повторно проверяются SHA всех originals. Сохраняются реализация, runtime экспортёра, исходные SHA и отдельные receipts. Команда не обращается к сети, базе jobs, камерам или роботам. Выходной каталог квалификации не должен находиться в production state.

Unit fixtures синтетические: проверяют отрицательный исход, immutable target partition, порчу исходных RGB/CAD/protocol, отсутствие отрицательного result, дополнительную started attempt, object tamper, ложное COMPLETE/learned/optimizer, TAR traversal/links и restore после переименования всего оригинального дерева в недоступный путь. Это не физические данные.


## Реальное свидетельство 2026-10-27

`_registry/evidence/study-v9-recorded-portability-20261027.json` связывает actual run на commit `81bc463`: 7 640 оригинальных файлов, 301 174 250 логических bytes, 6 817 CAS objects (142 072 541 bytes), TAR 150 620 160 bytes. За 93.57 s выполнены export, независимые unpack/restore в новом PID и точное восстановление frozen report: SUCCESS=2, FAILURE=2, исключений нет. Все исходные SHA после qualification неизменны; child запрещал открытия 7 640 исходных путей и не попытался их читать. Новых dynamics/render/optimizer/device вызовов нет.

Manifest SHA `7a27d6d16cf7bf00ae13c4ed7c95cb425be7b4d7af0d0f2dc452ceeed7eebdd9`; TAR SHA `8539784570ddecfa664f59ca7b2a97b4939549df423b658b3419e6fb8ff28fdb`; private qualification SHA `4b8b08d65d96bd329d4267247f88b152fc9f8f7a0050d43679e351988c0ac37e`.

Наблюдавшийся transient `ru_maxrss` parent процесса: 879 140 864 bytes; это не комбинированный cgroup memory proof. Для будущего queue worker рекомендуется резерв 2 GiB, CPU1, GPU0 и disk не менее 3×logical bytes+256 MiB. Это измерение standalone parent, а не production/cgroup qualification нового worker; typed queue reservation описан ниже.

## Typed shared queue

`explorer.study_job.request_settings(store, study_id)` вызывается сервером **до** `Store.create_job('study_export', '06', settings)`. Он делает только ограниченный обход/lstat metadata и сохраняет immutable request sidecar; не читает/хеширует source payloads, не вызывает `inspect_study` и не импортирует Torch. Исходные большие campaign-файлы фиксируются stat fingerprint; небольшие зарегистрированные context/evaluator bytes связаны заранее известными SHA, чтобы та же immutable release-copy могла быть прочитана worker в другом каталоге. Их действительные SHA проверяются только в worker. В settings уже есть точный неотрицательный `estimated_payload_bytes`.

`run_study_export(store, job, job['config'])` работает только для durable running `study_export` с теми же source-request fields. Он проверяет immutable sidecar/stat inventory, выполняет реальную инспекцию, CAS export, USTAR, независимый unpack и restore в новом filesystem root. Сам worker уже изолирован от CC; здесь restore выполняется в том же worker PID, что явно записывается `independent_restore_process=false`. Отдельный new-PID restore доказан предыдущим standalone qualifier и не выдаётся за каждый queue job.

Бюджет: CPU1, 2 GiB памяти, GPU0, nonpreemptible, один общий ingest/disk/network slot; disk=3×estimated payload+256 MiB. Для соблюдения дискового бюджета redundant `.staging-bundle` удаляется **только после** подтверждённого TAR unpack в окончательный `bundle`. Затем сохраняются bundle, TAR и полный restored root. Оригиналы и отрицательные свидетельства не удаляются. Любая ошибка/отмена оставляет partial outputs без completed receipt; повтор прерванного задания требует нового job, не перезаписывает старые попытки. Поддержка exact optimizer resume к этому виду jobs не заявляется.

`download_validation(store, job_id)` принимает только 32hex ID завершённого typed job, проверяет фиксированный `<state>/jobs/<id>/study.tar`, его SHA и размер. Клиент не передаёт filesystem paths. API/UI подключения остаются в основном CC приложении. Новый lazy branch не меняет training math и не обновляет историческое controller proof bc01/515.
