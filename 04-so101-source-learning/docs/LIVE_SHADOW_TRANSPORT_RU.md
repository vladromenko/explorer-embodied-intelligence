# ACT: одно предсказание по текущему native-наблюдению

`POST /api/lab/source-shadow` принимает только `model_job_id` завершённого
whole-skill ACT6, обученного по настоящему `SO101_RECORDED_DEMONSTRATIONS` cohort.
SIMULATION и Diffusion здесь отклоняются; history2 Diffusion требует отдельного
адаптера. Модель/checkpoint SHA закрепляется сервером в задании.

Worker `so101_skill_shadow` загружает модель отдельно от Control Center через
строгий source/cohort/checkpoint/official-processors loader. Первое наблюдение
используется только для camera/calibration binding. После загрузки получает
новый пакет и точный JPEG, проверяет SHA/boot/frame ID и локальное время HTTP
получения/инференса <=2с. Межмашинные monotonic timestamps не вычитаются.

Pi `GET /station/observation` читает только существующие CameraSource и
SO101Station.status caches. Возвращает измеренный follower6 с маской, JPEG,
предшествующий измерению, и cutoff создания пакета того же boot/domain.
`/station/observations/{id}/frame` отдаёт immutable pinned JPEG (8pins/16MiB,
срок2с), не подменяет его новым кадром. Ни один из путей не открывает camera/serial
и не вызывает enable, teleop, policy/start, action, lease или STOP.

Сохраняются исходный packet, JPEG, предсказание и независимые SHA. Это одно
техническое shadow-предсказание, **не полный rollout и не проверка навыка**;
оно не делает physical readiness принятой. Старый station_shadow legacy path
сохраняется отдельно. Физический допуск всегда остаётся false.

Тесты используют локальные software JPEG/модель/HTTP fixtures, причинный
валидатор настоящего ACT adapter, проверяют отказ при подмене кадра/boot/clock,
нечисленном/неполном state и неправильном источнике модели. Реальная предметная
policy и физическая успешность этим тестом не квалифицированы.
