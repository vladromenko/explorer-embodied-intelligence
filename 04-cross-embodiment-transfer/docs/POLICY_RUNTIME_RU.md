# Реальное исполнение checkpoint и виртуальный перенос выходной траектории

`explorer_sim.policy_transfer.run_policy_transfer(store, job, config)` — новый офлайн-путь. Он действительно загружает **официальный ACT LeRobot0.6.1**, выполняет preprocessor → `select_action` → postprocessor и получает предсказанные шесть именованных координат. Затем, если предоставлены корректные преобразования, выполняет SO101 FK → object-relative SE(3) registration → M3 IK. Независимый MuJoCo evaluator отправляет найденные углы в position actuators, делает400 физических шагов на точку и проверяет фактический `Gripping.xpos`, а не возвращает residual из IK.

Это replay входных наблюдений, не замкнутый rollout исходной руки: предсказанные исходные команды **не исполняются** на SO101. Полный путь доказывает программную исполнимость policy-output transfer и виртуальное слежение за позицией. Он не доказывает перенос наученного предметного навыка, захват/передачу предмета, ориентацию инструмента, collision safety или физический результат. Клешня не переносится без отдельной калибровки апертуры.

## Контракт задания для root job runner

`config['root']` — корень monorepo; `store.root` — существующее хранилище; `store.update_job` — имеющееся обновление прогресса. В `job['config']`:

- `dataset_manifest`: локальный manifest нашего NPZ-формата после существующего station importer;
- `source_episode_id`: один явный train/development episode, sealed/test отклоняются;
- `checkpoint_path`, `checkpoint_sha256`: локальный checkpoint и **все** файлы с SHA256, включая processor configs/states;
- для physical recording: `training_result_path`, `training_result_sha256`, связывающие точный checkpoint с SHA выбранного исходного manifest;
- для внешнего checkpoint без Explorer adapter: `checkpoint_contract_path/checkpoint_contract_sha256`; JSON связывает checkpoint SHA map, source manifest SHA, `action_names/action_units/state_basis` и `action_representation:joint_position`. Совпадения шести размеров недостаточно;
- `max_observations`:1..1000;
- `mapping_profile`: путь к явному профилю ниже; отсутствие не мешает реальному inference, но блокирует FK/IK;
- `registration`: `source_base_from_object`, `target_base_from_object`, `provenance` (`virtual_declared` или `measured`), `calibration_ids` для measured;
- `target_initial_rad`: пять именованных M3 URDF углов, по умолчанию только явно виртуальная исходная поза;
- `mesh_dir` либо существующая переменная `EXPLORER_SIM_MESH_DIR`:20 checksum-verified CAD meshes.

Минимальное root-owned подключение: новый job kind → вызов указанной функции в существующем worker с PYTHONPATH01/src+shared. В приложении показывать `status`, `executed_stages`, `mapping_problems`, `independent_virtual_attempts/passed`; `source_policy_executed=true` относится только к inference. Не заменять прежний виртуальный reference test новым результатом задним числом. Поддерживается сейчас ACT; произвольные Diffusion/VLA checkpoints не объявлены совместимыми.

## Нормализация без подмены единиц

Для внешнего ACT с сохранёнными `policy_preprocessor.json`/`policy_postprocessor.json` и state safetensors выполняется именно эта официальная пара. Для наших исторических ACT с Identity mapping используется официальная factory-пара плюс сохранённый явный `explorer_preprocessing.json` (ImageNet RGB и фиксированный native-range transform). Недостающая factory-пара сохраняется **в output**, оригинал checkpoint не меняется. Неизвестный normalizer, половина пары, несовпадающий checkpoint или неявные единицы отвергаются.

Физическая запись station содержит:

| SO101 joint | Native unit | Что ещё требуется для URDF |
|---|---|---|
| shoulder_pan | calibrated_normalized_-100_100 | знак, scale rad/native unit, нулевая поза offset rad, минимум две измеренные reference пары |
| shoulder_lift | calibrated_normalized_-100_100 | те же независимые reference measurements |
| elbow_flex | calibrated_normalized_-100_100 | те же независимые reference measurements |
| wrist_flex | calibrated_normalized_-100_100 | те же независимые reference measurements |
| wrist_roll | calibrated_normalized_-100_100 | те же независимые reference measurements |
| gripper | normalized_0_100 | отдельная измеренная апертура/target gripper mapping; пока не исполняется |

Проверенные bus IDs, homing offsets, min/max calibration ranges и SHA доказывают идентичность аппаратного профиля, но сами по себе не устанавливают знак/ноль/угловой диапазон URDF. Линейное растягивание[-100,100] на URDF limits запрещено данным контрактом. Нужны actual reference poses, привязанные к конкретному follower calibration SHA; root выполняет измерения отдельно, без изменения исходной калибровки.

Профиль JSON имеет `basis: measured_reference_pairs`, `source_urdf_sha256`, `source_calibration_sha256`, точные `joint_names/joint_units` шести измерений и `joints` для пяти arm names. Каждый joint: `scale_rad_per_native_unit`, `offset_rad`, `reference_pairs: [[native,rad], ...]`, `reference_uncertainty_rad>0`, `reference_evidence:{path,sha256}`. Reference evidence JSON должен содержать `kind:joint_radian_reference_measurements`, тот же `joint_name`, calibration/URDF SHA, `measurement_basis` (external_angle_measurement либо fixture_reference_pose), `observer`, те же `reference_pairs` и `uncertainty_rad`. Ссылка на произвольный calibration JSON недостаточна. Численные пары проверяются на согласованность и ненулевое различие; это контроль артефакта, а не проведение физических измерений самим ПО. `virtual_declared` допустим только для не-физического fixture и не делает физическую запись откалиброванной.

## Сохраняемые свидетельства

`policy-transfer-trace.json` содержит source episode/index/frame SHA, native state basis/clock, predicted native command, FK/IK и независимый simulator result. `policy-transfer-result.json` содержит SHA исходников, checkpoint/processors/dataset/mapping/training receipt, фактические версии и счётчики реально пройденных стадий. Оригиналы проверяются до/после; нормализация и URDF bounds не исправляются молча clipping. Отдельный lock предотвращает два worker одного задания. Незавершённые traces сохраняются в `interrupted_attempts`; исходные артефакты не перезаписываются.

Два фактически выполненных случая зафиксированы в `POLICY_RUNTIME_VALIDATION.json`: полный технический синтетический fixture с настоящими optimizer updates и non-Identity официальными processors; и inference настоящего stationary-hold checkpoint на физической записи, корректно остановленный до FK из-за отсутствующих измеренных reference mappings. Оба являются engineering evidence.

Для воспроизведения тяжёлого теста установить `EXPLORER_RUN_POLICY_TRANSFER_INTEGRATION=1`, указать проверенные meshes через существующую переменную и выполнить tests/test_policy_transfer_runtime.py в текущей среде с официальным LeRobot0.6.1. Никакие веса из сети не скачиваются.
