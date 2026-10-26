# Pure receiver dual-hold interlock05

`cooperative_receiver_interlock05.ReceiverHoldInterlock` — metadata state machine,
не controller, не hardware permission provider. Constructor и методы не открывают
камеру/serial, не вызывают RPC/driver, не пишут файлы и не меняют rates/limits/leases.
Интегратор сохраняет Station и существующий write admission; этот atomic их не
редактирует. Без фактического подключения к native publication gate полный05
остаётся заблокированным.

Stable API:

```python
ReceiverHoldInterlock(*, clock_domain, boot_id, clock=time.monotonic_ns, state=None)
bind(*, binding, permission, direction_profile=None, expected_revision)
arm(*, interlock_id, expected_revision, permission, last_command)
check(*, permission, control_epoch, mode, target, last_command)
finish_challenge(*, interlock_id, expected_revision, permission, giver_release_receipt)
finish(*, interlock_id, expected_revision, permission, challenge_id, confirmation)
reset_safe(*, interlock_id, expected_revision, permission,
           object_safe_no_dual_hold, actor_id, native_mode)
status(permission=None)
snapshot_state()
```

`permission` — только actual snapshot COMMON provider после проверки current
grant/Event/calibration/observer. Browser не передаёт такой snapshot. Helper
дополнительно сверяет purpose `cooperative05`, native6 contract, PHYSICAL domain,
run/coordinator epoch/object, grant/provider/native epoch, actor, calibration,
boot/clock и исходный observation deadline. `status()` без current permission
никогда не объявляет sticky authority. `binding_current` доступен до arm;
`capability_ready` также требует source-bound gripper direction.

`arm` использует только ORIGINAL `Station.last_command`, взятый под Station
RLock после действительного sync_write и sample. Нужны returned `dispatched6`,
точный owned epoch/clock/sequence и native timestamp. ACK, desired target и
measured pose отдельно не заменяют этот receipt. Freeze — командные first5,
не измеренная stationarity/attainment; независимое stationary evidence остаётся
за координатором.

`observed_gripper_direction(open_original_bytes,closed_original_bytes)` строит
profile из двух сохранённых source-native наблюдений: реальные measured6,
verified calibration SHA, исходный frame SHA/clock/boot, native uncertainty,
отдельные authenticated USER labels empty-open/empty-closed. Raw byte SHA
сохраняются. Producer должен формировать эти records из своих actual caches,
Browser не вводит numeric endpoints. Направление выводится из различимых
измеренных endpoints; один normalized[0,100] contract или calibration range не
доказывает физический знак. Metric aperture, сила и contact не утверждаются.
Отсутствие direction оставляет capability not ready; консервативно можно latch
все6 без разрешения закрытия.

В armed режиме `check` отклоняет любой unmatched/new/unowned epoch, даже когда
обычный permission gate считает его legacy. Поэтому интегратор вызывает check
ДО early legacy return. Target должен находиться внутри неизменённых LOW/HIGH,
чтобы исходный clipping не изменил фактический вектор. First5 сравниваются точно;
gripper может только оставаться или закрываться относительно последнего
действительного `last_command.dispatched`. Относительно старой arm pose закрытым,
но относительно нового actual gripper открывающим, target тоже запрещён.

После STOP/focus loss/revoke/expiry latch остаётся armed. Существующий отдельный
original measured-position hold при завершении mode допустим; следующий target
не обходит latch через новый epoch/owner. Никакого timer unfreeze нет.

Для unfreeze к receiver finish требуется actual giver opening host-publication
receipt того же run/object/coordinator epoch с native guard grant. После него
выдаётся one-use Pi challenge с прежним protocol hold TTL (это metadata deadline,
не motor setting). `finish` принимает новый consumed independent statement
`object_transferred_to_receiver`, связанный с этим giver receipt. Wrapper:

```text
operator_confirmation: original consumed authenticated Inbox row
coordinator_verified_confirmation_created_after_challenge: true
coordinator_clock_domain: actual NX domain
coordinator_challenge_received_ns: NX receive of this Pi challenge
coordinator_confirmation_checked_ns: NX current verification time
```

Root обязан проверить происхождение giver reply и свежий Inbox response.
`created_ns`/`received_ns` сравниваются только внутри указанного NX domain,
Pi challenge deadline — только в Pi domain. Nano timestamps не приравниваются
Pi/NX clocks. Model, closed gripper и timer не заменяют independent transfer.
Успешный metadata finish открывает возможность закончить receiver skill; он
сам ничего не публикует и не объявляет physical success.

Safe reset требует original idle, свежий current USER grant, точный old
interlock ID/revision и отдельное `object_safe_no_dual_hold=true`. Новый USER05
intent может явно очистить старый cancelled latch того же follower calibration;
старый запрос не сбрасывает новое состояние. Snapshot state имеет SHA и сохраняет
armed latch при восстановлении. После нового boot/provider старое permission
binding не становится current. Persistence/atomic journal — обязанность outer
provider вне критического write hook; повреждённое состояние не отбрасывается
в пользу permissive default.

Интеграционный порядок: Station RLock → existing publication lock → provider lock
→ helper lock. Helper никогда не ждёт Station/provider locks. `_arm_guard`
координатора должен arm receiver И giver до receiver_at_object transition;
частичная ошибка не снимает уже установленный latch. Full execution authority
требует фактически установленного и current-bound interlock до physical protocol.
Goal/user lease expiry и explicit hold-successor lifecycle остаются отдельными
критериями; этот модуль не расширяет исходный TTL и не повторяет команды.
