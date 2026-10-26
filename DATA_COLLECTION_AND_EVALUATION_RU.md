# Сбор данных, независимая оценка и экспорт Explorer

Это рабочий протокол платформы, а не отчёт об освоенных физических навыках.
Каноническая нумерация — v2; оригиналы до миграции сохраняют свою нумерацию и SHA.
Машиночитаемый контракт: [collection-export-v1.json](research/protocols/collection-export-v1.json).
Статус полного исследования остаётся `IN_PROGRESS`. Агент не двигает шасси или
руки; физический показ/испытание запускает пользователь в отдельной текущей
наблюдаемой сессии. Исходные настройки ROSMASTER не меняются.

## Что именно собираем

| Опыт | Источник | Наблюдение и целевое действие | Ограничение |
| --- | --- | --- | --- |
| 01 | MuJoCo ROSMASTER, scripted expert или обученная policy | Истинное состояние симулятора сохраняется отдельно от доступного policy RGB/состояния; native action9 | Успех SIM не является физическим успехом |
| 02 | Клавиатура/геймпад пользователя → исходный ROSMASTER | RGBD/доступные сенсоры, requested/accepted/dispatched, native action9: arm6 + base3 | Feedback руки выключен; command-estimate и reference90 — оценки, не измерения |
| 03 | Измеренный SO101 leader → тот же исходный ROSMASTER | Все6 измерений leader, source→target mapping, оригинальные native dispatch и action9 | Leader не является измерением target; совместные клавиши базы сохраняются в том же lease |
| 04 | Две SO101, public source skill workflow | Оригинальный RGB, измеренный follower6, dispatched action6, Spec/version/фаза/вариант | Сохранять один владелец камеры и существующих servo buses; предметный исход подтверждается отдельно |
| 05 | ROSMASTER giver + SO101 receiver | Два исходных native эпизода, два action head9/6, две камеры/clock domains и общий sidecar фаз | HTTP ACK, closed gripper и таймер не доказывают удержание/dispatch/передачу |
| 06 | Закреплённый source04 → отдельный target ROSMASTER | Source dataset/model SHA, измеренная геометрия, target-native action9 и target adaptation/eval | Joint replay разных роботов — только SIM baseline; source6 не превращается в target9 переименованием |

Показ начинается с подтверждённой версии Spec. Явно выбрать задачу, предмет и
его экземпляр, сцену, collection session, train/development/sealed split, вариант,
первую фазу и whole-episode/targeted-subskill. Нельзя автоматически угадывать фазу
по клавишам, именам файлов, закрытию клешни или VLM-тексту.

В эпизоде нужны camera/robot/action profiles, boot и clock domain, исходный
frame ID/JPEG SHA, monotonic source ticks и timestamp basis. Для разных машин
сохранять независимые clocks и оценки network receive/offset uncertainty; не
вычитать напрямую Pi/Nano/NX monotonic. При отсутствии exposure timestamp писать
`host_receive_estimate` или фактическое исходное основание, без заявления точной
экспозиционной синхронизации. Потерянные кадры, очередь и ring gaps сохранять.

`requested`, `accepted`, `dispatched`, `measured`, `estimated` — разные поля.
Отсутствующий канал остаётся null с validity mask/reason; нули нельзя подставлять
вместо неизвестного состояния. Полный lidar сохраняет порядок и невалидные лучи
с масками; отфильтрованное облако close points не является полным сканом.
Неизвестная covariance не становится известной точностью ориентации.

## Проверка и допуск данных

После остановки записи сохраняются исходный архив, manifest и независимые SHA;
импорт не удаляет оригинал узла. Связь с public capture intent и Spec проверяет
сервер. Не завершать чужую запись после позднего ответа: проверять её original
episode/recording ID. Незавершённая запись, STOP и неизвестный outcome остаются
в реестре. Исправления обзора — новые события, не перепись исходного архива.

Просмотреть целый эпизод и его фазы, отметить intervention, success/failure/unknown
и наблюдаемое основание исхода. Строгий aggregate admission повторно проверяет
сырьевые SHA, Spec и принятый cohort, action/observation contracts и пересечения
исходных эпизодов/сессий/decoded RGB. Observation-only sensor recording и общий
sidecar05 сами по себе не создают BC action targets. Для05 оба native оригинала
должны независимо пройти свои исходные inspectors и относиться к тому же
фактическому сеансу/предмету/Spec/split/domain.

Train, development и sealed_test разделять минимум по episode и collection
session; для заявлений о новых объектах/сценах — также по object instance/scene.
Копии кадров, повторные импорты и куски одного эпизода не независимые примеры.
Нормализаторы считать только на train. Development разрешён для выбора настроек;
sealed payload не передаётся fitter и не используется для выбора checkpoint.
После знакомства с sealed результатом новая настройка требует новой независимой
серии, а прежняя остаётся в истории.

