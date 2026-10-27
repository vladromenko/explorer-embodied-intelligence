# Giver05: durable publication latch

ENGINEERING_GLUE. Production `native_owner_mount` передаёт fixed source path
`ROOT/data/explorer-cooperative-giver-hold.json` в существующий
`NativeCooperativeOwner`. Ни original ManualArm/ManualTeleop, ни Publisher,
Ruckig, config, feedback OFF, numeric lease/TTL или servo mapping не меняются.
Файл содержит metadata запрета, а не новые controller settings.

`release_boundary.persistence` сообщает:

```text
installed, healthy, initialized, fail_closed_publication_gate,
recovery_required, restored_armed, fault, pending,
state_id, revision, last_sealed_sha256
```

Root должен требовать installed/healthy/initialized/fail_closed_publication_gate
равно true и recovery_required=false вместе с прочими **текущими** source
ownership/grants. Отсутствие поля у старого runtime не становится допуском.
Optional in-memory test binding честно возвращает installed=false/healthy=false.
Constructor/status не пишут файлы и не выполняют actuator operations.

До первого WAL fsync-сохраняется constant initialization marker. Перед explicit
metadata bind/arm/authorize-used-proof/reset сохраняется pending envelope, после
успеха — final envelope с SHA. Bounded no-follow JSON, mode600, exclusive temp,
atomic replace и file/directory fsync. Marker + потерянный WAL, повреждение,
pending или неизвестный state закрывают **все** arm publications. Recovery
repair marker сначала сохраняет pending/fault WAL; только подтверждённый reset
может финально запечатать безопасное unarmed состояние.

Publish и immediate revoke не выполняют WAL I/O. Durable barrier содержит armed
latch; latest pose и active authorization не обещаются актуальными после reboot.
Поэтому даже **валидный armed WAL** после нового owner construction требует
recovery и запрещает любую pose: closing, прежнюю pose, legacy/unowned caller и
новую USER authorization. Pending/grant никогда не восстанавливаются. Historical
events/grants внутри receipts остаются историей, а не разрешением. Used proof IDs
сохраняются. Публикация, уже допущенная до revoke, может вернуть настоящий receipt;
она сохраняется как host return, без MCU acknowledgement/attainment claim.

File fsync выполняется вне guard/teleop/publication locks. Pending mutation
закрывает новые публикации до final seal; несогласованность/revoke не оживляет
grant. В текущем armed runtime closing также проверяет прежний `_current`:
native owner/generation/epoch, original lease и наблюдаемое authorization. Это
закрывает legacy closing после потери разрешения, сохраняя прежние constants.

В production mount также обёрнут тот же traced `teleop.drive(values)`. Bound
cooperative/armed/recovery/fault/pending state блокирует все nonzero либо
неопределённые vector requests, даже при binding=None. Настоящий zero STOP всегда
делегируется исходному callback и сохраняет actual Tap receipt; host request не
объявляется достигнутой скоростью. Проверка и делегирование сериализованы с
изменением latch для nonzero/unknown. Zero STOP делегируется вне дополнительного
arm guard lock и не ждёт уже допущенный arm Publisher. Ни один lease не обновляется wrapper-ом. Chassis permit использует
тот же cached predicate. Это закрывает проверяемые keyboard/direct-teleop пути;
raw external ROS publishers вне audited native ABI не объявляются устранёнными.

## Explicit USER recovery

Existing `guard/reset` использует current USER activation и original STOP.
Production дополнительно требует server-pinned `expected_state_id` и
`expected_revision` из текущего source status, `observing=true`,
`object_safe_no_dual_hold=true`. Старая форма/CAS не снимает новый latch. Ни camera,
ни новый grant, ни web restart не снимают его автоматически.

Reset не выполняет HOLD/HOME/open/close и не очищает reference fault. После
неизвестного/blocked publication требуется отдельный существующий наблюдаемый
original factory reference. При смене kernel boot old fault сохраняется как
SHA/boot history, а действующая uncertainty отметка получает текущий native
clock: old/new boot timestamps не сравниваются. Это не очистка fault.

`guarded_reference` по-прежнему может признать только фактический новый original
reference receipt; metadata grant не заменяет его. Родительский rollback/update
должен отказать при armed/recovery/fault. Central-only rollback может оставить
исходный durable publication gate на месте. Старый source runtime без этой
логики нельзя подставлять поверх вооружённого состояния.

Scope — durable dual-hold publication prohibition, не полный H5 physical recovery,
не durable30Hz telemetry и не физическая приёмка. Actual command/phase recordings
и independent outcome evidence сохраняются отдельными существующими producers.

Проверки: fake original Publisher + новый owner instance, armed seal, opening
history без подмены latest pose, новый grant/legacy closing, corrupt/pending/
missing/null state/marker, USER reset CAS, reference-fault continuity across boot,
first/final fsync failures, no-WAL-I/O publication/revoke, вне-lock fsync,
pending revoke и один реально допущенный поздний return. Отдельный полный retained
original factory offline:6 cases passed, external drivers mocked, startup source
и controllers неизменны; реальные RPC/owner restarts/движения не выполнялись.
