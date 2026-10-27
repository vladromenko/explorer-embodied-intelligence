# Один Nano-производитель 9D для показов 02 и 03

`explorer_shared.native_capture.NativeCaptureWriter` записывает одни и те же
ROSMASTER-target RGB/9D-эпизоды для keyboard/browser gamepad02 и SO101 leader03.
Различается исходный input adapter. Контракт, SkillSpec, aggregate/model/eval
остаются едиными. Нет отдельного arm-only/base-only dataset. Показ без включённой
записи продолжает использовать тот же native stream независимо от этого writer.

Это source/software путь. Заводской retarget API пока требует отдельного монтажа
в исходном владельце и наблюдаемой квалификации; этот модуль не перезапускает его
и не отправляет motor POST. Старые ARM_ONLY/raw archives и реальные36 кадров
наблюдений сохраняются analysis-only. Новый модуль не переименовывает их в expert.

## API для NodeAgent-интеграции

```python
writer = NativeCaptureWriter(managed_recordings_root, existing_nano_camera,
    clock_domain=agent.delivery.clock_domain, boot_id=actual_boot_id,
    emit=agent.delivery.emit)

# Только после capability GET нового original-owner API:
source = native_client.dispatch_source_packet(0)  # exact original HTTP bytes
record = writer.start(validated_skill_context, source['source_bytes'])
recording_id = record['recording_id']
cursor = record['cursor']  # current native head at record boundary

# Независимый read-only fetch worker, максимум одна выполняющаяся HTTP-загрузка:
packet = native_client.dispatch_source_packet(cursor)
if writer.submit(recording_id, packet['source_bytes']):
    cursor = packet['payload']['next_sequence']

writer.stage(recording_id, 'grasp_confirm', reason='explicit_operator_annotation')
writer.finish(recording_id, exact_authenticated_outcome_evidence_bytes)
```

Native client существует отдельно от control writer и никогда не посылает lease
heartbeat при сборе данных. Нужны только GET dispatch/reference/status. Нативные
команды остаются у исходных ManualArm/ManualTeleop. Root должен остановить fetch
при finish и дождаться уже начатой загрузки перед writer.finish; сам writer
гарантирует обработку уже принятых submit перед sealing и отклоняет поздние
submit/stage. После sealing status возвращает `embodied_manifest`.

Новые NodeAgent HTTP endpoints должны иметь capability/schema check и тот же
private node auth. Предусмотренные операции: record/start(skill_context),
record/stage(recording_id,phase_id,reason,recovery_of), record/finish,
record/status/list, manifest/download. `skill_context` — metadata only: skill_id,
skill_version, skill_spec_sha256, variant_id, session_id, project_id, input_mode,
episode_scope, phase_id и split. Передача этих полей не выбирает controller,
не делает move/resume/home и не создаёт успешные labels. Пока endpoints/fetch
не смонтированы в работающем процессе, класс не означает аппаратную квалификацию.

`NativeCaptureService` реализует incremental GET fetch и lifecycle в NodeAgent
Nano. Пока новый original-owner tap не смонтирован, capability
native_owner_ready=false, start отвергается и legacy writer не получает kwargs.
Новый HTTP prefix `/native-capture/`: capabilities/status/recordings,
record/start, record/stage, record/finish, recordings/{id}/manifest|download.
Все пути требуют node token. Finish дожидается уже начатого GET, сохраняет его
original bytes и исключает training rows после местного finish timestamp;
никакого STOP или другого motor POST не делает.

Каталог отдельный: ready_recordings содержит recording_id, project_id,
input_mode, native_input_mode/input_mode_basis, archive SHA/size. Новые03 не
попадают в legacy mobile_observation.ready_recording_ids02 или Pi raw catalog.
Экспорт создаётся после sealing в file worker. Для пустого analysis-only capture
input_mode может быть только явно помеченной validated metadata декларацией,
а не native proof. Импорт маршрутизируется по producer descriptor, не по странице.

## Настоящая граница источника

