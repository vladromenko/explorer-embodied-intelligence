# Кэш реальных датчиков Nano для общего ROSMASTER workflow

`shared/explorer_shared/native_sensor_cache.py` — чистый sink/reader без открытия
ROS-узла, serial, камеры, publisher, настройки контроллера или firmware readback.
`nano_camera_cache.py` дополняет **существующий OWN CPU ROS-cache node** подписками
на уже работающие `/odom_raw`, `/imu/data_raw`, `/scan0`, `/scan1`, `/battery`,
`/camera/depth/camera_info`. Второй аппаратный владелец не создаётся. Новая версия
требует обычной интеграции/deploy родителем; разработчик её не запускал на Nano
и не перезапускал службы/контроллер.

## Подключение родителем

```python
from explorer_shared.native_sensor_cache import NativeSensorCache
cache = NativeSensorCache(
    agent.state_dir / 'nano-camera-cache' / 'sensors',
    clock_domain=agent.delivery.clock_domain, boot_id=actual_native_boot,
    camera_root=agent.legacy_bridge.root / 'data',
)
writer = NativeCaptureWriter(..., observation_provider=cache.observation_provider)
```

Уточнить собственный фактический `--state-dir` camera-cache unit, если он отличается.
OWN cache CLI принимает `--node-id nano` — именно ID observer/native tap, без `:`.
Он задаёт **ярлык** одного kernel clock, не синхронизирует разные часы. Режим
`--check` по-прежнему ничего не подписывает и не пишет. Параметр NodeAgent и новые
роуты здесь не изменялись.

API reader:

- `.refresh()` / `.status()` читают только own `index.json` и ограниченные файлы;
  наличие index не означает наличие всех каналов или готовность робота.
- `.snapshot(cutoff_ns)` выбирает последние реальные исходные сообщения с
  callback-receive <= завершения файла <= начала native call. Cutoff должен быть
  прошедшим моментом того же clock/boot. По умолчанию максимальный возраст500мс.
  Нет подстановки «последнего сейчас» для старого dispatch и нет интерполяции.
- `.observation_provider(native_receipt, frame)` подходит существующему
  `NativeCaptureWriter`: возвращает `{channels, source_payloads, diagnostics}`.
  `native_receipt.call_started_monotonic_ns` — cutoff, `stamp` обязан содержать
  тот же native boot/domain. Каналы: реальные odometry_pose/base_velocity/imu,
  выбранный `/scan0` (можно настроить `/scan1`), voltage power_context, а при
  доказанном совпадении RGB — camera_depth и частичная camera_calibration.

Оригинальные ROS CDR bytes и полный JSON сообщения сохраняются в SHA-bound batch.
NaN/±Inf в JSON обозначаются отдельными тегами; оригинальный CDR сохраняется
без изменений. В архив также передаются gzip-байты исходного index, с SHA
распакованного документа. Recorder далее сохраняет их своим fsync-механизмом.

Базовые ограничения sink:10Гц на topic,64 записи/topic,8МиБ journal,2МиБ batch,
512КиБ CDR; pending ограничен. Seen/throttled/error counters явные. Это история
**выбранных callback**, не обещание записать каждое сообщение100Гц. В одном
существующем cache node `.flush()` вызывается100мс. Старые own batch после
перезапуска не маскируются под новый boot/поток; удаляются только точные собственные
`batch-<SHA>.json`, чужие файлы не затрагиваются. Исходный контроллерский
`status.json`, settings и исходники не переписываются.

## Семантика и ограничения

- Odom velocity берётся из `Odometry.twist`, а не command/gate velocity. Сохраняются
  исходные odom/child frames и pose. Нулевые реально измеренные скорости валидны;
  они не превращаются в false mask из-за неподвижной фазы. Wheel odometry не
  объявляется доказанной глобальной локализацией.
- IMU acceleration/angular velocity имеют явные единицы и frame. Quaternion
  сохраняется только если присутствует, конечен, нормирован и ROS covariance не
  объявляет его отсутствующим (`-1`). Нулевая covariance ROS означает неизвестную
  погрешность; это не доказанная абсолютная ориентация/heading.
