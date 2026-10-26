# Текущий SO101 source04 → transfer06

`explorer.transfer_source` — ENGINEERING_GLUE, новый typed adapter. Старый
`transfer.policy_transfer_sources` признавал лишь `train_act/feature_dimension6`,
поэтому актуальные `so101_skill_train` не попадали в selector. Новый модуль не
трогает legacy bytes/routes или app; parent связывает его с UI/API.

## Control plane

`current_source_choices(store)` возвращает schema
`explorer_transfer_current_sources_v1`, `choices` текущих completed
`so101_skill_train`, отдельно `legacy_analysis_sources`. Контрольная загрузка
не импортирует Torch, robot/camera factories или runtime policy. Каждая current
choice имеет job/SkillUUID/version/specSHA, ACT/DP/history1/2, source kind/domain,
source native6 contract, camera/follower calibration SHA, aggregate/checkpoint
contentSHA, `technical_source_ready`, `source_object_rollout_qualified`,
`eligible_for_transfer`, `blockers`, unsealed episode choices и evaluation attempts.
Raw head6 никогда не переименовывается в target9; source и target версии одного
SkillUUID остаются разными реализациями.

`inspect_source_model(store, model_job_id)` повторно использует **настоящий**
`verify_so101_aggregate`: durable Spec/confirmation/cohort/review/source SHA и
разделение originals, а не собственные флаги dataset. Проверяет immutable training
receipt, native names/units/ranges, measured follower state, actual optimizer/
weights/save-reload/official Dataset readback receipts, полный checkpoint map,
registered SkillCard, CPU saved backend, ACT saved official processors и source
camera/calibration, отдельный compact DP history2 profile. Изменившаяся review
cohort или лишний checkpoint file делает technical readiness false.

Наличие прошедшей source04 training job **не** подтверждает предметный навык:
operator video labels — BC targets, MAE/loss/shadow — другая проверка.
Исторический hold/joint replay не становится object source.

## Два разных незакрытых условия

`evaluation_owner_capabilities()` лениво ищет будущий модуль
`explorer.so101_source_evaluation.source_evaluation_capabilities()`. Пока такого
реального владельца нет, остаётся **A_SOURCE_EVALUATION_OWNER_NOT_IMPLEMENTED**.
Это программная работа, её нельзя скрыть за запросом новых показов.

Без самостоятельного полного object rollout той же actual модели остаётся
**C_NO_QUALIFIED_OBJECT_TRACE**. После реализации software owner физическая
проверка потребует отдельной текущей user-observed сессии (B); этот selector
не создаёт её и не разрешает движение. Ни один existing actual current model
не объявлен им mastered. Тесты положительных контрактов ниже — явно SOFTWARE
STRUCTURAL fixtures, не реальные обученные policy или физические результаты.

## Строгая граница qualified source

Принимается только immutable completed typed server job `so101_skill_eval`,
source role04 и `config.source_model_job_id`, с result schema
`explorer_so101_source_model_object_evaluation_v1`. Сохраняемый result должен
содержать exact source model/Spec/aggregate/checkpoint content/camera/calibration/
domain lineage, `execution_kind=fresh_observation_learned_policy_rollout`,
independent full-task success и `raw_episode_replay_only=false`.

Он указывает server-managed `source_trace_manifest`+SHA. Trace проходит
`shared.skill_transfer.load_source_trace`: QUALIFIED_SOURCE_MODEL, object task,
source native6, один SkillUUID/version, unsealed split, retained model/processors/
evaluation and actual source RGB SHA. Дополнительно adapter требует:

- все saved checkpoint/processor bytes в source inventory; exact original
  `model.safetensors` и `explorer_preprocessing.json` SHA;
- весь набор confirmed SkillSpec phases;
- отдельный retained evaluation receipt (без circular trace↔result hash), schema
  как выше, тот же model/domain, independent full task и success всех фаз;
- число шагов равно trace; каждый native dispatched6, phase/reference, actual RGB
  SHA/frame/camera и checkpoint совпадают;
- unique native dispatch receipt IDs, правильный ACT history1 либо DP history2,
  causal one-domain/boot timestamps observation≤dispatch≤2s и strict action order.

Producer будущего owner отвечает за действительное выполнение сохранённой модели
и независимые object-evidence predicates. Нельзя добавить browser checkbox или
writer «успех=true» вместо evaluator. Сам selector не создаёт qualification job.
Проваленные старые попытки сохраняются в `evaluation_attempts` и не отравляют
отдельную последующую qualified attempt; они не становятся expert targets.

`prepare_qualified_source(store, model_job_id, evaluation_job_id,
expected_source_trace_sha256=...)` принимает IDs, повторно проверяет qualification
и возвращает typed source binding. Указывает source04/target06, тот же SkillUUID,
минимум следующую target version, source partition/domain и retained trace SHA.
Только source qualification: mapping, target-rendered data, adaptation/training,
independent target evaluation и physical authorization остаются отдельными.

## Отдельная offline диагностика

`prepare_source_probe(store, model_job_id, episode_id)` принимает только реальный
unsealed train/development episode текущего admitted cohort; sealed исключён.
Возвращает server binding без разрешения source/target dispatch или qualification.

`SourcePolicyProbe(binding,camera_id=...)` загружает actual существующий
`NativeACTPolicy` либо `NativeDiffusionPolicy` **только при явном runtime call**.
Общие source native6/camera/calibration/masks/clock/boot/freshness проверки стоят
перед predictor. ACT использует ровно1 текущий RGB/measured6 и saved official
processors; DP —2 разных chronological frames/states и4-step native6 chunk,
без двойной ImageNet normalization. Границы и shape output проверяются.
Возвращаются только arrays +receipt
`offline_original_observation_policy_inference_not_rollout`,
`qualifies_object_source=false`, `source_or_target_commands_dispatched=false`.
Нет robot.connect/send_action, native leases, camera owner, motors, service restart
или controller changes. Фазы/text/privileged object truth в actor не передаются.

Root API/UI и будущий observed source-evaluator owner отдельны. В этих файлах
нет новых app/job_runner/scheduler/Store/native hooks.
