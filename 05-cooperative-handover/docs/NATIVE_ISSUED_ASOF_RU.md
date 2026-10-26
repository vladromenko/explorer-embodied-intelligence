# Источник текущей командной оценки

Factory journal записывается до `Publisher.publish`; поэтому его servo pose
не доказывает публикацию. Native cooperative observation теперь отдельно
вызывает прежний reference/fault guard, но берёт все9 командных значений только
из actual Tap: последние аргументы и возврат **того же** original publisher,
плюс квитанция настоящего исходного base request. Неизвестная база не ноль.
Квитанции сохраняют boot/domain и native timestamps; это host publication return,
не ROS CDR receipt, MCU acknowledgement, достигнутая поза или удержание объекта.

Pending original arm/base call запрещает наблюдение частичного контекста.
Неопределённый возврат сохраняет unknown до новой настоящей публикации.
Arm receipt сохраняется внутри существующей publication guard transaction,
чтобы два concurrent caller не переставили историю после возврата. Guard arming
тоже использует actual issued pose, а не потенциально будущий factory journal.
Внутри publication boundary нет filesystem/reference I/O, новой камеры,
ROS publisher, потока или изменения исходных Ruckig/lease/controller settings.

68 targeted software tests прошли, включая journal177 против фактически
опубликованного90, блокированный publisher, отказ базы после исходного нуля,
восстановление только новым actual return и arm guard с ложной журналируемой
позой. Exact retained full web factory:4passed; оригинальный свежий старт
запросил HOME только через injected fake publisher как отрицательный контроль,
candidate aged-start дал0 actuator requests, candidate fresh-start отказал до
создания owners. Физические устройства в этой квалификации не использовались.

Immutable предыдущая factory qualification сохранена отдельно. Новое
свидетельство `NATIVE_ISSUED_ASOF_QUALIFICATION_20261026.json` связывает этот
исходник с четырьмя новыми receipt SHA. Это не remote deployment и не разрешение
агенту на HOME, begin, authorize или движение. Старый live owner без нового
actual Tap packet корректно блокирует новый106D worker.
