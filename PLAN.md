# Открытый план Explorer

Исходная цель и ограничения: [GOALS](GOALS.md). Факты: [STATUS](STATUS.md).
Единственная матрица приёмки01–06, gates и проценты SOFTWARE/PHYSICAL:
[_registry/acceptance.json](_registry/acceptance.json).

**CURRENT=03 → NEXT=04 → THEN=05→06→01.**
02A+02B scoped release722755dc развёрнут и независимо доступная программная
работа выполнена. Строгие real expert dataset/model/live-shadow gates02 остаются
OPEN до наблюдаемой подготовки владельца; они не заменены technical17frame case.
Продолжаем03, сохраняя эти незакрытые критерии и физический запрет.
Последнее окончательное указание пользователя: после безопасного checkpoint03
подготовить04 полностью software/operator-ready, затем05, затем06;01 PARKED
после текущего bounded audit. Новые долгие01 тренировки до04–06 не запускать.

## 02A: пригодная оператору запись

Первый результат — пользователь открывает `/experiments/02` с Mac через
Homebrain и самостоятельно собирает пригодные показы. После02A продолжить02B;
готовность записи не завершает весь02.

- Настоящие ROSMASTER камеры: состояние потока, показанный frame ID/timestamp,
  свежесть и понятная ошибка. Использовать существующий владелец камер.
- Авторизованное подключение к проверенному gateway Nano. Сохранить исправный
  controller/manual path и старую привычную keyboard-раскладку; на странице
  постоянно видна краткая таблица клавиш.
- Gamepad обязателен: обнаружение, калибровка осей/deadzone, раскладка, статус
  подключения и проверка входов без движения. Учесть HTTPS Gamepad API.
- Один manual flow arm+gripper+base с существующими STOP/deadman/focus-loss/
  network-loss. Проверить mapping/input preview/mock/shadow без движения.
- «Записать демонстрацию», skill/task name и описание; start/pause/cancel/
  finish; success/failure/reject/retry. Неудачи сохраняются отдельно от BC experts.
- Nano пишет рядом с камерами синхронные кадры/video, IDs/timestamps, operator
  input, requested/accepted/dispatched, command estimate, measured только если
  реально доступен, odom/доступные сенсоры, outcome и provenance. Сохранить
  clock domains и честную uncertainty, не подменять callback экспозицией.
- Проверка целостности; на NX отправлять только completed/approved episodes
  с resumable/hash verification, сохраняя оригинал. Проверить dataset round-trip,
  просмотр и скачивание записи.
- UI показывает число approved показов, покрытие условий и рекомендации
  следующего показа/ориентировочного дополнительного количества по качеству.
  Начать со smoke3–5; фиксированное число не обещает освоение навыка.

## 02B: законченный программный цикл

- Канонический Nano→NX LeRobotDataset→ACT train/resume→versioned checkpoint→
  save/reload. Единая очередь не конфликтует с другими тренировками.
- Offline evaluation, затем shadow на живых наблюдениях без dispatch движения.
- UI «Обучить», фактический прогресс/логи/ошибка; «Проверить», выбор версии,
  честные метрики и dataset/model/version/provenance.
- Один полный record→approve→transfer→dataset→train→reload→shadow→UI result
  с immutable evidence. Real episodes не заменять SIM/mock и не маркировать
  неподвижную техническую запись как успешную экспертную демонстрацию.
- Физическая supervised validation вынесена в точную checklist пользователя.
  Отсутствие человека не блокирует независимые программные исправления, но
  недостающие реальные demos/validation остаются явными OPEN gates.
- Полная адресная регрессия02, frozen полный прогон перед deploy, новый
  развёрнутый release. После deploy: краткая инструкция с проверенным URL —
  камера/gamepad→skill→первый episode→исход→STOP.

02 допускает статус SOFTWARE READY / PHYSICAL VALIDATION REQUIRED только
после всего программного объёма02A+02B. Одни зелёные mock tests этого не дают.

## 03 после программной готовности02

Переиспользовать pipeline02 без копирования. SO101 leader на Pi управляет рукой
ROSMASTER, шасси — keyboard через Nano; direct Pi→Nano, NX вне realtime loop.
Нужны ARM_ONLY и MOBILE_MANIPULATION operator workflows, clutch/recenter,
FK/IK retargeting, gripper calibration, latency/jitter. Сохранять исходную
калибровку и реальный open-loop/command-estimate ROSMASTER; не добавлять
недостоверный measured feedback, не перепрошивать контроллер.

Предоставить простое mirror leader→ROSMASTER без обучения и режим записи.
Actions — фактически принятые команды ROSMASTER; сохранять leader/retargeting,
камеры/base/odom. Тот же episode counter, инструкции, approve/train/reload/
shadow/UI, что02. Physical calibration/validation — точные шаги пользователя.
После полного программного03 — интеграционные проверки и release.

