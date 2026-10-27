# TRAIN-only B: остаточные ошибки измерены, причинное объяснение ещё открыто

На Homebrain завершены ровно **1756 CPU forward calls**: по 439 batch1
запросов к каждому из четырёх неизменных C-checkpoint. На каждом входе —
сохранённый TRAIN RGB и literal prior9 с causal elapsed/40 s либо ZERO.
Весь output16×9 сохранён. Актор не получает action labels, padding, phase,
teacher/evaluator/object truth. Вызовов posterior, optimizer, physics,
teacher controller и dispatch — 0. Новых weights или моделей нет.

Это анализ аппроксимации **одного исходного TRAIN эпизода74104** с teacher
forcing, а не новые независимые episodes или проверка выполнения задачи.
6904 valid target slots на модель перекрываются между окнами и не образуют
6904 независимых показов. DEV/SEALED не использованы как input, labels,
критерий выбора модели или tuning. Прежние C-v2 full-task FAILURE16/UNKNOWN0
и все их result/trace SHA сохранены; повторных rollout не было.

| Фиксированная модель | Model MAE | HOLD MAE | ZERO MAE | Model horizon0..7 | Model horizon8..15 |
|---|---:|---:|---:|---:|---:|
| ZERO_ABS | 0.02260445 | 0.02189745 | 0.34873244 | 0.02094116 | 0.02429887 |
| ELAPSED_ABS | 0.02471664 | 0.02189745 | 0.34873244 | 0.02303217 | 0.02643263 |
| ZERO_INCREMENT | 0.05016693 | 0.07332784 | 0.07332784 | 0.04924381 | 0.05110732 |
| ELAPSED_INCREMENT | 0.05288294 | 0.07332784 | 0.07332784 | 0.05422847 | 0.05151224 |

Это normalized MAE по всем 9 каналам и valid slots; padding исключён.
ABS нормирует endpoints, INCR — dispatched increments и base velocity.
Поэтому значения **нельзя ранжировать между разными representation**.
Внутри ABS обе модели немного хуже causal HOLD по общей ошибке; внутри
INCR обе лучше ZERO/HOLD. HOLD использует только origin prior9, не будущие
истинные команды. ZERO — normalized0: для ABS midpoint, для INCR zero delta.
Оба baseline — математические predictions; действий они не выполняли.
Три valid neutral chassis channels сохранены и входят в знаменатель.

Остаточные gripper ошибки по target phase:

| Модель / quantity | close Model | close HOLD | release Model | release HOLD |
|---|---:|---:|---:|---:|
| ZERO_ABS, endpoint deg | 7.294567 | 26.666667 | 10.250197 | 19.833333 |
| ELAPSED_ABS, endpoint deg | 11.874417 | 26.666667 | 9.157595 | 19.833333 |
| ZERO_INCREMENT, increment deg/step | 1.121429 | 3.589744 | 2.321780 | 2.333333 |
| ELAPSED_INCREMENT, increment deg/step | 0.612858 | 3.589744 | 2.327242 | 2.333333 |

Increment release остаётся близок к zero/hold baseline. Ошибка endpoint
и ошибка delta/step имеют разный смысл; они не сравниваются напрямую.
Teacher forcing подаёт записанные prior9, поэтому эти числа не ограничивают
накопление ошибки в самостоятельном closed loop и не доказывают grasp,
release, attained object goal или физическую точность. Полные joint/phase
метрики, counts и raw predictions доступны по SHA из machine receipt.

Статически inspected установленный ACT0.6.1: обучение использует sampled
posterior с future TRAIN action labels, dropout и masked L1 + 0.1 KL;
deployment `predict_action_chunk` переводит модель в eval с zero latent.
В B actual hooks подтвердили **1756 нулевых latent / 0 posterior calls**.
Исторические средние L1 последних50 optimizer entries: ZERO_ABS0.03070639,
ELAPSED_ABS0.03114124, ZERO_INCREMENT0.06193969,
ELAPSED_INCREMENT0.06022591. Это stochastic minibatch/posterior/dropout loss,
не контролируемый posterior-vs-prior forward на тех же inputs. Здесь такой
контраст не исполнялся; уникальная latent-gap причина не установлена.

Исходный A остаётся **pre-model guard failure, 0 forward / 3 NOT_STARTED**,
не performance negative. Его неверное сравнение dist2.11.0 с module build
2.11.0+cu130 не переписано. B отдельно зафиксировал version.py bytes/AST
literals, METADATA и actual exact runtime values без удаления suffix.
Ретроспективная full-build identity первоначального training не заявлена.

Все четыре units завершились и reaped. Source2102 pins совпали до/после;
actual model state и Torch RNG equality guards прошли. Во всех439 outputs
на модель проверены SHA; RGB/state10 и independent NumPy prepared-RGB
tensor reconstruction совпали для всех1756 inputs. Все scorer metrics
точно пересчитаны из сохранённых predictions без Torch
или новых model calls. Actual cgroup peak максимум575987712 B; CPU2,
MemoryMax3 GiB, swap0, LimitCORE0, RuntimeMax900 s, OOM/cap events0,
без CUDA execution или EGL. OS/runtime closure остаётся PARTIAL.

[Полный receipt, checkpoints, input/output/resource и исходные SHA](target-train-forward-audit-B-results-20261027.json).
Измерена неполная TRAIN-аппроксимация с фазовыми ошибками. Single-example
sampling, alias, representation, latent/optimization и closed-loop
distribution остаются проверяемыми вопросами, а не доказанной единственной
причиной всех16 отрицательных rollout. Модель не выбрана и не promoted;
learned-source04/H2, physical/nav/vision-causality и полный освоенный навык
не заявлены. Общий master-prompt остаётся **IN_PROGRESS**.
