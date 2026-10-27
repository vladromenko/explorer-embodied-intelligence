# Source04 capture adapter — атомик A, без control/deploy wiring

Код: `04-so101-source-learning/src/explorer_so101/capture_adapter.py`.
Класс `StationCaptureAdapter` — metadata/file adapter существующего Station
recorder. Ни serial, ни камера, ни servo loop, ни второй sample writer не создаются.
Импорт нового package пока не добавлен в production packaging: это задача Root.

## Фиксированные callback и публичный ABI

Конструктор:

```python
StationCaptureAdapter(root, *, status, record_start, record_finish,
    read_manifest, archive_original, clock_domain, boot_id, clock)
```

`root` — новый managed metadata каталог, **не** оригинальный Station recordings.
Все callback задаёт доверенный source bootstrap, браузер их не выбирает:

| Callback | Единственный разрешённый источник |
|---|---|
| `status()` | status того же существующего SO101Station |
| `record_start(body)` | `station.command('record/start', body)` |
| `record_finish(body)` | `station.command('record/finish', body)` с exact recording ID |
| `read_manifest(id)` | bytes original `station.root/recordings/id/manifest.json` |
| `archive_original(id)` | original `station.archive(id)` → trusted local Path |

Не вызывать central legacy `/api/station/record/finish`: там пока есть autoimport.
Не подменять callback на teleop/start, STOP, HOME, bus read/write или второй recorder.
`clock_domain/boot_id/clock` принадлежат этой же Pi source, не NX/browser clocks.
Конструктор и `status()` не вызывают callback; status — cached metadata.

| Метод | Контракт |
|---|---|
| `start(record_start_body, actor_id)` | Body из существующего `lab_skill_workflow.capture_context(...).record_start_body`; canonical04 v2, pinned Skill/version/spec/session/scene/split. Actor поступает от auth server. Original station уже отдельно user-started; adapter не начинает teleop |
| `pause(id)`, `resume(id)` | Exact active ID/state/native owner/boot/clock/calibration/context; source monotonic intervals. Raw samples продолжаются в original writer |
| `finish(id, outcome='unknown')` | Только identity-fenced record/finish и sealed transport; control/mode не меняет |
| `cancel(id, actor_id)` | Exact capture actor/ID; finish data с unknown outcome, outer state cancelled. **Никакого STOP** |
| `manifest(id)`, `archive(id)` | Проверенные immutable wrapper/transport; original bytes остаются отдельно неизменными |
| `review(id, decision, actor_id, expected_archive_sha256)` | Explicit approve/reject exact sealed outer archive SHA; shared Source-review ABI |
| `recording_review(id)`, `recordings()` | Current review и typed catalogue; transfer-ready только completed+latest approved exact SHA |
| `close()` | Закрывает только metadata ownership; не finish/STOP/revoke/servo/camera |

HTTP/Node/API/Store/poll/worker/Dataset/UI здесь **не реализованы**. Root позже
добавит fixed routes и не допустит approval bypass через старые import/events.

## Что запечатано

Source transport содержит ровно `manifest.json` и `source/recording.tar`.
Второй файл — byte-exact оригинальный Station TAR, включающий исходный native6
manifest и JPEG. Он проверяется streaming SHA/TAR scan, затем снова хешируется
при streaming упаковке. Whole archive в память не читается. Ограничения: raw TAR
≤2 GiB, отдельный manifest/frame ≤64 MiB, source samples ≤10000.

Outer schema `explorer_so101_source_capture_v1` связывает:

- original recording ID/project04/native6 contract, initial source context,
  original native owner epoch, source boot/clock и оба calibration SHA;
- original TAR SHA/размер, exact manifest SHA и original JPEG hashes;
- lifecycle start/pause/resume/finish source timestamps, revision/events;
- selection исходных sample indices и `recording_segment_id`, без изменения
  исходных action/state/leader/gripper/RGB/masks;
- original outcome отдельно от `training_admission=NOT_ASSESSED_USE_EXISTING_NATIVE6_INSPECTOR`.

Interval `[pause,resume)` исключён. Нулевая/короткая pause также разделяет episodes.
Для retained sample **state и action** обязаны принадлежать одному неприостановленному
segment; state до resume/action после resume остаётся raw и исключается из view.
RGB остаётся исходным cached кадром со своим timestamp; синхронизация/exposure не
изобретается. Будущий native6 Dataset adapter обязан использовать эти provenance-
bound segments, а не только прежнее gap>3/fps, и не объединять chunks через них.

