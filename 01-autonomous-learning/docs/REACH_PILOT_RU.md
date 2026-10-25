# CAD ROSMASTER: инженерный цикл PPO → официальный ACT

`explorer.simulation.run_sim_pilot(store, job, config)` запускает настоящий
MuJoCo-эксперимент, сохраняет артефакты в `store.root/jobs/<job_id>` и возвращает
результат. Функция не импортирует ROS, web-контроллер, SSH или serial и не
взаимодействует с физическим роботом. Настройки действующего контроллера не
меняются.

Робот строится исходным `legacy/simulator/explorer_model.py`: двадцать
STL-моделей сверяются с SHA-256 в `legacy/config/arm_model.json`, используются
исходные URDF/SRDF и записанная hand-eye reference. Пропущенный при импорте
`explorer.srdf` восстановлен из действующего исходника Nano; его hash точно
совпадает с исходным manifest. Куб или point-mass вместо геометрии робота не
используется. Дополнительный зелёный target — визуальная метка цели.

Первый эксперимент — дотянуться grasp-center до точки, полученной FK
достижимой конфигурации, с ошибкой менее 2,5 см в течение трёх решений подряд.
Начальное расстояние не меньше 4 см. База в симуляции закреплена явным weld,
активны первые три сустава руки. Это **инженерный reach pilot**, а не носок,
захват, мобильная манипуляция или физически проверенный перенос.

PPO из Stable-Baselines3 получает привилегированное состояние симулятора и
обучается настоящим optimizer. Детерминированные teacher rollouts создают
исходные эпизоды с RGB, командным состоянием, action9, reward и outcome. У всех
эпизодов есть hashes, seeds и явные labels; неуспешный rollout не называется
успешным показом. No-motion baseline и отдельные rollout episodes позволяют
увидеть реальную пользу обучения. Финальные научные sealed splits и несколько
training seeds остаются отдельной стадией исследования.

Студент — **непеределанный официальный**
`lerobot.policies.act.modeling_act.ACTPolicy` версии 0.6.1: ResNet18, VAE,
transformer, L1+KL objective и action chunks. Компактная конфигурация:
dim_model=128, 2 encoder layers, 1 decoder layer, chunk=8 по умолчанию.
Backbone обучается с нуля, сеть ничего не скачивает. Actor видит только RGB
overview и `command_estimate`; координаты target, MuJoCo qpos и evaluator
поля не попадают в его batch. RGB preprocessing и фиксированные диапазоны
action9 сохраняются отдельно. Temporal ensembling выключен; исполняется один
элемент нового chunk за решение. Сохранение и загрузка — официальные
`save_pretrained` / `from_pretrained`, равенство предсказаний проверяется.

Эпизоды пока имеют переносимый внутренний формат `explorer_npz_episode_v1`,
**не** выдают себя за официальный `LeRobotDataset`. Официальный ACT обучается
через открытый policy API с небольшим episode adapter. Необходимый экспорт
в LeRobotDataset/Parquet — отдельная интеграция, без изменения исходных данных.

Параметры берутся только из `job['config']`: `teacher_steps` (4096),
`demonstration_episodes` (12), `evaluation_episodes` (6), `horizon` (60),
`act_steps` (120), `batch_size` (8), `image_size` (64), `chunk_size` (8),
`seed` (41), `device` (`cpu`). Главный `config` содержит приватные подключения
и никогда не сериализуется. PPO работает на CPU; `device` относится к ACT.
На Orin CUDA разрешается выбирать только после проверки совместимости нужных
операций. Отдельный successful Linear test не квалифицирует CNN/ACT.

Нужен `EXPLORER_SIM_MESH_DIR`, указывающий на приватный проверенный каталог
meshes. Исходные STL не добавляются в Git. Кадр `frame.jpg` регулярно создаётся
настоящим renderer MuJoCo; его источник отмечен в `frame.json`.

Артефакты: provenance с версиями/настройками/hash исходников, `model.xml`,
`teacher_ppo.zip`, dataset manifest/NPZ, `act_training.json`,
`student_act/{config.json,model.safetensors,explorer_preprocessing.json}`,
optimizer checkpoint, actual rollout metrics и `result.json`. SkillCard
регистрируется только после настоящего изменения весов teacher и evaluation;
статус — `simulation_pilot_evaluated`, физические run IDs пусты. Даже нулевой
success rate сохраняется без переименования в успешное обучение.

Повторный запуск завершённого job проверяет hashes артефактов и возвращает
результат. При прерывании прежние данные переносятся внутри каталога job в
`interrupted_attempts/<id>`; новая попытка не перезаписывает исходные эпизоды.
Рабочий `worker.log` сохраняет свой текущий путь. Engineering evaluation
помечается `sealed_final_test=false`, чтобы не выдать её за финальную научную
проверку после подбора параметров.

## Проверенная программная совместимость

MuJoCo 3.17.0, SB3 2.9.0, LeRobot 0.6.1. LeRobot объявляет torch<2.12 и
torchvision<0.27. На имеющейся сборке torch2.14.1/torchvision0.29.1 импорт,
forward/backward, optimizer и official checkpoint roundtrip проверяются
фактически; это **исключение из объявленных bounds**, а не обещание полной
совместимости. Не заменяйте проверенный Jetson Torch автоматическим resolver.

Минимальные дополнительные ACT-зависимости: `lerobot==0.6.1` и соответствующий
уже установленному Torch torchvision (ставить без dependencies),
`einops==0.8.2`, `draccus==0.11.6`, `huggingface-hub>=1.6,<2`, `safetensors`,
`termcolor`, `tqdm`, `requests`, `packaging<26`, `opencv-python-headless`.
Для этого policy API не нужны transformers, wandb, datasets и torchcodec.

## Method provenance

- `PPO_SB3_2_9`: [официальный SB3 PPO](https://stable-baselines3.readthedocs.io/en/master/modules/ppo.html), MIT.
- `ACT_LEROBOT_0_6_1`: [официальный ACT source](https://github.com/huggingface/lerobot/tree/v0.6.1/src/lerobot/policies/act), Apache-2.0;
  [ACT paper](https://arxiv.org/abs/2304.13705).
- `EXPLORER_CAD_MUJOCO`: существующая CAD-модель владельца и
  [MuJoCo](https://github.com/google-deepmind/mujoco), Apache-2.0.
- `ENGINEERING_GLUE`: задача, fixture, episode adapter, временные метки,
  registry и UI integration; не заявляется научной новизной.

Официальный ACT source `modeling_act.py` в установленном wheel0.6.1 имеет
SHA-256 `a6c1a49c5daafa513b9abee9b986205d327e25c70ed13623ab2e6c0ab6b1f577`;
`configuration_act.py` —
`35a60c0dd3c811b137b5eb035bdbd40d40e2c24ba0cda61756052e6e1c829282`.

Тесты:

```sh
EXPLORER_SIM_MESH_DIR=/private/path/meshes PYTHONPATH=01-autonomous-learning/src \
  python -m pytest 01-autonomous-learning/tests/test_reach_pilot.py -q
EXPLORER_RUN_SIM_INTEGRATION=1 EXPLORER_SIM_MESH_DIR=/private/path/meshes \
  PYTHONPATH=01-autonomous-learning/src python -m pytest \
  01-autonomous-learning/tests/test_reach_pilot.py -q
```
