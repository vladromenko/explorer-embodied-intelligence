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

Проверка подключена к Workflow и непосредственно к `Service._submit`.
Workflow требует отдельную fresh schema, requested role/operation, Root actor,
epoch, исходные grant/lineage/ledger/phase pins и часы NX. Metadata preview
не может заменить этот receipt даже при legacy global=true. Перед native RPC
Service повторяет actual Source GET, сверяет exact command/reference/transport;
receipt сохраняет original native fields и добавляет derived Root admission.
Конструктор по-прежнему ничего не включает и не запрашивает.

57 combined tests/49.03s прошли; после добавления phase pin —18 wiring
tests/1.71s. Проверены cached/wrong/stale/foreign contexts, изменение ledger
во время проверки, повторная проверка перед submit и неизменность raw native
receipt. Explicit STOP сохраняет authenticated actor; internal STOP использует
исходного bound actor, не выдавая повторную cleanup команду.

Парные GET дополнительно ограничены исходным native_lease_remaining_s:
NX request start + literal remaining duration — консервативный deadline,
без сравнения абсолютных часов Nano и NX.2 tests/10.03s проверили истечение
lease при чтении партнёра и между последним status и activation reply.
Свежий на момент первого GET статус не продлевает native lease.

Для Pi используется отдельный native source_stamp статуса permission. Разница
между исходным observation_deadline_ns и native read time переводится только
в длительность окна NX, без сравнения абсолютных часов.3 tests/14.44s проверили
оба положительных role scope и истечение остатка наблюдения во втором GET.
Исходный срок наблюдения не обновляется и не превращается в motor lease.
