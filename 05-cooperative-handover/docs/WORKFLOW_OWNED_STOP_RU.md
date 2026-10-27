# Внутренняя отмена и исходные сессии до attach

`CooperativeWorkflow.cancel_session` вызывает optional `native_owned_stop` с
сохранённым pre-STOP row после отзыва Root epoch. Control Center связывает его
с `CooperativeNativeSessions.cancel_owned_sessions`: только ранее фактически
полученные source grant/epoch/generation, без принятия нового текущего владельца.
Callback не вызывается конструктором, восстановлением или чтением интерфейса.

Вызовы STOP двух подключённых transports и cleanup исходных сессий запускаются
параллельно вне Workflow lock. Они не зависят от успешной записи cancellation
marker в Inbox. Receipt или delivery_unknown сохраняются отдельно от достижения
физической остановки. Исходные dual-hold latches не снимаются; lease не продлевается.
HTTP STOP использует уже возвращённый Workflow cleanup и не дублирует его.

Проверено42 tests/16.16s: настоящие Root/COMMON/native wrappers на fake buses,
внутренняя ошибка после Pi begin и giver resume до attach, чужой новый source
epoch, задержка/ошибка cleanup, ошибка Inbox fsync, один вызов cleanup из API;
существующие workflow/checkpoint/unattached-STOP cases. Первый test run:
40passed/1failed из-за лишнего `['result']` в test fixture, оригинал сохранён.
Это software evidence; команды на реальные устройства не отправлялись.

Позднейшее исправление закрывает отдельную ошибку до первого Source STOP:
отзыв epoch и STOPPED теперь сначала публикуются в памяти под Workflow lock.
Known-owned cleanup и оба transport STOP запускаются до записи Root projection
и Inbox. Ошибка чтения skill spec не пропускает cleanup ранее полученного
Source scope. Старый worker не может записать прежний epoch поверх отзыва.

STOP projection пишется через отдельный уникальный временный файл, atomic
replace и directory fsync вне Workflow lock. UI читает действующий RAM STOP
даже при отказе диска; `coordinator_cancellation_projection_persisted=false`
и ошибки записи сохраняют неопределённость durability. При рестарте старая
активная disk projection становится INTERRUPTED без replay и Source RPC.
При неуспешном final fsync disk bytes могут уже содержать STOPPED, но RAM
receipt не объявляет запись гарантированно устойчивой.

20 адресных tests прошли за8.68s: блокировка/отказ первоначальной записи,
реальный отказ directory fsync после atomic replace, stale epoch, unreadable
spec и restart без replay. Первый run18PASS/2FAIL содержал ошибки fixtures;
исправленные прогоны20PASS/8.92s и20PASS/8.68s сохранены отдельно.
[Квитанция](../../_registry/evidence/cooperative-stop-projection-20261027.json).
Все Source buses тестовые; более широкий device/storage recovery остаётся
открытым и этим исправлением не объявляется завершённым.

После фактической metadata-установки обеих защит `receiver_at_object` повторно
проверяет свежесть обоих USER stationary facts. Сетевые GET, WAL и custody
расходуют исходные2s; истёкшие факты не переводят FSM к захвату. Защиты не
снимаются, Workflow отзывает epoch и выполняет исходные scoped STOP.
31 tests/8.87s проверили этот gate и release bridge:1.999999999s допускается,
2.000000001s блокируется без продления TTL. Физическая неподвижность не заявлена.
