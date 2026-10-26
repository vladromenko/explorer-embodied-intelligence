# Опыт06: данные LeRobot и различие replay / rollout

`explorer_sim.so101_dataset_qualification` — только офлайн-адаптер данных.
Он не импортирует классы роботов, не вызывает connect/send_action/replay CLI,
не обучает модель и не обращается к сети. Старые personal-данные не перемещаются
и не переписываются. Физическое движение любой руки требует отдельной наблюдаемой
проверки владельца; этот модуль такой проверки не выполняет.

`inspect_station_episode(path, task_type='joint_trajectory')` проверяет оригинальный
native manifest и точные JPEG. Общий контракт06:
`so101_follower_calibrated_position6_v1`, NAMES/UNITS/LOW/HIGH из прежнего
`explorer_shared.so101_station`: пять координат calibrated [-100,100], захват [0,100].
Состояние follower — отдельное измерение **перед** командой, не её достигнутый итог.
Requested action, command label и отдельно записанные accepted/dispatched/leader
показываются разными фактами; отсутствующие поля не восстанавливаются из движения
или номеров суставов. Старая запись удержания остаётся техническим материалом,
`object_expert_eligible=False`: независимый успех предметной задачи здесь не оценён.
Boot/session, camera identity/calibration и dispatch receipts отмечаются missing,
если их действительно нет в исходнике. SHA удостоверяет байты, а не физический успех.

`qualify_station_dataset(bindings, target, skill_id=..., skill_version=...,
task_type='joint_trajectory')` принимает явные accepted bindings `{manifest_path,
manifest_sha256, skill_id, skill_version, split, accepted}`. Нужны отдельные train
и development исходники. В один набор входят **все** принятые train/dev оригиналы,
их неизменные native metadata/JPEG и массивы штатного station_adapter. Sealed
сохраняется ссылками/хешами, его payload не попадает в export. Первоначальные split,
source IDs, frame IDs, RGB content SHA и известные session IDs не пересекают split;
разные калибровки рук не смешиваются. Служебный aggregate не доказывает физическую
независимость сессий, если исходная session ID отсутствовала.

Используются реальные официальные LeRobot0.6.1 `LeRobotDataset.create`, `add_frame`,
`save_episode`, `finalize`, затем официальный reader. Без Hub. Сравниваются все RGB
пиксели, native state/action, source clocks, causal conversion grid и boundaries;
source originals остаются byte-identical. Счётчики original episodes не заменяются
числом frames. Receipt содержит skill/version, aggregate SHA, source SHA, train/dev/
sealed IDs, `official_dataset_readback_verified`, missing full provenance, и явные
false для hardware/replay/learned rollout/object expert. Он не создаёт новый навык
в Store и не делает кнопку физического запуска готовой.

`replay_schedule(path)` только строит словари записанных команд с ключами
`shoulder_pan.pos` ... `gripper.pos` и исходными dispatch timestamps. Это команда
из записи; новый кадр не меняет её и обученная политика не участвует. Learned
rollout требует сохранённую модель, новые текущие RGB и measured follower state,
проверку текущих калибровок и отдельный наблюдаемый допуск. Ни replay, ни rollout
настоящих рук в квалификации не запускался.

## Существенная несовместимость стандартного hardware entry point

Проверен установленный primary source LeRobot0.6.1, его SHA включён в receipt:
`lerobot/robots/so_follower/config_so_follower.py` по умолчанию задаёт
`use_degrees=True`. Поэтому простое совпадение ключей `.pos` не делает наши
нормализованные координаты градусами. Для совместимого **программного** отображения
нужна явная семантика `use_degrees=False`; существующий executor сохраняется.

Кроме того, официальный `SOFollower.connect()` вызывает `configure()`. В исходнике
configure пишет Operating_Mode, P/I/D и Max_Torque_Limit. В условиях сохранения
исходных настроек этот hardware entry point не квалифицирован и не вызывался.
Официальный Dataset API и модель на Homebrain совместимы с использованием прежнего
native Pi executor, без подмены калибровок/настроек и без запуска robot.connect.

## Реальная программная проверка 2026-10-26

10 тестов `tests/test_so101_dataset_qualification.py` на Homebrain, около8 секунд:
4 синтетических native источника (2 train + development + sealed), 9 кадров/3 эпизода
официального image Dataset v3.0 реально записаны/finalize/readback; sealed payload
отсутствует. Проверены единицы `.pos`, requested ≠ dispatched labels, measured lag,
причинность, изменённые bytes/SHA, session/frame/content leakage, калибровки,
skill/version/split, дубликаты, запрет превращения legacy hold в object expert.
Первичные файлы и qualification.json находятся в
`/tmp/explorer-so101-dataset-qualified-20261026-v3/`; копия receipt рядом с этим
документом. Это software fixture, не новый физический показ и не обученный навык.
