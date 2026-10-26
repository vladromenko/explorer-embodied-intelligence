# Две настоящие CPU-модели в одном процессе

Finite isolated run действительно загрузил сохранённый `RosmasterSkillPolicy`
со state106/output9 и source04 `load_shadow_policy(...allow_simulation=True)`
с measured-state6/output6. Выполнены по2 настоящих Torch inference каждой head на
отдельных исходных development frames/SIM states; models не обучались заново.
Источник честно `SIMULATION`: это не движение, handover или live queue proof.
Production admission не получает allow_simulation; архивные metadata не изменены.

Совместный RSS после загрузки846938112B; peak895504384B (~0.834GiB).
Весь run6.07s, загрузка giver4.785s и receiver со source admission0.678s.
Inference9:55.9/71.2ms; inference6:34.2/33.6ms. Torch CPUthreads2,
оба loaded policy parameter sets проверены CPU-only. Запрошен isolated scope
MemoryMax4GiB/swap0/CPUQuota200%; измерение RSS независимо записано самим процессом.
Network/process calls заблокированы Python audit hook, control API отсутствуют.

`scripts/qualify_cooperative_cpu_pair.py` воспроизводит run с явными original
paths/job ID и новым output directory. SQLite/WAL source registry копируется
побайтно до read-only queries; source/checkpoint/envelope/RGB hashes проверяются,
повторная проверка после inference подтверждает сохранение originals. SO101
cutoff взят из настоящего original SIM dispatch receipt и помечен offline;
монотонные часы Pi/Nano/NX не приравниваются. Target actions/phase labels не
передаются в actor observation106. Native9/6 остаются раздельными.

Public receipt `_registry/evidence/cooperative-dual-cpu-finite-20261026.json`
связывает exact config/input/output shapes, checkpoint inventory hashes,
framework versions и source SHA с raw receipt SHA. Его область — только эта
пара и этот CPU2 runtime/profile. Будущие модели, image shapes, official runtime,
другие source graphs, queued actor lifecycle и physical skill не становятся
qualified автоматически. Warm snapshot сохраняет отдельный false флаг
`paired_runtime_benchmark_qualified` до появления проверяемого точного binding
для фактически выбранной production пары.

Raw receipt: private managed qualification-cooperative-dual-cpu-20261026-a.
Original actual CPU106/source04 model artifacts остаются в прежних qualification
каталогах; weights/raw source payloads не добавлены в Git. Helper tests2PASS
проверяют bounded read-only SQLite snapshot без Torch import и original writes.