Исходные native JSON bytes сохраняются без изменения, отдельно от канонических
проекций. Каждая обучающая строка выбирается из arm_host_publish события после
успешного возврата исходной публикации. API acceptance/intent без такой границы
остаётся отдельным сырым фактом. Все9 command coordinates/masks берутся ровно из
native command9; unknown base остаётся null. Только исходная base STOP/drive/
release квитанция создаёт командный контекст базы. Это host-command snapshot,
не атомарная аппаратная отправка всех девяти координат и не измеренная скорость.

preceding_command9 должен уже присутствовать в исходном native packet: Ruckig
q_estimated_deg плюс известная команда базы, записанные в исходном владельце.
Источник базы для предыдущего состояния выбирается as-of начала arm publication;
более позднее изменение не переносится назад. При отсутствии этого факта vector
не собирается из odometry, leader angles или endpoint.

Reference приходит только из native canonical_reference header. Его snapshot
имеет native_guard_verified и prior_reference_sha256 после чтения исходного
factory reference через прежние guards, без readback. Default guard=false.
После reboot/link fault нужна новая соответствующая исходная ссылка; записи
не склеиваются через boot. Наличие command estimate не доказывает attainment.

Для03 источник измеренного leader и calibration hash должен присутствовать в
source_intent_binding исходной квитанции. metadata input_mode=so101_leader само
по себе этого не доказывает. Реальный adapter берётся из native binding или
native wire source; заявленный client input mode хранится отдельно. Отсутствие
подтверждённой связи leader, native publication, reference, RGB или всех9 каналов
не скрывается — остаются raw originals и analysis-only причины.

## Камера, часы и маски

Writer использует existing CameraSource.before(native_dispatch_ns). Это исходный
JPEG, уже сохранённый единственным camera owner до команды. Никакого открытия
камеры, повторного capture или подстановки более нового latest frame нет.
Clock domain/boot packet обязаны совпадать с конкретным Nano producer; независимые
часы Pi не приравниваются к Nano. Исходные frame ID, JPEG SHA, receive/exposure
timestamp basis и времена native publications сохраняются. Нет обещания измерить
exposure time по HTTP receive timestamp.

Дополнительный observation_provider(row,frame) возвращает channels и
source_payloads {immutable_relative_name:exactbytes} из существующего sensor cache.
Он вызывается вне control loop при приёме packet. Writer сохраняет sensor bytes
и component masks; один immutable key нельзя переиспользовать для разных bytes.
Источник не заменяет authoritative RGB/clocks/prior9/native command. Доступные
intrinsics с неизвестными extrinsics сохраняются частично; более новый sensor
timestamp не переносится назад. Нужны as-of snapshots с оригинальными stamps/boot.

Измеренные q, depth, LiDAR, IMU, force и другие неэкспонированные каналы остаются
null/maskFalse с unavailable_reason и present=null (наличие устройства неизвестно
этому адаптеру). Это описание доступности pipeline, а не утверждение, что сенсор
физически отсутствует. Нельзя включать readback/firmware ради допуска. Данные
проверяются профилем rosmaster_command_estimate9_v1; полный navigation/3D pipeline
может отдельно оставаться BLOCKED. Действительно записанные нули остаются9D.

## Фазы, исходы и admission

