# Phase park и successor05: исходная граница без нового TTL

ENGINEERING_GLUE. Это отдельный source metadata слой. Исходный `Station.py`,
motor/controller settings, original30Hz, action goal duration и USER leases не
изменяются. Нет модели, второго servo/camera owner, heartbeat или target writes.
В текущем main source metadata подключён к fixed authenticated service/API/UI
через одноразовый server choice; старые owner IDs выводит только сервер. Live
release883 ещё не включает этот слой. Последующий explicit phase activation
остаётся отдельной незавершённой работой.

Исходный policy action живёт по прежнему deadline. Ветви простого policy TTL и
camera loss переходят в idle и вызывают измеренное удержание, но не публикуют
достаточного receipt. **Idle или прежний last_command не являются доказательством
удержания.** Новый источник принимает ровно два фактических основания:

1. Original COMMON scoped STOP завершился; `Station.last_command` содержит
   `hold_current_position`, настоящий seq−1, requested=dispatched, новую idle
   epoch и returned stamp. Retired previous grant хранит
   `EXPLICIT_USER_SCOPED_STOP`; preceding command связывает прежнего владельца.
2. Existing publication gate сохранил `last_measured_hold` после возвращения
   original measured-position write: exact previous grant/epoch → current idle
   epoch, actual measured/returned stamps и настоящий q6. Для этого случая
   создаётся явно помеченный reference **view**, sequence=null. Старый
   `Station.last_command` не переписывается и не объявляется этой публикацией.

Выбранный физический control path уже остановлен. Hardware position mode
удерживает исходную измеренную позу без повторения команд. Это не доказывает
контакт, удержание предмета, attainment старого policy target или успех фазы.

## Source API

```python
dispatch('successor', {
    'grant_id': new_explicit_user_idle_grant,
    'expected_control_epoch': current_idle_epoch,
    'interlock_id': pinned_interlock_id,
    'expected_revision': pinned_revision,
    'previous_grant_id': pinned_previous_owner_grant,
    'previous_control_epoch': pinned_previous_owner_epoch,
    'observing': True,
    'user_observed_test_confirmed': True,
}, authenticated_actor=server_actor)
```

Все ID source/server-issued; браузер не выбирает q, timestamps, источник hold,
calibration или inline receipt. Wrapper сам читает только существующие caches.
Fresh USER grant обязан быть новым и относиться к тому же actor, provider
process, boot/domain, calibration, SkillSpec, task/run/object/input binding.
Его показанный source frame принят **после** actual hold return. Текущая cached
measured6 после этого write должна точно равняться returned hold6 и удовлетворять
существующему observation freshness500ms. Tolerance не угадывается. Изменившаяся
pose, epoch или старая measurement закрывает successor; новый grant этого не
исправляет без настоящего нового основания.

Receipt `explorer_station_cooperative_successor_receipt_v1` сохраняет исходный
hold, предыдущую reference/command, old/new owner и monotonically incremented
revision. `retained_armed` остаётся прежним; pending прежнего opening challenge
очищается. Source-owned lineage хранится в том же fsync WAL внутри frozen
metadata и source events: original origin + initial reference + до32 явных
successions. Ни timer, revoke, ни свежая камера не снимают latch.

Пока source epoch и actual receipt остаются прежними, следующий явный USER
grant может принять **то же** sealed hold при новой проверке fresh measured6.
Это продление наблюдаемого metadata-владения, а не motor lease. Не создаются
новые команды и не устанавливается физическая authority. Потеря source process/
boot/epoch не становится продолжением прежнего hold; restart сохраняет запрет.

Парковка запрещает **все** последующие target writes, включая точный прежний
target и новый/unowned epoch. Original measured STOP holds остаются в штатном
пути. Generic bind/arm не снимают парковку; для дальнейшего движения требуется
отдельный будущий explicit USER native phase transition. Safe-object reset
остаётся отдельным USER действием под idle. Все мутации идут через прежний
pending/final WAL, CAS и порядок Station → publication → provider locks.

## Последующий lifecycle Root

1. Явный USER bounded phase вызывает существующий native action с прежним lease.
   Host publication не объявляется завершением движения или grasp.
2. До human prompt оператор явно паркует фазу через scoped original STOP; Root
   сохраняет настоящий returned measured hold. При опоздании/TTL без receipt
   состояние остаётся BLOCKED, а не «удерживает по idle».
3. Review/record и independent evidence могут ожидать без motor commands.
4. Новый displayed-frame USER grant и metadata successor закрепляют наблюдаемый
   hold. Root должен различать parked observed hold и running dispatch owner.
5. Продолжение — отдельный USER transition с native epoch handoff и сохранённым
   latch. Этот атомик его не включает и не вызывает begin/resume/lease/predict.
   Ранее созданные model outputs/grants не оживают от парковки.

Для giver потребуется аналогичный command-estimate stopped receipt: настоящий
original STOP, завершение pending publisher, actual Tap arm/base receipts и
same-boot reference guard; ROSMASTER measured feedback не включается. Нельзя
подменять его журналом или idle flag. Полный phase executor/held activation,
sourcegiver successor и Root UI пока остаются отдельными незавершёнными A.

Проверки: actual ORIGINAL Station worker/STOP и COMMON с fake buses; direct STOP
и gate-denial hold, immutable original reference/history, fresh USER ownership,
foreign actor/task/Spec, stale/changed pose/epoch/image/calibration, повторное
явное наблюдение того же hold, WAL failure/restart, cache-only target denial и
revoke во время pending WAL. Аппаратные RPC и физические тесты не выполнялись.
