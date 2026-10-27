# Обязательная continuity-проверка03 до развёртывания physical command path

Последнее указание владельца: admission и motion shaping разделены. Новые gates
не должны повторять прежнее дёрганье от программных ограничителей LeRobot.
До PASS этой проверки03 не SOFTWARE READY; новый физический command path
не развёртывается. Код и тесты можно готовить автономно, движения агенту запрещены.

Admission выполняется при acquire и смене режима; отказ запрещает сеанс целиком.
В устойчивом ACTIVE сохраняются прежние частота, значения, Native Ruckig и
параметры контроллеров. Новые clamp/filter/rate-limit/deadband, произвольные
ограничения скорости/суставов или обратная связь по сомнительным servo-значениям
не допускаются. Command estimate остаётся оценкой, firmware/configs/readback OFF
не меняются.

Stale, duplicate и out-of-order входы отбрасываются без подставленной нулевой
цели и без ACTIVE/CLUTCHED дребезга. Краткий jitter не создаёт command/zero/command.
Утрата действительного ownership/authorization или исходной identity — отказ
сеанса; это не способ модулировать разрешённую траекторию по кадрам. Прежняя
lease/watchdog семантика остаётся у исходного контроллера.

Clutch/recenter привязывает первую цель к текущему command-estimate и свежей
leader-позе после admission I/O. Абсолютный скачок к углам leader запрещён.
Для абсолютной калиброванной карты захвата предпочтительна отдельная admission
проверка выравнивания; нельзя молча добавлять offset/масштаб/клиппинг. Любой допуск
должен выводиться из реальной encoder/native quantization, без выдуманного epsilon.
Отпускание использует прежнее hold/stop; emergency STOP остаётся немедленным
в исходном пути, собственный oscillating ramp не добавляется.

## Что сохраняется в отчёте

Для неизменяемых реальных входов leader с SHA, clock domains, calibration IDs
и доказанной registration сравниваются поток до gateway и подготовленные
команды после gates. Записываются frequency, inter-command dt/jitter, RTT
как same-clock величина, discontinuities, max per-step delta, velocity,
acceleration, jerk, dropped/zero/substituted commands и переходы состояний.
Аппаратная latency и cross-clock alignment не выводятся из RTT.

Обязательные случаи: устойчивое удержание, delayed permission, один stale
пакет, короткий network jitter, повторный clutch, loss/reacquire, закрытие чужой
вкладки, неизменность траектории при устойчивой lease. В steady ACTIVE нет
чередования допуска, вставленных нулей, HOME или новых ограничителей.
Различия из-за retargeting и quantization описываются явно.

Generated fixtures проверяют state machine и математику. Реальное неподвижное
чтение cache отдельно проверяет transport/identity; оно не заменяет changing
leader trace. Отсутствие measured task-axis/gripper registration не блокирует
bootstrap-разработку: [профиль выводится из существующих источников](BOOTSTRAP_REGISTRATION_RU.md).
Физические направления и захват проверяются короткой отдельной приёмкой.
При отсутствии changing-input trace или действительного Native reference полный
continuity PASS остаётся OPEN. Никакой synthetic-успех не выдаётся
за реальное плавное движение.

## Следующее наблюдаемое действие владельца

Сначала неподвижное шасси, свободная зона и исходный emergency STOP. После
проверки питания/свежей MCU-телеметрии и регистрации выполнить **shadow без
motor dispatch**: одна малая, непрерывная амплитуда leader с полным журналом.
Только после PASS и отдельного текущего разрешения владельца — одна малая
непрерывная физическая проба, наблюдение отсутствия рывков, затем расширение.
Это checklist; агент не запускает её удалённо и не выдаёт grants/HOME.
