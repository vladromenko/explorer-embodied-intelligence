# Окно смены native phase во время ожидания наблюдения

`CooperativeWorkflow` различает ожидание человеческого наблюдения и отправку
native команды. `native_phase_change_window(row)` сообщает только о допустимости
изменения привязки: это не grant, lease, stationary/hold evidence или разрешение
движения. Root USER facade отдельно проверяет каждый source и opaque choice.

Перед source RPC facade вызывает
`begin_native_phase_change(session_id, expected_epoch, role, operation)` и держит
opaque token до завершения source reply/custody/lineage. Освобождение —
`end_native_phase_change(token)` в finally. Начало нового protocol step запрещено,
пока есть текущая reservation. Выход из evidence checkpoint сначала закрывает
новые reservation, затем ждёт уже принятые не дольше3s и исходного FSM deadline.
После ожидания повторно проверяются свежесть evidence и source dispatch authority.
Controller lease/goal/hold TTL не изменяются и не продлеваются.

Checkpoint и reservation — только RAM текущего процесса. Snapshot показывает
checkpoint, `_save` удаляет его из durable session projection. Нового fsync в
workflow/STOP lock нет. После рестарта прежний epoch INTERRUPTED; ни checkpoint,
ни незаконченная mutation не восстанавливаются как полномочие. STOP меняет epoch
и пробуждает ожидание без ожидания source reply. Отказ evidence сразу идёт в STOP,
сохраняя исходную ошибку, без3s ожидания mutation.

Уже выданные native release/transfer challenges привязаны к исходному epoch:
такие evidence checkpoints **не** разрешают park/adopt/activate. Это сохраняет
causal proof и не решает само по себе полный release workflow через human wait.
Для него требуется отдельная транзакция под current USER, а не ослабление proof,
автопродление lease или автоматическое повторение старого release.

Содержательные fake-owner проверки: evidence→reserved change→закрытое окно→
advance только после final reply; отказ новой mutation в actual guard submission;
STOP во время закрытия; немедленная отмена при отказе evidence; отсутствие
native RPC/replay после restart; неизменяемая привязка native challenge.
См. `tests/test_cooperative_phase_checkpoints.py`. Физическая приёмка не заявлена.
