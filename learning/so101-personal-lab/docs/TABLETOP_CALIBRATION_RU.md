# Опыт06: ChArUco, настоящая геометрия и измерения

Новый `explorer_shared.tabletop_calibrate` решает intrinsics/distortion и pose по
метрической доске через OpenCV, а привязку к robot/base — по заданным измеренным
соответствиям. Capture, камеры, Hailo/NPU, serial и руки модуль не открывает. Источник
всех изображений — существующий original cached JPEG, без визуальных overlays.
Реальной доски и реальных board-to-robot измерений в этой квалификации не было;
действующая калибровка камеры/робота не создана.

## Печать

Готовые исходные артефакты в `learning/so101-personal-lab/calibration/`:

- `explorer-charuco-6x8-30mm-v1.pdf` — vector A4,210x297mm.
- Одноимённый SVG — тот же metric pattern, без внешних ресурсов.
- Одноимённый JSON — параметры/геометрия и SHA PDF/SVG/raster.

ChArUco6x8 squares, DICT_4X4_50, square30mm, marker22.5mm, current nonlegacy pattern,
border1. Доска180x240mm, центрирована на A4. Печатать100%, отключить fit-to-page.
Перед использованием физически измерить линию100mm и клетки; само наличие файла
не подтверждает масштаб принтера. Pattern создан OpenCV, затем точно переведён в
vector rectangles. PDF прошёл pdfinfo/render:1страницаA4, без JavaScript; рисунок
просмотрен. Реконструкция SVG в raster побитно совпадает с generateImage. Pi4.10 и
Homebrain4.13 дали один rasterSHA
`0bfa32e7e6ceaf87eb65c352ce2e1075d9c30ae19982ec157020659c1414731d`
(точное значение находится в JSON; строка source receipt — источник проверки).

## Сбор исходников и solver API

Использованы официальные [ChArUco Board/Detector и solvePnP](https://docs.opencv.org/4.x/df/d4a/tutorial_charuco_detection.html)
и [calibrateCamera / Brown-Conrady calibration](https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html).
`observe_charuco(frame, expected_frame_sha256=...)` реально декодирует исходный JPEG
и вызывает `cv2.aruco.CharucoDetector.detectBoard`. Он возвращает ID/corners и точный
frame SHA, не XYZ. Нужны не менее10 разных хороших board views с поворотами по двум
осям и охватом кадра. Повторённый frame/JPEG не увеличивает число views. Неудачные
views и причины видны отдельно. Старые cached frames не подменяются current preview.

`calibrate_from_cached_frames(frames, measurement_bytes, context,
expected_frame_sha256={frame_id:originalSHA})` выполняет полный JPEG→corner detector→
`cv2.calibrateCameraExtended`→reference `cv2.solvePnP`→метрическую rigid registration
и plane solve. Возвращает `CalibrationBundle` с exact `artifact_bytes`,
`source_payloads`, diagnostics; `bundle.validate()` использует прежний TableCalibration.
Ни один путь не пишет параметры камеры/моторов/контроллера. Source-only helper
`calibrate_from_synthetic_observations` всегда маркирует результат synthetic.
`source_provenance='synthetic'` также позволяет проверить всю JPEG pipeline на
отрендеренных видах; явные synthetic frames не получают measured provenance.

Context заполняет trusted server из **реальной конфигурации**, не из VLM или
непроверенной страницы: camera_id, robot_id, base_frame, mount_id; node clock_domain/
boot_id; calibration_id и явный valid_from_ns/valid_until_ns. UNKNOWN/missing identity
не подходит. Все views обязаны иметь эту камеру, размер и boot/clock. Изменение
mount/boot/размера требует новой калибровки, а не переименования старой.

## Документ реальных измерений

Нужен оригинальный JSON bytes schema `explorer_board_to_robot_measurement_v1`, units=m,
provenance=measured (для настоящих данных) или synthetic (для теста). Обязательны:

- camera_id/robot_id/base_frame/mount_id/clock_domain/boot_id, совпадающие с context.
- board_spec_sha256 и printed_board_pdf_sha256 именно выданного board artifact.
- reference_frame_id и reference_frame_sha256: **тот конкретный вид**, в котором
  доска была на измеренном месте. Другие views могут двигать доску для intrinsics.
- measured_by, measurement_method, printed_scale_checked=True;
  printed_ruler_length_m около0.100, printed_ruler_sigma_m; действительная ошибка
  печати и её неопределённость входят в итоговую uncertainty.
- board_to_base: массивы board_points_m и base_points_m, минимум4 распределённые
  неколлинеарные пары. Board axes/размер известны по pattern; координаты base нужно
  действительно измерить относительно указанного robot/base. Они не выводятся
  по виду руки, VLM, детекции bbox или номерам суставов.
- measurement_sigma_m, rotation_sigma_rad и измеренная board_thickness_m.
- table_up_direction_base — явное unit direction правильной стороны стола;
  workspace_polygon_base_m — измеренный выпуклый безопасный region стола.

Из этих пар Kabsch/SVD вычисляет rigid base_from_board без скрытого масштабирования.
Pose даёт base_from_camera, а board plane/up/thickness — base_from_table. Слишком
большие residuals, неверный printed scale, несовпадающие reference SHA, clock/boot,
плохое покрытие/tilt и невалидный region дают CalibrationBlocked, без артефакта.
Модуль не выдаёт выдуманную координату, если реальных измерений нет.

## Provenance и пределы результата

В artifact остаются source hashes original JPEGs, corners, board SVG/PDF/spec,
measurement JSON, mount identity context и fit results. Intrinsics RMS и pose RMS
сохраняются в pixels; registration/plane residuals — в m. Uncertainty явно включает
заданные measurement/rotation/print-scale terms и pixel propagation; это не
статистический confidence interval и не измеренная точность робота. Image quality,
mount stability и правильность человеческих измерений требуют отдельной проверки.

Артефакт совместим с TableCalibration, хранит validity и workspace polygon. Он даёт
калиброванную **плоскость размещения**. RGB/ChArUco не устанавливает высоту предмета,
силу, контакт или успешность захвата; existing `ground(...purpose='object')` требует
отдельную измеренную depth/geometry. `physical_execution_allowed=False` и
`real_robot_execution_tested=False` остаются false. Никакого автозапуска руки нет.

## Квалификация 2026-10-26

14 Homebrain tests: calibrated synthetic corners восстанавливают известные K,
distortion, camera extrinsics и metric plane, затем project/ground roundtrip;
проверена настоящая JPEG→CharucoDetector→solve pipeline на12 отрендеренных видах.
Ошибки SHA/clock/reference/scale/registration/plane, high residuals и плохой охват
отклоняются. Исходные изображения/документы остаются неизменными. Только synthetic
результаты; real camera calibration/physical placement не заявляются.

Read-only CPU-проверка на PiOpenCV4.10.0 действительно выполнила
calibrateCameraExtended по тем же synthetic points (без capture/NPU/serial):
RMS9.56e-6px, восстановлены fx≈700/fy≈710. Детали и input SHA сохранены в
`calibration/SOLVER_QUALIFICATION.json`. Рядом выдана печатная доска, а не
«готовая калибровка». Реальные cached board views, print/ruler check, measurement
JSON и независимая проверка на физической сцене остаются незакрытыми.
