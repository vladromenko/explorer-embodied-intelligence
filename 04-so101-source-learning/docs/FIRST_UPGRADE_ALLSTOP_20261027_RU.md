# Первый Pi upgrade: all-stop candidate и отрицательный исход

Это qualification механизма на отдельных fake-процессах Pi, не допуск рабочего
observer к обновлению. [Машинное evidence](../../_registry/evidence/experiment04-first-upgrade-allstop-20261027.json).
Исходный observer43382, его сервис и контроллеры не изменялись.

Точный old4c archive содержит оригинальные Node constructor, localhost HTTP,
Station/worker/writer, COMMON и direct service. Использованы только fake buses,
один fake camera cache, synthetic token и отдельные новые state paths. Открытие
`/dev/*` и TCP connection вне loopback запрещены audit hook. Debugger использует
типы из установленных CPython3.13 headers и pinned official3.13.5 helper.
Inferior calls, memory/register writes и software breakpoints отключены.

12 случаев:

- idle и частично принятое HTTP body: all native threads stopped, bounded Python
  snapshot полного idle/no unfinished record/no native session, SIGKILL без
  возобновления. Ноль fake motor writes, Node.close и fixture finally.
- active Station, active/finishing record, unfinished queue task, camera record,
  direct active, direct lifecycle inflight и entered original enable-hold:
  admission rejected. Inspector не завершал отвергнутые fake-процессы; parent
  убирал только собственный Popen после проверки.
- missing field: `FAIL_UNKNOWN`, admission false.
- missing field плюс fake serial deadline, истёкший за время all-stop:
  `FAIL_UNKNOWN`; detach возобновил old4c. Оригинальный Station idle exception
  path попытался отправить один measured-position hold. Это отрицательный исход
  предлагаемого безопасного abort, сохранённый в raw events и evidence.

GNU документирует [остановку всех потоков в all-stop](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Thread-Stops.html)
и [возобновление процесса при detach](https://www.sourceware.org/gdb/current/onlinedocs/gdb.html/Attach.html).
Из этих свойств и actual fake deadline negative следует: положительный
freeze→kill не доказывает безопасный отказ с detach. Агент не прикрепляется к
настоящему observer при таком неполном failure contingency.

Новый same-bus cold guard34c5f10 отдельно блокирует idle exception hold до первого
валидированного COMMON USER start;73Python PASS, frozen Station не изменён.
[Evidence](../../_registry/evidence/experiment04-cold-publication-guard-20261027.json).
Он действует только после запуска нового profile и не исправляет old4c abort.
Первый unattended upgrade остаётся OPEN. Нужен отдельно наблюдаемый первый
переход/подтверждённое безопасное питание либо полностью доказанный never-resume
barrier с безопасной обработкой всех отказов. Release regression/recovery
preparation продолжается независимо. Физическая работоспособность не заявлена.
