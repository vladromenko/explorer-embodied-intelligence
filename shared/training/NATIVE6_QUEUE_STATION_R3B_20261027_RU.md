# Настоящая CPU-пауза ACT6 и Diffusion6 на текущем исходном коде

Frozen release: `2542dc7d1e86b5bf9191f476bb82bae4eca5328f`. Это реальный
Supervisor → bootstrap → job_runner в двух временных Store с нулём native nodes.
Оба существующих официальных теста прошли: **2 passed in 112.60s**. Unit
`explorer-station-cpu-controller-20261027-r3b.service` завершился кодом 0 за
120.304s; invocation `7b02d06c791f47a983a366fdc4e8d1f8`.

| Backend | Пауза | Старый → новый PID | После resume | Итоговые веса |
|---|---|---|---|---|
| ACT6 | 2 | 2061265 → 2061311 | ещё 6, всего 8 | `4305ead320fe9c301a960317b1f4ec852150b61125d8607a2ebbf5dd9ea6c5b1` |
| Diffusion6 | 2 | 2061444 → 2061537 | ещё 6, всего 8 | `ad5624f9191b923f00d501a2fc5bdcfedc70cfc91597b9674112acbb9f97548d` |

Supervisor проверил завершение старого PID до resume. Сохранённые
optimizer-paused/optimizer-active receipts независимо связывают PID, kernel
start_ticks и boot_id. У обоих backend итоговые веса, optimizer, losses,
sampler и Torch CPU RNG точно совпадают с собственным непрерывным запуском.
Python/NumPy RNG продолжает собственный paused checkpoint; независимые
процессы могут начинать ранее неиспользованные RNG по-разному. Дополнительная
проверка после теста сравнила также tensor dtype/layout/shape, типы scalar и
структуру коллекций. Веса обоих controller-профилей точно равны прежнему
PROFILE4 controller; DP здесь seed0, поэтому его SHA отличается от отдельной
native6 4+4 qualification с seed41. Это разные неизменённые сценарии.

Фактический unit: CPUQuota 200%, MemoryMax 4 GiB, MemorySwapMax 0. Изнутри
живого cgroup сохранены **memory.peak 3 330 977 792 B**, swap peak 0; все
memory.events 0. Parent-process ru_maxrss 1 452 621 824 B — отдельная метрика.
Root resource window освобождён, unit inactive. GPU/EGL не использовались.

SOURCE_LIBRARY содержит все четыре job, их config/result/checkpoint hashes,
cohort/train/dev/sealed receipt, выбранный DP runtime binding и исходный TAR.
SOURCE_TREE перечисляет SHA каждого обычного файла архива, распакованные байты
сверены; symlink metadata сохранена отдельно. Исторические receipts не изменены.
DP dispatch использовал новый измеренный binding
`8bc7bd625702da22a3074c5640e118ec6a83b096e81da6b9d5ff710704f2ac0b`.
Формальный Station SHA `2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885`.
Auxiliary Gate относится только к этому frozen release, не к последующим
engineering-only изменениям.

Все эпизоды — SIMULATION software fixtures. Проверены software optimizer и
жизненный цикл общей очереди; физический replay/rollout, RGB сенсорная
квалификация реального устройства и успешное владение предметной задачей здесь
не испытывались и не заявляются. Никакой исходный контроллер/feedback/servo
режим не менялся. Приватные originals, source.tar, checkpoint и журнал:
`/mnt/explorer-data/explorer-work/qualification-station-controller-20261027-r3b`.
