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

Отдельный более широкий recovery-аудит всех ошибок Root session storage не
подменяется этим тестом Inbox и остаётся в общей матрице recovery.

После фактической metadata-установки обеих защит `receiver_at_object` повторно
проверяет свежесть обоих USER stationary facts. Сетевые GET, WAL и custody
расходуют исходные2s; истёкшие факты не переводят FSM к захвату. Защиты не
снимаются, Workflow отзывает epoch и выполняет исходные scoped STOP.
31 tests/8.87s проверили этот gate и release bridge:1.999999999s допускается,
2.000000001s блокируется без продления TTL. Физическая неподвижность не заявлена.
