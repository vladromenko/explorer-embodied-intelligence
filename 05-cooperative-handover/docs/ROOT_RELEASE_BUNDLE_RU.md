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
