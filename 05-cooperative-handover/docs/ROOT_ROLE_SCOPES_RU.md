# Root: отдельный observation scope каждой роли

`service.observed_scopes` — `CooperativeObservedScopes` с интерфейсом:

* `refresh(row,spec,actor=...)` — только fixed authenticated GET источников;
* `snapshot(row,spec)` — cached preview, ноль RPC;
* `require_current(row,spec,roles=('giver','receiver'),actor=...)` — отдельный
  новый GET перед действием; unknown/mismatched/stale context отклоняется;
* `invalidate(row,role=None)` — только локальная отмена cache;
* `arm_role(row,spec,role,actor=...)` — explicit source metadata latch, без
  target/start/resume/lease, с immutable native-control custody.

Сначала проверяются исходный `observed_test_binding` и фактически архивированные
successors через `CooperativeGrantLineage`. Source Origin должен совпадать с
actual source response в immutable artifact, не просто с новым authorization ID.
Проверяются actor/run/object/epoch/input mode/Spec/model/constraint, source boot и
clock, живые original publication gates и текущие source owner/epoch/calibration.
Source command estimate не переименовывается в измеренное состояние, а measured
hold не объявляется grasp/stationarity/contact. Source и NX часы не сравниваются
как абсолютные времена.

Giver BEGIN_CLUTCHED принимается только с реально возвращённым Root-owned
epoch/generation, а не с произвольной новой эпохой из GET. Pi требует actual
sealed hold, неизменный native6, свежий measurement stamp и matching current
source grant/provider/calibration. Поздние source GET, чужие immutable lineage,
rehashed подмены packet/context/model/measurement, изменённый Source Origin,
artifact tamper, STOP и phase/checkpoint drift не становятся новыми полномочиями.

Cache фиксирует Root context, initial binding, successor entries, control ledger
head и protocol/pending/checkpoint. Его возраст максимум2s; для Pi он также
консервативно ограничен оставшимся observation deadline от начала NX запроса,
используя длительность source remaining interval, без clock conversion.
`cached_preview_only=true` и
`source_revalidation_required_before_native_dispatch=true` обязательны. Source
ошибка сохраняется как причина; render/constructor не повторяют GET автоматически.

`native_refresh` явно наполняет cache. Native action инвалидирует кэш своей роли
до RPC; общий STOP очищает его немедленно. Source cache не резервирует будущий
owner: physical action path обязан вызвать новый `require_current` и пройти
прежние native guards непосредственно на источнике.

`service._authority` сохраняет прежний
`explicit_user_observed_test_current` с BOTH ACTIVE semantics. Добавлены:

* `observation_scopes_current` — metadata preview каждой припаркованной роли;
* `observation_scope_proofs` — exact source envelopes и причины;
* `dispatch_authority_by_role` — preview: собственный current transport роли
  плюс current transport партнёра либо его проверенный parked observation scope;
* `per_role_authority_is_metadata_preview=true`, fresh source revalidation и
  independent stationary/hold proofs остаются обязательными.

Две припаркованные роли дают `dispatch_authority_by_role={giver:false,
receiver:false}`. Parked scope никогда не заменяет собственный moving transport.
Receiver direction profile и остальные прежние interlock/capability проверки
сохраняются. Compute warm/model loading не создаёт permission.

Для existing `receiver_at_object` guard callback `_arm_guard` поддерживает
припаркованные источники: fresh source check, receiver FIRST, затем giver.
Metadata receipts сохраняются до дальнейшего использования; каждому latch
повторно проверяется source scope. Ошибка после первой защёлки сохраняет её,
не unarm/reset/rebind. Старый active transport path сохраняет прежние guards.
WAL или artifact fsync не держат workflow/STOP lock. Поздний metadata receipt
архивируется без публикации новой authority; Source Origin/owner/history/usedproof
IDs остаются прежними.

Этот атомик не меняет Workflow policy/motion gates. Новая parked-release
bundle/CONTROL orchestration и её использование per-role checks подключаются
следующим отдельным этапом; полный H5/physical success не объявляется.

Проверки: реальный Root Store, source FastAPI, authenticated localhost Pi Node,
original finite Station worker с fake buses, реальные immutable source artifacts.
Положительные случаи обеих parked ролей и каждой отдельно движущейся роли;
semantic rehash/context/source/lineage/timing negatives, STOP во время GET и
artifact fsync, literal source cutoffs сохраняются отдельно. Ни одного real
hardware RPC/USER grant/motion/deployment или controller setting write.
