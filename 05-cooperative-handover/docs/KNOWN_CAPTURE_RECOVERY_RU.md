# Known DATA start и reconciliation

Candidate559e913, NOTdeployed. Root сохраняет immutable intent/уникальный attempt
в source context до record RPC. Оригинальный grant — буквальный parsed source
ответ; boot/clock/оба calibration hashes DATA хранятся отдельно. Это не новая
permission, feedback или measured pose. Один metadata writer берёт flock до
изменения workflow recovery. Pending/unknown intent блокирует следующий start.

Shared `source_capture_ack.validate_capture_start` теперь служит обоим consumers:
04 source_demo_capture и05 cooperative_capture_starts. Проверяется known actor/
body/permission/epoch/boot/calibrations/real original recording+receipt. Текущий
чужой control owner никогда не заимствуется. Возвращается DATA identity, не grant.

Явный POST `record_reconcile` принимает только session_id/expected_epoch; actor
приходит из authenticated session. Активный camera-recorder получает новый
reconciled sidecar, прежние start bytes не переписываются. После STOP/restart
только известная source-запись завершается UNKNOWN; protocol/control не оживают.
Чтение snapshot ничего не reconciles. UI показывает ошибки native-потоков,
DATA pending/unknown и отдельную кнопку без наблюдательных/control галочек.

Frozen targeted128PASS33.91s +276NodePASS2.76s. Fake node/clock/camera/control
fixtures only: lost reply AFTER writer, restart, foreign actor/context, original
bytes retained, no duplicate start, fsync failures, single writer, stopped UI.
[Proof](../../_registry/evidence/experiment05-known-capture-recovery-20261028.json).
Initial2 lost-ACK fixture failures retained: factory patch did not replace captured
consumer callback. Corrected test actually loses response; no product safety
assertion weakened. Whole05/physical transfer/learning/release remain OPEN.

Current candidate7ea604d also handles known receiver finish: durable finish intent,
shared exact sealed-manifest validator, lost response read-only ACK without second
finish, explicit pending DATA cleanup, immutable rejected replies and checksum
fences. Original outcome/bytes stay intact. UI/API now ALSO pin server-issued
`capture_attempt_id`, and reconciliation holds existing record-IO lock. Historical
559 body above was two fields; it was not deployed.

Persisted inactive camera pointer can be retired after explicit successful DATA
recovery: raw manifest snapshot and independent receipt retained; FSM stays
INTERRUPTED/STOPPED. Test simulates persisted active metadata, not actual SIGKILL.
159frozen targetedPASS42.04s +278NodePASS2.54s.
[Finish/restart proof](../../_registry/evidence/experiment05-receiver-finish-recovery-20261028.json).
Giver recovery/whole05 flow/learning/release and physical transfer remain OPEN.


## Giver native9 — candidate cbd82a1, NOT deployed

Тот же durable helper теперь обслуживает fixed roles receiver/giver; API принимает
optional `role=giver` вместе с exact server attempt. Receiver default сохранён.
Original NativeCaptureService/Writer02/03 остаётся единственным producer; shared
pure normalization не меняет original JSON. GET добавляет context SHA/clock/boot.
Read-only reference preflight выполняется до durable intent и source start.
Unique attempt +actor помещаются в source skill_context; grant/owner не заимствуются.
Native9 finish асинхронный: сверяется существующий sealed manifest и SHA исходного
`sources/outcome.json`, а не provenance-enriched labels. Lost ACK/restart не дают
управления. Incomplete ACK/failures сохраняются; own validated cleanup UNKNOWN
может завершить raw запись. Обе known source попытки должны быть terminal перед
retirement старого operational camera pointer.

Frozen cbd82a1: **192PASS49.41s**, all **279NodePASS2.591s**, CPU3/4G/swap0.
Это targeted qualification, не full05 release. Both02/03 original file writer,
Unicode, foreign context/clock/boot, lost start/finish before/after source write,
restart, exact role/actor/attempt и fsync failures проверены на fake входах.
[Machine proof](../../_registry/evidence/experiment05-giver-data-recovery-20261028.json).
Live HB976/Pi+NanoOWNc516 не менялись; physical/master OPEN. Продолжить whole
manual/manual backend/browser flow, paired learning и full scoped release.
