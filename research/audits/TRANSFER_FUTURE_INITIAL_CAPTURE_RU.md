# Начальный snapshot только для будущих transfer trials

ENGINEERING_GLUE. `record_target_trial` принимает необязательный серверный
`initial_state_capture`. Конфигурацию создаёт
`initial_state_capture_config(source_bindings=..., scenario=...)` из
`explorer_shared.skill_transfer`. Открытие интерфейса ничего не захватывает.
Настройки hardware, исходный provider, env, evaluator и старые V9 не меняются.

Зарегистрирован один helper:
`explorer_manipulation_stationary_initial_v1` из `sim_initial_state.py`.
Конфигурация закрепляет SHA его исходника. Произвольный callback, другое имя
helper или изменённый SHA отклоняются до первого наблюдения и команды.
Сам helper проверяет действительный стационарный MuJoCo профиль, native ABI,
CAD/runtime, RNG и ограничения объёма. Неподдержанный профиль даёт сохранённую
неуспешную попытку с `CAPTURE_FAILED`; продолжения без capture нет.

Порядок будущей попытки:

1. `target.reset(seed)`.
2. Capture и fsynced save в новый `trial/initial-state/`.
3. Первое `target.observe`, затем планирование и `target.advance`.

Конфигурация копируется до reset. Snapshot scenario содержит фактический reset
seed, тип предметной задачи, общий Skill UUID, исходную и целевую версии,
SHA целевого SkillSpec, source manifest и identity целевого provider. Дополнительный
сценарий сохраняется отдельно как `declared_trial_scenario`. Противоречащий reset
seed запрещён. Source bindings автоматически включают helper, recorder и
проверенный source manifest; вызывающая сторона может добавить frozen protocol
или driver. Зарезервированные привязки подменить нельзя.

В `result.initial_state` сохраняются `RECORDED`, путь и SHA manifest,
относительная папка, число файлов, объём payload, полный payload SHA inventory
и привязки источников. MJB бывает около 153 MiB: его байты хранятся в файле,
в result попадают только ссылки и размеры. Если последующая геометрия или
диспетчеризация симуляции завершатся ошибкой, snapshot остаётся у той же
неуспешной попытки. При незавершённом save имеющийся manifest остаётся доступным
для диагностики с явной отметкой `artifact_retained_but_capture_unverified`.

Без опции будущий result явно сообщает `NOT_RECORDED / CAPTURE_NOT_REQUESTED`.
Прежние результаты не переписываются. `RECORDED` подтверждает сохранение
зарегистрированного snapshot, но не новую независимую restore qualification:
`restore_reexecution_verified=False`, исходный runtime/OS closure `PARTIAL`,
перезапуск RGB `UNQUALIFIED`. Для точного finite replay нужен отдельный новый PID
и реальная квалификация на закреплённом источнике. Физический, learned-source
или полный предметный успех из capture не следует.

CPU-light tests используют явные MOCK target/capture и настоящий immutable
bundle writer. Они проверяют порядок, source/SHA/scenario lineage, сохранность
при последующей неудаче, отказ до dispatch и неизменяемую конфигурацию. Здесь
не выделялись CAD/MJB, renderer, GPU, policy или реальные устройства.

Новые target trial также принимают отдельный `target_split` с partition и
episode/scene/session/group IDs. Отсутствующий target split остаётся unknown;
исходный source train не заполняет его. Optimizer допускается только при
одновременных source train и явно заданном target train. Development и sealed
test не становятся BC-targets; успешный trial с неизвестным target split также
остаётся вне target dataset admission. Исторические V9 results и их прежний
source-only флаг сохранены побайтно и не переинтерпретируются этим изменением.