## 04 → 05 → 06 → отложенный01

Перед каждым пунктом — короткий reuse check. Общие camera/cache/capture lifecycle,
immutable archives/SHA/Range/recovery, time/splits, Dataset, Supervisor/ACT/DP,
model registry/reload/evaluation и UI имеют единую реализацию и project ownership.
Новые общие функции — incremental shared core + contract tests; отличия — тонкие
experiment adapters. Не копировать целые API/recorder/converter/trainer/UI и не
проводить массовый рефакторинг работающего02. Один physical camera owner + fanout.

04: реальная Pi station leader+follower и единственная Logitech; remote connect/
preflight/teleop/task/record/pause/cancel/review/approve/reject; native6+leader/RGB/
clocks/outcome/calibration→officialDataset→ACT resume→save/reload→shadow/replay UI.
Калибровка,3–5 smoke показов и continuity/pass-through перед физическим движением —
явная checklist. VLM/grid/click остаются отдельными проверяемыми capabilities.
Software-ready означает развёрнутый полный pipeline, ожидающий реальные данные;
не физический grasp/mastery.

05: reused02/04 recording/learning, manual/manual сначала; два существующих
camera views (Nano и PiLogitech), общая зона требует проверки. UI FSM, два робота,
STOP, skill/record/review, failure; два clocks/actions, handshake/grasp/release/
outcome→dataset/train/reload/shadow. FSM не называется обученным навыком.
Пользователь должен собрать и отправить показы без новых изменений кода;
реальный transfer/hold — отдельные physical gates.

06: только approved immutable source04/checkpoint с provenance; отдельные
camera/action/state/kinematics domains. FK/spatial/object-relative representations,
workspace registration→ROSMASTER IK/reachable preflight/SIMdryrun. UI source model,
target calibration и5/10/25target demos; zero-shot/adaptation/target-only comparisons,
metrics→save/reload/offline/shadow→physical checklist. Маркированный fixture/smoke
проверяет код до появления успешного source; transfer success этим не присваивается.

После04–06 вернуться к01 PPO→demos/DAgger→ACT, закрытому full-task threshold,
causal repair и UI. Существующие bounded probes/smoke сохраняются; новые долгие
01 продолжения пока запрещены. Sock/kitchen не заменяются reach curriculum.
Все старые transfer/retention/H1–H5/recovery критерии и отрицательные исходы сохранены.

## Организация и текущий checkpoint

Фактический scoped02B live: `722755dc99094fc86bab20796d2b7bc4bf56d536`
на central/worker/NanoOWN; Pi и оригинальный Nano web остаются4c5f4f5b.
2191PASS/18SKIP,231Node, actual17frame Nano analysis-only transfer,
passiveROS0 и прежние научные prefixes сохранены. Независимая работа03 продолжается.
Paper checkpoint `a5865cc` сохранён отдельно на work/temporal-numeric-admission;
полный offline rebuild/visual review остаются OPEN, submission_ready=false.

Продолжать текущий turn по последнему указанию пользователя, не завершать
его после этапа02. Рабочий режим High; root+максимум1 независимый субагент, effort повышать только для
конкретного трудного вопроса. Документ не меняет фактическую настройку модели.
Адресные тесты при правках, интеграционные в конце эксперимента, полный прогон
только перед deploy. Не повторять широкие04–06/2102-file аудиты без причины.
Подробные логи — в артефактах; в контексте только нужная текущему этапу сводка.

Шасси и руки агент не двигает; actual USER grants/HOME/native control через
agent tools запрещены. Исходные ROSMASTER configs/firmware/limits/watchdogs/
readbackOFF и Station.py остаются неизменными. Real GET и disarmed software
release допустимы; физические команды — только отдельный наблюдаемый тест.

## Канонический camera inventory владельца

Всего две физические камеры: одна ROSMASTER/Nano, одна Logitech Pi для LeRobot.
02 требует только подтверждённый Nano camera_rgb; depth того же устройства optional.
03 использует Nano как task-scene, Pi Logitech только optional leader/workfield.
04 использует единственную Logitech Pi.05 может использовать максимум эти два
ракурса после проверки совместной видимости; третьего общего ракурса нет.
06 сохраняет разные source/target domains, без заявления одинаковых viewpoints.
Никаких физических переподключений/перенаправлений или изменений настроек агентом.

03 имеет дополнительный обязательный gate до physical command-path deploy:
[continuity на реальных leader inputs](03-lerobot-teaching/docs/CONTINUITY_ACCEPTANCE_RU.md).
Fake/state-machine и реальные статические cache-чтения не закрывают changing-motion gate.
