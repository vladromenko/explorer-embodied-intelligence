# Новый RECORDED capture → авторизованная очередь → независимый byte restore

Испытан чистый commit `0ddee1169a0faaebd0661b5c1bac132a78d898fb`. Старый V7 не
переписывался и не получал новую RECORDED-обёртку. Создан новый development
`SOFTWARE_SCRIPTED_SIM` fixture со всеми пятью phase labels; исходная SO101 рука
не исполнялась, обученная policy отсутствует, object-expert claim отсутствует.

Реальный текущий MuJoCoTargetProvider, созданный неизменённым
`qualify_sim_initial_state.provider()`, выполнил reset и зарегистрированный
initial_state_capture_config. После настоящего target.observe сохранён один
PNG RGB 320×240; StopMapper намеренно завершил опыт программной ошибкой
`POST_CAPTURE_SOFTWARE_DIAGNOSTIC_STOP` **до первой target.advance**.
Исходная job `908251dcf9914585b30a0803bf6e398e` буквально сохранена failed с
полным реальным result; initial_state — RECORDED, MJB **153 859 356 B**, **20 CAD**,
0 finite steps/target advances, frames trace 0, full target success false.
Provider/env поля не подменялись; канонические настройки контроллеров не менялись.

Capture unit `explorer-recorded-initial-capture-20261027-a1.service` завершился
кодом 0 за 6.650s. Внутри живого cgroup измерены peak **1 463 365 632 B**, лимит
CPU1/3GiB/swap0. EGL использовался только для этой новой SIM-картинки.

Затем отдельный unit CPU1/2GiB/swap0 без GPU/EGL поднял реальный loopback uvicorn
с нулём nodes. Проверены actual authentication 401 → login 200, отсутствие CSRF
403, лишний filesystem input 400, ID-only POST 200. API поставил
`research_export` job `3a3cbfccccd9464aa8714a92fe487e00` в общую private очередь.
Настоящие Supervisor → bootstrap → job_runner создали export/worker restore/TAR;
worker PID 2072032 завершился кодом 0 и reaped. Его durable runtime содержит
bootstrap_admitted и worker_entry_admitted. Исходные SQL config/result/error и
все 33 файла source job остались неизменны.

Через настоящий authenticated download получен архив **196 956 160 B**, SHA256
`56febf27156d032e694b7ea276cd1460ebfb79c4f2db2434f57279e9687e7ffc`.
Сервер PID 2072029 остановлен/reaped. Новый процесс **2072079** из downloaded TAR
самостоятельно восстановил все **33** исходных файла byte-for-byte с SHA,
включая MJB, 20 CAD, native NPY/profile, helper/recorder/driver source blobs,
manifest, target PNG и observation/result/trace. Source-read positive control
был отклонён; затем Python audit hook зафиксировал **0** попыток открыть
исходный State при restore. Это проверка Python open events, не OS sandbox.
Архивные MJB/NPY/Python blobs не загружались/не исполнялись как модель или код.

Export unit `explorer-recorded-initial-export-20261027-a1.service` завершился
кодом 0 за 18.791s; kernel peak **1 326 534 656 B**, swap peak 0. У обоих units
все memory.events равны 0. Unit/worker/API/restore процессы завершены, ресурсное
окно освобождено. Послепроцессный systemd Memory peak здесь занижен; primary
метрика — memory.peak, прочитанный внутри живых cgroups.

В bundle — 42 файла, включая отдельный portable byte-admission receipt.
Результат **PARTIAL**: полный OS/transitive-dependency inventory не закрыт.
Экспорт доказывает сохранность байтов. Численное повторение commands/native
state и RGB reexecution этим опытом **не проверялись**. Успех предметной задачи,
навык source/target policy и физическая готовность не заявляются. Весь исходный
пользовательский промпт этим узким software evidence не объявляется исполненным.

Публичные JSON рядом: capture/export/resources/source-library/initial-manifest/
source-result/independent-restore/summary. Private originals, journals, auth
configuration (не экспортирована), downloaded TAR и обе restore копии:
`/mnt/explorer-data/explorer-work/recorded-initial-export-20261027-a1`.
