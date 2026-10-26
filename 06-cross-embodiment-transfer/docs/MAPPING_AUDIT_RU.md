# 04: вычислимые причины, физическая регистрация и источник навыка

Аудит относится к прежнему `blocked_mapping` из `POLICY_RUNTIME_VALIDATION.json`, а не объявляет весь04 завершённым. В том запуске `mapping_profile={}` давал «URDF mismatch» и «named contract mismatch» просто из отсутствующего профиля. Фактическая несовместимость этих данных этим не устанавливалась.

`explorer_shared.transfer_mapping04` использует только общие контракты/кинематику и переданные исходные артефакты, без imports experiments, robot.connect или motor writes. Оригинальные calibration bytes прочитаны read-only по SSH Pi и совпали с реестром: follower `da0032a29b7c0206b80df8aa3ada2eff8efb18e9f1a990126a5c83cfbdc66354`, leader `9512a1d08ca9f4a8fcd55ac230df5fcf2636e3e1661e9c44ea0ba68a4b8de78c`. Private cache остаётся вне Git. Контроллеры/регистры не опрашивались и не менялись.

A закрыто для mapping/preview/simulation/shadow:

- Реальный SHA URDF и точные6 source names/units привязываются автоматически.
- Native normalized position→encoder ticks воспроизводит фактические чистые методы установленного LeRobot0.6.1. Degrees используют `resolution-1=4095`; gripper имеет отдельный0…100 контракт. Сохранённый homing offset не добавляется второй раз: register Present_Position уже использует исходную аппаратную калибровку.
- По реальным range min/max вычислены radian delta scales пяти joints; quantization error отражён отдельно. Официальный pinned README определяет zero новой URDF как середину joint travel. Это nominal CAD convention для preview/SIM/shadow, не поддельные физические reference pairs.
- Wrist roll0…4095 — намеренный full-turn special case официальной calibrate(), а не повод растягивать значения на URDF limits. Выход за CAD limits отклоняется без clipping.
- Target servo mapping совпал по SHA с неизменённым native `servo_coordinates.py`: q1=rad(90-servo1), q2…5=rad(servo-90). Конвертер отдельно проверен против исходных функций.
- Уже сохранены target open30/empty-close170 и направление закрытия; они не дают metric aperture/force. Сохранён hand-eye camera→arm4 transform для stationary completed-command кадров, joints1…4 и исходная погрешность≤5.62mm/1.684°. Это знание используется в своём scope, не расширяется на движущиеся кадры или неподтверждённый current mount.

B остаётся только там, где нужны реальные дополнительные наблюдения: unit-specific URDF axis/zero agreement; source/target metric jaw aperture без выдуманного force; current camera/object/goal registration за пределами уже подтверждённого stationary scope. Эти требования не останавливают разработку SIM provider/reference. Они будут закрываться web wizard с capture/quality/retry/IDs, без ручного JSON/терминала и без движения до отдельного наблюдаемого теста.

C: прежний physical source checkpoint — engineering hold,4 recorded predictions; это не closed-loop object skill. Наличие processors и mapping не повышает его до candy→box. До нового квалифицированного06 object policy development использует только явно обозначенный SCRIPTED_SIM fixture, без learned-source/physical-success claims.

Actual audit/coefficients и SHA исходников: `MAPPING_AUDIT_20261026.json`. API: `SourceCoordinates(original_bytes,expected_sha256)`, `.encoder`, `.arm_encoder_delta_radians`, `.nominal_urdf_radians`, `.profile`; `TargetCoordinates(registered_original_source_bytes)`; `audit_known_mapping(...)`; `write_audit(...)`. Все триггеры physical execution остаются false. Пять tempfile/pure tests проверяют exact installed official arithmetic, отсутствие double homing, source SHA, named bounds, native factory roundtrip, immutable artifact и сохранение B/C.
