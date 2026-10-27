# 01 PARKED после текущих atomic smoke

Новое указание пользователя: после checkpoint03 очередь **04→05→06→01**.
Длинное обучение01 не запускалось. FULL248 continuation не был разрешён и не
выполнялся. Подготовленные helpers/requests/команды сохранены без удаления;
никаких новых01запусков после уже выполнявшегося atomic smoke не начинается.

До изменения приоритета Root разрешил два8-update smoke serial. Visual8 уже
завершился, Identity8 уже выполнялся; он доведён только до той же исходной
pause8. Итого16фактических новых private diagnostic optimizer updates, НЕ0.
Оба процесса вышли с ожидаемым75: `SMOKE_DURABLY_PAUSED_NOT_FINAL_QUALITY`.
Это не достигнутый criterion/навык и не ошибка, скрытая за PASS.

| Cell | Updates | Runtime | Cgroup peak | Reload | Prior active MAE |
|---|---:|---:|---:|---:|---:|
| Visual8, реальные успешные SIM expert labels | 8 | 22.303s | 1221545984B | 0 | 0.030509 |
| StateIdentity8, явно synthetic identity labels | 8 | 22.484s | 1223831552B | 0 | 0.121131 |

Обе initial weights действительно совпали:
`e66efe7c4be782f0e4aaf4e236877e43af08b99d9352fdc9092df851c8743f95`.
Original optimizer pause, sampler/RNG/model checkpoint8 и отдельные official
reload checkpoints сохранены; source/checkpoint SHA повторно проверены.
Swap0, memory.events все0. Восемь updates — resource/implementation smoke,
не проверка финальной memorization criterion; у обоих она пока false.

Все операции — новые приватные diagnostic модели по сохранённым SIM данным.
Не было Source/robot/network requests, render/sim-step, production/registry
writes или изменения старых original datasets/models/controller settings.
Identity labels не становятся expert демонстрациями. Initialization86001 уже
использована diagnostic запуском: будущий canonical preregistration должен
явно учесть это до открытия новых DEV/sealed, а не скрыть факт использования.

Машинные receipts и raw SHA: `TINY_CONTROLS_SMOKE8_20261027.json`.
Полные private originals:
`/mnt/explorer-data/explorer-work/01-tiny-positive-controls-20261027`.
Новый запуск01 требует нового окна/явного решения Root по актуальной очереди.
