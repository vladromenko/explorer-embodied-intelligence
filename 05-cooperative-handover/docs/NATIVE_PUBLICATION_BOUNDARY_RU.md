# 05: native policy и проверка выпуска у исходного издателя

Это source-only реализация с подставленным издателем в тестах. Реальные команды, перезапуски исходного владельца, прошивки и настройки контроллера агентом не выполнялись. Физическая передача и замещение endpoint ROSMASTER остаются отдельными наблюдаемыми проверками.

`native_cooperative05.NativeCooperativeOwner` монтируется в **тот же** фабричный процесс, с теми же экземплярами ManualArm, ManualTeleop и исходного ROS Publisher. Прокси делегирует существующему `arm.pub.publish`, не создаёт издателя, устройства или дополнительного servo worker. Поэтому проверка охватывает исходные `_publish_command` и HOME того же ManualArm. Движение вне этого издателя не объявляется квалифицированным.

Исходные Ruckig period `.04`, lookahead `.08`, native lease и factory hard limits не меняются. `action9` остаётся `[6 servo deg, vx,vy,wz]`. Для текущего stationary-сценария ненулевой хвост модели отклоняется целиком, а не заменяется нулями. Реальный neutral base receipt возникает только из исходного drive(0) при **явном пользовательском begin** или из реального policy input. Metadata bind, observation, challenge, authorize и конструкторы ничего не публикуют.

Модельный target преобразуется в latest velocity `(target-current_command_estimate)/original_streaming.lookahead_s`, затем проходит прежние factory geometry/Ruckig/limits. Это input adapter к существующему потоку. Измеренная обратная связь не включается; provenance называется `policy_prediction_not_measured_leader`. 03 measured-leader endpoint не может заменить отдельно авторизованный policy adapter. Предсказания не продлевают наблюдательную аренду, истечение требует прежнего explicit recenter/resume.

## Наблюдаемый запуск

1. Пользователь выбирает исходный keyboard owner в neutral/STOP и получает прежнюю camera-pinned observed authorization с `input_mode='cooperative_policy'`. Ни один service constructor не выбирает owner и не нажимает resume.
2. POST bind закрепляет `run_id`, `object_id`, `coordinator_epoch`, `authorization_id`, `observing=True`. Это метаданные. Для ручного giver допустима его исходная keyboard/browser_gamepad observed authorization; защита выпуска общая.
3. Явный пользовательский begin дополнительно передаёт `neutral=True`, `model={model_id,checkpoint_sha256,spec_sha256}`; после него явный resume. Только begin вызывает исходный neutral drive(0). Движение не запускается из metadata/записи/shadow.
4. Root predictor получает source observation и **её точный JPEG**. Native owner хранит последние 8 immutable pins; source ID/SHA/monotonic/boot/reference должны совпасть при input. Камера повторно не открывается. `command_reference9` доступен только при реальном известном base receipt, иначе None.
5. Только свежая фактическая наблюдательная аренда и серверный pinned model provider разрешают input. Отдельный `/lease` вызывается пользовательским observing-клиентом; predictor/запись его автоматически не вызывают.

## API существующего владельца и observer proxy

Все JSON routes требуют Bearer auth и не принимают query parameters:

| Native route `/api/explorer/cooperative/…` | Метод / назначение |
| --- | --- |
| `status` | GET, capabilities/состояние/guard/reference fault |
| `observation` | GET, immutable source observation ID + исходный command estimate/real base receipt |
| `observations/{id}/frame` | GET, точный JPEG, SHA/FrameID/NativeMono/Boot headers |
| `bind` | POST, только метаданные наблюдаемого сеанса |
| `begin` / `recenter` / `resume` | POST, explicit existing-owner transitions, точный control_epoch |
| `lease` | POST, только текущий наблюдающий оператор; никакого auto heartbeat |
| `input` | POST, `action9,source_sequence,command_id,source_observation,model` + binding/control_epoch |
| `guard/arm` | POST, заморозить giver arm при входе в совместное удержание, текущий original reference |
| `guard/challenge` | POST, native одноразовый challenge **до** нового операторского подтверждения receiver, с `after_receiver_command_id` |
| `guard/authorize` | POST, точный challenge + настоящий consumed operator inbox receipt; доверенный Root проверяет causal association |
| `guard/revoke` / `stop` | POST, отзыв; не HOME/open/release |
| `guard/reset` | POST, под original STOP, новый current auth, `observing=True,object_safe_no_dual_hold=True` |

Node JSON proxy: `/legacy/explorer/cooperative/…`; JPEG: `/native-cooperative/frame/{observation_id}`. Allowlist фиксированный; произвольный upstream path/query отсутствует. Путь actual dispatch остаётся `/legacy/explorer/retarget/dispatch?after_sequence=N` с прежним ограниченным cursor.

## Независимое удержание и часы

Перед совместным захватом `guard/arm` требует stationary giver: последующие изменения первых пяти joints запрещены этой фазой задачи, открытие клешни требует отдельного допуска. Это не изменение motor limits. Native challenge живёт ровно существующий hold TTL FSM, отсчитанный **от выдачи challenge по Nano monotonic**. Root создаёт/поглощает новый authenticated operator inbox receipt после получения этого challenge, с точными run/object/receiver/after-command. Время NX сравнивается только с NX; на Nano проверяется только native challenge deadline. Никакого приравнивания NX↔Nano часов и нового таймера автоматического выпуска.

`NativeGiverPolicyTransport` связывает эти реальные endpoints с `CooperativeWorkflow`. До prompts вызывает `prepare_release`; сразу перед submission проверяет новый consumed inbox receipt и получает native grant. Каждый открывающий вызов исходного Publisher должен увидеть этот grant, текущий native owner/lease/epoch/authorization. Challenge/receipt нельзя повторно использовать. STOP/revoke/camera loss/смена owner прекращают старое разрешение. Если три подтверждения заняли слишком долго, шаг завершается ошибкой; grant не обновляется автоматически.

HTTP input ACK остаётся acceptance. Transport ждёт actual native dispatch ring с точным command ID/run/object/epoch/model/target SHA и для release — **реальное opening publication с тем же grant ID**. Gap/timeout не превращаются в успех. Возврат Publisher не доказывает MCU application, достигнутый pose, силу или контакт. Ранее принятый bounded endpoint может закончиться после STOP; более сильная физическая гарантия не заявляется.

## Отказ и исходная reference

Фабричный ManualArm записывает command journal перед Publisher.publish. Поэтому отказ новой границы может оставить в journal **неизданный** target. Такой отказ отзывает старые mux intent/authorization только в памяти, оставляет dual-hold защиту включённой и ставит явный reference fault. Ни HOLD, ни open, ни HOME автоматически не отправляются.

После безопасного размещения предмета пользователь явно выполняет safe-object reset под STOP. Для именно этого fault требуется прежний user-observed factory HOME через существующий endpoint. Исходный completed same-boot reference должен иметь новый original SHA и `ends_monotonic` после fault; изменение JSON/SHA без нового успешного native reference недостаточно. После HOME потребуется свежая observed authorization. Это не включение feedback и не калибровка заново. Обычный 03 STOP вне cooperative protection сохраняет прежнее поведение.

Новые методы помечены `ENGINEERING_GLUE`/существующим `HANDOVER_VERIFIED_FSM`; новый научный метод и завершение всего master prompt не заявляются. Полная receiver-side защита и central assembly/квалификация конкретных реальных навыков учитываются отдельно; один FSM или этот источник не означает физически готовый 05.
