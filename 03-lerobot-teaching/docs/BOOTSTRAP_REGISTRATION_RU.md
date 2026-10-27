# 03: bootstrap из существующих источников

Отдельный вручную измеренный `registration.json` не нужен для реализации
leader → ROSMASTER. При доступной исходной калибровке и отсутствии explicit
observed-профиля `registered_native_leader_geometry` автоматически возвращает
`BOOTSTRAP_DERIVED`. Provenance:
`derived_from_existing_controller_and_lerobot_calibration`.

Переиспользованы `SourceCoordinates`, `NativeLeaderContext`,
`RelativeLeaderGeometry`, исходный direct Pi→Nano route из `1f62ded` (уже предок
main), cache единственного `SO101Station` и штатный native stream. Второго
servo bus, камеры, FIFO, feedback loop или собственного моторного исполнителя нет.
Homebrain не становится частью realtime-контура.

Исходная leader-калибровка проверяется по
`_registry/so101-calibration-manifest.json` (SHA9512a1d0…). Homing offset уже
применён устройством и повторно не добавляется. Encoder delta преобразуется
в радианы по документированному new-calibration midrange; source URDF находится
в `shared/robotics/so101`, target URDF и точный `servo_coordinates.py` —
в `01-autonomous-learning/src/legacy`. Исходные файлы сохраняются без изменений.

Bootstrap предполагает согласованные оси двух URDF base frames. Это **условие
установки, а не измеренная ориентация**. Каждый clutch/recenter заново связывает
текущую leader-позу с текущей принятой командной оценкой ROSMASTER; translation
не требует измерения внешней рулеткой. Оценка target не становится measured_state.
Для выбора установки предусмотрен маленький metadata-файл
`native-leader-bootstrap.json` с единственным полем `frame_convention`:
`aligned_urdf_base_axes`, `source_base_yaw_plus90`, `source_base_yaw_minus90`
или `source_base_yaw_180`. Неизвестные значения отклоняются. При необходимости
владельцу достаточно проверить «leader вперёд = ROSMASTER вперёд».

Захват предварительно использует официальную нормализацию LeRobot:
0=закрыт,100=открыт →170…30° штатных target-команд. Метрическая апертура,
контакт и усилие не выведены. Существующая acquire-only проверка выравнивания
сохраняется: несовпадающий начальный захват не вызывает абсолютный скачок.
ACTIVE mapper, integer serialization, исходные rate/lease/Ruckig остаются прежними.

Реальный сохранённый leader cache выявил shoulder_lift −1.80286758rad при
номинальном CAD minimum−1.74533rad. Подтверждённое encoder-значение не обрезается
и не масштабируется. Bootstrap использует существующий
`URDFChain.forward(enforce_limits=False)` только для **source observation FK**,
а превышение0.05753758rad явно сохраняет в диагностике. Target FK/IK по-прежнему
проверяет свои пределы; normalized source вне исходного контракта отклоняется.
Это не изменение аппаратных motor limits и не утверждение точности CAD-позы.

Для offline-проверяемого proposal без записи на устройства:

```bash
python scripts/bootstrap_native_leader.py \
  --calibration /path/to/existing/so101_leader.json \
  --output /new/path/derived-registration.json
```

Это экспорт proposal, не установка observed-профиля и не команда роботу.
Повреждённый explicit observed-профиль не заменяется молча bootstrap.

UI различает bootstrap и observed declaration, показывает SHA/provenance и
предварительное направление захвата. Три прежних observed capture остаются
необязательным уточнением. Bootstrapping не выдаёт USER grant, не делает HOME,
не изменяет controller feedback и само не запускает mirror.

Программные проверки:55Python PASS/8.58s,15UI PASS/182ms и финальная preview-проверка1PASS/0.17s; CPU1/MemoryMax1GiB/swap0. Проверены исходные source
границы, SHA mismatch, corrupt profile, четыре base rotation, repeated recenter,
command estimate, исходная gripper admission и ранее существовавшие native/epoch
guards. Реальный calibration SHA совпал с live Pi status; один cached sample
дал source FK с явным CAD excess. Это **не changing-input continuity PASS**.

Минимальная отдельная физическая приёмка владельца: неподвижное шасси, текущая
наблюдаемая сессия, малые вправо/влево, вперёд/назад, вверх/вниз, затем wrist
и захват; при необходимости поправить объявленную ориентацию/знак/zero. Полные
измеренные углы/апертура нужны для научного measured-профиля и точности.
До существующего real changing-input continuity PASS новый physical runtime03
не развёрнут. Текущий native reference409 остаётся отдельным фактом; агент
его не восстанавливает движением и не выполняет эту приёмку самостоятельно.
