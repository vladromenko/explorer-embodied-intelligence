# 04: зрение и управление используют разные этапы

Профиль `so101_source_only` допускает VLM только при `Station.mode=idle` и без
открытой исходной записи. Постановка семантического события и вход COMMON в
исходную команду сериализуются одним metadata admission. Исходный Station,
его публикация, цикл, serial/camera owners и настройки не меняются.

Пока событие pending/inflight либо освобождение accelerator не подтверждено,
новые start/recenter/action не входят в исходный callback. Cancel отменяет
результат, но не считается завершением уже вошедшего native backend. Scoped
STOP проходит независимо от VLM; исходный global STOP ABI сохранён. Read-only
статус не присваивает разрешение и не меняет порядок исходных Station locks.
Интерфейс показывает недоступность соответствующего действия. После VLM нужна
свежая фактическая наблюдаемость; непрерывно наблюдаемый собственный idle grant
сам по себе не становится новым разрешением или фактом движения.

Используются существующие `HailoObserver.semantic_slot`, `EventSemanticWorker`,
`LabVisionService` и `StationObservedPermission`. Нового владельца камеры,
актуатора или NPU нет. В остальных профилях COMMON context по умолчанию inert;
новая защита относится к source-only04 и ещё **не deployed**.

Actual Pi old4c: единственная Logitech/CV доступна; один локальный Qwen2-VL-2B
вернул SEMANTIC_RESULT за31.576s. Во время native initialization один poll
получил ReadTimeout6s, затем camera/CV/API восстановились в том же PID43382.
Station остался idle, новых motor/grant/STOP вызовов агент не выдавал. Это
подтверждение inference-пути, не точности смысла или предметного навыка.
Исходный JPEG именно VLM-запроса old4c не сохранён, только его SHA/receipt.

Отдельный более поздний actual JPEG сохранён: максимум2/255, сцена практически
чёрная. Он не подменяет исходный VLM input. Выбор placement-cell по точному
JPEG/SHA вернул coordinate_confirmed=false, grid400 без измеренной калибровки.
Это не доказательство пустого места, метрики или захвата. Освещение, предмет и
цель, измеренная привязка остаются задачами physical acceptance.

147 Python COMMON/source/evaluation/cooperative contracts и272 Node PASS.
Actual Firefox с generated CV/VLM и fake buses проверил JPEG/mask/click,
обе стороны UI admission и отдельный scoped STOP, desktop/mobile без overflow.
Kernel peak123359232B/swap0/OOM0. Обе гонки, cancel с работающим backend,
unknown release и lock order покрыты отдельными тестами.

[Полное evidence](../../_registry/evidence/experiment04-vision-source-admission-20261027.json).
Первый безопасный upgrade old4c, release regression и rollout всё ещё открыты.
