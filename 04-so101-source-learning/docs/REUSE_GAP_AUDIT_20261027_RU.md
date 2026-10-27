# 04: короткая карта reuse и незакрытых программных границ

27.10.2026, read-only аудит исходников `65ae9d0`. Это предложение до реализации,
не новая квалификация и не готовность оборудования. Приоритет: 04→05→06→01;
01 сохранён в `8e9d658` и PARKED после двух уже начатых bounded pause8 smoke.
Новых optimizer/render/device/network запусков при этом аудите не было.

**Вывод:** native6 learning core уже существует. Требуются тонкий source04 capture
adapter и завершение operator/data wiring, а не второй recorder/trainer/UI.
Software-ready пока OPEN: пользователь должен получить развёрнутый полный путь,
который ожидает реальные данные. Fixture, перенос файла и обучение сами по себе
не подтверждают grasp, автономный task success или физическую непрерывность.

## Что переиспользовать

| Граница | Существующая реализация | Сохраняемое значение |
|---|---|---|
| Физический владелец | `shared/explorer_shared/so101_station.py` | Один servo owner; исходные read/write/30 Hz/hold/relative leader mapping неизменны |
| RGB и наблюдение | `node_agent.py`, `so101_observation.py::SO101CachedObservation` | Единственная Logitech Pi; cached fanout, exact JPEG/frame/SHA, native boot/clock/cutoff |
| Source samples | Station `record/start`, `record/stage`, `record/finish`, `_sample` | Measured leader/follower6, requested/accepted/dispatched6, normalized gripper, RGB, native clocks/calibration/host-write receipt |
| Согласие и контроль | `station_observed_activation.py`, `station_observed_http.py`, publication gate | COMMON challenge/displayed-frame/actor/owned epoch; команды только через явное USER действие |
| Архив/перенос | `native_capture_approval.py`, `native_capture_download.py`, `native_capture_transfer.py` | Exact archive/review SHA, Range/If-Range, durable `.part`, retry/cancel guards, проверка approval до и после переноса |
| Skill и review | `lab_skill_workflow.py`, `lab_skill_api.py`, `skill_catalogue.py` | Confirmed immutable Spec/version, session/scene/splits, source-video и phase-interval review, cohort fingerprint |
| Native6 Dataset | `so101_dataset_qualification.py`, `station_adapter.py`, `so101_dataset_admission.py`, `dataset_export.py` | Типизированный `so101_follower_calibrated_position6_v1`, causality, original IDs/SHA, official LeRobot Dataset/readback, sealed payload exclusion |
| Обучение/версии | `so101_skill_training.py`, `so101_diffusion_training.py`, общий ACT/DP backend, Supervisor/bootstrap/job runner | Реальные optimizer/save/reload и durable pause/new-PID resume; immutable model/checkpoint/source lineage |
| Shadow/отдельная проверка | `so101_shadow_job.py`, `so101_shadow.py`, `so101_source_evaluation.py`, `source_evaluation_api.py` | ACT и DP; worker-only ML, native6 cached input, отсутствие dispatch в shadow, явная observed physical attempt отдельно |
| UI | `station.js`, `lab-skills.js`, `source-evaluation*.js`, `manual-camera.js`, общие queue/model components | Текущий whole-skill путь и отдельные source-checked capabilities; не новый UI framework |

Frozen Station SHA проверен чтением:
`2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885`.
Контракт source04 native6 не преобразуется в ROSMASTER action9/state106. Общими
являются lifecycle/transport/Dataset framework/queue/ML implementation, а не
физические action/state spaces. Нормализованный gripper не равен силе/апертуре.
Возврат host `sync_write` не равен MCU ACK, достижению позы или контакту.

## Конкретные пробелы и наименьшее изменение

1. **Finish немедленно импортирует, исходного approval нет.**
   `app.py::station_proxy` после успешного `record/finish` создаёт `station_import`.
   Station не предоставляет post-seal approve/reject. Это отличается от готового
   02/03 transfer gate. Нужен source-side review exact sealed archive SHA и явный
   transfer-ready; expert/video/outcome review остаётся отдельным решением.
   Уже импортированные исторические оригиналы не переписывать и не повышать.

2. **Pause/resume/cancel отсутствуют.** Station имеет start/stage/finish, причём
   настоящий writer queue и raw samples уже существуют. Тонкий metadata adapter
   должен использовать этот writer, а не второй sampling/control thread. Source
   monotonic pause intervals и cancellation фиксируются отдельным immutable
   lifecycle envelope, связанным с оригинальным manifest/JPEG SHA. Raw samples
   во время паузы остаются исходными; accepted Dataset view исключает их.
   Cancel сохраняет оригинал и явно исключает его из expert cohort.

3. **Нельзя полагаться только на timestamp gaps для pause boundaries.**
   `station_adapter._causal_segments` сейчас режет только gap `>3/fps`.
   Короткая пауза обязана разрывать chunk независимо от этого порога. Нужен
   source-bound segment adapter: проверенные lifecycle intervals→отдельные
   native6 episodes; shared chunk loader и official exporter используются дальше
   без изменения математики. Один raw episode ID остаётся единицей split/review;
   segments не становятся независимыми успешными показами.

