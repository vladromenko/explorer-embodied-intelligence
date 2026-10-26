# Обновление уже установленного same-owner source pin

Новый `explorer_shared.native_owner_repin` — local source helper. Он не делает
SSH/HTTP, не создаёт владельцев устройств, не перезапускает службу и не посылает
команды. Root отдельно владеет maintenance exclusion, staged source hashes,
полной startup qualification, web-only restart и пассивным ROS monitor.

`prepare_native_owner_repin(root, previous_prepared, output_dir,
pinned_shared_path=NEW_SHARED, baseline=CANONICAL_SIX,
expected_previous_plan_sha256=RETAINED_INITIAL_PLAN_SHA)` требует существующий
первоначальный `plan.json`, `web.original.py`, `web.patched.py`. Предыдущий plan
сверяется с независимо сохранённым SHA; factory original — с исходным WEB_SHA.
Все текущие original source modules и controller configs должны совпасть.
Прежний candidate повторно генерируется исходным patch generator и побайтно
сравнивается с текущим web. Изменить разрешено ровно один literal пути shared;
остальные original bytes и mount/preflight code сохраняются. Подготовка создаёт
`web.previous.py`, `web.repin.py`, `plan.json` в новом каталоге, без записи в root.

`apply_prepared_native_repin(root, prepared, runtime_guard=callback)` выполняет
file-only CAS. Callback читает **текущие authenticated GET** и возвращает:

- `native_status`: original `/api/explorer/retarget/status`;
- `cooperative_status`: original `/api/explorer/cooperative/status`;
- `capture_status`: own Nano `/native-capture/status`;
- `active_jobs`: текущие active authoritative job IDs; требуется пустой список.

Проверяются false execution permission, отсутствие authorization ID, active mux,
policy/dual-hold binding/grant/challenge, recording/inflight finish; STOP должен
оставаться latched. Callback должен закончиться за2s по локальному времени;
часы узлов не сравниваются. Затем прежний startup guard проверяет aged boot,
disabled autonomous-day, свежий MANUAL/STOP/zero velocity status и исходные
source/config hashes. Перед atomic file replacement ещё раз сверяются source,
конфиги и известный web. Этот условный preflight не заменяет maintenance
exclusion: пока идёт операция, пользовательские authorize/control запрещены
интегратором. Новый staged shared source отдельно квалифицируется целиком.

Откат `apply_prepared_native_repin(..., rollback=True, runtime_guard=callback)`
восстанавливает точный прежний уже mounted web. Если новый web не отвечает,
`runtime_guard=None` разрешает **только восстановление файла** после exact CAS и
source/config checks. Результат явно содержит
`file_only_rollback_without_runtime_observation=true`, `startup_guard=null` и
`service_restarted=false`. Ни эта операция, ни старый сохранённый preflight не
разрешают restart: перед ним Root заново выполняет все действующие idle/agedboot
guards. Никакого автоматического restart/HOME/authorize/движения здесь нет.

23 temp source/guard tests прошли, включая active grant/dual-hold/capture/job,
изменённый source, совместную подмену candidate+plan SHA, чужую позднюю запись
web, свежий boot и точный rollback. Ещё2 opt-in теста запускали **полную сохранённую
factory** с forward-pinned candidate и injected external drivers: aged-start
дал0 actuator requests, fresh-start отказал до создания owners. Это software
proof, без физического применения. SHA/receipt приведены в соседнем JSON.
