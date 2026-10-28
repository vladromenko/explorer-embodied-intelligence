# Очередь физических проверок владельца

Это только действия, требующие присутствия/оборудования. Программный backlog,
обучение на fixtures и deployment-подготовка остаются у Homebrain. Шасси не двигать.
Агент не нажимает HOME/grants/control/STOP на реальных устройствах.
Кнопки, которые сейчас заблокированы программным gate, не обходить; это не просьба
к владельцу исправлять ПО.02 готово программно;04 flow и03 gated software deployed;05 software/deploy завершён,06 capture UI готовится к MVP deploy. Physical gates остаются отдельными.

| ID / когда | Действие и точные доступные элементы | Ожидаемый результат / диагностика |
|---|---|---|
|P-CAMERA · перед реальными demos|Открыть `/experiments/02`, «Рабочая сцена»; лично проверить gripper/object/goal в одной Nano camera. На04 проверить единственную Pi Logitech, освещение/открытый объектив и значения «Ведущая / Ведомая»|Actual27Oct PiJPEG почти чёрный max2/255 сохранён;28Oct Pi показывает стол/частьSO101, Nano сейчас потолок; task scene не подтверждена. Нужны видимые руки/предмет/цель и оба6D cache-вектора; доступный pipeline или текст VLM не заменяют видимость. Агент не меняет настройки и не направляет камеры|
|P-REFERENCE · перед любой ROSMASTER пробой|При текущем `MCU telemetry is stale` лично проверить штатное питание/связь. Только при свежей телеметрии и свободной зоне использовать раздел «Штатный HOME и текущая командная оценка», его checkbox и «Выполнить штатный HOME под моим наблюдением», если reference действительно нужен|HOME реально двигает6осей к90°/4000ms; затем новый native-кадр/новое разрешение. Это command estimate, feedback остаётся OFF|
|P-GAMEPAD ·02 без motor enable|На HTTPS02 подключить геймпад к Mac; «Найти контроллеры» → выбрать устройство → «Калибровать центры осей». Проверить preview с отпущенными стиками, не нажимая «Наблюдаю ROSMASTER · подключить»|Raw inputs/центры/deadzone корректны, управление остаётся отключённым; неизвестную раскладку не угадывать|
|P-03-SHADOW · после software допуска trace UI|В ARM_ONLY, шасси неподвижно: вручную дать малую непрерывную траекторию только leader, записать changing-input shadow без motor dispatch|Записаны реальные меняющиеся leader samples; статический cache/100GET не подходит. Метрики считает ПО, пользователь не редактирует JSON/registration|
|P-03-BOOTSTRAP · после continuity PASS и отдельного текущего observed grant|Проверить labelled bootstrap provenance. «Наблюдаю сцену · включить контур» → «Текущая поза — начало» → отдельное удержание кнопки передачи; по одному малому вправо/влево, вперёд/назад, вверх/вниз, затем wrist/gripper. «STOP · контур leader» завершает свою попытку|Leader вперёд = ROSMASTER вперёд, recenter не даёт скачка, плавность без рывков; provisional0…100→170…30gripper имеет нужное направление. При несоответствии остановить пробу и записать ось/направление; достаточно выбора rotation/sign/zero, полная перекалибровка не нужна|
|P-PI-UPGRADE · DONE отдельно от physical skill|USER подтвердил силовое питание обеих SO101 OFF; первый переход old4c→newsource-only выполнен. Pi62031 disarmed/0writes/grantNone. Питание восстанавливает владелец только при готовности к отдельному наблюдаемому тесту; агент не выдаёт HOME/grants/control/STOP|Old43382 завершён без Node.close/STOP, original configs/calibs exact; passiveROS60s0. [Actual release](../_registry/evidence/experiment04-source-release-actual-ef3f5185-20261028.json). Прежний shutdown race/unknown detach negative сохранён|
|P-04-SMOKE · после нового operator-ready04 UI/release|Проверить питание/свободную зону; через выпущенный guided demo flow выполнить3–5 полных захват→подъём→перенос→отпускание→завершение. Просмотреть каждый исход/фазы и отдельно approve/reject|Это smoke, не обещание освоения; failed/unknown сохраняются. Новый UI deployed и проверен на generated inputs плюс actual HTTPS desktop/mobile GET. Реальное питание/сцена/показы/модель ещё не приняты; никаких automatic motion grants. [Кнопки/порядок](../04-so101-source-learning/docs/SOURCE_DEMO_UI_RU.md)|
|P-04-CALIBRATION · только для метрической object evaluation|Использовать существующую ChArUco/tabletop форму: мишень100%, измеренная клетка, разные виды и реальные контрольные точки; подтвердить overlay/accept только при совпадении|Реальные camera/workspace extrinsics с исходными изображениями; aperture/force не выводятся из endpoint commands|
|P-05-ZONE · после software flow05|Лично подтвердить видимость общей зоны в двух существующих views Nano и Pi Logitech; затем только отдельный observed manual/manual hold/release/handover|Нет третьей камеры; independent physical hold/transfer, а не FSM/HTTP receipts. Не начинать пока новый flow не допущен|
|P-06-SCENE · после accepted source04 и software06|Лично проверить различающиеся source/target сцены и workspace registration; собрать предложенные5/10/25 target demos и независимые observed trials через готовый UI|Реальная геометрия, разные камеры/action contracts, все исходы сохранены. Физический успех переноса отдельно от offline метрик|

Paused запись не означает остановленный робот. Разрешение/наблюдение/STOP всегда
отделены от data pause и learning. Новые реальные показы не становятся экспертами
автоматически. На01 сейчас пользовательских физических действий нет: только SIM,
optional physical deploy будет отдельным будущим gate.

Подробные протоколы: [02](../02-demonstration-learning/docs/OPERATOR_ACQUISITION_RU.md),
[03 bootstrap](../03-lerobot-teaching/docs/BOOTSTRAP_REGISTRATION_RU.md),
[03 continuity](../03-lerobot-teaching/docs/CONTINUITY_ACCEPTANCE_RU.md),
[Pi shutdown proof](../04-so101-source-learning/docs/OLD4C_LIFECYCLE_PROOF_20261027_RU.md).
