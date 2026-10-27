# Свежий допуск перед native командой

`CooperativeDispatchAuthority` не делает запросов при создании. `require` читает
фиксированные Source GET и проверяет неизменный Root Spec/actor/grant lineage,
отдельно подключённый transport, его exact native epoch/generation/model и
текущие исходные publication guards. Движущаяся роль не может заменить свой
действующий transport parked scope. Партнёр может иметь действующий transport
или свежий Source-held scope с исходной защитой и направлением захвата.

После чтения партнёра движущаяся роль проверяется повторно: прошедшее время
расходует исходный lease. Root attachment/context тоже проверяются ещё раз.
Для close/release требуются обе фактические armed bits. Прежние Native watchdog,
TTL, limit, Source validation и публикационный барьер остаются окончательной
проверкой. Этот модуль не отправляет target/start/resume/heartbeat.

Кэш `dispatch_authority_by_role` в UI остаётся metadata preview. Свежий receipt
имеет отдельную schema/role/operation и не означает состоявшееся физическое
движение, удержание или неподвижность. Callback в Workflow и проверка перед
`Service._submit` подключаются отдельно; сам класс не создаёт нового flow/FSM.

Проверены8 cases на Root→настоящий fake Node/COMMON/original wrappers: отдельный
движущийся владелец и parked партнёр, actor/epoch/provider/transport substitutions,
Source revoke после положительного cache, истечение native lease во время
Source GET, поздний ответ после STOP и каждая отсутствующая armed bit.5 cases
прошли28.40s, lease-case отдельно4.92s, обе armed negatives9.80s. Первые две
ошибки lease-test были неверной единицей fake clock и слишком узким regex;
сами причины сохранены. На реальные устройства команды не отправлялись.
