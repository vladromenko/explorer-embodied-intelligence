# Реальный controller pause/restart · ACT6 и Diffusion6

Immutable8480b618e97c23810c1843f56343fa43d10b3b12, включая финальный
rosmaster_skill_shadow dispatch hook, Store terminal/cancel/revision fences,
общий Supervisor, bootstrap и checkpoint-aware optimizer. Фактически
2tests passed108.89s, userunit113.389s, CPU200%/MemoryMax4GiB/swap0.
Пиковая память cgroup3,481,874,432B, memory max/oom events0.

Для каждого backend реальная отдельная private Store прошла полный путь
enqueue→scheduler→source/runtime pin→bootstrap→job_runner→official optimizer.
Uninterrupted8 сравнен с другим job: после observed2 updates pause request,
fsynced model/optimizer/RNG/sampler checkpoint, exit75, старый PID отсутствует,
lease/RAM освобождены; новый PID возобновляет ровно6 updates.
Модели, optimizer, losses, TorchRNG и sampler точно равны. Python/NumPy RNG
проверены как продолжение собственного checkpoint каждого paused job, поскольку
неиспользуемый global RNG независимых процессов может первоначально отличаться.

ACT pausedRSS1,063,530,496B; Diffusion1,061,957,632B. Финальные weights SHA:
ACT4305ead320fe9c301a960317b1f4ec852150b61125d8607a2ebbf5dd9ea6c5b1,
DP ad5624f9191b923f00d501a2fc5bdcfedc70cfc91597b9674112acbb9f97548d.
Это разные backend с собственными RNG/temporal/history semantics; совпадение
сравнивается с собственным8step reference, не между ACT и DP.

`NATIVE6_QUEUE_FINAL_20261026_SOURCE_LIBRARY.json` хранит four actual job
configs/pins и hashes обоих полных git archives:987 regular source files,
одинаковые tarSHA7b822e6c…/treeSHA52dd05e4…. Оригинальные releases/privateSQLite,
actual worker logs и final checkpoint payloads оставлены в
`/mnt/explorer-data/explorer-work/qualification-official-native6-queue-final-20261026`.
Это полные воспроизводимые source references private qualification; не запись
в production SourceLibrary и не public API worker smoke.

Все cohort episodes синтетические SIMULATION с реальным officialDataset
write/readback, skill/version/source/split checks. Никакие камеры, servo/network
устройства, Hub, simulator или robot hardware classes здесь не запускались.
Физический навык, законченная задача и CUDA resume данным CPU run не приписаны.
Исторические proof bytes сохранены; index native6-queue-final-20261026.json
выделяет эти actual controller receipts отдельно от optimizer-only проверок.
