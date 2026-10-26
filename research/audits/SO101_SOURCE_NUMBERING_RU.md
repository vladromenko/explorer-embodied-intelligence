# SO101 source-role: новая нумерация 04, без изменения управления

Новые Source SkillSpec, версии, сессии, capture context, эпизоды, события,
очередь ACT и новые карточки используют
`experiment_id('so101_source_learning')`, сейчас04, и
`experiment_numbering_version=2`. Общая таблица ролей определяет номер;
новый06 относится к переносу между воплощениями. У source catalogue остаётся
нативное общее действие SO101 follower6 с исходными именами и единицами.
У ROSMASTER02/03 сохраняется целый9D контракт. Composite05 ссылается на
конкретные версии ROSMASTER02 и source04, не объединяет их действия/наборы.

`SOURCE_SENSORS`/`SOURCE_PHASES` — действующие имена. Старые `SENSORS06` и
`PHASES06` оставлены как deprecated import aliases той же семантики SO101,
без привязки к новому transfer06.

Новый `_start_record` сохраняет canonical04/2 даже при явно legacy запросе
06/v1; точный исходный request identity остаётся отдельным полем. `personal`
— однозначный старый alias, неизвестный исходный epoch не объявляется v1.
Числовое06 без epoch не интерпретируется как SO101. Старые завершённые записи,
байты/SHA, исходные номера, Spec и модели не переписываются.

Source inspection читает оригинальный producer marker, либо доверенный
hash-bound migration index (`numbering_provenance.source=
verified_migration_index`, original_project_id/original_numbering_version,
projection/index SHA), либо явный server-stored source_numbering_version,
полученный import adapter из проверенного источника. Отдельный номер страницы
не подтверждает семантику исходного эпизода. Unresolved numeric06 остаётся
читаемым для анализа; его нельзя принять как whole-skill expert. Качество
старых hold/raw архивов не повышается из-за смены номера или нового разбора.

Raw Spec06 с явно объявленным epoch1 может быть прочитан/подтверждён без
замены его тела/SHA; canonical_identity указывает source04. Редактирование
создаёт следующую версию04/2, сохраняя прежнюю. Bare06 без v1 не становится
source-Spec. Внутренние сервисы используют исходный Spec SHA; проекция для
показа не подменяет этот SHA хешем изменённого представления.

Контроллер не менялся. В
`SO101_NUMBERING_CONTROL_INVARIANCE_20261026.json` сохранены AST SHA всех18
неизменённых методов Station, включая _connect, _write, _work и _sample.
Изменились только metadata части _start_record и _finish_record_locked;
_record_context принимает новые поля нумерации. Частота, настройки, калибровки,
порты, движения, torque/motor calls не добавлялись. Тесты записи используют
строгие no-hardware fakes; существующие завершённые байты проверяются на
неизменность. Реальные камеры, шасси и обе руки не запускались.

Фактические проверки 2026-10-26 на Homebrain:63pass/1 deliberate optimizer
skip, включая namespace, legacy v1, projections, immutable Spec revision,
recording serialization, station operations и настоящий officialDataset
readback. Отдельный own4GiB/2CPU unit выполнил новый04/2 ACT6 путь:
официальный Dataset4эпизода/32кадра,8 настоящих optimizer updates,
save/reload0, карточка SIMULATION_ONLY. Тест1pass14.25s, весь unit17.625s.
Loss1.107820→.293283. Все опубликованные checkpoint SHA побитно совпали с
доренумерационным fixture-прогоном: metadata не изменили обучение модели.
Новое свидетельство: `SO101_SOURCE04_ACT_QUALIFICATION_20261026.json`.
Старые qualification receipts и их исходный Spec06 не изменены.

Эти проверки подтверждают программную маршрутизацию и сохранность, не
предметный навык, shadow или физическое исполнение. Производственное
применение нумерации и общее API/UI отображение выполняются root отдельно.
