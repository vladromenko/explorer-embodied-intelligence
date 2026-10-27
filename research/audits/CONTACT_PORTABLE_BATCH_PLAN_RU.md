# ONE → полный batch из 16 numerical restore

ENGINEERING_GLUE. Первая настоящая попытка переносимого численного восстановления
near-left negative baseline завершилась PASS: 501×769 float64 и полный
contact/solver JSONL совпали побитно, score совпал; допуски не использовались.
ONE receipt SHA:
`8d1e08c434d483b6ea11f7718ea438174572adc6e603376f67a2cca0da54ee6b`.
Свежий child PID2078822 завершён/reaped, elapsed9.298 с, peak1,644,040,192 B,
CPU1/2 GiB/swap0, OOM0. Python positive-control действительно заблокировал
старую producer папку; во время restore/расчёта Python opens к ней не было.
C opens остаются `UNMEASURED`: strace не установлен. OS closure `PARTIAL`.

Новый `qualify_contact_portable_batch.py` не меняет прежний qualifier SHA
`013587bcf9b05a36da65bb98a50d45a71b8b86d8885a5eecae6ba45a8ac43f3c`,
helper, frozen14, V10 raw или исходы. Он проверяет реальное ONE доказательство
до копирования/моделей и закрепляет его SHA в отдельном frozen batch plan,
вместе с SHA unchanged verifier, его portable plan, wrapper/current Git и
тем же producer cohort/source14. Архивный Python не исполняется.

Byte-only prepare переносит все 16 snapshots, 20 STL и source/native/contact
refs каждого случая в новый root. После отдельного согласованного resource
окна coordinator использует тот же настоящий verifier в 16 свежих PID.
Дополнительный gate требует 16 уникальных replay UUID и точного совпадения
native/contacts/solver/score во всех случаях. Первый конкретный failure
сохраняется и останавливает дальнейший расход ресурса, без исправления старых
байтов или ослабления критерия.

CPU1, MemoryMax2 GiB, swap0, no EGL/GPU/renderer, max360 с. Восстанавливаются
новые V10 contact initial conditions; это не полная V9 reexecution, RGB replay,
learned skill или физический успех. Исходное ограничение hash timing сохранено:
raw contact/native SHA впервые зарегистрированы независимым V10 анализом после
original unit exit; initial snapshot SHA были закреплены самим producer.
