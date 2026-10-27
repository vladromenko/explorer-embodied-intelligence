# Общий Pi lab profile: software integration03/04

Candidate `so101_lab` сохраняет одного original SO101Station, его camera/cache,
COMMON provider, cold same-bus guard и source04 capture adapter. Вместо новой
реализации FK/IK/stream/recorder используется существующий PiNativeRetargetService,
NativeStreamClient, original-calibration bootstrap и общий Nano producer02/03.
`native_retarget` default остаётся совместимым, `so101_source_only` по-прежнему
вообще не создаёт03peer/client/worker.

Thin LabRetargetRuntime добавляет metadata физического gate к исходному status,
не переименовывает derived registration в measured и не заменяет математический
controller. Пока real continuity OPEN, start/recenter/lease/challenge/authorize
отклоняются до original peer entry. Статусы, data capture и optional measured
registration сохраняют исходную валидацию. Scoped STOP сохраняет original epoch
matching; global cleanup без retained native epoch не называется доставленным
STOP. Hardware hold не выводится из HTTP/API return.

CV/VLM admission получает только read-only source-claim predicate. При direct03
ownership/inflight/uncertain stop semantic submission и source04 native entry
закрыты; explicit owned STOP не блокируется. Для старых потребителей predicate
по умолчанию false. Исходный frozen Station не менялся, новые serial/camera/
recorder/trainer owners не создаются.

93Python PASS6.75s: actual Node localhost HTTP для source-only и lab, one Station/
camera, shared capture/cold guard, bootstrap status без false UNREGISTERED,
03physical rejects, legacy04 rejects, exact original global STOP body, both COMMON
consumers/source evaluation/cooperative, original03 fake peer scope/data/worker,
VLM/record/race/cancel/unknown-release/lock-order tests.
[Evidence](../../_registry/evidence/experiment03-lab-profile-contract-20261028.json).
Первый новый HTTP тест обнаружил false direct_source_stop_confirmed при no-owner;
исправлен только wrapper receipt, исходный stop не менялся; failure retained.

Это component software checkpoint, **не deployment** candidate profile. Actual
Pi ещё source-only9fc, HBef3f518. Следующие проверки: отдельный actual Pi3.13 fake-I/O
процесс с current full source/bootstrap и реальные read-only route/version checks,
затем frozen regression/scoped deploy. Current native reference409, changing
real leader trace/physical continuity и физический исход остаются OPEN.