- Лидар: сохраняется **весь опубликованный массив** с прежними индексами/углами,
  оба scan источника раздельны. Это не обещание360°/невидимых лучей/общей карты.
  Invalid beams = null + per-ray mask + причина, при этом `raw_ranges` и CDR
  содержат исходные0/NaN/Inf/вне-range значения. Invalid не означает свободное
  пространство. Трансформы лазеров не угадываются. До поддержки per-ray partial
  массива в canonical admission запись остаётся честно partial (mask ranges=False),
  даже если полная реальная исходная сетка уже доступна для последующей обработки.
- K/D — частичная калибровка. Camera→base/depth→RGB extrinsics и uncertainty
  остаются null/maskFalse, пока нет независимого действительного артефакта.
- Depth становится доступен только при точном совпадении выбранного JPEG SHA,
  native boot/domain, цветового/depth callback clocks и hash реального NPZ;
  проверяются shape/type/source stamps/frame. Сохраняются native frame ID и
  wrapper frame ID раздельно. Если JPEG callback был throttled, а RGBD pair новее,
  pair **не** присваивается старому кадру. Raw depth0/invalid pixels не меняются;
  valid/invalid counts и правило маски явные. Exposure alignment не заявляется.
- ROS header sec/nanosec/frame сохраняются отдельно. Их не переводят в Nano/NX
  monotonic по стеновым часам или догадке; callback time не является экспозицией
  и не доказывает верхнюю границу sensor/DDS latency.
- Физическая готовность, разрешение движения и успешность навыка всегда отдельны.

## Реальные read-only свидетельства 2026-10-26

Только пассивные временные ROS-подписки на действующие publishers. Никаких
publish/control POST/изменений файлов Nano/сервисов/прошивки не было.
Приватные исходники свидетельств:
`/mnt/explorer-data/explorer-work/nano-sensor-readonly-20261026/`:
`passive-topics.json`, `passive-cdr.json`, `qualification.json`.
Последний — локальный replay сохранённых реальных сообщений в временный cache,
не доказательство cross-host mapping или уже выполненного deploy.

Фактически подтверждены raw Odom, IMU, battery, depth CameraInfo, оба LaserScan.
В первом probe lidar0/1 по500 опубликованных лучей,470/463 в допустимом диапазоне;
исходные0 ниже range_min=.05 сохранены invalid. IMU содержит acceleration/angular
и near-unit quaternion, covariance all0 (неизвестна). ROS header приблизительно
на0.34–0.36с впереди host callback wall — поэтому исходный старый status.json
называет их CLOCK_FAULT и хранит старую odom. Новый read-only cache использует
реальную callback receipt mono, не переопределяя оригинальный controller gate.
За6с не получен `/camera/depth_to_color`; наличие имени topic само по себе не
даёт матрицу. Ни camera→base extrinsics, ни timing alignment не выдумываются.

### Дополнение: завершение метаданных и K (после ревью)

Нельзя выдавать callback receive за завершение вычисления/файла метаданных.
OWN writer теперь пишет `camera-cache-publication.json` и
`rgbd-cache-publication.json` **после** atomic replace соответствующих metadata.
Отдельный receipt содержит точный metadata SHA, payload SHA, native boot/domain
и время завершения. Raw-file reader требует этот receipt до native call cutoff;
содержимое metadata не объявляет само себя уже завершённым файлом.
CameraInfo callback и окончание проверки K также имеют отдельные реальные
monotonic stamps; K с отсутствующей/будущей временной привязкой остаётся
неподтверждённым до обновления OWN writer.

Root `Frame.native_metadata_bytes` имеет более сильную привязку к точному
завершённому чтению NodeAgent (`node_cache_read_completed_not_exposure`). Такой
пакет используется со своим `Frame.monotonic_ns` вместо позднего файла, даже если
последний перезаписан или companion уже вытеснен новым кадром. Будущий момент
завершения чтения отклоняется. Это не утверждение времени экспозиции.
