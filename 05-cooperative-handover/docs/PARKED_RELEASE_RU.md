# Parked release: отдельный запрос доказательств и USER resume

Новый source путь не ждёт человека внутри активного native lease. Метаданные
`phase/release/challenge` доступны только при armed healthy parked context:
manual `PARKED_NEW_USER` либо policy `BEGIN_CLUTCHED`, с тем же USER, Spec/run/
object/authorization, реальным drained original stream, actual issued9/base0 и
валидным original reference. Command-estimate остаётся estimate, feedback=null.

После возврата **настоящего source challenge** Root должен создать три новых
вопроса: receiver_holds_object, giver_stationary, receiver_stationary. USER
подтверждает именно их после получения challenge. Нельзя создавать consumed proof
из старого checkbox/click, принятого до native challenge. Срок challenge остаётся
исходным2s: можно явно запросить новый, но автоматических refresh/retry нет.

`phase/resume-release` — отдельный CONTROL. Он принимает только server-derived
IDs/CAS и consumed same-actor proofs. Private source hook в исходном resume
сверяет unchanged origin/current grant/epoch/generation, сохраняет actual resume
как history, затем атомарно устанавливает opening grant под новой guard revision.
Старые challenge bytes и issued/expires timestamps сохраняются без перенумерации
и без начала нового2s интервала. Native epoch policy уже был выдан при CLUTCHED
begin и не меняется при resume. Обычный phase/resume по-прежнему удаляет pending
challenge/grant и не разрешает раскрытие.

Используется тот же original resume с lease0.45s, Publisher, first5 stationary
boundary и latest-intent executor. Arm target и раскрытие этот вызов не отправляет.
Только последующая actual opening publication может пройти прежний guarded path,
если proof и original native lease ещё действуют. STOP/revoke/fault/expired final
fsync не возвращают current grant и не останавливают чужую новую эпоху.

WAL fsync находится вне control locks. Новые source contexts сохраняют literal
issue snapshot stamp отдельно от стабильных actual arm/base/reference SHA: время
нового чтения не считается изменением позы. Grant не вкладывает полную цепочку
старых grants рекурсивно; actual before-arm и returned base0 provenance сохранены.
При рестарте grants не восстанавливаются, parked barrier требует USER recovery.

Проверки используют настоящий durable OperatorEvidenceInbox и fake original
native executors, в том числе три input mode, старые/подменённые facts, исходный
expiry, revoke внутри resume, final fsync/lease expiry/foreign-owner cleanup,
fixed authenticated source API и original factory bootstrap с9 adapter hashes.
Control-center release orchestration/UI для этого нового transaction ещё
подключаются отдельно; весь H5 и физическая передача не объявлены завершёнными.
