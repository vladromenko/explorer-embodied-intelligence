# Запись прямого показа и честный импорт

Программный путь реализован в `shared/explorer_shared/retarget_recording.py` и
подключён к PiRetargetService/NodeAgent. Это запись конкретного ARM_ONLY
показа SO-101 leader → ROSMASTER, отдельная от обычного leader→follower SO-101
и отдельной видеозаписи камеры. Физические испытания этого пути не заявляются.

## Что записывается

Перед каждым обрабатываемым операторским событием Pi control worker берёт
неблокирующий snapshot уже существующего Pi camera cache и последнего доступного
Nano camera snapshot. Затем читает существующий measured leader cache и
исполняет прежний retarget step. Отдельный writer сохраняет:

- измеренные leader coordinates, их исходные имена/единицы/calibration SHA,
  source monotonic timestamp, clock domain и control epoch;
- геометрическое предложение и полный неизменённый Nano API receipt,
  включая requested/accepted/rejected/unknown состояния;
- исходные JPEG bytes обеих камер с frame IDs, SHA256, timestamp basis и
  оригинальными clock domains, где источник их действительно сообщил;
- Pi времена observation snapshot, action request/reply; регистрацию и
  меняющиеся epochs; исход оператора `success/failure/unknown`;
- пропущенные tickets/кадры и машинные причины отказа в учебной квалификации.

Все записи относятся к immutable recording ID и исходному direct run ID.
Recenter остаётся видимым через epochs и предложения. Новый direct run не может
незаметно дописаться в старую запись. Поздний finish старого recording ID не
меняет outcome новой записи. Начатый до finish control sample успевает попасть
в ту же запись; finish возвращает `finalizing`, пока такой sample не сохранён.

Локальный root:
`~/.local/share/explorer/node-state/retarget-recordings/<recording_id>/` на Pi.
Внутри — `manifest.json`, fsynced `samples.jsonl`, исходные
`frames/{source,target}-<sha>.jpg`, проверяемый `recording.tar`, transport
manifest и отдельный completion/outbox receipt. Повторённый cached frame не
считается новым независимым кадром: manifest различает ссылки в samples и
`unique_source_frames/unique_target_frames`. JPEG blobs могут дедуплицироваться.

## Камеры и временная причинность

Новый код не открывает USB/serial/V4L2 и не делает второго camera owner.
Pi используется через `camera.latest()` действующего observer. Отдельный writer
делает авторизованный GET существующего Nano
`/cameras/nano-rgbd/snapshot`. Чтение камеры, network timeout, запись файлов и
упаковка архива выполняются вне locks servo station и retarget control.
Heartbeat остаётся только явным пользовательским событием; recorder его не
генерирует и не меняет исходную `.6s` lease.

Node snapshot теперь отдаёт `X-Clock-Domain` своего boot clock и, если источник
имеет его, `X-Source-Wall-UTC`. Сохраняются прежние X-Frame-ID/X-Monotonic-NS/
X-Timestamp-Basis/X-Wall-UTC. Старый observer без нового header оставляет
`clock_domain=null` с причиной `TARGET_CLOCK_DOMAIN_UNREPORTED`; Pi clock
вместо Nano clock не подставляется.

Nano source timestamp и Pi source timestamp никогда не сравниваются как одна
шкала. Offset/drift uncertainty остаются `UNKNOWN/null`. Можно независимо
доказать лишь, что конкретный JPEG был **получен Pi до Pi action request**.
Этот causal receive-order отмечается отдельно от неизвестной exposure/dispatch
alignment. Legacy file-mtime/host-receive timestamp не объявляется exposure time.

Существующий JPEG endpoint не переносит raw depth. Поэтому target RGB сохраняется,
а `target_depth_available=false` и точная причина отсутствия depth остаются
явными. Новый RGB-D transport, exposure alignment и камерная registration не
выдумываются.

## API и UI hooks интегратора

Все Node endpoints используют существующий bearer authentication. UI работает
через защищённый central proxy с прежними auth/CSRF:

