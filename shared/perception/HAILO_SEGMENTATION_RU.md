# Квалификация декодера instance segmentation

Новый `explorer_shared.hailo_segmentation` получает BGR из уже существующего
`HailoObserver`. Импорт ничего не открывает. Камеры, serial/руки/шасси и capture-loop
модуль не создаёт. `create_hailo_detector(path)` выбирает сегментацию только для
проверенного SHA256 `edb7f33aa7212a24dca0090cfca9b83ca72cc81b7385a3225ca2cd465092b9ed`
модели `/usr/share/hailo-models/yolov5n_seg_h10.hef`; для остальных сохраняется
прежний COCO NMS detector. Реальное создание VDevice выполняет только прежний
единственный worker; `semantic_slot` продолжает освобождать его перед VLM.

## Primary source и лицензия

Взят официальный Hailo-Application-Code-Examples commit
`7b850e0444a142ada88443d675e3c3ddda18bd74`, каталог
`runtime/python/instance_segmentation`. README явно указывает HailoRT5.1.1 для
Hailo10H. MIT License (Copyright2023 Hailo) и точные исходные файлы/их SHA сохранены
в `_vendor/hailo_yolov5_seg/`. Пин, source URLs и изменения перечислены в
`source-manifest.json`; пакет data/JSON/LICENSE нужно включать в source deployment.

Из upstream без изменения математики взяты YOLOv5 grid/anchors/stride decoding,
confidence и mask coefficients, sigmoid prototype projection, bilinear upsample,
ROI crop и нормализация boxes. Cython NMS заменён эквивалентным NumPy port его
inclusive `+1` pixel IoU, сравнения `>=threshold` и original-index/tie order.
Это позволяет обойтись без Cython build на Pi. Dependency boundary не импортирует
visualization, чужой camera runner или robot libraries. Используется single-label
режим официального NMS API, threshold0.35, максимум20 инстансов до mask upsample;
mask threshold0.4 взят из официального config. Эта конфигурация явно записана в
model_info. Новый декодер и версия runtime не выбираются по догадке из tensor values.

Проверяются именно четыре FLOAT32 host-dequantized головы:
`160x160x32`, `20x20x351`, `40x40x351`, `80x80x351`, RGB640x640 и runtime5.1.1.
HailoRT outputs явно переводятся в FLOAT32, вход UINT8; неожиданный shape/order,
quantized integer payload, NaN или другая HEF/version отклоняются. Модель читает
640RGB, а входной Frame прежней камеры — BGR: преобразование RGB и официальный
floor-sized INTER_CUBIC114-letterbox воспроизведены точно. Сначала удаляется именно
реальное integer padding; маски вероятностей возвращаются к исходному разрешению,
затем порог. Box не подменяет прототипную маску.

## Контракт результата и точного кадра

Detections сохраняют `class_id`, `label`, `score`, нормализованный исходный xyxy `box`
и `track_id`. Маска — `mask_rle`:
`{encoding:'row_major_runs_v1',shape:[height,width],starts_with:0,counts:[...],mask_sha256}`.
SHA вычислен по `np.packbits(mask.ravel(order='C'))`. `decode_mask_rle` возвращает
истинный Boolean source-resolution array и проверяет SHA/shape/count bounds.
Максимум1920x1080 pixels,100000 runs/instance,200000 runs/observation,20 инстансов.
Если valid mask превышает encoding budget или пуста после threshold, box остаётся
оценкой, но `mask_available=False`, `mask_rle=None` с явной причиной. Click selection
такой инстанс не превращает в bbox-filled mask.

Observation содержит `frame_id`, `frame_sha256` **оригинальных JPEG bytes**,
`frame_monotonic_ns`, `model_sha256`, `track_session_id`, actual measured inference+
postprocess latency и прежние vision_estimate/contact_verified=False. Snapshot
возвращает глубокую JSON-копию. Кэш хранит до8 последних обработанных ORIGINAL Frame
и их собственных observations, плюс64MiB budget JPEG+BGR (обычный640x480 помещает8).
Методы `cached_observation_frame(id)`, `cached_observation(id)` и
`cached_observation_pair(id)` возвращают только точное совпадение, иначеNone.
Pair атомарно возвращает `(originalFrame, frozenJSONObservation)` для сервиса06.
Новейший30FPS preview не подставляется под предыдущую2FPS маску. Track session
локальна экземпляру worker, не является доказательством личности/контакта/захвата.

Root lab_vision_service должен декодировать RLE для existing tabletop.select_target,
использовать точный Frame+observation pair и сохранить frame/model/mask SHA в selection.
Сам декодер не выводит метрические XYZ, не калибрует стол/камеру и не запускает руку.

## Выполненная программная проверка

Homebrain: raw-head software fixture с одним COCO bottle, известным anchor box и
прототипом, у которого часть bbox не принадлежит маске. После unpadding получается
точный640x480 Boolean mask6660 pixels; пиксели внутри bbox, но вне prototype остаются
False. Проверены pixel resize с нечётным aspect ratio по точной официальной функции,
source license/SHA, NMS threshold/ties, повреждённые/неверные/quantized heads,
RLE bounds/checksum, immutable original tensors, factory fallback, строгий mock
HailoRT5 context/float buffers и cleanup при частичной ошибке. Кэш проверен на10
последовательных source Frames: первые2 evicted, остальные exact objects, frozen
masks не меняются вместе с current preview. Existing semantic-slot exclusive-owner
и camera/node tests также проходят.

На Pi только read-only import/spec inspection подтвердил numpy/cv2/scipy/hailo_platform
и HailoRT5.1.1; дополнительный VDevice, capture или serial не создавался. Реальная
сегментационная inference/latency/scene accuracy ещё не квалифицирована этим source
этапом: root отдельно перезапускает собственный idle observer и проверяет через
аутентифицированный API существующего владельца. Физический навык не подтверждён.
