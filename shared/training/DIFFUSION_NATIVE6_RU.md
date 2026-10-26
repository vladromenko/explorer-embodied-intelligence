# Native SO101 Diffusion: источник 04 и общий backend

Программный consumer использует семантическую роль
`experiment_id('so101_source_learning')`, сейчас04. Исторические номера,
SkillSpec и исходные SHA не переписываются. Фактическая квалификация ниже
сохранила старый Spec owner06 и создала canonical job04 по явной роли;
число06 не интерпретировалось как новое назначение страницы.

Общие компоненты в `explorer_shared.learning`:

* `HistoryWindows` — две предыдущая/текущая RGB+state позиции одного оригинала,
  target horizon начинается с t-1, выдаваемые команды — offsets1..4. Край
  повторяет только реальное граничное значение с явной маской. Нулевого
  заполнения и будущих кадров в actor нет. Кешируется один исходный эпизод.
* `diffusion_optimizer.optimize_diffusion` — официальный Adam preset, настоящий
  loss,None API, source/config/implementation signature, single writer lock,
  атомарный checkpoint, optimizer/RNG/sampler и настоящее продолжение updates.
  Shared primitives sampler/RNG/atomic save используются совместно с ACT,
  ACT-specific AdamW/loss semantics не переносятся.
* `diffusion_policy.save_reload_diffusion` — официальный save/load с фиксированным
  DDIM noise; `NativeDiffusionPolicy` — hash-bound loader и чистое преобразование
  history→четыре native-команды. Массивы никуда не отправляются. Исходные
  нормализованные единицы SO101 сохраняются, use_degreesFalse; hardware robot
  class, replay CLI и привод не используются.

`explorer.so101_diffusion_training.run_so101_diffusion_train(store,job)` связывает
общий backend с тем же серверным immutable whole-skill aggregate, который
использует ACT. Требуются серверное событие принятия набора, точный Skill/version
и source SHA, все source/review artifacts, measured follower6, отдельно issued
native6, исходные диапазоны, train/development оригиналы и подтверждённое чтение
настоящего официального LeRobotDataset. Raw/hold/analysis архив не может
объявить себя expert одной установкой флага в JSON. Существующий проверенный
reader/officialDataset adapter остаётся по compatibility пути explorer_sim;
общий backend не импортирует эксперимент01 или pilot.

Этот адаптер сохраняет provenance всех recorded leader/requested/accepted/
dispatched/measured полей, camera/clock/calibration, phase/outcome и session/
scene/split через исходный набор и SHA; модель получает только RGB history2
и measured follower6. Фазы/текст/операторские исходы не входят в actor. Закрытые
payload отсутствуют в обучающем view, их оригинальные ID остаются в lineage.
Метрическая калибровка стола и геометрия контакта не выводятся из потерь модели.

## Подключение root

Kind остаётся `so101_skill_train`, project определяется общей семантической
таблицей. Config: backend=`diffusion`,
profile_id=`diffusion_resnet18_unet32_64_ddim4_cpu_v1`, device=`cpu`,
image_size64, batch_size<=2, seed, optimizer_steps; старое имя act_steps
принимается как равный alias бюджета. Также нужны aggregate_manifest/SHA,
skill_id/version/specSHA. При отсутствии backend по-прежнему выбирается ACT
в существующей платформе; этот модуль default не меняет.

Worker должен использовать квалифицированный persistent executable
`~/.local/share/explorer/policy-runtimes/diffusion-0.38.0-cpu/bin/python`.
Base CPU venv не получает optional dependencies. Серверная очередь/маршруты
подключаются root отдельно; production_queue_ready остаётся false до их проверки.
Метод карточки: `DIFFUSION_LEROBOT_0_6_1`, source_kind/type SIMULATION для
квалификационных фикстур, deployment_scope SIMULATION_ONLY. Настоящие записи
порождают только OFFLINE_SHADOW_PENDING_SEPARATE_OBSERVED_TEST.

Реестр по умолчанию отказывается создавать общий Diffusion. Явный запрос
`get_backend('diffusion', profile_id=..., source_contract_id=
'so101_follower_calibrated_position6_v1', state_dim=6, action_dim=6,
device='cpu')` возвращает только проверенный профиль. ROSMASTER9D, GPU,
другие размеры и SmolVLA этим свидетельством не допускаются. Выбор backend
не заменяет принятие фактического набора данных в typed source adapter.

Loader не объявляет кадры свежими и не проверяет физический источник сам:
offline caller передаёт проверенный набор, будущий shadow caller обязан
проверить реальные frame IDs/timing/camera/boot/calibration и masks своей
общей acquisition-системой. Missing/invalid measured mask отклоняется,
а не заменяется нулями. Никакого физического запуска в этом пути нет.

## Реальные программные проверки 2026-10-26

Постоянные временные артефакты находятся ВНЕ production-state:
`/mnt/explorer-data/explorer-work/qualification-dp6/consumer-20261026-a`.
Синтетические5 originals: три train, один development, один sealed; официальный
Dataset содержит4 эпизода/32 кадра, sealed payload не используется. Прошёл
полный source→officialDataset readback→DP6 optimizer4→injected interruption→
restart4→save/load→native-coordinate prediction→Store SIMULATION_ONLY card.
Тест:1pass22.37s, собственный unit4GiB/2CPU, production-state не изменялся.
Loss1.290305→.689718, RGB first-conv gradient .0843255, reload error0.
Development normalized chunk MAE .880285 — слабый диагностический результат,
он не означает успешную манипуляцию. Модель физически не исполнялась.

Отдельный непрерывный reference на ТОМ ЖЕ неизменяемом наборе сделал8 updates.
Checkpoint files, loss history и сохранённый optimizer state побитно совпали
с прерванным4+4. Reload0. Reference peak RSS1 241 735 168B, kernel cgroup
memory.peak1 301 733 376B, max/oom/oom_kill0. Это измерение отдельного reference;
пик первоначального pytest4+4 отдельно не записывался. Обе задачи ограничены
4GiB и завершились успешно. Обычные source/history/registry/None-terms/RNG
тесты также проходят без повторного обучения модели.

Точные первичные receipts: `DIFFUSION_NATIVE6_QUALIFICATION.json`,
`DIFFUSION_NATIVE6_RUNTIME_BINDING.json`, `DIFFUSION_NATIVE6_MATCHED_REFERENCE.json`,
`DIFFUSION_NATIVE6_OPTIMIZER_COMPARISON.json`. Они подтверждают программный
consumer и воспроизводимость, не освоенный навык, shadow или физический успех.
