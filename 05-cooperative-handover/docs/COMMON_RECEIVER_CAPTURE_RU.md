# Общий guarded capture получателя05

Candidate f807a98: прежний COMMON cooperative05 grant теперь используется тем же
StationCaptureService, что и source04 demonstration. Нативный recorder/камеры/loop
не дублируются, frozen Station не меняется. Root выбирает explicit common profile,
проверяет current observed intent/actor и точное source binding; затем передаёт
DATA permission provenance в typed /so101-capture/record/*, без control calls.
Source повторно проверяет actor/skill/session/epoch/object/input и current native
mode/epoch непосредственно под original Station lock перед writer start.

Старый standalone NativeMetadataRecorder legacy consumer сохраняет контракт;
Root05 не откатывается на raw /station/record/* при старой source capability.
Finish/stage адресуют только known recording и authenticated capture actor;
UNKNOWN не становится успехом всего опыта. Запись не создаёт grants/teleop/policy,
не продлевает lease и не означает measured hold/transfer.

80tests/17.34s PASS: demo04+cooperative05 consumers, leader/policy original writer
on fake caches, actual localhost authenticated Node HTTP, raw bypass rejected,
foreign/late/expired/changed session/actor/Spec/native epoch/input cases. Начальные
2fixtures failures сохранены. Это component proof, NOTdeployed и не весь05.
[Evidence](../../_registry/evidence/experiment05-common-receiver-capture-20261028.json).

Далее durable source-start intent/explicit lost-ACK DATA reconciliation, whole
manual/manual flow/UI и paired learning; прежние physical gates остаются OPEN.
