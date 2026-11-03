# Обучение: ACT / SmolVLA и выборки

В UI этап3 — общая форма перед отправкой. SmolVLA использует локальный cached pretrained. ACT использует установленную реализацию LeRobot ACT с ResNet18, без скачивания весов: обучение с нуля. ACT не получает язык задачи, текст остаётся в датасете. Обе политики поддерживают baseline RGB+state и тот же SAM state adapter (32 числа, categorical tracking ID исключены). Политика выбирается до запуска; checkpoint загружается по собственному `config.type`, а не текущему выбору в форме.

Перед запуском нажать «Проверить параметры и выборки». Сервер проверяет raw hash/timestamps/validity, SAM sidecars при включении SAM, независимые группы и параметры. Невалидные параметры не ставятся в очередь. Synthetic данные обычным train отвергаются. Наличие только кнопки ACT не является проверкой: проверен настоящий ACT forward/backward/save/reload/resume на CPU с тестовым источником.

## Train / validation / test

- По долям: seed, validation%, test%; train — остаток. Доли округляются до целых групп с непустыми train/validation и test при test%>0. Предпросмотр показывает фактический состав и число кадров, а не обещает точные проценты на двух эпизодах.
- По умолчанию разделение целыми сессиями, когда их несколько; одну сессию можно делить целыми эпизодами, с предупреждением о сходстве. Явный режим «Только сессии» не допускает дробления одной сессии.
- Вручную: включить нужные эпизоды и назначить train/validation/test. Повторение эпизода в разных выборках запрещено. Отключённые эпизоды не участвуют. Нужно минимум два эпизода/группы для train+validation; три для отдельной test.
- Normalization joints/actions вычисляется только на train. SAM validity/box/score/target остаются в исходных 0..1. Весь validation оценивается через N optimizer steps; весь test — один раз после завершения обучения. Test не выбирает checkpoint и не обновляет веса. Offline loss не доказывает успешность захвата.
- `split.json` и hash состава сохраняются в модели и MLflow. При изменении выбранных данных после отправки задача требует повторной проверки. Resume не меняет split и схему.

## Параметры

Steps — число optimizer updates (при resume итоговый шаг, больше шага checkpoint). Batch size1..16; gradient accumulation1..64 увеличивает число примеров на update. Adam/AdamW, LR, weight decay, grad clipping, cosine/constant scheduler, warmup. Метрики, validation и checkpoint имеют независимые интервалы. Последний checkpoint сохраняется всегда. Итоговая папка содержит policy config/weights/processors, adapter/lineage, optimizer/scheduler/GradScaler/RNG для resume.

AMP доступен только CUDA, технические CPU-тесты AMP не подтверждают. DataLoader workers0..2 (0 — безопаснее по памяти Jetson), prefetch1, spawn. Количество потоков и batch size могут превысить память: resource guard и ошибки отображаются в job, универсальное отсутствие OOM обещать нельзя. Лёгкая аугментация яркости используется только на train. ACT: KL weight и отдельный LR backbone. SmolVLA: denoising steps. Общие image_size, chunk_size, target category/color. Raw RGB остаются неизменными. Для ACT применяется общий train/inference RGB letterbox; SmolVLA сохраняет штатный resize.

Локальные параметры формы сохраняются браузером; фактические параметры задачи сохраняются сервером в job.json/lineage/MLflow и не зависят от вкладки. Восстановление активного job второй копией запрещено. Для остановленного train кнопка Resume берёт последний полный checkpoint. Чтобы продолжить уже завершённый train, указать папку checkpoint в форме и увеличить Steps. Физические движения не resume автоматически.

Запись и train одного датасета защищены общей dataset lease. Если обучение использует данные, запуск записи отказывает до открытия устройств. Неполные и заменённые эпизоды остаются в quarantine. Никакой новый код не открывает аппаратуру при тестах/старте сервиса.

Официальный контракт ACT: https://huggingface.co/docs/lerobot/act . Точная реализация проверяется по установленному LeRobot0.6.1 в собственной .venv; runtime imports из других проектов запрещены.
