# Контактный V10: наблюдаемый конфликт floor/bin и оставшаяся неоднозначность

ENGINEERING_GLUE. 16 новых copied-CAD контактных попыток выполнены на Homebrain
по заранее закреплённому протоколу
`87f8c80a391f06d57263098d3da5c23007d0e811c57f62488a69006dd174ae45`.
Исходники physics driver — commit `21dd9e9`, SHA
`5c36678c9883835ea78017834c789a66bec3ad9a6417e127009aa8bac81757e6`.
Новый initial condition: source-bound native row317, свежие time0/warmstart0/RNG,
исходные controls constant, действительная статическая bin pose каждой сцены.
Исходные V9, env/provider/defaults и физические настройки не изменялись.

| Контактное условие | Recovered | Сохранённые неудачи |
|---|---:|---|
| Captured physics, dt0.002 | 2/4 | near-left, in-yawminus |
| Только dt0.0005 | 3/4 | in-yawminus: supported/settled, но outside |
| Толстое дно, прежний верх | 4/4 | Нет в этих четырёх initial conditions |
| Низкое падение из покоя | 4/4 | Нет в этих четырёх initial conditions |

Это контактный development denominator: он не заменяет исходную полную V9
оценку 2/4, не измеряет новый полный grasp/lift/carry/place и не подтверждает
learned/physical source, перенос навыка или H4. Все четыре сцены включены во
все условия, исключений и retuning после старта не было.

Новые raw-контакты позволяют установить **направление**, которого не было в
оригинальных scalar traces. При терминальной неудаче near-left дно действует
на предмет вниз в среднем −32.698190 Н, пол вверх +32.821796 Н. В in-yawminus
соответствующие величины −26.275998 и +26.399604 Н. Сумма world-Z сил контакта
равна весу около +0.123606 Н: предмет неподвижен в нефизичном геометрическом
зажиме между floor и пересечённым bin bottom. Сырые пары/normals/local wrench
сохранены, знаки независимо пересчитаны через официальный contact frame.
В near-left первые большие opposing силы возникают на NEW t0.126 с, в
in-yawminus — t0.128 с. В этих новых one-second контактах другие части робота
с предметом не контактировали. Все warnings нулевые; в двух baseline неудачах
solver niter не более 6, во всех 16 опытах не более 7. Исчерпание лимита 70
итераций не наблюдалось.

Численные параметры всё ещё требуют валидации. Изменение толщины сохраняет
верхнюю поверхность и предотвращает устойчивое opposing-force состояние в
этих сценах, но допускает до 17.2 мм transient signed penetration. Low-drop
также не устраняет мягкое проникновение: до 5.45 мм. Quarter-dt оставляет одну
контактную неудачу по критерию положения. Поэтому уникальная первопричина
solver/contact softness/dt остаётся `AMBIGUOUS`; geometric recovery нельзя
выдавать за доказанную физическую точность. Новые значения не внесены в
исходные defaults. Low-drop — изолированное начальное условие, а не готовый
collision-checked placement controller полной руки.

Каждая попытка сохранила поддержанный зарегистрированный initial profile
**до первого contact step**, с MJB/CAD/native/profile/RNG/source SHA. Независимый
байтовый анализ проверил все 16 snapshot inventories, равенство первой строки
native trajectory начальному native payload, все native clocks, local→world
force conversion/sums, полный терминальный интервал и неизменённые исходы.
MuJoCo/NumPy/Torch при этом анализе не импортировались; native state не
применялся, архивный код не исполнялся.
Restore/reexecution этих новых snapshot в другом PID всё ещё `UNQUALIFIED`.
Raw substep/native SHA впервые зарегистрированы независимым анализатором
**после** завершения unit; прежний producer не записал эти два hash в result.
Это честно указано в receipt. Snapshot hashes были закреплены producer.

Actual unit `explorer-contact-probe-v10-wizard-20261027.service`, invocation
`9d861915492a45bba12b47bcffe38d13`: 109.576 с; 16 дочерних процессов завершены
и reaped с exit0; CPU1, MemoryMax/Peak2 GiB, swap0, OOM0, OOM-kill0. Cache
reclaim достигал лимита памяти, поэтому запас сверх этого cap не заявляется.
GPU/EGL/renderer отсутствовали. 14000 contact steps и отдельно 3200 discarded
constructor/reset steps; последние не используются в contact scoring.
Unit завершён, ресурсное окно освобождено. Freeze-A сохранён `NOT_STARTED`.

Raw root: `/mnt/explorer-data/explorer-work/transfer-contact-probe-v10-20261027-b`.
Campaign result SHA:
`f18297d7e761542fd9e8fc69b380fc7e30829331b1f1776fbf210859ef7688ef`.
Independent report SHA:
`d0556fae852031a8673ff0de0a90f8ff0430f401494753dbe9465e32bd5ba4b8`.
Публичный полный receipt:
[transfer-contact-probe-v10-results-20261027.json](transfer-contact-probe-v10-results-20261027.json).
