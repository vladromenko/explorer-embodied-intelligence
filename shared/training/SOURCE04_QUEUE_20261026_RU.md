# Source04 после checkpoint-aware общего optimizer

После d03f1ba method/provenance headers фактические source04 квалификации
повторены на source73bacf2, включая checkpoint_control.py в source graph.
Прежние CURRENT и CURRENT_STRICT receipts не изменены. ACTIVE pointer теперь
выбирает отдельный CURRENT_QUEUE набор.

ACT6:1pass14.35s,8CPU updates, loss1.107820→.293283, model reload0 и actual saved
official processor comparison0. Native6 saved-policy adapter снова реально
выполнен только на offline development observation с SIMULATION source.
RSS1,178,361,856B; kernel cgroup peak1,243,443,200B.

Diffusion6:1pass20.39s,4actual updates +durable interruption+4resumed updates,
loss1.290305→.689718, model reload0. RSS1,227,026,432B, cgroup peak1,368,850,432B.
Оба isolated user unit: MemoryMax4GiB, swap0, CPUQuota200%; memory max/oom0.
Полные package RECORD/location/version и official/repository source SHA
совпали до/после запуска.

Checksum-проверенный torch.load(weights_only=True) дал metadata signature
`21d5df7e2c28c8b1ca4fb3bb4230d9b59070a1150f920dfb8de406e4393badac`,
равную pointer/checkpoint/result. Root runtime selector действительно проверил
новые proofs, binding
`8bec6b3c19d9b0bc42c40d70a51fec4bdb735e3a83f1f257a2dfd8ef24835b04`.

Веса model.safetensors ACT4305ead3… и DP149e662f… точно совпали с прежними
strict квалификациями: control/provenance дополнения не изменили model math.
Actual raw runs: `/mnt/explorer-data/explorer-work/qualification-source04-queue-20261026`.

Эти fixtures не подтверждают навык или физический успех. Здесь проверены math/
source/runtime/save/resume; worker-controller qualification и GPU qualification
имеют отдельные source graphs и receipts. Public production queue readiness этим
запуском не приписана. Камеры, serial, Hub, simulator и motor APIs не запускались;
private qualification Store не является production-state.
