# SO-101 AI Learning Lab

Независимая локальная лаборатория для записи демонстраций SO-101, разметки,
обучения ACT/SmolVLA и запуска обученной политики на роботе. Приложение работает
на Homebrain (Jetson), не открывает камеры или шины моторов при старте сервера и
не использует LLM в цикле управления.

Актуальный снимок проекта и история проблем описаны в
[docs/HOMEBRAIN_FINAL_STATE_RU.md](docs/HOMEBRAIN_FINAL_STATE_RU.md). Краткий
хронологический журнал сохранён в [STATUS.md](STATUS.md).

## Текущее состояние

- Веб-интерфейс: `http://192.168.0.168:8020` по LAN или
  `http://100.95.83.3:8020` через Tailscale.
- MLflow: те же адреса, порт `5020`.
- Запись: две камеры либо только wrist-камера, calibrated leader → follower,
  lossless PNG и фактические состояния/команды суставов.
- Датасет: 50 реальных wrist-only эпизодов задачи «синий кубик → белая коробка».
- Обучение: ACT и SmolVLA, независимые train/validation/test-сессии,
  checkpoints, resume и локальные метрики MLflow.
- Запуск: предварительная загрузка checkpoint без движения и отдельный явный
  физический Start через штатный LeRobot rollout.
- Физический итог: rollout выполнялся и возвращал руку в исходную область, но
  успешный автономный захват и укладка обученной политикой не подтверждены.
  Завершение процесса с кодом `0` означает только штатное окончание программы.

Raw-данные, checkpoints, MLflow DB и логи остаются только на Homebrain и не
входят в Git.

## Что реализовано

### Запись и управление

- Предпросмотр камер не включает torque.
- Телеоперация и запись используют сохранённые калибровки leader/follower.
- Камеры открываются одним владельцем и переиспользуются preview/recording.
- Wrist-only режим не записывает и не показывает фиктивную обзорную камеру.
- STOP прекращает команды, а незавершённый эпизод переносит в quarantine. В
  train попадает только явно сохранённый эпизод.
- Сессию можно продолжить до сохранённого целевого числа эпизодов.
- Удаление датасета, повтор эпизода, архивный просмотр и проверка raw доступны в
  интерфейсе.

### Данные и разметка

- Каждый кадр содержит монотонные timestamps, актуальное состояние робота,
  реально отправленную action-команду и хэши файлов.
- PNG пишутся асинхронно с ограниченной очередью; полная синхронизация диска
  выполняется при завершении эпизода, а не в 30 FPS контуре.
- SAM3 работает как фоновая разметка сохранённых кадров и не находится в
  realtime-контуре моторов.
- Маски, категории, цвета, причинные track IDs и provenance привязаны к точному
  исходному кадру.
- Есть валидация raw и официальный экспорт, читаемый LeRobot 0.6.1.

### Обучение и запуск

- Политики ACT и SmolVLA.
- Разделение train/validation/test целыми сессиями, чтобы соседние эпизоды одной
  записи не утекали в разные выборки.
- Настраиваемые steps, batch size, LR, scheduler, AMP, gradient accumulation,
  интервалы validation/checkpoint и число сохраняемых checkpoints.
- Проверка памяти, диска и владения GPU/устройствами до запуска задачи.
- Worker-процессы не зависят от вкладки; MLflow имеет durable spool и replay.
- Для ACT зарегистрирован тот же centered letterbox 256×256, который использован
  при обучении; скрипт исправляет старые checkpoints с неполным preprocessor.
- Перед официальным rollout стартовая конфигурация выводится из train-сессий.
- Диагностический plugin пишет action/state и редкие wrist-кадры, не меняя
  командный путь LeRobot.

## Структура

```text
src/so101_lab/
  app.py                 FastAPI, REST/WebSocket, координация ресурсов
  hardware.py            камеры, leader/follower, калибровки и шины
  preview.py             camera-only preview
  teleoperation.py       отдельное calibrated following
  recording.py           датасеты, эпизоды, writer, recovery/quarantine
  annotations.py         SAM3 и валидация разметки
  lerobot_export.py      проверка и экспорт в официальный формат LeRobot
  training_options.py    параметры и session-level split
  training.py            ACT/SmolVLA, checkpoint/resume/evaluation
  inference.py           проверка checkpoint и физический rollout
  policies.py            конфигурация моделей и centered letterbox
  jobs.py/resources.py   фоновые задачи и leases ресурсов
  telemetry.py           MLflow со spool/replay
  lerobot_plugin/        регистрация processor и rollout trace
static/                   интерфейс Studio
config/systemd/           user services UI, MLflow и replay timer
scripts/                  запуск служб, обслуживание и проверки
tests/                    Python и DOM-тесты без открытия аппаратуры
```

## Службы

```bash
cd /home/jetsonnx/Projects/so101_ai_learning
./scripts/start_services.sh
curl -fsS http://127.0.0.1:8020/api/status
curl -fsS http://127.0.0.1:5020/
```

Для диагностики:

```bash
systemctl --user status so101-ai-learning-ui so101-ai-learning-mlflow
journalctl --user -u so101-ai-learning-ui -n 100
```

Сервер стартует без подключения к моторам. Камеры, torque, запись и политика
включаются только отдельными действиями пользователя в UI.

## Проверка кода без робота

```bash
cd /home/jetsonnx/Projects/so101_ai_learning
PYTHONPATH=src .venv/bin/pytest -q
npm test
node --check static/app.js
```

Эти команды не должны открывать USB-камеры и motor bus. Физическую проверку
выполняют только из интерфейса при освобождённых устройствах.

## Локальные артефакты

```text
data/ds_<id>/              raw episodes, quarantine, annotations, exports
models/<job-id>/           checkpoints, split, lineage, result
runtime/jobs/              состояние фоновых задач
runtime/mlflow.db          локальная база MLflow
runtime/mlflow-spool/      недоставленные метрики
data/mlflow-artifacts/     MLflow artifacts
logs/                      worker и rollout logs/traces
```

Эти каталоги исключены через `.gitignore`. Для переноса работающей модели нужно
копировать всю папку checkpoint, а не только файл весов.

## Ограничения

- Текущий wrist-only датасет покрывает одну сцену и одну формулировку задачи;
  он недостаточен для универсальных команд, сортировки и новых предметов.
- Коробка не видна wrist-камере в начале части эпизодов и появляется после
  поворота руки. Явная метрическая геометрия цели в baseline не подаётся.
- SAM3 полезен для контроля данных и object features, но сам по себе не
  гарантирует захват и не исправляет недостаточное разнообразие демонстраций.
- В физических rollout наблюдались промахи, дрожание/запаздывание и периодические
  провалы частоты цикла. Следующий этап — сопоставить wrist-наблюдение, action
  trace и train-распределение в момент промаха.
- Геометрическая автономная система Wristlab и этот imitation-learning проект —
  разные репозитории и разные исполнительные контуры.

## Дополнительные материалы

- [Финальное состояние Homebrain](docs/HOMEBRAIN_FINAL_STATE_RU.md)
- [Настройки обучения](docs/TRAINING_CONTROLS.md)
- [Инструкция записи](RECORDING_GUIDE.md)
- [Надёжность записи](RECORDING_RELIABILITY_REVIEW.md)
- [Контракт данных](docs/CONTRACT.md)
- [Экспорт LeRobot](docs/LEROBOT_EXPORT.md)
- [Разметка](docs/ANNOTATIONS.md)
