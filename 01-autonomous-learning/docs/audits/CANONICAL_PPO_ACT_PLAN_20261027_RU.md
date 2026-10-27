# 01: причинный аудит и план канонического reach, 27 октября

Статус: READ-ONLY AUDIT / PROPOSED PREREGISTRATION. Это не исправленный student,
не запуск кампании и не закрытие01. Существующие исходники, модели, данные и
production не менялись; optimizer/model-load/render/sim-step/robot-RPC=0.
Исходная ветка `fa65609`; факты и36 SHA — в `SAVED_TRACE_CAUSE_AUDIT_20261027.json`.

## Что действительно произошло на NX

Есть actual NX PPO pilot `7a2877b5c47f49acaa04c164961e7fcd`: teacher6/6 на
engineering DEV10041..10046. Из12 PPO episodes/167frames успешны10 episodes,
НО это всего47 BC frames: два failed60-frame эпизода правильно исключены.
ACT имеет1/6, не надёжный навык. Старый другой fit2/6 не причинный контроль.

Actual NX DAgger `23eb8ea681894833872cffa8023e86c6`:120+120 optimizer updates,
47expert frames→176aggregate frames, включая129 current-state teacher queries.
Privileged teacher6/6 DEV; ACT0/6→0/6; mean final distance0.09455→0.08014m.
Это отрицательный результат по успеху. Mac development480+480 с2/6 описан
отдельно; он не новый NX run и не matched extra-BC control.

На360 DEV steps requested actions почти постоянны ДО clipping/executor:
std первых3 normalized координат до DAgger примерно
`[2.64e-5,3.14e-5,1.40e-5]`, после `[6.7e-7,6.3e-7,4.1e-7]`.
Это реальная input-insensitivity этих saved rollouts, не только эффект
slew/clipping. Причина ещё не установлена.

Все initial command states равны HOME; цель student может узнать только из
RGB. Initial RGB различаются: у10successful source goals отличия18–38pixels
из64² относительно первого. Простой high-green mask имеет1–3pixels, но
захватывает и зелёный grasp marker. Следовательно, target не доказан невидимым:
нужен контролируемый reach_target ON/OFF, а не green-mask эвристика.

Шесть target dimensions постоянны. State-copy baseline имеет normalized
L1(all9)=0.003618, active3=0.010854 на47expert frames. Saved final training L1
round0=0.10929/round1=0.06921. Это повод проверить conditioning/fit и per-axis
loss; разные minibatches/условия не дают из этих чисел доказательства причины.

## Что не следует «чинить» наугад

Source сохраняет pre-step command state/RGB и post-step causal endpoint9.
JSON содержит максимальные ошибки нормализации и `state[t+1]-action[t]` по
всем12episodes. Сейчас нет подтверждённого one-frame label shift.
`EpisodeChunks` даёт реальное будущие chunk endpoints одного expert episode;
DAgger правильно имеет только current-step counterfactual label, остальные
slots padded. Конечный teacher query не равен проверенному recovery.

Saved ACT config: official0.6.1, chunk8, n_action_steps1, temporal ensembleNone,
VAEtrue/KL0.1, ResNet18 from scratch,dim128. Очередь старого8-step chunk не
объясняет константность при n_action_steps1. Current local official source
нормирует L1 по valid positions, поэтому padded denominator не объявлен багом.
Train-only VAE posterior vs inference zero-latent — проверяемая гипотеза, а не
вывод. Версия/код конкретного исторического runtime должны быть явно указаны.

## Наименьшие различающие пробы, до нового optimizer

Каждая — новый immutable diagnostic result, без изменения checkpoint, Source
или product code. DEV/sealed ниже не участвуют в выборе этих saved-data probes.

1. **Арифметика данных**: по сохранённым SHA проверить before/after state,
   decode/encode endpoint, bounds, finite labels, actual split/success admission.
   Отдельно teacher raw increments/request/executed endpoint; requested failure
   ACT не становится expert target. Уже выполненный array audit — JSON.
2. **Истинная доступность цели**: frozen XML/CAD и ОДНО физическое состояние,
   без physics advance; меняется только `reach_target`. ON/OFF image difference
   при native320×240 и actor64/128. Exact object mask, projected center/coverage,
   occlusion и resize footprint; зелёный grasp marker не размечает goal.
   Preserve pose/model/site/camera hashes. Никаких goal_xyz в actor batch.
3. **Existingweights sensitivity**: по6saved initial DEV frames при SAME HOME
   сравнить own/swap/zero RGB, а затем varying saved state при fixed RGB.
   `reset()` перед каждым независимым query; raw predictions/axis deltas,
   train-vs-live preprocessing equality и loaded checkpoint hash до/после.
   Это CPU inference с existing weights, не rollout/deploy/optimizer.