1. На активном прямом контуре POST
   `/arm-retarget/record/start`:
   `{expected_control_epoch, observing:true, name, task?}`.
   Ответ — новый `recording_id`. Старт записи не двигает механизм.
2. POST `/arm-retarget/record/finish`:
   `{recording_id, outcome:"success"|"failure"|"unknown"}`.
   ID обязателен; сервер не угадывает «текущий» для запоздалого сообщения.
   Outcome — операторская оценка, не независимый physical validation.
3. GET `/arm-retarget/recordings` — сохранённые manifests;
   GET `/arm-retarget/recordings/<id>/manifest` — конкретный manifest;
   GET `/arm-retarget/recordings/<id>/download` — проверенный tar с
   `X-Content-SHA256`. Download допустим после завершения sealing.
4. `/arm-retarget/status.recording` показывает активный ID, очередь и ошибки.
   UI сохраняет ID своего показа и не заменяет его ID другой вкладки/сессии.

Глобальный STOP работает независимо от финализации записи. Заканчивать label
можно после остановки контура. При штатном закрытии observer недооценённая
запись сохраняется как unknown. После process restart оставшиеся записи
помечаются `interrupted`; неизвестное количество потерянных in-memory tickets
обозначается `UNKNOWN`, а не ложным нулём. Старые motor commands не воспроизводятся.

Завершение публикует `retarget.recording.finished` в существующий долговечный
EventDelivery outbox с project_id `03`, recording ID, archive SHA, counts и
qualification. Wi-Fi outage не уничтожает локальный архив или outbox.
Центральный обработчик должен создавать idempotent import по recording ID и
archive SHA. Повторное completion после crash между outbox append и локальным
receipt не должно запускать вторую научную попытку.

## Импорт в NX: анализ, пока недостаточно наблюдений

`import_retarget_recording(archive_path, destination,
expected_archive_sha256=...)` проверяет ожидаемый SHA из завершённого manifest
или download header, transport fingerprint, все исходные файлы,
sample/frame references и identity. Если ожидаемый SHA не передан, функция
вычисляет идентификатор архива, но не заявляет внешнюю проверку его происхождения.
Оригиналы сохраняются byte-for-byte в `destination/source_originals/`;
повторный импорт проверяет их SHA и не перезаписывает изменённый источник.
Traversal, лишние/повторные tar members и checksum mismatch отклоняются.

Результат по текущему контракту всегда содержит:

```json
{
  "training_admission": "analysis_only",
  "dataset_manifest": null,
  "BC_targets_created": false,
  "block_reasons": [
    "TARGET_DISPATCH_NOT_QUALIFIED",
    "TARGET_MEASURED_STATE_ABSENT",
    "CAUSAL_FULL_9D_TARGETS_ABSENT"
  ]
}
```

Это намеренное отражение доступных данных. Шесть requested/accepted endpoint
координат не превращаются в полное девятимерное действие. `base_action=null`,
`target_measured_state=null`, `dispatched_target_action=null`; отсутствующие
vx/vy/wz и q не заполняются нулями или host command echo. Полный исходный API
receipt сохранён для дальнейшего анализа независимо от этих derived labels.

Даже операторский outcome `success` не разрешает автоматически BC training.
Квалифицированный совместимый dataset можно добавить только по настоящей
причинно согласованной полной 9D state/action истории и отдельно проверенной
семантике доставки; такой источник текущий direct ARM_ONLY endpoint не даёт.
Никакой «expert dataset» из этих проб не создаётся.

## Проверка

`tests/test_retarget_recording.py` включает настоящий localhost authenticated
Pi→Nano маршрут с подставленным original motor executor и sensor caches:
record/start → cached source/target frames → retarget API receipts → outcome →
fsync archive/outbox → HTTP download/SHA → immutable NX raw import. Отдельно
проверяются независимые clocks, причинность Pi receive-order, медленный target
network и checkpoint без блокировки control cache/status, повторённые frames,
finish/inflight race, old-session labels, interrupted restart и tampering.
Это software sensor/transport fixtures; физических команд на роботах не было.
