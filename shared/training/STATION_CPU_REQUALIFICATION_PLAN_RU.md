# Новая CPU qualification после Station publication hook

Этот план не запускает роботов, cameras, policies на hardware, Hub download или production Store. Старые R2 proof/weights и профиль4 остаются неизменными. `scripts/qualify_current_cpu_sources.py` создаёт новый stat/source-bound plan для **чистого отдельного Git worktree** с полным commit SHA и отдельно переданными окончательными Station/Gate SHA256. Изменения исходников после freeze отвергаются до optimizer.

Только последовательные transient user units: CPU2, MemoryMax4GiB, MemorySwapMax0, OMP/MKL/OpenBLAS threads2. Каждый phase проверяет действительные kernel cgroup limits и имя своего unit до pytest/policy imports, сохраняет runtime/package RECORD/official-source bytes и implementation hashes до/после. Plan mode импортирует только stdlib; подготовка не загружает модели.

- ACT6: существующий официальный8-update тест; RGB32, batch2, chunk4, source measured native6→issued native6, saved official processors/readback и shadow на SIM fixture. Нативные ranges/normalization не меняются.
- Diffusion6: существующий официальный4+durable restart4 тест; RGB64, batch2, history2, horizon8, action4, seed41, CPU pinned persistent Diffusion runtime. Ни ACT queue semantics, ни12D не подставляются.
- Fused106 ACT: существующие8 updates, RGB32, batch2, chunk2, state106/action9, train-only normalizer/masks/heldout. Это отдельная CPU qualification, не разрешение native9 CUDA для106.

Все три model.safetensors hashes сравниваются точно с сохранёнными R2 refs. Observed reload0 также должен остаться0. Никакие tolerances не смягчаются. Source/native6 Diffusion graph содержит18 файлов, включая весь `so101_station.py`; новый PublicationGate отдельно фиксируется как auxiliary source вместе с controller/harness. Подлинные неуспехи сохраняются в новом phase tree с runtime-after/failure, старые proof файлы не перезаписываются.

После успешных трёх фаз отдельный extractor читает собственный checksum-bound DP checkpoint только `torch.load(weights_only=True)`, проверяет metadata signature и счётчик8; затем готовятся **новые датированные** qualification/runtime/implementation/source binding файлы и candidate ACTIVE pointer. Никаких вручную переписанных старых SHA. Готовый candidate должен быть проверен настоящим `qualified_diffusion_runtime` против соответствующих текущих source bytes/installed runtime.

Actual Supervisor→bootstrap→job_runner ACT/DP pause→oldPID exits→newPID resume сравнивается с собственным8-step reference отдельно, после staged current binding/source freeze. Существующий controller test сохраняет exact model/optimizer/loss/RNG/sampler проверки. Пока candidate не подготовлен и не закреплён source commit, orchestrator намеренно блокирует phase `controller`.

Пример подготовки (значения commit/Station/Gate задаёт основной агент после последнего review):

```bash
python scripts/qualify_current_cpu_sources.py --source-root <frozen-worktree> --output <new-private-output> --commit <full40sha> --station-sha256 <final64sha> --gate-sha256 <final64sha> --label 20261027_STATION_R3
```

Команды фаз запускаются соответствующим interpreter через согласованные own units; имена, source hashes, тесты и неизменные параметры находятся в `plan.json`. Orchestrator не редактирует app, Station, shared math, defaults или source helpers. Полный package/OS closure и физическое мастерство этим не объявляются.
