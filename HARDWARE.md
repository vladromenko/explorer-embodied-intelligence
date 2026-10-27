# Узлы и аппаратные ограничения

| Узел | Роль | Фактические ограничения |
|---|---|---|
| Homebrain / Orin NX16GB | Control Center8088, SQLite, queue, CPU/GPU learners, MuJoCo, Research Hub | Общая CPU/GPU память, отдельные квалифицированные runtimes и бюджеты; Mac необязателен. |
| explorer-pi / Pi5 +Hailo10H | Две SO101 leader/follower, одна Logitech Pi для рабочего поля LeRobot, измерения/native6, локальный outbox | Одни исходные servo/camera owners, сохранённые калибровки. Текущее чтение station восстановлено: available=true/idle/configuration_writes0; physical calibration/validation отдельны. |
| explorer / Orin Nano8GB | Штатные ROS2/MCU/ManualArm/Ruckig, unified9D, одна бортовая Orbbec DaBai DCW2 (RGB и optional depth)/LiDAR/IMU/odom | Исходные6 configs/firmware/limits/watchdogs/readback OFF неизменны. Reference после telemetry fault невалиден; агент HOME не делает. |
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

## Подтверждённые камеры: ровно два физических потока системы

[Фактический inventory](_registry/camera-inventory-20261027.json),27октября2026:

| Физическая камера | Домен и применение | Проверенная identity / возможности |
|---|---|---|
| Одна камера ROSMASTER на Nano | Основной task-scene02/03, target-domain06; сторона ROSMASTER05 при проверенном ракурсе | Действующий оригинальный драйвер и USB подтвердили Orbbec DaBai DCW2, serial CH82C5200KB. RGB640×480; драйвер настроен15fps, JPEG preview≈5fps. USB2bc5:0561 RGB и2bc5:06a0 depth относятся к одному RGBD-устройству.17реальных RGB кадров и11валидных depth records сохранены. Depth optional; exposure timestamp не подтверждена. |
| Одна Logitech Raspberry Pi | Рабочее поле LeRobot04; явно именованный optional leader/workfield-ракурс03; source-domain06 | Марка и единственное физическое устройство подтверждены владельцем. Точная модель не угадывается: текущая SSH USB-проверка не завершилась из-за banner timeout. Исторический stream ID pi-overview обозначает эту же Logitech, а не отдельную обзорную камеру. |

02 полностью поддерживает один подтверждённый `camera_rgb` Nano; Logitech Pi
не входит в обязательные данные02 и не подменяет бортовой поток. RGB и depth
одного RGBD-устройства не считаются двумя физическими камерами. Generic multi-camera
schema сохраняется только как возможность расширения.

Для05 максимум два имеющихся ракурса: Nano и Logitech Pi. Покрытие общей зоны
и межкамерная синхронизация ещё не подтверждены; третий «общий обзор» не существует.
Для06 source/target camera domains различны, равенство viewpoints не заявляется.
Камеры агент не переподключает, не направляет и не меняет их настройки.
