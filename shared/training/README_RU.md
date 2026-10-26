# Общий программный backend обучения Explorer

`explorer_shared.learning` принадлежит общей платформе экспериментов 01–06.
Импорт реестра не загружает Torch, симулятор или эксперимент 01. Реестр
`backend_catalogue()` описывает фактическую программную готовность;
`get_backend('act')` возвращает PolicyBackend с `create_policy`, `optimize`
и `save_reload`. Вызов factory требует явных размеров наблюдения и команды.
Конкретный адаптер проверяет исходные RGB, состояние, имена, единицы, маски,
разбиение, SkillSpec и происхождение своего воплощения. Ни один backend не
разрешает движение и не содержит аппаратного executor.

В `act_backend.py` перенесены существующие Explorer glue к официальному
LeRobot 0.6.1: ImageNet RGB preprocessing, ACT factory, AdamW, ограничение
градиента, точный sampler, RNG Python/NumPy/Torch/CUDA, атомарные durable
checkpoints, checksum/signature проверки, сохранение и official readback.
Код официального ACT не копируется и не подменяется. `artifacts.py` содержит
точные прежние canonical JSON/SHA/state digest helpers без Torch и симулятора;
`settings.py` содержит только параметры ACT. Shared не импортирует pilot,
explorer_sim, MuJoCo, serial или аппаратные классы.

Старый `explorer_sim.student` сохраняет совместимые публичные имена и
прежние defaults для своего 9D simulation adapter. У него остаются только
специфические episode/environment/evaluation функции. Текущие recorded,
SO1016D и ROSMASTER fused9D адаптеры обращаются к shared ACT напрямую.
Сериализованное старое имя `pilot_active_action_indices` сохраняется для
побайтовой совместимости preprocessing; адаптеры всегда передают реальные
диапазоны и индексы, а не получают координаты другого робота автоматически.

Программная подпись нового optimizer graph включает shared backend и generic
artifact helpers, dataset class/source files, официальный policy source,
settings, feature shapes, версии, runtime options и RNG/sampler. Старый
checkpoint с другой подписью отклоняется без изменения его pointer/весов.
Никакие сохранённые модели, исходники или старые receipts не мигрируют в месте
хранения. Старые durable jobs должны продолжать свою зафиксированную версию
кода; смена graph требует новой квалификации и отдельной задачи.

В реестре ACT имеет CPU offline qualification. Его новая CUDA graph пока
требует отдельного реального прогона; старое GPU-свидетельство не переносится.
Прямой ACT factory сохраняет существующие CUDA вычисления, чтобы выполнить
такую квалификацию отдельно. Diffusion и SmolVLA перечислены как UNQUALIFIED:
реестр отказывается создавать их до реализации и фактической проверки.
Это не заявление об их результатах или доступности на текущей памяти Jetson.

## Проверки 2026-10-26 на Homebrain

`tests/fixtures/act_backend_before_shared.py.txt` — замороженная копия прежнего
Explorer glue, SHA ce4610c12b13ca8689fb96dee26aebb274fbb2bf8b8fd88b730b040a99e0a866.
Matched qualification выполняет настоящий официальный ACT6 на 8 временных
RGB/state/action кадрах: прежний backend делает 8 updates, shared делает
4 updates, прерывается после durable checkpoint, восстанавливается и делает
оставшиеся 4. Все веса, optimizer state, loss history и опубликованные файлы
checkpoint побитно одинаковы. Save/reload error 0; loss 0.924544 → 0.467538.
Оригинальные байты не изменены. Старый optimizer graph отвергнут без перезаписи.
Физика и аппаратное исполнение отсутствуют, source_kind SIMULATION.

Отдельный matched run: 4 pass / 16.82s. Обычная совместимость: 41 pass,
3 explicit skip / 34.84s; включает существующие stochastic restart/lock/source
checks, настоящий official ACT4 resume и fused ROSMASTER ACT8. Три пропуска
предотвращают повтор особо выбранных optimizer/physics квалификаций.
Новый wheel собран локально без dependencies/Hub; shared subpackage включён.
Точное свидетельство: `ACT_BACKEND_QUALIFICATION.json`.

Обычный запуск: `pytest -q tests/test_shared_act_backend.py`.
Для явного matched прогона: `EXPLORER_RUN_SHARED_ACT_QUALIFICATION=1`.
Короткое программное обучение и численная ошибка не подтверждают владение
предметным навыком, закрытый full-task тест, shadow или физическую готовность.
