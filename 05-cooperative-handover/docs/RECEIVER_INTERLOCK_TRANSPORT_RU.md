# Центральный адаптер блокировки receiver05

`NativeReceiverTransport` использует существующий authenticated Pi request и
`StationObservedHttp.cooperative`. Constructor не выполняет RPC или native
commands. Исходные `_current` mode/epoch/calibration/USER lease checks сохранены.
Модель, warm и metadata не продлевают grants или моторные сроки.

```python
NativeReceiverTransport(..., dual_hold_integration_ready=False)
observe_state()
interlock_state(refresh=False)
arm_release_guard(row)
finish_challenge(row, actual_giver_opening_receipt)
finish_interlock(row, challenge_id, correlated_evidence.source_receipt)
```

`row` содержит точные `session_id`, `protocol_object_id`, `epoch`. Metadata calls
используют source-issued interlock ID/revision, exact current grant/native epoch
и actual authenticated actor из собственного grant. Inline browser positions,
новый binding и автоматические bind/reset не создаются. `arm` проверяет original
returned write reference, его digest, native6, epoch/domain/sequence.

`observe_state` разделяет текущего native owner, source capability/current
binding, armed state и подтверждённое интегратором наличие **обоих** guard/finish
hooks. Финальный `cooperative_dual_hold_publication_interlock_current` истинен
только при всех source checks и server-only `dual_hold_integration_ready=True`.
Флаг по умолчанию false; Root должен подключить receiver-FIRST arm, затем giver,
а также independent finish. Одна current permission не объявляется готовностью
handover. Cached `interlock_state()` не делает RPC и всегда возвращает final
current authority false; `source_current_at_last_fetch` — факт прошлого чтения.
Explicit `refresh=True` делает только GET.

`finish_challenge` возвращает original Pi challenge и собственный NX receive
stamp. Receiver остаётся armed. После этого Root запрашивает новое независимое
`object_transferred_to_receiver` подтверждение, привязанное к actual giver
opening receipt ID. `finish_interlock` принимает только исходный consumed receipt
`{kind:authenticated_operator_confirmation, confirmation:...}`. Проверяются
actor/run/object/role/after-command и NX created≥реальный NX receive challenge.
Adapter сам формирует typed native wrapper; Pi отдельно проверяет свои часы,
boot, revision, challenge expiry и одноразовость proof. Nano/Pi/NX timestamps не
сравниваются друг с другом.

Late arm после STOP может оставить настоящий source latch armed; такой ответ
не восстанавливает старую authority и не очищает новый grant. Ошибка, revoke,
focus loss и истечение срока не вызывают unarm/reset. Metadata operations не
отправляют servo action и не объявляют удержание/достижение или полный успех.

Проверки: actual COMMON/WAL/facade с fake original Station, cached snapshots без
RPC, source/actor/revision/unknown-direction/expiry negatives, late arm→STOP→new
grant, actual durable OperatorEvidenceInbox confirm/consume и независимый finish.
Настоящих native RPC, моторных публикаций, USER grants или физического теста
не выполнялось. Pi goal TTL и короткий native giver lease остаются отдельным
software ограничением human-prompt flow; адаптер их не расширяет.

Root coordinator теперь связывает обе стороны: перед grasp check сначала
вооружается исходный receiver publication guard, затем giver. Частичная ошибка
не снимает уже сохранённый latch; workflow запрашивает scoped STOP у владельцев.
Transfer confirmation сначала получает новый Pi challenge, привязанный к
реальной opening publication ROSMASTER; только затем создаётся независимый
operator Inbox prompt с этим native receipt ID. Прежний receiver-close ID не
подменяет событие открытия подающего. Подтверждение передаётся неизменённым
из consumed source receipt, unfreeze завершается до перехода в SO101_FINISH.
История максимум8 реальных opening publications сохраняет evidence, когда
последующая same-pose publication уже заменила last_publication.

Это программная связка границ, не физическая приёмка. Наблюдаемые endpoint
captures, текущий native reference и работающий phase/hold-successor цикл
по-прежнему необходимы. Исходные native TTL/lease не увеличены и не продлеваются
этой интеграцией автоматически.
