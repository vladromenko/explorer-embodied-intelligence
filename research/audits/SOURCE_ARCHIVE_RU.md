# Точные исходники до квалификационного trial

`explorer_shared.source_archive` принимает явный `{original_path: expected_SHA}`
и создаёт новый неизменяемый каталог. Копируются точные Python/CAD/config bytes,
manifest сохраняет исходный locator, разрешённый реальный path, SHA, byte count
и ограниченный JSON context. Источники не меняются. Повторное использование
каталога отклоняется; неудачная подготовка сохраняет rejection receipt/уже
скопированные bytes вместо стирания свидетельств.

`loaded_python_sources(prefixes)` возвращает только явно выбранный загруженный
Python graph. Это **не** заявление о сохранении всей ОС, драйверов или complete
transitive runtime. Caller отдельно перечисляет XML/URDF/mesh/calibration/script
и версии/байты требуемого engine. Никакое окружение, устройство или модель из
архиватора не запускается.

`verify_source_archive(path, expected_manifest_SHA, recheck_originals=True)`
проверяет заданную caller immutable привязку, все retained bytes и повторно
оригиналы/разрешение symlink после trial. Копии остаются воспроизводимыми, даже
если оригинал изменён позже; такой поздний запуск не проходит recheck originals.
Ни наличие архива, ни совпадение SHA не доказывают качество/успех эксперимента.

Стандартные границы:512 файлов,64МиБ/file,256МиБ перечисленных bytes,64КиБ context.
Разрешённые legacy symlink aliases записываются явно и не переписываются.
Научная итоговая квалификация должна учитывать и source recheck, и независимые
исходы; source archive не повышает отрицательный outcome до expert.
