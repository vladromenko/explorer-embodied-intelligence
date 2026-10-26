# Одно текущее разрешение наблюдаемого теста Pi для04 и05

`StationObservedPermission` использует **тот же** original SO101Station и
существующий Pi camera cache. Constructor не читает камеру/руки. Challenge,
показ pinned JPEG, authorize, check, observe и revoke — metadata-only; ни одна
из них не вызывает Station.command. Модель, свежие измерения или камера сами
по себе не создают разрешение. Оно не означает grasp/contact/stationarity/success.

Binding фиксирует purpose `source04_eval` либо `cooperative05`, actual native6
contract, skill UUID/version/original Spec SHA; policy — model_job_id и
checkpoint_content_sha256. Для05 также фиксируются cooperative_session_id,
coordinator_epoch и object_id. Native provider сохраняет фактический follower
calibration SHA и source boot/control_epoch, ничего не калибруя и не меняя.

Фактический authenticated Node API:

- POST `/station/observed-test/challenge` `{binding}`;
- GET `/station/observed-test/challenges/{challenge_id}/frame`: exact JPEG и
  X-Frame-ID/X-JPEG-SHA256/X-Boot-ID/X-Monotonic-NS;
- POST `.../authorize`: binding, challenge_id, displayed_jpeg_sha256,
  expected_control_epoch и три explicit USER booleans: observing,
  robot_workspace_checked, user_observed_test_confirmed; actor_id только от
  trusted authenticated central proxy;
- POST `.../observe`: grant_id/binding, expected_control_epoch, **действительно
  показанные** frame_id+SHA, observing и тот же server actor;
- POST `.../check`: grant_id/binding/expected_control_epoch;
- POST `.../command`: grant_id/binding, operation, body original ABI;
- POST `.../revoke`: exact expected_grant_id/binding/expected_control_epoch;
- GET `.../status`: current permission metadata. Inventory содержит тот же
  `station_observed_permission` и capability, без повторной камеры.

`StationObservedHttp` — fixed-path facade для Root/wizard owner; constructor
не делает запросов. authorize/observe strips client actor и подставляет server
identity. Worker policy loop **не вызывает observe**: обновление должно приходить
только от текущего явно наблюдающего пользователя/его displayed-frame UI.
Host permission freshness использует исходный `camera.stale_s`; motor lease,
goal_deadline, limits/PIDs/firmware/control sampling остаются прежними. Просроченный
grant не оживает от нового frame или sticky bool: нужен новый challenge.

`check()` возвращает grant receipt с grant_id/binding/control_epoch и текущим
execution_permission. `command()` проверяет permission и вызывает original
Station.command **атомарно под её существующим RLock**. Возврат:
`{status:<exact original result>, grant:<updated receipt>, native_command_called:true,
dispatch_claimed_from_HTTP_acceptance:false}`. Start/recenter обновляет native
epoch в том же grant; source owner затем проверяет новый epoch. Это API-call
receipt, не dispatch/attainment: owner отдельно читает настоящий last_command
и проверяет native sequence/epoch.

Разрешены только policy/start, teleop/start/recenter, action, stop. Пока этот
provider владеет current native epoch, старый ungated `/station/action` или start
не может его заимствовать; original explicit STOP освобождает ownership. Проверка
плюс original legacy call тоже находится под тем же RLock. Scoped observed STOP
не вызывает unrelated direct ROSMASTER retarget stop. Существующая несовместимость
активного direct03 source session с follower controller пока честно сохраняется;
новый provider не создаёт второго владельца и не обходит её.

Revocation Event немедленно отменяет конкретный grant/pending challenge даже при
заблокированном camera/native вызове. Старые frame/authorize/STOP/revoke ответы
не могут задеть новое разрешение. Уже начатый bounded original вызов может
завершиться: late/unknown return вызывает scoped original STOP только пока этот
же native epoch ещё принадлежит ему, и не восстанавливает permission. No grant
переживает Node restart. Исторические тела SO101Station._write/_work/_sample/
command **не изменены**, что отдельно проверяет существующий AST regression.

9 новых software tests проходят: metadata0commands, pinned frame/SHA, explicit
observation expiry/renewal, late authorize/newgrant, blocked native start+STOP,
old epoch CAS, unknown return cleanup и реальный authenticated localhost Node
HTTP/facade. Все native calls выполнены только fake Station; реальных grant,
Start/action/STOP, arm/chassis commands или deployment агент не выполнял.
