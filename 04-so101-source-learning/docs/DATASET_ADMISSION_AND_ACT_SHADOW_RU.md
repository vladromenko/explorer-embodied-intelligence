# Один допуск набора для ACT6 и Diffusion6

`explorer.so101_dataset_admission.verify_so101_aggregate(store, config)` возвращает
`(manifest_path, document, native_view_path, native_view)` до выделения модели.
Импорт helper не загружает Torch или robot APIs. ACT и Diffusion используют
одинаковую функцию; прежнее `so101_diffusion_training._aggregate` — alias.

Проверяются исходный SHA целого SkillSpec, подтверждение версии, долговечная
серверная aggregate event и SHA её payload, все оригинальные binding/review
снимки и актуальные решения существующего workflow. Флаги внутри архива сами
по себе не допускают обучение. Проверяются retained artifact SHA, точные
train/development/sealed original IDs, отсутствие sealed payload в model view,
наличие настоящих native6 state/action arrays, исходные диапазоны/имена/units.
Ранее реализованные phase/operator/clock/mask/scene/session/object split rules
переиспользуются, а не переписываются. Одинаковые декодированные RGB не могут
пересекать splits даже при разных JPEG metadata. Изменённый review/cohort
требует нового aggregate; старые bytes и модели остаются историей.

Правила допуска и producer contract входят в durable optimizer source context.
Изменение rules при прерывании не подменяет источник старого checkpoint.
Optimizer math/settings, range и ImageNet transforms сохранены.

`explorer_shared.learning.act_policy.NativeACTPolicy` загружает только hash-bound
CPU ACT6 с сохранённой официальной парой pre/post processors. Новое обучение
сохраняет и действительно исполняет эту пару, проверяя совпадение first action
с исходной model path. Старый checkpoint без пары не получает guessed fallback.
Нужны точные source camera ID и follower calibration SHA; use_degrees=False,
measured_state и нормированные родные координаты не заменяются radians/degrees.

`explorer.so101_shadow.load_shadow_policy(store, model_job_id, *,
expected_camera_id, expected_calibration_sha256, allow_simulation=False)`
проверяет серверную завершённую модель, карточку, cohort и все checkpoint SHA.
SIMULATION fixture требует явного `allow_simulation=True` и остаётся software-only.
`infer_shadow(policy, rgb, measured_native6, *, valid_mask, observation)`
возвращает predicted_native6, имена/units, lineage и receipt. Ни один путь не
импортирует official hardware class, не подключается к роботу и не отправляет
команды. Это hook для отдельного server-owned shadow job; не новый executor.

ACT history1 → output1; это отличается от Diffusion history2 → chunk4. Каждый
ACT вызов сбрасывает action queue и обусловлен текущим переданным RGB/state.
`observation` содержит camera_id/frame_id, original frame_sha256 и
decoded_rgb_sha256 (проверяется по переданному RGB), state_basis,
follower_calibration_sha256, frame/state/source_cutoff clock_domain/boot_id и
monotonic_ns. Все три stamp должны быть в одной доказанной native domain/boot,
frame≤state≤source_cutoff, возраст относительно cutoff≤2s. Нельзя подставлять NX
или Mac время вместо native cutoff. Допустимы только actual native packet
cutoff или явно offline original-dispatch cutoff. Null/invalid masks не
заполняются нулями; настоящие measured neutral значения сохраняются.

Транспортная freshness на NX, авторизация источника и проверка original JPEG
bytes по frame_sha256 — обязанности вызывающего server adapter: данный pure
feature API видит декодированный RGB, а не JPEG. Receipt явно сохраняет это
ограничение. Offline development кадр помечается offline inference, не живым
shadow rollout. Предсказание, range projection, source operator label или loss
не доказывают контакт/достижение/успех задачи и не разрешают физическое движение.
