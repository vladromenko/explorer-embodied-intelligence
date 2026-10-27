# Явные USER-фазы giver через Root

`explorer.cooperative_giver_phases.CooperativeGiverPhases` подключает четыре
существующих source-пути в `CooperativeNativeSessions`. Это инженерная связь
пользовательских действий с прежним original executor. Она не выдаёт
разрешения на физическое выполнение и не подтверждает удержание предмета.

| Root POST `/api/cooperative/…` | Source POST `/legacy/explorer/cooperative/…` | Эффект |
|---|---|---|
| `native_phase_park` | `phase/park` | CONTROL: прежний cancel-only STOP, затем durable barrier; возможен DRAINING |
| `native_phase_adopt` | `phase/adopt` | METADATA: новый отдельный USER grant и command-estimate successor; без lease/команд |
| `native_phase_begin` | `phase/begin` | CONTROL: policy CLUTCHED с настоящей исходной публикацией base0; без prediction/resume |
| `native_phase_resume` | `phase/resume` | CONTROL: отдельный прежний policy/manual resume и original0.45s lease |

Все четыре тела браузера одинаковы:

```json
{"session_id":"server-session-id","expected_epoch":"server-epoch",
 "role":"giver","native_control_choice_id":"server-opaque-64hex",
 "observing":true,"user_observed_test_confirmed":true,"neutral":true}
```

Иные поля отклоняются до source RPC. Браузер не выбирает native IDs, revision,
park token, модель, pose, constraint, actor или phase. Root выводит их из
подтверждённой SkillSpec, сессии, текущего original source reply и непрерывной
цепочки source receipts. Source capability
`capabilities.explicit_user_giver_phase_http=true` обязательна. Отсутствующий
путь, fault, WAL recovery/pending, DRAINING или reference fault не превращаются
в разрешение. Отсутствие measured feedback само по себе не блокирует настоящий
command-estimate контракт.

`native_refresh` выполняет только GET, constructor/cached native snapshot —
ноль RPC. Выбор одноразовый, не старше2s на сервере/1.5s в UI; он фиксирует
session/process/Spec/actor/native owner, а для phase operations ещё и
`protocol_state` плюс digest текущего pending/checkpoint. После получения
резервирования phase/checkpoint повторно сверяются до запроса. Резервирование
`begin_native_phase_change` охватывает source RPC, сохранение reply и lineage;
`end_native_phase_change` вызывается в finally. STOP не ждёт резервирования.
Pending step допускает изменения только в существующем evidence checkpoint;
окно epoch-bound native challenge не ослабляется.

ADOPT требует фактически полученного через `observed_setup.permissions` нового
разрешения именно этой Root-сессии, actor и Spec. Просто новый authorization ID
в source GET недостаточен. Исходный `observed_test_binding` не перезаписывается.
Parsed source reply сначала fsync-сохраняется как immutable native-control
artifact. Только затем `native_grant_successors.giver` может сослаться на него.
Проверяются previous/new owner, первоначальный USER anchor, исходный input mode,
Spec/model, точное перенесение зарегистрированного stationary9 constraint,
park token, source clock/boot, реальный issued9+base0 и reference SHA. Feedback
остаётся `null`, mask `[false,false,false,false,false,false]`; источник состояния
`command_estimate`. Измеренная поза/стационарность/контакт не утверждаются.

Каждый новый artifact содержит предыдущую запись lineage. Проверка идёт по
этим immutable originals до первоначального USER grant, независимо от последних
64 строк UI-проекции. Максимум1024 successors за одну проверку; более длинная
история блокирует новый dispatch до отдельного разбора и не отбрасывается.
Оригиналы native-control сохраняются после STOP, включая parsed late replies.
Orphan custody помечена `authority_publication_allowed=false` и не может
публиковать новый owner или lineage. Parsed JSON не называется исходными байтами
HTTP/ROS и не доказывает actuator attainment.

Перед source phase-действием старый giver transport отсоединяется. ADOPT/PARK
не подключают модель/transport. BEGIN требует текущей готовой policy head,
но не вызывает prediction. ATTACH — следующее отдельное USER действие.
Root запоминает точный возвращённый owner ДО artifact fsync. STOP в этот момент
может отменить его без ATTACH; поздний ответ не оживляет epoch. Cleanup использует
только реально возвращённый epoch/generation и никогда не заимствует текущий
чужой owner. Повторы, heartbeat и автоматическое продление отсутствуют.

UI сбрасывает подтверждения после каждого действия/blur/навигации/STOP, убирает
старые displayed giver frames после phase change и блокирует выбор при смене
фазы/checkpoint. Неопределённый ответ не повторяется автоматически. Полный
cooperative release/transfer и physical authority требуют самостоятельных
current interlock/evidence проверок; этот модуль их не подменяет. Новый source
parked-release transaction и parked observation scope интегрируются отдельно.

Проверки: temporary Store, настоящий source FastAPI + fake original executor,
localhost authenticated Node→LegacyBridge→source policy/manual roundtrips,
opaque choice/actor/source/CAS/phase negatives, immutable successor chain,
STOP до remembered reply и во время artifact fsync, чужой новый grant, сохранение
orphan originals; отдельные Node UI-тесты и thin authenticated Root API allowlist.
Реальные source RPC, USER grants, моторные команды и deployment не выполнялись.
