# Source-owned контекст припаркованной фазы

`cooperative_held_scopes05.py` выдаёт `explorer_cooperative_held_scope_v1` через
два фиксированных authenticated GET без тела и query:

* Nano: `/legacy/explorer/cooperative/phase/held-status` через прежний Bridge,
  source `/api/explorer/cooperative/phase/held-status`.
* Pi: `/station/observed-test/cooperative/held/status`.

Capability `parked_observation_scope_http` появляется в giver capabilities или
Pi cooperative interlock status только после установки HTTP handler. Pi dynamic
getter намеренно не вызывается из `provider.status()`: тот может уже держать
provider lock, тогда как getter использует порядок Station→publication→provider.
Создание getter/client и обычный cached status не запускают этот GET.

Ответ содержит role/ready/reason, точные binding/native_owner, actor, источник
origin+SHA, настоящий source boot/clock/stamp и независимые сведения о состоянии.
Всегда `dispatch_permission=false`, `stationarity_verified=false`,
`object_holding_verified=false`, `physical_attainment_claimed=false`,
`native_commands_sent=0`, `native_lease_renewed=false`. Даже `ready=true` относится
только к source-контексту наблюдения. Независимые USER stationary/hold/outcome
proofs и полномочия движущейся роли проверяются отдельно.

Giver требует текущего NEW USER authorization того же source actor/input mode и
исходного run/object/coordinator epoch в `PARKED_NEW_USER` либо
`BEGIN_CLUTCHED`. Проверяются initialized healthy durable WAL/marker, отсутствие
pending/recovery/reference fault, original worker/arm/base drain, точные
epoch/generation, original reference guard и настоящий Tap issued9 с base0.
Arm receipt и command9 должны совпадать с запечатанным origin. В CLUTCHED BEGIN
original executor действительно публикует ещё один base0; этот новый literal
receipt сохраняется отдельно и допустим при неизменных arm/q9. Состояние —
`command_estimate`; measured feedback остаётся null с false-mask. Журнал никогда
не используется как выдуманная текущая поза.

Original reference/cache/marker/WAL чтение идёт вне teleop/publication locks,
затем revision/binding/parked/persistence/actual publications повторно сверяются.
Новый `scope_reference_reader` использует прежний original reference с
`clear_cooperative_fault=false`: GET не может снять reference fault даже после
нового reference artifact. Прежние управляющие callers сохраняют прежний default.
Вместо `mux.status()` под control locks копируются только cached fields, поскольку
общий status вызывает chassis permission/camera callback.

Pi требует запечатанный actual original STOP/denial measured-hold origin,
текущий same USER grant/owner и полную source binding. Existing camera/check
выполняется вне control locks; WAL/marker читаются отдельно, затем под
Station→publication→provider сверяются revision/current permission. Важны
same boot/clock/provider/calibration/idle epoch, свежий measured6≤500ms, точное
равенство held6 без tolerance, current observed deadline и реально подтверждённый
кадр после возврата original hold. Поздний USER observe использует собственный
`observed_monotonic_ns`, а исходный grant creation stamp сохраняется как есть.
Getter не продлевает ни наблюдение, ни motor lease, не делает serial read/write.

Unknown/corrupt/missing WAL, новый owner/actor/object/epoch/calibration/clock,
stale/future/changed измерение, новые arm/base receipts или reference fault дают
`ready=false` с причиной. Никакие faults/latches не сбрасываются. Результат —
снимок на source cutoff; последующая публикация обязана независимо пройти
свои native guards, такой GET не резервирует будущие полномочия.

Проверки используют настоящие source providers/Node/Bridge/FastAPI и original
Station worker с fake buses. Reader/camera/WAL barriers доказывают, что STOP/revoke
не ждёт их control locks и поздний getter не возвращает ready. Factory startup
proof pin расширен с9 до10 adapter modules. Original Station SHA, ROSMASTER
контроллер, feedback OFF, limits, leases и streaming constants не изменены.

Отдельная следующая интеграция: Root per-role authority/observation scopes и
явный metadata held/arm с сохранением frozen origin/history/usedproofs. Generic
parked arm/rebind и существующие dispatch/motion gates этим getter не ослабляются.
Физическая cooperative квалификация остаётся незавершённой.
