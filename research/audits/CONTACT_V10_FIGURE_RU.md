# V10: проверяемый график четырёх сопоставленных development-сценариев

Новые PDF/PNG/два CSV и provenance находятся в `research/paper/figures/` под
префиксом `contact_development_v10`. Использованы публичный receipt SHA
`2552d94ddaffca33d9669f3e4c0eceefd4416542a756641d4a20a58d51af85d5`,
независимый report SHA `d0556fae852031a8673ff0de0a90f8ff0430f401494753dbe9465e32bd5ba4b8`
и frozen protocol SHA `87f8c80a391f06d57263098d3da5c23007d0e811c57f62488a69006dd174ae45`.
Скрипт проверил 84 input-файла (148 790 379 B): report/campaign/protocol,
16 raw result, 16 producer-bound initial manifests и 48 raw XML/native/substep
файлов. Нативные состояния, MJB, модели и архивный Python не исполнялись.
Payload всех 16 initial snapshot повторно не проверялся этим plotting-run;
их прежний независимый аудит и producer manifest SHA остаются отдельными фактами.

Каждая колонка содержит одни и те же четыре ранее осмотренные development-сцены,
а не held-out/случайную выборку. Восстановление контакта: 2/4, 3/4, 4/4, 4/4.
Парные discordances против captured-physics: quarter timestep — 1 gain/0 loss;
thicker bottom и low drop — по 2 gains/0 losses. Другие четыре-cases comparisons
и исходные неудачи не исключены. 14 000 contact substeps и 3 200 discarded
constructor/reset steps не являются независимыми trials. Для этого фиксированного
осмотренного набора не строится популяционный confidence interval и не
вычисляется significance: таблица содержит точные описательные парные counts.
Неизвестная генерализация ими не оценивается.

В первой панели — только frozen terminal contact predicate. Во второй —
`1000*max(0,-minimum_pair_signed_distance_m)` мм по всем записанным контактам.
В третьей — terminal mean bottom-contact force на предмет в world-Z, Н;
отрицательный знак означает вниз. Знак не заменён модулем. CSV сохраняет
полные float64 значения (17 значащих цифр), flags, case/attempt IDs и SHA.
Колонка `original_V9_full_task_success` буквально сохраняет исходный reported
simulation outcome для контекста; новый контактный outcome не заменяет его.

Quarter-dt всё ещё имеет outside-failure при supported/settled. Thicker bottom
и low drop восстанавливают contact predicate во всех четырёх случаях, но дают
до 17.21 и 5.45 мм transient signed penetration. Opposing floor/bottom trap
наблюдался в новых baseline raw-контактах; уникальная причина solver/contact
остаётся AMBIGUOUS. Learned repair, физическая точность и новый полный
манипуляционный навык не проверены. Initial snapshot restore/reexecution для
этих V10-попыток остаётся UNQUALIFIED; original OS closure — PARTIAL.

Raw native-trajectory/substep SHA впервые закреплены независимым анализатором
после завершения producer unit. Plotter повторно сверил текущие bytes с этими
SHA; это **не** добавляет задним числом отсутствовавшие producer raw hashes.
Исходные V9 outcomes, отрицательные результаты, defaults и scientific originals
не изменены. H4/значимость/новизна/mastery этим рисунком не заявляются.

Реальный unit `explorer-contact-v10-plot-20261027-a1.service`, invocation
`36865013c2af4f4683fb3fccd2cb9e5d`: exit0, 4.784s. CPU1/512 MiB/swap0,
inside-cgroup peak 184 815 616 B; все memory.events 0. Matplotlib 3.6.3 Agg,
NumPy 1.26.4 из существующего `/usr/bin/python3`; ACT/DP/GPU environments не
менялись. Два рендера в одном процессе/окружении дали одинаковые PDF и PNG.
Межплатформенная/межверсионная побайтовая идентичность не заявляется.
Standalone PDF — одна страница, subset CID TrueType embedded, без Type3;
PNG осмотрен: clipping/overlap не обнаружены. В fullpaper source добавлены один
ограниченный абзац и рисунок; полный новый manuscript не собран из-за отсутствия
TeX compiler. Старый трёхстраничный build receipt оставлен историческим.

Повторное построение выполняется стандартным Matplotlib Agg без GPU в own user
unit с MemoryMax=536870912, MemorySwapMax=0, CPUQuota=100%, RuntimeMaxSec=120.
Передайте `scripts/plot_contact_probe_v10.py` аргументы `--receipt` (этот public
receipt), `--raw-root` (точная копия перечисленных V10 inputs), `--output` (новая
папка вне original raw root) и `--unit` (имя того же user unit без `.service`).
`MPLCONFIGDIR` направляется в новую частную plotting-папку, не в policy runtimes.
Положительные и отрицательные metadata guards проверяет
`tests/test_contact_v10_plot_data.py`. Private actual render originals/cache/log:
`/mnt/explorer-data/explorer-work/contact-v10-figures-20261027-a1`.
