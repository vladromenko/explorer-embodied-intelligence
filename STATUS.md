# Explorer — фактическое состояние, 26.10.2026

Исходная задача **IN_PROGRESS**. Homebrain продолжает работу автономно;
Mac не нужен для runtime. Срок4–5часов снят в пользу качества и воспроизводимости.
Ни инфраструктура, ни успешный optimizer/save/reload не означают выполнение
всего мастер-промпта. H1–H5 и полные физические навыки не подтверждены.

Центральные Control Center и worker реально работают на `d176402`.
Панель: https://homebrain.tail4a7de4.ts.net/ через личный Tailscale;
в домашней сети доступен http://homebrain.local:8088.
Проверены вход, API, страницы04/06, `/evening`, printable ChArUco и настоящий
JPEG Nano. Перезапущены только две собственные центральные службы.

Каноническая нумерацияv2:01 автономное обучение;02 клавиатура/геймпад ROSMASTER;
03 SO101 leader→ROSMASTER;04 две SO101 — source learning;05 кооперация;
06 перенос source04→ROSMASTER. Семантический реестр общий для Python/API/браузера.
Рабочая SQLite мигрирована:24 задания,11 карточек; source04 имеет6 заданий,
transfer06 —2. Исходные event/card bodies и config/result каждого старого
задания побайтно совпали со снимком до миграции. Старые SHA и Spec CAS сохранены;
текущие представления связаны проверенным индексом. Прежние каталоги — только
совместимые ссылки. Подробности: [нумерация](research/audits/EXPERIMENT_NUMBERING_V2_RU.md).

Общий регрессионный прогон закреплённого `d176402`:845passed,15skipped;
выделенная полная native startup-фабрика:4passed с внешними драйверами-фикстурами.
Браузерный граф и lifecycle:58 Node-проверок, включая отсутствие автоматического
управления, поздние ответы/STOP и точный pinned native-кадр. Это не аппаратная
проверка геймпада. Свидетельство: `_registry/evidence/canonical-central-cutover-20261026.json`.

Настоящий путь API→durable scheduler→закреплённый CPU worker Diffusion выполнен
на `bde96e9`:8updates, официальный Dataset/native6, save/reload error0, карточка
SIMULATION_ONLY. Все исходные синтетические оригиналы неизменны, sealed payload
не использован. Текущий CPU профиль отдельно прошёл4+restart4 с точными runtime/
RECORD/source SHA. Это проверка ПО; development error слабый, навык не освоен.
ACT9 CUDA на общем графе:12tests,8updates,loss0.9924→0.3135,reload0, native replay
точный; независимая попытка0/1. Binding для фактически развёрнутого `d176402`
опубликован в immutable registry после совпадения14 source SHA и библиотек.
Эта квалификация не распространяется на106D fused ROSMASTER или source6 CUDA.

Pi observer пока `c1f9f35`, Nano observer пока `7a55fd1`; новая удалённая
интеграция разворачивается следующим этапом. Native same-owner ROSMASTER API
подготовлен и проверен по полной исходной Python factory, но настоящий
оригинальный web owner ещё не перезапускался. `/evening` честно показывает это
как программную работу Homebrain. Исходные MCU/камера/контроллер не изменены.
SSH-ключ Homebrain работает на Pi и Nano; повторный ssh-copy-id не нужен.

Обе руки и шасси агент самостоятельно не двигает. Разрешение выдаёт пользователь
только для отдельного текущего наблюдаемого теста. По умолчанию шасси закрыто.
Все6 исходных конфигураций ROSMASTER повторно проверены: SHA совпадают.
Firmware, limits, watchdog и отключённый servo readback не меняются. Target
feedback остаётся null/masks; command-estimate не выдаётся за измеренное
достижение/контакт. Поздний native telemetry fault действительно требует нового
наблюдаемого reference; агент не выполняет HOME и не стирает fault.

Полная копия на SamsungT5 действительно восстановлена отдельно:11812 файлов,
8,483,365,831байт, все SHA проверены; SQLite integrityok. Snapshot и старый NVMe
state сохранены. T5 ext4 постоянно смонтирован в `/mnt/explorer-data` по прежнему
явному разрешению владельца для точного serial. Полный аппаратный reboot ещё
не проверялся. Новые квалификации лежат отдельно от production-state.

Из прежних результатов: CPU ACT1000 сохраняет модель, но полная задача0/3;
исправленный reach ACT1/6; source→target scripted SIM перенос v7 сохраняет
захват/подъём/перенос, но полный итог0/1 — не expert. Технический source hold57
кадров не является предметным навыком. Реальный Pi Hailo segmentation runtime
и event VLM исполнялись; VLM-текст не подтверждает координаты или удержание,
измеренной tabletop calibration пока нет.102 полных текста проверены,35 изучены
углублённо; научные гипотезы остаются непроверенными.

Открыта работа: удалённые native/UI bindings, CharUco web wizard, source04
strict aggregate admission и native6 shadow, полный05 workflow, source-bound
transfer06 dataset/student/eval, общий fair scheduler и данные/экспорт,
предметные политики и независимые научные кампании. Физические шаги вынесены в
[веб-чеклист](EVENING_CHECKLIST_RU.md), программные блокеры не перекладываются на
пользователя. Завершение всей задачи не объявлялось.
