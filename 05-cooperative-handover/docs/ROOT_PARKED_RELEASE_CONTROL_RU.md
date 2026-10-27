# Явная Root transaction отпускания

`CooperativeParkedRelease(service)` не делает I/O. Service связывает
`Workflow.prepare_parked_release` с `prepare(row, after_receiver_command_id)`
и `wait_parked_release_control` с `wait(row, descriptor)`. Последний только
ждёт RAM completion; он не подтверждает Inbox, не забирает bundle и не управляет.
Действующий ACTIVE path возвращает `None` и сохраняет прежнее поведение.

Для PARKED giver prepare проверяет свежие source scopes, исходную неизменяемую
Root grant lineage, оба armed interlock и receiver direction. Фиксированный
authenticated source challenge сохраняется immutable receipt, затем Inbox
выдаёт три отдельных вопроса. NX deadline считается от начала запроса и
исходного literal source TTL ≤2 s. Часы Nano/Pi/NX не отождествляются.

Единственная CONTROL операция `native_parked_release_resume` принимает ровно:

```json
{"session_id":"server-session","expected_epoch":"server-epoch",
 "release_bundle_id":"32hex opaque one-use choice",
 "affirmations":{"receiver_holds_object":true,"giver_stationary":true,"receiver_stationary":true},
 "observing":true,"neutral":true,"user_observed_test_confirmed":true}
```

Actor, source IDs, CAS, park token, model, Spec, constraint, q и clocks приходят
только из серверных original receipts. CONTROL резервирует именно ожидающий
closed checkpoint, сохраняет literal три consumed факта и вызывает существующий
source `resume-release`. Он возобновляет original owner с прежней lease; цели
руки, предсказание модели, автоматический attach/lease retry не выдаются.
В рамках ЭТОГО явного CONTROL связывается scoped installed-grant adapter того же
вернувшегося владельца. Обычные четыре phase операции поведения не меняют.

Actual parsed owner запоминается до artifact fsync. Completion становится
доступен Workflow лишь после проверки полного source response, immutable custody,
повторной проверки current grant и освобождения reservation. Source grant
используется буквально: повторного `guard/authorize` нет. Исходные native
issue/expiry остаются неизменными; Source время проверяется по его boot/domain.
Policy opening может позднее пройти только существующий executor и его
публикационный guard; CONTROL сам открытия не выдаёт.

STOP сначала локально отменяет transaction/будит waiters, затем вне Root/helper
locks отменяет actual returned owner и отдельный declared cancel-only attempt.
У последнего scope взят из actual challenge ДО RPC; это не returned-owner receipt
и не доказательство dispatch. Более поздний GET никогда не назначает новый
epoch/generation. Чужая новая authorization/owner не останавливается. Дубли
cleanup могут вернуть honest already-stopped CAS, без заявления physical STOP.
Поздний parsed ответ сохраняется orphan original, но не публикует completion.
Ни STOP, ни revoke не размыкают durable interlocks.

Проверки используют только temp Store, authenticated loopback Node/Bridge/FastAPI,
реальный original worker с fake bus/Publisher и существующий Inbox. Покрыты
manual/policy, фактический Workflow wait, tamper/epoch/actor/expiry/replay,
late reply, fsync, lost reply, foreign new USER, reservation wakeup и WAL failure.
Это программная квалификация; физическое удержание, отпускание, контакт,
стационарность и full handover не подтверждены. Root fresh per-role dispatch
gate и реальная USER physical qualification остаются отдельными требованиями.

Итоговая targeted regression: **100 PASS / 140.62 s** (transaction, существующий
Workflow bridge, unattached STOP, Service, installed-grant adapters и frozen
Station/numbering). Отдельные шесть exact original factory/forward cases вместе
с исходными adapter cases: **20 PASS / 29.183 s**. Проверки provenance и
отсутствия Python `continue` прошли. Station SHA остался
`2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885`.
Оригинальные первые negative logs сохранены: один старый actor propagation
сбой закрыт отдельным Root ABI `fa140b5`, два setup-assertion negatives поправлены
в тестах. Эти проверки не доказывают общую восстановимость Root session `_save`
при любом отказе storage; это отдельная открытая граница.
