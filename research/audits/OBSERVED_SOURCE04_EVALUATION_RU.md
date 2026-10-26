# Программный владелец наблюдаемой проверки source04

`explorer.so101_source_evaluation` — ENGINEERING_GLUE. Это typed owner отдельной
попытки **текущей** source04 ACT/DP модели, а не historical station_rollout/hold.
Импорт, capabilities и request construction не загружают Torch, не создают camera/
serial owner и не запускают управление. У агента нет разрешения вызывать реальное
`authorize`, `policy/start`, `action`, test или resume: все проверки этого atomic
используют только явно названные SOFTWARE fixtures.

## Общая native граница

Используется **один** `StationObservedPermission` из common provider direct03;
нового authority boolean или ServoOwner нет. Source binding содержит purpose
`source04_eval`, exact model job/SkillUUID/version/specSHA/checkpoint contentSHA,
source_domain и native6 contract. SIMULATION нельзя авторизовать этим provider
для физической Station.

Ожидаемый facade:

- `permission.check(grant_id,binding,expected_control_epoch)` → точный текущий
  grant receipt, `execution_permission=true`. Camera/feedback сами не authority.
- `permission.command(grant_id,binding,operation,body)` →
  `{status:<original Station result>,grant:...,native_command_called:true}`.
  Проверка и original command атомарны на том же native Station lock.
- `permission.revoke(reason,expected_grant_id=...,expected_control_epoch=...,
  expected_binding=...)` — scoped CAS; поздний old cleanup не трогает нового user.
- Existing transport `station.status/observation/frame`; frame возвращает exact
  JPEG bytes и lowercase pinned-source HTTP headers. transport.execution_domain
  объявляется серверным адаптером, никогда browser body.

Ни одного отдельного unguarded `station.command` в evaluator нет. Native grant
обновляет epoch внутри original policy/start; source owner получает новый epoch
из reply.status, затем повторно проверяет grant. STOP только exact owned epoch.
При неизвестном start response epoch не угадывается и unscoped STOP не посылается;
common provider отвечает за собственный late/unknown original call cleanup.
Original lease/watchdog/limits/calibration остаются неизменными. Action request
использует тот же `duration_s=1`, что существующий station_runtime, в пределах
неизменённого original native cap; новый watchdog или настройка не вводятся.

## Подготовка и user flow (API/UI у parent)

`request_settings(store,model_job_id,body)` принимает только finite duration,
review timeout и обычные descriptions object/scene/goal. Повторяет actual current
source `_metadata`/admission; hold/trajectory и неподдержанная phase-reference
plan не становятся object task. Возвращает typed request; разрешения он не даёт.
Канонические task phases имеют явный deterministic object/goal reference map.
Фазы/text/context не передаются в actor.

`run_so101_skill_eval(store,job,config, station=...,permission=...,...)` — future
isolated worker entry; default transports отсутствуют и invocation без bound
COMMON owner отвергается. Root должен регистрировать typed job/API, не открывая
его через generic job inputs. Для physical попытки нужна самостоятельная текущая
user-observed сессия. Worker runtime CPU only; не использовать checkpoint-freezing
SIGSTOP как physical pause/resume.

Два поддержанных пути:

1. Рекомендуемый: job без grant сначала **неподвижно** загружает saved native
   adapter, публикует `awaiting_source_observed_grant` +trusted worker session.
   UI показывает exact common camera challenge; только user авторизует.
   `attach_permission(store,job_id,grant_id,epoch,owner_session_id,permission=...)`
   проверяет real native grant и durable current worker CAS. Сам attach не
   создаёт authorization. Worker повторно проверяет его перед native start.
2. Уже готовый current grant может быть server-bound в request. Проверяется
   перед и после load; истечение не возрождается, никакого auto authorize/observe.

При первом варианте медленная загрузка не расходует свежесть будущего user grant.
Без attach worker заканчивает unknown attempt без native calls и не отзывает
какое-либо чужое разрешение. User подтверждает display/space один раз после
подготовки; продолжение observer lease управляет common browser UI, не worker.

