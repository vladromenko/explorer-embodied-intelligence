# V9: сохранённый отрицательный результат и новый контактный эксперимент

ENGINEERING_GLUE. Анализатор `scripts/analyze_transfer_contacts.py` читает только
сохранённые JSON/XML/архив исходников. Он не импортирует MuJoCo/Torch, не вызывает
dynamics/render и не исправляет старые данные. Закреплённый V9 campaign receipt:
`e016bc0324d8049a9415ef81c24c50c9cb1c0d0ea76fdc293fe077efed43b034`.
Исходный content SHA протокола:
`7bc5db9056d99de459b299036fa9e240650a190bb270ce481664afba673ba744`.
Каждая из четырёх попыток содержит 439 действий и 10975 подшагов. Анализатор
сверяет SHA result/trace/XML/архива и каждого retained source object, проверяет
непрерывность native часов и сопоставляет каждый подшаг исходному phase/action.
Повторная проверка исходных файлов выполняется после анализа.

| Исходная сцена | Исходный полный SIM исход | Минимум Z при release, м | Последний минимум Z, м | Последний bin-bottom scalar, Н |
|---|---|---:|---:|---:|
| newjitter-7331 | success | −0.006799 | 0.007892 | 0.123606 |
| near-left-7332 | failure | −0.012019 | −0.010017 | 32.698195 |
| out-yawplus-7333 | success | −0.006894 | 0.007892 | 0.123606 |
| in-yawminus-7334 | failure | −0.011143 | −0.010321 | 38.663184 |

Все четыре предмета освобождаются на высоте центра 0.235–0.236 м; первая
записанная сила от дна возникает при скорости приблизительно 2.07–2.09 м/с.
Обе неудачи впервые пересекают центром середину slab Z=0.004 м в подшаге,
в котором scalar силы от дна впервые превышает 20 Н: 16.226 и 16.230 с.
В этих двух подшагах силы pad равны нулю. В двух успехах центр не пересекает
эту середину, хотя minZ также временно отрицателен. Масса из XML 0.0126 кг,
вес при gravity 9.81 составляет 0.123606 Н.

Архивный `manipulation_env.py` SHA
`a0bf6c275002d6a9ce457d1e38cbbc188b8edd13cfe1adbfa3026d533c8ffe8a`
показывает, что scalar — сумма **неотрицательных нормальных величин только
пар предмет–bin_bottom**. Это не сила пола и не знаковая сумма world-Z.
Исходная запись не содержит normals/geom pairs/solver stats каждого подшага.
Прежний terminal mj_forward recomputation отдельно остаётся recomputation,
а не измерением в оригинальном dynamics loop. Поэтому итог — `AMBIGUOUS`:
тонкое дно, сильный свободный удар и конфликт контактов пола/дна — проверяемая
гипотеза; конкретный механизм solver и контакты других частей руки ещё не
исключены.

В реальном V9 XML указан `integrator="discrete"`. Установленный официальный
MuJoCo 3.17.0 содержит `mjINT_DISCRETE=4`; минимальная компиляция XML без CAD
вернула этот enum. Это численный интегратор, а не название collision режима.
Экспериментальный IPC — отдельный flag, который V9 XML не задаёт. Описание
интеграторов и IPC: [официальный XML reference](https://mujoco.readthedocs.io/en/latest/XMLreference.html#option).
SHA installed `mjtype.h`:
`d1a9debb9d2453b9baf80ce1df79dcb8b4930bd695457b2af5d8bb39b8510b1f`.

Новый V10 проект сохраняет все четыре исходных случая, включая оба успеха.
Каждый получает четыре заранее выбранных условия:

1. Сохранённая физика: discrete, dt=0.002 с, half-height дна 0.004 м.
2. Только dt=0.0005 с.
3. Только half-height дна 0.020 м; верх Z=0.008 м прежний, центр Z=−0.012 м.
4. Прежняя физика; те же XY/orientation предмета, minZ=верх+0.015 м,
   все шесть его начальных velocity равны нулю.

Начальное состояние каждого NEW опыта берётся из source-bound native row317
при исходном времени 16.1 с: предмет уже free, контакта с дном ещё нет. Это
**новое начальное условие** после свежего reset, time=0, warmstart=0 и declared
RNG seed7401. Состояние не является восстановлением отсутствующего полного
начального состояния V9. Все actuator ctrl удерживаются исходными constant;
последующие V9 actions не проигрываются. Рука продолжает собственную физику,
поэтому её реальные контакты также записываются. Low-drop условие проверяет
контактную чувствительность, а не реализуемость безопасного robot placement.

16 новых попыток по 1 с дают 14000 scored contact native шагов. Поддерживаемый
настоящий env отдельно выполняет constructor reset100 и explicit reset100 до
пересадки NEW initial condition: ещё 3200 служебных native шагов, всего 17200
planned calls. Они явно отделены от новых contact outcomes. Статическая позиция
bin берётся из XML каждой исходной сцены и проверяется после компиляции;
integration vector не используется как замена `model.body_pos`.
Ресурсы: sequential fresh
subprocesses, CPU1 thread, MemoryMax2 GiB, swap0, wall max360 с, disk budget6 GiB;
renderer/EGL/GPU отсутствуют. Изменяется только новый скопированный SIM XML,
исходные env/provider/defaults и старые результаты остаются прежними.
До компиляции replacement CAD driver сохраняет только маленький immutable ABI
(counts/dtype/shape/index bytes), освобождает прежние env.data/env.model и
выполняет GC. Два полных CAD model/data не удерживаются одновременно; fake
weakref regression проверяет освобождение **до** вызова нового compiler.
Зарегистрированный initial helper применяется только после действительных
проверок его поддерживаемого env/provider/static/mutable профиля. Неподдержанный
профиль завершает попытку **до первого dynamics шага**, без fallback/обхода.
Snapshot включает native/MJB/profile/RNG/source SHA, но отдельная новая PID
restore/reexecution qualification остаётся `UNQUALIFIED`, пока не выполнена.

Каждый новый подшаг должен сохранять пары geom/body, contact position/frame,
signed distance, local wrench, signed world force на предмет, pose/velocity,
solver niter/nnz/stats/warnings. Оси contact frame находятся в строках: world
force = frame.T × local force; знак меняется, если предмет — geom0.
Основания: [официальная simulation documentation](https://mujoco.readthedocs.io/en/3.9.0/programming/simulation.html),
[официальный mjdata.h](https://github.com/google-deepmind/mujoco/blob/main/include/mujoco/mjdata.h),
[официальный XML reference о направлении force](https://mujoco.readthedocs.io/en/3.13.0/XMLreference.html#contact-pair).
Pure tests проверяют world conversion на повернутых contact axes и обеих
позициях предмета; перед новым CAD выполнением нужен также frozen driver.

Все setup failures/unknown/started attempts сохраняются, exclusions/retuning
после начала запрещены. Этот denominator относится только к contact engineering
development. Он не допускается в optimizer, не является H4 сравнением,
новым полным task success, learned-source qualification или физическим успехом.
