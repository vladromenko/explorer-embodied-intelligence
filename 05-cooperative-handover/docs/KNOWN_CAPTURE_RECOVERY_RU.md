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
