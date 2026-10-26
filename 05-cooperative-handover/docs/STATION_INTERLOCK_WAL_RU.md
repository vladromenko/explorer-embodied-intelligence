# Durable COMMON receiver metadata wrapper

`station_cooperative_interlock.StationCooperativeInterlock(provider,state_path)`
использует существующие provider/Station. Путь задаёт только server:
`station.root/cooperative-hold.json`. Constructor читает bounded no-follow JSON,
проверяет envelope/state SHA. Никаких native commands или camera/serial owners.

API для outer Node integration:

```python
status()                           # cached: no files/camera/provider.check
check_publication(epoch, mode, target, last_command=None)  # cached only
dispatch(operation, payload, *, authenticated_actor=None)
```

Операции: `bind`, `arm`, `finish/challenge`, `finish`, `reset_safe`.
Поддержаны aliases `finish-challenge`/`reset-safe`. Source server передаёт actor
kwarg либо trusted injected `actor_id`; он должен совпадать с actual grant.
Payload: только `grant_id`, `expected_control_epoch`, `expected_revision`,
`interlock_id` и typed fields operation: direction profile ID, giver release
receipt, challenge/confirmation либо explicit safe-object checkbox. Browser
native positions/uncertainty/inline direction profile отвергаются.

Provider.check проверяет текущие USER frame/native grant до metadata transaction.
После durable WAL current grant проверяется повторно под Station → publication
→ provider locks; arm берёт ORIGINAL actual `station.last_command`. Metadata
helper lock не ждёт этих locks. Capture/hash/profile file reads и fsync не
выполняются из publication admission.

Перед любой мутацией сохраняется pending WAL envelope с предыдущим exact state;
после — final state с SHA и cleared pending. File mode600, atomic replace и
file/directory fsync. Отклонённый запрос без изменения helper state запечатывает
прежнее состояние. Ошибка после мутации/final persistence либо startup
corrupt/pending/read failure ставит глобальный uncertainty latch. `status()`
остаётся доступным; любой последующий target, включая unowned legacy epoch,
получает `allowed=false`. Existing original measured STOP hold обрабатывает
исходный Station; wrapper его не заменяет и не объявляет target dispatched.

Recovery требует новый current authenticated USER grant в original idle,
`object_safe_no_dual_hold=true` и exact fault/interlock ID+revision. До успешного
final fsync runtime fault остаётся закрытым. Повреждённые original bytes не
публикуются как научный успех или как восстановленное управление.

На этом atomic direction profiles читаются только из server-managed
`cooperative-direction-profiles/<sha>.json`; данные должны быть сформированы
pure observed endpoint builder. Actual cache direction-capture producer —
следующий metadata-only этап, неподдержанная операция не угадывает знак.
Настоящий native gate/provider/Node hook подключает интегратор отдельно.

Проверки: настоящий COMMON provider с fake original owner, temporary WAL,
cached-only hook без camera/RPC/file reads, pending/corrupt restart, injected
final-fsync failure, unchanged rejected revision, fresh USER safe reset и
подмена IDs/numeric endpoints. Hardware, motion и full handover не выполнялись.
