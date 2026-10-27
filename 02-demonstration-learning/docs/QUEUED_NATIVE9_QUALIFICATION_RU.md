# Дополнение: настоящий queued Native02 worker

Прямая computational квитанция `NATIVE02_LEROBOT_CPU_20261027_QUALIFICATION.json`
сохраняется: она доказала actual Dataset/ACT pause/new process/resume/parity,
но сама запускала private fixture worker, а не общий Supervisor/bootstrap.

`test_native02_queued_learning.py` закрывает только эту другую границу. По явному
opt-in `EXPLORER_RUN_NATIVE02_QUEUE_QUALIFICATION=1` тест получает те же5 полностью
программных native producer записей с generated JPEG, raw publisher receipts,
pause segments, пассивным input journal и Source approval. Настоящий `git archive`
чистого HEAD распаковывается в commit-addressed release с `RELEASE_COMMIT`.
Child config содержит private Store и `nodes:[]`; никакой Source или robot RPC
не может быть подготовлен из fleet config.

Через authenticated local ASGI/CSRF создаётся **одно** ACT4 задание. Настоящий
Supervisor/Popen запускает original bootstrap→job_runner из архивного release.
Проверяются durable worker PID/start/boot/nonce, bootstrap admission,2CPU/4GiB/GPU0
reservation, execution pin, actual4 updates, Dataset metadata, save/reload,
worker exit и освобождение reservation. API models читает этот actual result,
не загружая модель в Control Center. Затем явный local ASGI evaluate POST
создаёт DEV job; другой настоящий worker получает2CPU/2GiB/GPU0 и shared serial
inference slot. Повторной оптимизации, SEALED чтения, модели в CC или dispatch нет.

До qualified запуска доступны только два pure freeze/config guard теста;
heavy case пропущен. Реальная queued квалификация требует отдельного Root
CPU2/MemoryMax4GiB/MemorySwapMax0/RuntimeMax600s/LimitCORE0 окна. Старый parity
тест не повторяется. Никакие настоящие показы, физическое masterство, rollout,
автономная motion authority или весь исходный промпт этим не объявляются готовыми.

27.10.2026 замороженный `86751f3ea96edcd88d76ce3b0177475235a54abf` фактически
прошёл этот путь: **3PASS33.92s**, unit runtime35.795s, CPU47.567s. Kernel cgroup
`memory.peak`1326194688байт, swap0/OOM0. ACT4 выполнил PID2426152, DEV — отдельный
PID2426205, с разными nonce и start ticks одного host boot. Оба worker прошли
bootstrap admission, вышли с0 и освободили reservations. API models/DEV checks
прочитал actual сохранённые results. Все19 исходных SHA и cleanGit перепроверены.

`NATIVE02_QUEUED_CPU_20261027_QUALIFICATION.json` сохраняет источники, full Git
archive SHA, реальные runtime admissions, immutable raw pointers и пределы
доказательства. Отдельный exact-source тест сравнивает нынешний official Dataset
путь с direct+queue receipts. Старый fused106 PROFILE4 receipt и его SOURCE_TREE
байты сохранены; исторический тест сверяет старые hashes с **его** frozen tree,
а не присваивает старую квитанцию нынешнему изменённому pipeline.
