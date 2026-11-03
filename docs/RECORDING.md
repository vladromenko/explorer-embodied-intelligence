# Запись и аппаратный контракт

`RecordingManager` работает на сервере и не зависит от открытой вкладки. Источник `synthetic_test` создаёт исключительно технические данные; его joints/actions помечены `valid=false`, а датасет `test_only=true`. Такие данные не доказывают исправность камер, шины или захвата и не входят в обычное обучение.

Рабочий процесс: создать датасет с произвольным отображаемым именем и текстом задачи → Start → End → Next. Re-record сохраняет отвергнутую запись в quarantine и исключает её из состава train. Stop завершает текущий непустой эпизод и закрывает собственные устройства. Abort сохраняет текущий эпизод как incomplete. Если достигнуто выбранное число эпизодов, сессия закрывается. Start того же датасета добавляет новую сессию; Next в паузе сохраняет текущий session_id. Число `51` интерпретируется как 51, дробные значения отклоняются.

Начало аппаратной записи требует `explicit_user_start=true`, проверки занятости всех четырёх устройств и общего flock `hardware`. Повторная проверка выполняется непосредственно перед каждым open. Для servo tty после открытия устанавливается Linux TIOCEXCL; он не позволяет другому обычному процессу открыть уже занятый tty. Старые контроллеры не обязаны соблюдать наши advisory locks; поэтому проверка `/proc` владельцев обязательна. В ходе разработки реальные устройства не открывались.

Сохранён самостоятельный snapshot motor calibration в `config/calibration_snapshots`, включая исходный путь, SHA-256, mtime и версию LeRobot. Он считается неприменённым до readback: при старте значения в моторах сравниваются со snapshot; несовпадение останавливает подключение до enable torque. Калибровка моторов и camera geometry — разные контракты. Top/wrist geometry и hand-eye остаются unknown: камеры перемещались, ранее полученный RMSE не переносится на эту установку.

Подключение не вызывает stock `configure()` или `calibrate()`: они меняют настройки моторов и могут повторять записи. Новый адаптер проверяет Position mode, отключает torque, записывает текущую измеренную позицию как Goal_Position и только затем включает follower torque. Leader torque отключается. Никаких автоматических записей калибровки, PID, acceleration, autofocus, exposure или white balance нет. После явного Start выбирается режим потока MJPG 640×480, 30 FPS; фактический размер проверяется. Значения camera controls не меняются.

Каждый action — один фактически переданный, квантованный Goal_Position, после пределов калибровки, software limits, delta и скорости. В sidecar сохранены raw ticks, ограниченные суставы, start/end отправки. Sync write не подтверждает получение каждым мотором: `action_acknowledged=false`, достижение позиции не выдумывается. Ошибка отправки завершает сессию; motion никогда не повторяется автоматически. Старый state (>500 ms) не допускается к движению. Stop один раз пытается снять torque, закрывает собственный bus и сохраняет ошибки закрытия.

Камеры читаются отдельными потоками. Для observation выбирается только последний кадр с host monotonic read-end <= завершения чтения follower state. Это причинная привязка, а не аппаратная синхронизация экспозиции: exposure timestamp отсутствует и явно invalid. Контракт хранит:

- state read start/end и state timestamp, отправку action start/end;
- отдельный sequence, timestamp и read interval обеих камер;
- lossless PNG из декодированного RGB, SHA-256 и относительный путь;
- reuse/drop counters, camera age, control lateness и writer latency;
- измеренный joint/gripper position в degrees/% calibrated range;
- velocity/current/load/force и неразмеченные phase/success/done/grasp = unknown/invalid.

Камера, задержанная более чем на 150 ms, потеря изображения, нечисловые суставы или переполнение bounded queue останавливают управление. PNG encoding/fsync выполняет отдельный поток; SAM не импортируется и не запускается в loop. При сохранении используются `.partial`, drain очереди, fsync PNG/frames manifest и atomic rename в `episodes`. Ошибки/обрыв сервера отправляют доступные данные в `quarantine`. Восстановление не трогает `.partial` живого writer (проверяются PID и Linux process start ticks).

Ограничения: host timestamps не являются временем экспозиции; USB/MJPG транспорт камеры уже может быть сжат с потерями. Поток сохранения не делает аппаратную систему hard realtime. Software joint limits не доказывают отсутствие столкновений с внешними предметами; требуется оператор, доступный физический STOP и контрольный медленный эпизод. Физический STOP кнопкой на Mac зависит от сети, не заменяет аварийное отключение питания.

Проверки: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_recording.py tests/test_recording_hardware.py` — 17 passed. Проверены тестовый источник, lifecycle/add/re-record, сохранность отвергнутых данных, hashes/causality, число 51, disk/busy/error/restart, quantized sent action, single send без retry, stale state и absolute measured state gates, snapshot provenance. Аппаратное подключение/torque/частота/реальная синхронизация не проверялись из-за владельцев устройств.

Первичные сведения: [LeRobot SO-101](https://huggingface.co/docs/lerobot/so101), [код SOFollower v0.6.1](https://github.com/huggingface/lerobot/blob/v0.6.1/src/lerobot/robots/so_follower/so_follower.py). Реальные API и особенности sync_write сверены также с read-only установленным исходным кодом LeRobot 0.6.1; runtime зависимости используют только независимую среду лаборатории.
