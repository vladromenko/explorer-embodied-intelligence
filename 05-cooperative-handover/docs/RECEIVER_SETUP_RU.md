# Metadata setup receiver05

`explorer.cooperative_receiver_setup.CooperativeReceiverSetup(service)` — новый
центральный helper; Root подключает service/API/UI отдельно. Constructor и
`snapshot(row)` не делают RPC, не открывают камеры/serial, не загружают модели.

```python
refresh(row, spec, actor=server_authenticated_actor)  # только fixed GET
snapshot(row)                                      # только memory cache
execute(operation, row, spec, body, actor=server_authenticated_actor)
invalidate_session(row)                            # только local generation
```

Операции `bind_guard`, `capture_empty_open`, `capture_empty_closed`, `reset_safe`.
Ответ refresh/snapshot: schema `explorer_cooperative_receiver_setup_v1`,
`operations[operation]={implemented,available,choices:[{choice_id,label,valid_for_s}],reason?}`,
`source_status`, `reason`, `received_ns`. Choices — opaque64hex, одноразовые,2s,
закреплены за точными session/epoch/process/Spec/actor/native grant/epoch/
calibration/interlock revision. Данные cache не объявляют execution authority.
При истечении cache Root должен предложить отдельный explicit refresh.

Каждый execute принимает `receiver_setup_choice_id`, `observing=true`,
`user_observed_test_confirmed=true`. Стандартные outer `session_id` и
`expected_epoch` разрешены только при точном совпадении row. Actor задаётся
server kwarg; URL, file paths, native profile IDs/arrays/uncertainty, frame IDs
и inline profiles из browser отвергаются.

Capture также требует `observation_choice_id`, `displayed_jpeg_sha256`,
`empty_gripper_confirmed=true`. Это **существующий** observation choice из
`CooperativeObservedService.display_observation`; пользователь должен получить
его JPEG через existing observed-frame endpoint. Helper проверяет actual
observed cache, served SHA, actor/context/current grant/boot/calibration и сам
извлекает исходный native frame ID. Он не вызывает `observe`, не продлевает
USER grant и не выдаёт новый кадр от отдельного camera owner. Source endpoint
`direction/observe` сохраняет настоящие cached measurements/original JPEG.

`bind_guard` переводит только выбранный opaque server choice в фактический
source profile ID для текущей verified follower calibration. Автоматического
выбора нет. Несколько профилей с разным/неэкспонированным направлением блокируют
bind с `MULTIPLE_DIRECTION_PROFILES_REQUIRE_SOURCE_REVIEW`; metadata не
догадывается, какой из них правильный. Если источник экспонирует одинаковый
проверенный closing_sign нескольких профилей, UI может выбрать один из них.

`reset_safe` требует отдельный `object_safe_no_dual_hold=true`, настоящий idle и
current USER grant, exact source interlock/fault ID+revision. Это не STOP и не
автоматическое снятие armed latch. Незавершённые/повреждённые WAL остаются
закрытыми до explicit source safe-reset. Ни metadata bind, ни captures, ни reset
не начинают native session и не являются готовностью полного handover.

Любой source mutation перечитывает grant/native/interlock и сверяет choice
identity, затем использует fixed `StationObservedHttp.cooperative`. Нет retries
или скрытого native begin/action/lease. Late reply после смены epoch/local
invalidation не восстанавливает choices/authority; реальные source metadata
факты не удаляются и не выдаются за hardware success. Root должен вызвать
`invalidate_session` на STOP/revoke/close; old row/epoch также блокируется
проверками самого workflow.

Проверки используют actual temporary workflow, observed-frame cache, COMMON
provider, WAL и fake native owners: served-image requirement, actual endpoint
pair/profile bind, actor/TTL/epoch/oneuse, numeric injection, conflicting
profiles, separate safe reset и late refresh fence. Физических команд,
изменений settings/lease/TTL и новых camera owners не выполнялось.
