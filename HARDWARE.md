# Узлы и аппаратные ограничения

| Узел | Роль | Фактические ограничения |
|---|---|---|
| Homebrain / Orin NX16GB | Control Center8088, SQLite, queue, CPU/GPU learners, MuJoCo, Research Hub | Общая CPU/GPU память, отдельные квалифицированные runtimes и бюджеты; Mac необязателен. |
| explorer-pi / Pi5 +Hailo10H | Две SO101 leader/follower, общий обзор камеры, измерения/native6, локальный outbox | Одни исходные servo/camera owners, сохранённые калибровки. Последняя station ошибка sync-read; USB-адаптеры оба присутствуют. |
| explorer / Orin Nano8GB | Штатные ROS2/MCU/ManualArm/Ruckig, unified9D, RGB-D/Logitech/LiDAR/IMU/odom | Исходные6 configs/firmware/limits/watchdogs/readback OFF неизменны. Reference после telemetry fault невалиден; агент HOME не делает. |
| Mac | Браузер/необязательный launcher | Не является сервером, хранилищем единственных данных или обязательным worker. |
| Samsung T5 | Управляемые данные/артефакты/recovery в /mnt/explorer-data | Исходный NVMe rollback и проверенные копии сохраняются; очистка не производится автоматически. |

Версии/среды с датой измерения находятся в
[deploy/environments](deploy/environments). Назначения и проверки —
[OPERATIONS](deploy/OPERATIONS_RU.md). Исходные controller SHA —
[_registry/controller-config-baseline.json](_registry/controller-config-baseline.json),
SO101 calibration SHA —
[_registry/so101-calibration-manifest.json](_registry/so101-calibration-manifest.json).
Статус online не доказывает свежих измерений или физического навыка.
Секреты/ключи/private environment остаются вне Git и этой страницы.
