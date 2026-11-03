# Обучение и контракт inference

Используется настоящая `lerobot==0.6.1` / `SmolVLAPolicy`: vision encoder SmolVLM, language tokens, state projection и action expert с flow-matching loss. Основной режим — RGB top + wrist, измеренные 6 joints и SAM 3 признаки текущего кадра. Baseline `rgb_state` использует те же RGB+joints без SAM. Сравнение качества этих режимов пока не выполнено.

Вместо неиспользуемых произвольных JSON-полей реализован конкретный адаптер: 6 joints + по 6 величин на каждую пару `(камера top/wrist, категория cube/box)` + две one-hot величины заданной категории цели, итого 32. Величины слота: valid, center_x/W, center_y/H, bbox_width/W, bbox_height/H, score. Они попадают в настоящий `observation.state`, затем в обучаемую `model.state_proj` и attention action expert. Максимальная размерность stock SmolVLA сохранена; state projection обучается. Отдельная scene-map не нужна для этого минимального пути.

Отсутствующее обнаружение — слот из нулей с valid=0; отсутствие или устаревание самой разметки вызывает ошибку. Для нескольких объектов одной категории выбирается наибольший SAM score с детерминированным разрешением равенства; при настройке target_color кубик фильтруется по отдельно измеренному валидному цвету. Это не гарантирует постоянную идентичность конкретного кубика. Tracking ID не передаётся как число. XYZ, размеры, force, успех, done, фазы, будущие frames/actions не входят в observation. Цвет не объявляется выходом SAM. Для другой целевой привязки требуется новая версия адаптера.

Raw RGB обоих ракурсов всегда сохраняются. `wrist_only_experimental` обучает/использует только wrist, оставляет слоты top невалидными; stock модель имеет n_obs_steps=1 и не обладает добавленной здесь памятью. История, done-head, XYZ-head, grasp loss и memory не реализованы и не заявляются.

Разметка проверяется по frame_id, monotonic timestamp, raw hash и lossless mask hash; модель/порог/prompts/preprocessing SAM должны совпадать во всех обучающих кадрах. Train/validation разделяются целыми сессиями, а при одной сессии целыми эпизодами с предупреждением. Нормализация joints/action вычисляется только по train; object validity/координаты/score/target остаются в [0,1]. Future action chunk — только обучающая цель и не переходит границу эпизода. Хвост masked padding не участвует в loss.

Checkpoints находятся в `models/<run>/checkpoints/step_XXXXXXXX/`:

- `pretrained_model/`: настоящие LeRobot `config.json`, `model.safetensors`, `policy_preprocessor.json`, `policy_postprocessor.json`, normalization tensors; `backbone/` содержит локальные config/tokenizer/processor assets без второй копии backbone weights; `object_adapter.json` и `lineage.json` описывают согласованный вход.
- `training_state/state.pt`: optimizer, scheduler, step, Torch/Python/sampler/CUDA RNG для фактического resume.
- `checkpoint.json`: статус, sha256 весов и контракта. Публикация в HF/W&B отключена. MLflow отправляет метрики реального loop и маленький manifest при промежуточных сохранениях; полный конечный checkpoint отправляется один раз за run в отдельный локальный artifact store для загрузки через MLflow.

`load_checkpoint` проверяет hashes, загружает policy strict=True и сохранённые processors с поправкой локальных путей при переносе папки. `LoadedPolicy.predict` применяет тот же адаптер/нормализацию и получает действие из модели. Tiny checkpoint для технических тестов запрещён аппаратному inference. Нет скрытого deterministic fallback.

Live SAM загружается и сравнивается с training provenance до открытия servo bus. Запуск физического worker требует явного Start в UI; HardwareSource проверяет владельцев устройств, калибровку и ограничивает единственную отправляемую команду. При долгом inference/устаревании изображения или изменении измеренного состояния действие не отправляется. Пока не выполнены проверки полного pretrained GPU train, реального SAM и физической latency: текущий параллельный эксперимент удерживает память/камеры/bus.

Официальные первичные источники: [SmolVLA model card](https://huggingface.co/lerobot/smolvla_base), [конфигурация SmolVLA](https://github.com/huggingface/lerobot/blob/main/src/lerobot/policies/smolvla/configuration_smolvla.py), [flow matching и обработка входа](https://github.com/huggingface/lerobot/blob/main/src/lerobot/policies/smolvla/modeling_smolvla.py), [SmolVLM с action expert](https://github.com/huggingface/lerobot/blob/main/src/lerobot/policies/smolvla/smolvlm_with_expert.py). Для реализации изучен фактически установленный независимый снимок 0.6.1, main не считается закреплённой версией зависимостей.