Source original state interrupted, сменившийся native owner или отсутствие samples
не выдаются за completed teaching. Cancelled/interrupted можно сохранить/reject,
но нельзя approve. Успех модели, MCU ACK, attainment, контакт/сила не выводятся.

## Uncertainty и неизменность

До record/start сохраняется durable attempt. Actual returned ID удерживается до
локального fsync; unknown start/finish, failed local persistence и restart во время
записи требуют recovery, без GET-adoption или автоматического finish чужой записи.
Corrupt journal сохраняется как есть; source writer не вызывается конструктором.
Завершение не публикуется до transport+manifest+lifecycle+archive-info commit.
Уже запечатанный folder не переиспользуется: unknown второй start/ID collision не
могут переписать предыдущий оригинал. Runtime recovery/reset API намеренно не добавлен.

Общий `sealed_capture_review.py` сохраняет прежние native02/03 schema, receipt keys,
ASCII JSON bytes, history-before-latest и review SHA chain. Исходные manifest/TAR
не меняются после review. `capture_timeline.py` — одна pure реализация intervals;
NativeCaptureWriter делегирует только pause/resume metadata и review, без нового
sampling/trajectory behavior. Бounded fake тесты сравнивают exact review bytes
и unchanged native02/03 samples/paused raw pages.

## Незакрытые границы

`DEPLOYMENT_BOUNDARY_REVIEW_20261027_RU.md` остаётся обязательным: Pi source-only
profile, предотвращающий новый physical03 deploy; teardown/admission/drain proof;
guarded source04_demo purpose; scoped auto-cleanup vs explicit global STOP.
Эти механизмы не реализованы этим атомиком. Station SHA сохранён `2d191534…a885`.
Нет device RPC, реальных показов, optimizer, нового runtime release или physical
qualification. Source04 software-ready остаётся OPEN до Root vertical integration.


## Дополнение Root: Node data plane после атомика A

`capture_service.py` связывает тот же adapter с фиксированным original Station
и COMMON `source04_demo`. Skill/version/specSHA/session, actor и exact acknowledged
native epoch проверяются перед попыткой и ещё раз под original Station lock перед
record/start. Session ID — hex-представление прежнего lab workflow UUID, без новой
параллельной идентичности. Callback не начинает teleop и не вызывает STOP.
Known denied-before-writer попытка имеет отдельный journal state и может повториться;
unknown original writer return по-прежнему не позволяет GET-adoption/retry.

`explorer_so101` добавлен в canonical package mapping. Explicit source-only Node
создаёт capture service поверх того же Station и одной camera cache. Fixed
`/so101-capture/*` использует общий capture GET handler и Range sender с native02/03,
а `transfer_native_capture(..., source_prefix='/so101-capture')` — тот же bounded
resumable transfer/approval validator. Prefix выбирает server, произвольные URL
не принимаются. POST actor поступает от authenticated central gateway; source
также сверяет его с COMMON grant/capture owner. Dataset/central API/UI ещё открыты.

55sharedNode/capture/transfer tests PASS5.40s +1single-owner wiring PASS0.18s;
provenance registry PASS. Source04 software-ready не повышен. Real Pi не обновлён.


## Дополнение Root: общий source timeline и существующий Dataset converter

`capture_timeline.select_source_samples` теперь одинаково используется producer
при seal и существующим `station_adapter.convert_station_episode` при чтении
optional SHA-bound capture envelope. Новый converter не создаётся. Selected state
и action остаются в одном source segment; paused raw samples/JPEG/state/action
полностью сохранены в source originals. Короткая пауза режет episodes независимо
от старого gap>3periods. Envelope, raw manifest, source clock/boot/context и exact
selection перепроверяются; forged selection не допускается даже с новым SHA.
Cached conversion не может потерять/заменить исходный timeline.

53integration PASS/2optinSKIP/5.75s и финальные6shared-selection/converter PASS0.70s.
Тесты использовали исходный writer и настоящие маленькие generated JPEG; official
LeRobot export и optimizer в этом scope не запускались. Первый group negative
(глобальная проверка Torch зависела от внешней test collection) сохранён; import
package boundary теперь проверяется в fresh interpreter. Source04 full pipeline,
worker/source approval binding и vertical UI/release всё ещё OPEN.
