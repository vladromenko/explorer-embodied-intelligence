# Экспорт записанного начального SIM-состояния

Общий научный export сохраняет непрозрачные MJB/CAD/source payload только через
явное `initial_state.status="RECORDED"` в неизменённом результате выбранной job
или её JSON-артефакте. Один manifest без этой записи не создаёт допуск.

Поддерживается только профиль `explorer_manipulation_stationary_initial_v1`
и schema `explorer_stationary_sim_initial_state_v1`. Проверяются исходные
manifest path/SHA, helper SHA и его сохранённая source binding, post-reset
boundary, `physical_io=false`, отсутствие ретроактивного capture, точная
payload inventory в record и manifest. Все файлы находятся в той же папке,
без symlink, лишних файлов/директорий, выхода из выбранных managed roots.
Обязательны compiled-model.mjb, native-integration.npy, model.xml,
profile-state.json; каждый source/assets binary имеет SHA в имени и отдельную
связь с manifest. Произвольное добавление .bin в inventory не даёт допуска.

Сначала проверяется вся объявленная структура/численные размеры, затем
фактические size/SHA каждого файла. Пределы: manifest/profile JSON 8 MiB,
не более 512 payload, сумма payload 256 MiB; MJB до 256 MiB, остальные payload
до 64 MiB; source/*.bin и assets/*.bin дополнительно ограничены полным text-inspection
бюджетом 16 MiB. Более крупный blob не получает допуска: export остаётся
PARTIAL, его непроверенный суффикс не копируется. Общие export caps также
сохраняются. Ни MuJoCo, ни NumPy, ни policy
не импортируются. Архивный Python/NPY/MJB не выполняется и не декодируется.
Сохранены существующие secret/SQLite/path guards. Разрешённые расширения
общего export и входящих raw TAR не расширяются: неизвестные .bin/.mjb и TAR
с такими неизвестными members по-прежнему исключаются.

Opaque payload отложены до завершения поиска metadata. Если запись найдена
позже уже выбранного .npy/XML/JSON, существующий узел связывается с тем же
byte-admission receipt. В portable bundle включается отдельный JSON receipt
с manifest SHA, полной payload inventory и явной областью проверки. Payload
зависят от receipt, тот зависит от исходного manifest. Повторный restore
экспорта проверяет **точные байты**, не numerical execution состояния.

Невалидные/неполные bundles дают withheld/PARTIAL (или остановку export при
нарушении его обычного source SHA); неизвестные binaries не приобретают допуск.
Изменение payload после enqueue отвергается обычным pinned-copy SHA guard.
Исходные файлы не переписываются. Даже корректный bundle сохраняет
`original_runtime_OS_closure=PARTIAL`, RGB replay `UNQUALIFIED`,
`numerical_reexecution_verified_by_export=false`. Успех предметной задачи и
физическая готовность здесь не подтверждаются.

Тестовые положительные bundles содержат явно искусственные MJB/NPY bytes:
они проверяют безопасную переносимость, а не работоспособность native модели.
Настоящий CAD/new-PID численный proof и его исходный runtime имеют отдельные
receipts; этот формат не превращает старый прямой capture в историческую
RECORDED-запись и не дополняет прошлые V9 попытки задним числом.
