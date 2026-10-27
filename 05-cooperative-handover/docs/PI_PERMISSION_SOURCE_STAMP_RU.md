# Source время Pi permission status

`StationObservedPermission.status().source_stamp` содержит literal
`{monotonic_ns, boot_id, clock_domain}` самого Pi, снятые после существующих
metadata reads. Поле доступно и при отсутствии/истечении USER grant; это не
permission, измеренная стационарность или доказательство удержания.

Новый timestamp не вызывает дополнительных camera/Station методов, не
продлевает `observation_deadline_ns`, не делает native команд и не меняет
замороженный Station.py. Root может проверить исходный Source deadline
относительно Source stamp и консервативную длительность собственного GET pair,
без сравнения абсолютных часов Homebrain/Pi.