4. **Posterior/prior diagnostic**: на тех же source observations отдельно
   измерить inference z=0 и label-conditioned VAE branch, если текущая library
   допускает безопасную read-only instrumentation. Children остаются eval,
   BatchNorm buffers не обновляются, gradients/optimizer отсутствуют. Labels в
   этом диагностическом вызове НЕ deployable actor inputs. Если instrumentation
   не может гарантировать неизменность, оставить probe BLOCKED.
5. **Только после результатов**: утверждённый tiny overfit positive control.
   Original ACT,8fixed successful first-frame examples: одинаковый command
   state, различные visual goals/teacher labels; отдельно state-identity control
   с fixed image. Если не проходит memorization, не запускать длинную кампанию.
   Сравнение raw per-axis error со state-copy/mean baseline; никакого нового
   smoothing/clamp/подстановки действия как «ремонта».

Возможные выводы различны: плохой target image требует observation repair;
identity-control failure — поиска fit/runtime проблемы; tiny overfit PASS при
closed-loop FAIL — покрытия/recovery/teacher-query validity. Один correlation
не выбирает новую архитектуру/другой KL/новую физику.

## Предлагаемая заранее фиксированная кампания (не запущена)

Scope — существующий fixed-base CAD reach с3active joints, period0.08s,
horizon60. Success ТОЛЬКО независимый evaluator: distance<0.025m три
последовательных решения, finite/stable physics. Это полный reach curriculum,
не sock/kitchen/grasp/mobile manipulation и не весь01/master.

До любых новых результатов Root должен утвердить и сохранить manifest:

- PPO training seeds81001,81002 (сначала один bounded engineering cell).
- Expert demonstration seeds82000..82047; только известные successful
  outcomes идут в BC, failures остаются raw analysis.
- DAgger collection83000..83011, отдельный training-only набор.
- DEV84000..84031 —32matched attempts для всех методов, не optimizer.
- SEALED85000..85063 —64attempts, закрыты до выбора final checkpoint.
- ACT initialization86001,86002; всё disjoint с историческими41,
  2041..2052,10040..10046,52041,54041..54044,62041..62046.

Threshold proposal: teacher≥29/32DEV; student≥26/32DEV без нестабильности.
Sealed допускается один раз после заморозки model+source+settings:≥52/64
полных reach success. Те же критерии после reload в НОВОМ процессе; exact
weights/source/checkpoint identity и совпадение действий в заданной numerical
runtime qualification. До threshold метрика отрицательная/OPEN. Эти пороги
предлагаются ДО будущего compute, не извлечены из уже увиденного sealed.

Initial teacher budget4096; если Root одобрит, заранее ограниченные8192/16384
проверки teacher DEV с сохранением всех результатов. Не подбирать camera/model
по sealed. Для ACT сначала resource/overfit smoke, затем заранее фиксированные
120/480/1200-step checkpoints, выбор только DEV. Это budget proposal, не
разрешение сейчас тратить compute и не обещание достаточного количества updates.

Если BC не достигает threshold, один DAgger round сравнить с **matched extra-BC**
на тех же исходных весах/seed и том же числе дополнительных updates480.
Обе ветки с одинаковой явно объявленной optimizer initialization; dataset
изменение — новая lineage, не ложный resume прежнего optimizer. Counterfactual
teacher labels на learner states сохраняются отдельно от actual dispatch.
Teacher finite query не считается successful expert continuation; отдельная
simulated recovery validity проверяется на сохранённом полном physics state,
а не на command9/goal alone. Старый trace не содержит полного qpos/qvel/solver
snapshot; его нельзя объявить exact branch replay без новой проверки.

Renderer/teacher/student/DAgger используют один pinned XML/CAD/executor/evaluator.
Raw requested, normalized, post-existing-clipping, actual simulated dispatch и
outcomes сохраняются отдельно. Разрешённая image_size128 — кандидат только
после target visibility probe, не автоматический «успешный» ремонт.

## Ресурсное предложение следующего шага

Root сейчас квалифицирует общие executors; никаких параллельных тяжёлых работ.
После docs-only checkpoint подготовить НОВЫЙ finite read-only probe, зафиксировать
его SHA/command до запуска. Existingweight CPU sensitivity: CPU1/MemoryMax2GiB,
Swap0/LimitCORE0/RuntimeMax120s, threads1/CUDAempty/HFoffline. По одной модели
round0/round1, без optimizer/сохранения новых weights. Render probe — отдельный
process/lease, один renderer, исходные проверенные CAD; только после явного
окна Root, без default запуска при import. Не открывать fleet config/network.

В этой записи не выполнялись probes2–5, не выполнялось новое обучение и не
заявляется, что найденная гипотеза уже причинно исправлена.
