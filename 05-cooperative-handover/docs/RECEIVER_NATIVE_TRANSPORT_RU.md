# Scoped SO101 receiver transport

`cooperative_receiver_transport05.NativeReceiverTransport` использует тот же
COMMON `StationObservedPermission` на исходной Pi Station. Конструктор не делает
запросов. Read-only `observe_state()` сверяет current grant ID, purpose/model/
cooperative binding, actor, provider process, native epoch, boot/clock,
calibration и настоящий native mode; stationarity/contact/успех не выводятся.

Для policy role требуется отдельный pure callback уже admitted isolated warm
worker. Его exact6 prediction, model/checkpoint, исходный RGB SHA/camera,
measured-state/calibration, causal Pi timestamps/boot и original frame artifact
проверяются. Manual role не получает скрытой модели или policy action.
Длительность action/моторный lease не задаётся: сохраняется исходный native
default. Worker не renew user observation и не запускает begin/resume.

Каждый action проходит атомарный native `permission.command`, а затем проверяется
фактический `Station.last_command`: точный epoch/sequence, requested/dispatched6,
frame ID и Pi clock с dispatch после source cutoff. API acceptance, `busy=false`
или закрытый gripper не заменяют receipt. `DispatchReceipt` связывает исходный
native write с coordinator command и сохраняет разные Pi/NX clocks; достижение
и контакт не утверждаются.

STOP сразу ставит локальный fence, отзывает только исходный grant/epoch и
вызывает exact scoped native STOP через общий provider. Поздний prediction не
доходит до action; старый STOP не затрагивает новый USER grant. Оригинальный
position-hold путь Station не заменяется и новый servo owner не создаётся.

Тесты используют настоящий COMMON provider и fake original driver; запись в
`last_command` появляется только отдельным fake driver publication после API
ответа. Проверены accepted-without-write timeout, настоящее correlated write,
STOP во время inference, old cleanup/new grant, source calibration/boot и manual
mode. Это software qualification; provider attachment, native user controls и
двусторонний publication interlock интегрируются отдельно. Никакого реального
движения этими тестами не выполняется.
