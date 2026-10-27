# Source04: профиль процесса и разрешение показа

Переиспользованы существующие `NodeAgent`, `StationObservedPermission`,
`StationPublicationGate`, original `SO101Station` и HTTP permission facade.
Новый `SourceOnlyRetarget` — тонкий адаптер отсутствующего03 runtime;
он не создаёт физических владельцев и не выполняет peer I/O.

Явный параметр `--pi-runtime-profile so101_source_only` оставляет SO-101 station,
одну Pi Logitech и их cache у прежнего единственного владельца. Новый
`PiNativeRetargetService`/`NativeStreamClient` не конструируется, private peer config
не читается, leader03 worker не запускается. Все direct03 POST, включая старые
activation/calibration/STOP routes, отклоняются409 до обращения к peer. Status
показывает enabled=false, `SOURCE04_ONLY` и причину. Старые03 архивы доступны
read-only через прежний reader. Disabled cleanup не выдаётся за доставленный
native STOP. Отдельный явный global Station STOP сохраняет исходную семантику.
Default profile не изменён; на реальном Pi этот новый профиль ещё не установлен.

Общий provider принимает новый purpose `source04_demo`: confirmed skill/version/
specSHA, `input_mode=leader`, `source_domain=PHYSICAL`, отдельные server-owned
`source_session_id` и `demonstration_id`. Поля model job/checkpoint отклоняются:
показ человеком не требует фиктивной модели. Challenge/authorization остаются
привязаны к exact displayed JPEG, actor, boot, follower calibration/native epoch;
для этого purpose добавлены verified leader calibration и fresh existing cache
на acquire. Нормализация и homing не изменяются.

Это разрешение допускает только прежние teleop/start, teleop/recenter и exact
scoped STOP. Policy/start/action не становятся доступными через demo purpose.
При смене leader calibration cache gate блокирует старую publication и использует
исходный measured hold. Stable ACTIVE сохраняет прежние target values, частоту,
ограничения и математику baseline_follower + leader − baseline_leader.
Другой demo ID или старый recenter epoch не могут очистить нового владельца.

6runtime/default tests PASS/1.34s и53demo/common eval/cooperative tests PASS/1.57s.
Тесты используют localhost и исходный Station worker с fake buses/camera;
реального USER grant или моторной команды устройствам не было.
[Evidence](../../_registry/evidence/experiment04-source-profile-demo-contract-20261027.json).

Это ещё не полный04 pipeline. Старый4c teardown/drain, typed capture API,
approval-only transfer, Dataset pause segments, UI и выпуск остаются открытыми.
Исходный Station файл сохранён с SHA2d191534…a885. Real Pi restart не выполнен.
