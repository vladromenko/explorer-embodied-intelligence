# Полный объект: текущие состояния ученика → проверенный placement teacher → отдельный learned specialist

`explorer_sim.manipulation_selective_repair` выполняет один конечный раунд
коррекций для candy→tray. Он сохраняет исходный BC incumbent из
`manipulation_curriculum`; отклонённый PPO не используется как новая основа.
Это привилегированный симуляционный baseline с явной композицией двух
нейросетевых actor, а не визуальная deployable policy или новый научный метод.

## Откуда взялось исправление

В исходном `97b6...` BC действительно захватывал, поднимал и переносил предмет,
но размещение не завершалось. В двух development-трассах carried predicate
появился на шагах 255/259; затем предмет оказался вне лотка без нижней опоры.
Loss BC не объясняет причину. Первый prototype teacher преждевременно раскрывал
частично открытый захват над лотком и также провалился; эта попытка сохранена.

Teacher исправлен по наблюдаемой геометрии: перед раскрытием требуется низкая
зона предмета и малая измеренная скорость. Частично открытый learner gripper
при высоком предмете не становится разрешением на отпускание.

Для проверки из точной исходной BC-трассы заново исполняются реальные команды
до выбранного held/carried состояния. И native physics state, и вся история
независимого evaluator восстанавливаются реальным replay, без подстановки
`grasped/lifted/carried` flags. В квалификации нет teleport, weld предмета,
изменения материала или force limits.

## Teacher и типы данных

Чистая функция `feedback_placement_query` видит только текущее привилегированное
состояние MuJoCo: положение всех точек предмета, jaw offset, фактические
bilateral pad forces, скорость и ранее наблюдавшийся carried stage.
Она центрирует предмет, опускает его и раскрывает захват только в допустимой
текущей зоне. Суставные коррекции получены local CAD Jacobian и проходят тот же
неизменённый sim velocity/range executor. Отсутствующее удержание вне зоны
release/settle приводит к abstention.

Разделены три роли:

- `actual_learner_dispatch6` и raw learner actions — то, что действительно
  исполнялось при beta=0 сборе состояний;
- `counterfactual_teacher_action6` — текущий запрос teacher, **не исполненный**
  вместо learner; он никогда не переименовывается в dispatch;
- `executed_feedback_recovery_action6` — настоящие действия отдельного,
  независимо завершённого recovery branch.

Failing learner states остаются в NPZ. Targets допускаются только после
положительной квалификации teacher на фактическом held state этой collection
попытки и при действительных геометрических/contact условиях текущего query.
Маска ложных/пропущенных/отказанных labels сохраняется. Будущие запросы на
будущих learner states не превращаются в chunk продолжения эксперта.

К успешным исходным expert placement rows добавляются current-step queries и
реально исполненные успешные recovery continuations. Последние покрывают
опускание/отпускание после выбора коррекции — состояния, в которые исходный
ошибающийся learner сам не попадал. Это явно обозначенная смесь DAgger queries
и supervised recovery demonstrations.

## Learned evaluation и сохранение incumbent

Adam обучает отдельный placement actor. Исходный BC checkpoint сохраняется.
`GatedPlacementPolicy` выбирает исходный learned actor до carried stage и новый
learned actor после него. Селектор использует только текущий флаг в уже
объявленном privileged observation36; teacher, план траектории или новые
future observations не вызываются во время learned rollout.

Это специальный baseline с привилегированным phase selector, а не стандартный
одно-сетевой DAgger и не готовый визуальный student. Независимые полные попытки
начинаются с обычного reset. Результат определяется полным contact evaluator,
включая отпускание и устойчивое размещение. Кандидат принимается для следующей
стадии разработки только при успехе всей текущей серии без регрессии incumbent,
точном native replay и сохранности исходных bytes.

Collection и development seeds объявляются до сбора. Исходные train seeds
8421–8423 и development 18421–18422 исключены из новой collection и нового
независимого evaluation. Prototype/tests на исходной 18421 используются только
для проверки teacher и не попадают в adaptation labels.

## Запуск интегратором

Job kind `manipulation_selective_repair`, project `01`, CPU:

```python
from explorer_sim.manipulation_selective_repair import run_manipulation_selective_repair
result = run_manipulation_selective_repair(store, job, config)
```

Источник — ровно один `source_job_id` завершённого `manipulation_curriculum`
или `source_result_path` вместе с `source_result_sha256`. Проверяются исходный
BC checkpoint, preprocessing implementation и все artifact SHA.
Конфигурация: `seed`, `collection_episodes`, `evaluation_episodes`,
`adaptation_steps`, `query_stride`, `horizon`, `recovery_horizon`, `device=cpu`.
По умолчанию 2 collection attempts, 4000 Adam steps, 2 development attempts.
Повторный полный запуск после прерывания сохраняет отдельную новую attempt;
точное продолжение optimizer после reboot этим модулем не заявляется.

Все queried/abstained состояния, реальные recovery branches, native states,
оба типа actions, исходники, исходы и SHA сохраняются рядом с результатом.
Никакого физического IO и изменения настроек контроллера нет.


## Фактический раунд Homebrain

Run `729c4daf77904311a5edb76f5325e20e`, 331,5 с. Новая collection:
15241/15242; новые development seeds 23241/23242. Исходные development
18421/18422 не использовались для query/adaptation supervision.

На настоящем learner state 15241 teacher прошёл recovery за 86 новых команд,
получив полный contact success после восстановления native-prefix с ошибкой
0,0. В этом failed learner episode получены 66 действительных counterfactual
queries. На 15242 исходный learner не достиг carried stage: все 600 состояний
сохранены, но valid query targets — 0. Они не превращены в экспертные действия.

Выполнены 4000 настоящих actor Adam steps; loss 0,07649 → 0,000134. Critic
не менялся. Сохранены отдельный learned placement checkpoint и исходный BC.

Независимые полные попытки остались **0/2 → 0/2**. В обеих новых сценах робот
не достиг carried stage; в одной он получил только захват без подъёма.
Следовательно, selector не вызвал placement specialist ни в одной из этих
попыток. По этому результату качество самого learned placement component
остаётся **UNKNOWN**, а общий навык задачи не улучшился. Нельзя приписывать
ему успех квалифицированного model-based teacher или считать небольшой loss
проверкой learned исполнения.

Кандидат отклонён, incumbent сохранён. Повторное исполнение 600 действий из
сохранённого native state дало максимальную ошибку 0,0. Teacher при learned
rollouts не вызывался. Точная следующая исследовательская потребность —
устойчивое начальное приобретение захвата/подъёма и отдельная проверка learned
placement из заранее зафиксированных held states; это ещё не выполнено данным
раундом. Дополнительные обучения после этого атомарного этапа не запускались.

Краткий receipt: `MANIPULATION_SELECTIVE_REPAIR_VALIDATION.json`; все
counterfactual/actual labels, неуспешные состояния, реальные recovery branches,
native states, RGB, checkpoints и SHA — по пути результата из receipt.
