# Запись: разбор отказов и проверка 7 ноября 2026

Физический запуск рук выполняет оператор. Приведённые ниже прогоны используют отдельный тестовый источник, повторно читаемые старые RGB-файлы и настоящий локальный MLflow; они не доказывают исправность USB или качество телеоперации под физической нагрузкой.

## Подтверждённая причина предыдущего обрыва

В `cube in the box` source остановился после `Raw writer queue overflow`. PNG записывались последовательно: около35ms на изображение вместе с fsync, около70ms на два ракурса при бюджете33ms. Это вызвало защитное закрытие источника; программный журнал не подтверждает USB disconnect. Оба неполных эпизода оставлены в quarantine исходного датасета.

Теперь два независимых потока сохраняют lossless PNG0 через OpenCV. RGB→BGR перед кодированием проверен побитным сравнением декодированных картинок. Очередь ограничена64, её заполнение проверяется до следующего control sample. Ошибка сохранения прекращает движения и оставляет неполный эпизод в архиве. Диск проверяется до запуска и каждую секунду записи. PNG0 требует примерно3,3GB/мин для двух640×480 ракурсов; свободное место показывается в UI.

## Известные проблемы аналогичных систем и наши меры

| Ошибка/источник | Решение в лаборатории | Доказательство и предел |
|---|---|---|
| Две камеры отдельно работают, вместе зависают; повторяется старый кадр. [LeRobot #1083](https://github.com/huggingface/lerobot/issues/1083), [#1743](https://github.com/huggingface/lerobot/issues/1743) | Один reader на устройство; preview передаёт его записи без повторного открытия. Проверяются фактические format/FPS, sequence, возраст и ошибки. Stale кадр не считается свежим. Controls не переписываются. | Тесты borrowed-reader/ошибок; прошлый аппаратный preview. Новая непрерывная физическая запись после исправления требует оператора. |
| Номера videoN меняются после USB переподключения. [LeRobot Cameras](https://huggingface.co/docs/lerobot/cameras) | Открытие и инвентарь используют устойчивые `/dev/v4l/by-id`; UI читает владельцев из того же инвентаря. | Регрессия с video42/video91; без открытия устройств. |
| Гонка закрытия camera reader. [LeRobot #3569](https://github.com/huggingface/lerobot/issues/3569) | Event не обнуляется; проверяется живой поток; при незавершённом закрытии camera lease удерживается, повторное открытие блокируется. | Тесты preview/cleanup. Повторять движение при ошибке запрещено. |
| Смешение границ эпизодов/хвостов и timestamps. [LeRobot #933](https://github.com/huggingface/lerobot/issues/933) | Монотонные ns сохраняются отдельно от nominal FPS; явные границы эпизодов; atomically finalized episodes и quarantine раздельно. Экспорт проверяет hash/изображения/время и открывается официальным loader. | 3572+1796 кадров точного readback; повреждённые/будущие timestamps и смешанный FPS отвергаются тестами. Не «лечим» время подменой на frame_id/FPS. |
| Тяжёлые задачи в процессе web/control блокируют запись. [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) | GPU SAM/обучение и доставка MLflow работают отдельными процессами. Raw writer — отдельные ограниченные потоки. Browser не владеет записью. | Chromium/WebKit disconnect/reload; минутный raw+настоящий GPU SAM; максимальная очередь1. |
| MLflow API/артефакты зависают или идут не в тот artifact store. [MLflow Tracking Server](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/) | Собственный tracking/DB/artifacts; небольшой локальный fsync spool; доставка вне servo процесса; raw не грузится в MLflow. Run status, метрики и summary проверяются чтением обратно. | Outage→record complete→same URI restore→metrics/artifact readback; реальный RUNNING и метрики прочитаны во время записи. |
| Имена датасетов совпадают и затирают прошлую запись. [NVIDIA SO101 troubleshooting](https://docs.nvidia.com/learning/physical-ai/sim-to-real-so-101/latest/troubleshooting.html) | Пользовательское имя отделено от UUID папки; re-record переносит старый эпизод в архив. Удаление для устранения ошибки не используется. | Шесть сессий/три re-record подряд; архивы не выбираются train. |
| SAM маска от старого кадра рисуется как текущий live | SAM preview использует hardlink точного сохранённого raw, его timestamp/hash; отдельный снимок с возрастом. Полный sidecar для обучения создаётся отдельной задачей после сессии. | Настоящий SAM3 CUDA,3 выборочных снимка во время60s записи. Это не30FPS segmentation; отсутствующие полные аннотации блокируют object-mode train. |
| Train/test смешаны по кадрам, не совпадает preprocessing, sensor labels выдуманы | Разбиение целыми эпизодами/сессиями; stats только train; baseline/object input имеют отдельный контракт и validity; checkpoint содержит processors/adapter/hash/optimizer state. | Полный ранее проверенный ACT/SmolVLA save/reload и текущие регрессии split/features/causality. Новая успешная политика пока не обучена. |

Это сопоставление конкретных классов отказов, а не утверждение, что каждый upstream issue относится к установленному LeRobot0.6.1. Поведение проверено локально; чужие калибровки, environments и процессы не менялись.

## Повторные прогоны

Машинные результаты: `validation/recording-fix-20261107/`.

- `load-test.json`:120s file replay,3572 кадра,2 эпизода, max queue29, официальный loader и точные RGB/state/action/task/timestamps.
- `concurrent-sam-load-test.json`: повтор с последней реализацией,60s file replay вместе с настоящим SAM3 CUDA;1796 кадров,2 эпизода, max queue1,3 SAM-снимка. Все1796 кадров экспортированы и открыты официальным loader, RGB побитно совпал.
- `repeat-and-live-mlflow.json`:6 последовательных сессий,3 re-record,8 гонок Start/STOP. Метрика recording/frames_written прочитана при активной записи и RUNNING; после Stop тот же run FINISHED.
- `mlflow-outage.json`:7 событий буферизованы без tracking server, затем доставлены; metric history и summary artifact перечитаны, run FINISHED.
- `crash-recovery.json`: SIGKILL только собственного тестового дочернего сервера;30 кадров доступны в quarantine; после рестарта idle, MLflow FAILED, автоматического движения нет.
- `browser-workflow.json`: Chromium/WebKit,15s prep,Space/R/Esc,2 complete+1archive, запись переживает закрытие вкладки. Это synthetic source, не аппаратный тест.

Операторская проверка остаётся обязательной: направления/скорость всех суставов, стабильность USB под движением, gripper, End/STOP и1 новый контрольный эпизод. Гарантировать отсутствие любых будущих аппаратных сбоев программными тестами нельзя.
