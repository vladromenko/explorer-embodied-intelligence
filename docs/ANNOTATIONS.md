# SAM 3 и наблюдаемые объектные признаки

Лаборатория использует настоящий **SAM 3 image model** из собственного установленного снимка официального исходного кода. `SAM3Segmenter` вызывает `build_sam3_image_model(checkpoint_path=..., load_from_HF=False, compile=False, enable_inst_interactivity=False)` и `Sam3Processor`. Автоматические загрузки, вход в HF и публикация не выполняются. По умолчанию веса берутся read-only из существующего локального `facebook/sam3` cache. SAM 3.1 не подменяет SAM 3 молча.

Источник API: [официальный README SAM 3](https://github.com/facebookresearch/sam3) и [официальный Sam3Processor](https://github.com/facebookresearch/sam3/blob/main/sam3/model/sam3_image_processor.py). Снимок исходного кода/патчи закреплены в `vendor/sam3-provenance.json`; установка в `.venv` проекта. В исходном upstream metadata указан `numpy<2`, тогда как LeRobot требует NumPy 2. Для общей среды изменена только metadata собственной копии. Импорт SAM и технические преобразования NumPy 2 проверены; реальный GPU forward SAM ещё требует отдельной проверки при свободной памяти.

Запись не вызывает SAM. Сначала сохраняются raw RGB обоих ракурсов, monotonic timestamps, measured joints и фактически отправленные actions. Отдельная задача затем читает только завершённые эпизоды. Перед созданием модели проверяются память, владельцы compute и общий lease `gpu_compute`. При конфликте задача ждёт на уровне job supervisor; владельцы ресурсов не завершаются. Базовый порог для SAM — минимум 8 GiB свободной общей памяти, не менее этого даже при пользовательском override.

Каждый `episodes/<id>/annotations/sam3-v1/frames.jsonl` содержит:

- schema `so101.annotations.v1`, точный raw `frame_id`, timestamp наблюдения и SHA256 контракта источника;
- для каждого top/wrist — точный timestamp capture, raw SHA256 и исходные размеры;
- категории `cube` и `box`, текстовые prompts, настоящие SAM score/boxes, центр видимых пикселей маски;
- маски PNG `L` со значениями только 0/255, SHA256 каждой маски, без потерь;
- отдельную оценку цвета из median RGB/HSV внутри видимой маски: метод и lighting limitation явные;
- provenance weights SHA256, source snapshot, threshold, preprocessing, configuration hash;
- видимость `visible_mask`; окклюзия, полная площадь, XYZ, depth, размеры и grasp по умолчанию **unknown/invalid**.

SAM image detector не даёт гарантированные identity. Здесь поверх текущих детекций реализовано только causal IoU association: ID локален для камеры и эпизода; confidence — overlap, `identity_guaranteed=false`. Пропавшие ID явно записываются в `lost_track_ids`, после исчезновения объект получает новый ID. При неоднозначном overlap создаётся `new_ambiguous`; пересечения одинаковых кубиков могут менять ID. При повторном использовании того же capture control loop метка `reused_capture` сохраняет идентичность, не создавая вымышленного нового наблюдения. Числа tracking ID не передаются в численный вход политики.

Цвет не является выходом SAM. HSV-пороги — проверяемый эвристический метод без гарантии при тенях/бликах; нужен реальный контрольный color-chart, если требуется точный цвет. При нулевых детекциях `not_detected_not_proven_absent`: это не доказательство отсутствия предмета.

Задача fsync-сохраняет каждую запись и маску; повреждённая последняя строка после сбоя отбрасывается при resume. Уже корректные кадры не пересегментируются. При изменении источника/конфигурации SAM переразмечается оставшийся causal suffix эпизода. Train loader проверяет raw image и mask SHA256, совпадение timestamp/hash, отсутствие будущего кадра и полноту обоих ракурсов. Stale/missing не превращаются в нули. Технические fixture-аннотации имеют backend `technical_test`; нормальное обучение отвергает их. Fixture backend нельзя выбирать в UI.

`SAM3Segmenter.annotate_observation` использует тот же контракт при inference: текущий RGB и те же prompts/threshold/preprocessing, без будущих кадров, action/success/phase/done labels. Время фактической генерации offline-аннотации хранится отдельно от capture timestamp: поздняя обработка файла сама по себе не является утечкой будущего. Для live-политики дополнительно необходимо контролировать возраст кадра/annotations перед отправкой действия.

# Геометрия

`geometry.py` не содержит догадок о длинах SO-101, размерах кубиков и текущем расположении камер. Скопированная старая калибровка сама по себе не считается актуальной. Применимость требует полного K/distortion/camera-to-world, разрешения, serial, подтверждённого mount fingerprint, совпадающего joint calibration hash и независимой verification с оператором/датой/методом.

`estimate_point_on_plane` может дать только **условную оценку** пересечения calibrated ray с независимо измеренной плоскостью `table`, `cube_top` или `box_surface`. Для верхней грани нужен отдельный измеренный plane/height; нельзя подставить стол вместо кубика. Uncertainty включает pixel perturbation и явно заданные calibration/plane uncertainties. Это не depth measurement; размер объекта, grasp и depth остаются unknown. Центр маски не гарантированно является физической точкой на плоскости.

FK допускается только с явно проверенными DH parameters, радианами, TCP transform, uncertainty и совпадающим joint-offset calibration SHA256. Встроенной выдуманной FK-модели нет. IK и grasp truth не выводятся.

# Реальная проверка

Программно проверены: lossless mask roundtrip, raw/annotation alignment, future-frame rejection, missing/stale/hash corruption rejection, causal ID loss/reuse, изменение конфигурации при resume, остановка/возобновление после torn JSONL, независимость от action/success labels, color method и консервативная геометрия. Эти тесты используют явно маркированные технические маски и математические калибровки. Они не доказывают работу SAM на реальных кубиках.

Остаются физические/ресурсные проверки: реальный SAM CUDA forward на текущих top/wrist кадрах; качество masks/boxes/color/ID при окклюзиях; актуальная top calibration и wrist hand-eye; измеренные uncertainties; latency при live inference. Их нельзя объявить выполненными до свободных ресурсов и проверки оператором.