Аннотация фазы получает timestamp в Nano kernel clock при приёме API. Разметка
строк выполняется по этим timestamp, а не по задержанному приходу poll packet и
не по часам Mac/Homebrain. Повторная фаза сохраняет отдельный occurrence_id
(например grasp_confirm#2) и semantic_phase_id, включая recovery_of. Raw phase
events сохраняются целиком.

Finish принимает точные bytes независимого outcome source с тем же recording_id:

```json
{"recording_id":"<original>","operator_outcome":"success|failure|inconclusive",
 "outcomes":{"end_to_end":{"status":"...","method":"operator_observation",
 "observer_id":"<server-authenticated actor>","independent_of_action_commands":true,
 "metrics":{"<independent measure>":"<observed value>"}},"per_phase":{"<occurrence ID>":{}}}}
```

Это пример структуры, не готовая метка. Реальные поля actor/correlation проверяет
сервер; один checkbox success или accepted command не создаёт её автоматически.
При отсутствии labels/correlation/per-phase metrics эпизод остаётся analysis-only.
Модель usage=declared_plan не маркируется actually used. Held-out partition и
global scene/session/object independence проверяет общий downstream workflow.

## Файлы, recovery и импорт

Запись идёт через ограниченную очередь в отдельном file writer. В servo loop нет
JPEG/fsync/HTTP. Исходные packets, JPEG, camera-reference indexes, metadata context,
phase events, outcome bytes и canonical facts имеют SHA256 и fsync/atomic rename.
Gaps/queue drops/native capture failure/invalid host receipt не дают expert claim.
При рестарте незавершённый manifest помечается interrupted, его прежние bytes
сохраняются отдельно. Ошибки доставки события не удаляют завершённый manifest.

JPEG bytes не накапливаются в памяти после fsync. Metadata и локальная admission
имеют явные budgets: по умолчанию20000 samples и128MiB загружаемых исходных bytes.
Превышение сохраняет оригиналы и даёт OFFLINE_REBUILD/ADMISSION_REQUIRED причины;
это ограничение обработки данных, не motor limit. Весь исходный transport и
frames остаются для отдельной проверки на NX.

`writer.archive(recording_id)` повторно проверяет все source SHA и создаёт
неcompressed tar вне control thread. `import_native_capture(archive, expected_sha,
managed_destination)` проверяет transport SHA, безопасные regular-file members,
source hashes/identity и admission заново. Сохраняются точный original manifest,
payloads и original-source-transport.tar. Коллизия ID с изменёнными bytes отвергается.

Результат импорта содержит recording_id/source_recording_id, embodied_manifest,
archive/source-manifest SHA и свежую admission. `dataset_manifest=None`: этот
коммит не создаёт выдуманный legacy NPZ и не запускает BC. Canonical inspector
принимает полноценный unified dataset без такого view; отдельный ACT RGB+prior9
view допустим только из этих же проверенных samples с явным соответствием.

Десять новых temp/mock тестов проверяют общий02/03 формат, сохранение исходных
bytes, full9/neutral base provenance, masked feedback, frame-before-action,
phase clocks, finish race, невозможность fake expert из ACK/metadata, budgets,
restart и безопасный повторный NX import. Никакого реального robot command не было.


## Общий data plane02/03 — подготовка 2026-10-27

Новая запись03 использует `/api/retarget/capture/…` → фиксированный Nano
`/native-capture/…`, тот же producer, что02. Pi `/api/retarget/status`, lease,
calibration и STOP остаются control plane; прежние Pi-архивы остаются legacy
analysis. Общие start/stage/pause/resume/cancel/finish, manifest/JPEG/Range-download
и отдельный post-seal review связываются с исходной SkillSpec и capture intent.
API проверяет project02/03 до меток и выдачи media. Завершение само не запускает
перенос; approve закрепляет archive SHA и текущий review SHA, автоматического
обучения нет. На Homebrain директории native-mobile и native-retarget раздельны.

Пока full continuity на меняющемся реальном leader input имеет OPEN, новый
physical begin/recenter03 и новые demonstrations03 закрыты в central API/UI.
Технический режим `observations_only` доступен без зеркала, HOME, grants и
движения; он принудительно analysis-only. Ограничение не меняет ACTIVE shaping,
исходный Ruckig, TTL, firmware или аппаратные настройки. Физический путь Pi пока
не развёртывается. Снятие gate требует нового проверенного evidence/review кода,
а не клиентского флага или подделанной отметки успеха.

Основной camera_rgb03 — единственная камера Nano. Logitech Pi — только
возможный явно отдельный вспомогательный ракурс leader/workfield; текущая запись
не утверждает получение её JPEG или синхронность. Старый pi-overview — та же
Logitech, а не третье устройство. Технические/generated fixtures не подтверждают
реальный expert dataset, physical continuity или освоение задачи.
