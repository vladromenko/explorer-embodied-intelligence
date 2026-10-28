# Базовый пользовательский путь04 и переход03

Software/deploy scope: `MVP_USER_TESTABLE` /
`DEPLOYED_AWAITING_PHYSICAL_ACCEPTANCE`. Последний общий live release —
`c82e6754a26194f3fd12533cce35153890ff5771`; это не physical acceptance.
Пункты ниже основаны на сохранённых release/UI receipts, а не на новых движениях.

| № | Что доступно в04 | Evidence / оставшаяся физическая проверка |
|---|---|---|
|1|Статус leader/follower и существующих calibration в панели; один исходный Station|[Release](../_registry/evidence/experiment04-source-release-actual-ef3f5185-20261028.json). Сейчас питание рук OFF: real powered joint vectors/connection не подтверждены.|
|2|Изображение существующей Logitech в04, desktop/mobile|[Actual HTTPS/JPEG](../_registry/evidence/experiment04-source-release-actual-ef3f5185-20261028.json). Полная object/goal scene требует USER проверки.|
|3|Start/Recenter/STOP leader→follower через общий executor/permissions|[Software/UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json). Агент реальные команды не выполнял; наблюдаемый teleop тест OPEN.|
|4|Skill name/instructions/Spec и фазы в существующем workflow|[Whole learning UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json). Реальная task-specific демонстрация OPEN.|
|5|Record Start/Finish, RGB, leader/follower state/action/timestamps → official LeRobotDataset|[Original cohort/conversion](../_registry/evidence/experiment04-approved-capture-queued-training-20261027.json). Generated originals квалифицированы; real accepted experts0.|
|6|Список эпизодов, отдельный outcome/admission/review и счётчик пригодных показов|[UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json). UNKNOWN/failure не считаются успешными.|
|7|ACT button, очередь/progress/result, сохранение/reload|[Actual generated ACT8](../_registry/evidence/experiment04-approved-capture-queued-training-20261027.json), [UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json). Успех физического навыка не установлен.|
|8|Каталог моделей и версий|[Actual learning/model UI](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json). Production real expert model ещё отсутствует.|
|9|Выбор модели/replay/policy через существующий frozen executor; offline DEV/shadow|[UI/executor scope](../_registry/evidence/experiment04-approved-learning-actual-UI-20261027.json). USER observed physical policy trial OPEN.|
|10|HB/Pi deploy, read-only UI/camera smoke и отдельная очередь physical acceptance|[Actual release](../_registry/evidence/experiment04-source-release-actual-ef3f5185-20261028.json), [PHYSICAL_CHECKS](PHYSICAL_CHECKS.md). Нужны питание, сцена, наблюдаемые показы/проба.|

Известных blocking software defects04 в release evidence нет. Новые редкие
recovery/race/SHA/approval случаи и расширенный VLM/grid отложены в
[DEFERRED HARDENING](BACKLOG.md). Это не утверждение об отсутствии всех дефектов.

03 начат сразу после checkpoint04: `2026-10-28T01:01:26+03:00`, commit
`2347c6ccf58b5734ea9f66495dd4ec05fc5d2fd4`. Интегрированы existing direct03,
calibration/URDF bootstrap и UI; HB/Pi/Nano software deployed.
[Actual03 release](../_registry/evidence/experiment03-shared-lab-actual-release-20261028.json).
Physical03 остаётся gated по changing-input continuity, fresh MCU/reference и
видимости сцены. После03 также выпущен05; текущий software MVP blocker —06.
