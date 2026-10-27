# C-v2: все четыре TARGET ACT-кандидата не выполнили целый навык

На Homebrain выполнены все 16 заранее замороженных независимых SIM rollout:
8 development, затем 8 sealed, без выбора, повторных запусков или дообучения
по результатам. Каждая модель завершила 800 реальных native dispatch9 в каждом
из четырёх парных сценариев. Полный успех — **0/16**, отрицательных исходов —
16, UNKNOWN и исключений — 0. Все отрицательные траектории сохранены.

| Фиксированная модель | DEV полный успех | SEALED полный успех | Захват за 4 эпизода | Подъём / перенос / размещение |
|---|---:|---:|---:|---:|
| ZERO_ABS | 0/2 | 0/2 | 1/4 | 0 / 0 / 0 |
| ELAPSED_ABS | 0/2 | 0/2 | 0/4 | 0 / 0 / 0 |
| ZERO_INCREMENT | 0/2 | 0/2 | 0/4 | 0 / 0 / 0 |
| ELAPSED_INCREMENT | 0/2 | 0/2 | 0/4 | 0 / 0 / 0 |

Единственный подтверждённый evaluator захват — ZERO_ABS / SEALED86102,
без подъёма и переноса. Флаги `settled`, `released` или `measured_gripper_open`
по отдельности не доказывают помещение предмета в коробку. Ни один кандидат
не выбран и не повышен до освоенного или физически проверенного навыка.

Это TARGET visual student с настоящим target-rendered RGB и state10:
предыдущие фактические команды9 плюс ZERO либо причинное elapsed/40 s.
Различаются часы и absolute/increment labels; архитектура, исходные веса,
seed, бюджет 1200 CPU updates, один допущенный TRAIN показ из 439 кадров и
chunk8 совпадают. Три неуспешных TRAIN показа исключены из expert targets и
сохранены для анализа. Актор не получает phase, goal truth, evaluator facts
или teacher waypoints. Исходный teacher — SCRIPTED_SIM, поэтому это не
learned-source04 или подтверждение H2. Шасси закреплено штатным SIM weld;
все 9 каналов сохраняются, actual stationary projection явная. Навигация
этим опытом не проверяется. Используется тот же известный candy CAD с
заранее объявленными геометрическими возмущениями, а не новый класс объектов.

Числовой допуск C-v2 относится только к доказанному float64 округлению
исходного native interpolation при совпадающих float32 neural tensors.
Он сработал дважды: по одному разу в двух ZERO_ABS DEV эпизодах. Literal
prior9, raw predictions, requested, issued и native values сохранены.
Executor, provider, physics, четыре модели и labels не менялись.
Это исправление проверки входа, не улучшение модели.

Первоначальный C-v1 остаётся отдельным: **UNKNOWN1 / NOT_STARTED15**,
163 dispatch до числового отказа, SEALED не открывался. Его исходные файлы
не заменены результатами C-v2. Первый DEV C-v2 уже наблюдался при разборе
этого отказа; факт раскрыт в протоколе до новых запусков. Все четыре
checkpoint и оба split были frozen до независимой серии.

Машинная проверка после серии связала SHA всех 2073 исходных pins,
12 800 RGB/request/dispatch/context и всех initial payloads. Подтверждены
16 уникальных trial UUID и 16 native boot ID. Внутри каждого из четырёх
парных сценариев native initial state, XML, MJB и первый RGB побайтно
совпали между моделями. Это проверка сохранённых байтов, не новый physics
или RGB replay. Полное OS/runtime dependency closure здесь не проверялось.
Четыре компактные SkillCard прошли настоящий private Store roundtrip;
production registry не изменён.

Все rollout шли последовательно в отдельных CPU2 / MemoryMax4 GiB /
swap0 / max1800 s units. Optimizer в C-v2 не запускался; EGL использовался
для rendering. Максимальный фактический `memory.peak` — 2 329 845 760 B,
OOM/cap events — 0. Exit0 означает завершение драйвера; отрицательный
исход навыка записан отдельно. Все дочерние units завершены и reaped.

[Полный machine-readable receipt с моделями, всеми 16 исходами и SHA](target-temporal-factorial-C-v2-results-20261027.json)
ссылается на private протокол, evaluation freeze, summary, raw traces,
initial snapshots и проверку retained campaign. Следующий вопрос требует
диагностики, а не выбора победителя из нулей: единичный TRAIN пример,
temporal alias, action error, latent uncertainty и distribution shift
пока не разделены причинным тестом. Всеобщий master-prompt остаётся
**IN_PROGRESS**; физического исполнения и освоенного полного навыка нет.
