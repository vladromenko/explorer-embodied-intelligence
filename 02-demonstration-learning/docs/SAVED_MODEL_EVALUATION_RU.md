# Сохранённые ROSMASTER9 версии и офлайн-проверка

`explorer.rosmaster_learning.model_versions(store, skill_id, version)` читает
метаданные уже завершённых `rosmaster_skill_train`. Список не загружает Torch,
модель или источник наблюдений. Совместимость в списке требует повторной
проверки файлов перед вычислением и не является физической квалификацией.

`enqueue_evaluation(store, skill_id, version, model_job_id)` создаёт отдельное
задание `rosmaster_skill_eval`. Предлагаемый Root маршрут — явный POST
`/api/skill-workflow/{skill_id}/evaluate` с `{version, model_job_id}`; сервер
сам разрешает пути и SHA. Класс ресурсов: CPU2, память2GiB, GPU0, общий
последовательный inference slot. Изолированный worker вызывает
`run_rosmaster_evaluation(store, job)`. Эти изменения сами не добавляют маршрут
или scheduler dispatch: их подключает Root в согласованном релизе.

Повторная оценка читает только DEV из закреплённого канонического aggregate.
TRAIN нужен для проверки сохранённого train-only normalizer; SEALED payload
не загружается. Оптимизатор, симулятор, Source HTTP и dispatch не создаются.
Проверяются версия SkillSpec, исходный aggregate, ACT106→9 и каждый сохранённый
checkpoint SHA до/после оценки. Отмена до загрузки и после вычисления не
публикует успешный результат. Отдельный immutable `offline-evaluation.json`
содержит ошибки предсказаний; они не означают успешный rollout или мастерство.

`enqueue_shadow(...)` использует существующий `rosmaster_skill_shadow`: CPU,
сначала загрузка модели, затем только authenticated GET свежего Source packet
и точно закреплённого кадра. Новый `skill_reference` дополнительно проверяется
worker. Старые state9 модели не взаимозаменяемы с state106. SIM/mixed модели
остаются offline. Ошибка реального original reference не обходится; до
отдельного наблюдаемого USER reference теста live shadow честно блокируется.

Тесты `test_rosmaster_learning.py` используют временные исходные файлы,
**фиктивные веса и injected evaluator**, поэтому доказывают только маршрутизацию,
хеши, DEV/SEALED разделение и отмену. Это не фактическое обучение/инференс и
не физические демонстрации. Бounded ACT/LeRobot qualification остаётся отдельной.
