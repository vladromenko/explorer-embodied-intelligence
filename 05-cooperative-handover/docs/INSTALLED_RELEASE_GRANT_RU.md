# Уже установленный source grant

`InstalledGrantGiverPolicyTransport` и `InstalledGrantGiverManualTransport`
наследуют существующие native transports. Отдельный явный USER CONTROL
`resume-release` уже установил grant; повторный `guard/authorize` недопустим.
Адаптер проверяет буквальные challenge/grant/три consumed подтверждения,
Root actor/binding, actual owner и durable source latch свежими фиксированными
GET. `release_boundary.source_stamp` — время самого Nano с его boot/domain;
оно проверяется относительно исходного native expiry, без преобразования часов.
Нет продления исходных 2 s / 0.45 s, нового publisher или загрузки модели.

Существующий policy `submit_observed` по-прежнему требует actual original
publication receipt. Manual adapter не выдаёт скрытых targets. Grant не означает
отпускание предмета, контакт, стационарность или измеренную целевую позу.

Квалификация: 20 PASS / 29.183 s, включая шесть retained exact original factory
и forward-repin случаев; только fake executor/localhost, оборудования не касались.
Private JUnit: `cooperative-parked-release-qualification-20261027/installed-grant-source.xml`.
Station.py, исходные настройки ROSMASTER и обучение не изменены.

Дополнительный stale-read regression: status сообщил remaining lease, затем
второй activation GET занял 451 ms при исходной 450 ms lease. Ранее адаптер мог
вернуть grant по первому снимку; Source Publisher всё равно отклонял бы запись.
Теперь весь GET pair должен уложиться в `NX_request_start + source_remaining`
по длительности. Это консервативная клиентская проверка уже существующего
deadline, без сравнения абсолютных часов и без изменения lease/контроллера.
Первый failing log сохранён; 17 adapter cases прошли после исправления.
