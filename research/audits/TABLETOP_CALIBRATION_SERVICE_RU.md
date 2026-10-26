# Веб-калибровка общего потока камеры

`LabVisionService` принимает дополнительные аргументы `calibration_dir` и
`calibration_context_provider`. Второй — доверенный callback без ввода клиента:
`{robot_id, base_frame, mount_id, robot_calibration_sha256?, camera_hardware_id?,
robot_hardware_id?}`. Все идентификаторы обозначают реальные известные сущности;
`unknown` не считается настройкой. Камера, её профиль, boot и clock берутся из
уже существующего владельца. Каталог — отдельный state_dir/tabletop-calibration,
не каталог исходной аппаратной калибровки. Старые config/calibration bytes не
перезаписываются. Без аргумента новый сервис выключен, прежний API совместим.

Сервис не открывает VideoCapture, VDevice, serial и robot executor. JPEG берётся
из `source.latest()` / опционального `source.frame_by_id()`. Статус удерживает
точный JPEG в ограниченном кэше; последующий capture связывается с его ID/SHA,
часами и boot. Кадр старше 2 секунд нужно обновить. Сегментация/Hailo не являются
условием обнаружения ChArUco. Неудачные распознавания сохраняются и не увеличивают
число хороших видов; повтор байтов не увеличивает покрытие.

## JSON-команды для существующего доверенного транспорта

Это внутренний API формы, пользователю JSON и терминал не нужны.

- `command('calibration/status', {session_id?})`: серия, revision, шаги по-русски,
  good/rejected views, текущий `current_frame`, issued PDF/SVG SHA, технические
  возможности OpenCV, `ready_to_solve`, `solve_blockers`, измерения и active SHA.
- `calibration/capture`: `{request_id:UUID, session_id?, expected_revision?,
  frame_id, frame_sha256, purpose:'intrinsics'|'reference', new_session?:bool}`.
  Первый request UUID становится ID серии. Повтор request UUID с тем же intent
  идемпотентен; иной intent отклоняется. Последующие изменения требуют revision.
- `calibration/solve`: `{request_id:UUID, session_id, expected_revision,
  measurements:{operator_id, reference_capture_id, printed_ruler_length_mm,
  printed_ruler_sigma_mm, measurement_sigma_mm, board_thickness_mm,
  base_corners_mm:[[X,Y,Z],...four corners...], printed_scale_checked:true,
  board_flat_on_table_confirmed:true, camera_fixed_confirmed:true,
  robot_base_fixed_confirmed:true}}`. Матрицы и неизвестные поля отклоняются.
  Возвращает QUEUED/RUNNING; status сообщает SOLVED/FAILED и подробную причину.
- `calibration/accept`: `{request_id:UUID, session_id, expected_revision,
  solution_id, solution_sha256, confirm_axes_overlay:true,
  confirm_mount_unchanged:true, expected_active_calibration_sha256:<status value>}`.
  Проверяет точную версию, неизменный context/profile/boot, свежий кадр и >=12
  общих углов доски на опорном месте (RMSE <=2px, максимум <=4px), затем атомарно
  связывает accepted event с active index. Синтетический результат не принимается
  как физическая калибровка. CAS исключает принятие из устаревшей вкладки.

`LabVisionService.calibration_board(format)` (`pdf|svg|json`) и
`.calibration_media(session_id,capture_id)` возвращают `(original_bytes, metadata)`.
`.calibration_preview(frame_id)` отдаёт показанный свежий JPEG из того же кэша.
Роутер должен отдавать их только по существующей авторизации, с фиксированными
MIME, без произвольного filesystem path. Preview metadata содержит точный SHA;
использовать обычный «последний JPEG» с чужим ID недопустимо.

`.calibration_reference()` — для серверного recorder: валидный calibration ID/SHA,
accepted lineage и текущий source-frame, без команды движения. Принятый artifact
немедленно используется `grid()`/`select()` и новым recording hook. Его валидность
ограничена текущим boot/profile/mount; автоматической смены boot в artifact нет.

## Что требуется в веб-форме физически

1. Скачать issued A4 PDF, распечатать 100% без fit-to-page, измерить линию100мм
   и толщину листа; указать погрешность линейки/измерений.
2. Двигать **только доску**: минимум10 разных хороших видов, углы/центр кадра,
   наклоны по двум осям. Камеру и роботов не двигать. Реальная достаточность
   разнообразия проверяется решателем, число10 само по себе не гарантирует fit.
3. Положить доску на стол. В интерфейсе выбрать reference; метки A/B/C/D — внешние
   углы известной доски (0,0), (180,0), (180,240), (0,240)мм, не углы страницы A4.
   Ввести измеренные XYZ этих углов в указанной robot base системе. Геометрия
   не извлекается из VLM/предположений. Плоскость считается локально горизонтальной
   относительно base +Z; наклон/плохая регистрация отклоняются решателем.
4. Проверить наложенные оси/границу в сохранённом reference JPEG. Оставить доску
   на месте, подтвердить неподвижность камеры/base и нажать «Применить к сетке».

Без действительно доступной base-системы и измерений software не выдаёт
экструзику из картинки. Разрешённый workspace консервативно ограничен измеренным
следом доски; расширение за его пределы требует отдельной измеренной геометрии.
RGB-калибровка плоскости не даёт высоту объекта, контакт/грип, полную геометрию
сцены или разрешение физического исполнения.

## Свидетельства и ограничения

SQLite WAL/FULL + hash-chained immutable events и fsync исходных JPEG/углов,
measurement form/fit/implementation bytes. Запрошенный численный solve возобновим
после перезапуска; flock допускает только один CPU solver в каталоге. Сам solve
не активирует калибровку. Сохраняются оригинальные артефакты с независимыми SHA.
Сбой emit не удаляет accepted event — возвращается event_delivery_error.

Тесты используют только программно отрисованные JPEG и известные fixture-XYZ,
включая отдельную проверку протокола acceptance с эмулированными доверенными
host timestamps. Это **не измерение реальной камеры и не тест физической точности**.
Все `physical_execution_allowed`, `real_robot_execution_tested` остаются false.
