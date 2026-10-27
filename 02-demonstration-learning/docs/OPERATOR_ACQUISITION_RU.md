# Запись показов02: программный путь и реальные ограничения

Один интерфейс: `/experiments/02`, HTTPS нужен для Browser Gamepad API.
Настоящий Nano gateway, клавиатурная раскладка, ManualTeleop/ManualArm,
контроллерные настройки и firmware сохраняются. Агент не выдаёт разрешение
на движение и не выполняет HOME или тест движения.

Запись использует существующий владелец камер и GET-only native tap:

- название/описание задачи, выбранная SkillSpec/version, session/split;
- start/pause/resume/cancel/finish, отдельные outcome и post-seal approve/reject;
- независимо записанные кадры с исходными IDs/clocks и доступные raw сенсоры
  даже без публикаций руки; это не синтетические action samples;
- исходные operator requests и native replies с Nano request/return stamps,
  client-declared gamepad inputs/calibration отдельно от native keyboard wire;
- actual native host-publication receipts, prior command estimate и masks;
  HTTP200 не становится accepted action, MCU dispatch или measured attainment;
- paused действия остаются в raw source pages, но исключаются из BC samples;
  source-bound recording segments запрещают склеивать chunks через паузу;
- кадры и sensor payloads сохраняются в immutable TAR с SHA; JPEG preview и
  download не изменяют архив. Cancelled архив сохраняется и не одобряется.

Пауза записи не является STOP робота. Для остановки используется прежняя
кнопка STOP/deadman/focus-loss/network-loss. Запись файлов и сенсоров выполняется
в независимом writer; passive input callback не ждёт writer lock/fsync.
При переполнении очереди или потере evidence запись теряет допуск к обучению.
Недоступные измерения остаются masked/unknown.

Отдельное явное approve связано с точным archive SHA и authenticated actor.
Последовательность review сохраняется отдельно от raw manifest/outcome.
Только approved completed originals автоматически попадают в import queue.
Worker проверяет pinned review до bulk GET и после передачи; поздний reject
исключает источник из нового training cohort. SHA-bound `.part` переживает
обрыв; «Повторить перенос» создаёт новый job с прежним review и retry lineage.
Range resume требует настоящего206, Content-Range и полного SHA.

Gamepad использует существующий keyboard executor: стики задают направления,
штатная скорость/сглаживание остаются у Nano. Есть motion-free input preview,
30+ resting samples для калибровки центров четырёх осей и deadzone. Изменённые
центры не применяются к активному управлению. Нестандартная mapping не угадывается;
включение требует явного подключения, свежей камеры и текущей USER permission.

## Проверено до deploy кандидата

59 targeted Python tests /8.48s для capture/service/lifecycle/approval/transfer/
worker/cohort/API/queue/camera guards; отдельно sensor-cache+capture42PASS/4.43s.
22 browser-gamepad scenarios, 8 Node render/camera tests; native control IO отсутствует.
Настоящий Firefox: desktop1440 и mobile390, реальный JPEG декодирован и свежесть
проверена, source frame ID/cache timestamp сохранены, overflow0/non-GET0.
Gamepad в browser qualification — явно программный fixture:50 resting samples,
центры откалиброваны, controller остался disconnected. Физический Mac gamepad
этим не квалифицирован. Initial browser fixture SyntaxError сохранён как
отдельный failed attempt; приложение им не менялось.

Артефакты: `/mnt/explorer-data/explorer-work/experiment02-operator-20261027/`.
Эти результаты не означают завершения02B или физически освоенного навыка.
02A+02B развёрнуты scoped commit722755dc: central/worker и собственные Nano observer/cache.
Pi и оригинальный Nano web остаются4c5f4f5b; их исходники/настройки/PIDs сохранены.
Полная frozen регрессия2191PASS/18SKIP,231Node. Строгие real expert/model/live-shadow
gates остаются OPEN. Добавлен отдельный режим технической записи наблюдений:
он не требует HOME, всегда analysis-only и не создаёт экспертных меток.

## Требуется наблюдаемая подготовка владельца

1. Открыть HTTPS Explorer, войти и выбрать02. Проверить, что видны предмет,
   захват и цель. При последнем GET камера смотрела в верхнюю часть комнаты.
2. Logitech не найдена среди действующих источников; read-only USB video
   inventory Nano пуст. Проверить подключение и доступный ракурс, не запускать
   второй USB capture owner. DaBai RGB реально доступен.
3. До движения пройти существующий мастер текущей наблюдаемой сессии. Native
   reference после прежнего telemetry fault невалиден; агент его не сбрасывает.
   Исходная процедура reference/HOME — только под наблюдением пользователя.
4. Подключить gamepad к Mac, найти устройство, проверить raw inputs/раскладку
   без движения, отпустить стики и откалибровать центры. Проверить deadzone.
5. Только в отдельно разрешённых условиях включить manual control и выполнить
   короткую проверку arm/gripper/STOP. Шасси остаётся запрещённым до отдельного
   текущего разрешения; программный фильтр не меняет native controller settings.
6. Выбрать/подтвердить skill, отдельную сцену/session/split; записать полный
   показ, отметить исход/фазы, просмотреть кадры и одобрить перенос. Начать
   с3–5 smoke-показов; дальнейший объём определяют охват и качество, не магическое число.
7. Физическую проверку обученной модели проводить только после software
   offline/shadow результатов и отдельного supervised preflight.

Actual17-frame observations-only Nano→NX проверка прошла без движения:0expert
actions, UNKNOWN outcome, raw TAR/preview/Range/SHA, отдельное analysis approve
и реальный import worker. Этот материал не является успешным демонстрационным
датасетом. MCU telemetry stale блокирует настоящий model shadow до подготовки владельца.
