# TRAIN-only forward audit B: точная runtime identity без смешения версий

A сохранён полностью: pre-model guard failure, 0 forward / 0 predictions,
остальные3 NOT_STARTED. Это не измерение model performance. Frozen source7002a18,
protocol SHA `bbd092ca6da934a484d5e18f16c718eda78ceb709c14c645c0d74501d5431eca`,
failure/resources и статический diagnostic сохраняются отдельными bytes/SHA.

Guard A ошибочно сравнивал distribution metadata `2.11.0` с module build
version `2.11.0+cu130`. B различает эти поля и не удаляет suffix: при подготовке
`torch/version.py` читается как bytes, фиксируется SHA; `__version__`, `cuda`,
`git_version` извлекаются только из единственных top-level AST literal
assignments. Eval, exec или импорт Torch при freeze отсутствуют. METADATA
bytes/SHA/version фиксируются отдельно. Runtime обязан точно совпасть с
полным module literal, отдельным distribution version, cuda/git literals,
путём и bytes загруженного version.py. Missing, nonliteral, duplicate,
bare-versus-full, другой suffix или изменённая METADATA отвергаются.
Python/NumPy exact guard остаётся прежним. Build с CUDA в строке версии
не разрешает CUDA execution: процесс по-прежнему CPU-only, CUDA не initialized.

Новый driver использует неизменные A preflight/admission/scorer/summary и
явную копию `run_cell`, где заменён только ошибочный runtime guard.
AST regression сравнивает весь остальной inference/scoring/hook body.
Все старые2090/2073 и B232 pins, source файлы, data, четыре weights/config,
образование state10, 1756 queries, targets, padding, baselines и phase/joint
metrics остаются прежними. Старые A файлы не редактируются. Новый base-admission
snapshot внутри B — только immutable preparation provenance, без forward.

После отдельного root-review B freeze: ZERO_ABS→ELAPSED_ABS→ZERO_INCREMENT→
ELAPSED_INCREMENT, 439 batch1 queries каждый, четыре новых serial units
CPU2 / MemoryMax3 GiB / swap0 / max900 s / LimitCORE0, без CUDA/EGL,
optimizer, posterior execution, physics, teacher, dispatch или новых weights.
Папки и unit names B отдельные от A. Исходная A попытка не повторяется и
не включается в performance denominator. Нет auto-fix/retry или выбора
модели по DEV/SEALED. Реальные forward B до нового review не запускаются.