## Обучение и самостоятельная попытка

ACT — текущий baseline, Diffusion — отдельный реально квалифицированный backend
для узкого source native6 CPU профиля. Квалификация9D CUDA не распространяется
на source6,106D fused profile или другую сборку runtime. Сохранять executable,
actual package/source/config bytes SHA, seed, train-only processors, optimizer,
RNG/sampler state и число завершённых updates. Отдельно показывать wall time,
RSS/UMA budget, inference latency и checkpoint/reload error.

Loss, optimizer8steps, точный replay и shadow без публикации подтверждают разные
технические свойства. Они не являются независимой попыткой предметной задачи.
Shadow должен использовать причинно предшествующий свежий native observation
packet того же boot/camera/calibration; inference по старому видео обозначать
offline replay. Ни shadow, ни выбор модели не разрешают движение.

До sealed оценки создать и зафиксировать SHA протокола с задачами, независимыми
success predicates, условиями/baseline revisions, trial order/seeds, split manifest,
числом испытаний, разрешёнными exclusions и одинаковыми compute budgets. Начать
с development pilot; затем выбрать объём серии по неопределённости/мощности.
20 попыток на условие — возможный старт, не доказательство достаточности.
Пустой шаблон и неначатая серия не считаются проведённой оценкой.

Сохранять каждую начатую попытку, включая неудачу, intervention и safety stop.
Единица оценки — полная независимая попытка, не кадр и не удачная фаза.
`research/benchmark/evaluate.py` проверяет frozen protocol, disjoint splits,
trial provenance и отдельно считает SIM/PHYSICAL/OFFLINE_REPLAY. Показывать
success/total, unknown fraction и identification bounds; Wilson95 только для
показанного соответствующего Bernoulli denominator. Не исключать неизвестные
исходы ради улучшения основной доли успехов.

Минимальные сравнения: независимые ACT specialists; target-only; matched02/03
на том же target/сценах; source+target с фактически доступными5/10/25 показами;
FK/IK/effect transfer; independent components и полная composition05;
retention старых задач после нового обучения; ablation каждого нового компонента.
Отказы и невозможность воспроизвести сильный baseline сохранять как ограничения,
без замены его заведомо слабым суррогатом. H1–H5 остаются гипотезами.

## Экспорт и воспроизводимость

Экспорт — отдельный immutable bundle с manifest, а не ссылка на изменяемый каталог.
Контракт ниже задаёт формат; UI упаковщик должен сообщать `PARTIAL`, пока не
включены и не проверены все требуемые оригинальные bytes. Существующий verified
state backup является recovery backup и не заменяет научный anonymized export.

В manifest: export/run/episode/job IDs, canonical semantic identity и исходная
numbering version, source commit, protocol/spec/dataset/model/processor hashes,
parents, units/domain/clock semantics, список файлов с относительными именами,
размерами и SHA256, отсутствие/причина пропущенных зависимостей. Для каждой
производной метрики указать original trial IDs и generating implementation SHA.
Оригинальные event/card/config/result bytes не переписывать ради новой нумерации;
прикладывать отдельно verified canonical projection/index binding.

В scientific bundle включать только выбранные исходные эпизоды/профили,
нормализаторы/checkpoints, frozen protocol, trials, reviews, negative outcomes,
зависимости и reproducibility environment receipts. Credentials, login tickets,
sessions, node tokens, SSH keys, Wi-Fi settings и пользовательские секреты не
экспортировать. Anonymization — новая производная копия с картой преобразования
и ссылкой на приватный SHA оригинала; не подменять ей исходник.

Упаковщик не должен следовать symlink за managed roots, принимать absolute/path
traversal имена TAR, считать изменившийся при копировании файл подтверждённым
или удалять originals после упаковки. В отдельном пустом каталоге проверить
каждый размер/SHA, импорт manifest и пересчёт таблиц из trials; только затем
отмечать `VERIFIED_COMPLETE`. Отсутствующий полный текст/зависимость/clock факт
остаются явными. До подтверждения источника и восстановления удаление запрещено.

## Текущее покрытие

Рабочая БД действительно мигрирована на numberingv2 с побайтной сохранностью
старых scientific bodies; backup восстановлен отдельно со всеми SHA. Есть
отдельные технические ACT/DP/save/reload/queue proofs и отрицательные независимые
SIM результаты. Предметный физический source expert, полный transfer/handover,
новая tabletop accuracy и научные H1–H5 пока не подтверждены. Эти пробелы нельзя
закрыть числом инфраструктурных тестов или новым текстом документации.
