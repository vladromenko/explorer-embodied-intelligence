# V10: 16/16 точных numerical restore в новых PID

ENGINEERING_GLUE. На Homebrain воспроизведены все 16 сохранённых V10 contact
initial conditions в новых каталогах и свежих PID на том же проверенном ARM64
runtime. Это не полная V9 reexecution, не новая оценка предметного навыка и не
learned/physical успех. Python-open из старого producer root запрещён и проверен
настоящим positive control. C-level opens не измерялись: strace отсутствует.
Поэтому полная OS/dependency isolation остаётся `PARTIAL/UNMEASURED`.

Сначала одна near-left baseline попытка: fresh child PID2078822, источник
`f89e69d041afa966ba37ee3ceb2c6c8d0375cbb8`, elapsed9.298 с, peak1,644,040,192 B.
Совпали 501×769 float64, весь contact/solver JSONL и score. ONE receipt SHA:
`8d1e08c434d483b6ea11f7718ea438174572adc6e603376f67a2cca0da54ee6b`.
После ревью ONE выполнен отдельно закреплённый full16 batch.

Полный batch: current-trusted commit
`6e50b6398d1d32daa4ed1a1dc1ec60401b6d5c62`, frozen batch plan SHA
`4358f49c80c2b8146f4c6fc0688111ec51df7a4b1cba5145dd05af88b70579e9`.
Immutable portable qualifier SHA
`013587bcf9b05a36da65bb98a50d45a71b8b86d8885a5eecae6ba45a8ac43f3c`.
Physics source14, probe driver, registered helper, env/provider и старые
V10 snapshots/траектории/оценки не менялись.

Проверено:

- 16 настоящих свежих PID, все exit0/reaped и 16 различных replay clock UUID.
- 14 016 native states ×769 float64: 10 778 304 слова, **0 битовых отличий**,
  max absolute numeric difference 0.
- Полный raw contact JSONL, signed forces, solver stats и contact score во всех
  случаях совпали байт-в-байт; допуск не вводился.
- 16 успешных Python-open positive controls; после них 0 обращений Python к
  старому producer root во время restore/численного расчёта.
- Embedded MJB загружался через `from_binary_path`; recorded XML использовался
  в точных исходных байтах и не компилировался со старыми asset paths.
- Все runtime/class/source/MJB/XML/CAD/static проверки unchanged helper прошли
  до native state application; прежний полный CAD model/data освобождался до
  загрузки следующего. Новые source locators были byte-identical payloads.

Побитно воспроизвелись также отрицательные исходы. Contact recovery остаётся
2/4 baseline, 3/4 quarter-dt, 4/4 thick-bottom и 4/4 low-drop. Здесь 16/16 —
численная воспроизводимость сохранённых данных, а не 16 успешных физических
или предметных испытаний. Первоначальная V9 full-task оценка остаётся 2/4.

Actual unit `explorer-contact-portable-v10-full16-wizard-20261027.service`,
invocation `e35896642aba4760b2a375b2b55e73ae`, coordinator PID2080366:
116.157 с, CPU1, MemoryMax/Peak2 GiB, swap0, OOM0/OOM-kill0. Cache reclaim
достигал cap; запас поверх него не заявляется. 14000 сравниваемых contact steps
и отдельно discarded constructor reset100 каждого fresh env. EGL/GPU/renderer
не использовались. Unit завершён, дочерние процессы reaped, ресурс освобождён.

Actual receipt:
`/mnt/explorer-data/explorer-work/contact-portable-v10-full16-20261027-a/batch-qualification.json`
SHA `51d598dc6ddb9c28010fec6a091288eae1b0487dd1d2deb982d84363de193b66`.
Полный публичный receipt:
[contact-portable-v10-full16-results-20261027.json](contact-portable-v10-full16-results-20261027.json),
SHA `6651b61b13dde13d85c4ac36ee2cc497d416852c36531379abf3992326bad9fd`.

Оригинальное ограничение raw hash timing сохраняется: V10 initial snapshots
были sealed producer SHA, а raw contact/native hashes впервые зарегистрированы
независимым анализом после original unit exit. Новая numerical validation
прикреплена отдельным receipt; прежние `UNQUALIFIED` записи не переписаны.
Будущие stochastic RNG draws, RGB rendering, перенос на другую архитектуру и
полная OS closure этой проверкой не квалифицированы.

После завершения frozen full16 отдельный wrapper-only follow-up закрывает
найденный независимым ревьюером status edge: прежний legacy coordinator
сохраняет отрицательный early-stop receipt, но возвращается нормально. Новый
batch wrapper сначала сохраняет этот отрицательный receipt, затем завершает
CLI ненулевым кодом, если не выполнены все16 exact/reaped/exit0 условия.
Изолированный subprocess FakeLegacy regression воспроизводит failed first
child, проверяет сохранённый отрицательный файл и реальный nonzero process exit.
Physics source14 и уже проверенный qualifier013587 не меняются; фактический
успешный full16 receipt остаётся привязан к своему исходному commit6e50.
