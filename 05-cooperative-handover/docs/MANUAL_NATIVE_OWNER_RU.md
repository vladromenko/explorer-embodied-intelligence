# Наблюдаемый manual giver использует тот же исполнитель

Новые native metadata/user boundary методы не запускаются в constructor:
`cooperative/prepare` вызывает исходный `ManualTeleop.select_source('keyboard',
True,True)` только по отдельному USER действию в STOP/neutral. Это тот же
аудированный ABI; browser gamepad bridge также использует исходный keyboard
executor. Никаких новых скоростей, watchdog/lease, камер, потоков или publishers.

После отдельного `cooperative/bind` метод `cooperative/manual/resume` требует
точные observed authorization/run/object/coordinator IDs и
`expected_native_generation`. Он вызывает исходный `resume('keyboard',True)` и
в рамках этого явного USER действия — исходный zero base request с настоящим
host receipt. Это не измеренная скорость. Нет action/model loop и нет auto lease
renewal. Ручной input продолжает поступать существующим оригинальным путём.

`NativeGiverManualTransport` читает только status/activation до явного protocol
действия. Native epoch для такого manual пути равен None; ownership определяется
точным original generation + authorization + cooperative binding + текущим lease.
Scoped STOP сверяет все эти identities; старый STOP не затрагивает новый
generation/USER grant. Он вызывает тот же исходный manual STOP. Отзыв разрешения
не снимает dual-hold защиту: safe-object reset остаётся отдельным подтверждением.

В исходной metadata-only FSM ручной `release_requested` не открывал native grant:
policy submit branch для manual роли пропускается. Теперь в physical observed
domain после свежего correlated hold/stationarity, текущей authority обоих native
owners и проверки исходных deadlines вызывается отдельный
`authorize_manual_release`. Он только подтверждает native publication boundary;
самостоятельной команды открытия не посылает. Открытие делает USER существующим
ручным executor, а тот же publisher проверяет native grant/deadline в момент
публикации. Observation-only и shadow annotations этот путь не вызывают.

Проверки используют fake original owner/publisher, actual authenticated native
FastAPI routes и полную metadata FSM. Они подтверждают exact selection/resume
ABI, отсутствие model/hidden action, истинный fake zero request, stale
generation/new-user STOP reject, metadata release без публикации и отдельное
ручное открытие через guard. Физическая квалификация не проводилась; перед
выкладкой оригинального owner интегратор повторяет full zero-startup proof.
