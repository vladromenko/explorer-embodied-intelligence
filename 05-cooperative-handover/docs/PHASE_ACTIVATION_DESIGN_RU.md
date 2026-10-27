# Source-atomic USER phase activation

ENGINEERING_GLUE. Pure software CONTROL boundary поверх того же COMMON и исходного
SO101Station. Station.py, его30Hz, `_enable_hold`, motor/controller settings,
исходные goal deadlines и USER freshness/leases не изменены. Ни модель, ни второй
camera/servo owner не созданы. Реальные операции агентом не вызывались.

## ABI

```text
StationCooperativeInterlock.dispatch('phase/activate', {
  grant_id, expected_control_epoch, interlock_id, expected_revision, phase_id,
  observing: true, user_observed_test_confirmed: true, neutral: true
}, authenticated_actor=SERVER_ACTOR)
```

Это **CONTROL**, `native_command_called=true`: операция вызывает тот же исходный
`teleop/start` для pinned leader binding либо `policy/start` для pinned model
binding. `phase_id` — server-derived bounded annotation; central должен брать его
из подтверждённого Spec. Source не принимает poses, durations, models, profiles,
paths или motor limits из этой формы. Отдельные HTTP/Node/central/UI hooks Root
подключает после проверки source atomic; здесь они не объявлены развёрнутыми.

Успешный receipt `explorer_station_cooperative_phase_activation_receipt_v1`:
`activation`, актуальные `status`/`grant`/`interlock`,
`phase_activation_current=true`, `native_command_called=true`,
`dispatch_claimed_from_HTTP_acceptance=false`, `controller_settings_written=false`.
В `activation` сохраняются phase/native operation, old/new source owners, SHA
origin, **исходный неизменённый** preceding Station.last_command и его SHA,
held6, actual start return epoch, native clock/boot и явное отсутствие
start-as-dispatch/attainment claim.

## Atomicity и сохранение latch

1. Source проверяет exact current idle grant/actor/Spec/object/run/calibration,
   interlock CAS, parked source-owned actual hold, свежий measured6, точное
   равенство held6 без придуманного tolerance и USER image после actual hold.
2. PREPARED WAL fsync с прежним armed/parked state записывается **до** original
   start и вне Station/publication/provider locks. Любой restart с pending WAL
   закрывает все targets, включая случай previous armed=false.
3. Под исходным Station RLock → publication lock → provider lock повторяются CAS
   и current USER checks. Тот же COMMON вызывает original start ровно один раз.
   Его returned epoch/mode/current grant и полученный baseline/explicit_goal
   обязаны точно совпасть с source facts/held6. Новый owner и metadata activation
   связываются **до освобождения Station lock**, сохраняя armed latch и history.
4. `Station.last_command` не изменяется этой metadata link и его epoch не
   переписывается. Typed source lineage разрешает использовать именно прежний
   неизменённый receipt и фактический hold как predecessor до первого настоящего
   new-epoch write. После него применяется обычный original returned receipt.
5. Final WAL fsync выполняется **вне control locks**. Только атомарно связанный
   current grant/epoch/mode/actor может пройти prepared runtime exception;
   foreign/legacy epochs закрыты даже при armed=false. Уже допущенный bounded
   write может действительно вернуть receipt во время final seal.
6. Final failure устанавливает global fault **до** exact owned-epoch STOP.
   Неизвестный start return не позволяет взять произвольный текущий epoch для
   cleanup. Чужой новый grant не останавливается. Actual earlier/late writes
   сохраняются как реальные receipts. Pending/fault никогда не восстанавливает
   authority после restart.
7. После успешного fsync source заново проверяет current USER/native ownership.
   Истёкший/отозванный за время I/O grant не возвращается как current activation;
   только его exact old epoch может пройти scoped cleanup. Успешно sealed event
   при таком обычном прекращении authority остаётся историей, не fault fiction.

COMMON `require_start_admission` блокирует обычный отдельный BEGIN, пока hold
parked. Проверка cached-only; admission exception принадлежит текущему source
thread/operation/exact owner/state SHA, а не browser flag. Нет автозапуска,
повторных predictions, TTL extension, heartbeat или auto leases.

## Последующие фазы

После нового actual scoped STOP либо publication-denial measured hold следующий
successor использует **новый actual hold origin**, сохраняя предыдущий source
origin и phase activation history. Старый hold не переименовывается в новый.
Границы retained history конечны; исчерпание не запускает native start.

Coordinator должен явно park перед human prompt и получать новый USER grant
перед следующей actuator phase. Исходные unreceipted TTL/camera-loss branches не
повышаются до доказанного parked hold. Dispatch/pose/idle не доказывают grasp,
contact, stationarity, transfer или успех всей задачи. Full physical05 ещё не
подтверждён; этот source transition не является приёмкой оборудования.

## Проверки

`test_station_phase_activation.py` использует исходный worker, реальный COMMON,
interlock/WAL и fake buses: leader/policy + scopedSTOP/denial origins, restart
pending при armed=false/true, native start before worker, unchanged old receipt,
actual first new-epoch return, immutable first5/unknown gripper direction,
repeated phase→newhold→newUSER succession, stale/future/changed pose/frame/actor/
object/calibration/grant/epoch/CAS, malformed numeric input, no-fsync-underlocks,
actual worker during final fsync, foreign epoch denial, failed seal, concurrent
revoke/new grant, changed native baseline, unknown start return, expired final
response и foreign-grant cleanup CAS. Original Station byte SHA неизменён.

`test_station_parked_activation_gap.py` сохраняет negative characterization
старого двухвызовного пути с отключённым **только в fake test** новым admission
callback: worker успевает завершить новый BEGIN до отдельного bind. Production
callback не отключается; отдельный negative test доказывает отказ до native write.
