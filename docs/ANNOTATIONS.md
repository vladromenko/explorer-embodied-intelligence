# SAM 3 и наблюдаемые объектные признаки

Лаборатория использует настоящий **SAM 3 image model** из собственного установленного снимка официального исходного кода. `SAM3Segmenter` вызывает `build_sam3_image_model(checkpoint_path=..., load_from_HF=False, compile=False, enable_inst_interactivity=False)` и `Sam3Processor`. Автоматические загрузки, вход в HF и публикация не выполняются. По умолчанию веса берутся read-only из существующего локального `facebook/sam3` cache. SAM 3.1 не подменяет SAM 3 молча.

Источник API: [официальный README SAM 3](https://github.com/facebookresearch/sam3) и [официальный Sam3Processor](https://github.com/facebookresearch/sam3/blob/main/sam3/model/sam3_image_processor.py). Снимок исходного кода/патчи закреплены в `vendor/sam3-provenance.json`; установка в `.venv` проекта. В исходном upstream metadata указан `numpy<2`, тогда как LeRobot требует NumPy 2. Для общей среды изменена только metadata собственной копии. Импорт SAM, технические преобразования NumPy2 и настоящий CUDA forward на двух реальных снимках проверены. Вне этого испытания совместимость не предполагается автоматически.

Запись не вызывает SAM. Сначала сохраняются raw RGB обоих ракурсов, monotonic timestamps, measured joints и фактически отправленные actions. Отдельная задача затем читает только завершённые эпизоды. Перед созданием модели проверяются память, владельцы compute и общий lease `gpu_compute`. При конфликте задача ждёт на уровне job supervisor; владельцы ресурсов не завершаются. Базовый порог для SAM — минимум 8 GiB свободной общей памяти, не менее этого даже при пользовательском override.

Каждый `episodes/<id>/annotations/sam3-v1/frames.jsonl` содержит:

- schema `so101.annotations.v1`, точный raw `frame_id`, timestamp наблюдения и SHA256 контракта источника;
- для каждого top/wrist — точный timestamp capture, raw SHA256 и исходные размеры;
- категории `cube` и `box`, текстовые prompts, настоящие SAM score/boxes, центр видимых пикселей маски;
- маски PNG `L` со значениями только 0/255, SHA256 каждой маски, без потерь;
- отдельную оценку цвета из median RGB/HSV внутри видимой маски: метод и lighting limitation явные;
- provenance weights SHA256, source snapshot, threshold, preprocessing, configuration hash;
- видимость `visible_mask`; окклюзия, полная площадь, XYZ, depth, размеры и grasp по умолчанию **unknown/invalid**.

SAM image detector не даёт гарантированные identity. Здесь поверх текущих детекций реализовано только causal IoU association: ID локален для камеры и эпизода; confidence — overlap, `identity_guaranteed=false`. Пропавшие ID явно записываются в `lost_track_ids`, после исчезновения объект получает новый ID. При неоднозначном overlap создаётся `new_ambiguous`; пересечения одинаковых кубиков могут менять ID. При повторном использовании того же capture control loop метка `reused_capture` сохраняет идентичность, не создавая вымышленного нового наблюдения. Числа tracking ID не передаются в численный вход политики.

Цвет не является выходом SAM. HSV-пороги — проверяемый эвристический метод без гарантии при тенях/бликах; нужен реальный контрольный color-chart, если требуется точный цвет. При нулевых детекциях `not_detected_not_proven_absent`: это не доказательство отсутствия предмета.

Задача fsync-сохраняет каждую запись и маску; повреждённая последняя строка после сбоя отбрасывается при resume. Уже корректные кадры не пересегментируются. При изменении источника/конфигурации SAM переразмечается оставшийся causal suffix эпизода. Train loader проверяет raw image и mask SHA256, совпадение timestamp/hash, отсутствие будущего кадра и полноту обоих ракурсов. Stale/missing не превращаются в нули. Технические fixture-аннотации имеют backend `technical_test`; нормальное обучение отвергает их. Fixture backend нельзя выбирать в UI.

`SAM3Segmenter.annotate_observation` использует тот же контракт при inference: текущий RGB и те же prompts/threshold/preprocessing, без будущих кадров, action/success/phase/done labels. Время фактической генерации offline-аннотации хранится отдельно от capture timestamp: поздняя обработка файла сама по себе не является утечкой будущего. Для live-политики дополнительно необходимо контролировать возраст кадра/annotations перед отправкой действия.

# Геометрия

`geometry.py` не содержит догадок о длинах SO-101, размерах кубиков и текущем расположении камер. Скопированная старая калибровка сама по себе не считается актуальной. Применимость требует полного K/distortion/camera-to-world, разрешения, serial, подтверждённого mount fingerprint, совпадающего joint calibration hash и независимой verification с оператором/датой/методом.

`estimate_point_on_plane` может дать только **условную оценку** пересечения calibrated ray с независимо измеренной плоскостью `table`, `cube_top` или `box_surface`. Для верхней грани нужен отдельный измеренный plane/height; нельзя подставить стол вместо кубика. Uncertainty включает pixel perturbation и явно заданные calibration/plane uncertainties. Это не depth measurement; размер объекта, grasp и depth остаются unknown. Центр маски не гарантированно является физической точкой на плоскости.

FK допускается только с явно проверенными DH parameters, радианами, TCP transform, uncertainty и совпадающим joint-offset calibration SHA256. Встроенной выдуманной FK-модели нет. IK и grasp truth не выводятся.

# Реальная проверка

Программно проверены: lossless mask roundtrip, raw/annotation alignment, future-frame rejection, missing/stale/hash corruption rejection, causal ID loss/reuse, изменение конфигурации при resume, остановка/возобновление после torn JSONL, независимость от action/success labels, color method и консервативная геометрия. Эти тесты используют явно маркированные технические маски и математические калибровки. Они не доказывают работу SAM на реальных кубиках.

После финального испытания ниже остаются: систематическая точность masks/boxes/color/ID при окклюзиях; актуальная top calibration и wrist hand-eye; измеренные uncertainties; latency при live inference. Их нельзя объявить выполненными до свободных ресурсов и проверки оператором.

## Финальная программная приёмка 2026-11-06

Добавлены строгая проверка полноты production provenance и общая функция `validate_annotation_provenance(..., expected=...)` для train/inference. Идентичность включает SHA256 весов и установленного исходного кода, закреплённый commit, prompts, threshold, preprocessing, tracking/color contract. Путь cache, mtime и device не определяют содержание модели: перенос неизменных весов не создаёт ложной несовместимости. `configuration_hash` теперь основан на содержимом и также обнаруживает изменения собственного установленного SAM-кода.

Resume после пропущенной строки переразмечает весь последующий causal suffix, чтобы не наследовать несогласованные tracking ID. Tracking теряет прежнюю identity при разрыве capture больше1s. Сохранённые mask area и centroid сверяются с фактическими пикселями PNG; malformed bbox, невалидный float RGB и немонотонные raw capture отвергаются. Пустой датасет не получает ложный `complete`.

`annotation_status.json` и job progress теперь содержат `completed_frames/total_frames`, `completed_images`, `detected_objects`, `objects_by_category`, `objects_by_camera`, `new_frames`, `resumed_frames`. Корректные resumed кадры входят в счётчики. `detected_objects` означает сумму обнаружений по camera-frame, а не число уникальных физических объектов. Исчезновение объекта не превращается в уверенное отсутствие. Episode manifest получает `in_progress/stopped/failed/complete`; ошибка хранится отдельно, raw не меняется.

В этой приёмке 21 тест масок/геометрии/provenance/resume пройден на CPU. Это немодельные проверки с явными technical fixtures. Новые тесты не подменяют настоящий SAM forward.

`docs/SAM_ACCEPTANCE_20261106.json` фиксирует read-only проверку версий, license/hash и единственного существующего HF cache без копирования весов. Фактическая лицензия установленного снимка — **SAM License**, November19,2025; metadata classifier upstream не следует принимать за MIT-лицензию. Реальный SHA2563,450,062,241-byte `sam3.pt` совпал с именем HF blob. Снимок отчёта отражает момент, когда guard блокировал PID2785/LeLab; после разрешённого пользователем освобождения ресурсов фактическое состояние проверяется заново.

Для явного настоящего CUDA smoke подготовлен `scripts/sam3_gpu_acceptance.py --execute`. Он читает только уже снятые root-оператором top/wrist PNG, не открывает устройства и не управляет моторами. Серийно выполняется официальный SAM3 forward с реальными masks/boxes/score/centers/color и ограниченным CUDA allocator; сохраняются peak allocated/reserved и минимум MemAvailable. Нулевое число детекций не заменяется заготовкой. `segmentation_quality` остаётся `NOT_TESTED` до просмотра результатов оператором. Наличие capture timestamp берётся только из sidecar; отсутствующее время не выдумывается. Состояние фактического запуска — в `validation/acceptance-20261106/sam3/report.json`, если запуск разрешён и выполнен.


### Настоящий SAM CUDA forward после освобождения ресурсов

По явному разрешению пользователя root освободил владельцев ресурсов и выполнил единственный защищённый SAM GPU subprocess. Скрипт не открывал устройства. `validation/acceptance-20261106/sam3/report.json`: **PASS реального forward**, два настоящих PNG640×480, load33.81s, первый top10.32s (включая возможный first-run overhead), следующий wrist1.26s. CUDA peak allocated3.788GiB/reserved4.152GiB, минимальный общий MemAvailable4.460GiB; parent guard не сработал. В логе PyTorch есть warning о native CC8.7, однако эти конкретные операции SAM реально выполнены; blanket CUDA compatibility это не доказывает. Upstream missing_keys в логе отсутствуют.

Оригинальный SAM output сохранён неизменным: top3cube+2box, wrist1cube+1box. На wrist две категории имеют почти одну маску (IoU0.999064): это не два доказанных физических объекта. Общая функция отмечает `category_ambiguous=true`, хранит candidate categories и реальные query score; адаптер исключает такие детекции из categorical slots с validity0. Метрические geometry/occlusion остаются unknown.

Отдельный postprocessing без новой модели (`--postprocess-only`) сохранил `report_postprocessing.json`, `snapshot_annotations.jsonl`, `snapshot_alignment_validation.json`. Проверены реальные raw/mask SHA256, бинарность, area, centroid и настоящие timestamp каждого снимка. Это два отдельно снятых ракурса, не синхронная демонстрация; `training_eligible=false`. Не следует использовать эти картинки как реальные успешные демонстрации.

При недостаточной яркости HSV(V<0.15) цвет теперь `valid=false,label=unknown`; измеренные medianRGB/HSV сохраняются. Это затронуло тёмный синий кубик и тёмную коробку top. Жёлто выглядящий кубик получил эвристический hue-labelorange: калибровка цвета/освещения всё ещё не выполнена, никакой ручной подмены правильным цветом нет.

`report.json` доказывает forward и сохранение действительных SAM выходов. Он не доказывает полную точность распознавания, качественный temporal tracking, пригодную для робота live latency или обученный захват. Нынешний guard возраста наблюдения нельзя обходить: тёплая обработка двух камер вместе с VLA ещё требует измерения. Остаточная физическая приёмка: настоящее control-loop recording, актуальные калибровки, операторский просмотр разметки/цвета/окклюзий и безопасное исполнение обученной политики.
