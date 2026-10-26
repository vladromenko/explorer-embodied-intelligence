# 03: исходный заводской поток вместо конечной очереди

Новые `explorer_shared.native_stream03`, `native_stream03_api` и
`native_stream03_client` расширяют **существующие экземпляры** ManualArm и
ManualTeleop внутри заводского web-процесса. Они не создают ROS node/publisher,
serial/servo owner, finite plan/start на каждый кадр или вторую периодическую
петлю управления. Синхронизацией, ограничениями и проверкой траекторий продолжает
владеть исходный Ruckig-поток. Firmware, readback и настройки не меняются.

## Проверенные исходники и реальная граница API

26.10.2026 исходники Nano прочитаны по SSH и сохранены приватно. SHA256 проверяет
`audit_factory_binding(source_bytes)`; несовпадение требует нового ревью.

| Исходник | SHA256 |
| --- | --- |
| src/manual_arm.py | 8ddadb774fb166878d8856974bcea3c075ad8014af7f3c28247cea0e0b8ecdb8 |
| src/manual_teleop.py | 809c465dae4d919f71e5e83e6bd111480bdb697f69552104a488519f85873ac1 |
| src/servo_coordinates.py | 6dbfbcaa238b04e2ddb61e08d43f1ebee250e8bfd8ca273d83ff2e1008df6b2f |
| src/arm_velocity.py | ee5300c7731b47806201e5509e8cad69e275f4e87812fb79d91964638159fd7c |
| config/arm-motion.json | 8ab3bc35a2d88f78050c720d1af8c97dcfffb500f20fcc04fee6a95a8dd4ca3a |

Настоящий ManualArm.stream_velocity принимает joint_velocity[6] в deg/s,
xyz_velocity[3], generation, valid_until, callable permit, owner и coordinated.
Он заменяет один последний intent, а не накапливает position delta. Исходные
period=.04, lookahead=.08 и остальные ограничения передаются без изменения.
Исходная ManualTeleop продлевает lease на .45 только по операторскому update;
новый адаптер не увеличивает это время. Ранний другой конечный gateway имел
свой отдельный ABI; его lease нельзя переносить на этот поток.

Заводской HTTP `/api/teleop/input` сейчас принимает только keyboard keys.
Он не принимает произвольную raw joint velocity и не поддерживает новый источник
SO101 автоматически. Поэтому новый API должен быть смонтирован **внутри того же
web-процесса**. Импортировать/создавать новый ManualArm из observer-процесса нельзя.
Приведённый код до такого монтажа не является работающей удалённой аппаратной
интеграцией. В исходной системе ничего не перезапускалось.

## Один владелец руки и базы

NativeTeleopMux.install() под исходным teleop.lock оборачивает методы конкретных
существующих объектов. При неактивном 03 ручное управление идёт исходным путём.
В 03 остаётся один native owner=keyboard: leader задаёт руку, keyboard задаёт
только базу. Его пакеты не затирают latest arm intent. Исходный native tick и
Ruckig worker остаются единственными периодическими исполнителями.

Начало требует выбранного keyboard owner, STOP и наблюдаемой нейтрали.
Clutch/recenter обнуляет intent, а продолжение явно вызывает исходный resume.
Старые epochs/generations, нарушенный порядок source_sequence, STOP и смена
owner не могут восстановить прежнее управление. Leader-пакет **не продлевает**
операторский lease. Для latest leader intent сохраняется deadline, который уже
задала исходная ManualTeleop; постоянные keyboard heartbeat не удерживают старую
скорость после потери leader. Истечение требует нового recenter, без автозапуска.

Запрет владельца на шасси применяется до исходного base dispatch; это политика
ввода, не настройка контроллера. По умолчанию chassis_motion_allowed=false.
Все тесты с разрешённой базой используют только mock callbacks, без оборудования.
Режим зеркала не требует включения записи или обучения.

## Геометрия

RelativeLeaderGeometry использует настоящие исходный и целевой URDFChain,
измеренный leader → зарегистрированное physical q → source FK → относительный
clutch → target IK. Существующие функции servo_coordinates.to_radians/to_servo
передаются из исходного модуля: q1=rad(90-servo1), q2..5=rad(servo-90).
Их нельзя заменять равенством индексов source/target.

Нормированные положения SO101 ещё требуют подтверждённого отображения физических
осей/нуля/масштаба и отдельной gripper-кривой. Сам факт наличия LeRobot calibration
JSON не доказывает все эти координатные соответствия. При отсутствии регистрации
возвращается UNREGISTERED. Тестовая synthetic-регистрация не применяется к железу.
Anchor использует текущую заводскую **оценку команды**, не measured q. После IK
разница с этой оценкой переводится в latest velocity на исходном native period;
сам Ruckig сохраняет исходные velocity/acceleration/jerk/limits/collision guards.
Недостижимый target возвращает отказ и нейтральный intent, без конечной очереди.

## Квитанции и 9D

