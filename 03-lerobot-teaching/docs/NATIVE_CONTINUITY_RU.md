# Границы непрерывности 03, 27 октября 2026

Это программная квалификация и анализ сохранённого входа. Физический путь не
развёрнут этим изменением. Полная проверка меняющейся реальной траектории OPEN.
Исходные `RelativeLeaderGeometry.propose`, Ruckig, период .04, lookahead .08,
оригинальная .45s lease, настройки/прошивка/readback и Station.py не меняются.

## Acquire и стабильный ACTIVE

Start/recenter возвращают CLUTCHED. Только новая явная нейтральная held-команда
с epoch/sequence/observing проходит admission. Status/reference проверяются до
resume; после original resume+neutral считывается текущая command reference,
затем самый свежий measured leader, проверяются identity/gripper и вычисляется
clutch anchor. Движение лидера за время admission IO не воспроизводится.
Дополнительный reference GET существует только на admission: он НЕ продлевает
lease. Если исходный native deadline истёк, первый intent отвергается штатно;
автоматического resume/retry/lease нет.

В ACTIVE цикл/числа geometry неизменны. Один stale, duplicate или out-of-order
sample отбрасывается: нет HTTP intent, подстановки zero, изменения epoch/state,
recenter или lease. Clock/boot/calibration/source-owner drift прерывает попытку.
Долгая потеря источника остаётся под исходным native deadline. Первая пригодная
поздняя команда не обходит native expiry. MOBILE lease не добавляет status GET:
текущую chassis permission проверяет тот же original native base endpoint.

## Гриппер: только admission

Absolute calibrated gripper mapper в ACTIVE не изменён. Перед resume mapped
leader gripper должен попадать в ТОТ ЖЕ существующий integer endpoint, что и
фактический `servo_deg` original reference. Floating `q_estimated_deg` остаётся
command estimate, не measured q. Сам estimate тоже должен сериализоваться в
тот же endpoint; старый незавершённый trajectory estimate не переименовывается
в стабильную позу.

Чистый `native_gripper_alignment.py` применяет существующие np.rint/retention
правила exact `arm_velocity.py` SHA
`ee5300c7731b47806201e5509e8cad69e275f4e87812fb79d91964638159fd7c`:
`abs(endpoint-previous_integer)<0.55`. Это существующая сериализация, не новый
deadband. Проверяются mapped candidate и границы его неопределённости в один
encoder tick: official gripper normalization `100/(range_max-range_min)`.
Для сохранённой calibration9512… диапазон2035..3274, tick100/1239. Погрешности
НЕ складываются в разрешение перепрыгнуть wire-grid endpoint. Неизвестная
calibration/context/native backend/integer wire reference блокирует acquire.

Status `gripper_alignment` содержит normalized source, mapped absolute value,
current command estimate/wire endpoint, delta, tick bound, source hashes и
причину нужного совмещения. Нет relative offset, новой шкалы/clamp, утверждения
о физической aperture/contact. Только два наблюдённых endpoint и task axes
могут зарегистрировать реальную геометрию; отсутствующие B не выдумываются.

## Конечный offline helper

`retarget_continuity.inspect_recorded_trace(bytes, source_payloads)` проверяет
SHA каждого оригинального Source JSON и точное соответствие trace projection.
Pi source dt/derivatives и Homebrain request RTT считаются раздельно, timestamps
вычитаются как integers до перевода ns→s. Нет вычитания часов разных машин.

`qualify_recorded_trace(..., geometry_factory=..., target_reference=...,
original_native_period_s=...)` при явных аргументах запускает actual Pi service
в конечном replay с memory sink и сравнивает его vectors с неизменённым
geometry.propose на тех же входах. Это НЕ native publication/Ruckig/MCU
qualification. Synthetic geometry остаётся synthetic в отчёте. Отсутствующая
реальная registration/reference означает BLOCKED, не фиктивный prior/zero.

Метрики включают source dt/frequency/jitter, max step, velocity/acceleration/
jerk, dropped samples, state sequence, exact vector equality, zero/substitution
counts и явные ограничения timing/attainment. Replay wall frequency не считается
частотой hardware. Сырые source bytes не переписываются.

Read-only CLI для сохранённой трассы:

```
python scripts/qualify_03_continuity.py TRACE.json --output REPORT.json
```

Полученная Root GET-only трасса SHA
`226113810f57771f7fdb4a19cfbc5f0f21f4de2152404980464f25bab8556e14`:
100 samples,100 unique Pi timestamps,mean10.0808478Hz; все6 position ranges
ровно0. Homebrain GET RTT min5.863277ms/p95 15.208449ms/max23.631450ms.
Это настоящий STATIC input subtest. Реальной changing trace, наблюдённой
cross-map B и действительной native reference сейчас нет. Mapped replay
BLOCKED; full_continuity_qualification OPEN, task_completed false,
physical_commands0. Отчёт `CONTINUITY_20261027.json` хранит hashes/facts.

Тесты используют mock owners/actual NativeMux, actual repository FK/IK,
извлечённый exact original quantizer; проверяют jitter/drop без chatter,
delayed permission/resume/neutral, repeated clutch, original expiry,
foreign identity/epoch и late close/STOP. Во время проверки реальные камеры,
сервоприводы, native HTTP и физические операторы не вызываются.
