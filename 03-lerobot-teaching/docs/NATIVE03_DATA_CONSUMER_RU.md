# Канонический consumer 03: общий путь ROSMASTER9

Новый `retarget_import`, project03, `source_format=native_capture_v1` читает
ТОЛЬКО Nano `/native-capture/recordings/{id}/{review,download}`. Pi остаётся
источником управления/leader measurements; новый TAR не перенаправляется через
старую Pi `/arm-retarget/recordings`. Старый explicit import без нового format
сохраняет прежний Pi raw путь; отсутствие approval не создаёт BC expert.

`job_runner.approved_native_capture_import` общий для точных пар
`mobile_import/02` и `retarget_import/03`. Повторно использует
`transfer_native_capture`: authenticated GET, bounded chunks, SHA/ETag/Range,
сохраняемый `.part`, explicit retry lineage и проверка ТОГО ЖЕ Source review
до и после переноса. Reject/смена review/cancel не дают import admission.
Approval original сохраняется отдельно от manifest/TAR/outcome.

Worker проверяет recording_id и ТОЧНЫЙ project в outer manifest и source
embodied envelope до извлечения. Новый03 destination `episodes/native-retarget`,
02 — прежний `episodes/native-mobile`. Unknown format не вызывает Pi fallback.

Inspector требует approval для ВСЕХ native-capture source containers, включая
03 и ошибочно выбранный import project. Kind/project должны соответствовать
source project. Для native03 нужен source-bound verified
`input_adapter.input_mode=so101_leader`, а не browser/client declaration.
`source_intent_binding` сохраняет actual source payload: leader6, calibration,
Pi boot/clock/sample, geometric/accepted-native provenance, где оно записано.
Nano action9/prior-command/RGB имеют собственные native clocks. Нет вычитания
Pi/Nano часов, подстановки measured follower/readback или неизвестных нулей.

Общие skill_workflow/cohort gates уже проверяют latest central rejection и
immutable approval snapshot перед delayed fit. Они применяются к03 через
тот же inspector; source outcomes и исходные байты не переписываются.
Чанки не переходят source-bound pause segments; демонстрации считаются по
original episode, не по количеству сегментов.

## Интеграционные проверки

`tests/native03_consumer_helpers.py` создаёт ТОЛЬКО программные фикстуры:
NativeCaptureWriter + NativeDispatchTap memory receipts + generated JPEGs,
source03 leader binding, approval, actual worker MockHTTP import,5демонстраций.
CanonicalCorpus получает3 train/1 DEV/1 sealed; последние исключены из fit.
Это НЕ реальные пользовательские демонстрации/MCU dispatch/physical outcome.

`tests/test_native03_consumer.py` проверяет полный data consumer, общий9D/106
contract, metadata-only enqueue/version/eval/shadow project03, Source refusal,
foreign project/actual input adapter, old-path separation, Range retry/cancel,
поздний reject и delayed cohort withdrawal. В evaluator используется явно
обозначенный dummy model/loader: оптимизатор/настоящие weights не создаются.

Опциональный Dataset-only тест (без модели/оптимизатора):

```
EXPLORER_RUN_NATIVE03_DATASET_FIXTURE=1 python -m pytest -q \
  tests/test_native03_consumer.py::test_native03_common_official_LeRobotDataset_target9_roundtrip_no_optimizer
```

Он вызывает существующий official LeRobotDataset0.6.1 exporter и сравнивает
каждый tensor с неизменённым common FusedChunks. action9/state106,
3originaltrain→6pausedsegment episodes, без sealed payload и data resampling.
Все Source originals rechecked. Отдельная JSON receipt сохраняет результаты
и отрицательные попытки; никакой optimizer/новой controller qualification нет.

## Root hooks и открытые границы

Root владеет Store/poll/API/UI: новый native03 job pin должен включать
source_format, archive_sha256, source_review_sha256; producer только Nano,
kind retarget_import/project03. Capture namespace `/api/retarget/capture/*`
отделён от Pi control. Импорт не запускает обучение автоматически.

Изменённый job_runner означает новую executor source identity. Старые02
actual CPU train/resume/queue receipts остаются историческими exact-source
доказательствами, их SHA/результаты не переписываются. ACT/106/officialDataset
math/runtime leaf bytes этим атомиком не меняются. Dataset-only тест не квалифицирует очередь. Отдельный фактический CPU queue
proof приведён ниже; исходная Dataset receipt сохраняет свой прежний scope.

Continuity03 с меняющимся реальным leader остаётся OPEN; observed task axes,
два endpoint гриппера и действительная Native reference отдельно не выдуманы.
Ни consumer, ни Dataset fixture не разрешают физический begin/movement.

Фактический bounded Dataset-only результат: `1 passed in13.75s`, unit runtime
18.334s, CPU1/MemoryMax2GiB/SwapMax0/LimitCORE0. Внутренний cgroup sampler:
peak526401536B, swap0, memory.events все0; child ru_maxrss744572KiB учитывает
другую RSS область. Ошибочный systemd MemoryPeak340KiB не принят как Torch
measurement. Предыдущие launch127 и test wrong-index-key FAIL сохранены, не
выданы за успешные запуски. Продуктовые/helper bytes и выполненная test function
не менялись во время этой проверки; весь test module не объявлен frozen.
Итоговая лёгкая группа:41PASS/1 optinSKIP25.83s; provenance/NoContinue/StationSHA
PASS. Вынесенная Dataset-only optin проверка прошла отдельно, не подменяет skip.


## Отдельная фактическая очередь03

`NATIVE03_QUEUE_CPU_20261027.json`: frozen f69b7d4e,1PASS33.30s;
5generated NativeSource03→approved Nano import→3TRAIN/1DEV/1sealed→реальный
ASGI/Supervisor/bootstrap/job_runner ACT4 updates/save-reload0→новый PID DEV10
с0optimizer. State106/action9,3originaltrain→6pause segments. Исходный общий
harness используется без копии training math; отдельный binding устанавливает
actual source/project03. First negative harness attempt использовал raw source
adapter вместо существующего workflow adapter; API отказал до train job/model.
Оба исхода сохранены. CPU2/4GiB/swap0/core0, kernel peak1336369152B/events0.
Это actual CPU execution на generated source data,0real expert demonstrations,
0physical requests. Реальный moving-leader continuity gate остаётся OPEN.

## Сырые RGB и командные пары — отдельные счётчики

Technical observations-only source может содержать RGB при0command samples.
Inspector отдельно проверяет исходные observation JSON/SHA, JPEG bytes/shape/decode
и уникальные frame IDs. `raw_frames_recorded`/`raw_rgb_verified_count` сохраняют
эти факты; `rgb_verified`, causal9D и training admission от этого не меняются.
Raw sensor availability/masks показаны отдельно от допуска и actual model use.
Evidence API/UI scoped по project02/03; ожидающий перенос job не называется
перенесённым. Раздельные browser clients сохраняют контекст при смене страниц.
Фактический candidate original39RGB проверен:39decoded/0expert/0admitted;
повторная запись или изменение source originals для проверки счётчика не нужны.
