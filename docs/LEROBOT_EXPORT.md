# Официальный LeRobot Dataset

Raw запись остаётся основным неизменяемым источником. `so101_lab.lerobot_export.export_dataset` создаёт отдельный снимок формата официального установленного **LeRobot 0.6.1 / v3.0**. Используются публичные `LeRobotDataset.create`, `save_episode(episode_data=...)`, `finalize` и обычный `LeRobotDataset(repo_id, root=...)` для проверки. Экспорт работает на CPU, не открывает камеры/bus, не загружает модели и ничего не публикует на Hugging Face.

По умолчанию допускается только source=`hardware` с валидными измеренными joints и действительно отправленными actions. Технические наборы требуют явного `allow_test_data=True`, получают `test_only`, отдельный local repo_id и предупреждение в README. Экспорт не добавляет успешные демонстрации или вымышленные feedback/labels.

Сохраняются оба исходных PNG RGB ракурса без потерь: официальный image-backed Dataset встраивает PNG bytes в Parquet. Он остаётся читаемым без исходной raw папки и не требует видеокодека Jetson. Официальный loader возвращает CHW float RGB `[0,1]`; его image-backed путь 0.6.1 не соблюдает `return_uint8=True`. Проверка сравнивает исходные uint8 пиксели с `round(255 * loaded_rgb)`; это не изменение сохранённых изображений.

LeRobot `add_frame()` формирует идеальное время `frame_index / fps`. Здесь вместо него используется официальный публичный `save_episode(episode_data=...)` с реально измеренным относительным временем observation. Дополнительно int64 features `raw.timestamp_ns`, `raw.state_timestamp_ns`, `raw.action_timestamp_ns`, `raw.top.timestamp_ns`, `raw.wrist.timestamp_ns` сохраняют точные monotonic ns. Source validity/ACK, frame_id и session_id также сохранены. Последующие action chunks в стандартном delta_timestamps API LeRobot выбираются по nominal FPS offsets: это не гарантия постоянного физического интервала. В `provenance/export.json` явно сохранены observed mean FPS, интервалы, drift/jitter, camera age/skew/reuse/drops и задержка state→action. Raw не пересэмплируется.

`provenance/frames.jsonl` сохраняет точную привязку exported index/episode/frame к исходному frame_id, всем timestamps, RGB hashes и raw metadata/feedback/labels. `provenance/raw_dataset.json` и `episode_XXXXXX.json` сохраняют оборудование, калибровки и их происхождение без объявления неверифицированной геометрии валидной. Полный source hash manifest закрепляет raw snapshot.

Датасет блокируется на время экспорта. Незавершённая запись, испорченные hashes, будущие camera frames, непоследовательные timestamps, несовместимые resolutions и смешанные requested FPS приводят к отказу. Экспорт создаётся в скрытой `.partial-*` папке; она становится видимой законченной версией только после `finalize` и проверки официальным loader всех frames. Ошибка/Stop сохраняет только явно incomplete staging с traceback, raw не меняется. Повторный запуск создаёт свежую безопасную попытку; совпадающий полный export повторно проверяется и используется без перезаписи.

По умолчанию destination: `data/<dataset>/exports/lerobot_<source-hash-prefix>/`. Требуемое место проверяется до записи. Существующий destination с другим snapshot не перезаписывается.

```python
from pathlib import Path
from so101_lab.lerobot_export import export_dataset
result = export_dataset(Path("data/<dataset>"))

from lerobot.datasets.lerobot_dataset import LeRobotDataset
loaded = LeRobotDataset(result["repo_id"], root=result["path"], video_backend="pyav")
print(len(loaded), loaded.num_episodes, loaded[0]["observation.images.top"].shape)
```

CLI для той же операции: `.venv/bin/python scripts/export_lerobot.py data/<dataset> --validate-only`, затем без `--validate-only`. UI использует тот же backend. Для чистой проверки формата допускается `--allow-test-data`; результат не считается аппаратной демонстрацией.

Программная приёмка 2026-11-06: 5 focused tests, 6 синтетических frames / 2 эпизода. Проверены официальный loader, RGB, joints/actions, настоящие irregular relative timestamps и int64 ns, задачи/границы, независимость от raw images, fail-closed integrity/partial/FPS проверки и атомарный Stop. Реальная аппаратная запись не выполнялась в этих тестах.
