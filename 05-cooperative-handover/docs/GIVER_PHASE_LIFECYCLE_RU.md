# Giver05: USER park → command-estimate successor → begin → resume

ENGINEERING_GLUE. Работает с **тем же** NativeCooperativeOwner, original
ManualArm/ManualTeleop, Publisher и Ruckig. Нового publisher/worker/feedback reader,
motor setting, controller constant, lease heartbeat или автоматического replay
нет. ROSMASTER readback остаётся OFF. Реальные RPC/GRANT/движения агентом не
выполнялись. Source HTTP подключён отдельным атомиком; central UI/lineage
подключает integrator. Наличие source route не означает live deployment.

Fixed authenticated routes: `POST /api/explorer/cooperative/phase/{park,adopt,begin,resume}`.
Nano Node proxy разрешает только те же четыре suffix под
`/legacy/explorer/cooperative/phase/`. GET, unknown suffix/query и actor_id из
phase payload не принимаются. Actor выводится сервером из actual observed-source
authorization receipt после private service Bearer authentication; для park
просроченного owner допускается сохранённый source-bound old receipt. Это не
произвольный browser actor. Central всё равно проверяет текущего пользователя
и opaque one-use choice перед forwarding.

`capabilities.explicit_user_giver_phase_http=true` появляется только после
регистрации actual fixed routes. Backend-only `phase_lifecycle.supported` сам
по себе не разрешает HTTP. Generic cooperative resume и retarget
begin/recenter/resume/input/base отклоняются при parked barrier; STOP остаётся
независимым. Fresh actor/model/constraint/epoch revalidation обязательна.

Factory startup attestation теперь hash-pins8 adapter sources, включая
`giver_phase_lifecycle05.py`. Это проверка original source с fake dependencies,
не source hardware dispatch qualification.

## Общая форма

```text
owner.park(body, authenticated_actor=SERVER_ACTOR)          CONTROL
owner.adopt_parked(body, authenticated_actor=SERVER_ACTOR)  metadata
owner.begin_parked(body, authenticated_actor=SERVER_ACTOR)  CONTROL
owner.resume_parked(body, authenticated_actor=SERVER_ACTOR) CONTROL
```

Common body:

```text
run_id, object_id, coordinator_epoch, authorization_id,
expected_state_id, expected_revision,
observing:true, neutral:true, user_observed_test_confirmed:true,
phase_id?: bounded server-derived source annotation
```

Actor — отдельный trusted server kwarg, не выбираемый browser. `phase_id` при
отсутствии остаётся None; source не угадывает соответствие Spec или camera phase.
Root берёт все IDs/CAS/phase/model refs из immutable source/current session,
а browser выбирает только server-issued opaque one-use action choice.
Poses, durations, source paths, profiles, models и numeric permissions в форме
не принимаются. В model/constraint может меняться только authorization lineage,
не сами исходные model/Spec/constraint bytes.

## park: настоящий исходный STOP

Добавляет `control_epoch` для policy либо `expected_native_generation` для
keyboard/gamepad. Сначала выполняется exact scoped original STOP, затем WAL
сохраняет parked barrier и park_request_id. STOP отменяет будущую публикацию;
он **не публикует новую arm hold pose**. Отдельный уже принятый endpoint может
закончиться; source не называет это measured hold/stationarity/attainment.

Response: `park` (source receipt с original stop return/old owner/native clock),
`native_command_called:true`, `interlock`,
`ready_for_command_estimate_successor` (только cached drained predicate),
`readiness_is_not_reference_or_attainment_qualification:true`.
Barrier может находиться в DRAINING: это ещё не доказанный command context.
GET/user refresh не продлевает lease и не запускает следующий этап.

После park **все arm targets запрещены**, даже unarmed/legacy/new authorization.
Настоящий zero base STOP по-прежнему проходит в исходный traced executor.
Parked state сохраняется через WAL; restart никогда не восстанавливает grant и
закрывает публикации также при previous armed=false.

## adopt_parked: только metadata под NEW USER authorization

