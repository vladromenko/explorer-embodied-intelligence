# TRAIN-only forward audit четырёх замороженных C-кандидатов

Подготовка отдельного опыта после C-v2 FAILURE16/UNKNOWN0. Цель — проверить,
воспроизводят ли четыре уже обученные модели единственный успешный TRAIN
показ при deployment inference, прежде чем объяснять отрицательные rollout
единственной причиной. Это не новый training, teacher, rollout, победитель
2×2 или проверка освоения навыка. DEV/SEALED не используются как входы,
targets, пороги, критерий выбора или источник новых expert labels.

## Неизменные данные и модели

Оригинальный C-v2 протокол SHA
`1a042117e539a481543792e3e4f88f4bc634cbe9a34971f35b7ad9f94df77250`.
Берутся те же четыре 1200-update checkpoint, config и preprocessing; новые
веса не создаются. TRAIN — ровно `target-v3-train-74104`, skill version3,
439 исходных RGB/prior9/action9 с native clocks. Единственный manifest SHA
и skill UUID приходят из frozen C-v2, а не из свободной строки пользователя.
Все прежние 2073 source/input pins и B232 остаются неизменными.

До Torch/model import проверяются: SHA manifest; единственный episode и
его первоначальная train-принадлежность; successful expert admission;
исходный canonical envelope и все его payload SHA с новым запуском чистого
canonical inspector; raw SCRIPTED_SIM result/trace; sample-exact RGB resize,
clock, prior9 и actual dispatch9. Unknown, development/sealed, incomplete,
duplicate, escaped paths, изменённые bytes, недоказанный RGB/action pairing
или необоснованная expert-метка прекращают аудит до модели. NPZ allocation
проверяется по NPY headers до загрузки, `allow_pickle=False`.

Актор получает только сохранённый реальный TARGET RGB и state10:
first9 — ровно исходная float32 нормализация stored prior9, last — actual
elapsed/40 s либо ZERO model ablation. RGB обработка — исходный `prepare_rgb`
с единственной ImageNet-нормализацией. Будущих action, pad mask, phase,
teacher/evaluator/object truth в actor batch нет. Labels считаются точно
как frozen `TemporalObjectChunks`, включая исходный float32 storage cast;
float64 raw trace не подменяет тренировочные targets.

## Что измеряется

На каждой из 439 origin-позиций — один batch1 `predict_action_chunk` с
`.eval()`, нулевым prior latent и сохранением всего output16×9. Итого
**1756 forward calls**, 439 на каждую модель, без очереди или dispatch.
Observed hooks отклоняют posterior-вызов или ненулевой latent; model state,
Torch RNG и checkpoint SHA должны оставаться неизменными. Ни один train-mode
forward с future labels в этом опыте не выполняется.

Установленный LeRobot0.6.1 inspectируется статически с точными source SHA:
`policy.train()` использует VAE posterior с будущими TRAIN action labels,
случайным latent и dropout. Loss — masked L1 по valid slots×9 плюс
0.1×mean KL. `predict_action_chunk` переводит модель в eval и использует
нулевой latent. Поэтому исторические последние50 training-loss entries
сохраняются отдельно; они не равны измерению deployment prior-zero.
Upstream byte identity пакета отдельно не утверждается.

Scorer после сохранения реального output видит labels и annotations.
Padding справа исключается. В одном chunk16 на всех origins суммарно
6904 valid target action slots: 3484 в horizon0..7 и 3420 в horizon8..15.
Выводятся normalized MAE, joint MAE/RMSE/max, ошибки по origin phase и
target phase; для increment — также positive/negative/zero gripper delta.
Native label errors по joint имеют разные единицы: absolute endpoint deg
или increment deg, затем m/s, m/s, rad/s. Mixed-unit scalar не считается.
Никакого threshold «успешный захват» по этим error metrics нет.

Два математических baseline не выполняют действий: `ZERO_NORMALIZED`
(для ABS midpoint, для INCR zero increment/base velocity) и
`HOLD_ORIGIN_NATIVE9` (постоянная origin prior9; для INCR zero arm delta с
origin base velocity). Ни один не использует будущий true prior для
предсказания. Они оцениваются тем же scorer с теми же padding/denominators.

## Исполнение после отдельного ревью freeze

Предлагаются четыре serial units CPU≤2, MemoryMax3 GiB, swap0,
max900 s каждый; CPU-only, без CUDA/EGL. Между моделями процесс завершается
и reap. Подготовка и fake tests не разрешают реальные forward автоматически.
Порядок: ZERO_ABS, ELAPSED_ABS, ZERO_INCREMENT, ELAPSED_INCREMENT.
Все predictions и частичные ошибки сохраняются; нет auto-retry или
подмены отрицательного/неполного результата. `completed.json` появляется
только после повторной проверки всех pins. Summary требует все четыре
полных результата, но не ранжирует и не выбирает модель.

Если ошибки уже велики на TRAIN deployment path, остаются вопросы
fit/latent/representation/sampling; если TRAIN точен, а independent rollout
отрицателен — вопросы closed-loop drift, distribution и visual feedback.
Ни один исход сам по себе не доказывает уникальную причину. Изменение
обучения после этого потребует нового отдельного протокола и новых честных
split; текущие SEALED не становятся development. Физическое движение,
controller settings и production state этим опытом не затрагиваются.
