# Текущая CPU-квалификация после Station E5

Реально испытан clean frozen commit `fb430399c46297d1297c012c83b218dae4cf9801`.
Формальный native6 граф содержит 18 файлов; единственное отличие от старого
R2 — Station SHA `2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885`.
Вспомогательный Gate SHA `0e01381384b0def9adfbd2ec4813f213c313a726bc1e9eea03d26520f5347a7a`
зафиксирован отдельно и относится только к этим испытаниям. Это не проверка
каких-либо последующих изменений Gate.

Все три настоящих pytest-проверки прошли, reload error равен нулю, веса точно
совпали с прежними восьмишаговыми R2. Параметры и нормализация не менялись:

| Профиль | Реальный optimizer | CPU, изображение, batch | Веса model.safetensors | kernel memory.peak |
|---|---|---|---|---|
| ACT6 | 8 | 2, 32, 2; chunk4; измеренное6 → issued6 | `4305ead320fe9c301a960317b1f4ec852150b61125d8607a2ebbf5dd9ea6c5b1` | 1 488 539 648 B |
| Diffusion6 | 4 + durable reload + 4 | 2, 64, 2; history2/horizon8/prediction4 | `149e662f58be6c77a2043c26eb0e41b45eabe61334993569ade0a271d41c87e5` | 1 370 181 632 B |
| ACT106 → 9 | 8 | 2, 32, 2; chunk2 | `dc58119198b0dfa94bd3b3f787ebdbcb2ef9deaa3bce4d55c6fb2a38938d55c2` | 1 196 605 440 B |

Каждый optimizer выполнялся в отдельном последовательном user unit с
MemoryMax 4 GiB, MemorySwapMax 0, CPUQuota 200%. Внутренние kernel memory.events
не содержат OOM/max; swap peak 0. `ru_maxrss` и cgroup peak приведены отдельно:
это разные способы учёта shared/file-backed страниц. Послепроцессный
systemd summary peak в этих запусках занижен; основное измерение — memory.peak,
прочитанный внутри живого cgroup и сохранённый в runtime-after.

У ACT6 и ACT106 сам pytest вернул 0, а внешний сборщик отчёта вернул 1:
первый дважды посчитал один файл через pytest current symlink, второй зависел
от сокращённого имени каталога. Оба старых отрицательных отчёта, журналы,
источники сборщика, исходные dataset/checkpoint сохранены. Новая валидация
читает типизированные артефакты внутри собственного basetemp, сводит только
одинаковые canonical paths, проверяет весь checkpoint, runtime/source и R2
SHA; обучение и dataset повторно не создавались. Поле
`original_reporting_wrapper_exit_code=1` остаётся в новых receipts.
Отдельный отказ первого metadata publisher при записи итогового имени файла
также сохранён в attempt ledger; он не запускал model/optimizer.

Новый ACTIVE pointer выбирает только датированные R3B файлы. Старые R2
qualification/binding/weights не изменены. Реальный `qualified_diffusion_runtime`
прошёл на скопированном текущем графе18 без импорта Torch, dispatch binding
`8bc7bd625702da22a3074c5640e118ec6a83b096e81da6b9d5ff710704f2ac0b`.
Контрольная подмена одного байта Station была отвергнута.

Diffusion 4+4 здесь — durable optimizer reload внутри pytest процесса.
Отдельная новая проверка Supervisor→bootstrap→job_runner→остановка процесса→новый
PID ещё не входит в эти receipts и должна следовать после commit нового ACTIVE.
Все данные — SIMULATION software fixtures. Ни физический replay/rollout, ни
успех предметной задачи, ни готовность к автономному движению не заявляются.

Приватные оригиналы:
`/mnt/explorer-data/explorer-work/qualification-station-cpu-20261027-r3/act`
и `/mnt/explorer-data/explorer-work/qualification-station-cpu-20261027-r3b`.
