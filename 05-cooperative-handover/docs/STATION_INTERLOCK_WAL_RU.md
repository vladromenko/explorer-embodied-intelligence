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

Операции: `bind`, `arm`, `finish/challenge`, `finish`, `reset_safe`, `direction/observe`.
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

`direction/observe` получает только `grant_id`, `expected_control_epoch`,
`label=empty_open|empty_closed` (`open` — alias), `frame_id`,
`displayed_jpeg_sha256`, `observing=true`, `empty_gripper_confirmed=true`,
`user_observed_test_confirmed=true`. Actor задаёт source server. Значения
суставов, знак движения и uncertainty из browser не принимаются. USER должен
отдельно получить и показать указанный исходный JPEG; endpoint ничего не двигает.

Producer берёт exact JPEG из существующего camera cache и настоящие cached
follower6 из ORIGINAL Station. Frame receive ≤ measurement ≤ cutoff в одном
Pi boot/domain, measurement не старее существующего observation-контракта500ms.
Camera exposure sync не заявляется. Под original locks копируются metadata;
чтение calibration, hash и file/directory fsync выполняются после/до locks.
Не открываются camera/serial и не обновляется native lease/USER grant.

Сохраняются original JPEG и source JSON в bounded managed
`cooperative-direction-captures/<capture_id>.{jpg,json}` с mode600, exclusive
create и fsync; возврат содержит actual source SHA. Неудачное создание профиля
оставляет исходные записи и явную причину. Неоднозначные/отсутствующие endpoints
не получают direction profile. Попарное сравнение timestamp допускается только
для одного текущего boot/domain/actor и той же verified follower calibration.
Исторические часы разных boot не сортируются вместе.

Для STS3215 в исходном position mode один encoder tick соответствует
`100/(range_max-range_min)` native gripper units. Range берётся из exact
SHA-bound original connected calibration, без register readback/изменения.
Это только оценка квантования нормализации; физическая погрешность позиции,
метрическая ширина, сила и факт удержания предмета остаются неизвестными.
Направление выводится из двух явно помеченных USER пустых endpoint observations,
а не из названия `close` или нормализации. Empty-closed не становится squeeze
target для предмета.

Профили находятся в `cooperative-direction-profiles/<sha>.json`, связывают SHA
обоих исходных records и ID/JPEG каждого capture. При startup и explicit bind
их originals перечитываются и проверяются; это не выполняется из control loop.
Повреждённый уже зарегистрированный source закрывает глобальный latch.
Publication/status используют только memory snapshots. Настоящий native
gate/provider/Node hook подключает интегратор отдельно; source software tests
не подтверждают физическое направление без настоящих USER captures.

Проверки: настоящий COMMON provider с fake original owner, temporary WAL,
cached-only hook без camera/RPC/file reads, pending/corrupt restart, injected
final-fsync failure, unchanged rejected revision, fresh USER safe reset и
подмена IDs/numeric endpoints. Hardware, motion и full handover не выполнялись.
