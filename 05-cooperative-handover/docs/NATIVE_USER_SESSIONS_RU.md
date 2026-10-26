# Отдельные пользовательские native-сеансы05

`explorer.cooperative_native_sessions.CooperativeNativeSessions` связывает
существующие original Nano/Pi owners с подтверждённым metadata intent. Constructor
и cached snapshot не делают сетевых запросов/команд, не загружают модели и не
запускают lease/worker. Native control возможен только после двух отдельных USER
grants и `bind_observed_test`; warm compute сам разрешением не становится.

Центральный сервис поддерживает `native_refresh` с `session_id`,
`expected_epoch` и server authenticated actor. Это только чтение source status.
Ответ `snapshot.native_controls` имеет поля:

```json
{
  "schema": "explorer_cooperative_native_controls_v1",
  "roles": {
    "giver": {
      "input_mode": "keyboard",
      "operations": {
        "native_begin": {
          "implemented": true,
          "available": true,
          "choice_id": "server-issued-opaque-choice"
        }
      },
      "reason": null
    }
  },
  "native_controls_started_by_snapshot": false
}
```

Если current cache отсутствует/истёк, `roles={}` и
`reason=EXPLICIT_NATIVE_REFRESH_REQUIRED`. One-use choices связывают точный
session/process/spec/object/actor и native authorization/grant/epoch/generation;
срок2с относится к snapshot choice, не меняет original motor lease. Повтор,
другая роль/эпоха/оператор, новый native owner и pending protocol step отклоняются
до управляющего запроса. Browser не передаёт native tokens, IDs, URL, matrices
или targets.

Все отдельные USER operations имеют тело
`{session_id,expected_epoch,role,native_control_choice_id,observing:true,
user_observed_test_confirmed:true}`. Для begin/recenter также нужно
`neutral:true`. Возвращаются parsed source receipt и новый GET-only controls
snapshot; HTTP acceptance не объявляется hardware dispatch/attainment.

| operation | реальный существующий путь |
|---|---|
| `native_begin` giver manual | same-owner `cooperative/prepare` + metadata `bind`; resume отдельно |
| `native_begin` giver policy | `prepare`, `bind`, immutable stationary9 `constraint/register`, original `begin`; без resume/attach |
| `native_recenter` giver policy | отдельный original `cooperative/recenter`, без auto resume |
| `native_resume` giver | original cooperative policy resume либо scoped original manual resume |
| `native_begin` Pi | COMMON atomic `policy/start` либо `teleop/start`; требуются native write-permission gate capability и current grant |
| `native_attach` | только уже работающий exact source owner; отдельный pinned warm callback нужен лишь для policy role |
| `native_lease` | только giver policy, отдельное USER действие в прежний existing lease API |
| `native_stop` | exact scoped original native STOP |

Pi не получает выдуманного resume/recenter/lease. Manual giver сохраняет
существующий keyboard/gamepad input; helper не создаёт пустой heartbeat, который
мог бы перезаписать ручные controls. Для SO101 giver показан явный blocker
`USE_EXISTING_DIRECT_PI_LEADER_SESSION_NO_SECOND_OWNER`: этот helper не подменяет
существующую прямую measured-leader session и не создаёт второго bus owner.

При late response после cooperative STOP сохраняется отмена protocol epoch.
Cleanup использует только полученный точный native epoch/generation; stale choice
или новый owner не являются основанием для STOP. Неизвестный ответ не
превращается в success или автоматический повтор. Source replies в журнале
помечены как parsed authenticated JSON, не original source byte hashes.

Подключённые два текущих USER owners отражаются отдельно:
`execution_authority.both_current_user_grants_and_native_owners`. Полный
`explicit_user_observed_test_current` остаётся false, пока receiver transport не
подтверждает `cooperative_dual_hold_publication_interlock_current=true`. Permission
gate перед записью сам по себе не доказывает dual-hold interlock, grasp/contact
или physical mastery. Это отдельный незакрытый software критерий05.

`tests/test_cooperative_native_sessions.py` использует настоящий temporary Store,
native FastAPI activation/cooperative routes и COMMON Pi provider с fake original
owners. Проверяются отдельность begin/resume/attach, отсутствие model inference
и auto lease, actor/role/epoch/one-use/expiry, authentic native identifiers,
publication-gate negative, late STOP cleanup, новый owner и explicit recenter.
Реальные native RPC, HOME, arm/chassis команды и deployments не выполнялись.
