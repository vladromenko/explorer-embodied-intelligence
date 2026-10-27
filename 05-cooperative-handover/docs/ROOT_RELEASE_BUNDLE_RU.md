# Три свежих наблюдения после source challenge

`OperatorEvidenceInbox.prepare_release_bundle` вызывается только внутренним
координатором после фактически полученного parked source challenge. Он сохраняет
его буквальный ответ и SHA, actor/run/object/actual receiver command, создаёт три
новых вопроса: удержание получателем и неподвижность каждой руки. Старая галочка
или ранее полученное подтверждение не становятся новым свидетельством.

`confirm_release_bundle` принимает один явный USER event с тремя отдельными
affirmations=true и observing=true. Actor приходит из authenticated session;
body не может задать source ID, часы или готовый proof. Три consumed records
пишутся одной atomic manifest, затем один текущий worker может однократно
забрать их через `wait_release_bundle`. Обычный confirm не принимает их IDs.

NX deadline = начало фактического source request + исходная source длительность
(не более исходных2s). Абсолютные часы Nano и NX не сравниваются. Задержки сети,
записи и подтверждения расходуют это окно; source issued/expires не меняются.
Source заново проверяет исходный срок при resume/publication; исходная0.45s lease
также не меняется. Получение доказательств само не вызывает resume или target.

RAM publication требуется после успешного fsync. Ошибка после replace сохраняет
реальную manifest, но не публикует authority. Рестарт, cancel, истечение срока,
вторая claim или ошибка claim не восстанавливают подтверждения.

Фактические software проверки используют durable inbox и fake original Source
во всех трёх giver modes. Подтверждено сохранение literal challenge/expiry и0
новых arm publications; это не физический тест. Первые3 test failures были
отсутствующим parent folder в fixture; сохранены отдельно, исправлена fixture.

Это внутренний building block. Полный Root coordinator/API/UI и приёмка05
остаются открытыми до отдельной сквозной интеграции и проверки.

Подготовлена отдельная UI-команда `native_parked_release_resume`: три новых
галочки, observing, neutral и отдельное разрешение наблюдаемого возобновления.
Source/actor/control IDs в body отсутствуют. Все галочки RAM-only и сбрасываются
при новом bundle/hash/epoch, перезагрузке, скрытии страницы или потере фокуса.
Поздний ответ не отменяет STOP; автоматического запроса/повтора нет. HTTP allowlist
подключён, серверная SourceControl orchestration остаётся отдельным незавершённым
шагом. Обычная metadata confirmation не превращается в resume.

Workflow bridge использует одну существующую HandoverFSM. Optional hooks:
`prepare_parked_release(row, after_receiver_command_id)` возвращает descriptor
или None для прежнего ACTIVE path; `wait_parked_release_control(row, descriptor)`
ждёт проверенный CONTROL completion, не выполняет Source RPC и не забирает Inbox
proof второй раз. Descriptor копируется и сохраняется в исходном pending step.
Completion обязан содержать exact bundle/run/object/epoch/operation, три literal
consumed records, immutable source-control pointer и исходный installed grant.
Проверяются исходные issue/expiry, отдельный source clock/boot и Root receive
times, actor, команды, SHA, provenance всех трёх фактов и их буквальное сохранение
в grant. Одного source ACK или validated-флага недостаточно.

`begin_parked_release_control(session_id, expected_epoch, bundle_id)` резервирует
только эту отдельную USER-команду внутри закрытого epoch-bound checkpoint.
Обычные phase/attach операции там по-прежнему запрещены. После успешного CONTROL
worker ждёт завершения reservation, заново проверяет epoch/phase/freshness и
применяет существующие FSM transitions. STOP reservation не ждёт; late completion
не восстанавливает состояние. Повторный `guard/authorize` после установленного
parked grant не вызывается. Policy path передаёт literal grant отдельному strict
transport adapter; его Source validation и per-role authority ещё интегрируются.

Фактически53 combined tests/10.38s и22 final bridge tests/3.82s прошли. Это real
Inbox/FSM на explicit SourceControl fixtures: metadata facts без CONTROL не
продвигают FSM, changed contexts блокируются, Source epoch проверяется семантически
даже при согласованных SHA, STOP и исходный ACTIVE path ограждены. Полный
Root→Source integration остаётся отдельным незавершённым шагом.