## Настоящий loop и исходные данные

Каждый шаг получает новый pinned cached JPEG и измеренные native6/mask, сверяет
camera/calibration/boot/domain, JPEG/decoded SHA, source order/units и chronology.
ACT — history1, saved official processors; DP —2 разных chronological frames и
его квалифицированный4-step chunk. Исполняется только текущий первый native6
output; остальные chunk predictions не объявляются dispatch.

Перед и после load/inference, перед native operations и при receipt polling
проверяется current grant; cancel после inference также блокирует action. Exact
phase annotations приходят от root UI и не попадают в модель. Для разных машин
сравниваются отдельно host transport elapsed и native source stamps; NX/Pi
monotonic не вычитаются напрямую.

Original HTTP action reply сохраняется как **accepted**, а не dispatch. Затем
worker ждёт actual original `last_command`: exact epoch/sequence/frame/clock,
requested/dispatched native6. Это host sync-write evidence, не measured attainment
или удержание. Без него ни sample, ни qualified trace не создаётся.

Сохраняются отдельно original observation/JPEG, prediction, request, accepted,
actual native step, initial/rotated epoch/status, permission receipts и scoped
STOP. Namespace/fixed private paths ограничены текущим job. Raw failures,
partial attempts и original scientific bytes не переписываются.

Runtime qualifier допускает native saved ACT/DP adapter с exact checkpoint SHA.
Injected software predictor — только SIMULATION diagnostic; такой script не
может начать physical Station и **никогда** не публикуется как qualified source
модель. Warm loader должен вернуть тот же проверенный native adapter; неизвестный
proxy требует отдельного typed qualification adapter. Default loader исполняет
реальные existing NativeACTPolicy/NativeDiffusionPolicy, не scripted teacher.

## Фазы и независимый предметный исход

`current_phase(store,job_id)` и
`record_phase(store,job_id,{phase_id,expected_revision,expected_control_epoch},
authenticated_actor=...)` — durable metadata only. Нельзя skip/relabel назад,
применить stale revision/native epoch или продвинуть фазу до actual sample текущей
фазы. Cancel/ended native session запрещает новые annotations. Эти поля не actor
inputs и не разрешения движения.

После bounded loop native управление **сначала** scoped STOP/revoke. Затем
SourceOutcomeInbox создаёт отдельные вопросы по сохранённым фазам и whole task.
Он переиспользует existing OperatorEvidenceInbox authentication/session/expiry/
cancel/one-use guards. `confirm` принимает affirmative observing+note, actor только
из server session. `record_status` отдельно хранит failure/unknown. Пропущенное
подтверждение остаётся unknown; оно не превращается в success и не исключается.
Correlation alias для Inbox не является доказательством distinct physical object.
Root показывает original frames/timeline и exact after-command context; нет JSON
или ручного ввода native IDs.

Qualification требует current cohort recheck, genuine saved runtime, все actual
Spec phases и independent successes всех фаз/полного предметного исхода. Тогда
сохраняется model/processor/eval/raw/native/RGB-bound `QUALIFIED_SOURCE_MODEL`
trace, тот же SkillUUID/source version, **development** source attempt. Selector
`transfer_source` повторно проверяет всё это; target source transfer остаётся
отдельной следующей версией, target native9 FK/IK, target-rendered data, training
и independent target eval. Source raw angles не копируются в target head.

Нет current actual source object qualification от этого atomic: положительные
parser fixtures — structural SOFTWARE, loop fake predictors — diagnostics only.
Ни одного физического наблюдаемого испытания агент не выполнял. Root API/UI/
worker-classification/native HTTP facade assembly остаются отдельной интеграцией.

Перед native start сохраняется exact snapshot owner/adapter implementation bytes
и он перепроверяется после попытки. Это явный ограниченный source inventory,
не complete OS/runtime closure. Native-library/package portability отдельно.
