# 04: граница Pi release и точная семантика cleanup

Read-only дополнение к `REUSE_GAP_AUDIT_20261027_RU.md`, исходники `65ae9d0`
и сохранённый Pi baseline `4c5f4f5`. Код/оборудование не менялись, тесты и device
RPC не выполнялись. Station SHA в обоих снимках одинаковый `2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885`.
Это design review до реализации, не разрешение на restart или physical readiness.

## 1. Обновление 04 не должно активировать новый physical runtime03

Сейчас `NodeAgent.__init__` в режиме Pi безусловно создаёт
`PiNativeRetargetService.from_config`; `start()` запускает его worker. Поэтому
обычный whole-tree Pi update загрузит новые03 service/client, даже если центральная
кнопка отключена. Новые03 пути нельзя развернуть до реальной continuity PASS.

Рекомендуемый минимальный профиль будущего Pi release: **so101_source_only**.
Он должен на source boundary:

- не создавать PiNativeRetargetService/NativeStreamClient, не читать peer secrets,
  не запускать leader→Nano worker и не создавать второго camera/serial owner;
- отклонять fixed03 control/activation/calibration mutations до peer RPC с
  `CONTINUITY_QUALIFICATION_REQUIRED`; stale/другая вкладка обхода не получает;
- явно публиковать `enabled=false`/причину; допускается отдельный read-only view
  старых raw03 archives, без их повышения или исполнения;
- не выдавать disabled/no-owned cleanup за подтверждённый physical STOP.

Это пока предложение, соответствующего source-profile флага в текущем main нет.
Альтернатива — exact4c pin **всего import closure**03 с отдельным immutable package
и attestation; одного старого `pi_native_retarget.py` недостаточно из-за relative
dependencies. Source disable проще проверить, чем selective mixed runtime.

До такого release возможен только ограниченный central/data-only adapter к уже
существующему authenticated HTTP: status/recordings/manifest/download и literal
native metadata из record/stage. Он не открывает serial/camera и не добавляет
writer. Но он сам не создаёт source-local approval, новый COMMON demo-purpose или
source lifecycle capability. Такой промежуточный результат **не software-ready04**.
NX timestamp нельзя назвать source pause timestamp: для source intervals нужны
реальные Pi stage annotation stamps и hash-bound final source manifest.

## 2. Что действительно вызывает shutdown

В baseline4c и current source:

1. `NodeAgent.close()` вызывает COMMON revoke (metadata), lab worker close,
   PiNativeRetarget.close(), затем Station.close(), camera/delivery close.
2. PiNativeRetarget.close() только ставит CLOSED/stop events и join. Native HTTP
   STOP/lease/begin он не посылает. COMMON revoke также не вызывает native command,
   но active original worker при следующем denied publication может сделать hold.
3. **Station.close() всегда вызывает локальный `command('stop',{})`.** Внутри
   Station STOP `holding = mode!='idle' and bool(measurements)` под Station lock.
   При точно idle: `_end_session`/новый epoch, без `_positions`/`_write`.
   При active: настоящий measured follower read + Goal_Position hold write.
4. Original worker exception handler даже из idle пытается `_write(_positions())`.
   В `finally` он вызывает `disconnect(disable_torque=False)`, не default torque-off.
5. `AgentHTTPServer.daemon_threads=True`; `server_close()` не является доказанным
   drain уже вошедших HTTP handlers. Отдельный idle snapshot не исключает late
   teleop/start, который включит hold/torque между проверкой и teardown.

Следовательно, read-only код подтверждает только **условный idle no-position-write
путь**, не unconditional zero-write shutdown. Локальный вызов с именем STOP
нельзя скрывать или выдавать за отсутствие самого вызова. Штатный restart до
проверки Root не одобрен; kill/обход cleanup в этом отчёте не предлагается.

До restart нужен отдельный fake-only actual4c lifecycle proof: original Node/Station
с injected camera/bus/native sink; idle→close даёт ноль Goal_Position/torque/native
POST; active/exception negative controls показывают реальные hold попытки.
Внешний admission/drain (без Station edit) должен закрыть новые control entries,
отсечь уже вошедшие late requests и удержать exact idle/no-owner/no-inflight
проверку до завершения процесса. При невозможности обеспечить это restart остаётся
явным наблюдаемым USER этапом, а не тихой операцией deployer.

## 3. USER global STOP и auto-cleanup — разные ABI

`Node /station/stop` — global путь: **сначала** безусловно
`station_observed.revoke`, **потом** Station STOP/CAS, **затем** internal
`arm_retarget.stop()` с очисткой retained03 target epoch. Даже stale
`expected_control_epoch` не предотвращает уже выполненный foreign COMMON revoke.
Этот путь сохраняется для отдельного явного USER global STOP.

Автоматические blur/navigation/late-reply handlers не должны использовать этот
global путь. Они очищают только acknowledged returned owner через существующий
COMMON `command('stop')`: exact grant ID + binding + expected native epoch. Pending
start CAS не является ownership; foreign status приводит к local drop без POST.
Для legacy ungranted epoch нужен отдельный scoped boundary с CAS **до** revoke и
без direct03 global cleanup. Это изменение Node/provider boundary, не Station.

## 4. Legacy teleop и предлагаемый guarded demo binding

Сегодня прямой `/station/teleop/start` требует available+observing, current expected
epoch и camera **либо** `operator_present`. Если epoch не принадлежит COMMON,
Node пропускает этот legacy command; publication gate сохраняет unowned legacy
поведение (кроме глобального armed interlock). Это не новый source04 demo grant.

Original start выполняет `_enable_hold`: Goal_Position=current raw measured,
Torque_Enable только для выключенных моторов; затем свежие leader/follower
baselines и новый epoch. Каждая команда stable teleop остаётся
`baseline_follower + leader_now - baseline_leader` по всем6 каналам. Recenter
разрешён только в teleoperation, читает новые baselines/меняет epoch, не вызывает
`_enable_hold`. Не менять эту математику, 30 Hz, motor limits/settings или Station.

Предложение нового purpose: `source04_demo`, `input_mode='leader'`, canonical
native6 action contract, confirmed `skill_id/skill_version/spec_sha256`, server-owned
`source_session_id` и отдельный `demonstration_id` на попытку, `source_domain=PHYSICAL`.
**Нет fake model_job_id/checkpoint.** Existing source grant продолжает связывать
actor/displayed JPEG/boot/follower calibration/native epoch; leader calibration
проверяется source preflight из существующего профиля, а не browser numeric values.
Разрешены только explicit USER teleop/start, recenter и exact scoped stop; policy
start/action не разрешаются этим purpose. Никакой grant из camera polling,
constructor/render, auto-resume или нового controller TTL.

## 5. Approval gate нельзя оставить только в UI

SO101Station не emit events. Нынешний автоматический station_import создаёт
`app.station_proxy` сразу после record/finish, до Source approval. Камерный
`RecordingManager` emit `recording.finished(camera_only)` — app.ingest его не
импортирует. `retarget.recording.finished` отдельно автозапускает **03 legacy**
import; новый04 producer не должен использовать этот kind.

Новый04 typed catalogue/event/import profile должен требовать exact approved
archive/review SHA в worker и current cohort, включая delayed rejection. Текущий
Store `source_format='native_capture_v1'` намеренно принимает только02/03; source04
нельзя провести через него под ложным project/action9. Legacy station_import и
generic train_act артефакты сохраняются историческими/ограниченными и не становятся
full-task expert/model без verified whole-skill path.
