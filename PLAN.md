# Открытый план Explorer

Исходная цель и ограничения: [GOALS](GOALS.md). Факты: [STATUS](STATUS.md).
Единственная матрица приёмки01–06, gates и проценты SOFTWARE/PHYSICAL:
[_registry/acceptance.json](_registry/acceptance.json).

**CURRENT PROGRAMMATIC PRIORITY=03 → NEXT=01 → 04 → 05 → 06.**
02A+02B scoped release722755dc развёрнут и независимо доступная программная
работа выполнена. Строгие real expert dataset/model/live-shadow gates02 остаются
OPEN до наблюдаемой подготовки владельца; они не заменены technical17frame case.
Продолжаем03, сохраняя эти незакрытые критерии и физический запрет.
04/05/06 — PARKED: сохранять существующие изменения и evidence; не расширять
их до программной готовности02. Общие исправления текущего02/03 допустимы.
Последнее указание пользователя заменяет прежнюю очередь01→02.

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

## 01, затем PARKED04–06

После02/03 добиться одного канонического простого curriculum skill на
достоверной ROSMASTER MuJoCo модели: PPO teacher→пригодные demos/DAgger→
компактный ACT/LeRobot student. До новой оценки объявить threshold полной
closed-loop задачи, independent DEV/SEALED trials и правило reload reproduction.
Нынешний TARGET0/16 — NOT READY; локализовать geometry/contact/reward/obs/
action/data причину и исправить целевой путь. UI01 choose/start/workers/
progress/pause/stop/evaluate/prepare-deploy обязан отражать настоящий backend.
Физический deploy — только подготовка/preflight до отдельного теста владельца.

Затем04→05→06 по numbering v2 и незакрытым gates канонической матрицы;
отрицательные результаты, научные оригиналы и подготовленные ветки сохранены.
Полные transfer/retention/H1–H5/recovery критерии не сняты.

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
