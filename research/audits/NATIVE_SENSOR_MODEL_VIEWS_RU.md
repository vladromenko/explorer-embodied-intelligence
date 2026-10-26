# Доступные native sensors и неизменённый ACT encoder

Новый `native_model_views.py` адаптирует форматы данных к существующему feature encoder, не меняя ACT, normalizer, optimizer, Ruckig или контроллер. Вызов NativeCaptureWriter выполняется в независимом file writer, а не на границе публикации/servo loop.

Две выявленные несовместимости закрыты явными model views:

- OWN RGBD cache хранит исходный NPZ с float32 depth в метрах. Frozen encoder умеет raw/Pillow depth, но не NPZ. View сохраняет исходный NPZ побайтно/SHA и создаёт lossless float32 TIFF с теми же формой, единицами, NaN/inf и значениями. Resampling, scale guess и RGB→z отсутствуют; readback проверяется на равенство.
- Partial LiDAR имеет null вместо невалидных лучей и отдельный per-ray valid mask. Раньше dict mask приводил к игнорированию всего скана в encoder. View сохраняет null/raw ranges/per-ray masks и помечает структуру доступной для **того же** существующего фильтра конечных лучей. Только реально заполненные angular buckets получают model valid=True. Неизвестное направление не становится нулём или свободным пространством.

Оригинальные channel records и описание преобразования сохраняются отдельным SHA-bound source JSON. Корреляция кадров, source stamps и extrinsics не меняются. Неизвестные формат/precision/маски сохраняют исходную available запись и явный `unsupported_feature_formats`; они не переписываются как отсутствующие измерения.

TIFF encode кешируется по исходному SHA+shape: максимум 8 views/16MiB, без дополнительного потока или устройства. Тест 100 повторов одного исходника выполняет encode один раз. Числовой тест обращается к настоящему неизменённому `sensor_features`: четыре depth statistics и пять валидных LiDAR buckets получены из заданных исходных массивов; оригинальные bytes и invalid rays неизменны. 40 связанных source/capture/cache тестов прошли без аппаратных запросов.

View не обучает модель задним числом. Если сохранённый normalizer/model manifest не видел этот канал на train, он остаётся recorded/admitted, но не объявляется model-used. Новое обучение получает тот же формат view, что последующий runtime. Это инженерный data adapter, не доказательство навигации, контакта, grasp или физического владения задачей.
