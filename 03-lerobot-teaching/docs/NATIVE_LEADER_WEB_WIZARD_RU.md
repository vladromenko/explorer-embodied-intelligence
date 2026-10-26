# Известная калибровка и web-мастер03

Стандартный путь больше не требует пяти заново снятых physical-radian пар или
копирования servo_coordinates.py через терминал. `NativeLeaderContext` читает
существующий calibration path из действующего SO101Station.configuration,
проверяет original leader SHA9512a1…de78c и использует общий SourceCoordinates:
официальную0.6.1 normalization→encoder арифметику, drive_mode,4095 denominator и
документированный new-URDF zero посередине travel. Homing offset повторно НЕ
добавляется. Wrist сохраняет полный range иπ/100 rad/native-unit; вне CAD limits
возвращается typed отказ, а не скрытое уменьшение диапазона. Исходная calibration
не переписывается, bus/robot/follower не создаётся.

Actual A audit — `NATIVE_LEADER_MAPPING_A_20261026.json`. Он вычислен на настоящих
исходных bytes уже откалиброванного leader, без подключений/команд. Повторно
используются bundled exact factory servo coordinates SHA6dbf…b6b2f и original
gripper reference SHAed275…e11cb:30° open,170° empty visually closed. Это команды,
не измеренная апертура, сила или контакт. Геометрия физической единицы/осей не
придумывается из encoder arithmetic.

До наблюдаемых B status по-прежнему UNREGISTERED, но содержит `A` с вычисленными
параметрами. UI должен показывать «исходная калибровка сохранена; арифметика
готова», а не просить повторную калибровку SO101. Source FK preview работает уже
на этапе A. Исторический generic measured-profile остаётся только совместимостью.

## Pi web ABI

Все операции authenticated POST `/arm-retarget/calibration/{operation}`;
для read-only status можно также брать retarget.status.calibration_wizard:

| operation | body / result |
| --- | --- |
| status | `{}` → A, remaining_B, captured, current_registration_sha256 |
| preview | `{}` → текущее измерение существующего leader cache и nominal source FK,0 commands |
| capture | `{kind,actor_id,observing:true,user_observed_test_confirmed:true,axes_and_orientation_confirmed:true}` |
| accept | `{observing:true,accept_observed_cross_map:true,expected_registration_sha256:status.current_registration_sha256}` |

kind: `leader_gripper_open`, `leader_gripper_closed`, `task_axes`.
`axes_and_orientation_confirmed` требуется только последнему. Две gripper
отметки берутся из реально текущего измерения leader, а target endpoints берутся
из проверенного original reference. Для task_axes дополнительно читаются только
GET native status/reference: нужен native STOP и действующая заводская оценка
после наблюдаемой исходной позы. Недействительный после fault reference остаётся
отдельным шагом; новый метод не посылает HOME и не включает feedback.

Capture сохраняет exact measured source/cache clocks, actor confirmation,
оригинальный cached Pi JPEG+SHA при его наличии и native command reference с его
независимым clock/boot. Нет clock conversion или утверждения measured target q.
`clutch_relative_end_effector_frames` — явная подтверждаемая оператором конвенция,
не выдуманная измеренная extrinsic matrix. Original SO101 normalized gripper не
превращается в метрическую апертуру/force.

Accept hash-проверяет originals, сохраняет только отдельные private appfiles
native-leader-registration.json/native-leader-evidence с raw calibration copy,
квитанциями и JPEG, SHA/fsync/backup прежней app-registration. Контроллерные6 JSON,
firmware/оригинальные calibration bytes не меняются. Новый profile имеет provenance
`calibration_derived_with_observed_taskframe`. Geometry reload меняет source epoch,
не начинает зеркалирование. Capture/accept сериализованы с lifecycle, новый start
не может обогнать accept; STOP/close не оживляются поздним ответом.

После принятия нужен обычный отдельный observed authorization/start. Физическая
квалификация task/contact/attainment этим metadata-мастером не объявляется.

## Наблюдаемый тест шасси

Авторизация NativeActivation дополнительно принимает явные
`input_mode: keyboard|browser_gamepad|so101_leader` и
`owner_chassis_test_authorized:true`. По умолчанию этого разрешения нет. Новые
motor limits, watchdog или lease не задаются; проверяется существующий native
lease.02 keyboard/browser gamepad остаются на исходном keyboard executor,
03 leader — на том же native mux. STOP/camera loss/revoke/generation change
снимают разрешение. UI checkbox не должен оставаться установленным после STOP.
Никакого теста движения агент не выполняет.

Только явный наблюдаемый native begin после neutral+STOP вызывает исходный
drive([0,0,0]) один раз. Это настоящая host-request квитанция для9D base context,
не synthetic zero из запрета движения и не доказательство достигнутой скорости.
Startup, challenge,authorize и calibration metadata не вызывают drive/arm.
