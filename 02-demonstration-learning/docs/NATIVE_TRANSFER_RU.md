# Approved native02 archive transfer

Source Node wiring, только fixed download route:
`send_native_capture_download(handler, source_archive_path, recording_id)`.
Helper принимает один `Range: bytes=start-`, strong `If-Range` SHA ETag;
несовпадающий If-Range возвращает whole200, некорректный диапазон416.
Whole archive SHA остаётся в `X-Content-SHA256` и ETag; bytes читаются128KiB,
исходный tar не меняется. Caller уже проверил authentication и выбрал path.

Worker `transfer_native_capture(client, base_url, recording_id, archive_sha256,
review_sha256, artifact_root, guard=cancel_check)` сначала GET actual source
review, затем stream fixed download и повторяет review после получения bytes.
Partial `.part` сохраняется при сбое сети и переиспользуется только по pinned
archive SHA; точный206/Content-Range обязателен для заявления resumed_bytes.
Если Source игнорирует Range и возвращает200, partial безопасно начинается
сначала. Целый partial после сбоя до rename восстанавливается по полному SHA
без416. Final archive получает atomic rename+directory fsync. Reject/изменение
review/хеша не допускает источник в dataset. Контроллеры и Source архив не
мутируются; никакого motor/API POST нет.

9 targeted tests PASS0.78s, включая настоящий authenticated localhost HTTP
обрыв после128KiB → точный Range retry → full SHA, повторную review-проверку,
completed-part recovery и reject до bulk GET. Это software qualification.
