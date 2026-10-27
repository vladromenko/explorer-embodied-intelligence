# Одобрение sealed source archive

Исходные recording/manifest/outcomes остаются неизменяемыми. Успешный исход
не означает отдельного разрешения передать или использовать запись в обучении.
Source `record/review` и GET `recordings/{id}/review` возвращают wrapper
`{source_review,review_sha256,transfer_ready}`. Хеш проверяется по точной
сериализации NativeCaptureWriter._bytes (JSON ASCII escaping), отдельно от
canonical embodied admission. Проверяются recording/archive SHA, решение,
actor, native review clock/boot и факт неизменности raw archive.

Root после authenticated Source ответа вызывает
`skill_workflow.capture_reviewed(store,capture_id,native_episode_id,wrapper,authenticated_actor=actor)`.
Это immutable/idempotent событие `capture.reviewed`; `capture.finished` и raw
outcomes не меняются, обучение не запускается. Одобрение записи с unknown
outcome не превращает её в успешный BC expert. Reject сохраняет оригинал.