4. **Legacy station import ещё whole-buffer.** `job_runner.station_import`
   загружает `response.content` целиком, без `.part`, approval pin и Range retry.
   Общий `transfer_native_capture` уже решает эти задачи, но prefix пока фиксирован
   на `/native-capture/recordings`. Минимальное расширение: trusted fixed source
   endpoint profile для `/station/recordings`; browser не задаёт URL/путь/actor.
   Range helper можно вызывать для station sealed archive без нового transport.

5. **Ручной UI не держит подтверждённую ownership identity.** `station.js`
   вызывает `stopStation` с `{}`, а interrupt может сработать при pending start;
   poll не снимает локальное владение при чужом новом epoch. Переиспользовать уже
   проверенную схему 03: acknowledged ownedEpoch, поздний ответ очищает только
   свой returned epoch, pending CAS не является владением, чужой poll→локальный
   drop без STOP. Record finish должен передавать exact recording ID. Отдельный
   global emergency STOP остаётся отдельным явным действием.

6. **COMMON manual-demo purpose не реализован.** Текущий binding принимает
   `source04_eval` (только policy) либо `cooperative05`; обычный leader demo идёт
   через legacy station proxy с `operator_present:true`. Нужен разрешённый thin
   COMMON source04-demo binding со server-owned skill/session/calibration/input
   context, без фиктивного model ID. Существующий native executor/baseline/limits
   сохраняются; camera freshness использует реально отображённый exact Pi JPEG.
   Это требует отдельного Root согласования provider/Node hooks, не Station edit.

7. **Два UI представления модели расходятся.** Legacy `stationSkills` выбирает
   только `train_act`, хотя whole-skill jobs имеют `so101_skill_train`; lab-skills
   показывает shadow кнопку только ACT, хотя typed backend уже поддерживает DP.
   Свести текущие панели к существующей typed model/readiness/source-eval
   projection. Не выдавать старую offline модель или кнопки legacy rollout за
   full-task physical qualification. Обновить устаревшие утверждения docs о DP.

## Предлагаемый тонкий контракт для следующего атомика

Сохранить Station `record/start|stage|finish`. Source04 adapter добавляет только
ID-fenced metadata `record/pause|resume|cancel|review` и fixed read-only catalogue,
review/manifest/frame/download. Все mutations получают actor только от server
auth; браузер передаёт recording ID, ожидаемые revision/archive SHA и явное
решение, без numeric robot values/локальных путей/калибровок.

Новый sealed envelope связывает original Station manifest + JPEG hashes,
source-owned lifecycle intervals/segments и source context. Новая архивная сборка
содержит эти исходные bytes и metadata; старые Station manifest/TAR остаются
неизменёнными legacy originals. Последующий approval sidecar с SHA chain не
изменяет sealed archive. По возможности переиспользовать существующий review ABI
`explorer_native_capture_review_v1`: он связывает UUID/archive/actor/boot/clock,
а не размерность действия. Общую bounded archive/review функцию извлекать
инкрементально с contract tests, без массового переписывания работающего 02.

## Минимальная квалификация после реализации

- Fake original Station + один fake cached camera: start→pause→resume→finish;
  исходные raw bytes неизменны, короткая пауза разрывает native6 chunk, exact
  recording/revision guards; cancel/restart/late finish не повышают expert status.
- Authenticated local HTTP: reject/unapproved→0 transfer; approve exact SHA→Range
  interrupt/retry `.part`→hash/readback; delayed reject во время переноса и после
  aggregate инвалидирует cohort/train gate. Старые данные сохраняются analysis.
- Реальный official Dataset roundtrip на явных synthetic fixtures: RGB+state6+
  issued6 и masks/clocks/leader lineage сохранены, train/dev/sealed не смешиваются,
  action chunk не пересекает lifecycle boundary. Не запускать новый optimizer,
  пока Root не согласует ресурсное окно и frozen source pins.
- Browser fake tests: foreign tab/late start/blur/focus/camera loss, captured epoch
  scoped cleanup, single displayed camera readiness, finish ID, approve/reject UI;
  constructor/render/poll не делают control POST.
- Переиспользовать существующие ACT/DP Supervisor resume/reload и shadow tests;
  новый полный queue smoke нужен лишь на окончательном Source/core graph после
  согласования Root, не повторять алгоритмическую квалификацию по каждому UI edit.

## Что остаётся для пользователя и deploy

Фактический Pi preflight/ID/calibration/current camera и software release проверяет
Root отдельно. Не начинать полную перекалибровку без несовместимости: сохранить
leader `9512a1…78c` и follower `da0032…54` originals. Далее отдельная наблюдаемая
checklist: actual field of view, measured coordinates/gripper directions, original
continuous relative teleop и hold/STOP/network-loss, затем 3–5 малых smoke показов
с явным outcome/video review. Эти действия агентом не выполнялись.

VLM/segmentation/grid/click/table calibration — отдельные уже существующие
capabilities; они не являются условием native6 record/Dataset/ACT. Камеру не
открывать повторно и не требовать второго/третьего Pi stream. Shadow/replay video
не является physical rollout. После полного развертывания software gates и
реальные demos/physical acceptance продолжают учитываться раздельно.
