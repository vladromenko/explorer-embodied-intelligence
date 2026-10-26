# Изолированная квалификация восстановления на Homebrain

`scripts/qualify_recovery.py` проверяет реальные процессы, SQLite WAL и loopback
HTTP в новой отдельной папке. Он не перезапускает Homebrain/Pi/Nano, действующие
службы, Wi-Fi, камеры или контроллеры. Физических команд нет. Такая проверка
**не закрывает** аппаратную приёмку reboot/Wi-Fi-loss и восстановление настоящего
learner после OOM.

Проверенный запуск 26 октября 2026:

```bash
~/.local/share/explorer/venv/bin/python scripts/qualify_recovery.py \
  --source-root /home/jetsonnx/work/Explorer \
  --output ~/.local/share/explorer/state/qualification/isolated-recovery-20261026-v1 \
  --with-isolated-oom
```

Повторный запуск требует новой папки `--output`; существующие свидетельства не
перезаписываются. В Git сохранена точная копия итоговой квитанции:
`_registry/evidence/isolated-recovery-20261026.json`. SQLite, журналы, source
releases, события и kernel receipts находятся в указанной папке квалификации.

## Действительно пройденные сценарии

- Из двух настоящих Git commits созданы локальные неизменяемые releases. В отдельной
  БД запущены **настоящие scheduler и job_runner** с программным `handover_test`.
  Собственный worker остановлен перед завершением, собственные supervisor/worker
  получили SIGKILL. Новый supervisor использовал более новый release, завершил тот
  же job по исходному pinned commit. Осталось ровно одно completion event; повторная
  финализация идемпотентна, конфликтующий результат отклонён. Ещё один перезапуск
  supervisor сохранил завершённый результат. SQLite integrity — `ok`.
- **Настоящий EventDelivery** отправлял три сохранённых события в loopback HTTP
  endpoint с `Store.ingest`. Сначала HTTP listener отсутствовал; delivery process
  получил ошибку соединения и затем SIGKILL. Все три события остались pending.
  После восстановления listener первое событие было записано в центральную БД,
  но ACK потерян и producer снова получил SIGKILL. Три события опять остались
  pending. Третий процесс повторил доставку: четыре HTTP-запроса дали три уникальных
  события и три ACK. Исходные event IDs, bodies, clocks и SHA сохранены; конфликтующая
  запись под тем же event ID отклонена. Это отказ loopback HTTP, не разрыв Wi-Fi.
- **Настоящий NanoArmGateway** в двух отдельных процессах отклонил отменённые action
  и lease epochs. Executor здесь явно обозначен как программный fixture; после
  перезапуска к нему не ушло ни одной команды. Это квалификация ownership protocol,
  не физического STOP и не перезагрузки Nano.
- Реальный kernel OOM произошёл **только в новом собственном transient user cgroup**.
  Перед выделением памяти проверены фактические `memory.max=67108864` и
  `memory.swap.max=0`; без этих значений script отказывается выделять память.
  Отдельный allocator пытается выделить не более 128 MiB внутри лимита 64 MiB.
  Зафиксированы SIGKILL allocator, `oom_kill` delta **1** и `memory.peak=67108864`.
  Главный проверяющий процесс выжил и прочитал kernel counters. Собственный transient
  unit остановлен и удалён из failed units; лимиты производственных процессов не
  менялись. Это не глобальный OOM и не проверка реального учебного job после OOM.

`--with-isolated-oom` необязателен. Если cgroup v2 memory controller или установка
точного лимита недоступны, результат будет `UNAVAILABLE`/`REFUSED`; script не
переходит к давлению на память хоста. Запуск всех этапов с флагом считается успешным
только при подтверждённом kernel OOM в указанном ограниченном cgroup.

Проверка основных транспортных инвариантов отдельно:

```bash
~/.local/share/explorer/venv/bin/python -m pytest tests/test_recovery_qualification.py -q
```

Фактически: **2 passed**, 1,97 с. Оба теста запускают отдельные процессы и локальный
HTTP server. Они не подменяют I/O mock-ответами; только arm executor для проверки
ownership специально остаётся неаппаратным fixture.

## Что ещё требуется для полной приёмки

Нужны отдельные разрешённые испытания настоящих устройств после reboot и Wi-Fi
loss/reconnect, анализ поведения действующего learner при resource fault и проверка
возобновления по его optimizer/RNG checkpoint. Изолированная квитанция не является
свидетельством прохождения этих аппаратных сценариев. До них сохраняются текущие
ограничения пользователя: шасси неподвижно, исходные настройки ROSMASTER неизменны.
