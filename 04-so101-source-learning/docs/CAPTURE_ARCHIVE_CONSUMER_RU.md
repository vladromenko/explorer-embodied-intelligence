# Source04 archive consumer: verified local data import

Новый тонкий adapter `src/explorer_so101/capture_archive.py`:

```python
import_source_capture(artifact, archive_sha256, destination, recording_id, source_review)
```

Только локальные файлы. Artifact — уже перенесённый доверенным worker immutable
outer TAR, destination — managed root. Browser не задаёт эти пути. Authenticated
Source GET/Range/retry/latest-review/cohort/job lifecycle остаются у Root.
Ни Dataset, ни converter, trainer, UI или control API здесь не создаются.

## Совместный код и профиль TAR

Consumer использует общие `validate_source_approval`, `source_bytes`, `atomic_bytes`
и `capture_timeline.select_source_samples`. Policy выбора samples не копируется:
пересчитанный canonical selection сравнивается с sealed envelope byte-canonical
JSON, поэтому numeric1 вместо booleanTrue также отклоняется.

Распаковка — stdlib `tarfile`, без `extractall`. Малый raw-header preflight до
stdlib parser ограничивает extension allocation: только regular files и local
PAX timestamps, metadata ≤64 KiB. Исходный Station default PAX mtime проходит
roundtrip. GNU/path/size/sparse extensions и ссылки не входят в этот producer
profile. Parser read/seek дополнительно ограничены; payload копируется ≤64 KiB.
Не используется whole-TAR read_bytes или декомпрессия.

Outer допускает ровно `manifest.json` и `source/recording.tar`. Inner — ровно
исходный `manifest.json` и JPEG paths, перечисленные sealed envelope. Нет
symlink/hardlink/device/sparse/directory/extra/duplicate/traversal members.
Raw inner TAR ≤2 GiB, outer ≤2 GiB+64 MiB+1 MiB, manifest/JPEG member ≤64 MiB,
samples/JPEG ≤10000. Все входные files открываются regular/no-follow; artifact
SHA и inode/size/mtime/ctime повторно проверяются до публикации.

## Проверяемые привязки

- Approved Source review связывает exact recording ID/outer archive SHA/receipt
  SHA. Receipt сохраняется без relabeling; approval не становится expert outcome.
- Completed canonical04/native6 envelope и lifecycle должны совпадать по ID,
  boot/clock, initial source context, native control epoch, actor и calibration SHA.
- Original inner archive size/SHA, manifest size/SHA и все JPEG hashes проверяются.
- Raw manifest/native6 names/units/normalization, context/Skill/version/spec/split,
  recording receipts, calibration и source sample/host-dispatch owner/clock
  сверяются с envelope. Source kind/outcome сохраняются исходными декларациями.
- Native6 state/action vectors не преобразуются; unavailable optional measured
  channels не заполняются нулями. Raw paused samples также остаются исходными.
- Lifecycle events/revision/intervals должны согласовываться; selection проверяет
  source state/action boundaries общей функцией, без assumed clock mapping.

Review может быть передан dict либо exact bytes. Dict сохраняется в существующей
Source canonical JSON serialization; bytes сохраняются буквально, включая
whitespace. `source_review_sha256` — digest receipt; `source_review_bytes_sha256`
отдельно хеширует сохранённый wrapper. Capture и review timestamps не приравниваются:
review исторического архива может быть сделан после reboot producer.

## Результат и расположение

Под `destination/recording_id/`:

```text
capture-manifest.json       # exact outer envelope bytes
source/recording.tar        # exact original inner TAR
original/manifest.json      # exact original Station manifest
original/<JPEG paths>       # exact raw JPEG bytes
source-review.json          # original supplied approval wrapper
import-provenance.json      # immutable import pins, no expert/training claim
```

Return содержит:

- `source_manifest_path/sha256`: raw Station manifest для существующего converter;
- `capture_manifest_path/sha256`: sealed source lifecycle envelope;
- `original_archive_path/sha256`: retained inner TAR;
- `source_archive_path/sha256`: approved outer artifact, который caller сохраняет;
- `source_review_path`, receipt/raw-wrapper SHA, verified `source_approval`;
- `provenance_path/sha256`, original ID/project/source-format и idempotence flag.

`dataset_manifest=None`, `training_admission_assessed=False`,
`physical_success_inferred=False`, `automatic_training_started=False`.
Затем Root явно вызывает существующий native6 converter с
`capture_manifest_path/expected_capture_sha256` и существующий review/cohort gate.

## Повтор/ошибка/конкуренция

Существующий imported ID проверяется по incoming outer/inner/raw/review pins и
всем сохранённым files. Exact повтор не переписывает ничего и не создаёт новую
копию stage. Иной review revision/bytes или повреждённый existing ID — conflict;
такой оригинал не удаляется/не заменяется. Новая ревизия approval требует отдельной
явной политики Root, а не silent mutation imported scientific evidence.

Новые imports сначала пишутся в private UUID stage. Ошибки оставляют partial
stage и `.import-failure.json`; автоматического rmtree нет. Публикация выполняется
под per-ID metadata lock через Linux renameat2(RENAME_NOREPLACE), поэтому даже
поздний чужой empty destination не заменяется. Unsupported atomic publication
fail-closed оставляет stage. Caller должен резервировать disk для retained inner
TAR+распакованных raw payloads и своего outer artifact; renderer/trainer пишет в
отдельный dataset/model каталог.

## Проверка и незакрытые границы

40 focused tests PASS: consumer37 + shared core3. Generated valid4×3 JPEG проходят
исходный Station writer; все actuator/camera/network функции fake/prohibited.
Positive exact bytes/idempotence/PAXmtime и rehashed semantic corruption, unsafe
TAR, rejected approval, incomplete/tampered originals, retained failed stages,
concurrent imports и late empty-directory race проверены отдельно.

Нет реальных демонстраций, actual sensors, optimizer, устройства/RPC/deploy.
Root Node/API/Store/worker transfer/current-review/cohort integration остаётся
следующим этапом. Старый4c shutdown/drain и physical/operator acceptance не
закрываются. Source04 software-ready по одному этому consumer не присваивается.
