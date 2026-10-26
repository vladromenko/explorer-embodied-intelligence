# AW-0: решение для Explorer на 2026-10-26

**Решение: полный AW-0 на имеющееся оборудование не устанавливать. Веса не скачивать. Программную разметку и trace использовать как полезный подход к существующему skill cycle; нейронную память оставить отдельным условным кандидатом.** Полный выпущенный сервер не помещается в 16 ГБ общей памяти Orin NX. Маленький модуль памяти помещается, но это само по себе не доказывает пользу для текущего reach. Возможный опыт будет адаптацией методов AW-0, а не запуском AW-0 и не подтверждением H1–H5.

Статус этой проверки: первичные материалы прочитаны, публичные метаданные и пути загрузки кода проверены. `weights_downloaded=false`, `installed=false`, `AW0_executed=false`, `Jetson_qualified=false`. Выполнены только чтение документов/исходников и арифметика размеров. На устройствах ничего не изменялось; частные изображения никуда не отправлялись.

## 1. Какую модель проверили

Это **Agentic World Action Models / AW-0**, препринт [arXiv:2610.30136v1](https://arxiv.org/abs/2610.30136v1), поданный 21 октября 2026 года, авторы из Berkeley, NVIDIA и UPenn. [Официальный сайт](https://agentic-wam.github.io/) связывает [репозиторий](https://github.com/ZhuoyangLiu2005/agentic-wam) и [опубликованный checkpoint](https://huggingface.co/miniFranka/agentic-wam-pretrain-yamego3k-soup-e1). Наличие препринта не устанавливает рецензирование или принятие на конференцию.

AW-0 совместно генерирует видео, программу и действия: видеопоток Cosmos-Predict2.5-2B, кодовый эксперт SDAR-1.7B, отдельный action DiT. Дополнительно используются замороженная визуальная башня Qwen3-VL, Wan2.2 VAE и отдельный Cosmos-Reason1 для инструкции. По умолчанию программа обусловливает нейросетевую политику; в отдельном гибридном режиме RoboTwin она может исполняться через библиотеку примитивов. Это не наш компактный `explorer_sim/world_model.py`: наш модуль предсказывает следующий RGB/state по текущему RGB/state/action и не реализует трёхпоточный генеративный MoT. [Архитектура и режимы](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/README.md).

Проверенные фиксации:

| Артефакт | Версия / размер |
|---|---|
| Git | `7ad228bebb897bf01705cc10171e1d88cf5e9b22`, commit 2026-10-22T02:39:44Z |
| Checkpoint HF | `603c59fd434774a269978e704730ba60740b1c17`, ungated |
| `agenticwam_pretrain.pt` | 9,959,836,973 байт =9.276 ГиБ **файл**, LFS SHA256 `d13f68f0167dc6141db4e21a07fa5358d56d353f741b89beac343d2ebb690dc4` |
| Cosmos-Reason1-7B | `375e24000b24baed78f4618d3dd779e47cd96323`, HF tensor metadata: 8,292,166,656 BF16 параметров |
| Qwen3-VL-2B-Instruct | `89644892e4d85e24eaac8bacfd4f463576704203`; полный файл 4,255,140,312 байт |
| SDAR-1.7B-Chat | `14fcc4b796f05b9a11aa565b2353199af757d079`; файл 4,063,515,640 байт; metadata 2,031,739,904 BF16 параметра, несмотря на номинальное имя 1.7B |
| Wan2.2-TI2V-5B | `921dbaf3f1674a56f47e83fb80a34bac8a8f203e`; нужен `Wan2.2_VAE.pth` 2,818,839,170 байт, не весь видеогенератор |

Размеры и SHA получены из публичных API, без скачивания тензоров: [AW checkpoint API](https://huggingface.co/api/models/miniFranka/agentic-wam-pretrain-yamego3k-soup-e1?blobs=true), [Cosmos API](https://huggingface.co/api/models/nvidia/Cosmos-Reason1-7B?blobs=true), [Qwen API](https://huggingface.co/api/models/Qwen/Qwen3-VL-2B-Instruct?blobs=true), [SDAR API](https://huggingface.co/api/models/JetLM/SDAR-1.7B-Chat?blobs=true), [Wan API](https://huggingface.co/api/models/Wan-AI/Wan2.2-TI2V-5B?blobs=true). API без revision отражает текущий HEAD; зафиксированные выше SHA относятся к моменту проверки.

## 2. Почему штатный вариант не помещается

Проверка основана на реальном пути inference, а не только на размере checkpoint. В [deploy_policy.py](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/robotwin/deploy/deploy_policy.py) конструктор сначала создаёт/загружает основную модель, затем создаёт `self.text`. `_CosmosTextEncoder` переносит весь Qwen2.5-VL/Cosmos-Reason1 в BF16 на **тот же device** и оставляет его в `self.qwen`. Исключение текстового кодировщика из trainable-модели через `include_text_encoder=false` не исключает его из сервера.

| Часть | Что известно о памяти |
|---|---|
| Инструкция: Cosmos-Reason1 | 8,292,166,656 ×2 =16,584,333,312 байт =**15.445 ГиБ только весов** |
| Кодовый эксперт | Дополнительные миллиарды параметров SDAR; отдельно от Reason1. Уже Reason1 + даже номинальные 1.7B BF16 кода превышают 16 ГиБ, до видео/action |
| Видео/action/прочие обучаемые веса | Checkpoint имеет размер 9.276 ГиБ; размер файла не равен измеренному resident RAM/VRAM. Пиковая память не измерена |
| Визуальная башня Qwen3-VL | Загружается отдельно. [CodeVisionPrefix](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/src/agenticwam/models/cosmos_mot/code_vision.py) оставляет только vision tower, а не весь 2B VLM; нельзя ошибочно считать весь Qwen постоянно resident |
| Wan VAE | Отдельная модель и промежуточные feature maps; [build.py](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/src/agenticwam/models/cosmos_mot/build.py) загружает её вне AW checkpoint |
| Embedding cache | Код сохраняет инструкции на device в словаре. При L=128 и BF16 один `[1,128,100352]` embedding =**24.5 МиБ**; явного ограничения числа разных инструкций в этом словаре не найдено |
| Attention/графы/видео | Добавляются KV/attention buffers, визуальные токены, latents, декодирование видео, CUDA graph pools и временные копии при загрузке. Пиковые размеры авторами для Orin не опубликованы и здесь не измерены |
| ОС и Explorer | Используют ту же физическую память Jetson; CPU offload внутри NX не создаёт второй банк памяти |

Для обычного BF16 KV-cache геометрия `2 × layers × tokens × KV_width × 2 bytes` показывает рост с длиной контекста; это не измерение конкретной оптимизированной реализации. Число одновременно живущих буферов зависит от выбранного sampler/CFG/compile-пути. Поэтому здесь нет выдуманной точной оценки «требуется X ГБ VRAM». Уже проверенной нижней границы по весам достаточно для отказа от штатной установки на NX16GB. FP32 по умолчанию ещё тяжелее; launcher реального сервера включает BF16. Временные CPU state dict и первоначально созданные FP32-модули могут дополнительно увеличить пик загрузки.

Сумма перечисленных необходимых файлов весов около **37.68 ГБ** плюс зависимости, результаты и task checkpoint. Свободного NVMe порядка175 ГБ достаточно для самих этих файлов, однако диск не решает дефицит оперативной памяти. Память NX/Pi/Nano не складывается в общую VRAM. Квантизация, заранее вычисленные инструкции и последовательная выгрузка могли бы изменить границу, но поддержанного полного 16GB-пути в просмотренном релизе не найдено. Это отдельный порт с измерением качества/пика, а не готовая настройка.

## 3. Совместимость и полезность готовой политики

[INSTALL](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/docs/INSTALL.md) ориентирован на H100, CUDA12.8, Python3.10, torch2.7.1+cu128/torchvision0.22.1, DeepSpeed и дополнительные видеозависимости. Обучение в статье использовало48 H100; downstream-скрипты запускаются на8 GPU. Это рецепты авторов, а не доказательство минимального числа GPU для любого fine-tuning. В статье latency0.23с измерена на **RTX5090**, не на Jetson. [Первичная статья, таблицы5–6 и latency ablation](https://arxiv.org/html/2610.30136v1).

| Узел | Вывод |
|---|---|
| Orin NX16GB, aarch64, текущий квалифицированный ACT torch2.12/cu132 | Полный сервер не помещается. Общие PyTorch-операции принципиально переносимы, но ARM64/SM87/compile/видеодекодирование AW не квалифицированы. Успешный ACT GPU-тест этого не доказывает. Заменять рабочую среду на авторский стек ради пробного запуска не следует |
| Raspberry Pi16GB +Hailo10H | Нет опубликованного AW HEF/поддержанной сборки. Hailo не запускает произвольный PyTorch MoT checkpoint. Та же проблема общего размера |
| Nano8GB | Памяти ещё меньше; текущая неисправность GPU bring-up дополнительно исключает его из кандидатов |
| Компактный отдельный модуль памяти/разметка | Обычный PyTorch и локальные данные; вычислительно реалистичны. Нужен отдельный эксперимент, измерения и регистрация адаптации |

Сам checkpoint является **pretraining starting point**, а не готовым SO101/M3-контроллером. [REALWORLD](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/docs/REALWORLD.md) описывает три камеры и 64D delta-EEF пространство; реальный сервер дан для YAM с FK/IK-преобразованием. Нет готового адаптера наших SO101/M3, схемы камер/калибровки и собственного post-training. Простое дополнение наших осей нулями до64 не даёт совместимости. Режим без checkpoint прямо обозначен авторами как smoke с бессмысленными действиями.

Сильные результаты не универсальны: в RoboTwin default AW-0 имеет80.8% clean и20.6% randomized, тогда как π0.5 randomized27.7%. Число71.1 в реальных задачах является средним баллом по подзадачам/рубрике, а не долей полностью успешных эпизодов. Hybrid использует отдельно собранные program-generated demonstrations, поэтому его отличие нельзя приписать одному переключателю execution. Эти результаты не переносятся на наше оборудование или робота без новых измерений. [Таблицы1–3 и определение метрик](https://arxiv.org/html/2610.30136v1).

## 4. Что конкретно принять в работу

### A. Локальная разметка программами и происхождение фаз

Использовать документированный авторами **Route B: task program**: заранее заданная программа узкой задачи плюс привязка её строк/примитивов к текущим кадрам. Для MuJoCo можно получить происхождение действий непосредственно при выполнении проверенной программы; облачная VLM для этого не нужна. Хранить sidecar с episode/frame IDs, source SHA, версией библиотеки, program ID, активной фазой, event evidence и способом получения labels. Это полезно для нашего двухэтапного skill cycle и разбора неуспешных переходов. [CODE_ANNOTATION, routes A/B и frame alignment](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/docs/CODE_ANNOTATION.md).

Учебная разметка может использовать события из записанной траектории как **target**. Актор при обучении и проверке получает только разрешённые текущие/прошлые наблюдения и заданную инструкцию; будущий RGB, истинный будущий program slice и privileged sim state не становятся его входами. Program labels динамики и успешные expert BC targets сохраняют разные роли.

### B. Небольшая рекуррентная память программы

В релизе уже есть [LearnedCodeMemory](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/src/agenticwam/models/experts/code_memory.py): K слотов, gated cross-attention write, confidence weighting, read prefix. Модуль зависит только от PyTorch; [общая конфигурация](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/configs/model/agenticwam_cosmos.yaml) выключает его по умолчанию (`code_memory:false`). Это наличие кода, не доказательство качества опубликованной модели с памятью.

Статически подсчитанная формула для `ffn_mult=4`: **14d² +20d +2Kd** параметров. Для d128/K8 это **233,984 параметра**, около0.893 МиБ FP32 весов; веса+градиенты+два FP32 Adam-момента около3.57 МиБ до активаций. Это вполне ограниченный модуль для текущего железа; потребуется собственный малый encoder программных токенов и согласование размерности. Подсчёт сделан по Linear/LayerNorm/slot declarations, без импорта или запуска upstream кода. Его fit нельзя выдавать за fit полного AW-0.

Перед переносом нужны тесты all-invalid mask (текущий softmax от всех `-inf` способен дать NaN), пустой истории, нулевой уверенности и сброса памяти между эпизодами. Нулевая уверенность не означает побитно неизменное состояние: после gated update исходник всё равно применяет FFN/LayerNorm. При копировании сохранить BSD-уведомления и записать точный upstream SHA.

### C. Условный контролируемый опыт

**Сначала проверить необходимость.** Существующий reach с видимой активной целью не устанавливает проблему забывания программы: слабый первый примитив или неуспешное достижение первой цели не исправляются автоматически памятью. До добавления нового neural module нужно найти в текущем code path различимые ошибки сохранения плана/перехода фаз и успешные отдельные примитивы. Если такой причины нет, оставить исходную систему и программный trace, а не строить игрушечную функцию только из-за малого числа параметров. Следующий протокол применим после этого условия.

1. Взять существующий двухэтапный MuJoCo reach и как минимум два порядка/программы с различными фазами. На единственной постоянной метке `reach` пользу программной памяти измерить нельзя. Программы/инструкции задаются до rollout; фазовые labels пишутся из причинного журнала исполнения.
2. Заморозить разделение **целых эпизодов/целей/seed**, отдельные train и development; sealed test не использовать. Повторить минимум на3 training seeds. Записать начальные параметры, одинаковые данные/шаги и runtime. Это всё ещё development pilot.
3. Сравнить одинаковый actor/world backbone: без памяти; с текущим program token, без истории; с gated recurrent memory; дополнительно shuffled-history или reset-every-step control. Не подставлять правильную программу на development вместо предсказанной/доступной из инструкции. Поддержать честные persistence/action-agnostic baselines нашего world benchmark.
4. Измерить task completion и успешность переходов/двух фаз в симуляции, next-state/RGB error с эпизодным агрегированием, сохранение результата прежних задач, latency и peak memory. Различать command-estimated и измеренные состояния. Не объявлять pixel MSE или training loss «физической успешностью».
5. Сохранить optimizer steps/losses, веса+reload, source/label/split hashes, сырые исходы по seed и episode. Если persistence или baseline выигрывает — сохранить этот результат. В текущем компактном world-model pilot persistence уже сильнее, поэтому улучшение нельзя предполагать заранее.
6. Сначала только offline/simulation. Генерируемые программы представлены типизированными IDs/аргументами проверенных примитивов; произвольный Python не исполняется. Аппаратное исполнение и движения шасси из этого решения не следуют. Текущий запрет движения шасси сохраняется.

**Выбранный следующий шаг:** учитывать AW-0 как близкий prior art, использовать A для происхождения фаз/разбора существующего skill cycle и проверить фактическую необходимость B. Сейчас B не включать. Только при установленной проблеме сохранения плана провести C на текущем реальном software path, без новых внешних моделей. Сам по себе статический fit B не является основанием для нового эксперимента. Полный AW checkpoint отложить до поддержанного экономного inference-пути/подходящего вычислителя и собственного embodiment adapter. Никакие закупки, облако или частные загрузки из решения не следуют.

## 5. Предшествующие работы и лицензии

AW уже включает извлечение повторяющихся программных паттернов, admission в библиотеку и проверку согласованности с contact/gripper events, затем замораживание библиотеки. В коде есть и optional learned recurrent memory. Поэтому общие «библиотека навыков», «нейронная память», «проверка контрактами» и «world/action с программой» не являются самостоятельной новизной Explorer. Проверяемое отличие могло бы состоять в конкретном непрерывном цикле расширения/отката/retention на заданном протоколе; оно пока не установлено. Существование этих компонент AW не доказывает оценку online continual skill learning: такой эксперимент в прочитанных результатах не найден. [Библиотека и admission](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/docs/CODE_ANNOTATION.md), [optional memory source](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/src/agenticwam/models/experts/code_memory.py).

Код проекта: [BSD-2-Clause](https://github.com/ZhuoyangLiu2005/agentic-wam/blob/7ad228bebb897bf01705cc10171e1d88cf5e9b22/LICENSE). Карточка AW checkpoint также декларирует BSD-2 с сохранением собственных лицензий публичных компонентов. [Qwen](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct), [SDAR](https://huggingface.co/JetLM/SDAR-1.7B-Chat) и [Wan](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B) декларируют Apache-2.0; [Cosmos-Reason1](https://huggingface.co/nvidia/Cosmos-Reason1-7B) — NVIDIA Open Model License. Нельзя приписать всему составному runtime только BSD. Исходные pretraining corpora и исходный video checkpoint для обучения с нуля в этом релизе не даны; готовый AW checkpoint нужен для воспроизведения этого starting point. Публикация собственного отчёта не должна называть невыполненный перенос воспроизведением авторских результатов.

## 6. Локальное свидетельство аудита

Тексты небольших публичных исходников сохранены вне Git: `/Users/vladromenko/Library/Caches/Explorer/aw0-audit/`; `source-receipt.json` содержит URL/байты/SHA256 первых11 файлов. Дополнительно прочитан `code_memory.py`. Весов и private recordings там нет. При независимой проверке первичны указанные Git/HF revisions, а не существование локального cache.

| Прочитанный файл | SHA256 |
|---|---|
| `robotwin/deploy/deploy_policy.py` | `55749811853e3e1311e76e45860349722cb4bf3dc3b6f82a5cb45540cc03bd38` |
| `src/agenticwam/models/cosmos_mot/build.py` | `3435a56968893665c27b89f8fbab6ba9ea1b99507f8465bce020e16680849555` |
| `src/agenticwam/models/cosmos_mot/code_vision.py` | `1de8a50541a6b8d869a1bd673173fe6b736c53a5c4a43d0fae387a3184f6422a` |
| `src/agenticwam/models/experts/code_memory.py` | `cb83eb2400431579aa680e7ee74418a08bc637791f7a07c85827da79eccc4a74` |
| `docs/INSTALL.md` | `0fa4e24812ff2f6a8b44acbced98875ab8fcf2adbcd7b619c001a0a1939ac8ef` |
| `docs/REALWORLD.md` | `56b7a5f4f7b32292411ddcc70e78a3138e1b65221e38dbfa3e78519d027a3cf9` |
| `docs/CODE_ANNOTATION.md` | `34c44a5f90cc3d829b4dfbb812cab2074746f78d962c228636f2fd842b988424` |

Непроверенное: фактический peak RAM/VRAM любого изменённого/квантизованного варианта; его latency/качество на SM87; все зависимости на aarch64; post-training для наших рук; физическая работоспособность. Ни одна из этих неизвестных не скрыта под отметкой «AW-0 подключена».

## Машиночитаемое решение

```json
{
  "audit_date": "2026-10-26",
  "model_identity": "Agentic World Action Models / AW-0",
  "paper": "https://arxiv.org/abs/2610.30136v1",
  "git_url": "https://github.com/ZhuoyangLiu2005/agentic-wam",
  "git_commit": "7ad228bebb897bf01705cc10171e1d88cf5e9b22",
  "model_url": "https://huggingface.co/miniFranka/agentic-wam-pretrain-yamego3k-soup-e1",
  "model_revision": "603c59fd434774a269978e704730ba60740b1c17",
  "model_file": "agenticwam_pretrain.pt",
  "model_file_bytes": 9959836973,
  "model_lfs_sha256": "d13f68f0167dc6141db4e21a07fa5358d56d353f741b89beac343d2ebb690dc4",
  "stock_text_encoder_bf16_parameters": 8292166656,
  "stock_text_encoder_weight_bytes": 16584333312,
  "decision": "DO_NOT_INSTALL_STOCK_AW0_ON_CURRENT_FLEET",
  "weights_downloaded": false,
  "upstream_model_executed": false,
  "device_changes": false,
  "private_media_uploaded": false,
  "new_neural_module_enabled": false,
  "next_action": "Use phase/program trace in existing skill-cycle analysis; memory ablation only if current-path failure establishes need",
  "our_world_model_is_AW0": false,
  "H1_H5_status": "UNTESTED"
}
```
