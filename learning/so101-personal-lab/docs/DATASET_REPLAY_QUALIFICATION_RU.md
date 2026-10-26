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

## Аддитивная запись новых показов native Pi

`SO101Station` сохраняет прежние поля и schema_version=1. Только новые записи
получают `recording_provenance_version=2`: оригинальный boot, зафиксированные SHA
исходных motor calibrations, camera source ID/device и producer on-disk source SHA
(без заявления о доказанной runtime binary identity). Калибровки камеры в текущем
источнике нет: `camera_calibration=None`, availability=False; она не придумывается.

В `_sample` без новых bus reads копируются уже полученные в этом же цикле raw leader
и follower measurements. Leader имеет true mask только при точном совпадении общего
clock, measurement timestamp и follower snapshot. Отдельно сохраняются requested,
accepted и dispatched action. Dispatched true/host receipt выдаётся только при
совпадении уже существующего `last_command` с текущим control epoch, временем,
requested/accepted values и clock **после возврата прежнего sync_write**. Это host
write completion, без MCU ACK, motor application time или достижения цели. При
отсутствии такой связи dispatched=None/maskFalse; реальные нули остаются true.
Измерение follower относится к моменту **до текущего действия**. Следующая строка
содержит новое собственное измерение, а вымышленного next-state в старую строку нет.
Новые v2 записи с отсутствующим receipt не проходят даже технический допуск.

Record/start может передать `skill_id`+`skill_version` вместе, `skill_spec_sha256`,
`scene_id`, `session_id`, `phase_id`, `phase`, `task_type`, `context`. Это ограниченная
JSON-копия исходного запроса, не независимое доказательство успеха; context не может
менять настройки контроллеров. Центральный server adapter должен передать выбранную
подтверждённую версию явно. Старые сохранённые manifest/JPEG не дополняются задним числом.

13 дополнительных/существующих checks записи и native operations вместе с 10 checks
Dataset проходят в одном запуске. В source-recording tests любой вызов _write,
_positions, _connect, _enable_hold или camera.latest немедленно падает: сама запись
метаданных и завершение writer queue не выполняют ни одной такой операции. Прежние
write/connect/servo-loop методы и их частота не менялись. Развёртывание на Pi и
физический показ этим source commit не выполнялись.

Новые record/start принимают только явные split `train`, `development`, `sealed_test`;
значение сохраняется и в оригинальном manifest, и в source_context. Без split
сохраняется прежний train default. Inspector читает буквальные source-bound
skill/version/spec SHA, scene/session/phase; противоречащие top/nested значения
отклоняются, неизвестная историческая идентичность остаётся неизвестной.
Aggregate не меняет split и не присваивает другой skill/version уже размеченному
источнику. Отмеченные сцены и сессии также не пересекают train/heldout.
