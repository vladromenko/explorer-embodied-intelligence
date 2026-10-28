# Компонент06: source-модель → исходная геометрия → сохранённый мастер

Статус: **IMPLEMENTED / TARGETED_SOFTWARE_TESTED / NOT_DEPLOYED**.
Это начало E06-PIPELINE. Target9 dataset, adaptation5/10/25, полная UI-проверка,
полный regression/release06 и физическое принятие ещё не завершены.

Общий SO101GeometryContext используется старым NativeLeaderContext03 и новым
follower06. Leader URDFbc947e… и follower URDF3a65d2… выбираются явно. Encoder/FK,
исходные servo coordinates, command gripper references и SourceCoordinates
переиспользуются; command estimate не называется measured state. Existing03
status/preview и bootstrap contracts сохранены. Station SHA не менялся.

Authenticated Node GET `/station/calibration-artifact` принимает только role и
expected_sha256. Он возвращает исходные configured-file bytes/base64/SHA, не
подключается к устройствам и не перепроверяет аппаратную калибровку. Root06
принимает model_job_id, повторно проверяет текущий04 admission/checkpoint/Spec,
связывает exact follower SHA и строит существующий audit A/B/C. Physical source
использует existing authenticated Pi metadata transport; observed permission
client для этого чтения не создаётся. SIM требует исходный calibration artifact
в неизменном source manifest: SHA без байтов не превращается в выдуманный диапазон.

Новые data-only routes: GET `/api/transfer/geometry`, POST
`/api/transfer/calibration`, GET `/api/transfer/calibration/{wizard_id}`.
Клиент не передаёт путь, audit, измерения, domain, skill UUID или native grant.
Сервер сохраняет возрастающую target version того же source UUID, точную model/
aggregate/checkpoint/calibration binding и authenticated actor. Позднее изменение
source/weights блокирует использование прежнего мастера, сохраняя его события.

UI06 позволяет отдельно проверить геометрию до принятия предметного source skill,
показывает оставшиеся метрические и научные проверки, сохраняет и восстанавливает
мастер. Response scopes/counters отвергают поздние ответы другой модели; target
version сохраняется при re-render, data request ID повторяется после lost reply.
Нет native control/permission/action transport, автоматического target training
или объявления physical registration. SIM wizard не даёт physical readiness.

Проверки:78 Python13.62s обоих HTTP consumers, затем32 source-boundary8.48s;
291Node5.24s. Модели в новых route fixtures — явно structural SOFTWARE placeholders,
не optimizer/model-quality evidence; HTTP loopback и fake native dependencies.
Отдельный actual generated ACT2 source metadata preflight подтверждает loader,
но hold/no independent object trace и отсутствие original calibration bytes
ограничивают его применение к переносу. Новых optimizer updates и physical IO0.

Старые optimizer receipts не переписываются. Новый exact inverse source receipt
для двух router-install строк цепляется к сохранённому05 compatibility receipt;
весь прежний app.py и его training routes восстанавливаются до прежнего whole-file
SHA. Неизвестные изменения не разрешаются этим receipt.
