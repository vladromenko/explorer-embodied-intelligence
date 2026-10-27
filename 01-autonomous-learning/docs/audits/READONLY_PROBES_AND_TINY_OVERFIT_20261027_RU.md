# 01: результаты read-only probes и следующий различающий контроль

Новых optimizer updates, model writes, sim steps, robot/network requests —0.
Существующие product sources/checkpoints/Source arrays не менялись. Два CPU
inference запуска и два EGL render diagnostic запуска согласованы отдельно;
raw originals, requests, scripts, resource samples и отрицательные исходы
сохранены в новых приватных каталогах. Это не исправленный ACT и не whole01.

## Existing weights: результат и существенная слабость state-пробы

Оба checkpoint actual NX DAgger23eb… загружены serial, CPU1/2GiB/swap0/max120s,
120 prior(z0) chunk queries каждый. Все modules eval, gradients отсутствуют,
files/state_dict byte hashes неизменны; inference RNG неизменен, исходные
Python/NumPy/Torch CPU RNG восстановлены; CUDA не инициализирована.
Runtime13.767/13.757s, cgroup peaks614494208/615411712B, swap/events0.
Posterior не форсировался: official branch требует training semantics.

Round0 RGB permutation max active3 change4.17e-5/2.50e-5/2.45e-5; весь black
RGB даёт3.39e-4/1.57e-4/1.08e-4. Round1 соответственно
7.75e-7/1.57e-6/9.02e-7 и1.84e-5/2.25e-5/3.14e-5. Сохранённые raw chunks
подтверждают почти постоянные predictions и слабое влияние этих RGB контрастов.
Black RGB — OOD stimulus, не task-performance test и не доказанная причина.

**State permutation оказалась недостаточно различающей.** Две initial группы
имеют точно одинаковые HOME states; late6 spans только0.0023deg. Малый ответ
на такой stimulus НЕ доказывает нечувствительность к значимому изменению позы.
JSON явно сохраняет native ranges каждого stimulus. Следующий state-control
должен использовать разные реальные source states, а не выдать нулевую
перестановку одинаковых входов за причинную абляцию.

## True reach_target ON/OFF: оба общих исхода отрицательные

Первый exact probe завершился1 на repeatability/fullstate assertion, до
qualification результата. Instrumented helper не удалял warmstart/pluginstate,
не менял invariant и не добавлял mj_forward/render calls. Он записал каждое
поле до/после original operations. НИ ОДНО integration field не изменилось.
После8конечных pair examples goal8 завершился1: fullstate hash совпадает,
но OFF-vs-OFF отличаются7pixels, max1LSB. Это rendered-byte repeatability noise,
но конкретная driver/render причина не установлена. Новый threshold «простить»
пиксели не введён. Все неудачные попытки остаются FAIL.

В восьми завершённых STATIC-reference pairs true reach_target в64² влияет на
13..26pixels, max115..172channel levels. Это сильнее green-mask1..3pixels,
который смешивал target с grasp marker. Поэтому нельзя объяснять collapse
утверждением «цели совсем нет на RGB». Это только конечные8статических примеров,
не full12 visibility qualification, не recorded settled-state replay и не
доказательство, что сеть научилась видеть цель. Source/XML/CAD и камера
сохранены, динамика/actor в render diagnostic не исполнялись.

## Наименьший preregistered optimizer positive control — ПРОЕКТ, не запуск

Сначала Root утверждает exact helper/request/source pins/resources. Ниже
диагностические наборы, не новые expert демонстрации/модель для исполнения.
Исходные episodes остаются неизменными.

Два независимых конечных control cell на original official ACT0.6.1: CPU,
state9/action9,RGB64,chunk8 с ОДНИМ current label/7 padded positions,
ResNet18fromscratch,dim128,тот же L1+KL/optimizer/preprocessing. Seed86001,
одинаковая initial weights identity, batch8. Не новая архитектура/физика.

- **Visual8**: первые8successful original PPO episodes, index0. Все HOME states
  одинаковы, RGB/реальные teacher endpoint labels различаются. Только эти8
  executed-success source labels; no learner failure actions/goal_xyz input.
- **StateIdentity8**:8РАЗНЫХ actual pre-command states из тех же successful
  training originals, выбранных заранее deterministic farthest-point по
  normalized active3 (first tie by originalID/frame). Один fixed RGB, target
  равен source STATE9. Это явно SYNTHETIC_IDENTITY_DIAGNOSTIC, не expert action
  и не training-eligible demonstration. Проверяет способность conditioning по
  существующему state input без маленькой цели в изображении.

До optimizer зафиксировать8source IDs/frame indices/SHA, state/label ranges,
normalized constant-median baseline MAE(active3) и checkpoint seed. Если данные
не дают ≥2varying active axes или8distinct states для Identity — setup BLOCKED,
не заменять их вымышленными числами. Teacher/evaluator fields не входят в actor.

Предлагаемый жёсткий smoke budget: максимум256updates на cell, CPU1/4GiB/swap0/
max300s/threads1/LimitCORE0, один process за раз. Actual8-update resource check
можно выделить первым; OOM/NaN/timeout — сохранённый FAIL, не auto-retry. Это
пока предложение, ресурс/optimizer не разрешён и compute не запускался.

Заранее фиксируемый memorization criterion на собственных8контрольных samples,
только eval prior(z0), а не teacher-forced training loss:
MAE(active3) ≤25% optimal constant-median baseline, и ≤0.003 normalized на
каждой varying axis; inactive-axis MAE≤0.005. Правильная permutation после fit
должна увеличить target MAE минимум2×: RGB permutation для Visual8, meaningful
state permutation для Identity8. После save/reload в новом процессе predictions должны пройти тот же критерий.
Это конечная проверка conditional fit, не
независимое task success/DEV/sealed evidence.

Интерпретация:

- Identity PASS / Visual FAIL → visual path/observability/fit hypothesis сильнее;
  сначала исследовать размер/контраст и градиенты visual conditioning.
- Оба FAIL → разобрать optimization/conditioning/runtime, не тратить длинную
  PPO/DAgger кампанию и не менять модель наугад.
- Оба PASS → интерфейс способен запомнить conditional mapping; canonical
  teacher→данные→BC/DAgger на НОВЫХ DEV/sealed seeds из основного плана всё ещё
  нужен для обобщения/recovery. Сохраняется matched extra-BC control.

Ни один вариант не разрешает physical path, не повышает статусы sock/kitchen/
contact/mobile и не превращает reach в весь выполненный master-prompt.
