# Проверка источников единого мобильного навыка

`explorer.mobile_dataset_evidence.inspect_mobile_import(job, *, managed_root=None,
include_manifest=False, max_episode_bytes=268435456, training_jobs=())` ничего не
записывает, не обучает и не отправляет роботу. `mobile_evidence_summary(jobs, ...)`
возвращает счётчики и эти отчёты. Передавайте корень управляемого state: пути вне
него, выход через symlink/`..`, несовпадения SHA и слишком большие артефакты отклоняются.

Основной контракт только один: весь ROSMASTER, одновременно все девять координат
действия. Настоящие нули не маскируются. Измеренные суставы и одометрия не превращаются
конкатенацией в выдуманное предыдущее командное состояние 9D.

## Канонический источник и сохранение байтов

Читаются `result.embodied_manifest`, `result.observation_manifest` и
`result.dataset_manifest`. Поддержаны исходный документ
`explorer_embodied_episode_v1`, поле `embodied_episode` внутри манифеста или
`embodied_episode_path` + `embodied_episode_sha256`. Для обёртки нужен
`embodied_source_files_sha256`: исходное относительное имя → SHA256. У прямого
документа используется его `source_files_sha256`. Байты всех payload проверяются
и передаются авторитетному `explorer_shared.embodied_episode.inspect_embodied_episode`.
ID конверта обязан совпадать с первоначальным ID записи из import job.

`mobile_embodied` — результат этой проверки; его `source_artifacts` содержит
`{relative_name, path, sha256}`, а `envelope_binding` связывает конверт с исходным
манифестом/sidecar. При `include_manifest=True` возвращаются также исходный конверт
и манифест. `embodied_source_files` даёт абсолютный безопасный путь → SHA256 для
агрегатора. При копировании в агрегат нужно сохранить все payload и исходные
относительные имена конверта; один RGB-NPZ не заменяет RGB/depth/лидар/IMU и остальные
данные. Нельзя менять исходные файлы или назначать источнику другую сессию/scene/split.

`admitted_for_training` здесь означает структурный допуск полного канонического
эпизода; `operator_success`, `source_quality_usable`, completed, разделение сцен,
разрешённая optimizer partition и успешный независимый outcome остаются отдельными
условиями workflow. Допуск источника не означает наличие обученного backend,
использование всех датчиков моделью или физическую квалификацию. Без авторитетного
модуля результат типизированно закрыт. Валидный canonical-only источник не требует
старого NPZ, дополнительного `preceding_command9` или синтетического state9.

## Служебное прежнее представление ACT

`legacy_RGB9D_view` сохраняет свои `manifest_path`, `manifest_sha256` и, при
`include_manifest=True`, `manifest`. Они относятся только к прежнему view:
верхний манифест может быть каноническим исходником без NPZ. Не передавайте
верхний canonical-манифест старому RGB-NPZ reader. Поле
`canonical_sample_correspondence_verified=False` не позволяет считать одинаковый
ID эпизода доказательством покадрового соответствия двух представлений.
View сохраняет отдельную проверку существующего конвертера:
исходные JPEG/JSON/индексы/команды/причинные timestamps сравниваются с реальными
массивами NPZ; форма state/action — 9, RGB — uint8 NHWC. `command_estimate` остаётся
командной оценкой, host-reported target — сообщённой целью. MCU-dispatch и измеренные
q этим не доказываются. Внутри полного навыка это ограниченный RGB + буквально
записанная предыдущая команда 9D, а не самостоятельный частичный режим.
`legacy_RGB9D_contract_admitted` и причины этого view никогда не блокируют
независимо валидный канонический источник. Его старые development/sealed разделы
не становятся train. Фактические сохранённые модели связываются с SHA источника,
успешным optimizer run и проверенным config.json; чужая модель не засчитывается.

`raw_rgb_verified_count` означает проверенные SHA/декодирование исходных JPEG.
`raw_integrity_errors` отдельно показывает несовпадение привязки JPEG к исходным
метаданным камеры. Сохранённые файлы при этом не теряются и не становятся обучением.

## UI-хуки

Подключить `manual-camera.js` и `mobile-evidence.js` до `app.js`.
Один `createManualCameraPreview({image: existingCameraImage, onUnavailable})`
обновляет реально полученные JPEG ROSMASTER: authenticated GET, максимум один
запрос одновременно, 200 мс, browser receive monotonic clock, повтор frame ID не
продлевает свежесть. `preview.ready()` — ровно один callback для клавиатуры и gamepad;
`onUnavailable` немедленно отсоединяет manual controllers. При скрытии вкладки,
потере превью, HTTP/timeout или stop поздний кадр не возобновляет допуск. Это preview
около 5 FPS; исходная запись Nano 15 FPS не меняется. Старый MJPEG/image.load не
является доказательством свежести кадра и не должен быть вторым владельцем preview02.

`createMobileEvidenceClient({fetchEvidence, onChange})` объединяет одновременные
read-only refresh; таймера и управляющих запросов нет. Root может дать ему
`fetchEvidence: () => api('/api/mobile/evidence')` и отрисовывать
`mobileEvidencePanel(client.snapshot(), {preview: preview.state(), teaching})`.
`teaching={active:{id,name,frames,samples},last:{id,outcome}}` — реальные статусы записи.
Панель показывает причины исключения, SHA/камеру/сессии, а для каждого сенсора отдельно
«записано / допущено / реально используется моделью». Только реальный preview
`ready` + nano-rgbd + frame ID + browser receive clock даёт свежий статус.
Проверку артефактов обновлять по завершению import/изменению источников или явной
кнопке, не на каждом JPEG. Камера должна оставаться видимой вместе с manual control
и записью на всём экране02. Приложение/CSRF/layout подключает root.

## Проверено 2026-10-26 на Homebrain

10 Python проверок: прежний конвертер, SHA/единицы/порядок/причинность/точные RGB,
analysis-only capture, неизменность файлов, canonical-only источник из авторитетной
синтетической fixture (9b931f5), отсутствие выдуманного prior-state9, независимость
от сломанного optional view, чужой ID и повреждённый payload. Никаких Torch optimizer,
сети, камеры или движения эти тесты не запускают. Плюс 10 Node проверок preview/панели.

Read-only SQLite+retained-file inspection реальных трёх импортов после SSD: 9+9+18=36
кадров сохранены; ни один полный обучающий эпизод, успех оператора или trained RGB9D
model не подтверждён. У записи18 кадров обнаружены несовпадения SHA привязки части
кадров к метаданным камеры; это отдельный фактический дефект источника, не повод
подменять метаданные. Официальный ACT forward/backward/readback именно по мобильному
конвертеру ещё не запускался; готовность физического навыка не заявляется.