Добавляет:

```text
park_request_id,
previous_authorization_id, previous_native_generation, previous_control_epoch
```

Нужны новый отдельный USER authorization после фактического park, тот же actor,
input mode/run/object/coordinator epoch и exact source CAS. Image должен быть
после park в том же boot. Различные названия Nano clock domains не приравниваются
произвольно: alias допускается только для source-known native_kernel_monotonic
при том же boot и **том же actual monotonic_ns callable**; relation записывается.

Actual Tap arm/base pending0, original stream inactive и original worker lock
свободен; original reference guard должен быть valid. Берутся actual issued9,
настоящие returned Publisher/base-zero receipts и reference SHA; unknown base
не заменяется нулями, journal не подменяет actual issued context. Changed pose,
reference fault, unelapsed/unknown original reference, foreign generation,
actor/input mode/model/constraint, stale IDs или late revoke дают NOT_READY.

Response: `successor` с origin `state_source=command_estimate`,
`measured_feedback:null`, `measured_feedback_mask=[false]*6`,
`measured_hold_or_stationarity_claimed:false`; `native_command_called:false`,
`execution_authority:false`, `interlock`.

Armed latch, used proof IDs и exact registered stationary Spec/model constraint
сохраняются. Existing constraint переводится на NEW authorization с SHA previous
registration; нельзя зарегистрировать другой или молча удалить старый.
Повторный NEW USER successor под тем же реальным parked context возможен без
дополнительного STOP или движения.

## begin_parked и resume_parked

Для policy `begin_parked` добавляет park_request_id, вызывает original begin и
оставляет CLUTCHED epoch. Он отправляет **настоящий original base0 request**;
lease не обновляется и arm target не публикуется. Нужен тот же сохранённый model.

`resume_parked` добавляет park_request_id и actual control_epoch для policy.
Keyboard/gamepad проходят напрямую из PARKED_NEW_USER в original manual resume.
Только этот отдельный USER control вызов использует прежний original resume и
его исходный .45s lease. Atomic source ownership/link снимает parked barrier,
сохраняя armed latch/first5 и требование нового receiver holding proof до opening.
Новое policy input после успешного ответа сохраняет full raw9, actual requested9
и exact carried constraint в исходном dispatch trace. Старый output не повторяется.

Responses CONTROL: `native`, `native_command_called:true`, `interlock`.
Поздний revoke, changed generation/reference/context до control call не получает
действующий owner. Failed final fsync сохраняет fault/pending latch и выполняет
только exact returned owner cleanup; expired authorization None можно cleanup по
закреплённому epoch/generation, foreign NEW grant/epoch не принимается за свой.
Истёкший за время final fsync .45s lease не возвращается как active phase.

Existing generic begin/manual resume/recenter/constraint replacement запрещены
при parked. Source HTTP generic resume должен также проверять этот barrier;
один текущий lease не означает full cooperative authority. Root `_authority`
должен требовать `release_boundary.parked is None` наряду со всеми прочими guards.

## History и пределы

WAL хранит последние32 control metadata events, monotonic ordinal, SHA chain,
`phase_event_count` и честный `complete_phase_event_bytes_retained`.
Это **не complete episode и не scientific dataset**. Used holding-proof IDs,
current origin/model/constraint/armed state не обрезаются. Root сохраняет полные
immutable API receipts отдельно; native capture остаётся raw science authority.
Ни одна filesystem операция не добавлена в Publisher/revoke/servo loop.

Проверки используют fake native transports и исходный dispatch ABI: policy,
keyboard/gamepad, actual correlated raw9/constraint after resume, unknown/missing
base, drift/reference/stream/worker/pending barriers, CAS/actor/input/model changes,
revoke/new-owner during WAL, final failure/expired lease/scoped cleanup, restart
unarmed parked state, no-fsync-underlocks, bounded history. Source Station.py,
training math16/18 и шесть исходных controller configs не менялись. Полное
физическое H5 recovery и реальная передача предмета не объявлены выполненными.
