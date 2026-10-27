# Новая portable numerical проверка V10

ENGINEERING_GLUE. Сначала одна попытка: `v10-v9-near-left-7332-captured-physics`,
включая её отрицательный контактный исход и нестандартную bin pose. Исходный
V10, его 16 snapshots/траекторий/оценок, frozen physics driver/helper/env/provider
не изменяются. Только после реального результата первого случая возможна
отдельная новая квалификация всех 16.

`scripts/qualify_contact_portable.py --mode prepare` работает только с байтами:
проверяет доверенный SHA независимого V10 report и действительный current Git
commit, копирует точный initial bundle, 20 CAD/STL, source payloads и эталонные
native/contact/result файлы в новый root. Записывает SHA plan. Canonical STL
basenames сохраняются для строгого registered profile, но каталог и полные
пути новые. Source blobs копируются как данные; архивные `.py` не исполняются.

Verifier — свежий PID. Python audit запрещает open во всём старом producer
root; перед расчётом выполняется настоящий отрицательный positive-control open.
Если `strace` установлен, coordinator дополнительно сохраняет file syscalls и
отклоняет обращения open к старому root. На текущем Homebrain `strace` не найден;
поэтому C opens и полная OS-изоляция останутся `UNMEASURED/PARTIAL`. Python
audit не называется kernel sandbox.

Исполняются только исходники текущего явно доверенного Git commit, совпадающие
с 14 исходными SHA V10. Его unchanged trusted `simulate` импортируется с
проверенного current path, а не из snapshot source blobs. Настоящий тот же
`ManipulationEnv` сначала создаётся на relocated 20 STL. До MJB loader прежние
model/data освобождаются через уже проверенный `release_then_compile`.
Затем `MjModel.from_binary_path` читает embedded MJB; recorded `model.xml`
устанавливается **байт-в-байт** и не компилируется с его старыми asset paths.

Подготовка известных Python static/evaluator/derived-buffer ABI выполняется из
фактического загруженного MJB и действительных текущих классов: joint/geom IDs,
goal/half, свежий evaluator, actual dt. Неизвестные поля не копируются из
снимка в обход проверки. Unchanged `restore_initial_trial` проверяет точные
runtime/class/source/MJB/XML/asset/static bindings до применения native state.
Source bindings указывают новые byte-identical payload locators; replay clock
получает новый UUID. Затем bundle освобождается до simulate.

Gate требует bitwise равенства всех float64 native states, полного raw contact
JSONL (включая signed forces и solver stats) и contact score. Один ULP считается
отличием; допуска/ослабления нет. Failure, runtime/MJB/static gap и численное
расхождение сохраняются отдельно. На первом конкретном провале campaign
останавливается, полные 16 не расходуют ресурс на известную проблему. Никаких
изменений старых данных или автоматической поправки helper при провале.

Одна own unit: CPU1, MemoryMax2 GiB, swap0, no renderer/EGL/GPU, max360 с.
Native nodes/serial/controller APIs отсутствуют. Это воспроизведение новых
contact initial conditions, а не полная V9 reexecution, learned skill,
физическое исполнение или закрытие master prompt.
