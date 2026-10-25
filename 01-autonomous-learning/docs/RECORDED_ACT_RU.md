# Обучение ACT на явно выбранных показах

`explorer.simulation.run_train_act(store, job, config)` — отдельный путь от
симуляционного pilot. Обязателен `job.config.dataset_manifest`: manifest
синхронных RGB/state/action эпизодов. Без него функция сообщает, какие данные
нужны; симуляция автоматически не запускается. Видео без action labels
остаётся наблюдением камеры и не выдаётся за демонстрацию управления.

Используется тот же официальный `ACTPolicy` 0.6.1, с размером state/action
**6 или 9** из данных, соответствующими names/units и явной нормализацией.
Actor получает только RGB и заявленное состояние: `measured_state` либо
`command_estimate`. Веса, optimizer state, preprocessing и original source
hashes сохраняются. Official save/reload проверяет равенство предсказаний.
Ни MuJoCo environment, ни робот не исполняются этим job.

Train/development выбираются целыми исходными эпизодами. `sealed_test` и
`test` не участвуют ни в optimizer, ни в offline metrics; проверяются только
их неизменяемые hashes. Повторное использование исходного эпизода/hash или
камерного frame ID между splits отклоняется. Не подменяйте независимую
оценку случайным разбиением соседних кадров одной записи.

Если development episodes есть, вычисляется L1 по action chunks и MAE каждого
действия в его исходных units с учётом padding. Если их нет, evaluation
честно недоступен. Эти ошибки — **offline diagnostics**, не доказательство
успешного rollout или удержания предмета. SkillCard получает статус
`trained_offline_only`; физических run IDs нет. Исходный NPZ-корпус не
переписывается и не копируется без ограничений; loader кеширует один эпизод.

## Чистый адаптер станции Pi

`explorer_sim.station_adapter.convert_station_episode(source_manifest_path,
target_dir)` преобразует уже записанный эпизод в валидированный NPZ manifest.
Функция читает существующие JPEG/PNG и JSON; не открывает serial, камеру,
сетевые соединения и не меняет калибровки или настройки контроллера.

Исходный station manifest:

```json
{
  "schema_version": 1,
  "episode_id": "unique-episode-id",
  "source_type": "station_demonstration",
  "state_basis": "measured_state",
  "action_basis": "dispatched_command",
  "action_names": ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"],
  "action_units": ["calibrated_normalized_-100_100", "calibrated_normalized_-100_100", "calibrated_normalized_-100_100", "calibrated_normalized_-100_100", "calibrated_normalized_-100_100", "normalized_0_100"],
  "normalization": {"low": [-100, -100, -100, -100, -100, 0], "high": [100, 100, 100, 100, 100, 100]},
  "fps": 10,
  "split": "train",
  "samples": [{
    "frame": {"file": "frames/000000.jpg", "sha256": "actual-source-file-sha256", "frame_id": "actual-camera-frame-id", "monotonic_ns": 1000000000, "clock_domain": "pi:actual-boot-id"},
    "state": [0, 0, 0, 0, 0, 50],
    "action": [1, 0, 0, 0, 0, 50],
    "state_monotonic_ns": 1010000000,
    "action_monotonic_ns": 1020000000,
    "clock_domain": "pi:actual-boot-id"
  }]
}
```

Пример описывает структуру, а не реальные измерения. SHA, boot ID и значения
должны приходить от действующего recorder. Поля с calibration metadata
переносятся в provenance, их наличие само по себе не означает аппаратной
проверки совместимости.

Адаптер проверяет hashes изображений, dimensions/names/units, диапазоны
нормализации, один boot-clock и причинный порядок
`frame receive ≤ state read ≤ accepted/dispatch`. `operator_input` и
`requested_action` не подменяют принятые/отправленные действия. Для реального
измеренного состояния **не** требуется равенство `state[t+1] == action[t]`:
сервопривод имеет динамику и задержку. Native state/action и все три исходных
monotonic timestamps сохраняются отдельно; физический успех не выводится.

RGB-массив при конвертации собирается через временный memory map. Длинный
эпизод не требует держать все исходные изображения в RAM одновременно.
NPZ и manifest имеют hashes; источники остаются нетронутыми.

`export_lerobot_v3()` затем создаёт настоящий официальный image-only v3
dataset и читает его заново. Помимо стандартного nominal-FPS timestamp
сохраняются отдельные int64 native frame/state/action timestamps. Не
называйте эти временные метки временем аппаратной экспозиции или фактического
применения момента. Нерегулярный decision stream требует явно описанного
resampling; адаптер не переставляет кадры и не выдумывает пропуски.

Старый Nano `explorer_mobile_episode_v3` пока имеет иной контракт; этот
адаптер его не переименовывает и не предполагает измеренную truth из команд.
