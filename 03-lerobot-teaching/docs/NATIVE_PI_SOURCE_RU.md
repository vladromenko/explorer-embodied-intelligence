# Pi leader → исходный native latest-intent

Актуальный стандартный cross-map путь — [web-мастер](NATIVE_LEADER_WEB_WIZARD_RU.md):
известная SO101 calibration/official normalization/new-URDF convention дают
вычислимые A автоматически; остаются наблюдаемые task axes и2 gripper captures.
Приведённый ниже generic measured profile — совместимость, его пять joint pairs
и private servo-source copy не требуются стандартному мастеру.

`PiNativeRetargetService` — новый default Pi NodeAgent для03. Старый
`PiRetargetService` оставлен только для исторических данных/тестов finite ABI.
Новый source никогда не вызывает plan/start/trajectory, не открывает serial,
не пишет follower и не создаёт второго владельца камеры. Независимый поток берёт
последнее измерение существующего SO101Station: один HTTP intent в работе,
промежуточные измерения объединяются, FIFO отсутствует. Повтор одного timestamp
не посылается. Ошибка/устаревший source переводит в INTERRUPTED без autoresume.

Рабочая цепочка: измеренный leader → зарегистрированные physical-rad
координаты → source FK → clutch relative pose → target IK → velocity к исходному
`ManualArm.stream_velocity`. Период берётся из actual native status, а не
назначается адаптером. Native Ruckig/lease/ограничения не изменены. Сами source
кадры lease не продлевают: только явный наблюдаемый `lease` оператора вызывает
native base mux. Default chassis prohibited допускает нейтральные base inputs;
свежий отдельный owner_chassis_test_authorized разрешает наблюдаемый тест на
исходном native executor. Факт нулевой target base берётся из native host receipt,
никогда из запрета движения.

Start требует существующего native STOP, наблюдения, реальной cross-map и
`execution_permission=true` исходного mount. Mount по умолчанию false до
отдельного user-observed test. **Start/recenter возвращают CLUTCHED**: измерения
leader ещё не вызывают intent, а resume и lease не генерируются worker. Сам
явный USER start выполняет существующий native begin с настоящим neutral base0
host request; это CONTROL, не чистый metadata preview и не измерение скорости.

Статус объявляет `source_hold_gate_schema=explorer_pi_native_explicit_hold_v1`.
Root должен требовать этот marker перед start/recenter/lease: старый Pi backend
с немедленным ACTIVE не эквивалентен новому held gate при смешанном релизе.
`operator_profile` выбирается в start: `ARM_ONLY` по умолчанию или
`MOBILE_MANIPULATION`. Он закреплён до конца попытки. Первый запрещает любой
ненулевой base intent даже при гипотетическом разрешении шасси; второй требует
текущего native разрешения. Оба сохраняют **тот же единый action9 contract**,
нейтральные каналы и общий Dataset/Skill/model; motor settings не меняются.

Первый явный свежий `lease` с `held:true, observing:true`, текущим source epoch
и возрастающим sequence должен содержать нейтральную базу. Он повторно проверяет
собственный сохранённый target epoch/boot/generation/USER authorization и текущий
measured leader owner/clock/calibration. Затем берёт свежую опорную пару
leader/reference, вызывает original native resume для **этого** target epoch,
передаёт нейтральный lease frame и лишь после этого переводит source в ACTIVE.
В CLUTCHED можно переставить leader; его новая поза на held edge не эхом посылается
на цель. Никакого resume, heartbeat или продления исходной0.45 lease от source
sample, poll, render или таймера нет. Отпускание кнопки завершает попытку; новый
held без нового явного start не возобновляет её автоматически.

Рецентрирование немедленно меняет source epoch, а native recenter меняет target
epoch и остаётся CLUTCHED до отдельного held edge. Запоздавший pre-clutch intent
не принимается, source sequence lease начинается заново для нового source epoch.
HTTP `command('stop')` требует **сохранённый своим клиентом** control_epoch или
expected_control_epoch. Отсутствующий/устаревший epoch не отменяет новую попытку.
Внутренний `stop()` для отдельного явного Node global STOP сохраняется, но он
отзывает только известный собственный target epoch, никогда не выбирает чужой
epoch из GET. Header global STOP исходного native owner — отдельное действие.
STOP ждёт уже начатый begin/recenter/first-held, затем отзывает и поздно созданный
собственный target epoch; новый start до этого запрещён. Потерянный begin reply
остаётся UNKNOWN, а не ложным «STOP доставлен»: чужой active owner не отменяется;
подтверждённый GET inactive может снять неопределённость без owner adoption.
Закрытие observer не посылает actuator POST и не генерирует lease.

`source_cache_claimed` отмечает retained target/lifecycle/step/pending/unknown
ownership; Root Node guard должен использовать marker для блокировки конкурирующего
personal-lab start, пока source использует общий measured cache (отдельный Node/UI
hook). `last_proposal` и `last_lease_receipt` раздельны,
чтобы очередной held frame не стирал FK/IK proposal. Timing ring последних64 HTTP
RTT и source-cache age измерен только Pi monotonic clock. Это API round trip,
**не** latency привода, контакт, stationarity или cross-device clock conversion.
Типизированная ошибка reference сохраняет original MCU/stale reason; отсутствие
measured target feedback само по себе не блокирует command-estimate.

Для03 достаточно единственного подтверждённого target camera stream на Nano.
Единственная Pi Logitech — необязательный отдельный вид leader/workfield,
не silent fallback и не синхронизированный автоматически второй target stream.

Private config `arm-retarget-peer.json` сохраняет прежние имена `nano_url`,
`nano_node_token`; секрет не коммитится. Новая отдельная private cross-map:
`native-leader-registration.json`, schema `explorer_native_leader_registration_v1`:

* `provenance: measured`, source/target URDF SHA256;
* `source_calibration_sha256` уже известного leader (не повторная калибровка);
* `source_joint_points`: пять имён SOURCE_NAMES[:5], каждое ≥2 соответствий
  `[leader_normalized, physical_radians]`, только измеренный диапазон;
* `leader_normalized_to_target_gripper_servo_deg`: отдельная измеренная кривая;
* `evidence_source_files_sha256`: relative private artifact → actual SHA256;
* рядом `factory-servo-coordinates.py`: точные исходные535bytes с SHA
  `6dbfbcaa238b04e2ddb61e08d43f1ebee250e8bfd8ca273d83ff2e1008df6b2f`.

Нет файла/полных данных → UNREGISTERED cross-map, исходная SO101 calibration
сохраняется. Числа осей/zero/scale/aperture не выдумываются и шаблон с фиктивными
физическими значениями не создаётся.

`/arm-retarget/{start,recenter,lease,stop}` сохраняет UI ABI; epoch передаётся
как `expected_control_epoch` для start/recenter или `control_epoch` для lease.
`record/start` принимает validated `skill_context` с project_id03 и вызывает
Nano `/native-capture/record/start`; stage принимает phase_id или stage,
finish передаёт точный typed outcome_evidence. Это тот же source producer и
канонический 9D контракт, что02. Новые IDs появляются только в typed Nano
`ready_recordings` с project_id/input_mode, старый Pi `/recordings` показывает
только прежние raw архивы. Импорт — `native_capture.import_native_capture`,
result pointer `embodied_manifest`; NPZ/state9 не фабрикуется.

Проверки: только localhost authenticated HTTP и mock-cache/native peer,
включая delayed frame, STOP during begin/recenter, stale source, coalescing,
общую record proxy и отказ от unknown factory source. Никакого физического
POST к роботам, движения или перезапуска исходного владельца не было.
