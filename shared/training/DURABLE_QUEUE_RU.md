# Общая долговечная очередь

Все01..06 используют исходную единственную SQLite `jobs` table. Новая
`job_scheduling` хранит scheduling metadata и runtime отдельно от неизменяемых
старых config/result/events/cards; `job_scheduling_history` хранит изменения
расписания с SHA, `scheduler_usage` — fair-share budget*time charge, не научный
счётчик фактических optimizer updates.

Control-center hooks: `Store.schedule_job(job_id, options,
expected_revision=...)`, `schedule_status(job_id)`,
`request_job_operation(job_id, 'pause'|'resume'|'cancel')` и read-only
`explorer.scheduler.queue_status(store)`. Последний не создаёт metadata и не
стартует процессы. Root API может применять auth/CSRF как у существующей очереди.
UI должен использовать request_job_operation для явного пользовательского pause:
его флаг запрещает automatic resume после fair-share preemption.

Semantic slug, skill/version, backend, dataset SHA и GPU slot выводятся сервером
из существующего job. Клиент может менять priority[-10,10], preemptible,
проверенные DAG dependencies, not_before и бюджеты выше server minimum.
На работающем задании меняются только priority/preemptible. Неизвестный
dataset SHA остаётся null, не выводится из несуществующего доказательства.
Проверка текущего job status и изменение metadata выполняются в одной SQLite
транзакции. Worker claim привязан к точной revision выбранного плана: если
бюджет или зависимости изменились после планирования, процесс не стартует,
а следующий tick заново проверяет актуальный план. Новые source/runtime pins
завершённым или отменяемым jobs не добавляются; повтор существующего pin
возвращает значение без пересериализации исторического config.

Pure queue_plan учитывает role service+неограниченный aging, зависимости и их
failure/cancel/wait причины. Два CPU ядра оставлены control/camera; worker
affinity ограничивается выделенным набором. Одновременно максимум один GPU
lease, включая inference и SIGSTOP-процессы. Учитываются фактическая host UMA
available memory, оставшийся рост живых budgets,2.5GiB runtime reserve, disk,
существующие hot/critical thermal trips. Power/controller settings не меняются.
Это консервативные reservations, не полная OS/NVIDIA allocation isolation.
Memory pressure пытается освободить совместимый optimizer на checkpoint;
неподдерживаемый этап честно сообщает невозможность checkpoint preemption.

Worker bootstrap ждёт server-persisted PID/start_ticks/boot/nonce lease прежде,
чем импортировать job runner. Supervisor после restart усыновляет только точно
тот же живой worker вместо запуска дубликата. Старое наблюдаемое физическое
разрешение не восстанавливается. Исторические pinned releases не переписываются.

Shared ACT/DP объявляют checkpoint capability только внутри source-bound
optimizer. Scheduler пишет pause request для конкретного PID/start/boot/signature.
После завершённого optimizer step сохраняются настоящие model/optimizer/RNG/
sampler/dataset state, fsync pointer+receipt, затем SystemExit75 завершает процесс.
`job_runner` ordinary Exception handler не превращает этот exit в failed.
Supervisor проверяет receipt/checkpoint SHA и сам process exit и лишь затем
показывает `paused_checkpoint_process_exited`, memory_retained=false.
Resume создаёт новый процесс, сохраняя прежний source/runtime pin.

Другие этапы не получают fake exact resume: явная ручная пауза через SIGSTOP
помечается `paused_process_memory_retained`/exact_optimizer_resume=false и
удерживает ресурсный lease. Автоматическая fair-share/inference preemption
никогда не выдаёт такой SIGSTOP за освобождение RAM. После потери такого
процесса статус явно показывает lost process/no exact resume.

Source изменения checkpoint protocol намеренно отзывают прежние shared-graph
квалификации. Старые proof bytes сохраняются; новые CPU/native6/ACT9 qualifiers
обязательны перед включением новых runtime bindings. Физическое исполнение и
широкие H1–H5 результаты этой инфраструктурой не подтверждаются.

Новый GPU source graph3 включает checkpoint_control.py и требует явный
qualified_tensor_profile из actual optimizer result. Исторические graph1/2
остаются привязаны к своим исходным файлам. Новый64px/batch2/chunk4 FP32
receipt не допускает224px/batch32 или иные непроверенные tensor shapes под тем
же4GiB бюджетом. Model dimensions/precision и фактические settings проверяются
вместе с исходниками/runtime; число updates и seed не подменяют shape profile.
source04 `so101_skill_shadow` — отдельный CPU-only inference class:2CPU/2GiB,
GPU0, один interactive slot. Он не получает Diffusion runtime/history допуск.
`rosmaster_skill_shadow` использует тот же общий serial inference slot и бюджет,
но сохраняет отдельный ROSMASTER state106/action9 model contract.
