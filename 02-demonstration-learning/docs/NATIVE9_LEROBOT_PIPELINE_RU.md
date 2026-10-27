# Native02 → LeRobotDataset → ACT106→9

Это дополнение сохраняет единый девятимерный ROSMASTER contract. Реальные
нейтральные координаты остаются девятью координатами; отсутствующий measured
feedback остаётся null с false mask. Исходные native TAR, manifest, canonical
facts, RGB и approval receipt не редактируются.

Новый shared `native_capture_approval` содержит единственный чистый проверяющий
Source review. Старый central import API сохранён. Aggregate несёт точный
approval snapshot, archive SHA и признак native source; `CanonicalCorpus`
повторно проверяет не только байты, но и approve/identity/hash semantics.
Перед отложенным worker fit `native_capture_cohort` проверяет последнюю локально
сохранённую central USER review. Поздний reject или новая revision не меняет
старый aggregate: задание блокируется до нового явного решения/aggregate.
Это local recheck; он не заявляет повторный remote Source GET во время fit.

Паузы определяются только `recording_segment.index` с provenance в исходном
JSON и совпадающим `recording_segment_id`. Скаляр в envelope сам по себе не
доказательство. Старый producer без сегментов допускает один segment только
при отсутствии source pause intervals. Chunk не пересекает паузу, пропущенный
исходный sample или конец episode: остаток имеет `action_is_pad=True`.
Segment не создаёт дополнительную демонстрацию для coverage/split.

`rosmaster_lerobot_dataset` — worker-only адаптер к установленному официальному
`lerobot==0.6.1`. TRAIN и DEV экспортируются в **разные** local LeRobotDataset,
SEALED bytes исключены. Каждая непрерывная исходная часть — отдельная technical
episode. Колонки `action` содержат реальный causal issued target9, state —
53 исходных значения и53 availability mask. Native timestamps int64 и полные
clock/boot stamps сохранены отдельно, RGB сохраняется lossless после одного
явного resize. Загрузка сверяет значения/state/masks/RGB/indices/timestamps с
исходным каноническим corpus. Предобработка и нормализация ACT прежние,
fit normalizer только TRAIN, без повторной ImageNet нормализации.

Официальный writer запрещает caller-supplied timestamp и сам ставит
`frame_index/fps`. Поэтому `fps=1` здесь — **логическая индексная сетка**, не
частота native публикаций, не exposure и не предположение об одинаковом
временном шаге. Exact native monotonic ns/clock/boot остаются source columns
и immutable index. Автоматический temporal resampling/interpolation отсутствует;
ACT loader выбирает соседние исходные indices с настоящими segment boundaries.

Экспорт fsync-ит файлы и каталоги, закрепляет SHA inventory/index, затем atomic
rename. Повторное использование проверяет прежние байты; частично опубликованный
derived dataset не принимается как полный. Source и implementation pins входят
в optimizer resume signature. Cancel проверяется до модели, между export rows и
перед результатом; прежний checkpoint protocol/RNG/sampler/optimizer не заменён.

`test_rosmaster_native02_e2e.py` сначала пропускает настоящие классы producer,
tap, camera cache, importer и cohort через **искусственные memory-only native
публикации и сгенерированные JPEG**. Все HTTP requests — GET с fake auth.
Это инженерный software fixture, не реальная демонстрация и не physical success.
Опциональная bounded CPU qualification выполняет официальный Dataset roundtrip,
ACT8, original durable pause на finished step4 с exit75, новый процесс +4,
сравнение с uninterrupted8, save/reload, saved DEV evaluation и настоящий model
inference над source fixture без dispatch. Инструментация теста лишь ставит
обычный pause request на четвёртом завершённом шаге; optimizer/math не подменены.

Запуск квалификации требует отдельного согласованного CPU2/4GiB/swap0 окна:
`EXPLORER_RUN_NATIVE02_CPU_QUALIFICATION=1`. До фактического успешного запуска
этого кандидата Root сохраняет production02A pause-training guard. Старые CPU/GPU
квитанции не квалифицируют новый dataset-loader source graph. Реальные approved
показы пока отсутствуют; production cohort остаётся blocked. Старый actual
native reference fault также остаётся отдельным live-shadow blocker до USER
наблюдаемого reference, без агентского HOME или включения readback.