NativeDispatchTap — ограниченный in-memory журнал после возврата исходного
`_publish_command`, base drive/release/STOP callbacks. В критическом пути нет
fsync, камеры или сети. Он различает принятие HTTP/intent и реальную публикацию
на исходной host-границе. Даже такая квитанция имеет
hardware_dispatch_verified=false, measured_q=null/maskFalse,
attainment_measured=false, contact_verified=false. Она не доказывает передачу
MCU, положение сервопривода или удержание объекта.

После наличия настоящего arm и base command context сохраняется общий вектор
arm6+vx/vy/wz. Его смысл явно current_native_command_snapshot_after_this_publication.
components_published_in_this_event и отдельные component_receipt_ids указывают,
какая часть действительно отправлена в этом событии. Вектор не выдаётся за одну
атомарную аппаратную публикацию всех девяти каналов. До известного контекста
неизвестные координаты остаются null с покомпонентными масками; запрет движения
шасси сам по себе не создаёт доказательства нулевой base-команды.

Связь с leader intent восстанавливается по точным исходным native generation и
input_received_at. Даже если публикация успела произойти до возврата stream call,
последующий since() добавит точную связь из intent-каталога; часы Pi и Nano при
этом не сравниваются. Неразрешённая связь остаётся null. Переполнение кольца
помечается gap; ошибки записи — capture_failures. Потребитель не может назвать
такой интервал lossless или экспертным без отдельной проверки.

Новый capture должен читать эти квитанции и существующий Nano JPEG cache с
исходными stamp/hash/boot, сохранять полный 9D и common downstream SkillSpec,
input_adapter=so101_leader, phases/outcomes/splits. Указатель импорта согласован
как embodied_manifest, original recording_id = envelope.episode_id. Сам этот
коммит не создаёт законченный canonical capture: исходный архив прежнего
ARM_ONLY/host-intent recorder сохраняется analysis-only и не переименовывается.

## Монтаж для ревью интегратором

Только после проверки исходных SHA, резервной копии исходного web.py и отдельной
оценки startup-пути, в том месте, где **уже существуют** manual_arm и
gamepad_panel.teleop:

```python
from explorer_shared.native_stream03 import (
    AUDITED_FACTORY_SHA256, audit_factory_binding, NativeDispatchTap, NativeTeleopMux,
)
from explorer_shared.native_stream03_api import mount_native_stream_api
from arm_commissioning import coordinated_status

binding = audit_factory_binding({name: (ROOT / name).read_bytes()
                                 for name in AUDITED_FACTORY_SHA256})
if not binding['exact_audited_source']:
    raise RuntimeError('Factory source changed; review before mounting')
tap = NativeDispatchTap(boot_id=manual_arm.boot,
                        clock_domain=EXISTING_NANO_NODE_CLOCK_DOMAIN)
mux = NativeTeleopMux(gamepad_panel.teleop, manual_arm, tap,
                      chassis_motion_allowed=False)
mux.install()
mount_native_stream_api(app, mux, token=TOKEN,
                        observed_test_permit=lambda: False,
                        reference_reader=lambda: manual_arm.reference(coordinated_status))
```

EXISTING_NANO_NODE_CLOCK_DOMAIN берётся из действующего observer node ID и того
же boot_id; не придумывается и не приравнивается часам Pi. Разрешение исполнения
по умолчанию false, пока нет отдельного разрешённого наблюдаемого теста. Прежний
глобальный STOP остаётся независимым. Новый API проверяет тот же приватный Bearer
token дополнительно к исходному middleware. LegacyBridge проксирует только
перечисленные новые explorer/retarget пути, сохраняя token внутри Nano.

GET: status, reference, dispatch. POST: begin, recenter, resume, input, base,
stop. Prefix `/api/explorer/retarget/`; Pi NativeStreamClient ходит через
аутентифицированный node `/legacy/explorer/retarget/`. Повторный start или старая
session-команда отвергаются. Полученный accepted=true не является dispatch.

Rollback исходного монтажа — восстановить **точную сохранённую** web.py,
проверить её SHA и согласованно перезапустить исходного владельца только при
доказанном отсутствии startup-движений либо отдельном наблюдаемом тесте. Не
перезапускать MCU, не трогать настройки и не создавать другой publisher.

## Startup и выполненные проверки

Фактическая factory-arm-startup.enabled=true запускает поток из конструктора.
В первые180 секунд полного boot он может послать pose90×6 на4000ms после8s delay.
На момент аудита uptime10484s делал этот конкретный путь неактивным, но это не
доказательство нулевых actuator writes всех остальных startup owners. Поэтому
живой hot-swap/restart не выполнялся и не объявляется безопасным без проверки.

12 source/mock тестов проверяют owner mux, leases, потерю leader, STOP/старые
epochs, ограниченные drops, точную позднюю correlation, genuine native-host
receipts, chassis ban, HTTP auth/default execution block и настоящее URDF FK/IK.
Кроме того, выполнен **только численный replay** исходного arm_velocity.py в
фактическом Nano `.venv/bin/python3` с установленным Ruckig0.15.3:180 шагов,
38 изменившихся endpoint, затем settled=true; исходные period.04/lookahead.08 и
config SHA до/после равны. ROS и устройства не импортировались, servo owner не
создавался, физические команды=0. Подробности — соседний JSON receipt.
