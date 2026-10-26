# 06 · Проверяемый перенос между SO-101 и ROSMASTER

Реальная реализация: `explorer.transfer.run_virtual_experiment` и общая геометрия `explorer_shared.kinematics`. Задание `virtual_transfer` рассчитывает FK официальной SO-101, переносит позы относительно предмета и решает ограниченный IK существующего URDF ROSMASTER. Конструкции имеют разные именованные цепи; захват не включается в фиктивное шестисуставное соответствие.

Привязка выражена явно:

`T_target_tool = T_target_object · inverse(T_source_object) · T_source_tool`.

По умолчанию используются **виртуально заданные** объектные системы координат около опорных поз. Для измеренной регистрации обязательны IDs калибровок. Самая первая тестовая траектория — детерминированная геометрическая проба, **не обученная SO-101 policy**. Конфигурация также принимает внешний путь `source_positions_rad` с обязательным `source_joint_names`.

Пилот по умолчанию ограничивает ошибку позиции в метрах; полная ориентация не объявляется совпадающей. `orientation_weight>0` включает ориентацию в цель IK. Недостижимые точки перечисляются отдельно, вместе с остатками. Коллизии, контакт и физическая успешность этим расчётом не проверяются.

Артефакты каждого запуска: `transfer_errors.csv`, `kinematic_trajectories.npz`, график рассчитанных X/Z-путей `frame.jpg`, `transfer_report.json`, SHA256. Прямое копирование joint-вектора сравнивается только как иллюстративный виртуальный baseline, без заявления превосходства над опубликованными методами. Безошибочный геометрический перенос не доказывает физический навык или гипотезу H2.

## Импорт LeLab / LeRobot

`inspect_lerobot_source(dataset_path, checkpoint_path, semantics)` читает исходные файлы без изменения: `meta/info.json`, checkpoint `config.json`, `model.safetensors`, `policy_preprocessor.json`, `policy_postprocessor.json`. Проверяются robot_type, action names/shape, fps, версия датасета, type policy, структура processors, объявленные единицы/порядок суставов и gripper semantics. Результат содержит хеши и причины несовместимости.

Совпадение шести измерений недостаточно. Даже полная проверка метаданных требует последующего исполнения processors соответствующей официальной версии LeRobot и rollout исходной policy; `extraction_ready` остаётся false до такого испытания. Action head SO-101 напрямую на ROSMASTER не переносится.

Проверенные первоисточники: SO-ARM100 `a758567c3978dfeefe282ede0500085a48fe8f78`, Apache-2.0; LeLab `9b5afaee33baa1d773e499937564f3831d25de22`, `pyproject.toml` требует Python ≥3.12 и LeRobot v0.6.0. Установленная версия может отличаться: совместимость исполнения не предполагается. Источники/хеши URDF находятся в `shared/robotics/so101/provenance.json`; visual/collision meshes здесь не копируются и для FK не требуются.

Все методы имеют статус KNOWN_BASELINE / ENGINEERING_GLUE: `SO101_URDF_FK_IK`, `OBJECT_RELATIVE_RETARGET`, `LELAB_CHECKPOINT_IMPORT`.

Каноническая роль — `cross_embodiment_transfer`, numberingv2. Источник обучается
в04; target ROSMASTER использует отдельный9D контракт. Новые target CAD traces
сохраняют настоящий target RGB, prior9/request/dispatch и независимые исходы;
source RGB не подставляется вместо target render. Последний полный scripted
SIM пилот сохранил grasp/lift/carry, но0полных успехов и0expert. Он не является
переносом освоенной source policy. Старые raw06/personal/04 metadata сохраняются
с исходной версией нумерации и проверяемой проекцией, без перезаписи SHA.
