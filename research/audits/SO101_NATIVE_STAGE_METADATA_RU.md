# Source-native аннотации фаз SO101

`SO101Station.record_stage(body)` — отдельный metadata API без управления.
Root NodeAgent может направить `/station/record/stage` непосредственно сюда;
existing Station.command/control dispatch менять не требуется. Новые record
должны иметь canonical source identity04/numbering2. Body:

* episode_id (или recording_id) — точный активный эпизод;
* phase_id — bounded ASCII identifier, request_id — неизменяемый retry ID;
* annotation_source, phase_scope (`source_skill_phase`, `coordinator_phase`
  или `operator_annotation`);
* optional coordinator_context — исходные чужие clock/boot/context bytes в JSON,
  без переименования их в source time.

Приём формирует собственный Pi monotonic/clock_domain/boot_id stamp, отдельный
annotation ID/sequence и intent SHA. Повтор того же request возвращает исходный
stamp; изменение intent под тем же ID отклоняется. Caller не может подставить
native_stamp, contact/success или неподдержанные поля. Неверный/завершённый
эпизод, oversize, NaN и слишком длинная история отклоняются без записи команд.

Начальный skill context/phase_id не меняется. Последняя аннотация размещается
в отдельном source_context.native_phase_annotation. Уже существующий _sample
делает глубокую копию этого context, поэтому предыдущие строки не получают
новую фазу задним числом. Все stage_events сохраняются в finalized manifest
и обычном source archive. Ни _sample, ни _work, ни _record_writer, ни control/
write/connect методы, частота или настройки контроллера не изменены.

Receipt честно указывает accepted-in-memory/durable=false: дополнительный
manifest writer не создаётся. Долговечное доказательство — только проверенный
финальный source manifest/archive; незавершённый или потерянный эпизод не
становится обучающим. Периодический существующий writer также может сохранить
метаданные раньше, но receipt этого не предполагает.

`inspect_native_phase_annotation(sample, episode_id=...)` отдельно проверяет
episode, native clock/boot и annotation<=measured-state<=dispatch. Будущий
stamp или другой boot даёт unavailable/None, а не нулевой phase. Эта проверка
определяет только причинную аннотацию состояния. Камера могла получить кадр
до аннотации; frame_predates_annotation явно отражается. Истинное exposure time,
контакт, достигнутая поза и успех задачи этими label/clock не подтверждаются.

Тесты работают только со строгими no-hardware fakes и временными файлами:
native/coordinator clocks разделены, snapshots/ретраи неизменяемы, финальный
archive содержит те же аннотации, недействительные поля не меняют active record,
и все18 ранее зафиксированных методов Station остаются AST-идентичными.
