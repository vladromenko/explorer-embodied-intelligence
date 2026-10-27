# Точный старый Pi4c lifecycle: условный zero-write и HTTP-контрпример

Исполнен отдельный private harness на Homebrain, без устройств/TCP/RPC/Torch.
Git source `4c5f4f5b0fe4e37a8a66bf3643f0f696c0232db4`: целиком загружены старые
NodeAgent, SO101Station, observed provider и PiNativeRetarget. Исходные методы не
переписывались. Station SHA `2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885`.
Текущие product/runtime/profile/drain/binding файлы не менялись.

## Что именно исполнялось

Original `Station.start → _connect → _work → close`, включая исходный файловый
writer и disconnect(disable_torque=False). Feetech bus/Motor/calibration и camera
заменены fake external dependencies; serial-методов/камеры устройства нет.
Native peer — fatal-on-call sink. socket/urlopen запрещены. Native retarget worker
работал в IDLE и закрывался своим точным4c close. Исходный COMMON revoke выполнен
без текущего grant; физический observed test не создавался.

`NodeAgent.close` вызван из точного старого класса. Spy вокруг original
Station.command считает локальные вызовы STOP, не заменяя его тело. Статус idle
проверялся после настоящего fake `_connect`/первого исходного цикла измерений.

Original `make_handler().do_POST`, token authentication/body parser/station route
и `AgentHTTPServer.process_request/server_close` исполнялись с BytesIO/DelayedBody
и fake listener socket. Реальный TCP server не открывался. Это проверка handler/
daemon lifecycle, не сетевой latency/transport или аппаратная квалификация Pi.

## Полученные исходы — все шесть assertions PASS

| Случай | Goal/Torque | Локальный STOP | Native RPC |
|---|---|---|---|
| Точно idle → original Node.close | 0/0 | 1 | 0 |
| Active → close | close делает 1 measured Goal hold, torque не меняет | 1 | 0 |
| Ошибка camera в idle worker | 1 Goal hold attempt, write возвращается | 1 при последующем close | 0 |
| Та же ошибка, fake bus отклоняет write | 1 реальная попытка, возврат не заявлен; last_hold_error сохранён | 1 | 0 |
| Pending authenticated POST после завершённого Station.close со старым epoch | HTTP400, 0/0 | 1 | 0 |
| Pending authenticated POST до Station.close, но после server_close | HTTP200: 1 Goal+1 Torque acquire, затем 1 close hold | 1 | 0 |

Active setup сам включает исходный `_enable_hold` отдельным fake start; в таблице
active-close отделён от этих setup writes. Никакой fake hold-return не называется
attainment, безопасным реальным удержанием или MCU ACK.

## Конкретная воспроизведённая гонка

1. Source station точно idle, writes0. Authenticated POST уже вошёл в старый
   handler и блокируется при чтении body с текущим expected epoch.
2. Настоящий старый `server_close()` возвращается: handler daemon и не drain-ится.
3. `Node.close` выполняет COMMON revoke и входит в `lab_vision.close`; fake join
   останавливает только этот участок close для детерминированного interleaving.
   Station.close ещё не вызван, старый epoch по-прежнему текущий.
4. POST дочитывает исходный body. Empty-grant legacy admission пропускает его;
   original `teleop/start` делает Goal_Position/current-position+Torque_Enable,
   принимает baselines и новый epoch. Ответ HTTP200.
5. Продолженный Node.close вызывает original Station.close и measured hold.

Итого idle snapshot+listener close не устанавливают source admission barrier.
Отдельный stale-after-close случай HTTP400 подтверждает, что original CAS
действительно работает; дефект нельзя описывать как «любой поздний POST проходит».
После уже состоявшегося Station.close его смена epoch защищает старый запрос.

Idle worker exception показывает вторую независимую границу: даже без active
control оригинальная обработка ошибки пытается measured hold. Полный all-faults
zero-write teardown нельзя вывести из нормального idle случая.

## Минимальная внешняя граница для будущего Root deploy

Без изменения frozen Station возможен только conditional maintenance protocol,
а не обещание «restart всегда без writes»:

1. Уже действующий trusted ingress должен охватывать **все** источники control к
   старому Pi owner: прямой LAN endpoint, proxy, persistent/keep-alive connections,
   pending body и уже разобранные запросы, включая COMMON/retarget mutations.
   Центральная disabled кнопка или закрытие listener этого не доказывают.
2. Перед shutdown запретить новые acquire/start/recenter/action/authorize/lease
   entries и получить свидетельство **нулевых in-flight admitted control entries**.
   Старый4c owner такого счётчика/latched shutdown API не предоставляет. Ожидание
   N секунд или число всех threads не заменяет это свидетельство. Connection
   read timeout10s не является общим абсолютным drain deadline для keep-alive/
   частичного тела. Уже начатый command нельзя отозвать внешним закрытием TCP.
3. Root задаёт конечный maintenance deadline, не меняя motor lease/watchdog.
   При nonzero/unknown in-flight, изменении actor/epoch/mode/grant или ошибке
   runtime проверка **прерывается**, прежний owner/profile сохраняется. Deadline
   не разрешает kill/force-close с последующим утверждением zero-write.
4. Только под удерживаемой ingress границей проверить exact idle/no current
   grant/direct03 attempt/no recording/no fault, затем использовать условный
   idle lifecycle. USER global emergency STOP не блокируется ради upgrade:
   его появление переводит unattended attempt в отменённый/наблюдаемый сценарий.

Такой ingress/drain **не реализован и не подтверждён для живого4c** этим harness.
Поэтому первый unattended Pi upgrade остаётся **OPEN**. Минимальная честная
альтернатива — отдельный явный USER-observed первый upgrade с сохранёнными
исходниками/калибровками и Root review, а не скрытый control вызов агента.
Новый source-only profile/drain/binding принадлежат Root и проверяются отдельно.

## Воспроизводимость и границы

Private executable Python:
`/mnt/explorer-data/explorer-work/04-old4c-lifecycle-20261027/harness.py`.
Launcher/cgroup samples, full old sources, traces и result сохранены рядом в
`run-a/`; SHA/команда/лимиты записаны в JSON evidence этого checkpoint.
Повторять только с **новым** `EXPLORER_OLD4C_PROOF_OUT`: existing case directories
не перезаписываются. Отрицательные controls — ожидаемые опасные ветви fake bus,
а не физические эксперименты. Все имена портов/профили/токен synthetic.

Исполнение Homebrain Python3.12, fake внешние drivers. Это не проверка реальных
USB/serial faults или Pi Python3.13 binary/stdlib, не actual TCP qualification,
не full factory/main/server startup и не право на реальный STOP/HOME/grant.
Relative imports служебных helper разрешались из текущего shared namespace;
полный4c dependency closure не заявлен. Старые тела исполненных close/command/
worker/handler методов, напротив, byte-pinned исходниками git4c.
Source04 software-ready и physical acceptance этим документом не закрываются.
